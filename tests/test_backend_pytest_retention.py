"""Deterministic actual-function tests; no native processes, cgroups or fixtures.

Import the actual supervisor module and stub external effects only. These tests
do not qualify real reap/ECHILD/EOF/cgroup behavior or provide native cleanup proof.
"""

import io
import json
from pathlib import Path
import sys
import unittest
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

from tests import backend_pytest_supervisor as supervisor


class Failure(Exception):
    pass


class Pinned(BaseException):
    """Test-only stop on first retention sleep; no real indefinite loop."""


class Socket:
    def fileno(self):
        return 10

    def close(self):
        pass

    def shutdown(self, _):
        pass

    def recv(self, *_):
        return b"x"


class Domain:
    fds = (11, 12)
    path = "/synthetic/domain"
    identities = []

    def __init__(self):
        self.busy = False
        self.checked = 0

    def check(self):
        self.checked += 1

    def populated(self):
        return self.busy

    def close(self):
        pass


class MemoryPath:
    """Evidence writer stub: no filesystem mutation during function execution."""

    def __init__(self, *_):
        pass

    def __truediv__(self, _):
        return self

    def resolve(self):
        return self

    def open(self, *_args, **_kwargs):
        return MemoryFile()


class MemoryFile(io.StringIO):
    def fileno(self):
        return 20


FACTS = dict(
    direct_reaped=True, no_children=True, domain_empty=True, stdout_eof=True, stderr_eof=True
)


def setup(testcase):
    state = NS(closed=[], exit=1, messages=[], domain=Domain(), process=None)

    def popen(*_a, **_k):
        state.process = NS(pid=30, returncode=state.exit, poll=lambda: state.exit)
        return state.process

    g = dict(
        __file__="synthetic.py",
        __name__="synthetic",
        Path=MemoryPath,
        subprocess=NS(Popen=popen, DEVNULL=-1),
        os=NS(
            pidfd_open=lambda *_: 40,
            getpid=lambda: 50,
            close=state.closed.append,
            fsync=lambda _: None,
            environ={},
        ),
        sys=NS(executable="synthetic-python", stderr=io.StringIO()),
        pair=lambda: (Socket(), Socket()),
        create_domain=lambda *_: state.domain,
        send=lambda *args: state.messages.append(args),
        receive=lambda *_: {},
        context_frame=lambda _: {},
        remaining=lambda _: 1,
        finish_direct=lambda *_: state.exit,
        SupervisionFailure=Failure,
        json=json,
        RESCUE_SECONDS=5,
        MAX_STREAM=100,
        CHANNEL="SYNTHETIC",
        secrets=NS(token_hex=lambda _: "synthetic"),
        time=NS(monotonic=lambda: 0, sleep=lambda _: (_ for _ in ()).throw(Pinned())),
        select=NS(select=lambda *_: ([Socket()], [], [])),
        socket=NS(MSG_PEEK=2, SHUT_WR=1),
    )
    guard = patch.dict(supervisor.__dict__, g)
    guard.start()
    testcase.addCleanup(guard.stop)
    return supervisor.__dict__, state


class RetentionTests(unittest.TestCase):
    def test_deadline_subject_registration_is_restored(self):
        from tests.test_backend_pytest_deadline import DeadlineTests

        key = "_backend_deadline_subject"
        missing = object()
        original = {name: module for name, module in sys.modules.items() if name == key}
        try:
            for previous in [missing, ModuleType(key)]:
                with self.subTest(previous=previous):
                    if isinstance(previous, ModuleType):
                        sys.modules[key] = previous
                    else:
                        sys.modules.pop(key, None)
                    subject = DeadlineTests()
                    try:
                        subject.setUp()
                        self.assertIs(sys.modules[key], subject.subject)
                    finally:
                        subject.doCleanups()
                    self.assertIs(sys.modules.get(key, missing), previous)
        finally:
            sys.modules.pop(key, None)
            sys.modules.update(original)

    def manager_case(self, worker_exit, verified, inject_failure=False):
        g, s = setup(self)
        g["Path"] = Path
        client = NS(pid=70, returncode=0, poll=lambda: None if not s.messages else 0)
        # hello send does not terminate the client; one request calls manager_one.
        counter = iter([None, 0, 0])
        client.poll = lambda: next(counter, 0)
        g["subprocess"].Popen = lambda *_a, **_k: client
        g["bootstrap"] = lambda _: ("group", 99)
        g["receive"] = lambda *_: dict(kind="run", data={}, nonce="n")
        g["request_from"] = lambda *_: None

        def one(_local, _fd, _group, _request, retained, _context):
            process = None if worker_exit == "missing" else NS(poll=lambda: worker_exit)
            retained.append(
                dict(
                    process=process,
                    domain=s.domain,
                    control=None,
                    pidfd=40,
                    release_verified=verified,
                )
            )
            if inject_failure:
                raise Failure("setup/IPC failure")

        g["manager_one"] = one
        context = NS(interpreter="/synthetic/.venv/bin/python", nonce="task")
        if verified:
            self.assertEqual(g["manager"]([], context), 1)
            self.assertEqual(s.closed, [99])
        else:
            with self.assertRaises(Pinned):
                g["manager"]([], context)
            self.assertEqual(s.closed, [])

    def test_exited_worker_does_not_release_unknown(self):
        self.manager_case(1, False)

    def test_never_started_worker_does_not_release_unknown(self):
        self.manager_case("missing", False, True)

    def test_live_worker_retains_unknown(self):
        self.manager_case(None, False)

    def test_accounted_failure_returns_nonzero(self):
        self.manager_case(1, True)

    def test_accounted_receipt_delivery_failure_returns_nonzero(self):
        self.manager_case(1, True, True)

    def result_case(self, accounting, *, busy=False, exit=1, broken_send=False, ok=False):
        g, s = setup(self)
        s.exit, s.domain.busy = exit, busy
        data = dict(ok=ok, end=10, accounting=accounting, rescue_used=not ok, facts=FACTS.copy())
        packets = iter(
            [
                dict(kind="ready", data=dict(pid=30, scope="group", context_digest="d")),
                dict(kind="final", data=data),
            ]
        )
        g["receive"] = lambda *_: next(packets)
        if broken_send:

            def send(*args):
                if args[3] == "final":
                    raise Failure("client IPC lost")

            g["send"] = send
        context = NS(evidence="memory", digest=lambda: "d")
        request = NS(nonce="n", deadline=10, wire=lambda: {})
        retained = []
        error = None
        try:
            g["manager_one"](Socket(), 99, "group", request, retained, context)
        except Failure as caught:
            error = caught
        return retained, error, s

    def test_valid_rescued_five_facts_are_release_verified(self):
        jobs, error, s = self.result_case(dict(accounted=True, facts=FACTS))
        self.assertIsNone(error)
        self.assertIs(jobs[0]["release_verified"], True)
        self.assertEqual(s.domain.checked, 1)

    def test_valid_no_child_accounting_is_release_verified(self):
        jobs, error, _ = self.result_case(dict(accounted=True, no_child_started=True))
        self.assertIsNone(error)
        self.assertIs(jobs[0]["release_verified"], True)

    def test_unknown_accounting_is_not_release_verified(self):
        jobs, error, _ = self.result_case(dict(accounted=False))
        self.assertIsNone(error)
        self.assertIs(jobs[0]["release_verified"], False)

    def test_incomplete_rescue_facts_are_rejected(self):
        for key in FACTS:
            with self.subTest(key=key):
                jobs, error, _ = self.result_case(dict(accounted=True, facts={**FACTS, key: False}))
                self.assertIsInstance(error, Failure)
                self.assertIs(jobs[0]["release_verified"], False)

    def test_accounted_flag_alone_is_rejected(self):
        jobs, error, _ = self.result_case(dict(accounted=True))
        self.assertIsInstance(error, Failure)
        self.assertIs(jobs[0]["release_verified"], False)

    def test_populated_leaf_is_not_release_verified(self):
        jobs, error, _ = self.result_case(dict(accounted=True, facts=FACTS), busy=True)
        self.assertIsInstance(error, Failure)
        self.assertIs(jobs[0]["release_verified"], False)

    def test_wrong_worker_exit_is_not_release_verified(self):
        jobs, error, _ = self.result_case(dict(accounted=True, facts=FACTS), exit=0)
        self.assertIsInstance(error, Failure)
        self.assertIs(jobs[0]["release_verified"], False)

    def test_delivery_failure_keeps_verified_accounting(self):
        jobs, error, _ = self.result_case(dict(accounted=True, facts=FACTS), broken_send=True)
        self.assertIsInstance(error, Failure)
        self.assertIs(jobs[0]["release_verified"], True)

    def test_normal_result_removes_job(self):
        jobs, error, _ = self.result_case({}, ok=True, exit=0)
        self.assertIsNone(error)
        self.assertEqual(jobs, [])

    def test_normal_delivery_failure_keeps_verified_accounting(self):
        jobs, error, _ = self.result_case({}, ok=True, exit=0, broken_send=True)
        self.assertIsInstance(error, Failure)
        self.assertIs(jobs[0]["release_verified"], True)


if __name__ == "__main__":
    unittest.main()
