"""Pilot-only owned DNS helpers; no import/constructor I/O or CLI entrypoint.

Explicit invocation of StdlibSpawner launches a fixed isolated stdlib program.
It belongs ONLY in the future reviewed gateway container (128 MiB/no-swap,
32 PIDs, half CPU, 16 MiB scratch), not a host-DNS qualification path. AF_INET
requests A candidates, not an enumeration of AAAA records. Native getaddrinfo
may allocate its raw result inside libc/the child; container memory containment,
not the parent JSON cap, bounds that allocation. No socket is dialed here.

The parent retains at most 4097 output bytes and reads chunks <=1024, before
strict parsing. The asyncio pipe reader has limit=4096 (transport buffers are
separate). Gateway validate_dns_answers and trusted owned inventory remain
authoritative for public/private/owned-address policy; this adapter checks shape.

One event-loop actor owns at most four jobs, INCLUDING spawn/read/wait/cleanup.
Spawn publication is shielded and never cancelled: cancelling an awaiting native
create_subprocess_exec before it returns could lose the process handle. Late
publication stays owned, is killed and reaped, and never recycles a slot early.
Cleanup waits <=2 seconds, retaining unresolved tasks/handles and latching fatal
failure for supervisor container teardown. Synchronous kill/accessors and the
injected factory must not block the event loop. Python cannot kill a wedged task.
Child exit alone is not cleanup: failure paths serialize a bounded discard drain
(128 KiB plus one overflow-detection byte) and require stdout EOF before releasing
the job. Discarded bytes are never decoded or retained as DNS answers.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import sys
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from scripts.web_fetch_pilot_core import require_canonical_hostname

MAX_OUTPUT = 4096
MAX_JOBS = 4
READ_CHUNK = 1024
CLEANUP_OUTPUT_LIMIT = 128 * 1024

# Trusted source literal, never received over IPC or assembled from a hostname.
# No raw exception text, stderr, shell, import hooks, or remote-code interpretation.
HELPER_SOURCE = """import socket, json, sys
try:
    rows = socket.getaddrinfo(sys.argv[1], 443, family=socket.AF_INET,
                              type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not 1 <= len(rows) <= 8:
        raise ValueError()
    addresses = []
    for family, kind, proto, canonname, sockaddr in rows:
        if family != socket.AF_INET or kind != socket.SOCK_STREAM or proto != socket.IPPROTO_TCP:
            raise ValueError()
        address = sockaddr[0]
        if type(address) is not str or len(address) > 15:
            raise ValueError()
        if address not in addresses:
            addresses.append(address)
    output = json.dumps({"status": "ok", "addresses": addresses}, separators=(",", ":"))
except Exception:
    sys.stdout.write('{"status":"error","addresses":[]}')
    sys.exit(1)
sys.stdout.write(output)
"""


class DNSFailure(OSError):
    """Ordinary admission refusal; contains no child/DNS diagnostic text."""


class DNSCleanupFailure(RuntimeError):
    """Fatal actor failure; intentionally escapes Gateway._admit's refusal catch."""


class BoundedReader(Protocol):
    async def read(self, size: int, /) -> bytes: ...


class ProcessProtocol(Protocol):
    @property
    def stdout(self) -> BoundedReader | None: ...

    @property
    def returncode(self) -> int | None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


Spawn = Callable[[str], Awaitable[ProcessProtocol]]


def _host(host: str) -> str:
    if type(host) is not str:
        raise ValueError("canonical hostname string required")
    return require_canonical_hostname(host)


class StdlibSpawner:
    """Explicit native factory; caller qualifies the absolute interpreter path.

    Construction checks strings only, not interpreter/image provenance. No PATH
    search, env inheritance, site packages, shell or caller-supplied source/options.
    Use through DNSResolver: direct caller cancellation lacks actor ownership.
    """

    def __init__(self, python_executable: str | None = None) -> None:
        executable = sys.executable if python_executable is None else python_executable
        if type(executable) is not str or not os.path.isabs(executable) or "\x00" in executable:
            raise ValueError("qualified absolute Python executable required")
        self.executable = executable

    async def __call__(self, host: str) -> ProcessProtocol:
        host = _host(host)
        return await asyncio.create_subprocess_exec(
            self.executable,
            "-I",
            "-S",
            "-c",
            HELPER_SOURCE,
            host,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
            close_fds=True,
            start_new_session=True,
            limit=MAX_OUTPUT,
        )


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("non-JSON constant")


def _answers(raw: bytes) -> tuple[str, ...]:
    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict or set(value) != {"status", "addresses"}:
            raise ValueError()
        addresses = value["addresses"]
        if value["status"] != "ok" or type(addresses) is not list or not 1 <= len(addresses) <= 8:
            raise ValueError()
        for address in addresses:
            if type(address) is not str or str(ipaddress.IPv4Address(address)) != address:
                raise ValueError()
        if len(set(addresses)) != len(addresses):
            raise ValueError()
        return tuple(addresses)
    except (ValueError, TypeError, RecursionError):
        raise DNSFailure("DNS result refused") from None


@dataclass(eq=False)
class _Job:
    process: ProcessProtocol | None = None
    spawn: asyncio.Task[ProcessProtocol] | None = None
    run: asyncio.Task[tuple[str, ...]] | None = None
    cleanup: asyncio.Task[None] | None = None
    tasks: set[asyncio.Task] = field(default_factory=set)
    stopping: bool = False
    kill_attempted: bool = False
    reaped: bool = False
    stdout_eof: bool = False
    read_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


T = TypeVar("T")


class DNSResolver:
    """A callable single-owner actor; no waiting queue/semaphore followers.

    Resolver deadline includes spawn, EOF and exit. Cleanup has separate grace.
    aclose() must be called by the future gateway supervisor on every run ending;
    pending_tasks/owned_processes/cleanup_failed are teardown evidence, not proof
    that the actor can force-kill a container. Fatal failure never resets.
    """

    def __init__(self, spawn: Spawn, *, deadline: float = 3.0, cleanup_grace: float = 2.0) -> None:
        if not 0 < deadline <= 3 or not 0 < cleanup_grace <= 2:
            raise ValueError("DNS deadline/grace exceeds pilot ceiling")
        self._factory = spawn
        self._deadline = deadline
        self._grace = cleanup_grace
        self._jobs: set[_Job] = set()
        self._closing = False
        self._failed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def cleanup_failed(self) -> bool:
        return self._failed

    @property
    def pending_tasks(self) -> tuple[asyncio.Task, ...]:
        return tuple(task for job in self._jobs for task in job.tasks if not task.done())

    @property
    def owned_processes(self) -> tuple[ProcessProtocol, ...]:
        return tuple(
            job.process for job in self._jobs if job.process is not None and not job.reaped
        )

    @property
    def active_jobs(self) -> int:
        return len(self._jobs)

    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if loop is not self._loop:
            raise DNSCleanupFailure("DNS actor used from another event loop")

    def _fatal(self) -> None:
        self._failed = True
        self._closing = True
        for job in tuple(self._jobs):
            self._stop(job)

    def _track(self, job: _Job, work: Coroutine[object, object, T]) -> asyncio.Task[T]:
        task = asyncio.create_task(work, name="pilot-dns-owned")
        job.tasks.add(task)

        def settled(done: asyncio.Task[T]) -> None:
            # Retrieve EVERY completion/exception, including after caller return.
            if not done.cancelled():
                done.exception()
            job.tasks.discard(done)
            if not job.tasks:
                if job.process is not None and not job.reaped:
                    self._fatal()
                else:
                    self._jobs.discard(job)

        task.add_done_callback(settled)
        return task

    def _kill(self, job: _Job) -> None:
        if job.process is None or job.reaped or job.kill_attempted:
            return
        job.kill_attempted = True
        try:
            if job.process.returncode is None:
                job.process.kill()
        except ProcessLookupError:
            pass  # Process exited between accessor and kill; still await wait().
        except Exception:
            self._fatal()

    def _stop(self, job: _Job) -> None:
        job.stopping = True
        self._kill(job)
        # The run task may be cancelled before its first instruction/finally.
        # Establish cleanup ownership synchronously, never rely on that finally.
        if not job.reaped and job.cleanup is None:
            job.cleanup = self._track(job, self._reap(job))
        if job.run is not None and not job.run.done() and not job.run.cancelling():
            job.run.cancel()

    async def _publish(self, job: _Job, host: str) -> ProcessProtocol:
        process = await self._factory(host)
        job.process = process  # No await between publication and late-kill decision.
        if job.stopping or self._closing:
            self._kill(job)
        return process

    async def _reap(self, job: _Job) -> None:
        if job.spawn is None:
            return
        try:
            # wait() shields ownership without an abandoned shield Future's
            # late-exception diagnostics. The tracked task's result is consumed.
            await asyncio.wait((job.spawn,))
            job.spawn.result()
        except Exception:
            return  # Factory failure published no handle (factory contract).
        self._kill(job)
        if job.process is not None and not job.reaped:
            try:
                # Child exit alone does not close a paused asyncio stdout pipe.
                # Serialize with a cancelled in-flight reader before discarding.
                async with job.read_lock:
                    if not job.stdout_eof:
                        reader = job.process.stdout
                        if reader is None:
                            raise ValueError("missing owned stdout")
                        discarded = 0
                        while True:
                            size = min(READ_CHUNK, CLEANUP_OUTPUT_LIMIT + 1 - discarded)
                            chunk = await reader.read(size)
                            if type(chunk) is not bytes or len(chunk) > size:
                                raise ValueError("invalid cleanup read")
                            if not chunk:
                                job.stdout_eof = True
                                break
                            discarded += len(chunk)
                            if discarded > CLEANUP_OUTPUT_LIMIT:
                                raise ValueError("cleanup stdout limit")
                result = await job.process.wait()
                if type(result) is not int or job.process.returncode != result:
                    raise ValueError("invalid exit publication")
                job.reaped = True
            except Exception:
                self._fatal()

    async def _read(self, process: ProcessProtocol, job: _Job) -> bytes:
        reader = process.stdout
        if reader is None:
            raise DNSFailure("DNS result refused")
        raw = bytearray()
        while True:
            size = min(READ_CHUNK, MAX_OUTPUT + 1 - len(raw))
            chunk = await reader.read(size)
            if type(chunk) is not bytes or len(chunk) > size:
                raise DNSFailure("DNS result refused")
            if not chunk:
                job.stdout_eof = True
                return bytes(raw)
            raw.extend(chunk)
            if len(raw) > MAX_OUTPUT:
                raise DNSFailure("DNS result refused")

    async def _run(self, job: _Job) -> tuple[str, ...]:
        try:
            if job.spawn is None:
                raise DNSFailure("DNS unavailable")
            await asyncio.wait((job.spawn,))
            process = job.spawn.result()
            if job.stopping:
                raise DNSFailure("DNS unavailable")
            async with job.read_lock:
                raw = await self._read(process, job)
            result = await process.wait()
            if type(result) is not int or process.returncode != result:
                raise DNSFailure("DNS result refused")
            job.reaped = True
            if result != 0:
                raise DNSFailure("DNS unavailable")
            return _answers(raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise DNSFailure("DNS unavailable") from None
        finally:
            if not job.reaped:
                job.stopping = True
                if job.cleanup is None:
                    job.cleanup = self._track(job, self._reap(job))
                await asyncio.wait((job.cleanup,))
                job.cleanup.result()

    async def _settle(self, jobs: tuple[_Job, ...]) -> bool:
        end = asyncio.get_running_loop().time() + self._grace
        while tasks := tuple(task for job in jobs for task in job.tasks if not task.done()):
            remaining = end - asyncio.get_running_loop().time()
            if remaining <= 0:
                self._fatal()
                return False
            try:
                await asyncio.wait(tasks, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                self._fatal()
                return False
        return not self._failed

    async def __call__(self, host: str) -> tuple[str, ...]:
        host = _host(host)  # Validate BEFORE any factory task is created.
        self._bind()
        if self._failed or self._closing:
            raise DNSCleanupFailure("DNS actor closed or cleanup failed")
        if len(self._jobs) >= MAX_JOBS:
            raise DNSFailure("DNS capacity unavailable")
        job = _Job()
        self._jobs.add(job)
        job.spawn = self._track(job, self._publish(job, host))
        job.run = self._track(job, self._run(job))
        try:
            finished, _ = await asyncio.wait((job.run,), timeout=self._deadline)
            if not finished:
                raise DNSFailure("DNS deadline exceeded")
            result = job.run.result()
            if self._failed or self._closing:
                raise DNSCleanupFailure("DNS actor closed or cleanup failed")
            return result
        except BaseException:
            self._stop(job)
            if not await self._settle((job,)):
                raise DNSCleanupFailure(
                    "DNS cleanup failed; supervisor teardown required"
                ) from None
            raise

    def close(self) -> None:
        """Stop admissions and synchronously kill owned handles; never cancel spawn."""
        self._closing = True
        for job in tuple(self._jobs):
            self._stop(job)

    async def aclose(self) -> None:
        """Bounded cleanup, including late spawn publication; fatal on uncertainty."""
        self._bind()
        self.close()
        if not await self._settle(tuple(self._jobs)):
            raise DNSCleanupFailure("DNS cleanup failed; supervisor teardown required")
