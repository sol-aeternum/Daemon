"""D1 deadline/relay models: real control functions, no native process operations.

These dependency-free tests can run under the normal managed launcher while the
native runner/pytest caller prerequisites remain unavailable. They do NOT qualify
pidfd/prctl/cgroup semantics or replace the real fractional/resistant-chain tests.
"""

from contextlib import ExitStack
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class RetentionObserved(BaseException):
    """End a model's retention loop without claiming an actual child was stopped."""


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).with_name("backend_pytest_supervisor.py")
        spec = importlib.util.spec_from_file_location("_backend_deadline_subject", path)
        if spec is None or spec.loader is None:
            self.fail("local deadline subject import spec/loader unavailable")
        self.subject = importlib.util.module_from_spec(spec)
        previous = {name: module for name, module in sys.modules.items() if name == spec.name}

        def restore_module():
            sys.modules.pop(spec.name, None)
            sys.modules.update(previous)

        self.addCleanup(restore_module)
        sys.modules[spec.name] = self.subject
        spec.loader.exec_module(self.subject)
        # Explicitly retained fixture files; no TemporaryDirectory auto-deletion.
        home = Path(tempfile.mkdtemp(prefix="backend-pytest-d1-"))
        self.root = home / ".local/share/opencode-work"
        source = self.root / "worktrees/model-task"
        self.context = self.subject.InvocationContext(
            "model-task",
            "ses_Model",
            "c" * 32,
            str(source),
            str(self.root / "resources/model-task"),
            str(self.root / "evidence/model-task"),
            str(home / ".local/state/opencode-work/owned-manifests/model-task.json"),
            str(source / ".venv/bin/python"),
        )
        # Synthetic __file__ supplies pure path identity for the model handshake;
        # native operations remain mocked, not granted by this value.
        self.subject.__file__ = str(source / "tests/backend_pytest_supervisor.py")
        Path(self.context.evidence).mkdir(parents=True)

    def worker_model(self, detection):
        s = self.subject
        clock = [1.0]
        phases, sent, emitted, native_signals = [], [], [], []
        assert s.__file__ is not None
        request = s.Request(
            (
                "/usr/bin/bash",
                str(Path(s.__file__).resolve().parents[1] / "scripts/run_backend_pytest.sh"),
                "--help",
            ),
            str(self.root),
            (("PATH", "/usr/bin:/bin"),),
            "a" * 32,
            40.0,
        )
        domain = s.Domain(
            "/sys/fs/cgroup/model-own.scope/backend-pytest-" + request.nonce,
            (-101, -102, -103, -104),
            [[0, 0, 0, 0, 0]] * 4,
        )

        class Channel:
            def getsockopt(self, *args):
                return s.struct.pack("3i", 4242, s.os.getuid(), 1000)

            def close(self):
                pass

        control, startup = Channel(), Channel()
        setup = {
            "request": request.wire(),
            "manager_pid": 4242,
            "group": "/model-own.scope",
            "path": domain.path,
            "fds": list(domain.fds),
            "identities": domain.identities,
            **s.context_frame(self.context),
        }

        def receive(channel, nonce, seq, deadline):
            self.assertEqual(nonce, request.nonce)
            s.remaining(deadline)
            if channel is control:
                return (
                    {"kind": "setup", "data": setup} if seq == 0 else {"kind": "launch", "data": {}}
                )
            return {"kind": "placed", "data": {"path": domain.path}}

        def send(channel, nonce, seq, kind, data, deadline):
            sent.append((seq, kind, data, deadline))
            s.remaining(deadline)

        def reap(collector):
            collector.direct_status = 137
            collector.no_children = True
            collector.eofs = {"stdout", "stderr"}

        def collect(domain, request, collector, control, deadline, *, rescue):
            phases.append((deadline, rescue))
            if not rescue:
                clock[0] = detection
                raise TimeoutError("strictly late original overrun")
            s.remaining(deadline)
            reap(collector)
            return collector.facts(True)

        def emit(control, request, collector, final, deadline, seq):
            emitted.append((final, deadline, seq))
            s.remaining(deadline)

        with ExitStack() as stack:
            stack.enter_context(patch.object(s.time, "monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(s.os, "getppid", return_value=4242))
            stack.enter_context(patch.object(s, "self_group", return_value="/model-own.scope"))
            stack.enter_context(patch.object(s, "subreaper"))
            stack.enter_context(patch.object(s.Domain, "check"))
            stack.enter_context(patch.object(s.Domain, "populated", return_value=False))
            stack.enter_context(patch.object(s, "receive", side_effect=receive))
            stack.enter_context(patch.object(s, "send", side_effect=send))
            stack.enter_context(
                patch.object(
                    s, "fork_parked", return_value=(s.CapturedChild(123, -124, -125, -126), startup)
                )
            )
            stack.enter_context(patch.object(s.Collector, "reap", reap))
            stack.enter_context(patch.object(s, "collect", side_effect=collect))
            stack.enter_context(patch.object(s, "emit", side_effect=emit))
            stack.enter_context(
                patch.object(
                    s.signal,
                    "pidfd_send_signal",
                    side_effect=lambda *args: native_signals.append(args),
                )
            )
            stack.enter_context(patch.object(s.time, "sleep", side_effect=RetentionObserved))
            try:
                code = s.worker(control, request.nonce, request.deadline)
            except RetentionObserved:
                code = "retained"
        return request, domain, code, phases, sent, emitted, native_signals

    def test_strictly_late_detection_caps_then_relays_accounted_failure(self):
        s = self.subject
        request, domain, code, phases, sent, emitted, signals = self.worker_model(40.1)
        self.assertEqual(code, 1)
        self.assertEqual(phases, [(40.0, False), (50.0, True)])
        final, end, seq = emitted[0]
        self.assertEqual((end, seq), (50.0, 2))
        self.assertFalse(final["ok"])
        self.assertTrue(final["rescue_used"])
        self.assertTrue(final["accounting"]["accounted"])
        self.assertEqual(signals, [])
        rescue_start = next(item for item in sent if item[1] == "rescue-start")
        self.assertEqual(rescue_start[2], {"end": 50.0})

        class Channel:
            def fileno(self):
                return -201

            def close(self):
                pass

        class Process:
            pid = 9001
            returncode = 1

            def poll(self):
                return self.returncode

        local, remote, client = Channel(), Channel(), Channel()
        clock = [1.0]
        worker_packets = [
            {
                "seq": 0,
                "kind": "ready",
                "data": {
                    "pid": 9001,
                    "scope": "/model-own.scope",
                    "context_digest": self.context.digest(),
                },
            },
            {"seq": 1, "kind": "rescue-start", "data": {"end": end}},
            {"seq": 2, "kind": "final", "data": final},
        ]
        relayed = []

        def receive(channel, nonce, seq, deadline):
            packet = worker_packets.pop(0)
            self.assertIs(channel, local)
            self.assertEqual((nonce, seq), (request.nonce, packet["seq"]))
            if seq:
                clock[0] = 40.1
            s.remaining(deadline)
            return packet

        def send(channel, nonce, seq, kind, data, deadline):
            s.remaining(deadline)
            if channel is client:
                relayed.append({"seq": seq, "kind": kind, "data": data})

        retained = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(s.time, "monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(s, "create_domain", return_value=domain))
            stack.enter_context(patch.object(s, "pair", return_value=(local, remote)))
            stack.enter_context(patch.object(s.subprocess, "Popen", return_value=Process()))
            stack.enter_context(patch.object(s.os, "pidfd_open", return_value=-202))
            stack.enter_context(patch.object(s.Domain, "check"))
            stack.enter_context(patch.object(s.Domain, "populated", return_value=False))
            stack.enter_context(patch.object(s, "receive", side_effect=receive))
            stack.enter_context(patch.object(s, "send", side_effect=send))
            s.manager_one(client, -203, "/model-own.scope", request, retained, self.context)
        self.assertEqual(len(retained), 1)  # retain failure status, not an UNKNOWN pin
        self.assertTrue(retained[0]["release_verified"])
        self.assertEqual([packet["kind"] for packet in relayed], ["ready", "rescue-start", "final"])
        self.assertFalse(relayed[-1]["data"]["ok"])
        self.assertTrue(relayed[-1]["data"]["accounting"]["accounted"])

        clock[0] = 1.0

        def client_receive(channel, nonce, seq, deadline):
            packet = relayed.pop(0)
            self.assertEqual((nonce, seq), (request.nonce, packet["seq"]))
            if seq:
                clock[0] = 40.1
            s.remaining(deadline)
            return packet

        with ExitStack() as stack:
            stack.enter_context(patch.object(s.time, "monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(s, "client_channel", return_value=client))
            stack.enter_context(patch.object(s, "receive", side_effect=client_receive))
            stack.enter_context(patch.object(s, "send"))
            stack.enter_context(patch.object(s.secrets, "token_hex", return_value=request.nonce))
            with self.assertRaisesRegex(s.SupervisionFailure, "failed/rescued/UNKNOWN"):
                s.run_runner(
                    list(request.argv),
                    cwd=Path(request.cwd),
                    env=dict(request.environment),
                    bound_seconds=39.0,
                )

    def test_already_exhausted_ceiling_fails_and_retains_without_reset(self):
        request, domain, code, phases, sent, emitted, signals = self.worker_model(50.1)
        self.assertEqual(code, "retained")
        self.assertEqual(phases, [(40.0, False)])
        final, end, seq = emitted[0]
        self.assertEqual((end, seq), (50.0, 2))
        self.assertFalse(final["ok"])
        self.assertTrue(final["rescue_used"])
        self.assertFalse(final["accounting"]["accounted"])
        self.assertEqual(signals, [])
        self.assertIn("deadline exhausted", final["accounting"]["reason"])
        self.assertTrue(all(item[-1] <= 50.0 for item in sent))


if __name__ == "__main__":
    unittest.main()
