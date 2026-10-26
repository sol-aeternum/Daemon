"""Eager-entry regressions; local socket models do not qualify host ownership."""

import builtins
import os
from pathlib import Path
import runpy
import select
import socket
import sys
import time
from types import SimpleNamespace

import pytest

from tests import backend_pytest_client as client
from tests import backend_pytest_supervisor as s


def _context():
    source = Path(s.__file__).resolve().parents[1]
    base, state, _ = s.layout(source)
    return s.InvocationContext(
        source.name,
        "ses_Model",
        "a" * 32,
        str(source),
        str(base / "resources" / source.name),
        str(base / "evidence" / source.name),
        str(state / "owned-manifests" / (source.name + ".json")),
        str(source / ".venv/bin/python"),
    )


def _observe_pytest(events, selection):
    original_import = builtins.__import__

    def main(argv):
        assert argv == selection
        events.append("pytest-main")
        return 43

    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pytest":
            events.append("pytest-import")
            return SimpleNamespace(main=main)
        return original_import(name, globals, locals, fromlist, level)

    return importing


@pytest.fixture
def private_hello(monkeypatch):
    # Same-process UNIX peers: only parent/scope expectations are synthetic.
    # No registration, cgroup, process, reaping or host authority is exercised.
    manager, remote = s.pair()
    socket_type = socket.socket
    claimed = []
    pid = os.getpid()
    nonce = "b" * 32
    context = _context()

    def claim(*, fileno):
        assert fileno == remote.fileno() and not claimed
        channel = socket_type(fileno=remote.detach())
        claimed.append(channel)
        return channel

    monkeypatch.setattr(s, "_client", None)
    monkeypatch.setattr(s, "self_group", lambda: "/fictional-own.scope")
    monkeypatch.setattr(s.os, "getppid", lambda: pid)
    monkeypatch.setattr(s.socket, "socket", claim)
    monkeypatch.setenv(s.CHANNEL, f"{remote.fileno()}:{nonce}:{pid}")
    data = {
        "manager_pid": pid,
        "client_pid": pid,
        "task_nonce": context.nonce,
        "scope": "/fictional-own.scope",
        **s.context_frame(context),
    }
    try:
        yield manager, nonce, data, context
    finally:
        for channel in claimed:
            channel.close()
        remote.close()
        manager.close()


def test_eager_entry_authenticates_before_pytest_import_and_reuses_cache(
    private_hello, monkeypatch
):
    manager, nonce, data, context = private_hello
    s.send(manager, nonce, 0, "hello", data, time.monotonic() + 2)
    events = []
    selection = ["-q", "-x", "tests/test_managed_backend_pytest.py"]
    original_channel = s.client_channel
    before = time.monotonic()

    def authenticate(deadline):
        assert before + 30 <= deadline <= time.monotonic() + 30
        events.append("authenticate")
        return original_channel(deadline)

    monkeypatch.setattr(s, "client_channel", authenticate)
    monkeypatch.setattr(sys, "argv", ["backend_pytest_client.py", *selection])
    with pytest.raises(SystemExit) as result:
        with monkeypatch.context() as entry:
            entry.setattr(builtins, "__import__", _observe_pytest(events, selection))
            runpy.run_path(str(Path(client.__file__)), run_name="__main__")
    assert result.value.code == 43
    assert events == ["authenticate", "pytest-import", "pytest-main"]
    assert client.supervisor is s
    cached = s._client
    assert cached[0] == os.getpid() and cached[2] == context
    assert original_channel(time.monotonic() + 2) is cached[1]
    assert not select.select([cached[1]], [], [], 0)[0]  # hello already consumed


def test_eager_entry_missing_channel_prevents_pytest_import(monkeypatch):
    monkeypatch.setattr(s, "_client", None)
    monkeypatch.delenv(s.CHANNEL, raising=False)
    events, selection = [], []
    with pytest.raises(s.SupervisionFailure, match="missing original"):
        with monkeypatch.context() as entry:
            entry.setattr(builtins, "__import__", _observe_pytest(events, selection))
            client.main(selection)
    assert events == [] and s._client is None


@pytest.mark.parametrize("failure", ["eof", "malformed", "reset"])
def test_eager_entry_failed_handshake_prevents_pytest_import(private_hello, monkeypatch, failure):
    manager, nonce, _, _ = private_hello
    if failure == "eof":
        manager.close()
    elif failure == "malformed":
        s.send(manager, nonce, 0, "hello", {}, time.monotonic() + 2)
    else:

        def reset(*args):
            raise ConnectionResetError(104, "synthetic private-channel reset")

        monkeypatch.setattr(s, "receive", reset)
    error = ConnectionResetError if failure == "reset" else s.SupervisionFailure
    events, selection = [], []
    with pytest.raises(error):
        with monkeypatch.context() as entry:
            entry.setattr(builtins, "__import__", _observe_pytest(events, selection))
            client.main(selection)
    assert events == [] and s._client is None


@pytest.mark.skipif(sys.platform != "linux", reason="Linux UNIX sequenced-packet close semantics")
@pytest.mark.parametrize("consume", [False, True], ids=["unread-reset", "consumed-eof"])
def test_synthetic_seqpacket_unread_vs_consumed_hello_close(consume):
    # Real local sockets only; not a managed launch or historical syscall trace.
    manager, remote = s.pair()
    nonce = "c" * 32
    try:
        s.send(manager, nonce, 0, "hello", {}, time.monotonic() + 2)
        if consume:
            assert s.receive(remote, nonce, 0, time.monotonic() + 2)["kind"] == "hello"
        remote.close()
        assert select.select([manager], [], [], 2)[0] == [manager]
        if consume:
            assert manager.recv(1, socket.MSG_PEEK) == b""
        else:
            with pytest.raises(ConnectionResetError):
                manager.recv(1, socket.MSG_PEEK)
    finally:
        remote.close()
        manager.close()


def test_manager_selects_eager_entry_without_changing_selection_or_status(monkeypatch):
    context = _context()
    selection = ["-q", "-x", "tests/test_managed_backend_pytest.py"]
    calls, closed, sent = [], [], []
    local = SimpleNamespace(close=lambda: closed.append("local"))
    remote = SimpleNamespace(fileno=lambda: -200, close=lambda: closed.append("remote"))

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=9001, poll=lambda: 43, returncode=43)

    monkeypatch.setattr(s, "bootstrap", lambda value: ("/fictional-own.scope", -201))
    monkeypatch.setattr(s, "pair", lambda: (local, remote))
    monkeypatch.setattr(s.subprocess, "Popen", popen)
    monkeypatch.setattr(s, "send", lambda *args: sent.append(args))
    monkeypatch.setattr(s.os, "close", lambda fd: closed.append(fd))
    assert s.manager(selection, context) == 43
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv == [context.interpreter, "-m", "tests.backend_pytest_client", *selection]
    assert options["close_fds"] is True and options["pass_fds"] == (-200,)
    assert options["env"][s.CHANNEL].startswith("-200:")
    assert [message[3] for message in sent] == ["hello"]
    assert closed == ["remote", -201]


@pytest.mark.parametrize(
    "missing",
    [
        "direct_reaped",
        "no_children",
        "domain_empty",
        "stdout_eof",
        "stderr_eof",
    ],
)
def test_eager_entry_does_not_relax_existing_completion_facts(missing):
    facts = dict.fromkeys(
        [
            "direct_reaped",
            "no_children",
            "domain_empty",
            "stdout_eof",
            "stderr_eof",
        ],
        True,
    )
    facts[missing] = False
    request = s.Request((), "/fictional", (), "d" * 32, 1)
    assert not s.complete(facts)
    assert not s.Closure(request, 0, facts, False).complete


def test_client_context_reuses_authenticated_canonical_cache(private_hello):
    manager, nonce, data, context = private_hello
    s.send(manager, nonce, 0, "hello", data, time.monotonic() + 2)
    actual = s.client_context(time.monotonic() + 2)
    cached = s._client
    assert cached is not None
    assert client.supervisor is s
    assert actual == context and actual is cached[2]
    assert s.client_context(time.monotonic() + 2) is actual
    assert s.client_channel(time.monotonic() + 2) is cached[1]
    assert s._client is cached


def test_client_context_missing_post_call_cache_fails_closed(monkeypatch):
    # Explicit fake channel seam: returns without populating the real cache.
    fake_channel = object()
    calls = []

    def fake_client_channel(deadline):
        calls.append(deadline)
        return fake_channel

    monkeypatch.setattr(s, "_client", None)
    monkeypatch.setattr(s, "client_channel", fake_client_channel)
    with pytest.raises(s.SupervisionFailure, match="missing authenticated client context"):
        s.client_context(17)
    assert calls == [17] and s._client is None
