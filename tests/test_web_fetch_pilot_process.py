"""Fake-only launcher/session tests; NO real OS pipes/process/DNS here.

The native backend is inspected as source only (AST/text); it is never
invoked. Fakes mirror important AsyncFD constructor ownership refusals, and
every injected gate is released in a ``finally`` so no fake task leaks. A
default-capture witness proves these tests never autouse-patch os.pipe2/os.close.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import textwrap
from dataclasses import dataclass, field

import pytest


from scripts.web_fetch_pilot_process import (
    MAX_ARGV_BYTES,
    MAX_ARGV_ITEMS,
    MAX_CLOSE_GRACE_SECONDS,
    MAX_LAUNCH_SECONDS,
    NativeBackend,
    ProcessCleanupFailure,
    ProcessLaunchFailure,
    RawLauncher,
    RawSession,
)

# Witness defaults, captured before any possible patch; never autouse-patched.
_REAL_PIPE2: object = os.pipe2
_REAL_CLOSE: object = os.close

FAKE_ARGV = ("/bin/fake-a", "/bin/fake-b", "x")


@dataclass
class FakePipe:
    """One pipe pair with a shared buffer and two endpoint descriptors."""

    buf: bytearray = field(default_factory=bytearray)
    read_fd: int = -1  # child read / parent write (stdin)
    write_fd: int = -1  # child write / parent read (stdout, stderr)
    read_closed: bool = False
    write_closed: bool = False
    adopted: bool = False
    backend: FakeBackend | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class FakeChannel:
    """Adopted FakePipe endpoint with AsyncFD-sized bounded I/O parity.

    Fake failure mirrors real descriptor ownership: writes fail only when
    this endpoint itself is closed (the child's kept dup is invisible to
    the parent); reads EOF once the writer side is closed and the buffer is
    empty; close closes exactly the adopted endpoint fd.
    """

    pipe: FakePipe
    fd: int
    is_writer: bool

    async def read(self, size: int) -> bytes:
        if type(size) is not int or not 0 < size <= 16:
            raise ValueError("fake read size outside bounded range")
        while not self.pipe.buf:
            if self.pipe.write_closed:  # writer side gone => EOF
                return b""
            try:
                await asyncio.wait_for(self.pipe.wake.wait(), timeout=5)
            except TimeoutError:
                raise AssertionError("fake pipe reader deadline exceeded") from None
            self.pipe.wake.clear()
        payload = bytes(self.pipe.buf[:size])
        del self.pipe.buf[:size]
        return payload

    async def write(self, data: bytes) -> int:
        if type(data) is not bytes or not 0 < len(data) <= 16 + 12:
            raise ValueError("fake write size/type outside bounded range")
        if self.is_writer and self.pipe.write_closed:
            raise BrokenPipeError("fake pipe write endpoint closed")
        self.pipe.buf.extend(data)
        self.pipe.wake.set()
        return len(data)

    def close(self) -> None:
        assert self.pipe.backend is not None
        self.pipe.backend.close_fd(self.fd)


@dataclass
class FakeProcess:
    """Fake child handle: exit is only published by an explicit child_exit."""

    backend: FakeBackend
    returncode: int | None = None
    report_override: int | None = None
    killed: bool = False
    exited: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self) -> int:
        await self.exited.wait()
        reported = self.report_override if self.report_override is not None else self.returncode
        if reported is None:
            raise AssertionError("fake child exited without returncode")
        return reported

    def kill(self) -> None:
        if self.backend.lookup_error_on_kill:
            raise ProcessLookupError("fake process already exited")
        if self.backend.fail_kill:
            raise OSError("fake kill failure")
        if self.returncode is None:
            self.killed = True


@dataclass
class FakeSpawnCall:
    argv: tuple[str, ...]
    env: dict[str, str]
    stdin_fd: int
    stdout_fd: int
    stderr_fd: int


class FakeBackend:
    """Fake RawBackend; every close is logged so double close is detectable."""

    def __init__(self) -> None:
        self.pipes: list[FakePipe] = []
        self.spawn_calls: list[FakeSpawnCall] = []
        self.close_log: list[int] = []
        self.adopt_log: list[int] = []
        self.pipe2_calls = 0
        self.pipe2_fail_on_call: int | None = None
        self.adopt_fail_fds: frozenset[int] = frozenset()
        self.close_fail_fds: frozenset[int] = frozenset()
        self.spawn_gate: asyncio.Event | None = None
        self.spawn_error: BaseException | None = None
        self.fail_kill = False
        self.lookup_error_on_kill = False
        self.process: FakeProcess | None = None
        self.next_fd = 50_000

    def live_fds(self) -> set[int]:
        live: set[int] = set()
        for pipe in self.pipes:
            if not pipe.read_closed:
                live.add(pipe.read_fd)
            if not pipe.write_closed:
                live.add(pipe.write_fd)
        return live

    def _pipe(self, fd: int) -> FakePipe:
        for pipe in self.pipes:
            if fd in (pipe.read_fd, pipe.write_fd):
                return pipe
        raise OSError(9, "fake unknown descriptor")

    def pipe2(self) -> tuple[int, int]:
        if self.pipe2_fail_on_call == self.pipe2_calls + 1:
            raise ValueError("fake pipe allocation refused")
        self.pipe2_calls += 1
        pipe = FakePipe(read_fd=self.next_fd, write_fd=self.next_fd + 1, backend=self)
        self.next_fd += 2
        self.pipes.append(pipe)
        return pipe.read_fd, pipe.write_fd

    def close_fd(self, fd: int) -> None:
        pipe = self._pipe(fd)
        if fd in self.close_fail_fds:
            raise OSError("fake close failure")
        if fd == pipe.read_fd:
            if pipe.read_closed:
                raise OSError("fake double close")
            pipe.read_closed = True
        else:
            if pipe.write_closed:
                raise OSError("fake double close")
            pipe.write_closed = True
        self.close_log.append(fd)
        pipe.wake.set()

    def adopt(self, fd: int) -> FakeChannel:
        # Mirrors AsyncFD constructor refusals: unknown/closed/reused refuse.
        pipe = self._pipe(fd)
        if fd in self.adopt_fail_fds or pipe.adopted:
            raise ValueError("fake adopt refused")
        if any(
            (fd == pipe.read_fd and pipe.read_closed, fd == pipe.write_fd and pipe.write_closed)
        ):
            raise ValueError("fake adopt refused closed descriptor")
        pipe.adopted = True
        self.adopt_log.append(fd)
        return FakeChannel(pipe=pipe, fd=fd, is_writer=fd == pipe.write_fd)

    async def spawn(
        self,
        argv: tuple[str, ...],
        env: dict[str, str],
        *,
        stdin_fd: int,
        stdout_fd: int,
        stderr_fd: int,
    ) -> FakeProcess:
        self.spawn_calls.append(FakeSpawnCall(argv, dict(env), stdin_fd, stdout_fd, stderr_fd))
        if self.spawn_gate is not None:
            await self.spawn_gate.wait()
        if self.spawn_error is not None:
            raise self.spawn_error
        self.process = FakeProcess(backend=self)
        return self.process

    def child_exit(self, code: int) -> None:
        assert self.process is not None
        self.process.returncode = code
        self.process.exited.set()


def assert_drained(backend: FakeBackend, launcher: RawLauncher) -> None:
    assert backend.live_fds() == set()
    assert launcher.owned_fds == ()
    assert launcher.pending_tasks == ()
    assert launcher.owned_process is None


async def settle(launcher: RawLauncher) -> None:
    for _ in range(60):
        if not launcher.pending_tasks:
            return
        await asyncio.sleep(0)
    raise AssertionError("fake owned launcher tasks did not settle")


def test_launcher_constructor_is_shape_only_no_backend_ops() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    assert launcher.pending_tasks == ()
    assert launcher.owned_fds == ()
    assert launcher.owned_process is None
    assert launcher.cleanup_failed is False
    assert backend.spawn_calls == []  # no default spawn trigger in constructor
    assert backend.pipe2_calls == 0
    with pytest.raises(ValueError):
        RawLauncher(backend, launch_deadline=0.0)
    with pytest.raises(ValueError):
        RawLauncher(backend, close_grace=MAX_CLOSE_GRACE_SECONDS * 2)


@pytest.mark.asyncio
async def test_six_pipe_ownership_exact_spawn_kwargs_no_env_inherit() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    env = {"PILOT_K": "pilot-v"}  # exact, scrubbed: nothing ambient inherited
    session = await launcher.launch(FAKE_ARGV, env)
    assert isinstance(session, RawSession)
    try:
        (call,) = backend.spawn_calls
        assert call.argv == FAKE_ARGV
        assert call.env is not env and call.env == env
        pipes = backend.pipes
        assert call.stdin_fd == pipes[0].read_fd
        assert call.stdout_fd == pipes[1].write_fd
        assert call.stderr_fd == pipes[2].write_fd
        assert backend.adopt_log == [pipes[0].write_fd, pipes[1].read_fd, pipes[2].read_fd]
        assert set(launcher.owned_fds) == {pipes[0].write_fd, pipes[1].read_fd, pipes[2].read_fd}
        assert len(backend.close_log) == 3  # child sides raw-closed post-publication
        await session.stdin.write(b"ping")
        assert bytes(pipes[0].buf) == b"ping"
        pipes[1].buf.extend(b"pong")
        assert await session.stdout.read(16) == b"pong"
    finally:
        backend.child_exit(0)
        await session.aclose()
        await settle(launcher)
        assert_drained(backend, launcher)
    assert len(backend.close_log) == 6


def pipe_fds(backend: FakeBackend) -> list[int]:
    out: list[int] = []
    for pipe in backend.pipes:
        out.extend((pipe.read_fd, pipe.write_fd))
    return out


@pytest.mark.asyncio
async def test_session_stdin_stdout_roundtrip_then_aclose_releases_everything() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    session = await launcher.launch(FAKE_ARGV, {})
    try:
        pipes = backend.pipes
        pipes[1].buf.extend(b"payload")
        assert await session.stdout.read(16) == b"payload"
        assert await session.stdout.read(16) == b""  # write end already closed
        assert await session.stderr.read(16) == b""
        await session.stdin.write(b"request")
        assert bytes(pipes[0].buf) == b"request"
        assert session.returncode is None
        backend.child_exit(7)
    finally:
        await session.aclose()
        await settle(launcher)
    assert session.returncode == 7
    assert len(backend.close_log) == 6
    assert len(set(backend.close_log)) == 6  # exactly once per descriptor
    assert_drained(backend, launcher)


@pytest.mark.asyncio
async def test_close_stdio_forces_child_eof_and_refuses_more_writes() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend, close_grace=0.1)
    session = await launcher.launch(FAKE_ARGV, {})
    try:
        stdin_pipe = backend.pipes[0]
        assert backend.process is not None and not backend.process.killed
        session.close_stdio()
        assert stdin_pipe.write_closed is True  # child read side observes EOF
        session.close_stdio()  # repeated close stays a safe no-op
        assert backend.process.killed is False
        with pytest.raises(BrokenPipeError):
            await session.stdin.write(b"late")
        assert session.returncode is None  # exit was not implied by stdio close
    finally:
        backend.child_exit(0)
        await session.aclose()
        await settle(launcher)
    assert_drained(backend, launcher)


@pytest.mark.asyncio
async def test_close_synchronous_kills_then_release_after_late_exit() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    session = await launcher.launch(FAKE_ARGV, {})
    launcher.close()
    assert backend.process is not None and backend.process.killed
    assert session.returncode is None  # kill does not imply exit
    try:
        launcher.close()  # repeated close safe
        backend.child_exit(9)
    finally:
        await session.aclose()
        await settle(launcher)
    assert session.returncode == 9
    assert_drained(backend, launcher)
    with pytest.raises(ProcessCleanupFailure):
        await launcher.launch(FAKE_ARGV, {})  # closed launcher never reused


@pytest.mark.asyncio
async def test_cancelled_launch_retained_then_late_publication_kill_reap() -> None:
    backend = FakeBackend()
    gate = asyncio.Event()
    backend.spawn_gate = gate
    launcher = RawLauncher(backend)
    for _ in range(1):  # repeated cancel of the same owning ownership intent
        task = asyncio.ensure_future(launcher.launch(FAKE_ARGV, {}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(asyncio.CancelledError):
            await task  # re-cancelling a settled task never loses ownership
    assert launcher.pending_tasks != ()  # retained until native publication
    assert len(launcher.owned_fds) == 6
    gate.set()
    try:
        for _ in range(50):
            if backend.process is not None:
                break
            await asyncio.sleep(0)
        assert backend.process is not None  # late publication happened
        backend.child_exit(3)
        await settle(launcher)
    finally:
        if not gate.is_set():
            gate.set()
            if backend.process is not None:
                backend.child_exit(3)
            await settle(launcher)
    process = backend.process
    assert process is not None and process.killed
    assert launcher.owned_fds == ()
    assert launcher.pending_tasks == ()
    assert len(backend.close_log) == 6
    assert len(set(backend.close_log)) == 6
    assert_drained(backend, launcher)


@pytest.mark.asyncio
async def test_adopting_partially_uses_shared_pair_buffer_streaming_safe() -> None:
    # Owned parent ends keep reading buffered payload even after the child
    # ends are closed right after spawn publication.
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    session = await launcher.launch(FAKE_ARGV, {})
    backend.pipes[2].buf.extend(b"err!")
    try:
        assert await session.stderr.read(16) == b"err!"
        assert await session.stderr.read(16) == b""
    finally:
        backend.child_exit(0)
        await session.aclose()
        await settle(launcher)
    assert_drained(backend, launcher)


@pytest.mark.asyncio
async def test_spawn_refusal_closes_all_six_fds_and_is_ordinary() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    backend.spawn_error = ValueError("fake spawn refused")
    with pytest.raises(ProcessLaunchFailure):
        await launcher.launch(FAKE_ARGV, {})
    assert len(backend.close_log) == 6
    assert len(set(backend.close_log)) == 6
    assert launcher.owned_fds == ()
    assert launcher.pending_tasks == ()
    assert launcher.cleanup_failed is False
    assert launcher.owned_process is None


@pytest.mark.asyncio
async def test_partial_pipe_allocation_failure_closes_only_allocated() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    backend.pipe2_fail_on_call = 3
    with pytest.raises(ProcessLaunchFailure):
        await launcher.launch(FAKE_ARGV, {})
    assert len(backend.pipes) == 2  # two pairs existed and were closed
    assert sorted(backend.close_log) == sorted(
        fds for pipe in backend.pipes for fds in (pipe.read_fd, pipe.write_fd)
    )
    assert len(backend.close_log) == 4
    assert launcher.owned_fds == ()
    assert launcher.owned_process is None
    assert launcher.cleanup_failed is False
    assert launcher.pending_tasks == ()


@pytest.mark.asyncio
async def test_adopt_failure_paths_close_only_owned_and_latch_fatal() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    backend.adopt_fail_fds = frozenset({50_000 + 2})  # stdout parent read side
    with pytest.raises(ProcessCleanupFailure):
        await launcher.launch(FAKE_ARGV, {})
    try:
        # Adopted stdin channel closed; remaining raw fds raw-closed.
        assert backend.live_fds() == set()
        assert launcher.cleanup_failed is True
        with pytest.raises(ProcessCleanupFailure):
            await launcher.launch(FAKE_ARGV, {})  # fatal prohibits new allocation
    finally:
        if backend.process is not None:
            backend.child_exit(1)
        await settle(launcher)


@pytest.mark.asyncio
async def test_failed_kill_retains_visibility_latches_fatal_then_drains() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend, close_grace=0.1)
    session = await launcher.launch(FAKE_ARGV, {})
    try:
        backend.fail_kill = True
        with pytest.raises(ProcessCleanupFailure):
            session.kill()
            await session.aclose()
        assert launcher.cleanup_failed is True
        assert launcher.owned_process is not None
        with pytest.raises(ProcessCleanupFailure):
            await launcher.launch(FAKE_ARGV, {})
    finally:
        backend.fail_kill = False
        backend.child_exit(5)
        await settle(launcher)
    assert launcher.owned_fds == ()
    assert launcher.pending_tasks == ()
    with pytest.raises(ProcessCleanupFailure):
        await launcher.launch(FAKE_ARGV, {})  # fatal latch never resets


@pytest.mark.asyncio
async def test_process_lookup_error_kill_is_swallowed_still_releases() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    session = await launcher.launch(FAKE_ARGV, {})
    backend.lookup_error_on_kill = True
    session.kill()
    backend.child_exit(6)
    await session.aclose()
    await settle(launcher)
    assert session.returncode == 6
    assert launcher.cleanup_failed is False
    assert_drained(backend, launcher)


@pytest.mark.asyncio
async def test_exit_publication_mismatch_latches_fatal_and_retains_visible() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend, close_grace=0.1)
    session = await launcher.launch(FAKE_ARGV, {})
    process = backend.process
    assert process is not None
    process.report_override = 7
    process.returncode = 1
    process.exited.set()
    try:
        with pytest.raises(ProcessCleanupFailure):
            await session.aclose()
        assert launcher.cleanup_failed is True
        assert launcher.pending_tasks == ()  # mismatch failed the owned reap
        assert launcher.owned_process is not None  # handle stays retained-visible
    finally:
        process.report_override = None
    with pytest.raises(ProcessCleanupFailure):
        await launcher.launch(FAKE_ARGV, {})


@pytest.mark.asyncio
async def test_launch_deadline_exceeded_kills_and_stays_ordinary_runnable() -> None:
    backend = FakeBackend()
    gate = asyncio.Event()
    backend.spawn_gate = gate
    launcher = RawLauncher(backend, launch_deadline=0.01, close_grace=0.2)
    feeder = asyncio.ensure_future(late_publish(backend, gate))
    with pytest.raises(ProcessLaunchFailure):
        await launcher.launch(FAKE_ARGV, {})
    await feeder
    published = backend.process
    assert published is not None and published.killed
    assert launcher.cleanup_failed is False
    assert_drained(backend, launcher)


async def late_publish(backend: FakeBackend, gate: asyncio.Event) -> None:
    """Publish the native handle late; exit as soon as the fake child exists."""
    try:
        await asyncio.sleep(0.05)
        gate.set()
        for _ in range(200):
            if backend.process is not None:
                backend.child_exit(0)
                return
            await asyncio.sleep(0)
        raise AssertionError("no late publication")
    finally:
        gate.set()
        if backend.process is not None and not backend.process.exited.is_set():
            backend.child_exit(0)


@pytest.mark.asyncio
async def test_session_bad_io_shapes_refused_without_state_change() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend, close_grace=0.1)
    session = await launcher.launch(FAKE_ARGV, {})
    try:
        with pytest.raises(ValueError):
            await session.stdin.write(b"")
        with pytest.raises(ValueError):
            await session.stdin.write("str")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            await session.stdout.read(0)
    finally:
        backend.child_exit(0)
        await session.aclose()
        await settle(launcher)
    assert_drained(backend, launcher)


@pytest.mark.asyncio
async def test_oversize_or_invalid_argv_and_env_refused_clean() -> None:
    launcher = RawLauncher(FakeBackend(), launch_deadline=0.01)
    with pytest.raises(ValueError):
        await launcher.launch(("/bin/x",) * (MAX_ARGV_ITEMS + 1), {})
    with pytest.raises(ValueError):
        await launcher.launch(("relative", "x"), {})  # no PATH search permitted
    with pytest.raises(ValueError):
        await launcher.launch(("bin/x\x00",), {})
    over = "/bin/x" + "y" * 200_000  # exceeds 128 KiB argv cap
    with pytest.raises(ValueError):
        await launcher.launch((over,), {})
    with pytest.raises(ValueError):
        await launcher.launch(("/bin/x",), {"K": "v\x00"})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        await launcher.launch(("/bin/x",), {"K": 1})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        await launcher.launch(("/bin/x",), "ambient-like")  # type: ignore[arg-type]
    assert launcher.pending_tasks == ()


@pytest.mark.asyncio
async def test_session_returncode_int_match_and_slots_visible() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend, close_grace=0.1)
    gate = asyncio.Event()
    backend.spawn_gate = gate
    task = asyncio.ensure_future(launcher.launch(FAKE_ARGV, {"K": "v"}))
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(launcher.owned_fds) == 6  # all fds owned during allocation
    assert launcher.owned_process is None  # not published yet
    session = None
    try:
        gate.set()
        session = await task
        assert len(launcher.owned_fds) == 3  # child sides already force-closed
        pipes = backend.pipes
        assert backend.adopt_log == [pipes[0].write_fd, pipes[1].read_fd, pipes[2].read_fd]
        backend.child_exit(11)
    finally:
        gate.set()
        if session is not None:
            await session.aclose()
        else:
            await launcher.aclose()
        await settle(launcher)
    assert session is not None and session.returncode == 11
    assert_drained(backend, launcher)


def test_no_autouse_native_pipe_patch_witness() -> None:
    assert os.pipe2 is _REAL_PIPE2
    assert os.close is _REAL_CLOSE


def test_native_backend_source_shape_ast_only_not_invoked() -> None:
    source = textwrap.dedent(inspect.getsource(NativeBackend))
    tree = ast.parse(source)
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.ClassDef)
    methods = {
        node.name
        for node in tree.body[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {"pipe2", "close_fd", "adopt", "spawn"}
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    for forbidden in (
        "PIPE",
        "communicate",
        "capture_output",
        "StreamReader",
        "StreamWriter",
        "shell",
        "popen",
        "system",
        "environ",
        "getenv",
        "devnull",
        "subprocess",
        "socket",
        "open",
    ):
        assert forbidden not in names, forbidden
    for required in (
        "os.pipe2(os.O_CLOEXEC)",
        "os.close(fd)",
        "AsyncFD(fd)",
        "asyncio.create_subprocess_exec",
        "stdin=stdin_fd",
        "stdout=stdout_fd",
        "stderr=stderr_fd",
        "env=env",
        "close_fds=True",
        "start_new_session=True",
    ):
        assert required in source, required
    module_source = inspect.getsource(RawLauncher)
    assert "create_subprocess_exec" not in module_source
    # Export surface and ceilings (proofs of the fixed pilot contract).
    assert issubclass(ProcessLaunchFailure, RuntimeError)
    assert issubclass(ProcessCleanupFailure, RuntimeError)
    assert MAX_LAUNCH_SECONDS == 45.0
    assert MAX_CLOSE_GRACE_SECONDS == 2.0
    assert MAX_ARGV_ITEMS == 256 and MAX_ARGV_BYTES == 128 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_pair", [1, 2, 3])
async def test_clean_allocation_failure_releases_slot(failed_pair: int) -> None:
    backend = FakeBackend()
    backend.pipe2_fail_on_call = failed_pair
    owner = RawLauncher(backend)
    with pytest.raises(ProcessLaunchFailure):
        await owner.launch(FAKE_ARGV, {})
    assert_drained(backend, owner)
    backend.pipe2_fail_on_call = None
    session = await owner.launch(FAKE_ARGV, {})
    backend.child_exit(0)
    await session.aclose()
    assert_drained(backend, owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [1, 2, 4])
async def test_adopted_close_failure_retained_others_closed_child_killed(offset: int) -> None:
    backend = FakeBackend()
    owner = RawLauncher(backend, close_grace=0.02)
    session = await owner.launch(FAKE_ARGV, {})
    failed = 50_000 + offset
    backend.close_fail_fds = frozenset({failed})
    try:
        owner.close()
        assert backend.process is not None and backend.process.killed
        assert backend.live_fds() == {failed}
        assert owner.owned_fds == (failed,) and owner.cleanup_failed
        backend.child_exit(0)
        with pytest.raises(ProcessCleanupFailure):
            await session.aclose()
        with pytest.raises(ProcessCleanupFailure):
            await owner.launch(FAKE_ARGV, {})
        # Even if close now would succeed, unknown descriptors are never retried.
        backend.close_fail_fds = frozenset()
        before = list(backend.close_log)
        with pytest.raises(ProcessCleanupFailure):
            await owner.aclose()
        assert backend.close_log == before and backend.live_fds() == {failed}
        assert owner.owned_fds == (failed,) and not owner.pending_tasks
    finally:
        backend.close_fail_fds = frozenset()
        backend.child_exit(0)
        await settle(owner)
        for fd in backend.live_fds():
            backend.close_fd(fd)  # Explicit fake backstop, not launcher retry.


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, 3, 5])
async def test_child_end_close_failure_still_closes_parents_and_kills(offset: int) -> None:
    backend = FakeBackend()
    failed = 50_000 + offset
    backend.close_fail_fds = frozenset({failed})
    owner = RawLauncher(backend)
    try:
        with pytest.raises(ProcessCleanupFailure):
            await owner.launch(FAKE_ARGV, {})
        assert backend.process is not None and backend.process.killed
        assert backend.live_fds() == {failed} and owner.owned_fds == (failed,)
        backend.child_exit(0)
        with pytest.raises(ProcessCleanupFailure):
            await owner.aclose()
        assert not owner.pending_tasks
    finally:
        backend.close_fail_fds = frozenset()
        backend.child_exit(0)
        await settle(owner)
        for fd in backend.live_fds():
            backend.close_fd(fd)


@pytest.mark.asyncio
async def test_adoption_unwind_unknown_channel_close_no_raw_retry() -> None:
    backend = FakeBackend()
    backend.adopt_fail_fds = frozenset({50_002})
    backend.close_fail_fds = frozenset({50_001})
    owner = RawLauncher(backend)
    try:
        with pytest.raises(ProcessCleanupFailure):
            await owner.launch(FAKE_ARGV, {})
        assert backend.process is not None and backend.process.killed
        assert backend.live_fds() == {50_001} and owner.owned_fds == (50_001,)
        backend.child_exit(0)
        with pytest.raises(ProcessCleanupFailure):
            await owner.aclose()
    finally:
        backend.close_fail_fds = frozenset()
        backend.child_exit(0)
        await settle(owner)
        for fd in backend.live_fds():
            backend.close_fd(fd)


@pytest.mark.asyncio
async def test_close_error_after_release_does_not_close_reused_virtual_fd() -> None:
    class ReusedBackend(FakeBackend):
        attempted = 0
        foreign_live = False

        def close_fd(self, fd: int) -> None:
            if fd == 50_004:
                self.attempted += 1
                if self.foreign_live:
                    self.foreign_live = False
                    raise AssertionError("closed unrelated reused descriptor")
                super().close_fd(fd)
                self.foreign_live = True
                raise OSError("close returned uncertain after releasing descriptor")
            super().close_fd(fd)

    backend = ReusedBackend()
    owner = RawLauncher(backend)
    session = await owner.launch(FAKE_ARGV, {})
    owner.close()
    backend.child_exit(0)
    for _ in range(2):
        with pytest.raises(ProcessCleanupFailure):
            await session.aclose()
    assert backend.foreign_live and backend.attempted == 1
    assert owner.owned_fds == (50_004,) and owner.cleanup_failed
    assert not owner.pending_tasks
    backend.foreign_live = False  # Test-owned virtual unrelated-resource recovery.


@pytest.mark.asyncio
async def test_env_subclass_rejected_before_callbacks_or_allocation() -> None:
    class Hostile(dict):
        def items(self):
            raise AssertionError("subclass callback executed")

    backend = FakeBackend()
    owner = RawLauncher(backend)
    with pytest.raises(ValueError):
        await owner.launch(FAKE_ARGV, Hostile())
    assert backend.pipe2_calls == 0


@pytest.mark.asyncio
async def test_released_session_cannot_hide_fatal_kill_failure() -> None:
    backend = FakeBackend()
    owner = RawLauncher(backend)
    session = await owner.launch(FAKE_ARGV, {})
    backend.fail_kill = True
    session.kill()
    backend.child_exit(0)
    for _ in range(2):
        with pytest.raises(ProcessCleanupFailure):
            await session.aclose()
    assert_drained(backend, owner)
    assert owner.cleanup_failed


@pytest.mark.asyncio
async def test_launch_timeout_preserves_fatal_latch_after_late_child_exit() -> None:
    stopped = asyncio.Event()

    class ObservedLauncher(RawLauncher):
        def _stop_job(self, job):
            super()._stop_job(job)
            stopped.set()

    backend = FakeBackend()
    backend.spawn_gate = asyncio.Event()
    backend.fail_kill = True
    owner = ObservedLauncher(backend, launch_deadline=0.01, close_grace=0.2)
    publication = backend.spawn_gate

    async def publish_then_exit() -> None:
        await stopped.wait()  # Publish only after the actual deadline stop.
        publication.set()
        while backend.process is None:
            await asyncio.sleep(0)
        # The factory's publication and failed kill contain no intervening await.
        assert owner.cleanup_failed
        backend.child_exit(0)

    feeder = asyncio.create_task(publish_then_exit())
    try:
        with pytest.raises(ProcessCleanupFailure):
            await owner.launch(FAKE_ARGV, {})
        await feeder
        assert_drained(backend, owner)
        assert owner.cleanup_failed
        with pytest.raises(ProcessCleanupFailure):
            await owner.launch(FAKE_ARGV, {})
        with pytest.raises(ProcessCleanupFailure):
            await owner.aclose()
    finally:
        stopped.set()
        publication.set()
        await asyncio.wait_for(feeder, 1)
        if backend.process is not None:
            backend.child_exit(0)
        await settle(owner)


@pytest.mark.asyncio
async def test_session_wait_returns_exit_without_kill_and_stdio_stays_owned() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    session = await launcher.launch(FAKE_ARGV, {})
    try:
        waiter = asyncio.ensure_future(session.wait())
        await asyncio.sleep(0)
        assert not waiter.done()
        backend.child_exit(4)
        assert await asyncio.wait_for(waiter, 1) == 4
        assert await session.wait() == 4  # Repeated wait after reap is stable.
        process = backend.process
        assert process is not None and not process.killed
        assert len(launcher.owned_fds) == 3  # Waiting never closes stdio.
    finally:
        await session.aclose()
        await settle(launcher)
    assert session.returncode == 4
    assert not launcher.cleanup_failed
    assert_drained(backend, launcher)


@pytest.mark.asyncio
async def test_session_wait_caller_cancel_never_cancels_owned_reap() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend)
    session = await launcher.launch(FAKE_ARGV, {})
    try:
        waiter = asyncio.ensure_future(session.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert launcher.pending_tasks != ()  # Reap retained by the owner.
        backend.child_exit(0)
        await settle(launcher)
        assert await session.wait() == 0
    finally:
        await session.aclose()
        await settle(launcher)
    assert not launcher.cleanup_failed
    assert_drained(backend, launcher)


@pytest.mark.asyncio
async def test_session_wait_exit_mismatch_latches_fatal() -> None:
    backend = FakeBackend()
    launcher = RawLauncher(backend, close_grace=0.1)
    session = await launcher.launch(FAKE_ARGV, {})
    process = backend.process
    assert process is not None
    process.report_override = 7
    process.returncode = 1
    process.exited.set()
    try:
        with pytest.raises(ProcessCleanupFailure):
            await session.wait()
        assert launcher.cleanup_failed is True
        with pytest.raises(ProcessCleanupFailure):
            await session.wait()
    finally:
        process.report_override = None
        with pytest.raises(ProcessCleanupFailure):
            await session.aclose()
    assert launcher.owned_process is not None  # Retained visible, never reset.
