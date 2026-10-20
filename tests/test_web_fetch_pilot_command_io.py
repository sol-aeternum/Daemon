"""Controlled native command-executor fixtures: REVIEW BEFORE FIRST EXECUTION.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_command_io.py``.

Qualifies only ``CommandExecutor`` over the real ``NativeBackend`` with tiny
inert isolated Python children: exact result/limit classification, native
``RawSession.wait()`` without kill, stdout cap, continuous stderr draining,
deadline stop, caller cancellation, executor close and refuse-when-full. No
shell, PATH search, socket, DNS, Docker, browser, bootstrap or
application-file writes.

Every child is self-bounded by ``signal.alarm`` (default SIGALRM action) and
runs in its own session. The backstop kills/reaps only handles recorded at
native creation; it never closes descriptors, discovers pids or signals
process groups. Execution must be owned by an external watchdog (coreutils
``timeout``); asyncio deadlines cannot bound a wedged spawn or teardown.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from scripts.web_fetch_pilot_command import (
    DIAGNOSTIC_RING_BYTES,
    MAX_STDOUT_BYTES,
    CommandExecutor,
    CommandLimitFailure,
)
from scripts.web_fetch_pilot_process import (
    NativeBackend,
    ProcessCleanupFailure,
    ProcessLaunchFailure,
    RawProcessHandle,
)

MARKER = "daemon-pilot-command-native-fixture"  # Inert argv; external pgrep check.
MISSING = "/nonexistent/daemon-pilot-command-native-fixture"
STDERR_FLOOD = 1024 * 1024

_PREAMBLE = """import os, signal, sys
signal.alarm(10)
sys.dont_write_bytecode = True
"""
ECHO = (
    _PREAMBLE
    + """os.write(2, b'diag:start\\n')
while data := os.read(0, 65536):
    view = memoryview(data)
    while view:
        view = view[os.write(1, view):]
"""
)
EOF_THEN_EXIT = (
    _PREAMBLE
    + """import time
os.close(1)
os.close(2)
time.sleep(0.2)  # Output EOF well before exit: the owner must wait, not kill.
os._exit(3)
"""
)
OVER_CAP = (
    _PREAMBLE
    + f"""import time
os.write(1, b'o' * {MAX_STDOUT_BYTES + 1})
time.sleep(30)
"""
)
STDERR_FLOOD_PROGRAM = (
    _PREAMBLE
    + f"""for index in range({STDERR_FLOOD // 4096}):
    os.write(2, index.to_bytes(4, 'big') * 1024)
"""
)
SLEEP = (
    _PREAMBLE
    + """import time
time.sleep(30)
"""
)


def argv(program: str) -> tuple[str, ...]:
    return (sys.executable, "-I", "-S", "-c", program, MARKER)


class RecordingBackend(NativeBackend):
    """Real native operations; records every handle at creation."""

    def __init__(self) -> None:
        self.processes: list[RawProcessHandle] = []

    async def spawn(
        self,
        argv: tuple[str, ...],
        env: dict[str, str],
        *,
        stdin_fd: int,
        stdout_fd: int,
        stderr_fd: int,
    ) -> RawProcessHandle:
        assert len(self.processes) < 3  # Bounded child count per fixture.
        handle = await super().spawn(
            argv, env, stdin_fd=stdin_fd, stdout_fd=stdout_fd, stderr_fd=stderr_fd
        )
        self.processes.append(handle)
        return handle


def fd_set() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


async def restored(fds: set[str], tasks: set[asyncio.Task[object]]) -> None:
    """Allow scheduled transport/pidfd callbacks to run; bounded, then assert."""
    for _ in range(200):
        if fd_set() == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert fd_set() == fds
    assert set(asyncio.all_tasks()) == tasks


def assert_drained(executor: CommandExecutor) -> None:
    assert executor.active_jobs == 0
    assert executor.retained_launchers == ()
    assert executor.pending_tasks == ()
    assert not executor.cleanup_failed


async def wait_for_children(backend: RecordingBackend, count: int) -> None:
    async with asyncio.timeout(5):
        while len(backend.processes) < count:
            await asyncio.sleep(0.005)


@asynccontextmanager
async def owned(*, max_jobs: int = 4) -> AsyncIterator[tuple[RecordingBackend, CommandExecutor]]:
    """Always close the executor; recover only recorded native children."""
    backend = RecordingBackend()
    executor = CommandExecutor(backend, max_jobs=max_jobs, close_grace=2.0)
    try:
        async with asyncio.timeout(15):
            yield backend, executor
    finally:
        try:
            try:
                await executor.aclose()
            except ProcessCleanupFailure:
                pass  # The test body asserts cleanup state; recovery still runs.
        finally:
            # Only recorded handles: never pid discovery, killpg or fd closes.
            for handle in backend.processes:
                if handle.returncode is None:
                    try:
                        handle.kill()
                    except ProcessLookupError:
                        pass
                await asyncio.wait_for(handle.wait(), 2)


@pytest.mark.asyncio
async def test_native_command_echo_result_with_diagnostics() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    payload = bytes(range(256)) * 240  # 60 KiB, under the stdout cap.
    async with owned() as (backend, executor):
        result = await executor.run(argv(ECHO), {}, stdin=payload, timeout=5.0)
        assert result.returncode == 0
        assert result.stdout == payload
        assert result.diagnostics == b"diag:start\n"
        assert (result.diagnostic_bytes, result.diagnostic_dropped) == (11, 0)
        assert backend.processes[0].returncode == 0
        assert_drained(executor)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_output_eof_before_exit_waits_for_exact_exit_without_kill() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (_backend, executor):
        started = time.monotonic()
        result = await executor.run(argv(EOF_THEN_EXIT), {}, timeout=5.0)
        assert result.returncode == 3  # A kill would report -9.
        assert time.monotonic() - started >= 0.2
        assert (result.stdout, result.diagnostics) == (b"", b"")
        assert_drained(executor)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_stdout_over_cap_is_limit_and_child_killed() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (backend, executor):
        with pytest.raises(CommandLimitFailure, match="stdout cap"):
            await executor.run(argv(OVER_CAP), {}, timeout=5.0)
        assert backend.processes[0].returncode == -9
        assert_drained(executor)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_stderr_flood_drains_into_drop_oldest_ring() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (_backend, executor):
        result = await executor.run(argv(STDERR_FLOOD_PROGRAM), {}, timeout=10.0)
        assert result.returncode == 0
        assert result.diagnostic_bytes == STDERR_FLOOD
        assert result.diagnostic_dropped == STDERR_FLOOD - DIAGNOSTIC_RING_BYTES
        last = STDERR_FLOOD // 4096
        expected = b"".join(i.to_bytes(4, "big") * 1024 for i in range(last - 16, last))
        assert result.diagnostics == expected
        assert_drained(executor)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_deadline_stops_running_child() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (backend, executor):
        started = time.monotonic()
        with pytest.raises(CommandLimitFailure, match="deadline"):
            await executor.run(argv(SLEEP), {}, timeout=0.5)
        assert time.monotonic() - started < 0.5 + 2 * 2.0
        assert backend.processes[0].returncode == -9
        assert_drained(executor)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_caller_cancel_teardown_stays_owned() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (backend, executor):
        run = asyncio.create_task(executor.run(argv(SLEEP), {}, timeout=5.0))
        await wait_for_children(backend, 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        await executor.aclose()
        assert backend.processes[0].returncode == -9
        assert_drained(executor)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_refuse_when_full_then_close_is_limit_for_running_commands() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned(max_jobs=2) as (backend, executor):
        runs = [asyncio.create_task(executor.run(argv(SLEEP), {}, timeout=5.0)) for _ in range(2)]
        await wait_for_children(backend, 2)
        with pytest.raises(CommandLimitFailure, match="full"):
            await executor.run(argv(SLEEP), {}, timeout=5.0)
        assert len(backend.processes) == 2  # Refused before any allocation.
        executor.close()
        outcomes = await asyncio.wait_for(asyncio.gather(*runs, return_exceptions=True), 5)
        assert all(
            isinstance(o, CommandLimitFailure) and "executor closed" in str(o) for o in outcomes
        )
        await executor.aclose()
        assert [p.returncode for p in backend.processes] == [-9, -9]
        assert_drained(executor)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_missing_executable_is_launch_failure_and_slot_released() -> None:
    assert not os.path.exists(MISSING)
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned(max_jobs=1) as (backend, executor):
        with pytest.raises(ProcessLaunchFailure):
            await executor.run((MISSING, MARKER), {}, timeout=5.0)
        assert backend.processes == []
        assert_drained(executor)
        result = await executor.run(argv(EOF_THEN_EXIT), {}, timeout=5.0)
        assert result.returncode == 3
        assert_drained(executor)
    await restored(fds, tasks)
