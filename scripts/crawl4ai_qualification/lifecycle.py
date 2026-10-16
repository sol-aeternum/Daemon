"""Single-allocation lifecycle against an injected, trusted bounded transport.

No native backend exists here. The transport must target the independently
reviewed local daemon, bound parsing/output and never equate command failure with
absence. These mocks/contracts do NOT establish native enforcement/compatibility.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from .config import ConfigSnapshot, require, require_config_archive, stock_config
from .policy import Specification, created, identity

T = TypeVar("T")
MAX_SECONDS = 30.0


class Transport(Protocol):
    """No ambient credentials, network/daemon selection or subprocess API.

    None from inspect means AUTHORITATIVE absence, never HTTP/CLI failure.
    find returns complete exact-name results up to two; two is refused as
    ambiguous, larger/unbounded results raise. Errors raise, never mean absence.
    All create/start/remove acknowledgements may have uncertain effects.
    Implementations must retain operation provenance and limit wire output BEFORE
    parsing. Returned records describe one daemon; its identity must be stable.
    """

    async def daemon_identity(self) -> str: ...
    async def create(self, spec: Specification, name: str, run: str, path: str) -> str: ...
    async def find(self, name: str) -> tuple[str, ...]: ...
    async def inspect(self, identifier: str) -> dict | None: ...
    async def read_config(self, identifier: str) -> bytes: ...
    async def start(self, identifier: str) -> None: ...
    async def remove(self, identifier: str) -> None: ...


class Allocation:
    """One-shot run. Public cancellation requests teardown, not remote cancellation.

    Operations are driver-owned and shielded from caller cancellation. A timed-out
    transport operation stays retained; cleanup cannot race its unknown effects.
    A transport that ignores cancellation is NOT forcibly terminated. No automatic
    retry/allocation is allowed, even after successful cleanup.
    """

    def __init__(
        self,
        transport: Transport,
        spec: Specification,
        parent: str,
        config: bytes,
        *,
        seconds: float = MAX_SECONDS,
    ) -> None:
        require(type(seconds) in (int, float) and 0 < seconds <= MAX_SECONDS)
        self.transport, self.spec = transport, spec
        self.snapshot = ConfigSnapshot(parent, stock_config(config))
        self.seconds = seconds
        self.name = "daemon-c4ai-" + secrets.token_hex(12)
        self.run = secrets.token_hex(16)
        self.identifier: str | None = None
        self.started = False  # Start acknowledgement only, not job/exit/termination evidence.
        self.filesystem_verified = False
        self.absence_verified = False
        self.cleanup_failed = False
        self._cancel = False
        self._created_attempted = False
        self._setup: asyncio.Task[None] | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._operations: set[asyncio.Task] = set()

    @property
    def pending(self) -> tuple[asyncio.Task, ...]:
        return tuple(
            t
            for t in (*self._operations, self._setup, self._cleanup)
            if t is not None and not t.done()
        )

    async def _bounded(self, make: Callable[[], Awaitable[T]]) -> T:
        async def invoke() -> T:
            return await make()

        task = asyncio.create_task(invoke())
        self._operations.add(task)
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        # asyncio.wait does not wait indefinitely for a cancellation-resistant
        # coroutine like wait_for can. Keep timed-out operations owned/observable.
        done, _ = await asyncio.wait({task}, timeout=self.seconds)
        require(bool(done))
        self._operations.remove(task)
        return task.result()

    async def _call(self, make: Callable[[], Awaitable[T]]) -> T:
        daemon = await self._bounded(self.transport.daemon_identity)
        require(type(daemon) is str and daemon == self.spec.daemon_id)
        return await self._bounded(make)

    def _checkpoint(self) -> None:
        if self._cancel:
            raise asyncio.CancelledError

    async def start(self) -> None:
        require(self._setup is None and not self._cancel)
        self._setup = asyncio.create_task(self._prepare())
        try:
            await asyncio.shield(self._setup)
        except asyncio.CancelledError:
            self._cancel = True
            self._begin_cleanup()
            raise
        except Exception:
            await asyncio.shield(self._begin_cleanup())
            raise

    async def _prepare(self) -> None:
        await self._call(self._empty_name)
        self._checkpoint()
        self.snapshot.create()
        self._created_attempted = True
        identifier = await self._call(
            lambda: self.transport.create(self.spec, self.name, self.run, self.snapshot.path)
        )
        # Validate full ID independently before retaining it as removal authority.
        require(
            type(identifier) is str
            and len(identifier) == 64
            and all(c in "0123456789abcdef" for c in identifier)
        )
        self.identifier = identifier
        self._checkpoint()
        await self._preflight()
        archive = await self._call(lambda: self.transport.read_config(identifier))
        require_config_archive(archive, self.snapshot.content)
        await self._preflight()
        self.snapshot.verify()
        self._checkpoint()
        self.filesystem_verified = True

        async def start_verified() -> None:
            self.snapshot.verify()
            self._checkpoint()
            await self.transport.start(identifier)

        await self._call(start_verified)
        self.started = True

    async def _empty_name(self) -> None:
        matches = await self.transport.find(self.name)
        require(type(matches) is tuple and not matches)

    async def _preflight(self) -> None:
        assert self.identifier is not None
        identifier = self.identifier
        record = await self._call(lambda: self.transport.inspect(identifier))
        created(record, self.spec, self.name, self.run, identifier, self.snapshot.path)
        self.snapshot.verify()
        self._checkpoint()

    def _begin_cleanup(self) -> asyncio.Task[None]:
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._teardown())
            # Retrieve an asynchronous failure even if cancelled caller never
            # returns; task/result and cleanup_failed remain observable to owner.
            self._cleanup.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return self._cleanup

    async def close(self) -> None:
        self._cancel = True
        await asyncio.shield(self._begin_cleanup())

    async def _teardown(self) -> None:
        try:
            if self._setup is not None:
                try:
                    await asyncio.shield(self._setup)
                except (Exception, asyncio.CancelledError):
                    pass
            # Never race a timed-out create/start/read/remove. Completed late
            # results remain recorded but cannot manufacture command success.
            for task in tuple(self._operations):
                require(task.done())
                if not task.cancelled():
                    task.exception()
                self._operations.remove(task)
            if self._created_attempted:
                await self._remove()
            self.absence_verified = True
            self.snapshot.cleanup()
        except BaseException:
            self.cleanup_failed = True
            raise

    async def _remove(self) -> None:
        if self.identifier is None:
            candidates = await self._call(lambda: self.transport.find(self.name))
            require(type(candidates) is tuple and len(candidates) <= 1)
            if not candidates:
                return  # Authoritative complete exact-name lookup; no create in flight.
            candidate = candidates[0]
            record = await self._call(lambda: self.transport.inspect(candidate))
            identity(record, self.spec, self.name, self.run, candidate)
            self.identifier = candidate
        identifier = self.identifier
        record = await self._call(lambda: self.transport.inspect(identifier))
        if record is None:
            require(await self._call(lambda: self.transport.find(self.name)) == ())
            return
        identity(record, self.spec, self.name, self.run, identifier)
        await self._call(lambda: self.transport.remove(identifier))
        require(await self._call(lambda: self.transport.inspect(identifier)) is None)
        require(await self._call(lambda: self.transport.find(self.name)) == ())
