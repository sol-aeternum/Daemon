"""Child end of the disposable launcher's private, one-shot ownership channel."""

from __future__ import annotations

import os
import hashlib
import socket
import struct
from pathlib import Path
from uuid import UUID

from .model import HistoryError


def fixture_history_id(listener: socket.socket, directory: Path) -> UUID:
    """Bind initial fixture provisioning to the launcher's live listener.

    Linux SO_COOKIE identifies the actual kernel socket across dup/exec. A
    new socket at the same address (or another private channel plus a copied
    handoff ID) cannot select this fixture. This proves neither exit nor ACK.
    """
    if (
        listener.family != socket.AF_INET
        or listener.getsockname()[0] != "127.0.0.1"
        or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
    ):
        raise HistoryError("inbox_launcher_required")
    cookie = listener.getsockopt(socket.SOL_SOCKET, 57, 8)  # Linux UAPI SO_COOKIE
    info = directory.stat()
    boot = UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
    digest = hashlib.sha256(
        b"daemon.disposable-inbox-listener.v1\0"
        + boot.bytes
        + cookie
        + f"/{info.st_dev}/{info.st_ino}".encode("ascii")
    ).digest()
    return UUID(bytes=digest[:16])


class InboxChild:
    def __init__(
        self, channel: socket.socket, listener: socket.socket, path: Path, history_id: UUID
    ):
        self._pid = os.getpid()
        self._parent = os.getppid()
        if channel.family != socket.AF_UNIX or channel.type != socket.SOCK_SEQPACKET:
            raise HistoryError("inbox_launcher_required")
        peer, uid, _ = struct.unpack(
            "3i", channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        )
        root = path.parent.stat()
        if (
            peer != self._parent
            or uid != os.geteuid()
            or os.get_inheritable(channel.fileno())
            or fixture_history_id(listener, path.parent) != history_id
        ):
            raise HistoryError("inbox_launcher_required")
        self._channel = channel
        self._path = path
        self._binding = f"{root.st_dev}:{root.st_ino}:{history_id}"
        channel.settimeout(2)
        frame, _, flags, _ = channel.recvmsg(192)
        parts = frame.split(b" ")
        if flags or len(parts) != 2 or parts[0] != self._binding.encode("ascii"):
            raise HistoryError("inbox_launcher_required")
        self.handoff = None if parts[1] == b"original" else UUID(parts[1].decode("ascii"))
        self._host: object | None = None
        self._acked = False
        channel.setblocking(False)

    def bind(self, host: object, path: Path, history_id: UUID) -> None:
        root = path.parent.stat()
        if (
            self._host is not None
            or path != self._path
            or self._binding != f"{root.st_dev}:{root.st_ino}:{history_id}"
        ):
            raise HistoryError("inbox_launcher_required")
        self._host = host
        self.check(host)

    def check(self, host: object) -> None:
        if host is not self._host or os.getpid() != self._pid or os.getppid() != self._parent:
            raise HistoryError("inbox_launcher_lost")
        # No additional inbound frame is valid. EOF is never a clean shutdown ACK.
        try:
            self._channel.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            return
        raise HistoryError("inbox_launcher_lost")

    def _acknowledge(self, host: object, record: UUID) -> None:
        self.check(host)
        if self.handoff is not None or self._acked:
            raise HistoryError("inbox_retirement_refused")
        self._acked = True
        packet = f"{self._binding} {record}".encode("ascii")
        if self._channel.send(packet) != len(packet):
            raise HistoryError("inbox_retirement_uncertain")
