"""Pilot-only bounded command executor over owned raw pipes; no CLI or import I/O.

Runs trusted fixed-argv commands (the future Docker CLI driver) through one
:class:`RawLauncher` per child. Owner-selected contract (2026-10-02): bounded
stdout **capture** (not a streaming sink) and **refuse-when-full** concurrency
with no queue. Scope excludes Docker argv/policy, identity inspection and the
framed browser/gateway pumps.

The trusted monotonic deadline starts before spawn and is never reset by
stream activity. stdin is bounded and written with backpressure, then closed.
stdout is captured up to a hard cap; one extra byte detects overflow and no
more is buffered. stderr drains continuously into a drop-oldest diagnostic
ring that is never parsed. Outcomes stay distinct:

- :class:`CommandResult`: verified exact exit. Nonzero is command failure,
  for the caller to classify.
- :class:`CommandLimitFailure`: deadline, output/input/protocol limit,
  occupancy or executor close; ownership ended clean.
- :class:`ProcessLaunchFailure`: ordinary launch refusal, clean.
- :class:`ProcessCleanupFailure`: fatal ownership uncertainty. Latches the
  executor, retains the launcher visibly and prohibits new runs forever.

Teardown always runs as an executor-owned task bounded by twice the close
grace, so caller cancellation returns promptly and never cancels a settle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from scripts.web_fetch_pilot_io import TransportError
from scripts.web_fetch_pilot_process import (
    MAX_CLOSE_GRACE_SECONDS,
    MAX_LAUNCH_SECONDS,
    ProcessCleanupFailure,
    ProcessLaunchFailure,
    RawBackend,
    RawLauncher,
    RawSession,
)

MAX_JOBS = 4
MAX_COMMAND_SECONDS = MAX_LAUNCH_SECONDS
MAX_STDIN_BYTES = 64 * 1024
MAX_STDOUT_BYTES = 64 * 1024
DIAGNOSTIC_RING_BYTES = 64 * 1024
READ_CHUNK = 16 * 1024
WRITE_CHUNK = 16 * 1024 + 12  # AsyncFD maximum write size.


class CommandLimitFailure(RuntimeError):
    """Ordinary deadline/limit/occupancy outcome; ownership ended clean."""


class _OutputLimit(Exception):
    pass


class _InputRefused(Exception):
    pass


class DiagnosticRing:
    """Bounded drop-oldest byte ring with exact counters; never parsed."""

    def __init__(self, capacity: int = DIAGNOSTIC_RING_BYTES) -> None:
        if type(capacity) is not int or not 0 < capacity <= DIAGNOSTIC_RING_BYTES:
            raise ValueError("ring capacity outside bounded range")
        self._capacity = capacity
        self._buf = bytearray()
        self.total_bytes = 0
        self.dropped_bytes = 0

    def append(self, data: bytes) -> None:
        if type(data) is not bytes:
            raise ValueError("diagnostic bytes required")
        self.total_bytes += len(data)
        self._buf.extend(data[-self._capacity :])
        self.dropped_bytes += len(data) - min(len(data), self._capacity)
        excess = len(self._buf) - self._capacity
        if excess > 0:
            del self._buf[:excess]
            self.dropped_bytes += excess

    def snapshot(self) -> bytes:
        return bytes(self._buf)


@dataclass(frozen=True)
class CommandResult:
    """Verified exact exit plus bounded output; nonzero is command failure."""

    returncode: int
    stdout: bytes
    diagnostics: bytes
    diagnostic_bytes: int
    diagnostic_dropped: int


@dataclass(eq=False)
class _Command:
    launcher: RawLauncher
    io: tuple[asyncio.Task[None], ...] = ()
    teardown: asyncio.Task[None] | None = None
    stdout: bytearray = field(default_factory=bytearray)
    ring: DiagnosticRing = field(default_factory=DiagnosticRing)


class CommandExecutor:
    """Refuse-when-full executor: at most ``max_jobs`` owned children, no queue.

    Constructor is shape-only: no backend operations and no event-loop
    binding. ``active_jobs``/``pending_tasks``/``cleanup_failed`` are teardown
    evidence, not proof of success.
    """

    def __init__(
        self,
        backend: RawBackend,
        *,
        max_jobs: int = MAX_JOBS,
        close_grace: float = MAX_CLOSE_GRACE_SECONDS,
    ) -> None:
        if not all(
            callable(getattr(backend, name, None))
            for name in ("pipe2", "close_fd", "adopt", "spawn")
        ):
            raise ValueError("backend allocator/adopter/spawner protocol required")
        if type(max_jobs) is not int or not 1 <= max_jobs <= MAX_JOBS:
            raise ValueError("job limit outside bounded range")
        if type(close_grace) is not float or not 0 < close_grace <= MAX_CLOSE_GRACE_SECONDS:
            raise ValueError("close grace outside bounded range")
        self._backend = backend
        self._max_jobs = max_jobs
        self._grace = close_grace
        self._commands: set[_Command] = set()
        self._changed: asyncio.Event | None = None
        self._closing = False
        self._failed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def cleanup_failed(self) -> bool:
        return self._failed

    @property
    def active_jobs(self) -> int:
        return len(self._commands)

    @property
    def retained_launchers(self) -> tuple[RawLauncher, ...]:
        return tuple(command.launcher for command in self._commands)

    @property
    def pending_tasks(self) -> tuple[asyncio.Task[None], ...]:
        tasks: list[asyncio.Task[None]] = []
        for command in self._commands:
            tasks.extend(task for task in command.io if not task.done())
            if command.teardown is not None and not command.teardown.done():
                tasks.append(command.teardown)
            tasks.extend(command.launcher.pending_tasks)
        return tuple(tasks)

    def _bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
            self._changed = asyncio.Event()
        elif loop is not self._loop:
            self._latch_fatal()
            raise ProcessCleanupFailure("executor used from another event loop")
        return loop

    def _latch_fatal(self) -> None:
        self._failed = True
        self._closing = True

    def _notify(self) -> None:
        if self._changed is not None:
            self._changed.set()

    async def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        stdin: bytes = b"",
        timeout: float,
    ) -> CommandResult:
        """Run one command to verified exit; see module docstring outcomes."""
        if type(timeout) is not float or not 0 < timeout <= MAX_COMMAND_SECONDS:
            raise ValueError("command timeout outside bounded range")
        if type(stdin) is not bytes or len(stdin) > MAX_STDIN_BYTES:
            raise ValueError("stdin bytes outside bounded range")
        loop = self._bind()
        deadline = loop.time() + timeout  # Trusted bound starts before spawn.
        if self._failed:
            raise ProcessCleanupFailure("executor fatal; new allocation prohibited")
        if self._closing:
            raise CommandLimitFailure("executor closed")
        if len(self._commands) >= self._max_jobs:
            raise CommandLimitFailure("executor full; no queue")
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise CommandLimitFailure("command deadline exceeded before spawn")
        # Launch keeps its own bounded deadline and settle, outside timeout_at:
        # cancelling its settle would latch fatal rather than stop cleanly.
        launcher = RawLauncher(self._backend, launch_deadline=remaining, close_grace=self._grace)
        command = _Command(launcher)
        self._commands.add(command)
        outcome: BaseException | None = None
        result: CommandResult | None = None
        try:
            session = await launcher.launch(argv, env)
            async with asyncio.timeout_at(deadline):
                await self._exchange(command, session, stdin)
                code = await session.wait()
            result = CommandResult(
                returncode=code,
                stdout=bytes(command.stdout),
                diagnostics=command.ring.snapshot(),
                diagnostic_bytes=command.ring.total_bytes,
                diagnostic_dropped=command.ring.dropped_bytes,
            )
        except BaseException as exc:  # Classified only after owned teardown.
            outcome = exc
        teardown = self._start_teardown(command)
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome  # Teardown stays executor-owned; aclose awaits it.
        await asyncio.shield(teardown)  # Fatal teardown raises here.
        if isinstance(outcome, ProcessCleanupFailure):
            raise outcome
        if self._closing and (
            outcome is None or isinstance(outcome, (ProcessLaunchFailure, TransportError, OSError))
        ):
            # A close-time kill or superseded launch must not masquerade as the
            # command's own exit or an independent launch refusal.
            raise CommandLimitFailure("executor closed during command") from None
        if isinstance(outcome, ProcessLaunchFailure):
            raise outcome
        if outcome is None and result is not None:
            return result
        if isinstance(outcome, TimeoutError):
            raise CommandLimitFailure("command deadline exceeded; owned child stopped") from None
        if isinstance(outcome, _OutputLimit):
            raise CommandLimitFailure("stdout cap exceeded; owned child stopped") from None
        if isinstance(outcome, _InputRefused):
            raise CommandLimitFailure("child closed stdin before input was written") from None
        if isinstance(outcome, (TransportError, OSError)):
            raise CommandLimitFailure("stdio transport failure; owned child stopped") from None
        if outcome is None:
            raise ProcessCleanupFailure("command outcome missing")
        raise outcome

    async def _exchange(self, command: _Command, session: RawSession, data: bytes) -> None:
        loop = asyncio.get_running_loop()
        command.io = (
            loop.create_task(_feed(session, data), name="pilot-command-stdin"),
            loop.create_task(_capture(session, command.stdout), name="pilot-command-stdout"),
            loop.create_task(_drain(session, command.ring), name="pilot-command-stderr"),
        )
        done, _ = await asyncio.wait(command.io, return_when=asyncio.FIRST_EXCEPTION)
        for task in command.io:
            if task in done:
                task.result()  # Raise the first failure in fixed stream order.

    def _start_teardown(self, command: _Command) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(
            self._teardown(command), name="pilot-command-teardown"
        )
        command.teardown = task
        executor = self

        def settled(done: asyncio.Task[None]) -> None:
            failure = None if done.cancelled() else done.exception()
            if done.cancelled() or failure is not None:
                executor._latch_fatal()  # Launcher stays visible in retained set.
            else:
                executor._commands.discard(command)
            executor._notify()

        task.add_done_callback(settled)
        return task

    async def _teardown(self, command: _Command) -> None:
        launcher = command.launcher
        launcher.close()  # Closes stdio, which wakes waiting stdio tasks.
        for task in command.io:
            if not task.done():
                task.cancel()
        if command.io:
            done, pending = await asyncio.wait(command.io, timeout=self._grace)
            for task in done:
                if not task.cancelled():
                    task.exception()  # Retrieved; classification happened in run.
            if pending:
                raise ProcessCleanupFailure("stdio tasks did not settle within grace")
        await launcher.aclose()
        if (
            launcher.cleanup_failed
            or launcher.owned_fds
            or launcher.pending_tasks
            or launcher.owned_process is not None
        ):
            raise ProcessCleanupFailure("launcher ownership not drained")

    def close(self) -> None:
        """Refuse new runs and stop every owned child; does not await."""
        self._closing = True
        for command in tuple(self._commands):
            command.launcher.close()

    async def aclose(self) -> None:
        """Bounded executor teardown; fatal ProcessCleanupFailure on uncertainty."""
        loop = self._bind()
        self.close()
        changed = self._changed
        if changed is None:
            raise ProcessCleanupFailure("executor change signal missing")
        end = loop.time() + 3 * self._grace
        while self._commands and not self._failed:
            remaining = end - loop.time()
            if remaining <= 0:
                self._latch_fatal()
                break
            changed.clear()
            try:
                await asyncio.wait_for(changed.wait(), remaining)
            except TimeoutError:
                self._latch_fatal()
            except asyncio.CancelledError:
                self._latch_fatal()
                raise
        if self._failed:
            raise ProcessCleanupFailure("executor cleanup failed; launchers retained visible")


async def _feed(session: RawSession, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        try:
            count = await session.stdin.write(data[offset : offset + WRITE_CHUNK])
        except BrokenPipeError:
            raise _InputRefused from None
        if type(count) is not int or not 0 < count <= WRITE_CHUNK:
            raise TransportError("invalid partial-write count")
        offset += count
    session.stdin.close()  # Child-side EOF; owner close never recloses.


async def _capture(session: RawSession, sink: bytearray) -> None:
    while True:
        # Never buffer past the cap: request at most one detection byte more.
        data = await session.stdout.read(min(READ_CHUNK, MAX_STDOUT_BYTES - len(sink) + 1))
        if not data:
            return
        if len(sink) + len(data) > MAX_STDOUT_BYTES:
            raise _OutputLimit
        sink.extend(data)


async def _drain(session: RawSession, ring: DiagnosticRing) -> None:
    while data := await session.stderr.read(READ_CHUNK):
        ring.append(data)
