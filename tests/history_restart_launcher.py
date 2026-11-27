"""Own one create-new disposable cluster/Redis pair and exactly one backend replacement."""

import asyncio
import fcntl
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
from uuid import UUID

from tests.compute_metadata_support import Cluster
from orchestrator.services.restore_history.inbox_lineage import fixture_history_id
from tests.owned_search_redis import OwnedSearchRedis


class _InboxCluster(Cluster):
    inbox_history_id: UUID


class RestartFixture:
    def __init__(self):
        self.pid = os.getpid()
        base = Path(os.environ["OPENCODE_WORK_TEST_DIR"])
        evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
        self.root = Path(tempfile.mkdtemp(prefix="i", dir=base))
        self.lock = os.open(self.root, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.cluster = _InboxCluster(self.root, evidence)
        self.redis = OwnedSearchRedis(self.root, "isolated-inbox-restart")
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.cluster.inbox_history_id = fixture_history_id(self.listener, self.root)
        self.backend = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.phase = "new"
        self.child = self.channel = None

    async def original(self, s, origin, log, *, fault=None):
        if os.getpid() != self.pid or self.phase != "new" or s.db.cluster is not self.cluster:
            raise RuntimeError("fixture already used or not owned")
        self.phase = "original"
        assert self.cluster.databases == {s.db.name}
        assert s.path == self.root / "h" and s.journal == self.root / "j1"
        assert s.db.host.parent == self.root
        self.packet = dict(
            socket=str(s.db.host),
            database=s.db.name,
            encryption_key=s.encryption_key,
            journal=str(s.journal),
            journal_id=str(s.journal_id),
            path=str(s.path),
            history_id=str(s.identity.history_id),
            key_check=s.identity.key_check,
            inbox_two_jobs=True,
            redis=str(self.redis.socket),
            redis_run=self.redis.run_id,
            origin=origin,
            fault=fault,
        )
        self.log = log
        self.binding = (
            f"{os.fstat(self.lock).st_dev}:{os.fstat(self.lock).st_ino}:{s.identity.history_id}"
        )
        await self._spawn("original")

    async def _spawn(self, value):
        assert self.child is None and self.channel is None
        assert os.path.samestat(self.root.stat(), os.fstat(self.lock))
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.channel = parent
        try:
            packet = {
                **self.packet,
                "listener_fd": self.listener.fileno(),
                "control_fd": child.fileno(),
            }
            environment = {
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "HOME": str(self.root),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "TMPDIR": os.environ["TMPDIR"],
                "DAEMON_ENCRYPTION_KEY": packet["encryption_key"],
            }
            self.child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "tests.history_text_process_driver",
                "managed",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=self.log,
                pass_fds=(child.fileno(), self.listener.fileno()),
                env=environment,
            )
            parent.sendall(f"{self.binding} {value}".encode("ascii"))
            parent.setblocking(False)
            await self.send(json.dumps(packet))
            assert await self.read() == {"stage": "serving"}
        finally:
            child.close()

    async def send(self, message):
        assert self.child and self.child.stdin
        self.child.stdin.write(message.encode() + b"\n")
        await self.child.stdin.drain()

    async def read(self):
        assert self.child and self.child.stdout
        line = await asyncio.wait_for(self.child.stdout.readline(), 40)
        if not line:
            raise RuntimeError("backend exited before its receipt")
        return json.loads(line)

    async def replace(self):
        if os.getpid() != self.pid or self.phase != "original":
            raise RuntimeError("no replacement available")
        self.phase = "consumed"
        assert self.channel and self.child
        if self.child.returncode is not None:
            raise RuntimeError("original not available for retirement")
        await self.send("retire")
        # A refused preflight must not leave a launcher indefinitely waiting
        # for another command. This request consumes the sole replacement.
        assert self.child.stdin is not None
        self.child.stdin.close()
        if await asyncio.wait_for(self.child.wait(), 30) != 0:
            raise RuntimeError("original exit not acknowledged")
        size = struct.calcsize("3i")
        frame, ancillary, flags, _ = self.channel.recvmsg(192, socket.CMSG_SPACE(size))
        if (
            flags
            or not frame.startswith(self.binding.encode() + b" ")
            or len(ancillary) != 1
            or ancillary[0][:2] != (socket.SOL_SOCKET, socket.SCM_CREDENTIALS)
            or struct.unpack("3i", ancillary[0][2]) != (self.child.pid, os.geteuid(), os.getegid())
        ):
            raise RuntimeError("original retirement not acknowledged")
        record = UUID(frame.split(b" ")[1].decode())
        if self.channel.recv(1) != b"":
            raise RuntimeError("unexpected second retirement")
        self.channel.close()
        self.child = self.channel = None
        await self._spawn(str(record))

    async def close_child(self):
        if self.channel:
            self.channel.close()
        if self.child and self.child.returncode is None:
            try:
                await asyncio.wait_for(self.child.wait(), 12)
            except TimeoutError:
                self.child.kill()
                await self.child.wait()
                raise

    def close(self):
        if os.getpid() != self.pid:
            raise RuntimeError("not the fixture owner")
        self.listener.close()
        try:
            self.redis.close()
        finally:
            try:
                self.cluster.stop()
            finally:
                os.close(self.lock)
