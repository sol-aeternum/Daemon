"""Fake-only command executor tests; NO real OS pipes/process/DNS here.

The fake backend models kernel pipe semantics that the executor relies on:
reference-counted ends (EOF only when every writer copy is closed, EPIPE
only when every reader copy is closed), bounded capacity with partial writes
and backpressure, and scripted children holding their own end copies until
exit. The real RawLauncher is exercised; only the four backend operations
are fake. Every gate is released in ``finally`` so no fake task leaks.
"""

from __future__ import annotations

import asyncio
import errno
import time
from collections.abc import Awaitable, Callable

import pytest

from scripts.web_fetch_pilot_command import (
    DIAGNOSTIC_RING_BYTES,
    MAX_COMMAND_SECONDS,
    MAX_JOBS,
    MAX_STDIN_BYTES,
    MAX_STDOUT_BYTES,
    CommandExecutor,
    CommandLimitFailure,
    DiagnosticRing,
)
from scripts.web_fetch_pilot_io import TransportError
from scripts.web_fetch_pilot_process import ProcessCleanupFailure, ProcessLaunchFailure

PIPE_CAPACITY = 64 * 1024


class FakePipe:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.buf = bytearray()
        self.readers = 1
        self.writers = 1
        self._waiters: set[asyncio.Future[None]] = set()

    def notify(self) -> None:
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._waiters.clear()

    async def changed(self) -> None:
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.add(waiter)
        try:
            async with asyncio.timeout(5):
                await waiter
        except TimeoutError:
            raise AssertionError("fake pipe wait exceeded test bound") from None

    def take(self, size: int) -> bytes:
        data = bytes(self.buf[:size])
        del self.buf[:size]
        self.notify()
        return data

    def put(self, data: bytes) -> int:
        count = min(len(data), self.capacity - len(self.buf))
        self.buf.extend(data[:count])
        self.notify()
        return count


class FakeChannel:
    """Parent end with AsyncFD-sized bounds and close-wakes-waiter parity."""

    def __init__(self, backend: FakeBackend, fd: int) -> None:
        self._backend = backend
        self.fd = fd
        self.pipe = backend.fds[fd][0]
        self.closed = False

    async def read(self, size: int) -> bytes:
        if type(size) is not int or not 0 < size <= 16 * 1024:
            raise ValueError("fake read size outside bounded range")
        while True:
            if self.closed:
                raise TransportError("descriptor closed")
            if self.pipe.buf:
                return self.pipe.take(size)
            if self.pipe.writers == 0:
                return b""
            await self.pipe.changed()

    async def write(self, data: bytes) -> int:
        if type(data) is not bytes or not 0 < len(data) <= 16 * 1024 + 12:
            raise ValueError("fake write size/type outside bounded range")
        while True:
            if self.closed:
                raise TransportError("descriptor closed")
            if self.pipe.readers == 0:
                raise BrokenPipeError(errno.EPIPE, "fake broken pipe")
            if len(self.pipe.buf) < self.pipe.capacity:
                return self.pipe.put(data)
            await self.pipe.changed()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.pipe.notify()
        self._backend.close_fd(self.fd)


class FakeChild:
    """Scripted child holding its own copies of the three child-side ends."""

    def __init__(self, stdin: FakePipe, stdout: FakePipe, stderr: FakePipe) -> None:
        self._stdin: FakePipe | None = stdin
        self._stdout: FakePipe | None = stdout
        self._stderr: FakePipe | None = stderr
        stdin.readers += 1
        stdout.writers += 1
        stderr.writers += 1

    async def read_stdin(self, size: int) -> bytes:
        pipe = self._stdin
        assert pipe is not None
        while not pipe.buf and pipe.writers:
            await pipe.changed()
        return pipe.take(size)

    async def _write(self, pipe: FakePipe | None, data: bytes) -> None:
        assert pipe is not None
        view = memoryview(data)
        while view:
            if pipe.readers == 0:
                raise BrokenPipeError(errno.EPIPE, "fake child broken pipe")
            count = pipe.put(bytes(view[: pipe.capacity]))
            view = view[count:]
            if view:
                await pipe.changed()

    async def write_stdout(self, data: bytes) -> None:
        await self._write(self._stdout, data)

    async def write_stderr(self, data: bytes) -> None:
        await self._write(self._stderr, data)

    def close_output(self) -> None:
        for name in ("_stdout", "_stderr"):
            pipe = getattr(self, name)
            if pipe is not None:
                pipe.writers -= 1
                pipe.notify()
                setattr(self, name, None)

    def release(self) -> None:
        self.close_output()
        if self._stdin is not None:
            self._stdin.readers -= 1
            self._stdin.notify()
            self._stdin = None


Program = Callable[[FakeChild], Awaitable[int]]


class FakeProcess:
    def __init__(self, backend: FakeBackend, child: FakeChild, program: Program) -> None:
        self._backend = backend
        self.child = child
        self.returncode: int | None = None
        self.killed = False
        self.report_override: int | None = None
        self.exited = asyncio.Event()
        self.task = asyncio.get_running_loop().create_task(self._run(program))
        # A kill before the task's first step skips _run entirely; the kernel
        # still reaps a killed child, so the fake must publish that exit too.
        self.task.add_done_callback(self._killed_before_start)

    def _killed_before_start(self, task: asyncio.Task[None]) -> None:
        if self.returncode is None:
            self.child.release()
            self.returncode = -9
            self.exited.set()

    async def _run(self, program: Program) -> None:
        try:
            code = await program(self.child)
        except asyncio.CancelledError:
            code = -9
        except BrokenPipeError:
            code = -13  # Default SIGPIPE disposition restored in real children.
        self.child.release()
        self.returncode = code
        self.exited.set()

    def kill(self) -> None:
        if self._backend.fail_kill:
            raise OSError("fake kill failure")
        if self.returncode is None:
            self.killed = True
            self.task.cancel()

    async def wait(self) -> int:
        await self.exited.wait()
        if self.report_override is not None:
            return self.report_override
        assert self.returncode is not None
        return self.returncode


class FakeBackend:
    def __init__(self, programs: dict[str, Program], *, capacity: int = PIPE_CAPACITY) -> None:
        self.programs = programs
        self.capacity = capacity
        self.fds: dict[int, tuple[FakePipe, bool]] = {}
        self.close_log: list[int] = []
        self.spawn_calls: list[tuple[str, ...]] = []
        self.processes: list[FakeProcess] = []
        self.spawn_gate: asyncio.Event | None = None
        self.spawn_error: BaseException | None = None
        self.fail_kill = False
        self.next_fd = 60_000
        self.backend_ops = 0

    def pipe2(self) -> tuple[int, int]:
        self.backend_ops += 1
        pipe = FakePipe(self.capacity)
        read_fd, write_fd = self.next_fd, self.next_fd + 1
        self.next_fd += 2
        self.fds[read_fd] = (pipe, True)
        self.fds[write_fd] = (pipe, False)
        return read_fd, write_fd

    def close_fd(self, fd: int) -> None:
        self.backend_ops += 1
        if fd not in self.fds:
            raise OSError(errno.EBADF, "fake double/unknown close")
        pipe, is_read = self.fds.pop(fd)
        if is_read:
            pipe.readers -= 1
        else:
            pipe.writers -= 1
        self.close_log.append(fd)
        pipe.notify()

    def adopt(self, fd: int) -> FakeChannel:
        self.backend_ops += 1
        if fd not in self.fds:
            raise ValueError("fake adopt of unknown descriptor")
        return FakeChannel(self, fd)

    async def spawn(
        self,
        argv: tuple[str, ...],
        env: dict[str, str],
        *,
        stdin_fd: int,
        stdout_fd: int,
        stderr_fd: int,
    ) -> FakeProcess:
        self.backend_ops += 1
        self.spawn_calls.append(argv)
        assert env == {}
        if self.spawn_gate is not None:
            await self.spawn_gate.wait()
        if self.spawn_error is not None:
            raise self.spawn_error
        child = FakeChild(self.fds[stdin_fd][0], self.fds[stdout_fd][0], self.fds[stderr_fd][0])
        process = FakeProcess(self, child, self.programs[argv[0]])
        self.processes.append(process)
        return process

    async def backstop(self) -> None:
        """Test-only recovery: exit recorded fake children, never touch fds."""
        self.fail_kill = False
        if self.spawn_gate is not None:
            self.spawn_gate.set()
        for process in self.processes:
            if not process.task.done():
                process.task.cancel()
        await asyncio.gather(*(p.task for p in self.processes), return_exceptions=True)


async def cat(child: FakeChild) -> int:
    await child.write_stderr(b"diag:start\n")
    while data := await child.read_stdin(4096):
        await child.write_stdout(data)
    return 0


async def exit_three(child: FakeChild) -> int:
    await child.write_stderr(b"bad input\n")
    return 3


async def flood_cap(child: FakeChild) -> int:
    await child.write_stdout(b"o" * MAX_STDOUT_BYTES)
    return 0


async def flood_over_cap(child: FakeChild) -> int:
    await child.write_stdout(b"o" * (MAX_STDOUT_BYTES + 1))
    await asyncio.Event().wait()  # Never exits by itself: the owner must stop it.
    return 0


STDERR_FLOOD = 1024 * 1024


async def flood_stderr(child: FakeChild) -> int:
    for index in range(STDERR_FLOOD // 4096):
        await child.write_stderr(index.to_bytes(4, "big") * 1024)
    return 0


async def sleeper(child: FakeChild) -> int:
    await asyncio.Event().wait()
    return 0


async def chatty(child: FakeChild) -> int:
    while True:
        await child.write_stderr(b".")
        await asyncio.sleep(0.01)


async def no_read_exit(child: FakeChild) -> int:
    return 0


async def eof_then_exit(child: FakeChild) -> int:
    child.close_output()
    await asyncio.sleep(0.05)
    return 5


PROGRAMS: dict[str, Program] = {
    "/fake/cat": cat,
    "/fake/exit-three": exit_three,
    "/fake/flood-cap": flood_cap,
    "/fake/flood-over-cap": flood_over_cap,
    "/fake/flood-stderr": flood_stderr,
    "/fake/sleeper": sleeper,
    "/fake/chatty": chatty,
    "/fake/no-read-exit": no_read_exit,
    "/fake/eof-then-exit": eof_then_exit,
}


def assert_drained(backend: FakeBackend, executor: CommandExecutor) -> None:
    assert backend.fds == {}
    assert len(backend.close_log) == len(set(backend.close_log))  # No double close.
    assert executor.active_jobs == 0
    assert executor.retained_launchers == ()
    assert executor.pending_tasks == ()
    assert all(process.task.done() for process in backend.processes)


async def wait_for_process(backend: FakeBackend, count: int = 1) -> None:
    async with asyncio.timeout(2):
        while len(backend.processes) < count:
            await asyncio.sleep(0)


async def wait_for_spawn_call(backend: FakeBackend) -> None:
    async with asyncio.timeout(2):
        while not backend.spawn_calls:
            await asyncio.sleep(0)


def test_ring_drops_oldest_with_exact_counters() -> None:
    ring = DiagnosticRing(4)
    ring.append(b"ab")
    ring.append(b"")
    assert (ring.snapshot(), ring.total_bytes, ring.dropped_bytes) == (b"ab", 2, 0)
    ring.append(b"cdefgh")
    assert (ring.snapshot(), ring.total_bytes, ring.dropped_bytes) == (b"efgh", 8, 4)
    ring.append(b"i")
    assert (ring.snapshot(), ring.total_bytes, ring.dropped_bytes) == (b"fghi", 9, 5)
    for bad in (0, DIAGNOSTIC_RING_BYTES + 1, 4.0):
        with pytest.raises(ValueError):
            DiagnosticRing(bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ring.append(bytearray(b"x"))  # type: ignore[arg-type]


def test_constructor_shape_only_no_backend_ops() -> None:
    backend = FakeBackend(PROGRAMS)
    CommandExecutor(backend)
    assert backend.backend_ops == 0
    for kwargs in ({"max_jobs": 0}, {"max_jobs": MAX_JOBS + 1}, {"close_grace": 2}):
        with pytest.raises(ValueError):
            CommandExecutor(backend, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CommandExecutor(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_shape_refused_before_any_backend_operation() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend)
    for kwargs in (
        {"timeout": 5},
        {"timeout": 0.0},
        {"timeout": MAX_COMMAND_SECONDS + 1},
        {"timeout": 1.0, "stdin": "text"},
        {"timeout": 1.0, "stdin": b"x" * (MAX_STDIN_BYTES + 1)},
    ):
        with pytest.raises(ValueError):
            await executor.run(("/fake/cat",), {}, **kwargs)  # type: ignore[arg-type]
    assert backend.backend_ops == 0
    with pytest.raises(ValueError):
        await executor.run(("relative",), {}, timeout=1.0)  # Launcher argv check.
    assert backend.backend_ops == 0
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_echo_with_backpressure_partial_writes_and_diagnostics() -> None:
    backend = FakeBackend(PROGRAMS, capacity=4096)
    executor = CommandExecutor(backend)
    payload = bytes(range(256)) * 240  # 60 KiB through 4 KiB pipes.
    result = await executor.run(("/fake/cat",), {}, stdin=payload, timeout=2.0)
    assert result.returncode == 0
    assert result.stdout == payload
    assert result.diagnostics == b"diag:start\n"
    assert (result.diagnostic_bytes, result.diagnostic_dropped) == (11, 0)
    assert not backend.processes[0].killed
    assert_drained(backend, executor)
    await executor.aclose()


@pytest.mark.asyncio
async def test_nonzero_exit_is_command_result_not_exception() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend)
    result = await executor.run(("/fake/exit-three",), {}, timeout=2.0)
    assert (result.returncode, result.stdout, result.diagnostics) == (3, b"", b"bad input\n")
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_stdout_exactly_at_cap_is_accepted() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend)
    result = await executor.run(("/fake/flood-cap",), {}, timeout=2.0)
    assert result.returncode == 0 and len(result.stdout) == MAX_STDOUT_BYTES
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_stdout_over_cap_is_limit_child_stopped_executor_reusable() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend)
    with pytest.raises(CommandLimitFailure, match="stdout cap"):
        await executor.run(("/fake/flood-over-cap",), {}, timeout=2.0)
    assert backend.processes[0].returncode == -9
    assert not executor.cleanup_failed
    assert_drained(backend, executor)
    result = await executor.run(("/fake/exit-three",), {}, timeout=2.0)
    assert result.returncode == 3
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_stderr_flood_drains_continuously_into_drop_oldest_ring() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend)
    result = await executor.run(("/fake/flood-stderr",), {}, timeout=5.0)
    assert result.returncode == 0
    assert result.diagnostic_bytes == STDERR_FLOOD
    assert result.diagnostic_dropped == STDERR_FLOOD - DIAGNOSTIC_RING_BYTES
    last = STDERR_FLOOD // 4096
    expected = b"".join(i.to_bytes(4, "big") * 1024 for i in range(last - 16, last))
    assert result.diagnostics == expected
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_stalled_stdin_sink_hits_deadline_and_is_stopped() -> None:
    backend = FakeBackend(PROGRAMS, capacity=4096)
    executor = CommandExecutor(backend, close_grace=0.5)
    started = time.monotonic()
    with pytest.raises(CommandLimitFailure, match="deadline"):
        await executor.run(("/fake/sleeper",), {}, stdin=b"x" * MAX_STDIN_BYTES, timeout=0.2)
    assert time.monotonic() - started < 0.2 + 2 * 0.5
    assert backend.processes[0].returncode == -9
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_stream_activity_never_extends_deadline() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend, close_grace=0.5)
    started = time.monotonic()
    with pytest.raises(CommandLimitFailure, match="deadline"):
        await executor.run(("/fake/chatty",), {}, timeout=0.2)
    assert time.monotonic() - started < 0.2 + 2 * 0.5
    assert backend.processes[0].returncode == -9
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_deadline_starts_before_spawn_not_after_late_publication() -> None:
    backend = FakeBackend(PROGRAMS)
    gate = asyncio.Event()
    backend.spawn_gate = gate
    executor = CommandExecutor(backend, close_grace=0.5)

    async def publish_late() -> None:
        await asyncio.sleep(0.15)
        gate.set()

    feeder = asyncio.create_task(publish_late())
    started = time.monotonic()
    try:
        with pytest.raises(CommandLimitFailure, match="deadline"):
            await executor.run(("/fake/sleeper",), {}, timeout=0.25)
        # A deadline restarted at publication would end near 0.40 s.
        assert time.monotonic() - started < 0.35
    finally:
        gate.set()
        await feeder
    assert backend.processes[0].returncode == -9
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_child_closing_stdin_before_input_written_is_limit() -> None:
    backend = FakeBackend(PROGRAMS, capacity=4096)
    executor = CommandExecutor(backend)
    with pytest.raises(CommandLimitFailure, match="stdin"):
        await executor.run(("/fake/no-read-exit",), {}, stdin=b"x" * MAX_STDIN_BYTES, timeout=2.0)
    assert backend.processes[0].returncode == 0
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_output_eof_before_exit_waits_for_true_exit_code_without_kill() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend)
    result = await executor.run(("/fake/eof-then-exit",), {}, timeout=2.0)
    assert result.returncode == 5
    assert not backend.processes[0].killed
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_refuse_when_full_without_queue_then_slot_reusable() -> None:
    release = asyncio.Event()

    async def gated(child: FakeChild) -> int:
        await release.wait()
        return 0

    backend = FakeBackend({**PROGRAMS, "/fake/gated": gated})
    executor = CommandExecutor(backend, max_jobs=2)
    runs = [asyncio.create_task(executor.run(("/fake/gated",), {}, timeout=2.0)) for _ in "ab"]
    try:
        await wait_for_process(backend, 2)
        with pytest.raises(CommandLimitFailure, match="full"):
            await executor.run(("/fake/gated",), {}, timeout=2.0)
        assert len(backend.spawn_calls) == 2  # Refused before any allocation.
        assert executor.active_jobs == 2
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*runs), 2)
    assert [r.returncode for r in results] == [0, 0]
    assert (await executor.run(("/fake/exit-three",), {}, timeout=2.0)).returncode == 3
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_caller_cancel_during_io_returns_promptly_teardown_stays_owned() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend)
    run = asyncio.create_task(executor.run(("/fake/sleeper",), {}, timeout=5.0))
    await wait_for_process(backend)
    await asyncio.sleep(0)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    await executor.aclose()
    assert backend.processes[0].returncode == -9
    assert not executor.cleanup_failed
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_cancel_before_late_publication_child_killed_on_publication() -> None:
    backend = FakeBackend(PROGRAMS)
    gate = asyncio.Event()
    backend.spawn_gate = gate
    executor = CommandExecutor(backend)
    run = asyncio.create_task(executor.run(("/fake/sleeper",), {}, timeout=5.0))
    try:
        await wait_for_spawn_call(backend)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert executor.active_jobs == 1
        assert len(backend.fds) == 6  # Child ends retained while unpublished.
    finally:
        gate.set()
    await executor.aclose()
    assert backend.processes[0].killed and backend.processes[0].returncode == -9
    assert not executor.cleanup_failed
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_executor_close_during_command_is_limit_not_a_kill_result() -> None:
    backend = FakeBackend(PROGRAMS)
    executor = CommandExecutor(backend)
    run = asyncio.create_task(executor.run(("/fake/sleeper",), {}, timeout=5.0))
    await wait_for_process(backend)
    executor.close()
    with pytest.raises(CommandLimitFailure, match="executor closed"):
        await asyncio.wait_for(run, 2)
    await executor.aclose()
    with pytest.raises(CommandLimitFailure, match="executor closed"):
        await executor.run(("/fake/exit-three",), {}, timeout=1.0)
    assert len(backend.spawn_calls) == 1
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_spawn_refusal_is_ordinary_and_releases_slot() -> None:
    backend = FakeBackend(PROGRAMS)
    backend.spawn_error = FileNotFoundError("fake missing executable")
    executor = CommandExecutor(backend, max_jobs=1)
    with pytest.raises(ProcessLaunchFailure):
        await executor.run(("/fake/cat",), {}, timeout=1.0)
    assert not executor.cleanup_failed
    assert_drained(backend, executor)
    backend.spawn_error = None
    assert (await executor.run(("/fake/exit-three",), {}, timeout=1.0)).returncode == 3
    assert_drained(backend, executor)


@pytest.mark.asyncio
async def test_kill_failure_is_fatal_retained_visible_and_refuses_new_runs() -> None:
    backend = FakeBackend(PROGRAMS)
    backend.fail_kill = True
    executor = CommandExecutor(backend, close_grace=0.2)
    try:
        with pytest.raises(ProcessCleanupFailure):
            await executor.run(("/fake/sleeper",), {}, timeout=0.1)
        assert executor.cleanup_failed
        assert len(executor.retained_launchers) == 1
        assert executor.retained_launchers[0].owned_process is not None
        with pytest.raises(ProcessCleanupFailure):
            await executor.run(("/fake/exit-three",), {}, timeout=1.0)
        with pytest.raises(ProcessCleanupFailure):
            await executor.aclose()
        assert len(backend.spawn_calls) == 1
    finally:
        await backend.backstop()
    async with asyncio.timeout(1):
        while executor.retained_launchers[0].pending_tasks:
            await asyncio.sleep(0)
    assert backend.fds == {}  # Recovered only by the recorded child's exit.
    assert executor.cleanup_failed  # The latch never resets.


@pytest.mark.asyncio
async def test_exit_publication_mismatch_is_fatal_not_a_result() -> None:
    async def exit_zero_reported_one(child: FakeChild) -> int:
        process = backend.processes[0]
        process.report_override = 1
        return 0

    backend = FakeBackend({"/fake/mismatch": exit_zero_reported_one})
    executor = CommandExecutor(backend, close_grace=0.2)
    try:
        with pytest.raises(ProcessCleanupFailure):
            await executor.run(("/fake/mismatch",), {}, timeout=1.0)
        assert executor.cleanup_failed
        assert len(executor.retained_launchers) == 1
    finally:
        await backend.backstop()
    assert backend.fds == {}
