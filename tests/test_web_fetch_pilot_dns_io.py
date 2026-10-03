"""Native process fixtures: review before first execution, NO DNS/network.

The immutable fixture program is captured before any spawn. StdlibSpawner and
HELPER_SOURCE are never invoked or patched: these tests qualify DNSResolver's
ownership with native process handles, not getaddrinfo or container containment.
One owned child per case; no shell, application-file writes, sockets or browser.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import pytest

from scripts.web_fetch_pilot_dns import MAX_OUTPUT, DNSCleanupFailure, DNSFailure, DNSResolver

HOST = "fixture.invalid"  # Passed as inert argv, never resolved by fixture code.
SUCCESS = """import sys
sys.dont_write_bytecode = True
import json, os
assert sys.flags.isolated == 1 and sys.flags.no_site == 1
assert 'PILOT_FIXTURE_PARENT_MARKER' not in os.environ
assert sys.argv[1] == 'fixture.invalid'
print(json.dumps({'status': 'ok', 'addresses': ['8.8.8.8']}), end='', flush=True)
"""
SLEEPING = """import sys
sys.dont_write_bytecode = True
import time
time.sleep(10)
"""


class NativeFixtureFactory:
    """No caller-supplied program at query time; late publication stays owned."""

    def __init__(self, program: str, *, delayed: bool = False) -> None:
        self._command = (sys.executable, "-I", "-S", "-c", program, HOST)
        self.processes: list[asyncio.subprocess.Process] = []
        self.started = asyncio.Event()
        self.publish = asyncio.Event()
        if not delayed:
            self.publish.set()

    async def __call__(self, host: str) -> asyncio.subprocess.Process:
        assert host == HOST and not self.processes  # Exactly one child per fixture.
        process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
            close_fds=True,
            start_new_session=True,
            limit=MAX_OUTPUT,
        )
        self.processes.append(process)
        self.started.set()
        await self.publish.wait()
        return process


async def settled(resolver: DNSResolver) -> None:
    async with asyncio.timeout(1):
        while resolver.pending_tasks or resolver.active_jobs or resolver.owned_processes:
            await asyncio.sleep(0.001)


@asynccontextmanager
async def owned(
    program: str, *, delayed: bool = False, deadline: float = 1.0, grace: float = 0.5
) -> AsyncIterator[tuple[NativeFixtureFactory, DNSResolver, list[asyncio.Task]]]:
    """Always release publication and recover only explicitly recorded children."""
    factory = NativeFixtureFactory(program, delayed=delayed)
    resolver = DNSResolver(factory, deadline=deadline, cleanup_grace=grace)
    queries: list[asyncio.Task] = []
    try:
        yield factory, resolver, queries
    finally:
        factory.publish.set()
        # close() establishes cleanup ownership even before a run task starts.
        resolver.close()
        for query in queries:
            if not query.done():
                query.cancel()
        try:
            try:
                await resolver.aclose()
            except DNSCleanupFailure:
                # A fatal latch is expected in the late-publication case, but
                # every task/process still must settle; no permission to leak.
                pass
        finally:
            try:
                await asyncio.wait_for(asyncio.gather(*queries, return_exceptions=True), 1)
            finally:
                # Run this even if actor/query cleanup fails. Only recorded
                # handles: never pid discovery, broad signals or killpg.
                for process in factory.processes:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    await asyncio.wait_for(process.wait(), 1)
                await settled(resolver)


@pytest.mark.asyncio
async def test_real_dns_actor_native_stdout_eof_and_exit_without_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    monkeypatch.setenv("PILOT_FIXTURE_PARENT_MARKER", "synthetic-parent-only")
    async with owned(SUCCESS) as (factory, resolver, queries):
        query = asyncio.create_task(resolver(HOST))
        queries.append(query)
        assert await asyncio.wait_for(query, 2) == ("8.8.8.8",)
        assert len(factory.processes) == 1 and factory.processes[0].returncode == 0
        await settled(resolver)
        assert not resolver.cleanup_failed
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks


@pytest.mark.asyncio
async def test_real_dns_actor_deadline_kills_and_reaps_owned_child_without_dns() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    async with owned(SLEEPING, deadline=0.2) as (factory, resolver, queries):
        query = asyncio.create_task(resolver(HOST))
        queries.append(query)
        with pytest.raises(DNSFailure, match="deadline exceeded"):
            await asyncio.wait_for(query, 2)
        assert len(factory.processes) == 1 and factory.processes[0].returncode == -9
        await settled(resolver)
        assert not resolver.cleanup_failed
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks


@pytest.mark.asyncio
async def test_real_dns_actor_late_native_handle_retained_then_killed_without_dns() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    async with owned(SLEEPING, delayed=True, grace=0.05) as (factory, resolver, queries):
        query = asyncio.create_task(resolver(HOST))
        queries.append(query)
        await asyncio.wait_for(factory.started.wait(), 1)
        query.cancel()
        with pytest.raises(DNSCleanupFailure):
            await asyncio.wait_for(query, 1)
        assert resolver.cleanup_failed and resolver.pending_tasks
        assert len(factory.processes) == 1 and factory.processes[0].returncode is None
        assert resolver.owned_processes == ()  # Handle not yet published; spawn task owned.
        with pytest.raises(DNSCleanupFailure):
            await resolver(HOST)
        factory.publish.set()
        await settled(resolver)
        assert factory.processes[0].returncode == -9
        assert resolver.cleanup_failed  # The fatal latch never resets after recovery.
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks


@pytest.mark.asyncio
async def test_real_dns_overflow_reaps_and_closes_paused_stdout_without_dns() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    program = "import os, time; os.write(1, b'x' * 65536); time.sleep(10)"
    async with owned(program, delayed=True, deadline=3) as (factory, resolver, queries):
        query = asyncio.create_task(resolver(HOST))
        queries.append(query)
        await asyncio.wait_for(factory.started.wait(), 1)
        process = factory.processes[0]
        reader = process.stdout
        assert reader is not None
        try:
            async with asyncio.timeout(1):
                # Read-only fixture observation; production uses public read().
                while not getattr(reader, "_paused", False):
                    await asyncio.sleep(0.001)
            factory.publish.set()
            with pytest.raises(DNSFailure):
                await asyncio.wait_for(query, 2)
            await settled(resolver)
            assert process.returncode == -9 and reader.at_eof()
            assert not resolver.cleanup_failed
            assert set(os.listdir("/proc/self/fd")) == baseline
        finally:
            # Backstop if the regression returns: kill/retrieve before draining,
            # with independent bounds; never read concurrently with the query.
            factory.publish.set()
            resolver.close()
            if not query.done():
                query.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(query, return_exceptions=True), 1)
                if not resolver.pending_tasks:
                    async with asyncio.timeout(1):
                        discarded = 0
                        while chunk := await reader.read(1024):
                            discarded += len(chunk)
                            assert discarded <= 131072
            finally:
                if process.returncode is None:
                    process.kill()
                await asyncio.wait_for(process.wait(), 1)
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks
