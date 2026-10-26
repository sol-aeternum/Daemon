"""Synthetic contract tests only; no sudo/service/cgroup writes from these tests."""

import ast
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def modules(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    loaded = []
    for name, relative in (
        ("hosted_candidate_supervisor", "tests/backend_pytest_supervisor.py"),
        ("hosted_candidate_launcher", "scripts/run_hosted_backend_pytest.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    s, launcher = loaded
    monkeypatch.setattr(launcher, "supervisor", lambda: s)
    return s, launcher


@pytest.fixture
def context(modules):
    s, _ = modules
    root = str(Path(__file__).resolve().parents[1])
    nonce = "a" * 32
    unit = "daemon-pytest-123-1-positive-" + nonce + ".service"
    control = "/system.slice/" + unit + "/manager"
    return s.HostedContext.parse(
        s.hosted_canonical(
            {
                "tag": s.HOSTED_TAG,
                "run": "123",
                "attempt": "1",
                "phase": "positive",
                "nonce": nonce,
                "unit": unit,
                "source": root,
                "resources": root + "/.hosted-pytest/" + nonce,
                "evidence": root + "/.hosted-pytest/" + nonce,
                "interpreter": root + "/.venv/bin/python",
                "uid": 1000,
                "gid": 1000,
                "groups": [1000],
                "deadline": time.monotonic() + 60,
                "invocation": "b" * 32,
                "control": control,
                "payload": control + "/payload-" + nonce,
                "control_identity": [55, 1, 1000, 1000, 448],
                "payload_identity": [55, 2, 1000, 1000, 448],
            }
        )
    )


def seed(context):
    value = context.wire()
    for key in ("control", "payload", "control_identity", "payload_identity", "invocation"):
        value.pop(key)
    value.update(home="/home/runner", path="/usr/bin:/bin")
    return value


def receipt(context):
    return {
        "manager_pid": 100,
        "client_pid": 101,
        "client_status": 0,
        "manager_status": 0,
        "facts": dict.fromkeys(
            ("direct_reaped", "no_children", "domain_empty", "stdout_eof", "stderr_eof"), True
        ),
        "stream_bytes": {"stdout": 0, "stderr": 0},
        "invocations": [],
    }


def properties(launcher, context, *, terminal=False):
    data = dict.fromkeys(launcher.READ_PROPERTIES, "")
    data.update(
        Id=context.unit,
        LoadState="loaded",
        Description=launcher.description(seed(context)),
        Transient="yes",
        User="1000",
        Group="1000",
        Type="exec",
        WorkingDirectory=context.source,
        Delegate="yes",
        DelegateSubgroup="manager",
        ExitType="cgroup",
        CollectMode="inactive",
        NoNewPrivileges="yes",
        Restart="no",
        KillMode="control-group",
        KillSignal="6",
        FinalKillSignal="9",
        SendSIGKILL="yes",
        LimitCORE="0",
        UMask="0077",
        TimeoutStartUSec="30s",
        TimeoutStopUSec="15s",
        RuntimeMaxUSec="1min",
        InvocationID=context.invocation,
        ControlGroup=context.control.rsplit("/", 1)[0],
        MainPID="100",
        ExecMainPID="100",
        ActiveState="active",
        SubState="running",
    )
    if terminal:
        data.update(
            ActiveState="inactive",
            SubState="dead",
            ExecMainCode="1",
            ExecMainStatus="0",
            Result="success",
        )
    return data


def test_import_does_not_execute_host_interfaces(modules):
    s, launcher = modules
    assert s._client is None
    assert launcher.PROPERTIES["Delegate"] == ""


def test_local_hello_cannot_accept_hosted_context_without_placement(modules, context, monkeypatch):
    import struct

    s, _ = modules
    channel = SimpleNamespace(
        set_inheritable=lambda flag: None,
        setblocking=lambda flag: None,
        getsockopt=lambda *args: struct.pack("3i", 100, os.getuid(), os.getgid()),
    )
    fake_os = SimpleNamespace(**vars(os))
    fake_os.environ = {s.CHANNEL: "9:" + context.nonce + ":100"}
    fake_os.getppid = lambda: 100
    fake_os.getpid = lambda: 101
    monkeypatch.setattr(s, "os", fake_os)
    monkeypatch.setattr(
        s,
        "socket",
        SimpleNamespace(
            socket=lambda **kwargs: channel,
            SOL_SOCKET=socket.SOL_SOCKET,
            SO_PEERCRED=socket.SO_PEERCRED,
        ),
    )
    monkeypatch.setattr(s, "self_group", lambda: context.control)
    monkeypatch.setattr(
        s,
        "receive",
        lambda *args: {
            "kind": "hello",
            "data": {
                "manager_pid": 100,
                "client_pid": 101,
                "task_nonce": context.nonce,
                "scope": context.control,
                "context": context.canonical(),
                "context_digest": context.digest(),
            },
        },
    )
    with pytest.raises(s.SupervisionFailure, match="local client context"):
        s.client_channel(context.deadline)
    assert s._client is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("tag", "local"),
        ("nonce", "x" * 32),
        ("uid", 0),
        ("gid", True),
        ("groups", [1000, 1000]),
        ("control", "/same-prefix/manager"),
        ("payload", "/same-prefix/payload"),
        ("invocation", "missing"),
        ("deadline", float("inf")),
        ("extra", "ignored"),
    ],
)
def test_exact_context_rejects_malformed_fields(modules, context, key, value):
    s, _ = modules
    data = context.wire()
    data[key] = value
    with pytest.raises((s.SupervisionFailure, ValueError, TypeError)):
        s.HostedContext.parse(json.dumps(data, sort_keys=True, separators=(",", ":")))


def test_local_context_schema_unchanged_and_no_hosted_bypass(modules, context):
    s, _ = modules
    assert tuple(s.InvocationContext.__dataclass_fields__) == (
        "task",
        "owner",
        "nonce",
        "source",
        "resources",
        "evidence",
        "manifest",
        "interpreter",
    )
    with pytest.raises(s.SupervisionFailure):
        s.InvocationContext.parse(context.canonical())


def test_recipe_exact_fixed_privilege_and_environment(modules, context):
    _, launcher = modules
    argv = launcher.recipe(seed(context))
    assert argv[:4] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/systemd-run"]
    assert "--property=Delegate=" in argv
    assert "--property=Delegate=yes" not in argv
    assert "--property=RuntimeMaxSec=60" in argv
    assert "--property=TimeoutStopSec=15" in argv
    assert "--expand-environment=no" in argv
    assert "-I" not in argv and "--collect" not in argv
    assert not any(x in argv for x in ("stop", "kill", "reset-failed"))
    assert {x.split("=", 2)[1] for x in argv if x.startswith("--setenv=")} == {
        "HOME",
        "PATH",
        "UV_PROJECT_ENVIRONMENT",
        "PYTHONPATH",
        "PYTHONFAULTHANDLER",
        "PYTHONUNBUFFERED",
        "DAEMON_PYTEST_ASYNCIO_DUMP_S",
        "TASKS_TEST_DATABASE_URL",
    }


@pytest.mark.parametrize(
    "key",
    [
        "Delegate",
        "Transient",
        "User",
        "Group",
        "InvocationID",
        "ControlGroup",
        "RuntimeMaxUSec",
        "TimeoutStopUSec",
        "CapabilityBoundingSet",
        "NoNewPrivileges",
    ],
)
def test_readback_mismatch_never_accepts(modules, context, key):
    s, launcher = modules
    data = properties(launcher, context)
    data[key] = "wrong"
    with pytest.raises(s.SupervisionFailure):
        launcher.validate_properties(data, seed(context), context=context)


@pytest.mark.parametrize(
    "fact", ["direct_reaped", "no_children", "domain_empty", "stdout_eof", "stderr_eof"]
)
def test_every_payload_completion_fact_is_mandatory(modules, context, fact):
    s, launcher = modules
    data = receipt(context)
    data["facts"][fact] = False
    with pytest.raises(s.SupervisionFailure):
        launcher.verify_receipt(data, context, 100)


@pytest.mark.parametrize(
    "mutation",
    [
        "sudo-signal",
        "missing-eof",
        "manager-alive",
        "unit-signal",
        "wrong-invocation",
        "no-terminal",
    ],
)
def test_finality_independent_of_zero_launcher_status(modules, context, mutation):
    s, launcher = modules
    data = properties(launcher, context, terminal=True)
    status, dead, eofs = 0, True, {"stdout", "stderr"}
    if mutation == "sudo-signal":
        status = -9
    elif mutation == "missing-eof":
        eofs = {"stdout"}
    elif mutation == "manager-alive":
        dead = False
    elif mutation == "unit-signal":
        data["ExecMainCode"] = "2"
    elif mutation == "wrong-invocation":
        data["InvocationID"] = "c" * 32
    else:
        data["LoadState"] = "not-found"
    with pytest.raises(s.SupervisionFailure):
        launcher.terminal_gate(
            data, seed(context), context, 100, receipt(context), status, dead, eofs
        )


def test_terminal_positive_requires_all_actual_correlations(modules, context):
    _, launcher = modules
    launcher.terminal_gate(
        properties(launcher, context, terminal=True),
        seed(context),
        context,
        100,
        receipt(context),
        0,
        True,
        {"stdout", "stderr"},
    )


@pytest.mark.parametrize(
    "raw", [b"", b"populated 1\n", b"populated 0", b"populated 0\npopulated 0\n", b"frozen 0\n"]
)
def test_empty_domain_requires_authentic_complete_sample(modules, monkeypatch, raw):
    s, launcher = modules
    monkeypatch.setattr(os, "pread", lambda *args: raw)
    with pytest.raises(s.SupervisionFailure):
        launcher.read_empty(7)


def test_vanished_events_never_means_empty(modules, monkeypatch):
    _, launcher = modules

    def vanished(*args):
        raise OSError(19, "inactive kernfs")

    monkeypatch.setattr(os, "pread", vanished)
    with pytest.raises(OSError):
        launcher.read_empty(7)


@pytest.mark.parametrize(
    "kind,seq,phase",
    [("startup-ack", 1, "positive"), ("final-ack", 0, "positive"), ("startup-ack", 0, "suite")],
)
def test_ack_phase_and_state_not_interchangeable(modules, context, kind, seq, phase):
    s, _ = modules
    left, right = socket.socketpair()
    try:
        frame = {
            "tag": s.HOSTED_TAG,
            "nonce": context.nonce,
            "phase": phase,
            "context_digest": context.digest(),
            "seq": seq,
            "kind": kind,
            "data": {},
        }
        right.sendall((s.hosted_canonical(frame) + "\n").encode())
        with pytest.raises(s.SupervisionFailure):
            s.hosted_read_frame(left.fileno(), context, 0, "startup-ack", context.deadline)
    finally:
        left.close()
        right.close()


def test_budget_cannot_borrow_termination_grace(modules, context):
    s, _ = modules
    request = SimpleNamespace(deadline=context.deadline - s.RESCUE_SECONDS)
    with pytest.raises(s.SupervisionFailure):
        s.hosted_request_budget(context, request)
    s.hosted_request_budget(
        context, SimpleNamespace(deadline=context.deadline - s.RESCUE_SECONDS - 1)
    )


def test_runner_uses_authenticated_hosted_cache_and_refuses_before_send(
    modules, context, monkeypatch
):
    s, _ = modules
    channel = object()
    monkeypatch.setattr(s, "_client", (os.getpid(), channel, context))
    monkeypatch.setattr(s, "client_channel", lambda deadline: channel)

    def forbidden(*args):
        pytest.fail("over-budget hosted request reached send")

    monkeypatch.setattr(s, "send", forbidden)
    with pytest.raises(s.SupervisionFailure, match="ceilings exceed phase budget"):
        s.run_runner(["synthetic"], cwd=context.source, env={}, bound_seconds=60)


def test_final_ack_has_direct_exit_not_python_shutdown(modules):
    s, _ = modules
    tree = ast.parse(Path(s.__file__).read_text())
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "hosted_manager"
    )
    last = function.body[-1]
    assert isinstance(last, ast.Expr) and isinstance(last.value, ast.Call)
    assert ast.unparse(last.value.func) == "os._exit"
    assert ast.unparse(function.body[-2]).startswith("hosted_demand(hosted_read_frame")


@pytest.mark.parametrize(
    "peer_pid,peer_uid,parent,group",
    [
        (100, 1000, 101, "control"),
        (99, 1000, 100, "control"),
        (100, 0, 100, "control"),
        (100, 1000, 100, "payload"),
        (100, 1000, 100, "same-prefix-wrong-group"),
    ],
)
def test_wrong_peer_or_initial_role_group_rejects_before_placement(
    modules,
    context,
    monkeypatch,
    peer_pid,
    peer_uid,
    parent,
    group,
):
    import struct

    s, _ = modules
    channel = SimpleNamespace(getsockopt=lambda *args: struct.pack("3i", peer_pid, peer_uid, 1000))
    monkeypatch.setattr(os, "getppid", lambda: parent)
    monkeypatch.setattr(
        s,
        "self_group",
        lambda: (
            getattr(context, group)
            if group in {"control", "payload"}
            else context.control + "-other"
        ),
    )

    def forbidden(*args):
        pytest.fail("foreign role reached placement/credential I/O")

    monkeypatch.setattr(s, "hosted_open_group", forbidden)
    monkeypatch.setattr(s, "hosted_credentials", forbidden)
    with pytest.raises(s.SupervisionFailure):
        s.hosted_place(context, "client", 100, channel)


def test_output_is_data_not_control_and_sequences_cannot_replay(modules, context):
    import io

    s, launcher = modules
    reader = launcher.OuterReader.__new__(launcher.OuterReader)
    reader.log = io.BytesIO()
    reader.seq = 1
    reader.totals = {"stdout": 0, "stderr": 0}
    payload = b'{"kind":"final","data":{"facts":true}}\xff'
    common = {
        "tag": s.HOSTED_TAG,
        "nonce": context.nonce,
        "phase": context.phase,
        "context_digest": context.digest(),
    }
    frames = iter(
        [
            {**common, "seq": 1, "kind": "stdout", "data": base64.b64encode(payload).decode()},
            {**common, "seq": 2, "kind": "final", "data": receipt(context)},
        ]
    )
    reader.next = lambda: next(frames)
    assert reader.checked(context)["kind"] == "final"
    assert reader.log.getvalue() == payload and reader.totals["stdout"] == len(payload)
    reader.next = lambda: {**common, "seq": 1, "kind": "stdout", "data": "YQ=="}
    with pytest.raises(s.SupervisionFailure):
        reader.checked(context)


def test_simultaneous_payload_pipes_are_drained_while_manager_busy(modules, context, monkeypatch):
    s, _ = modules
    outputs = []
    monkeypatch.setattr(
        s,
        "hosted_frame",
        lambda fd, ctx, seq, kind, data, end: outputs.append((seq, kind, base64.b64decode(data))),
    )
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    process = SimpleNamespace(stdout=os.fdopen(out_r, "rb"), stderr=os.fdopen(err_r, "rb"))
    pipes = s.HostedPipes(process, context, threading.Lock())
    payload = b'{"kind":"final-ack"}\xff' * 10000

    def write_all(fd):
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view) :]
        finally:
            os.close(fd)

    writers = [threading.Thread(target=write_all, args=(fd,)) for fd in (out_w, err_w)]
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join(10)
        assert not thread.is_alive()
    pipes.finish()
    assert [seq for seq, _, _ in outputs] == list(range(1, len(outputs) + 1))
    for name in ("stdout", "stderr"):
        assert b"".join(chunk for _, kind, chunk in outputs if kind == name) == payload
    assert pipes.eofs == {"stdout", "stderr"}


class ManagerExit(BaseException):
    """Intercept only the terminal os._exit in the synthetic manager."""


@pytest.fixture
def manager_model(modules, context, monkeypatch):
    """Run the real manager control flow against entirely synthetic host I/O."""
    s, launcher = modules
    events = []
    state = SimpleNamespace(unexpected=[], retained=False, ack="valid", eof=True)
    package = ModuleType("scripts")
    setattr(package, "run_hosted_backend_pytest", launcher)
    monkeypatch.setitem(sys.modules, "scripts", package)
    local = SimpleNamespace(recv=lambda *args: b"x", close=lambda: events.append("local-close"))
    remote = SimpleNamespace(fileno=lambda: 9, close=lambda: events.append("remote-close"))
    polls = iter((None, 0))
    client = SimpleNamespace(pid=101, poll=lambda: next(polls))
    request = SimpleNamespace(nonce="c" * 32, deadline=context.deadline - s.RESCUE_SECONDS - 1)
    messages = iter(
        (
            {
                "kind": "hosted-placed",
                "data": {
                    "pid": 101,
                    "role": "client",
                    "group": context.payload,
                    "context_digest": context.digest(),
                },
            },
            {"kind": "run", "data": {}, "nonce": request.nonce},
        )
    )

    def waitpid(*args):
        events.append("wait-all")
        if state.unexpected:
            return state.unexpected.pop(0)
        raise ChildProcessError

    def exit_manager(status):
        events.append(("exit", status))
        raise ManagerExit(status)

    fake_os = SimpleNamespace(**vars(os))
    fake_os.environ = dict.fromkeys(s.HOSTED_ENV, "synthetic")
    fake_os.getpid = lambda: 100
    fake_os.open = lambda *args, **kwargs: 7
    fake_os.close = lambda fd: events.append(("close", fd))
    fake_os.set_blocking = lambda *args: None
    fake_os.pidfd_open = lambda *args: 8
    fake_os.waitpid = waitpid
    fake_os._exit = exit_manager
    monkeypatch.setattr(s, "os", fake_os)
    monkeypatch.setattr(
        s, "subprocess", SimpleNamespace(Popen=lambda *args, **kwargs: client, DEVNULL=-3, PIPE=-1)
    )
    monkeypatch.setattr(s, "select", SimpleNamespace(select=lambda fds, *args: (fds, [], [])))
    monkeypatch.setattr(s, "subreaper", lambda: events.append("subreaper"))
    monkeypatch.setattr(s, "hosted_open_group", lambda *args: 6)
    monkeypatch.setattr(s, "fd_identity", lambda fd: (55, fd, 1000, 1000, 448))
    monkeypatch.setattr(s, "pair", lambda: (local, remote))
    monkeypatch.setattr(s, "receive", lambda *args: next(messages))
    monkeypatch.setattr(s, "request_from", lambda *args: request)
    monkeypatch.setattr(s, "send", lambda channel, nonce, seq, kind, data, end: events.append(kind))
    monkeypatch.setattr(s, "finish_direct", lambda *args: events.append("direct-reap") or 0)
    monkeypatch.setattr(launcher, "validate_manager", lambda *args: events.append("validate"))
    monkeypatch.setattr(launcher, "phase_args", lambda *args: [])
    monkeypatch.setattr(launcher, "check_process_group", lambda *args: events.append("placement"))
    monkeypatch.setattr(launcher, "read_empty", lambda *args: events.append("payload-empty"))
    monkeypatch.setattr(s, "hosted_only_manager", lambda *args: events.append("owned-tree-empty"))

    def serve(channel, root, group, req, retained, ctx):
        events.append("fixture-service")
        if state.retained:
            retained.append({"release_verified": False})

    monkeypatch.setattr(s, "manager_one", serve)
    monkeypatch.setattr(
        s,
        "private_record",
        lambda path: {
            "result": {
                "ok": True,
                "rescue_used": False,
                "facts": dict.fromkeys(
                    ("direct_reaped", "no_children", "domain_empty", "stdout_eof", "stderr_eof"),
                    True,
                ),
            }
        },
    )

    class Pipes:
        seq = 1
        totals = {"stdout": 0, "stderr": 0}

        def __init__(self, *args):
            events.append("drains-start")

        def check(self):
            events.append("drains-check")

        def finish(self):
            s.hosted_demand(state.eof, "synthetic missing payload EOF")
            events.append("payload-eofs")

    monkeypatch.setattr(s, "HostedPipes", Pipes)
    frames = {}

    def frame(fd, ctx, seq, kind, data, end):
        events.append(kind)
        frames[kind] = data

    def ack(fd, ctx, seq, kind, end):
        events.append(kind)
        if kind == "startup-ack":
            return {}
        if state.ack == "eof":
            raise s.SupervisionFailure("synthetic ACK EOF")
        import hashlib

        digest = hashlib.sha256(s.hosted_canonical(frames["final"]).encode("ascii")).hexdigest()
        return {"receipt_digest": digest if state.ack == "valid" else "wrong"}

    monkeypatch.setattr(s, "hosted_frame", frame)
    monkeypatch.setattr(s, "hosted_read_frame", ack)
    return SimpleNamespace(run=lambda: s.hosted_manager(context), state=state, events=events)


def test_manager_orders_all_proof_before_receipt_and_terminal_ack(manager_model):
    model = manager_model
    with pytest.raises(ManagerExit) as caught:
        model.run()
    assert caught.value.args == (0,)
    ordered = [
        "subreaper",
        "bootstrap",
        "startup-ack",
        "hosted-hello",
        "placement",
        "hosted-exec",
        "fixture-service",
        "direct-reap",
        "payload-eofs",
        "wait-all",
        "payload-empty",
        "owned-tree-empty",
        "final",
        "final-ack",
        ("exit", 0),
    ]
    positions = [model.events.index(event) for event in ordered]
    assert positions == sorted(positions)
    assert model.events[-2:] == ["final-ack", ("exit", 0)]


@pytest.mark.parametrize("children", [[(0, 0)], [(303, 0)]])
def test_manager_rejects_live_or_already_reaped_unexpected_child(modules, manager_model, children):
    s, _ = modules
    manager_model.state.unexpected = children.copy()
    with pytest.raises(s.SupervisionFailure, match="unexpected"):
        manager_model.run()
    assert "direct-reap" in manager_model.events
    assert "final" not in manager_model.events


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("retained", True, "unresolved worker"),
        ("eof", False, "missing payload EOF"),
        ("ack", "wrong", "final ACK receipt binding"),
        ("ack", "eof", "ACK EOF"),
    ],
)
def test_manager_missing_proof_or_ack_never_exits_successfully(
    modules, manager_model, field, value, match
):
    s, _ = modules
    setattr(manager_model.state, field, value)
    with pytest.raises(s.SupervisionFailure, match=match):
        manager_model.run()
    assert ("exit", 0) not in manager_model.events
    if field != "ack":
        assert "final" not in manager_model.events


def test_payload_drainer_propagates_frame_write_failure(modules, context, monkeypatch):
    s, _ = modules

    def broken(*args):
        raise OSError("synthetic broken transport")

    monkeypatch.setattr(s, "hosted_frame", broken)
    descriptors = [os.pipe(), os.pipe()]
    for _, writer in descriptors:
        os.write(writer, b"payload")
        os.close(writer)
    process = SimpleNamespace(
        stdout=os.fdopen(descriptors[0][0], "rb"), stderr=os.fdopen(descriptors[1][0], "rb")
    )
    pipes = s.HostedPipes(process, context, threading.Lock())
    with pytest.raises(s.SupervisionFailure, match="drain/write failure"):
        pipes.finish()
    assert all(not thread.is_alive() for thread in pipes.threads)
    assert set(pipes.errors) == {"stdout", "stderr"}


@pytest.fixture
def outer_model(modules, context, monkeypatch):
    """Exercise the real outer state machine without services or real host FDs."""
    s, launcher = modules
    events = []
    state = SimpleNamespace(acked=False, fault=None, identities=0, readbacks=0)
    payload_receipt = receipt(context)
    stream = SimpleNamespace(fileno=lambda: 9, close=lambda: events.append("stream-close"))
    process = SimpleNamespace(
        pid=99, stdin=stream, stdout=stream, stderr=stream, returncode=0, poll=lambda: 0
    )
    fake_os = SimpleNamespace(**vars(os))
    fake_os.pidfd_open = lambda pid, flags: pid
    fake_os.open = lambda *args, **kwargs: 7
    fake_os.close = lambda fd: events.append(("close", fd))
    fake_os.fsync = lambda fd: events.append("log-sync")
    monkeypatch.setattr(launcher, "os", fake_os)
    monkeypatch.setattr(
        launcher, "subprocess", SimpleNamespace(Popen=lambda *args, **kwargs: process, PIPE=-1)
    )
    monkeypatch.setattr(launcher, "write_private", lambda *args: None)
    # This model starts after filesystem/venv admission; it does not provision
    # a hosted root or claim to qualify the real runner's host prerequisites.
    monkeypatch.setattr(launcher, "validate_seed", lambda *args: events.append("seed-admitted"))
    monkeypatch.setattr(launcher, "namespace_check", lambda *args: events.append("namespace"))
    monkeypatch.setattr(launcher, "check_process_group", lambda *args: events.append("placement"))
    monkeypatch.setattr(s, "hosted_open_group", lambda *args: 6)

    def identity(fd):
        state.identities += 1
        return [
            55,
            999 if state.fault == "identity" and state.identities > 4 else fd,
            1000,
            1000,
            448,
        ]

    monkeypatch.setattr(s, "fd_identity", identity)

    def readback(*args, **kwargs):
        state.readbacks += 1
        events.append("terminal-readback" if state.acked else "active-readback")
        if state.readbacks == 1:
            return {"LoadState": "not-found"}
        if state.acked and state.fault == "metadata":
            raise s.SupervisionFailure("synthetic terminal metadata unavailable")
        return properties(launcher, context, terminal=state.acked)

    monkeypatch.setattr(launcher, "readback", readback)

    def empty(fd):
        events.append("payload-empty")
        if state.fault == "payload" and "receipt" in events:
            raise s.SupervisionFailure("synthetic populated payload")

    monkeypatch.setattr(launcher, "read_empty", empty)

    def only_manager(*args):
        events.append("only-manager")
        if state.fault == "sibling" and "receipt" in events:
            raise s.SupervisionFailure("synthetic foreign sibling member")

    monkeypatch.setattr(s, "hosted_only_manager", only_manager)
    monkeypatch.setattr(
        launcher,
        "select",
        SimpleNamespace(
            select=lambda fds, *args: (
                fds if state.acked or state.fault == "manager-dead" else [],
                [],
                [],
            )
        ),
    )

    def frame(fd, ctx, seq, kind, data, deadline):
        events.append(kind)
        s.remaining(deadline)
        if kind == "final-ack":
            state.acked = True

    monkeypatch.setattr(s, "hosted_frame", frame)

    class Reader:
        seq = 1
        totals = {"stdout": 0, "stderr": 0}

        def __init__(self, *args):
            self.eofs = set()
            self.frames = []
            self.buffer = bytearray()
            self.selector = SimpleNamespace(close=lambda: events.append("selector-close"))

        def next(self):
            events.append("bootstrap")
            return {
                "kind": "bootstrap",
                "seq": 0,
                "tag": context.tag,
                "nonce": context.nonce,
                "phase": context.phase,
                "context_digest": context.digest(),
                "data": {
                    "pid": 100,
                    "context": context.canonical(),
                    "events_identity": [55, 7, 1000, 1000, 448],
                },
            }

        def checked(self, ctx):
            events.append("receipt")
            return {"kind": "final", "seq": 1, "data": payload_receipt}

        def tick(self):
            events.append("outer-eofs")
            if state.fault == "eof":
                raise s.SupervisionFailure("synthetic missing outer EOF deadline")
            self.eofs = {"stdout", "stderr"}
            if state.fault == "post-ack":
                self.buffer.extend(b"unexpected")

    monkeypatch.setattr(launcher, "OuterReader", Reader)
    log = SimpleNamespace(flush=lambda: events.append("log-flush"), fileno=lambda: 5)
    return SimpleNamespace(
        run=lambda: launcher.run_phase(seed(context), log, {}), state=state, events=events
    )


def test_outer_accepts_only_after_independent_barrier_and_terminal_evidence(outer_model):
    result = outer_model.run()
    events = outer_model.events
    assert result["manager_dead"] and result["outer_stdout_eof"] and result["outer_stderr_eof"]
    assert result["sudo_direct_status"] == 0
    assert events.index("receipt") < events.index("final-ack") < events.index("terminal-readback")
    between = events[events.index("receipt") + 1 : events.index("final-ack")]
    assert between == ["payload-empty", "only-manager", "active-readback", "log-flush", "log-sync"]
    assert events.index("outer-eofs") < events.index("terminal-readback")


@pytest.mark.parametrize(
    "fault,ack_allowed",
    [
        ("identity", False),
        ("payload", False),
        ("sibling", False),
        ("manager-dead", False),
        ("metadata", True),
        ("eof", True),
        ("post-ack", True),
    ],
)
def test_outer_missing_proof_never_returns_success(modules, outer_model, fault, ack_allowed):
    s, _ = modules
    outer_model.state.fault = fault
    with pytest.raises(s.SupervisionFailure):
        outer_model.run()
    assert outer_model.state.acked is ack_allowed


@pytest.mark.parametrize("raw", [b'{"kind":', b'{"kind":"final"}\n', b"{}\n"])
def test_real_outer_reader_rejects_partial_or_malformed_frames(modules, context, raw):
    import io

    s, launcher = modules
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    process = SimpleNamespace(stdout=os.fdopen(out_r, "rb"), stderr=os.fdopen(err_r, "rb"))
    os.write(out_w, raw)
    os.close(out_w)
    os.close(err_w)
    reader = launcher.OuterReader(process, io.BytesIO(), context.deadline)
    try:
        with pytest.raises(s.SupervisionFailure):
            reader.next()
    finally:
        reader.selector.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize("fault", ["nonce", "digest", "duplicate", "eof", "expired"])
def test_actual_ack_transport_rejects_foreign_replayed_lost_or_late_ack(modules, context, fault):
    s, _ = modules
    frame = {
        "tag": s.HOSTED_TAG,
        "nonce": context.nonce,
        "phase": context.phase,
        "context_digest": context.digest(),
        "seq": 1,
        "kind": "final-ack",
        "data": {},
    }
    if fault == "nonce":
        frame["nonce"] = "c" * 32
    elif fault == "digest":
        frame["context_digest"] = "0" * 64
    raw = (s.hosted_canonical(frame) + "\n").encode()
    left, right = socket.socketpair()
    try:
        if fault != "eof":
            right.sendall(raw * (2 if fault == "duplicate" else 1))
        right.shutdown(socket.SHUT_WR)
        deadline = time.monotonic() - 1 if fault == "expired" else context.deadline
        with pytest.raises(s.SupervisionFailure):
            s.hosted_read_frame(left.fileno(), context, 1, "final-ack", deadline)
    finally:
        left.close()
        right.close()


def test_outer_logger_short_write_cannot_count_payload_as_delivered(modules, context):
    s, launcher = modules
    reader = launcher.OuterReader.__new__(launcher.OuterReader)
    reader.log = SimpleNamespace(write=lambda raw: len(raw) - 1)
    reader.seq = 1
    reader.totals = {"stdout": 0, "stderr": 0}
    reader.next = lambda: {
        "tag": s.HOSTED_TAG,
        "nonce": context.nonce,
        "phase": context.phase,
        "context_digest": context.digest(),
        "seq": 1,
        "kind": "stdout",
        "data": base64.b64encode(b"payload").decode(),
    }
    with pytest.raises(s.SupervisionFailure, match="exact payload log bytes"):
        reader.checked(context)
    assert reader.totals == {"stdout": 0, "stderr": 0}
