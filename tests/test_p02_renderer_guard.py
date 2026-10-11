"""Non-destructive checks of the test-only renderer pidfd protocol.

All commit-path signals are mocks. The subprocess check supplies no commit and
uses children which exit via stdin, never a signal or a real browser.
"""

import importlib.util
import io
import json
import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

HELPER = Path(__file__).resolve().parents[1] / "frontend/e2e/renderer_guard.py"


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location("p02_renderer_guard", HELPER)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def virtual(module, monkeypatch):
    closed: list[int] = []
    exited: set[int] = set()
    states = {700: (600, "1"), 701: (700, "2"), 702: (700, "3")}
    send = Mock()
    monkeypatch.setattr(
        module,
        "os",
        SimpleNamespace(
            pidfd_open=lambda pid: pid + 1000,
            close=closed.append,
            getppid=lambda: 600,
        ),
    )
    monkeypatch.setattr(module, "signal", SimpleNamespace(pidfd_send_signal=send, SIGKILL=9))
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(module, "process_state", lambda pid: states[pid])

    def alive(fd):
        if fd in exited:
            raise module.Refused("bound process has exited")

    monkeypatch.setattr(module, "alive", alive)
    record = {
        "root": {"pid": 700, "start": "1"},
        "candidates": [
            {"pid": 701, "start": "2"},
            {"pid": 702, "start": "3"},
        ],
    }
    return SimpleNamespace(
        module=module, closed=closed, exited=exited, states=states, send=send, record=record
    )


def test_commit_selects_retained_handle_once_with_mock_signal(virtual):
    guard = virtual.module.Guard(virtual.record)
    try:
        assert guard.commit({"nonce": guard.nonce, "holder": 701, "peer": 702}) == (701, 702)
        virtual.send.assert_called_once_with(1701, 9)
        with pytest.raises(virtual.module.Refused, match="already attempted"):
            guard.commit({"nonce": guard.nonce, "holder": 702, "peer": 701})
        assert virtual.send.call_count == 1
    finally:
        guard.close()
    assert sorted(virtual.closed) == [1700, 1701, 1702]


@pytest.mark.parametrize(
    "change",
    [
        {"holder": 703},
        {"holder": 702},
        {"holder": True},
        {"peer": -1},
        {"nonce": "wrong"},
        {"extra": 1},
    ],
)
def test_bad_commit_never_signals(virtual, change):
    guard = virtual.module.Guard(virtual.record)
    try:
        with pytest.raises(virtual.module.Refused):
            guard.commit({"nonce": guard.nonce, "holder": 701, "peer": 702, **change})
        virtual.send.assert_not_called()
    finally:
        guard.close()


@pytest.mark.parametrize("fd", [1700, 1701, 1702])
def test_bound_process_exit_prevents_commit_even_if_pid_metadata_matches(virtual, fd):
    guard = virtual.module.Guard(virtual.record)
    try:
        virtual.exited.add(fd)
        with pytest.raises(virtual.module.Refused, match="exited"):
            guard.commit({"nonce": guard.nonce, "holder": 701, "peer": 702})
        virtual.send.assert_not_called()
    finally:
        guard.close()


def test_start_drift_and_lineage_cycle_refuse_without_signal(virtual):
    guard = virtual.module.Guard(virtual.record)
    try:
        virtual.states[701] = (700, "different")
        with pytest.raises(virtual.module.Refused, match="metadata changed"):
            guard.commit({"nonce": guard.nonce, "holder": 701, "peer": 702})
        virtual.send.assert_not_called()
    finally:
        guard.close()
    virtual.states[701] = (702, "2")
    virtual.states[702] = (701, "3")
    with pytest.raises(virtual.module.Refused, match="lineage"):
        virtual.module.Guard(virtual.record)
    virtual.send.assert_not_called()


def test_partial_binding_closes_every_open_handle(virtual, monkeypatch):
    def opening(pid):
        if pid == 702:
            raise ProcessLookupError("gone")
        return pid + 1000

    monkeypatch.setattr(virtual.module.os, "pidfd_open", opening)
    with pytest.raises(ProcessLookupError):
        virtual.module.Guard(virtual.record)
    assert sorted(virtual.closed) == [1700, 1701]
    virtual.send.assert_not_called()


def test_unsupported_pidfd_facilities_refuse(virtual, monkeypatch):
    monkeypatch.delattr(virtual.module.os, "pidfd_open")
    with pytest.raises(virtual.module.Refused, match="facilities required"):
        virtual.module.Guard(virtual.record)
    assert virtual.closed == []
    virtual.send.assert_not_called()


@pytest.mark.parametrize(
    "candidates", [[], [{"pid": 701, "start": "2"}] * 2, [{"pid": 700, "start": "1"}] * 2]
)
def test_ambiguous_candidates_refuse_before_binding(virtual, candidates):
    with pytest.raises(virtual.module.Refused):
        virtual.module.Guard({**virtual.record, "candidates": candidates})
    assert virtual.closed == []
    virtual.send.assert_not_called()


def test_uid_mismatch_refuses_process_read(module, monkeypatch):
    monkeypatch.setattr(
        module,
        "os",
        SimpleNamespace(stat=lambda _path: SimpleNamespace(st_uid=999), geteuid=lambda: 1000),
    )
    with pytest.raises(module.Refused, match="UID"):
        module.process_state(700)


@pytest.mark.parametrize("payload", [b"", b"x" * 8194, b'{"x":1,"x":2}\n', b"not-json\n"])
def test_bad_protocol_input_is_bounded_and_refused(module, monkeypatch, payload):
    with tempfile.TemporaryFile() as file:
        file.write(payload)
        file.seek(0)
        monkeypatch.setattr(module, "sys", SimpleNamespace(stdin=file))
        with pytest.raises((module.Refused, ValueError)):
            module.read_record()


def test_protocol_timeout_refuses_without_read(module, monkeypatch):
    monkeypatch.setattr(module, "select", SimpleNamespace(select=lambda *_args: ([], [], [])))
    monkeypatch.setattr(module, "sys", SimpleNamespace(stdin=io.StringIO()))
    # StringIO has no fd; supply an inert descriptor never read by select.
    module.sys.stdin = SimpleNamespace(fileno=lambda: 0)
    with pytest.raises(module.Refused, match="timed out"):
        module.read_record()


def test_real_bound_children_receive_no_signal_without_commit():
    child_code = "import sys; sys.stdin.readline()"
    root_code = (
        "import subprocess,sys,json; "
        f"children=[subprocess.Popen([sys.executable,'-c',{child_code!r}],stdin=subprocess.PIPE,text=True) for _ in range(2)]; "
        "print(json.dumps([p.pid for p in children]),flush=True); sys.stdin.readline(); "
        "[(p.stdin.write('exit\\n'),p.stdin.flush(),p.wait(timeout=5)) for p in children]"
    )
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    with subprocess.Popen(
        [sys.executable, "-c", root_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    ) as root:
        assert root.stdout is not None and root.stdin is not None
        assert select.select([root.stdout], [], [], 5)[0]
        children = json.loads(root.stdout.readline())

        def record(pid):
            text = Path(f"/proc/{pid}/stat").read_text()
            return {"pid": pid, "start": text[text.rindex(")") + 2 :].split()[19]}

        try:
            request = {"root": record(root.pid), "candidates": [record(pid) for pid in children]}
            result = subprocess.run(
                [sys.executable, str(HELPER)],
                input=json.dumps(request) + "\n",
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
            )
            lines = [json.loads(line) for line in result.stdout.splitlines()]
            assert result.returncode == 1
            assert [line["status"] for line in lines] == ["ready", "refused"]
            assert "without commit" in lines[1]["reason"]
            assert root.poll() is None
            for pid in children:
                assert Path(f"/proc/{pid}/stat").exists()
        finally:
            root.communicate("exit\n", timeout=5)
        assert root.returncode == 0
