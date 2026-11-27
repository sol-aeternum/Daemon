"""Linux lifetime boundary only; no database, history mutation or job dispatch."""

import ctypes
import fcntl
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys

import pytest

from orchestrator.services.restore_history.inbox_lineage import InboxChild, fixture_history_id
from orchestrator.services.restore_history.model import HistoryError


def listening():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener


def test_fixture_identity_requires_original_listener(tmp_path):
    with listening() as first, first.dup() as retained, listening() as unrelated:
        assert fixture_history_id(first, tmp_path) == fixture_history_id(retained, tmp_path)
        assert fixture_history_id(first, tmp_path) != fixture_history_id(unrelated, tmp_path)


def _lost_launcher(root):
    # Scope Linux subreaping to this disposable test interpreter. We can then
    # reap the orphan specifically, without sending signals to an unknown PID.
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
    reader, writer = os.pipe()
    parent = os.fork()
    if parent == 0:
        os.close(reader)
        signal.alarm(8)  # Bound even a failed grandchild readiness assertion.
        lock = os.open(root, os.O_DIRECTORY | os.O_CLOEXEC)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with listening() as listener:
            original, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            history = fixture_history_id(listener, root)
            if os.fork() == 0:
                os.close(lock)
                original.close()
                boundary = InboxChild(child, listener, root / "h", history)
                host = object()
                boundary.bind(host, root / "h", history)
                other = os.open(root, os.O_DIRECTORY)
                with pytest.raises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.close(other)
                os.write(writer, os.getpid().to_bytes(8))
                assert select.select([child], [], [], 5)[0] == [child]
                with pytest.raises(HistoryError, match="inbox_launcher_lost"):
                    boundary.check(host)
                os._exit(0)
            child.close()
            stat = root.stat()
            original.sendall(f"{stat.st_dev}:{stat.st_ino}:{history} original".encode())
            signal.pause()
        os._exit(1)
    os.close(writer)
    assert select.select([reader], [], [], 5)[0] == [reader]
    raw = os.read(reader, 8)
    assert len(raw) == 8
    child_pid = int.from_bytes(raw)
    os.kill(parent, signal.SIGKILL)
    assert os.waitpid(parent, 0) == (parent, signal.SIGKILL)
    assert os.waitpid(child_pid, 0) == (child_pid, 0)
    os.close(reader)


def test_original_launcher_death_with_live_child(tmp_path):
    subprocess.run(
        [sys.executable, "-m", "tests.test_history_restart_lineage", str(tmp_path)],
        check=True,
        timeout=15,
    )


if __name__ == "__main__":
    _lost_launcher(Path(sys.argv[1]))
