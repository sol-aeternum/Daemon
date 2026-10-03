"""Pilot-only one-child raw pipe ownership adapter; no CLI or import I/O.

A dependency-injected backend owns the only four permitted real operations
(pipe allocation, descriptor close, descriptor adoption and child spawn).
Scope excludes the Docker driver, argv/policy validation, image inspection,
record parsing, rings, the frame pump and the full supervisor.

Exactly one active job INCLUDING pending launch/reap; no queue. The slot
frees only when every owned fd is closed AND the child exit verified with an
exact int returncode match (or the backend spawn raised: that contract
locates no child there, so no handle was lost). A cancelled awaiting launch
caller never cancels the spawn future: the tracked task is retained until
native publication, the handle is assigned with no intervening await, and a
stopping owner immediately kills only its own child then reaps. Killing a
process never implies closed pipes; parent ends may close immediately to
force child-side EOF before child exit. Cleanup bounded by the close grace:
unresolved state latches fatal ``ProcessCleanupFailure`` which retains
visible tasks/handles/fds, never resets and prohibits new allocation.
Ordinary ``ProcessLaunchFailure`` requires a fully clean end state.

The caller is trusted configuration; nothing here grants execution authority
and the unqualified native backend must be qualified by the future
supervisor. No shell, PATH search, ambient env inheritance, CLI/entrypoint,
or constructor/import I/O.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from scripts.web_fetch_pilot_io import AsyncFD

MAX_LAUNCH_SECONDS = 45.0
MAX_CLOSE_GRACE_SECONDS = 2.0
MAX_ARGV_ITEMS = 256
MAX_ARGV_BYTES = 128 * 1024


class ProcessLaunchFailure(RuntimeError):
    """Ordinary refusal/outcome; ownership ended fully clean, nothing retained."""


class ProcessCleanupFailure(RuntimeError):
    """Fatal ownership/cleanup uncertainty: escapes callers, never resets."""


class RawByteChannel(Protocol):
    """Bounded nonblocking read/write channel (AsyncFD-compatible shape)."""

    async def read(self, size: int, /) -> bytes: ...

    async def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


class RawProcessHandle(Protocol):
    """Native child handle shape used after backend publication."""

    @property
    def returncode(self) -> int | None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


class RawBackend(Protocol):
    """Backend protocol: fake allocator/closer/adopter/spawner in tests."""

    def pipe2(self) -> tuple[int, int]: ...

    def close_fd(self, fd: int) -> None: ...

    def adopt(self, fd: int) -> RawByteChannel: ...

    def spawn(
        self,
        argv: tuple[str, ...],
        env: dict[str, str],
        *,
        stdin_fd: int,
        stdout_fd: int,
        stderr_fd: int,
    ) -> Awaitable[RawProcessHandle]: ...


def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Shape-only trusted argv check; no assembly, inspection or execution."""
    if type(argv) not in (list, tuple):
        raise ValueError("argv list/tuple of plain strings required")
    args = tuple(argv)
    if not 1 <= len(args) <= MAX_ARGV_ITEMS:
        raise ValueError("argv item count outside bounded range")
    total = 0
    for item in args:
        if type(item) is not str or not item or "\x00" in item:
            raise ValueError("plain nonempty argument strings required")
        total += len(item.encode("utf-8"))
    if total > MAX_ARGV_BYTES:
        raise ValueError("argv bytes exceed bounded cap")
    if not os.path.isabs(args[0]):
        raise ValueError("absolute argv[0] required; PATH search not permitted")
    return args


def _validate_env(env: Mapping[str, str]) -> dict[str, str]:
    """Copy exactly the caller-provided plain mapping; no ambient inheritance."""
    if type(env) is not dict:
        raise ValueError("plain mapping env required")
    copy: dict[str, str] = {}
    for key, value in env.items():
        if type(key) is not str or not key or "\x00" in key:
            raise ValueError("plain nonempty env keys required")
        if type(value) is not str or "\x00" in value:
            raise ValueError("plain str env values required")
        copy[key] = value
    return copy


_FD_RAW = "raw"
_FD_ADOPTED = "adopted"
_FD_CLOSED = "closed"
_FD_UNKNOWN = "close_unknown"


@dataclass(eq=False)
class _OwnedFd:
    fd: int
    state: str = _FD_RAW


@dataclass(eq=False)
class _Job:
    fds: list[_OwnedFd] = field(default_factory=list)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    spawn: asyncio.Task[None] | None = None
    reap: asyncio.Task[None] | None = None
    process: RawProcessHandle | None = None
    stdio: tuple[tuple[int, RawByteChannel], ...] | None = None
    session: RawSession | None = None
    stopping: bool = False
    retained: bool = False
    released: bool = False
    reaped: bool = False
    kill_attempted: bool = False
    stdio_closed: bool = False
    factory_failed: bool = False


@dataclass(frozen=True)
class _Ends:
    stdin_read: int
    stdin_write: int
    stdout_read: int
    stdout_write: int
    stderr_read: int
    stderr_write: int


class NativeBackend:
    """Explicit native backend: the only permitted real descriptor operations.

    Qualification evidence for the future reviewed supervisor, not
    authorization. Methods use raw child fd passthrough only: no stdlib
    stdout/stdin transports, no PIPE/communicate/capture_output and no
    shell/PATH search.
    """

    def pipe2(self) -> tuple[int, int]:
        return os.pipe2(os.O_CLOEXEC)

    def close_fd(self, fd: int) -> None:
        os.close(fd)

    def adopt(self, fd: int) -> RawByteChannel:
        return AsyncFD(fd)

    async def spawn(
        self,
        argv: tuple[str, ...],
        env: dict[str, str],
        *,
        stdin_fd: int,
        stdout_fd: int,
        stderr_fd: int,
    ) -> RawProcessHandle:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin_fd,
            stdout=stdout_fd,
            stderr=stderr_fd,
            env=env,
            close_fds=True,
            start_new_session=True,
        )


class RawSession:
    """Caller-owned parent-side stdio channels for exactly one child.

    Launch success only means the native handle was published, NOT command
    success. ``stdin``/``stdout``/``stderr`` are bounded nonblocking AsyncFD
    adapter channels owning the parent ends (reads <=16 KiB, writes
    <=16 KiB + 12). ``aclose`` closes stdio (child-side EOF), kills only
    the owned running child, reaps with exact int returncode verification
    and closes every owned descriptor; grace/condition failure latches
    fatal and retains visible teardown ownership. Stdio close does not
    imply child exit; ``kill`` never implies closed pipes.
    """

    def __init__(self, launcher: RawLauncher, job: _Job) -> None:
        self._launcher = launcher
        self._job = job

    @property
    def stdin(self) -> RawByteChannel:
        return self._channel(0)

    @property
    def stdout(self) -> RawByteChannel:
        return self._channel(1)

    @property
    def stderr(self) -> RawByteChannel:
        return self._channel(2)

    @property
    def returncode(self) -> int | None:
        handle = self._job.process
        return None if handle is None else handle.returncode

    def _channel(self, index: int) -> RawByteChannel:
        if self._job.stdio is None:
            raise ProcessCleanupFailure("session stdio never published")
        return self._job.stdio[index][1]

    def close_stdio(self) -> None:
        """Close parent sides: child sees EOF; child exit is NOT implied."""
        self._launcher._close_stdio(self._job)

    def kill(self) -> None:
        """Kill the owned running child; pipes/state unchanged."""
        self._launcher._kill(self._job)

    async def wait(self) -> int:
        """Await verified child exit WITHOUT killing; pipes/ownership unchanged.

        Starts the retained owner reap task; caller cancellation never cancels
        it. A verification failure latches fatal exactly like teardown reaps.
        """
        return await self._launcher._wait_job(self._job)

    async def aclose(self) -> None:
        """Bounded owner teardown: EOF, kill, exact exit verify, all fds closed."""
        await self._launcher._aclose_job(self._job)


class RawLauncher:
    """One-child raw pipe ownership launcher; injected trusted backend.

    Constructor config is shape-validated only: no real operations and no
    event-loop binding before explicit launch/aclose. Fatal contract:
    bounded close grace; unresolved state latches
    :class:`ProcessCleanupFailure`, retains visible teardown evidence and
    prohibits new allocation forever. ``pending_tasks``/``owned_fds``/
    ``owned_process``/``cleanup_failed`` are teardown evidence, not proof
    of success.
    """

    def __init__(
        self,
        backend: RawBackend,
        *,
        launch_deadline: float = MAX_LAUNCH_SECONDS,
        close_grace: float = MAX_CLOSE_GRACE_SECONDS,
    ) -> None:
        if not all(
            callable(getattr(backend, name, None))
            for name in ("pipe2", "close_fd", "adopt", "spawn")
        ):
            raise ValueError("backend allocator/adopter/spawner protocol required")
        if type(launch_deadline) is not float or not 0 < launch_deadline <= MAX_LAUNCH_SECONDS:
            raise ValueError("launch deadline outside bounded range")
        if type(close_grace) is not float or not 0 < close_grace <= MAX_CLOSE_GRACE_SECONDS:
            raise ValueError("close grace outside bounded range")
        self._backend = backend
        self._deadline = launch_deadline
        self._grace = close_grace
        self._job: _Job | None = None
        self._closing = False
        self._failed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def cleanup_failed(self) -> bool:
        return self._failed

    @property
    def pending_tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(
            task for job in (self._job,) if job is not None for task in job.tasks if not task.done()
        )

    @property
    def owned_fds(self) -> tuple[int, ...]:
        return tuple(
            record.fd
            for job in (self._job,)
            if job is not None
            for record in job.fds
            if record.state != _FD_CLOSED
        )

    @property
    def owned_process(self) -> RawProcessHandle | None:
        job = self._job
        if job is None or job.reaped or job.factory_failed:
            return None
        return job.process

    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif loop is not self._loop:
            self._latch_fatal()
            raise ProcessCleanupFailure("launcher used from another event loop")

    def _latch_fatal(self) -> None:
        self._failed = True
        self._closing = True

    def _track(
        self, job: _Job, work: Coroutine[object, object, None], *, fatal: bool
    ) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(
            work, name="pilot-process-owned-any" if fatal else "pilot-process-owned"
        )
        job.tasks.add(task)
        launcher = self

        def settled(done: asyncio.Task[None]) -> None:
            # Retrieve every completion, including after caller return.
            failure = None if done.cancelled() else done.exception()
            if fatal and failure is not None:
                launcher._latch_fatal()
            job.tasks.discard(done)
            launcher._maybe_release(job)

        task.add_done_callback(settled)
        return task

    def _own(self, job: _Job, fd: int) -> None:
        job.fds.append(_OwnedFd(fd))

    def _record_for_close(self, job: _Job, fd: int) -> _OwnedFd:
        for record in reversed(job.fds):
            if record.fd == fd and record.state != _FD_CLOSED:
                return record
        self._latch_fatal()
        raise ProcessCleanupFailure(f"owned-descriptor registry has no closeable {fd}")

    def _close_raw(self, job: _Job, fd: int) -> None:
        record = self._record_for_close(job, fd)
        if record.state != _FD_RAW:
            return  # Never retry an uncertain close or bypass an adopted owner.
        record.state = _FD_UNKNOWN
        try:
            self._backend.close_fd(fd)
        except BaseException:
            self._latch_fatal()  # close outcome unknown; fd may still be live
        else:
            record.state = _FD_CLOSED

    def _unwind_raw(self, job: _Job) -> None:
        for record in tuple(job.fds):
            if record.state == _FD_RAW:
                self._close_raw(job, record.fd)

    def _allocate(self, job: _Job) -> _Ends:
        pairs: list[tuple[int, int]] = []
        try:
            for _ in range(3):
                read_fd, write_fd = self._backend.pipe2()
                self._own(job, read_fd)
                self._own(job, write_fd)
                pairs.append((read_fd, write_fd))
        except BaseException:
            self._unwind_raw(job)
            job.factory_failed = True  # Allocation never invoked the factory.
            raise
        stdin_read, stdin_write = pairs[0]
        stdout_read, stdout_write = pairs[1]
        stderr_read, stderr_write = pairs[2]
        return _Ends(stdin_read, stdin_write, stdout_read, stdout_write, stderr_read, stderr_write)

    def _close_child_ends(self, job: _Job, ends: _Ends) -> None:
        for fd in (ends.stdin_read, ends.stdout_write, ends.stderr_write):
            self._close_raw(job, fd)

    def _adopt_stdio(self, job: _Job, ends: _Ends) -> None:
        adopted: list[tuple[int, RawByteChannel]] = []
        try:
            for fd in (ends.stdin_write, ends.stdout_read, ends.stderr_read):
                channel = self._backend.adopt(fd)
                adopted.append((fd, channel))
                record = self._record_for_close(job, fd)
                record.state = _FD_ADOPTED
                job.stdio = tuple(adopted)
        except BaseException:
            self._latch_fatal()
            self._stop_job(job)
            raise ProcessCleanupFailure(
                "stdio adoption failed; owned child killed/reaped where possible"
            ) from None
        job.stdio = tuple(adopted)

    def _close_stdio(self, job: _Job) -> None:
        if job.stdio is None or job.stdio_closed:
            return
        job.stdio_closed = True
        for fd, channel in job.stdio:
            record = self._record_for_close(job, fd)
            if record.state != _FD_ADOPTED:
                continue
            record.state = _FD_UNKNOWN
            try:
                channel.close()
            except BaseException:
                self._latch_fatal()
            else:
                record.state = _FD_CLOSED

    def _kill(self, job: _Job) -> None:
        handle = job.process
        if handle is None or job.reaped or job.kill_attempted:
            return
        job.kill_attempted = True
        try:
            if handle.returncode is None:
                handle.kill()
        except ProcessLookupError:
            pass  # Exited between accessor and kill; the reap still verifies.
        except BaseException:
            self._latch_fatal()

    def _ensure_reap(self, job: _Job) -> None:
        if job.process is None or job.reaped or job.reap is not None:
            return
        job.reap = self._track(job, self._reap_work(job), fatal=True)

    async def _reap_work(self, job: _Job) -> None:
        # Retained/never-cancelled task; verification errors latch fatal.
        handle = job.process
        if handle is None:
            return
        result = await handle.wait()
        if (
            type(result) is not int
            or type(handle.returncode) is not int
            or handle.returncode != result
        ):
            self._latch_fatal()
            raise ProcessCleanupFailure("child exit publication mismatch")
        job.reaped = True

    async def _wait_job(self, job: _Job) -> int:
        self._bind()
        handle = job.process
        if handle is None or job.factory_failed:
            raise ProcessCleanupFailure("no published child to wait for")
        self._ensure_reap(job)
        reap = job.reap
        if reap is not None:
            await asyncio.shield(reap)  # Re-raises the reap's own failure.
        if self._failed:
            raise ProcessCleanupFailure("launcher cleanup failed")
        code = handle.returncode
        if not job.reaped or type(code) is not int:
            self._latch_fatal()
            raise ProcessCleanupFailure("child exit not verified")
        return code

    async def _spawn_work(self, job: _Job, argv: tuple[str, ...], env: dict[str, str]) -> None:
        try:
            ends = self._allocate(job)
        except BaseException:
            self._maybe_release(job)
            raise
        try:
            handle = await self._backend.spawn(
                argv,
                env,
                stdin_fd=ends.stdin_read,
                stdout_fd=ends.stdout_write,
                stderr_fd=ends.stderr_write,
            )
        except BaseException:
            self._unwind_raw(job)
            job.factory_failed = True
            self._maybe_release(job)
            raise
        job.process = handle  # Native handle publication; no intervening await.
        stopping = job.stopping or self._closing
        try:
            self._close_child_ends(job, ends)
            if self._failed:
                raise ProcessCleanupFailure("child descriptor close uncertain")
            self._adopt_stdio(job, ends)
        except BaseException:
            self._stop_job(job)
            raise
        if stopping:
            self._stop_job(job)

    def _maybe_release(self, job: _Job) -> None:
        if job.tasks or job.released or self._job is not job:
            return
        if any(record.state != _FD_CLOSED for record in job.fds):
            return
        if job.reaped or job.factory_failed:
            job.released = True
            job.spawn = None
            job.reap = None
            if self._job is job:
                self._job = None

    async def _settle(self, job: _Job) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + self._grace
        while not job.released:
            self._ensure_reap(job)
            tasks = tuple(task for task in job.tasks if not task.done())
            if not tasks:
                break
            remaining = end - loop.time()
            if remaining <= 0:
                self._latch_fatal()
                raise ProcessCleanupFailure(
                    "close grace exceeded; retained tasks/handles/fds visible"
                ) from None
            try:
                await asyncio.wait(tasks, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                self._latch_fatal()
                raise
        if job.released:
            return
        if not (job.reaped or job.factory_failed) or any(
            record.state != _FD_CLOSED for record in job.fds
        ):
            self._latch_fatal()
            raise ProcessCleanupFailure(
                "release conditions unmet; retained tasks/handles/fds visible"
            ) from None
        self._maybe_release(job)

    async def _aclose_job(self, job: _Job) -> None:
        """Bounded per-job teardown; fatal on any uncertain end state."""
        if job.released:
            if self._failed:
                raise ProcessCleanupFailure("launcher cleanup failed")
            return
        self._stop_job(job)
        await self._settle(job)
        if self._failed:
            raise ProcessCleanupFailure("launcher cleanup failed")

    def _stop_job(self, job: _Job) -> None:
        job.stopping = True
        self._close_stdio(job)
        # Raw child ends may still be in use by a pending factory. Only unwind
        # them after publication/failure; late publication invokes this again.
        if job.process is not None or job.factory_failed:
            self._unwind_raw(job)
        self._kill(job)
        self._ensure_reap(job)

    def _retain_after_cancel(self, job: _Job) -> None:
        job.retained = True
        self._stop_job(job)

    def _classify_spawn_failure(self, job: _Job, exc: BaseException) -> Exception:
        """Return the truthful launch failure; never claim fake cleanliness."""
        if isinstance(exc, asyncio.CancelledError) or self._failed:
            self._latch_fatal()
            return ProcessCleanupFailure(
                "launch failed; descriptor/handle state uncertain; retained visible"
            )
        if job.process is None and all(record.state == _FD_CLOSED for record in job.fds):
            return ProcessLaunchFailure("native backend refused launch; no owned handle")
        self._latch_fatal()
        return ProcessCleanupFailure("spawn failure contract violated; retained teardown evidence")

    async def launch(self, argv: Sequence[str], env: Mapping[str, str]) -> RawSession:
        """Launch exactly one child; success is handle publication only."""
        self._bind()
        if self._closing or self._failed:
            raise ProcessCleanupFailure("launcher closed/fatal; new allocation prohibited")
        args = _validate_argv(argv)
        owned_env = _validate_env(env)
        if self._job is not None:
            raise ProcessLaunchFailure("launch slot occupied by pending/active child")
        job = _Job()
        self._job = job
        deadline = asyncio.get_running_loop().time() + self._deadline
        job.spawn = self._track(job, self._spawn_work(job, args, owned_env), fatal=False)
        try:
            finished, _ = await asyncio.wait(
                (job.spawn,), timeout=max(0.0, deadline - asyncio.get_running_loop().time())
            )
        except asyncio.CancelledError:
            self._retain_after_cancel(job)
            raise
        if not finished:
            self._stop_job(job)
            await self._settle(job)
            if self._failed:
                raise ProcessCleanupFailure("launch cleanup failed; fatal latch retained")
            raise ProcessLaunchFailure("launch deadline exceeded; owned child stopped")
        try:
            job.spawn.result()
        except asyncio.CancelledError:
            self._latch_fatal()
            raise ProcessCleanupFailure("spawn task cancelled; teardown evidence retained")
        except BaseException as exc:
            failure = self._classify_spawn_failure(job, exc)
            if isinstance(failure, ProcessCleanupFailure):
                self._latch_fatal()
            raise failure
        if self._failed:
            raise ProcessCleanupFailure("launch cleanup failed; retained ownership")
        if self._closing or job.stopping or job.stdio is None or job.process is None:
            await self._settle(job)
            if self._failed:
                raise ProcessCleanupFailure("launch cleanup failed; fatal latch retained")
            raise ProcessLaunchFailure("launch superseded by launcher close")
        session = RawSession(self, job)
        job.session = session
        return session

    def close(self) -> None:
        """Synchronous stop: refuse new launches, close stdio, kill published."""
        self._closing = True
        job = self._job
        if job is not None:
            self._stop_job(job)

    async def aclose(self) -> None:
        """Bounded launcher teardown; fatal ProcessCleanupFailure on uncertainty."""
        self._bind()
        self.close()
        job = self._job
        if job is not None:
            await self._settle(job)
        if self._failed:
            raise ProcessCleanupFailure("launcher cleanup failed")
