"""Pilot-only acceptor for an explicitly supplied, owned loopback listener.

No socket is created, bound or discovered here. Construction transfers ownership
only on success; rejected listeners remain caller-owned. The future launcher must
establish network-none, backlog/port configuration and descriptor inventory. A
loopback address alone does not establish browser or container containment.
"""

from __future__ import annotations

import asyncio
import socket

from scripts.web_fetch_pilot_io import AsyncFD, SocketConnection, TransportError


class LoopbackAcceptor:
    """One event-loop-bound listener, one outstanding accept, no internal tasks.

    Only already listening IPv4 TCP sockets bound to exactly 127.0.0.1 qualify.
    Child cancellation before returning an accepted socket closes that socket;
    close wakes a waiting accept and releases only explicitly owned resources.
    """

    def __init__(self, listener: socket.socket) -> None:
        self._loop = asyncio.get_running_loop()
        if listener.family != socket.AF_INET or listener.type != socket.SOCK_STREAM:
            raise ValueError("IPv4 TCP listener required")
        address = listener.getsockname()
        if (
            type(address) is not tuple
            or len(address) != 2
            or type(address[0]) is not str
            or address[0] != "127.0.0.1"
            or type(address[1]) is not int
            or not 1 <= address[1] <= 65535
            or listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1
        ):
            raise ValueError("owned listening loopback socket required")
        # AsyncFD makes it nonblocking only after all listener policy checks.
        self._fd = AsyncFD(listener.fileno(), owns_fd=False)
        self._listener = listener
        self._address = address
        self._closed = False
        self._accepting = False

    @property
    def address(self) -> tuple[str, int]:
        return self._address

    async def accept(self) -> SocketConnection | None:
        if asyncio.get_running_loop() is not self._loop:
            raise TransportError("listener used from another event loop")
        if self._closed:
            return None
        if self._accepting:
            raise TransportError("overlapping accept is forbidden")
        self._accepting = True
        accepted: socket.socket | None = None
        try:
            while not self._closed:
                if self._listener.getsockname() != self._address:
                    raise TransportError("listener address changed")
                try:
                    accepted, _ = self._listener.accept()
                except BlockingIOError:
                    try:
                        await self._fd.ready(writing=False)
                    except TransportError:
                        if self._closed:
                            return None
                        raise
                    continue
                except InterruptedError:
                    continue
                # Check the actual connected socket, not accept()'s claimed peer.
                peer = accepted.getpeername()
                if (
                    type(peer) is not tuple
                    or len(peer) != 2
                    or type(peer[0]) is not str
                    or peer[0] != "127.0.0.1"
                    or type(peer[1]) is not int
                    or not 1 <= peer[1] <= 65535
                ):
                    raise TransportError("non-loopback accepted peer")
                connection = SocketConnection(accepted)
                accepted = None  # Connection now owns the socket; no await before return.
                return connection
            return None
        finally:
            self._accepting = False
            if accepted is not None:
                accepted.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # Unregister before releasing the socket fd, avoiding descriptor reuse.
            self._fd.close()
        finally:
            self._listener.close()
