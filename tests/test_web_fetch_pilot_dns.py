"""Fake-only DNS ownership tests: NO process, socket, DNS or container execution."""

from __future__ import annotations

import ast
import asyncio
import json
from collections import deque
from collections.abc import Callable

import pytest

from scripts.web_fetch_pilot_dns import (
    CLEANUP_OUTPUT_LIMIT,
    HELPER_SOURCE,
    MAX_OUTPUT,
    DNSCleanupFailure,
    DNSFailure,
    DNSResolver,
    StdlibSpawner,
)

HOST = "example.com"
GOOD = b'{"status":"ok","addresses":["8.8.8.8"]}'


class FakeReader:
    def __init__(self, data: bytes = GOOD, *, blocked: bool = False, forever: bool = False):
        self.chunks = deque([data])
        self.ready = asyncio.Event()
        if not blocked:
            self.ready.set()
        self.started = asyncio.Event()
        self.sizes: list[int] = []
        self.forever = forever
        self.cancelled = False

    async def read(self, size: int) -> bytes:
        self.started.set()
        self.sizes.append(size)
        try:
            await self.ready.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.forever:
            return b"x" * size
        if not self.chunks:
            return b""
        chunk = self.chunks.popleft()
        if len(chunk) > size:
            self.chunks.appendleft(chunk[size:])
        return chunk[:size]


class FakeProcess:
    def __init__(
        self,
        reader: FakeReader | None = None,
        *,
        exitcode: int = 0,
        blocked_exit: bool = False,
        wedged: bool = False,
    ):
        self.stdout = reader if reader is not None else FakeReader()
        self.returncode: int | None = None
        self.exitcode = exitcode
        self.exit = asyncio.Event()
        if not blocked_exit:
            self.exit.set()
        self.wait_started = asyncio.Event()
        self.wait_calls = 0
        self.wait_cancelled = False
        self.kill_calls = 0
        self.wedged = wedged
        self.kill_error: Exception | None = None
        self.wait_error: Exception | None = None

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_error is not None:
            raise self.kill_error
        self.returncode = -9
        self.stdout.ready.set()
        if not self.wedged:
            self.exit.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        self.wait_started.set()
        try:
            await self.exit.wait()
        except asyncio.CancelledError:
            self.wait_cancelled = True
            raise
        if self.wait_error is not None:
            raise self.wait_error
        if self.returncode is None:
            self.returncode = self.exitcode
        return self.returncode


class FakeFactory:
    def __init__(self, make: Callable[[], FakeProcess] = FakeProcess, *, blocked: bool = False):
        self.make = make
        self.hosts: list[str] = []
        self.processes: list[FakeProcess] = []
        self.started = asyncio.Event()
        self.ready = asyncio.Event()
        if not blocked:
            self.ready.set()
        self.cancelled = False
        self.error: Exception | None = None

    async def __call__(self, host: str) -> FakeProcess:
        self.hosts.append(host)
        self.started.set()
        try:
            await self.ready.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        process = self.make()
        self.processes.append(process)
        return process


async def ticks() -> None:
    for _ in range(12):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def prohibit_native_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("real subprocess execution prohibited")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)


@pytest.mark.asyncio
async def test_native_factory_fixed_source_options_no_execution(monkeypatch: pytest.MonkeyPatch):
    captured: list[tuple[tuple[object, ...], dict[str, object]]] = []
    process = FakeProcess()

    async def capture(*args: object, **kwargs: object) -> FakeProcess:
        captured.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    factory = StdlibSpawner("/qualified/python")
    assert captured == []
    resolver = DNSResolver(factory)
    assert await resolver(HOST) == ("8.8.8.8",)
    assert captured == [
        (
            ("/qualified/python", "-I", "-S", "-c", HELPER_SOURCE, HOST),
            {
                "stdin": asyncio.subprocess.DEVNULL,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.DEVNULL,
                "env": {},
                "close_fds": True,
                "start_new_session": True,
                "limit": 4096,
            },
        )
    ]
    # Parse trusted literal, NEVER execute it, including its DNS call.
    tree = ast.parse(HELPER_SOURCE)
    imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert imports == ["socket", "json", "sys"]
    lookup = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getaddrinfo"
    )
    assert [ast.unparse(arg) for arg in lookup.args] == ["sys.argv[1]", "443"]
    assert {kw.arg: ast.unparse(kw.value) for kw in lookup.keywords} == {
        "family": "socket.AF_INET",
        "type": "socket.SOCK_STREAM",
        "proto": "socket.IPPROTO_TCP",
    }
    assert "len(rows) <= 8" in HELPER_SOURCE
    assert "except Exception:" in HELPER_SOURCE and "sys.exit(1)" in HELPER_SOURCE
    assert "print(" not in HELPER_SOURCE
    await resolver.aclose()


@pytest.mark.parametrize("path", ["python", "", "relative/python", "/bad\x00path", 1])
def test_interpreter_must_be_absolute(path: object):
    with pytest.raises(ValueError):
        StdlibSpawner(path)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "EXAMPLE.com",
        "example.com.",
        "127.0.0.1",
        "::1",
        "a\nb.com",
        "--x.com",
        "x;echo.com",
        "example.com/",
        1,
    ],
)
async def test_host_rejected_before_factory(host: object):
    factory = FakeFactory()
    resolver = DNSResolver(factory)
    with pytest.raises(ValueError):
        await resolver(host)  # type: ignore[arg-type]
    assert factory.hosts == [] and resolver.active_jobs == 0


@pytest.mark.asyncio
async def test_host_subclass_not_accepted():
    class Host(str):
        pass

    resolver = DNSResolver(FakeFactory())
    with pytest.raises(ValueError):
        await resolver(Host(HOST))


@pytest.mark.asyncio
async def test_partial_stdout_eof_and_exit_required_exact_eight():
    addresses = [f"8.8.8.{i}" for i in range(1, 9)]
    raw = json.dumps({"status": "ok", "addresses": addresses}).encode()
    reader = FakeReader()
    reader.chunks = deque(raw[i : i + 3] for i in range(0, len(raw), 3))
    process = FakeProcess(reader, blocked_exit=True)
    resolver = DNSResolver(FakeFactory(lambda: process))
    query = asyncio.create_task(resolver(HOST))
    await process.wait_started.wait()
    assert not query.done() and not reader.chunks
    process.exit.set()
    assert await query == tuple(addresses)
    assert process.kill_calls == 0 and process.wait_calls == 1
    await ticks()
    assert resolver.pending_tasks == () and resolver.active_jobs == 0
    await resolver.aclose()


@pytest.mark.asyncio
async def test_valid_json_without_eof_not_accepted():
    class NoEOF(FakeReader):
        async def read(self, size: int) -> bytes:
            if self.chunks:
                chunk = await super().read(size)
                self.ready.clear()
                return chunk
            self.started.set()
            await self.ready.wait()  # Fake kill closes the child's write side.
            return b""

    process = FakeProcess(NoEOF())
    resolver = DNSResolver(FakeFactory(lambda: process), deadline=0.01)
    with pytest.raises(DNSFailure):
        await resolver(HOST)
    assert process.kill_calls == 1 and process.wait_calls == 1
    await resolver.aclose()


BAD_OUTPUTS = [
    b"",
    b"not JSON",
    GOOD + b"{}",
    b"\xff",
    b'{"status":"ok","status":"ok","addresses":["8.8.8.8"]}',
    b'{"status":"ok","addresses":[NaN]}',
    b'{"status":"ok","addresses":[Infinity]}',
    b'{"status":"ok","addresses":["008.8.8.8"]}',
    b'{"status":"ok","addresses":["::1"]}',
    b'{"status":"ok","addresses":["8.8.8.8","::1"]}',
    b'{"status":"ok","addresses":[8]}',
    b'{"status":"ok","addresses":[true]}',
    b'{"status":"ok","addresses":null}',
    b'{"status":"ok","addresses":[]}',
    b'{"status":"ok","addresses":["8.8.8.8","8.8.8.8"]}',
    b'{"status":"error","addresses":["8.8.8.8"]}',
    b'{"status":"ok","addresses":["8.8.8.8"],"extra":1}',
    b"[]",
    b"null",
    b"true",
    json.dumps({"status": "ok", "addresses": [f"8.8.8.{i}" for i in range(9)]}).encode(),
    b"[" * 2000 + b"]" * 2000,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", BAD_OUTPUTS)
async def test_strict_output_ordinary_refusal_only_after_reap(raw: bytes):
    process = FakeProcess(FakeReader(raw))
    resolver = DNSResolver(FakeFactory(lambda: process))
    with pytest.raises(DNSFailure, match="DNS unavailable") as failure:
        await resolver(HOST)
    assert failure.value.__cause__ is None
    assert process.wait_calls == 1 and process.returncode == 0
    assert not resolver.cleanup_failed
    await resolver.aclose()


@pytest.mark.asyncio
async def test_private_candidates_remain_gateway_authoritative_not_truncated():
    raw = b'{"status":"ok","addresses":["8.8.8.8","10.0.0.1"]}'
    resolver = DNSResolver(FakeFactory(lambda: FakeProcess(FakeReader(raw))))
    answers = await resolver(HOST)
    assert answers == ("8.8.8.8", "10.0.0.1")
    from scripts.web_fetch_pilot_core import (
        DestinationPolicyError,
        parse_owned_inventory,
        validate_dns_answers,
    )

    with pytest.raises(DestinationPolicyError):
        validate_dns_answers(answers, parse_owned_inventory(("203.0.113.4",)))
    await resolver.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("forever", [False, True])
async def test_output_overflow_stops_reads_kills_reaps(forever: bool):
    reader = FakeReader(b"x" * (MAX_OUTPUT + 10), forever=forever)
    process = FakeProcess(reader)
    resolver = DNSResolver(FakeFactory(lambda: process))
    with pytest.raises(DNSCleanupFailure if forever else DNSFailure):
        await resolver(HOST)
    assert reader.sizes[:5] == [1024, 1024, 1024, 1024, 1]
    assert process.kill_calls == 1
    if forever:
        assert sum(reader.sizes[5:]) == CLEANUP_OUTPUT_LIMIT + 1
        assert resolver.cleanup_failed and resolver.owned_processes == (process,)
        with pytest.raises(DNSCleanupFailure):
            await resolver.aclose()
    else:
        assert process.wait_calls == 1 and not resolver.cleanup_failed
        await resolver.aclose()


@pytest.mark.asyncio
async def test_nonzero_exit_generic_failure_then_reusable():
    factory = FakeFactory(lambda: FakeProcess(exitcode=1))
    resolver = DNSResolver(factory)
    with pytest.raises(DNSFailure):
        await resolver(HOST)
    assert not resolver.cleanup_failed
    factory.make = FakeProcess
    assert await resolver(HOST) == ("8.8.8.8",)
    await resolver.aclose()


@pytest.mark.asyncio
async def test_factory_failure_is_retrieved_refusal_and_reusable():
    factory = FakeFactory()
    factory.error = OSError("private DNS diagnostics")
    resolver = DNSResolver(factory)
    with pytest.raises(DNSFailure, match="^DNS unavailable$"):
        await resolver(HOST)
    factory.error = None
    assert await resolver(HOST) == ("8.8.8.8",)
    await resolver.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["spawn", "read", "wait"])
async def test_cancellation_in_each_phase_kills_and_reaps(phase: str):
    process = FakeProcess(FakeReader(blocked=phase == "read"), blocked_exit=phase == "wait")
    factory = FakeFactory(lambda: process, blocked=phase == "spawn")
    resolver = DNSResolver(factory, cleanup_grace=0.1)
    query = asyncio.create_task(resolver(HOST))
    await factory.started.wait()
    if phase == "read":
        await process.stdout.started.wait()
    elif phase == "wait":
        await process.wait_started.wait()
    query.cancel()
    await ticks()
    if phase == "spawn":
        assert not query.done() and not factory.cancelled
        factory.ready.set()
    with pytest.raises(asyncio.CancelledError):
        await query
    assert process.kill_calls == 1 and process.returncode == -9
    assert process.wait_calls >= 1 and not factory.cancelled
    assert process.wait_cancelled == (phase == "wait")
    await resolver.aclose()


@pytest.mark.asyncio
async def test_cancel_before_run_first_instruction_still_owns_spawn_cleanup():
    factory = FakeFactory(blocked=True)
    resolver = DNSResolver(factory, cleanup_grace=0.1)
    query = asyncio.create_task(resolver(HOST))
    # __call__ schedules spawn/run; this continuation cancels before they run.
    await asyncio.sleep(0)
    resolver.close()  # Synchronous stop before either newly queued task runs.
    query.cancel()
    factory.ready.set()
    with pytest.raises(asyncio.CancelledError):
        await query
    assert len(factory.processes) == 1
    assert factory.processes[0].kill_calls == 1
    await resolver.aclose()


@pytest.mark.asyncio
async def test_wedged_spawn_bounded_fatal_late_publication_reclaimed():
    factory = FakeFactory(blocked=True)
    resolver = DNSResolver(factory, deadline=0.01, cleanup_grace=0.02)
    start = asyncio.get_running_loop().time()
    with pytest.raises(DNSCleanupFailure) as failure:
        await resolver(HOST)
    assert asyncio.get_running_loop().time() - start < 0.3
    assert not isinstance(failure.value, (OSError, TimeoutError))
    assert resolver.cleanup_failed and resolver.active_jobs == 1 and resolver.pending_tasks
    assert resolver.owned_processes == () and not factory.cancelled
    with pytest.raises(DNSCleanupFailure):
        await resolver(HOST)
    assert len(factory.hosts) == 1
    factory.ready.set()  # Publication AFTER the caller already returned fatal.
    await ticks()
    assert factory.processes[0].kill_calls == 1
    assert factory.processes[0].wait_calls == 1
    assert resolver.pending_tasks == () and resolver.owned_processes == ()
    assert resolver.active_jobs == 0 and resolver.cleanup_failed
    with pytest.raises(DNSCleanupFailure):
        await resolver.aclose()


@pytest.mark.asyncio
async def test_cleanup_eof_required_even_when_child_already_exited():
    class HeldEOF(FakeReader):
        async def read(self, size: int) -> bytes:
            if self.chunks:
                return await super().read(size)
            await self.ready.wait()
            return b""

    reader = HeldEOF(b"x" * (MAX_OUTPUT + 1))
    original_read = reader.read

    async def paused_read(size: int) -> bytes:
        value = await original_read(size)
        if not reader.chunks:
            reader.ready.clear()
        return value

    reader.read = paused_read
    process = FakeProcess(reader)
    process.returncode = 0  # No kill; EOF is still separately pending.
    resolver = DNSResolver(FakeFactory(lambda: process), deadline=0.01, cleanup_grace=0.01)
    try:
        with pytest.raises(DNSCleanupFailure):
            await resolver(HOST)
        assert resolver.cleanup_failed and resolver.owned_processes == (process,)
        assert resolver.active_jobs == 1 and resolver.pending_tasks
    finally:
        reader.read = original_read
        reader.ready.set()
        await ticks()
    assert not resolver.pending_tasks and not resolver.owned_processes


@pytest.mark.asyncio
async def test_late_spawn_error_consumed_without_orphan_warning():
    errors: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    old = loop.get_exception_handler()
    loop.set_exception_handler(lambda _, context: errors.append(context))
    try:
        factory = FakeFactory(blocked=True)
        resolver = DNSResolver(factory, deadline=0.01, cleanup_grace=0.01)
        with pytest.raises(DNSCleanupFailure):
            await resolver(HOST)
        factory.error = OSError("late error")
        factory.ready.set()
        await ticks()
        assert resolver.pending_tasks == () and resolver.active_jobs == 0
        assert errors == []
    finally:
        loop.set_exception_handler(old)


@pytest.mark.asyncio
async def test_four_slots_include_cleanup_no_waiting_followers():
    processes: list[FakeProcess] = []

    def make() -> FakeProcess:
        process = FakeProcess(FakeReader(blocked=True), blocked_exit=True, wedged=True)
        processes.append(process)
        return process

    factory = FakeFactory(make)
    resolver = DNSResolver(factory, cleanup_grace=0.2)
    queries = [asyncio.create_task(resolver(HOST)) for _ in range(4)]
    await ticks()
    assert len(processes) == resolver.active_jobs == 4
    queries[0].cancel()
    await ticks()
    assert processes[0].kill_calls == 1 and resolver.active_jobs == 4
    with pytest.raises(DNSFailure, match="capacity"):
        await resolver(HOST)
    assert len(factory.hosts) == 4
    processes[0].exit.set()
    with pytest.raises(asyncio.CancelledError):
        await queries[0]
    await ticks()
    assert resolver.active_jobs == 3
    closing = asyncio.create_task(resolver.aclose())
    await ticks()
    for process in processes[1:]:
        process.exit.set()
    await closing
    await asyncio.gather(*queries[1:], return_exceptions=True)
    assert resolver.active_jobs == 0 and resolver.pending_tasks == ()


@pytest.mark.asyncio
async def test_wedged_wait_retains_handle_and_disables_new_helpers():
    process = FakeProcess(FakeReader(blocked=True), blocked_exit=True, wedged=True)
    factory = FakeFactory(lambda: process)
    resolver = DNSResolver(factory, deadline=0.01, cleanup_grace=0.02)
    with pytest.raises(DNSCleanupFailure):
        await resolver(HOST)
    assert resolver.cleanup_failed and resolver.owned_processes == (process,)
    assert resolver.active_jobs == 1 and resolver.pending_tasks
    with pytest.raises(DNSCleanupFailure):
        await resolver(HOST)
    assert len(factory.hosts) == 1
    process.exit.set()
    await ticks()
    assert resolver.active_jobs == 0 and resolver.owned_processes == ()


@pytest.mark.asyncio
async def test_repeated_caller_cancel_is_fatal_retains_then_reaps():
    factory = FakeFactory(blocked=True)
    resolver = DNSResolver(factory)
    query = asyncio.create_task(resolver(HOST))
    await factory.started.wait()
    query.cancel()
    await ticks()
    query.cancel()
    with pytest.raises(DNSCleanupFailure):
        await query
    assert resolver.cleanup_failed and resolver.pending_tasks
    factory.ready.set()
    await ticks()
    assert factory.processes[0].kill_calls == 1 and resolver.active_jobs == 0


@pytest.mark.asyncio
async def test_aclose_cancel_retains_late_child_and_closing_prevents_jobs():
    factory = FakeFactory(blocked=True)
    resolver = DNSResolver(factory)
    query = asyncio.create_task(resolver(HOST))
    await factory.started.wait()
    closing = asyncio.create_task(resolver.aclose())
    await ticks()
    with pytest.raises(DNSCleanupFailure):
        await resolver(HOST)
    closing.cancel()
    with pytest.raises(DNSCleanupFailure):
        await closing
    factory.ready.set()
    await ticks()
    await asyncio.gather(query, return_exceptions=True)
    assert factory.processes[0].kill_calls == 1 and resolver.active_jobs == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["kill", "wait"])
async def test_cleanup_syscall_failure_latches_fatal(operation: str):
    process = FakeProcess(FakeReader(blocked=True))
    if operation == "kill":
        process.kill_error = OSError("kill failed")
    else:
        process.wait_error = OSError("wait failed")
    resolver = DNSResolver(FakeFactory(lambda: process), deadline=0.01, cleanup_grace=0.02)
    with pytest.raises(DNSCleanupFailure):
        await resolver(HOST)
    assert resolver.cleanup_failed
    if operation == "wait":
        assert resolver.owned_processes == (process,) and resolver.active_jobs == 1
    with pytest.raises(DNSCleanupFailure):
        await resolver(HOST)


@pytest.mark.asyncio
async def test_fatal_resolver_escapes_gateway_ordinary_refusal_catch():
    from scripts.web_fetch_pilot_gateway import Gateway

    class Frames:
        async def receive(self):
            raise AssertionError("unused")

        async def send(self, *args):
            raise AssertionError("must not emit ordinary OPEN_ERROR")

        def close(self):
            pass

    async def connector(*args):
        raise AssertionError("must not dial")

    factory = FakeFactory(blocked=True)
    resolver = DNSResolver(factory, deadline=0.01, cleanup_grace=0.01)
    gateway = Gateway((HOST,), ("203.0.113.4",), Frames(), resolver, connector)
    stream = gateway._new_stream(1)
    with pytest.raises(DNSCleanupFailure):
        await gateway._admit(stream, HOST)
    factory.ready.set()
    await ticks()
    assert resolver.pending_tasks == ()


@pytest.mark.parametrize(
    "options", [{"deadline": 3.1}, {"cleanup_grace": 2.1}, {"deadline": 0}, {"cleanup_grace": 0}]
)
def test_fixed_deadline_and_cleanup_ceilings(options: dict[str, float]):
    with pytest.raises(ValueError):
        DNSResolver(FakeFactory(), **options)
