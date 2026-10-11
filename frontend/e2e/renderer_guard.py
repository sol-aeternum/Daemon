"""Linux-only, two-phase pidfd guard for the disposable P0.2 browser test.

Not imported by application code and not an HTTP endpoint. The controller must
own a launchServer handle and obtain candidates from that browser's CDP list.
All kernel handles are bound before the controller collects page markers.
"""

import json
import os
import secrets
import select
import signal
import sys
import time
from typing import Any

MAX_BYTES = 8192
MAX_CANDIDATES = 32
MAX_DEPTH = 32
PHASE_SECONDS = 15.0


class Refused(Exception):
    """An identity or protocol condition was not established; never signal."""


def integer(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise Refused("invalid process identifier")
    return value


def exact_keys(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise Refused("invalid record fields")
    return value


def identity(value: Any) -> tuple[int, str]:
    record = exact_keys(value, {"pid", "start"})
    start = record["start"]
    if (
        not isinstance(start, str)
        or not start.isascii()
        or not start.isdecimal()
        or len(start) > 24
    ):
        raise Refused("invalid process start metadata")
    return integer(record["pid"]), start


def process_state(pid: int) -> tuple[int, str]:
    directory = f"/proc/{pid}"
    if os.stat(directory).st_uid != os.geteuid():
        raise Refused("process UID does not match test controller")
    with open(f"{directory}/stat", encoding="ascii") as file:
        text = file.read(4096)
    fields = text[text.rindex(")") + 2 :].split()
    if fields[0] in {"Z", "X", "x"}:
        raise Refused("process has exited")
    return int(fields[1]), fields[19]


def lineage(pid: int, root: int) -> None:
    seen: set[int] = set()
    for _ in range(MAX_DEPTH):
        if pid == root:
            return
        if pid in seen or pid <= 1:
            break
        seen.add(pid)
        pid, _start = process_state(pid)
    raise Refused("process lineage does not reach owned root")


def alive(fd: int) -> None:
    poll = select.poll()
    poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    if poll.poll(0):
        raise Refused("bound process has exited")


class Guard:
    def __init__(self, record: Any) -> None:
        self.fds: dict[int, int] = {}
        self.starts: dict[int, str] = {}
        self.nonce = secrets.token_hex(16)
        self.controller = os.getppid()
        self.committed = False
        if (
            sys.platform != "linux"
            or not hasattr(os, "pidfd_open")
            or not hasattr(signal, "pidfd_send_signal")
        ):
            raise Refused("Linux pidfd facilities required")
        data = exact_keys(record, {"root", "candidates"})
        self.root, root_start = identity(data["root"])
        candidates = data["candidates"]
        if not isinstance(candidates, list) or not 2 <= len(candidates) <= MAX_CANDIDATES:
            raise Refused("invalid candidate count")
        entries = [identity(item) for item in candidates]
        pids = [pid for pid, _start in entries]
        if len(set(pids)) != len(pids) or self.root in pids or self.root == self.controller:
            raise Refused("ambiguous root or candidates")
        self.candidates = frozenset(pids)
        try:
            # Open every handle BEFORE any trace or marker can identify a target.
            for pid, start in [(self.root, root_start), *entries]:
                self.fds[pid] = os.pidfd_open(pid)
                self.starts[pid] = start
                self.validate(pid)
            self.validate_root()
            for pid in self.candidates:
                lineage(pid, self.root)
                self.validate(pid)
        except Exception:
            self.close()
            raise

    def validate(self, pid: int) -> None:
        alive(self.fds[pid])
        if process_state(pid)[1] != self.starts[pid]:
            raise Refused("process start metadata changed")
        alive(self.fds[pid])

    def validate_root(self) -> None:
        if os.getppid() != self.controller:
            raise Refused("controller exited")
        self.validate(self.root)
        # launchServer's owned child must also descend from this helper's actual
        # parent controller. This is an additional fail-closed ownership gate.
        lineage(self.root, self.controller)

    def commit(self, record: Any) -> tuple[int, int]:
        if self.committed:
            raise Refused("commit already attempted")
        self.committed = True
        data = exact_keys(record, {"nonce", "holder", "peer"})
        if data["nonce"] != self.nonce:
            raise Refused("commit does not acknowledge binding receipt")
        holder, peer = integer(data["holder"]), integer(data["peer"])
        if holder == peer or holder not in self.candidates or peer not in self.candidates:
            raise Refused("unbound or shared renderer")
        self.validate_root()
        for pid in (holder, peer):
            self.validate(pid)
            lineage(pid, self.root)
            self.validate(pid)
        self.validate(self.root)
        # The only signal operation: retained kernel handle, never a PID fallback.
        signal.pidfd_send_signal(self.fds[holder], signal.SIGKILL)
        return holder, peer

    def close(self) -> None:
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Refused("duplicate record field")
        result[key] = value
    return result


def read_record() -> Any:
    deadline = time.monotonic() + PHASE_SECONDS
    data = bytearray()
    # Read a single bounded record without buffering a later protocol phase.
    while len(data) <= MAX_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin.fileno()], [], [], remaining)[0]:
            raise Refused("protocol phase timed out")
        byte = os.read(sys.stdin.fileno(), 1)
        if not byte:
            raise Refused("protocol ended without commit")
        if byte == b"\n":
            return json.loads(data, object_pairs_hook=unique_object)
        data.extend(byte)
    raise Refused("protocol record too large")


def report(value: dict[str, Any]) -> None:
    print(json.dumps(value), flush=True)


def main() -> int:
    guard: Guard | None = None
    try:
        guard = Guard(read_record())
        report(
            {
                "status": "ready",
                "nonce": guard.nonce,
                "root": guard.root,
                "candidates": sorted(guard.candidates),
            }
        )
        holder, peer = guard.commit(read_record())
        report({"status": "signaled", "holder": holder, "peer": peer})
        return 0
    except (Refused, OSError, ValueError, KeyError, IndexError) as error:
        report({"status": "refused", "reason": str(error)})
        return 1
    finally:
        if guard is not None:
            guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
