"""Fake listener fixtures only; no real socket creation or service probing."""

from __future__ import annotations

import asyncio
import socket
from typing import cast

import pytest

import scripts.web_fetch_pilot_acceptor as adapters
from scripts.web_fetch_pilot_io import TransportError


class Accepted:
    def __init__(self) -> None:
        self.peer: object = ("127.0.0.1", 23456)
        self.closed = False

    def getpeername(self) -> object:
        return self.peer

    def close(self) -> None:
        self.closed = True


class Listener:
    family = socket.AF_INET
    type = socket.SOCK_STREAM

    def __init__(self) -> None:
        self.address: object = ("127.0.0.1", 12345)
        self.listening = 1
        self.closed = False
        self.accepted: Accepted | None = Accepted()
        self.accept_calls = 0
        self.error: Exception | None = None

    def fileno(self) -> int:
        return 999999

    def getsockname(self) -> object:
        return self.address

    def getsockopt(self, level: int, option: int) -> int:
        assert (level, option) == (socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        return self.listening

    def accept(self) -> tuple[Accepted, tuple[str, int]]:
        self.accept_calls += 1
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        if self.accepted is None:
            raise BlockingIOError
        accepted, self.accepted = self.accepted, None
        return accepted, ("192.0.2.1", 9)  # Deliberately false accept() metadata.

    def close(self) -> None:
        self.closed = True


class FD:
    def __init__(self, fd: int, *, owns_fd: bool) -> None:
        assert fd == 999999 and not owns_fd
        self.closed = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def ready(self, *, writing: bool) -> None:
        assert not writing
        self.entered.set()
        await self.release.wait()
        if self.closed:
            raise TransportError("closed fixture fd")

    def close(self) -> None:
        self.closed = True
        self.release.set()


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "AsyncFD", FD)


@pytest.mark.asyncio
async def test_fake_accepted_socket_ownership_and_actual_peer(
    fakes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    listener = Listener()
    owned = listener.accepted
    seen: list[Accepted] = []
    peers: list[object] = []

    def connection(sock: Accepted, *, peer: object) -> Accepted:
        seen.append(sock)
        peers.append(peer)
        return sock

    monkeypatch.setattr(adapters, "SocketConnection", connection)
    acceptor = adapters.LoopbackAcceptor(cast(socket.socket, listener))
    assert acceptor.address == ("127.0.0.1", 12345)
    assert await acceptor.accept() is owned and seen == [owned]
    assert peers == [("127.0.0.1", 23456)]  # The actual verified peer, never accept() metadata.
    acceptor.close()
    acceptor.close()
    assert listener.closed and owned is not None and not owned.closed
    assert await acceptor.accept() is None
    owned.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peer",
    [("127.0.0.2", 10), ("::1", 10), ("127.0.0.1", True), ("127.0.0.1", 0), ["127.0.0.1", 10]],
)
async def test_fake_unqualified_connected_peer_closed(fakes: None, peer: object) -> None:
    listener = Listener()
    owned = listener.accepted
    assert owned is not None
    owned.peer = peer
    acceptor = adapters.LoopbackAcceptor(cast(socket.socket, listener))
    try:
        with pytest.raises(TransportError):
            await acceptor.accept()
        assert owned.closed
    finally:
        acceptor.close()


@pytest.mark.asyncio
async def test_fake_construction_error_reclaims_accepted_socket(
    fakes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    listener = Listener()
    owned = listener.accepted

    def fail(sock: Accepted, *, peer: object) -> None:
        raise ValueError("injected connection construction failure")

    monkeypatch.setattr(adapters, "SocketConnection", fail)
    acceptor = adapters.LoopbackAcceptor(cast(socket.socket, listener))
    try:
        with pytest.raises(ValueError):
            await acceptor.accept()
        assert owned is not None and owned.closed
    finally:
        acceptor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["close", "cancel"])
async def test_fake_readiness_close_cancel_and_no_overlapping_accept(
    fakes: None, action: str
) -> None:
    listener = Listener()
    listener.accepted = None
    acceptor = adapters.LoopbackAcceptor(cast(socket.socket, listener))
    fd = cast(FD, acceptor._fd)
    task = asyncio.create_task(acceptor.accept())
    try:
        await asyncio.wait_for(fd.entered.wait(), 1)
        with pytest.raises(TransportError, match="overlapping"):
            await acceptor.accept()
        if action == "close":
            acceptor.close()
            assert await asyncio.wait_for(task, 1) is None
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not acceptor._accepting
    finally:
        acceptor.close()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [("0.0.0.0", 10), ("::1", 10), ("127.0.0.1", 0), ("127.0.0.1", True), ["127.0.0.1", 10]],
)
async def test_fake_wrong_bind_rejected_without_taking_ownership(
    address: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    listener = Listener()
    listener.address = address

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid listener must not construct fd adapter")

    monkeypatch.setattr(adapters, "AsyncFD", forbidden)
    with pytest.raises(ValueError):
        adapters.LoopbackAcceptor(cast(socket.socket, listener))
    assert not listener.closed and listener.accept_calls == 0


@pytest.mark.asyncio
async def test_fake_listener_state_and_address_stability(fakes: None) -> None:
    listener = Listener()
    listener.listening = 0
    with pytest.raises(ValueError):
        adapters.LoopbackAcceptor(cast(socket.socket, listener))
    assert not listener.closed
    listener.listening = 1
    acceptor = adapters.LoopbackAcceptor(cast(socket.socket, listener))
    listener.address = ("127.0.0.1", 12346)
    try:
        with pytest.raises(TransportError, match="changed"):
            await acceptor.accept()
        assert listener.accept_calls == 0
    finally:
        acceptor.close()


class ResetAccepted(Accepted):
    def getpeername(self) -> object:
        raise OSError(107, "ENOTCONN")  # Client reset before we looked.


@pytest.mark.asyncio
async def test_fake_client_reset_before_peer_check_is_skipped(
    fakes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    listener = Listener()
    vanished = ResetAccepted()
    listener.accepted = vanished
    good = Accepted()
    original = listener.accept

    def accept() -> tuple[Accepted, tuple[str, int]]:
        pair = original()
        if pair[0] is vanished:
            listener.accepted = good  # The next client is queued behind it.
        return pair

    listener.accept = accept  # type: ignore[method-assign]
    monkeypatch.setattr(adapters, "SocketConnection", lambda sock, *, peer: sock)
    acceptor = adapters.LoopbackAcceptor(cast(socket.socket, listener))
    try:
        assert await acceptor.accept() is good  # The run keeps accepting.
        assert vanished.closed and not good.closed
    finally:
        acceptor.close()
        good.close()
