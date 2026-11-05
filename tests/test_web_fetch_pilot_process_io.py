"""Controlled native raw-launch fixtures: REVIEW BEFORE FIRST EXECUTION.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_process_io.py``.

Qualifies only ``RawLauncher`` with the real ``NativeBackend`` operations
(``pipe2``/``close``/``AsyncFD`` adoption/``create_subprocess_exec``) and tiny
inert isolated Python children: child descriptor inheritance, EOF, partial
writes, exact exit/reap, cancellation around native handle publication and
stale-descriptor safety. No shell, PATH search, socket, DNS, Docker, browser,
bootstrap or application-file writes.

Every child is self-bounded by ``signal.alarm`` (default SIGALRM action) and
runs in its own session. The backstop kills/reaps only handles recorded at
native creation; it never closes descriptors, discovers pids or signals
process groups. Asyncio deadlines cannot bound a wedged spawn/teardown, so
execution must be owned by an external watchdog (coreutils ``timeout``).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from scripts.web_fetch_pilot_io import TransportError
from scripts.web_fetch_pilot_process import (
    NativeBackend,
    ProcessCleanupFailure,
    ProcessLaunchFailure,
    RawLauncher,
    RawProcessHandle,
)

MARKER = "daemon-pilot-raw-native-fixture"  # Inert argv; external pgrep check.
CHUNK = 16 * 1024 + 12  # AsyncFD maximum write size.
PAYLOAD = bytes(range(256)) * 1024  # 256 KiB: four times default pipe capacity.
MISSING = "/nonexistent/daemon-pilot-raw-native-fixture"

_PREAMBLE = """import os, signal, sys
signal.alarm(10)
sys.dont_write_bytecode = True
"""
ECHO = (
    _PREAMBLE
    + """import json, stat, time
time.sleep(0.3)  # Parent must fill the pipe and observe a partial write first.
listing = [int(name) for name in os.listdir('/proc/self/fd')]
def is_open(fd):
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True
report = {
    'argv': sys.argv[1:],
    'env': sorted(os.environ),
    'extra_fds': sorted(fd for fd in listing if fd > 2 and is_open(fd)),
    'fifo': [stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (0, 1, 2)],
    'isolated': sys.flags.isolated == 1 and sys.flags.no_site == 1,
    'session_leader': os.getsid(0) == os.getpid(),
}
total = 0
while True:
    data = os.read(0, 65536)
    if not data:
        break
    view = memoryview(data)
    while view:
        view = view[os.write(1, view):]
    total += len(data)
report['echoed'] = total
os.write(2, json.dumps(report, sort_keys=True).encode())
"""
)
EOF_EXIT = (
    _PREAMBLE
    + """while os.read(0, 4096):
    pass
os._exit(7)  # Only stdin EOF reaches this; kill is -9, alarm is -14.
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
    """Real native operations; records each handle at creation, may delay it.

    ``publish`` holds back only the return of an already created native
    handle, so late publication is exercised with a real child.
    """

    def __init__(self, *, delayed: bool = False) -> None:
        self.processes: list[RawProcessHandle] = []
        self.created = asyncio.Event()
        self.publish = asyncio.Event()
        if not delayed:
            self.publish.set()

    async def spawn(
        self,
        argv: tuple[str, ...],
        env: dict[str, str],
        *,
        stdin_fd: int,
        stdout_fd: int,
        stderr_fd: int,
    ) -> RawProcessHandle:
        assert not self.processes  # Exactly one child per fixture.
        handle = await super().spawn(
            argv, env, stdin_fd=stdin_fd, stdout_fd=stdout_fd, stderr_fd=stderr_fd
        )
        self.processes.append(handle)
        self.created.set()
        await self.publish.wait()
        return handle


def fd_set() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


async def restored(fds: set[str], tasks: set[asyncio.Task[object]] | None = None) -> None:
    """Allow scheduled transport/pidfd callbacks to run; bounded, then assert."""
    for _ in range(200):
        if fd_set() == fds and (tasks is None or set(asyncio.all_tasks()) == tasks):
            break
        await asyncio.sleep(0.005)
    assert fd_set() == fds
    if tasks is not None:
        assert set(asyncio.all_tasks()) == tasks


def assert_drained(launcher: RawLauncher) -> None:
    assert launcher.owned_fds == ()
    assert launcher.pending_tasks == ()
    assert launcher.owned_process is None


async def exited(handle: RawProcessHandle) -> int:
    for _ in range(400):
        code = handle.returncode
        if code is not None:
            return code
        await asyncio.sleep(0.005)
    raise AssertionError("child exit not published within 2 seconds")


@asynccontextmanager
async def owned(
    *, delayed: bool = False, launch_deadline: float = 5.0, launcher_type: type = RawLauncher
) -> AsyncIterator[tuple[RecordingBackend, RawLauncher]]:
    """Always release publication; recover only recorded native children."""
    backend = RecordingBackend(delayed=delayed)
    launcher = launcher_type(backend, launch_deadline=launch_deadline, close_grace=2.0)
    try:
        async with asyncio.timeout(10):
            yield backend, launcher
    finally:
        backend.publish.set()
        launcher.close()
        try:
            try:
                await launcher.aclose()
            except ProcessCleanupFailure:
                pass  # The test body asserts cleanup_failed; recovery still runs.
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
async def test_native_echo_partial_write_eof_exit_zero_and_child_fds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    monkeypatch.setenv("PILOT_FIXTURE_PARENT_MARKER", "synthetic-parent-only")
    async with owned() as (backend, launcher):
        session = await launcher.launch(argv(ECHO), {})
        assert len(launcher.owned_fds) == 3  # Child ends closed after publication.
        counts: list[int] = []

        async def write_all() -> None:
            offset = 0
            while offset < len(PAYLOAD):
                chunk = PAYLOAD[offset : offset + CHUNK]
                count = await session.stdin.write(chunk)
                counts.append(count)
                offset += count
            session.stdin.close()  # Half-close; owner close must not reclose.

        async def read_all(channel_name: str) -> bytes:
            channel = getattr(session, channel_name)
            received = bytearray()
            while data := await channel.read(16 * 1024):
                received.extend(data)
            return bytes(received)

        _, echoed, report_raw = await asyncio.gather(
            write_all(), read_all("stdout"), read_all("stderr")
        )
        assert echoed == PAYLOAD
        assert any(count < CHUNK for count in counts[:-1])  # Real partial write.
        report = json.loads(report_raw)
        assert report == {
            "argv": [MARKER],
            "echoed": len(PAYLOAD),
            "env": report["env"],
            "extra_fds": [],
            "fifo": [True, True, True],
            "isolated": True,
            "session_leader": True,
        }
        assert set(report["env"]) <= {"LC_CTYPE"}  # Interpreter locale coercion only.
        assert await exited(backend.processes[0]) == 0
        await session.aclose()
        assert session.returncode == 0
        assert not launcher.cleanup_failed
        assert_drained(launcher)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_close_stdio_delivers_eof_child_exits_without_kill() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (backend, launcher):
        session = await launcher.launch(argv(EOF_EXIT), {})
        session.close_stdio()
        assert await exited(backend.processes[0]) == 7  # EOF, not kill/alarm.
        await session.aclose()
        assert session.returncode == 7
        assert not launcher.cleanup_failed
        assert_drained(launcher)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_aclose_kills_running_child_within_grace() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (backend, launcher):
        session = await launcher.launch(argv(SLEEP), {})
        assert session.returncode is None
        started = time.monotonic()
        await session.aclose()
        assert time.monotonic() - started < 2.0
        assert backend.processes[0].returncode == -9
        assert not launcher.cleanup_failed
        assert_drained(launcher)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_spawn_refusal_is_ordinary_and_releases_slot() -> None:
    assert not os.path.exists(MISSING)
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (backend, launcher):
        with pytest.raises(ProcessLaunchFailure, match="refused launch"):
            await launcher.launch((MISSING, MARKER), {})
        assert backend.processes == []
        assert not launcher.cleanup_failed
        assert_drained(launcher)
        await restored(fds)  # All six raw fds closed natively.
        session = await launcher.launch(argv(SLEEP), {})  # Slot was released.
        await session.aclose()
        assert backend.processes[0].returncode == -9
        assert_drained(launcher)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_cancel_at_first_launch_yield_child_still_owned() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned() as (backend, launcher):
        launch = asyncio.ensure_future(launcher.launch(argv(SLEEP), {}))
        await asyncio.sleep(0)  # Launch awaits its tracked spawn task.
        # Earliest caller cancel; the spawn task is never cancelled, so any
        # child it creates (before or after this point) must stay owned.
        launch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await launch
        await launcher.aclose()  # Spawn still runs; late child stopped/reaped.
        assert len(backend.processes) == 1
        assert backend.processes[0].returncode == -9
        assert not launcher.cleanup_failed
        assert_drained(launcher)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_cancel_after_creation_before_publication_late_kill_reap() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned(delayed=True) as (backend, launcher):
        launch = asyncio.ensure_future(launcher.launch(argv(SLEEP), {}))
        await asyncio.wait_for(backend.created.wait(), 5)  # Real child, unpublished.
        launch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await launch
        with pytest.raises(asyncio.CancelledError):
            await launch  # Re-awaiting the cancelled caller loses nothing.
        child = backend.processes[0]
        assert child.returncode is None
        assert launcher.pending_tasks != ()
        assert len(launcher.owned_fds) == 6  # Child ends retained while unpublished.
        backend.publish.set()
        await launcher.aclose()
        assert child.returncode == -9
        assert not launcher.cleanup_failed
        assert_drained(launcher)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_launch_deadline_late_publication_is_ordinary() -> None:
    stopped = asyncio.Event()

    class ObservedLauncher(RawLauncher):
        def _stop_job(self, job):
            super()._stop_job(job)
            stopped.set()

    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned(delayed=True, launch_deadline=0.5, launcher_type=ObservedLauncher) as (
        backend,
        launcher,
    ):

        async def publish_after_stop() -> None:
            await stopped.wait()  # Publish only after the actual deadline stop.
            backend.publish.set()

        feeder = asyncio.create_task(publish_after_stop())
        try:
            with pytest.raises(ProcessLaunchFailure, match="deadline exceeded"):
                await launcher.launch(argv(SLEEP), {})
        finally:
            stopped.set()
            await asyncio.wait_for(feeder, 1)
        assert backend.processes[0].returncode == -9
        assert not launcher.cleanup_failed
        assert_drained(launcher)
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_repeated_close_never_touches_reused_descriptor_numbers() -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    foreign: list[int] = []
    try:
        async with owned() as (backend, launcher):
            session = await launcher.launch(argv(SLEEP), {})
            parent_ends = set(launcher.owned_fds)
            assert len(parent_ends) == 3
            await session.aclose()
            assert_drained(launcher)
            for _ in range(8):  # Lowest-free allocation reuses a released number.
                foreign.extend(os.pipe2(os.O_CLOEXEC))
                if parent_ends & set(foreign):
                    break
            assert parent_ends & set(foreign)
            session.close_stdio()
            session.kill()
            await session.aclose()
            with pytest.raises(TransportError):
                await session.stdout.read(1)  # Stale channel refuses before any syscall.
            with pytest.raises(TransportError):
                await session.stdin.write(b"x")
            launcher.close()
            await launcher.aclose()
            for read_fd, write_fd in zip(foreign[::2], foreign[1::2], strict=True):
                assert os.write(write_fd, b"z") == 1
                assert os.read(read_fd, 1) == b"z"
            assert backend.processes[0].returncode == -9
            assert not launcher.cleanup_failed
    finally:
        for fd in foreign:  # Test-created foreign pipes only.
            os.close(fd)
    await restored(fds, tasks)
