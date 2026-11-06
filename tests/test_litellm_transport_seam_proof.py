"""Offline S4 feasibility proof for #681; NO production integration.

Both variants borrow one factory-built aiohttp transport. create_client delegates
all HTTPX configuration to LiteLLM, then replaces only its unstarted transport.
The first transport becomes the pool; later unused factory transports have only
a callable session factory (no sockets/session to close). A production factory
should avoid that redundant allocation, preserving the same configuration.

Source-qualified negative proof (installed aiohttp): client.py:854-876 connects
BEFORE req.send; connector.py:1309-1367,1476-1545,1593-1598 raise connector errors
only during DNS/TCP/TLS establishment. Proxy CONNECT may already have been sent,
but not the inference request. client.py:909-927 and client_reqrep.py:1603 can
raise ClientOSError AFTER send: ConnectError by itself is NEVER a certificate.
POST is not in aiohttp's persistent-connection retry set (client.py:739-741).
Redirects and additional HTTPX sends each re-enter this forwarder.

Lifetime: the application owns/explicitly closes the pool after draining calls;
borrowers never close it. One owner PER EVENT LOOP and SSL/session configuration
is required: _get_valid_client_session replaces/closes foreign-loop sessions,
so sharing across backend/worker loops could interrupt an active stream.

TLS/proxy parity: super().create_client preserves verify, cert, headers, blocked
HTTPX cookie jar, follow_redirects and trust_env defaults. The SAME default
_create_async_transport -> _create_aiohttp_transport retains SSL context/override,
connector settings, DummyCookieJar and both proxy-environment lookup paths.
No trust_env=False or omitted ssl_verify override. As in baseline, HTTPX cert
does not configure the explicit aiohttp transport (mTLS is NOT newly proved).
Real TLS success, proxy tunnelling, Python 3.11 execution and live providers are
not qualified here. Context copying and wait_for are explicitly exercised.

UnsupportedParamsError inventory on this OpenRouter chat path: utils.py:4210,
get_optional_params._check_valid_arg (unsupported non-default OpenAI params,
unless dropped/explicitly allowed). OpenrouterConfig -> OpenAIGPTConfig adds no
other such raise. utils.py:4105's function-calling branch is unreachable for
OpenRouter (explicit exclusion at :4085); :3225 transcription, :3354 image and
:3449/:3506/:3729/:3744 embeddings are not chat paths. This is a source-qualified
local-validation candidate, NOT permission to trust arbitrary exception classes
or absent transport evidence; the latter alone still charges.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import socket
import struct
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import httpx
import pytest
from litellm.exceptions import (
    APIError,
    RateLimitError,
    ServiceUnavailableError,
    UnsupportedParamsError,
)
from litellm.llms.custom_httpx.aiohttp_transport import LiteLLMAiohttpTransport
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

from tests.test_litellm_send_evidence_proof import completion, offline_only, peer

__all__ = ["offline_only"]  # Reuse the enforced loopback/.invalid network fixture.


@dataclass
class Attempt:
    not_sent: bool = False
    status: int | None = None
    error: str | None = None
    cause: str | None = None


@dataclass
class Evidence:
    attempts: list[Attempt] = field(default_factory=list)
    broken: bool = False
    break_observer: bool = False

    @property
    def charge(self) -> bool:
        return self.broken or not self.attempts or not all(a.not_sent for a in self.attempts)

    @property
    def statuses(self) -> list[int]:
        return [] if self.broken else [a.status for a in self.attempts if a.status is not None]


current: contextvars.ContextVar[Evidence | None] = contextvars.ContextVar("s4", default=None)


class Forwarder(httpx.AsyncBaseTransport):
    def __init__(self, pool: LiteLLMAiohttpTransport, bound: Evidence | None) -> None:
        self.pool = pool
        self.bound = bound
        self.closes = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        evidence = self.bound if self.bound is not None else current.get()
        attempt = Attempt()
        if evidence is not None:
            try:
                if evidence.break_observer:
                    raise RuntimeError("synthetic observer failure")
                evidence.attempts.append(attempt)
            except Exception:
                evidence.broken = True
        try:
            response = await self.pool.handle_async_request(request)
        except BaseException as exc:
            # Direct mapped cause only, never type/message of LiteLLM's wrapper.
            attempt.error = type(exc).__name__
            attempt.cause = type(exc.__cause__).__name__
            attempt.not_sent = isinstance(exc.__cause__, aiohttp.ClientConnectorError)
            raise
        attempt.status = response.status_code
        return response

    async def aclose(self) -> None:
        self.closes += 1  # Borrower close intentionally cannot destroy the pool.


class Handler(AsyncHTTPHandler):
    def __init__(
        self, pool: LiteLLMAiohttpTransport | None = None, bound: Evidence | None = None
    ) -> None:
        self.pool = pool
        self.bound = bound
        self.forwarders: list[Forwarder] = []
        super().__init__(timeout=0.2)

    def create_client(
        self,
        timeout: float | httpx.Timeout | None,
        event_hooks: Mapping[str, list[Any]] | None,
        ssl_verify: Any = None,
        shared_session: Any = None,
    ) -> httpx.AsyncClient:
        client = super().create_client(timeout, event_hooks, ssl_verify, shared_session)
        original = client._transport
        assert isinstance(original, LiteLLMAiohttpTransport)
        assert callable(original.client)  # No unowned live session is discarded.
        if self.pool is None:
            self.pool = original
        forwarder = Forwarder(self.pool, self.bound)
        self.forwarders.append(forwarder)
        client._transport = forwarder
        return client

    async def shutdown(self) -> None:
        await self.close()
        assert self.pool is not None
        await self.pool.aclose()


async def dispatch(
    base: str, stream: bool, handler: Handler, evidence: Evidence, **kwargs: Any
) -> BaseException | None:
    token = current.set(evidence)
    try:
        # Explicit copied-context child, followed by completion's wait_for child.
        task = asyncio.create_task(
            completion(base, stream, handler, **kwargs), context=contextvars.copy_context()
        )
        return await task
    finally:
        current.reset(token)


def report(name: str, stream: bool, e: Evidence, exc: BaseException | None) -> None:
    response = getattr(exc, "response", None)
    print(
        f"S4 {name} stream={stream} exception={type(exc).__name__} "
        f"response={getattr(response, 'status_code', None)} charge={e.charge} "
        f"attempts={e.attempts}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["refused", "dns", "tls", "close", "immediate", "read_timeout"])
async def test_socket_outcomes(mode: str, stream: bool) -> None:
    h, e = Handler(), Evidence()
    try:
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            async with peer(mode) as (server, base):
                if mode == "refused":
                    base = f"http://127.0.0.1:{bound.getsockname()[1]}/v1"
                elif mode == "dns":
                    base = "http://send-evidence.invalid/v1"
                elif mode == "tls":
                    base = base.replace("http:", "https:")
                exc = await dispatch(base, stream, h, e)
                report(mode, stream, e, exc)
                assert exc is not None
                assert e.charge is (mode not in {"refused", "dns", "tls"})
                if mode in {"close", "read_timeout"}:
                    assert server.bodies
                assert e.attempts
    finally:
        await h.shutdown()


@dataclass
class Capture:
    mode: str = "ok"
    accepts: int = 0
    bodies: list[bytes] = field(default_factory=list)
    headers: list[list[bytes]] = field(default_factory=list)
    writers: list[asyncio.StreamWriter] = field(default_factory=list)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    received: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    server: asyncio.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.accepts += 1
        self.writers.append(writer)
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        try:
            while True:
                header = await reader.readuntil(b"\r\n\r\n")
                self.headers.append(header.split(b"\r\n"))
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                if self.mode in {"reset", "crux"}:
                    self.bodies.append(await reader.readexactly(min(length, 16)))
                    self.received.set()
                    if self.mode == "crux":
                        assert self.server is not None
                        self.server.close()
                    sock = writer.get_extra_info("socket")
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    writer.transport.abort()
                    return
                body = await reader.readexactly(length)
                self.bodies.append(body)
                self.received.set()
                status = int(self.mode) if self.mode.isdigit() else 200
                streaming = json.loads(body).get("stream", False)
                chunk = {
                    "id": "offline",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "openai/gpt-4.1-mini",
                    "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}],
                }
                if status != 200:
                    output = json.dumps({"error": {"message": "offline", "code": status}}).encode()
                elif streaming:
                    first = "data: " + json.dumps(chunk) + "\n\n"
                    error = 'data: {"error":{"message":"offline","code":503}}\n\n'
                    output = (
                        (first if self.mode != "error_before" else "")
                        + (
                            error
                            if self.mode.startswith("error_")
                            else ""
                            if self.mode == "active"
                            else "data: [DONE]\n\n"
                        )
                    ).encode()
                else:
                    chunk["object"] = "chat.completion"
                    chunk["choices"] = [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ]
                    output = json.dumps(chunk).encode()
                content_type = (
                    "text/event-stream" if streaming and status == 200 else "application/json"
                )
                if self.mode == "active" and streaming:
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
                    )
                    writer.write(f"{len(output):x}\r\n".encode() + output + b"\r\n")
                    await writer.drain()
                    await self.release.wait()
                    done = b"data: [DONE]\n\n"
                    writer.write(f"{len(done):x}\r\n".encode() + done + b"\r\n0\r\n\r\n")
                else:
                    writer.write(
                        f"HTTP/1.1 {status} Offline\r\nContent-Type: {content_type}\r\n"
                        f"Content-Length: {len(output)}\r\nSet-Cookie: proof=no\r\n\r\n".encode()
                        + output
                    )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.tasks.discard(task)


@asynccontextmanager
async def capture(mode: str = "ok") -> AsyncIterator[tuple[Capture, str]]:
    state = Capture(mode)
    state.server = await asyncio.start_server(state.handle, "127.0.0.1", 0)
    try:
        yield state, f"http://127.0.0.1:{state.server.sockets[0].getsockname()[1]}/v1"
    finally:
        state.release.set()
        state.server.close()
        for writer in state.writers:
            writer.close()
        await state.server.wait_closed()
        if state.tasks:
            await asyncio.gather(*state.tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 408, 422, 429, 500, 502, 503, 504])
async def test_real_status(status: int, stream: bool) -> None:
    h, e = Handler(), Evidence()
    try:
        async with capture(str(status)) as (server, base):
            exc = await dispatch(base, stream, h, e)
            report(str(status), stream, e, exc)
            assert exc is not None and server.bodies and e.charge
            assert e.statuses == [status]
    finally:
        await h.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["reset", "crux"])
async def test_reset_and_sent_retry_refused(mode: str, stream: bool) -> None:
    h, e = Handler(), Evidence()
    try:
        async with capture(mode) as (server, base):
            exc = await dispatch(base, stream, h, e)
            report(mode, stream, e, exc)
            assert exc is not None and server.bodies and e.charge
            assert e.attempts[0].cause == "ClientOSError"
            assert not e.attempts[0].not_sent
            assert len(e.attempts) == 2
            assert sum(f.closes for f in h.forwarders) == 1
            if mode == "crux":
                assert len(server.bodies) == 1 and e.attempts[1].not_sent
    finally:
        await h.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("variant", ["context", "bound"])
async def test_pooling_parity_and_cost(stream: bool, variant: str) -> None:
    started = time.perf_counter()
    owner = Handler()
    owner_startup = time.perf_counter() - started
    started = time.perf_counter()
    baseline = AsyncHTTPHandler(timeout=0.2)
    baseline_startup = time.perf_counter() - started
    try:
        async with capture() as (server, base):
            times: dict[str, float] = {}
            for name in ["off", variant]:
                start = time.perf_counter()
                for _ in range(40):
                    e = Evidence()
                    h = Handler(owner.pool, e) if name == "bound" else owner
                    exc = (
                        await completion(base, stream, baseline)
                        if name == "off"
                        else await dispatch(base, stream, h, e)
                    )
                    assert exc is None
                    if name == "bound":
                        await h.close()
                    if name != "off":
                        assert e.statuses == [200] and e.charge
                # Include shared-client construction amortized across the batch;
                # bound also includes every per-dispatch client construction.
                startup = baseline_startup if name == "off" else owner_startup
                times[name] = (time.perf_counter() - start + startup) * 1000 / 40
            assert server.accepts == 2  # One baseline and one shared seam connection.
            assert len(set(server.bodies)) == 1
            assert all(headers == server.headers[0] for headers in server.headers)
            payload = json.loads(server.bodies[0])
            assert payload["provider"] == {"order": ["OpenAI"], "allow_fallbacks": False}
            assert payload["reasoning"] == {"effort": "low", "exclude": False}
            assert payload.get("stream", False) is stream
            print(
                f"COST {variant} stream={stream} ms={times} added={times[variant] - times['off']:.3f}"
            )
    finally:
        await baseline.close()
        await owner.shutdown()
        assert owner.pool is not None
        assert isinstance(owner.pool.client, aiohttp.ClientSession) and owner.pool.client.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_concurrent_copied_contexts(stream: bool) -> None:
    h = Handler()
    evidence = [Evidence() for _ in range(3)]
    try:
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            async with peer("close") as (_, sent), capture("503") as (_, rejected):
                refused = f"http://127.0.0.1:{bound.getsockname()[1]}/v1"
                errors = await asyncio.gather(
                    *(
                        dispatch(base, stream, h, e)
                        for base, e in zip([refused, sent, rejected], evidence, strict=True)
                    )
                )
                assert all(exc is not None for exc in errors)
                assert [e.charge for e in evidence] == [False, True, True]
                assert [e.statuses for e in evidence] == [[], [], [503]]
                assert len(evidence[0].attempts) == 2
    finally:
        await h.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error_before", "error_after"])
async def test_stream_error_is_not_rejection(mode: str) -> None:
    h, e = Handler(), Evidence()
    try:
        async with capture(mode) as (_, base):
            exc = await dispatch(base, True, h, e)
            report(mode, True, e, exc)
            assert exc is not None and e.statuses == [200] and e.charge
    finally:
        await h.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["before", "read"])
async def test_cancel(mode: str, stream: bool) -> None:
    h, e = Handler(), Evidence()
    try:
        async with peer("read_timeout") as (server, base):
            task = asyncio.create_task(dispatch(base, stream, h, e, timeout=5))
            if mode == "read":
                await asyncio.wait_for(server.received.wait(), 3)
            task.cancel()
            try:
                result = await task
            except asyncio.CancelledError as cancelled:
                result = cancelled
            # The reused completion helper catches BaseException once started.
            assert isinstance(result, asyncio.CancelledError)
            assert e.charge
            assert bool(e.attempts) is (mode == "read")
            print(f"CANCEL {mode} stream={stream} {e}")
    finally:
        await h.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["broken", "bypassed"])
async def test_observer_fail_closed(mode: str) -> None:
    h, e = Handler(), Evidence(break_observer=mode == "broken")
    try:
        async with capture() as (_, base):
            exc = (
                await completion(base, False, h)
                if mode == "bypassed"
                else await dispatch(base, False, h, e)
            )
            assert exc is None and e.charge and not e.statuses
    finally:
        await h.shutdown()


@pytest.mark.parametrize("kind", [APIError, RateLimitError, ServiceUnavailableError])
def test_synthetic_exception_has_no_provenance(kind: Any) -> None:
    kwargs = {"message": "offline", "model": "offline", "llm_provider": "openrouter"}
    exc = kind(**kwargs, **({"status_code": 503} if kind is APIError else {}))
    assert exc.status_code in {429, 503}
    assert Evidence().charge and not Evidence().statuses


@pytest.mark.xfail(
    strict=True, reason="reliable owned-loopback TCP connect timeout not established"
)
def test_connect_timeout_unproved() -> None:
    pytest.fail("No external blackhole or mocked connector used")


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_local_unsupported_parameter(stream: bool) -> None:
    h, e = Handler(), Evidence()
    try:
        async with capture() as (server, base):
            exc = await dispatch(base, stream, h, e, verbosity="high", drop_params=False)
            report("local-validation", stream, e, exc)
            assert isinstance(exc, UnsupportedParamsError)
            assert not server.bodies and not e.attempts
            # Separate allowlisted source-path proof is required for release.
            assert e.charge and not e.statuses
    finally:
        await h.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_stale_idle_connection_reconnects(stream: bool) -> None:
    h = Handler()
    try:
        async with capture() as (server, base):
            assert await dispatch(base, stream, h, Evidence()) is None
            assert server.accepts == 1
            server.writers[0].close()
            await server.writers[0].wait_closed()
            e = Evidence()
            exc = await dispatch(base, stream, h, e)
            report("stale-pool", stream, e, exc)
            assert exc is None and server.accepts == 2
            assert e.charge and e.statuses == [200]
            # aiohttp discarded idle closed socket before sending; no handler
            # retry was needed. Post-send stale-reset retry is covered by crux.
    finally:
        await h.shutdown()


@pytest.mark.asyncio
async def test_retry_close_preserves_concurrent_active_stream() -> None:
    h, active_e, failed_e = Handler(), Evidence(), Evidence()
    task: asyncio.Task[BaseException | None] | None = None
    try:
        async with capture("active") as (server, base), capture("crux") as (_, failed):
            task = asyncio.create_task(dispatch(base, True, h, active_e, timeout=3))
            await asyncio.wait_for(server.received.wait(), 3)
            # The slow stream has sent headers/chunk but deliberately no EOF.
            exc = await dispatch(failed, False, h, failed_e)
            assert exc is not None and failed_e.charge and len(failed_e.attempts) == 2
            assert failed_e.attempts[1].not_sent
            assert sum(f.closes for f in h.forwarders) == 1
            assert h.pool is not None and isinstance(h.pool.client, aiohttp.ClientSession)
            assert not h.pool.client.closed and not task.done()
            server.release.set()
            assert await asyncio.wait_for(task, 3) is None
            assert active_e.statuses == [200]
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await h.shutdown()


def test_foreign_event_loop_recycles_session() -> None:
    async def first() -> tuple[Handler, aiohttp.ClientSession]:
        h = Handler()
        assert h.pool is not None
        old = h.pool._get_valid_client_session()
        return h, old

    h, old = asyncio.run(first())

    async def second() -> None:
        assert h.pool is not None
        new = h.pool._get_valid_client_session()
        assert new is not old and old.closed and not new.closed
        assert new._loop is asyncio.get_running_loop()
        await h.shutdown()
        assert new.closed

    asyncio.run(second())
