"""Production S4 transport, exercised over real owned-loopback sockets (#681).

The production handler borrows one factory-built aiohttp transport. create_client
delegates all HTTPX configuration to LiteLLM, then replaces only its unstarted
transport. Later factory transports have only a callable session factory (no live
session to close). Dispatch-local context supplies evidence, including request
hooks that expose a proxy/mount bypass as an unknown attempt.

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
import gzip
import json
import socket
import struct
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, cast
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import httpx
import pytest
from litellm.exceptions import (
    APIError,
    RateLimitError,
    ServiceUnavailableError,
    UnsupportedParamsError,
)
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

from orchestrator import inference_transport
from orchestrator.inference_transport import Attempt, Evidence, Handler, current
from tests.inference_transport_support import completion, offline_only, peer
from tests.test_dispatch_accounting import Ledger
from tests.test_compute_runtime import _account_scope, _chat_service, _qualified_policy, _route
from orchestrator import compute_runtime as runtime

__all__ = ["offline_only"]  # Reuse the enforced loopback/.invalid network fixture.


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
                assert e.release_path(exc) == (
                    "dispatch_not_sent" if mode in {"refused", "dns", "tls"} else None
                )
    finally:
        await h.shutdown()


@dataclass
class Capture:
    mode: str = "ok"
    sse_body: bytes | None = None
    sse_tail: bytes = b"data: [DONE]\n\n"
    sse_encoding: str = "identity"
    sse_type: str = "text/event-stream"
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
                if self.mode in {"reset", "crux"} or (
                    self.mode in {"sent_then_503", "reset_once"} and not self.bodies
                ):
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
                if self.mode == "sent_then_503":
                    status = 503
                if self.mode in {"reject_once", "reject_twice"}:
                    status = (
                        503 if len(self.bodies) <= (1 if self.mode == "reject_once" else 2) else 200
                    )
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
                    if self.sse_body is not None:
                        output = self.sse_body
                    if self.sse_encoding == "gzip":
                        output = gzip.compress(output)
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
                content_type = self.sse_type if streaming and status == 200 else "application/json"
                encoding = self.sse_encoding if streaming and status == 200 else "identity"
                if self.mode in {"active", "truncated"} and streaming:
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
                    )
                    writer.write(f"{len(output):x}\r\n".encode() + output + b"\r\n")
                    await writer.drain()
                    await self.release.wait()
                    if self.mode == "truncated":
                        return
                    tail = self.sse_tail
                    writer.write(f"{len(tail):x}\r\n".encode() + tail + b"\r\n0\r\n\r\n")
                else:
                    writer.write(
                        f"HTTP/1.1 {status} Offline\r\nContent-Type: {content_type}\r\n"
                        f"Content-Encoding: {encoding}\r\n"
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
async def capture(mode: str = "ok", **sse: Any) -> AsyncIterator[tuple[Capture, str]]:
    state = Capture(mode, **sse)
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
            assert e.release_path(exc) == (
                None if status in {408, 500, 504} else "dispatch_provider_rejected"
            )
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
            assert h.borrower_closes == 1
            if mode == "crux":
                assert len(server.bodies) == 1 and e.attempts[1].not_sent
    finally:
        await h.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_pooling_parity_and_cost(stream: bool) -> None:
    variant = "context"
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
                    h = owner
                    exc = (
                        await completion(base, stream, baseline)
                        if name == "off"
                        else await dispatch(base, stream, h, e)
                    )
                    assert exc is None
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
    h, e = Handler(), Evidence(broken=mode == "broken")
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
    assert Evidence().release_path(exc) is None


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
            assert e.release_path(exc) == "dispatch_not_sent_local"
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
            assert h.borrower_closes == 1
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


@pytest.mark.asyncio
async def test_bypass_mount_records_unknown_attempt() -> None:
    from httpx._utils import URLPattern

    h, e = Handler(), Evidence(attempts=[Attempt(status=503)])
    # An HTTPX proxy/mount bypasses the forwarding transport, but not the hook.
    h.client._mounts[URLPattern("all://")] = httpx.AsyncHTTPTransport()
    try:
        async with capture() as (_, base):
            assert await dispatch(base, False, h, e) is None
        assert len(e.attempts) == 2
        assert e.release_path(RuntimeError()) is None
    finally:
        await h.shutdown()


@pytest.mark.asyncio
async def test_broken_observer_does_not_refuse_inference() -> None:
    class BrokenAttempts(list[Attempt]):
        def append(self, value: Attempt) -> None:
            raise RuntimeError("synthetic observer failure")

    h, e = Handler(), Evidence(attempts=BrokenAttempts())
    try:
        async with capture() as (_, base):
            assert await dispatch(base, False, h, e) is None
        assert e.broken and e.release_path(RuntimeError()) is None
    finally:
        await h.shutdown()


@pytest.mark.asyncio
async def test_startup_check_degrades_without_private_factory(monkeypatch) -> None:
    monkeypatch.delattr(AsyncHTTPHandler, "_create_aiohttp_transport")
    assert inference_transport.get_handler() is None
    await inference_transport.shutdown()


@pytest.mark.asyncio
async def test_shutdown_waits_for_active_body() -> None:
    h, e = Handler(), Evidence()
    async with capture("active") as (server, base):
        task = asyncio.create_task(dispatch(base, True, h, e, timeout=3))
        await asyncio.wait_for(server.received.wait(), 3)
        shutdown = asyncio.create_task(h.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert isinstance(h.pool.client, aiohttp.ClientSession) and not h.pool.client.closed
        server.release.set()
        assert await task is None
        await shutdown
        assert h.pool.client.closed


@pytest.mark.asyncio
async def test_shutdown_force_closes_abandoned_body(monkeypatch, caplog):
    monkeypatch.setattr(inference_transport, "SHUTDOWN_DRAIN_TIMEOUT_S", 0.01, raising=False)
    h = inference_transport.get_handler()
    assert h is not None
    response = None
    try:
        async with capture("active") as (_, base):
            request = h.client.build_request(
                "POST", base + "/chat/completions", json={"stream": True}
            )
            response = await h.client.send(request, stream=True)
            assert h.active == 1
            await asyncio.wait_for(inference_transport.shutdown(), 0.3)
            assert h.pool.client.closed
            assert "active=1" in caplog.text
    finally:
        if response is not None:
            await response.aclose()
        await h.shutdown()


@pytest.mark.asyncio
async def test_backend_state_closes_if_inference_shutdown_fails(monkeypatch):
    from orchestrator import main
    from orchestrator.memory import startup

    state = SimpleNamespace(db_pool=None, history_runtime=None)
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(main, "_validate_startup_config", lambda _: None)
    monkeypatch.setattr(main, "apply_resolved_database_url", lambda _: None)
    monkeypatch.setattr(main, "init_app_state", AsyncMock(return_value=state))
    close_state = AsyncMock()
    monkeypatch.setattr(main, "close_app_state", close_state)
    monkeypatch.setattr(startup, "require_multi_query_schema", AsyncMock())
    monkeypatch.setattr(inference_transport, "get_handler", lambda: None)
    monkeypatch.setattr(
        inference_transport,
        "shutdown",
        AsyncMock(side_effect=RuntimeError("synthetic shutdown failure")),
    )
    with pytest.raises(RuntimeError, match="synthetic shutdown failure"):
        async with main.lifespan(cast(Any, SimpleNamespace(state=SimpleNamespace()))):
            pass
    close_state.assert_awaited_once_with(state)


@pytest.mark.asyncio
async def test_worker_pool_closes_if_inference_shutdown_fails(monkeypatch):
    from orchestrator.worker import worker
    from orchestrator.entitlements import attestation

    pool = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(attestation, "stop", AsyncMock())
    monkeypatch.setattr(
        inference_transport,
        "shutdown",
        AsyncMock(side_effect=RuntimeError("synthetic shutdown failure")),
    )
    shutdown = worker.on_shutdown
    assert shutdown is not None
    with pytest.raises(RuntimeError, match="synthetic shutdown failure"):
        await shutdown(cast(Any, {"db_pool": pool}))
    pool.close.assert_awaited_once()


def test_mixed_attempts_never_erase_unknown_send() -> None:
    for attempts in (
        [Attempt(), Attempt(status=503)],
        [Attempt(status=503), Attempt()],
        [Attempt(status=200), Attempt(status=503)],
    ):
        assert Evidence(attempts=attempts).release_path(RuntimeError()) is None
    assert (
        Evidence(attempts=[Attempt(not_sent=True), Attempt(status=503)]).release_path(
            RuntimeError()
        )
        == "dispatch_provider_rejected"
    )


def guarded_fixture(monkeypatch, base):
    route = _route(model="openrouter/openai/gpt-4.1-mini")
    route.endpoint = base
    _qualified_policy(monkeypatch, route=route)
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: SimpleNamespace(
            request_timeout_s=0.3,
            openrouter_api_key="fictional-offline-key",
            get_provider_config=lambda _: SimpleNamespace(extra_headers={}),
        ),
    )
    ledger = Ledger()
    service = _chat_service(
        reserve=AsyncMock(side_effect=ledger.reserve), settle=ledger.settle, release=ledger.release
    )
    return route, ledger, service


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 408, 422, 429, 500, 502, 503, 504])
async def test_guarded_real_status_accounting(monkeypatch, status, stream):
    try:
        async with capture(str(status)) as (_, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            async with _account_scope(service) as scope:
                with pytest.raises(runtime.ComputeUnavailable):
                    await runtime.guarded_completion(
                        model=route.model,
                        messages=[{"role": "user", "content": "hi"}],
                        stream=stream,
                    )
                decision = next(iter(scope.terminal_decisions.values()))
                unknown = status in {408, 500, 504}
                size = runtime._request_bound({"messages": [{"role": "user", "content": "hi"}]})
                assert decision.operation == ("settle" if unknown else "release")
                assert decision.amount == (route.estimate_microusd(size.bound, 1) if unknown else 0)
                assert decision.path == (
                    "dispatch_unknown_floor" if unknown else "dispatch_provider_rejected"
                )
                assert len(ledger.actions) == 1 and not scope.outstanding
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error_before", "error_after", "close_before"])
async def test_guarded_acquired_stream_failures(monkeypatch, mode):
    try:
        async with capture("ok" if mode == "close_before" else mode) as (_, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            async with _account_scope(service) as scope:
                stream = await runtime.guarded_completion(
                    model=route.model, messages=[{"role": "user", "content": "hi"}], stream=True
                )
                if mode == "close_before":
                    await stream.aclose()
                    for hold in tuple(scope.outstanding.values()):
                        await scope.finish_failure(hold, hold.failure_amount, path="scope_cleanup")
                else:
                    with pytest.raises(runtime.ComputeUnavailable):
                        async for _ in stream:
                            pass
                record = next(iter(ledger.reservations.values()))
                decision = next(iter(scope.terminal_decisions.values()))
                assert decision.operation == "settle"
                if mode == "error_after":
                    assert decision.amount == record["bound"] and decision.path == "stream_end"
                else:
                    assert decision.amount < record["bound"]
                    assert decision.path == (
                        "scope_cleanup" if mode == "close_before" else "dispatch_unknown_floor"
                    )
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["503", "reject_once", "reject_twice"])
async def test_guarded_extended_rejections_rearm_unit(monkeypatch, mode):
    try:
        async with capture(mode) as (_, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            routes = [_route(model=route.model) for _ in range(3)]
            for index, candidate in enumerate(routes):
                candidate.route_id = f"candidate-{index}"
                candidate.endpoint = base
            _qualified_policy(monkeypatch, route=routes)
            service.resolve.return_value.capabilities.add("extended_agents")
            async with _account_scope(service, extended=True, auto_route=True) as scope:
                if mode == "503":
                    with pytest.raises(runtime.ComputeUnavailable):
                        await runtime.guarded_completion(
                            model="auto", messages=[{"role": "user", "content": "hi"}]
                        )
                else:
                    await runtime.guarded_completion(
                        model="auto", messages=[{"role": "user", "content": "hi"}]
                    )
                assert ledger.units_used == int(mode != "503")
                assert all(record["extended_run"] for record in ledger.reservations.values())
                assert scope.extended_holds == int(mode != "503")
                assert not scope.outstanding
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
async def test_guarded_release_failure_stops_candidate_walk(monkeypatch):
    try:
        async with capture("503") as (server, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            routes = [_route(model=route.model) for _ in range(2)]
            for index, candidate in enumerate(routes):
                candidate.route_id = f"candidate-{index}"
                candidate.endpoint = base
            _qualified_policy(monkeypatch, route=routes)
            ledger.error = RuntimeError("financial failure")
            async with _account_scope(service, auto_route=True) as scope:
                with pytest.raises(runtime.ComputeUnavailable) as caught:
                    await runtime.guarded_completion(
                        model="auto", messages=[{"role": "user", "content": "hi"}]
                    )
                assert caught.value.code == "settlement_failed" and not caught.value.retryable
                assert len(server.bodies) == len(ledger.actions) == 1
                assert len(scope.outstanding) == 1
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_guarded_source_qualified_local_validation(monkeypatch, stream):
    # Inject an SDK-only unsupported parameter at the call boundary, without
    # widening guarded_completion's deliberately closed caller parameter set.
    original = runtime.litellm.acompletion

    async def unsupported(**kwargs):
        return await original(**kwargs, verbosity="high", drop_params=False)

    monkeypatch.setattr(runtime.litellm, "acompletion", unsupported)
    try:
        async with capture() as (server, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            async with _account_scope(service) as scope:
                with pytest.raises(runtime.ComputeUnavailable):
                    await runtime.guarded_completion(
                        model=route.model,
                        messages=[{"role": "user", "content": "hi"}],
                        stream=stream,
                    )
                decision = next(iter(scope.terminal_decisions.values()))
                assert (decision.operation, decision.amount, decision.path) == (
                    "release",
                    0,
                    "dispatch_not_sent_local",
                )
                assert not server.bodies and ledger.actions[0][0] == "release"
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["refused", "tls", "close", "immediate", "read_timeout"])
async def test_guarded_socket_failure_table(monkeypatch, stream, mode):
    try:
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            async with peer("tls" if mode == "refused" else mode) as (_, base):
                if mode == "refused":
                    base = f"http://127.0.0.1:{bound.getsockname()[1]}/v1"
                elif mode == "tls":
                    base = base.replace("http:", "https:")
                route, ledger, service = guarded_fixture(monkeypatch, base)
                async with _account_scope(service) as scope:
                    with pytest.raises(runtime.ComputeUnavailable):
                        await runtime.guarded_completion(
                            model=route.model,
                            messages=[{"role": "user", "content": "hi"}],
                            stream=stream,
                        )
                    decision = next(iter(scope.terminal_decisions.values()))
                    not_sent = mode in {"refused", "tls"}
                    size = runtime._request_bound({"messages": [{"role": "user", "content": "hi"}]})
                    assert decision.operation == ("release" if not_sent else "settle")
                    assert decision.path == (
                        "dispatch_not_sent" if not_sent else "dispatch_unknown_floor"
                    )
                    assert decision.amount == (
                        0 if not_sent else route.estimate_microusd(size.bound, 1)
                    )
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_guarded_sent_then_retry_503_is_unknown(monkeypatch, stream):
    try:
        async with capture("sent_then_503") as (server, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            async with _account_scope(service) as scope:
                with pytest.raises(runtime.ComputeUnavailable):
                    await runtime.guarded_completion(
                        model=route.model,
                        messages=[{"role": "user", "content": "hi"}],
                        stream=stream,
                    )
                decision = next(iter(scope.terminal_decisions.values()))
                assert len(server.bodies) == 2
                assert decision.operation == "settle" and decision.path == "dispatch_unknown_floor"
                size = runtime._request_bound({"messages": [{"role": "user", "content": "hi"}]})
                assert decision.amount == route.estimate_microusd(size.bound, 1)
                assert ledger.actions[0][0] == "settle"
    finally:
        await inference_transport.shutdown()


_DONE = b"data: [DONE]\n\n"
_MISSING = {"state": "missing"}
_USAGE = {"prompt_tokens": 7, "completion_tokens": 31, "total_tokens": 38}


def _sse_chunk(choices, **fields):
    chunk = {
        "id": "offline",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "openai/gpt-4.1-mini",
        "choices": choices,
        **fields,
    }
    return b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n"


def _sse_choice(reason, delta=None):
    return _sse_chunk(
        [{"index": 0, "delta": delta if delta is not None else {}, "finish_reason": reason}]
    )


_TEXT = _sse_choice(None, {"content": "ok [not a sentinel] ☀"})
_USAGE_TAIL = _sse_chunk([], usage=_USAGE)


def _confirmed(reason):
    return {"state": "confirmed", "reason": reason, "provenance": "provider_wire"}


async def _guarded_stream(model):
    return await runtime.guarded_completion(
        model=model, messages=[{"role": "user", "content": "hi"}], stream=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        (b"", _MISSING),
        (_sse_choice("stop"), _confirmed("stop")),
        (_sse_choice("length"), _confirmed("length")),
        (_sse_choice("content_filter"), _confirmed("content_filter")),
        (_sse_choice("tool_calls"), _confirmed("tool_calls")),
        (_sse_choice("stop") + _sse_choice("length"), {"state": "conflicting"}),
        (_sse_choice("stop") + _sse_choice("fictional"), _MISSING),
    ],
    ids=["synthetic-stop", "stop", "length", "content-filter", "tool-calls", "conflict", "unknown"],
)
async def test_guarded_wire_finish_and_usage(monkeypatch, terminal, expected):
    # Stop and length have identical message text and token counts; only wire
    # evidence distinguishes them. All cases use the unchanged usage-only tail.
    try:
        async with capture(sse_body=_TEXT + terminal + _USAGE_TAIL + _DONE) as (server, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            # Tie the route ceiling to the account's 128-token sizing limit
            # rather than mistaking the sent value for the default route cap.
            route.max_output_tokens = 128
            async with _account_scope(service) as scope:
                stream = await _guarded_stream(route.model)
                assert runtime.provider_finish(stream) == _MISSING
                sdk_reasons = []
                async for chunk in stream:
                    assert runtime.provider_finish(stream) == _MISSING
                    sdk_reasons.extend(choice.finish_reason for choice in chunk.choices)
                if not terminal:
                    assert "stop" in sdk_reasons  # LiteLLM supplies it, not the wire.
                evidence = runtime.provider_finish(stream)
                assert evidence.pop("dispatch_id")
                assert evidence == expected
                limits = runtime.answer_limit_candidates(stream)
                equal_candidates = {limit["kind"] for limit in limits if limit["value"] == 128}
                assert {"sent_output", "route_output", "account_output"} <= equal_candidates
                assert not any(limit["binding"] for limit in limits)
                await stream.aclose()
                assert runtime.provider_finish(stream)["dispatch_id"]
                assert len(server.bodies) == len(ledger.actions) == 1
                (decision,) = scope.terminal_decisions.values()
                assert (decision.path, decision.amount) == ("stream_end", 38)
                assert decision.usage == {"input_tokens": 7, "output_tokens": 31}
                assert not scope.outstanding
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "eof_without_done",
        "unterminated_done",
        "after_done",
        "gzip",
        "wrong_media_type",
        "duplicate_reason",
        "mixed_id",
    ],
)
async def test_guarded_wire_finish_unsupported_or_ambiguous(monkeypatch, case):
    stop = _sse_choice("stop")
    body = _TEXT + stop + _DONE
    headers = {}
    if case == "eof_without_done":
        body = _TEXT + stop
    elif case == "unterminated_done":
        body = _TEXT + stop + _DONE.rstrip()
    elif case == "after_done":
        body += _sse_choice("length")
    elif case == "gzip":
        headers["sse_encoding"] = "gzip"
    elif case == "wrong_media_type":
        headers["sse_type"] = "application/json"
    elif case == "duplicate_reason":
        body = _TEXT + stop.replace(b'"stop"', b'"length", "finish_reason": "stop"') + _DONE
    elif case == "mixed_id":
        body = _TEXT + stop.replace(b'"id": "offline"', b'"id": "unrelated"') + _DONE
    try:
        async with capture(sse_body=body, **headers) as (server, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            async with _account_scope(service):
                stream = await _guarded_stream(route.model)
                assert [chunk async for chunk in stream]  # Same SDK behavior.
                evidence = runtime.provider_finish(stream)
                assert evidence.pop("dispatch_id")
                assert evidence == _MISSING
                assert len(server.bodies) == len(ledger.actions) == 1
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
async def test_guarded_wire_finish_requires_raw_eof_after_done(monkeypatch):
    try:
        async with capture("truncated", sse_body=_TEXT + _sse_choice("stop") + _DONE) as (
            server,
            base,
        ):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            async with _account_scope(service):
                stream = await _guarded_stream(route.model)
                await anext(stream)
                server.release.set()  # Close socket without the zero HTTP chunk.
                assert "stop" in [
                    choice.finish_reason async for chunk in stream for choice in chunk.choices
                ]  # LiteLLM swallows the ReadError once it has a finish reason.
                evidence = runtime.provider_finish(stream)
                assert evidence.pop("dispatch_id")
                assert evidence == _MISSING
                assert len(ledger.actions) == 1
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("fail_settlement", [False, True])
async def test_guarded_wire_finish_publishes_only_after_finalizer(
    monkeypatch, managed, fail_settlement
):
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        async with capture(
            "active", sse_body=_TEXT + _sse_choice("stop"), sse_tail=_USAGE_TAIL + _DONE
        ) as (server, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)

            async def settle(*args, **kwargs):
                entered.set()
                await release.wait()
                return await ledger.settle(*args, **kwargs)

            service.settle = settle
            if fail_settlement:
                ledger.error = RuntimeError("fictional settlement failure")
            async with _account_scope(service) as scope:
                if managed:
                    scope.inference_invocation = runtime.InferenceInvocation()
                    scope.inference_identity = runtime.AttemptIdentity(
                        uuid.uuid4(),
                        scope.user_id,
                        0,
                        uuid.uuid4(),
                        1,
                        scope.scope_id,
                        uuid.uuid4(),
                        uuid.uuid4(),
                    )
                    scope.inference_recovery = cast(Any, SimpleNamespace(mark=AsyncMock()))

                    async def reserve(identity, amount, **kwargs):
                        assert identity is scope.inference_identity
                        return await ledger.reserve(identity.account_id, amount, **kwargs)

                    service.reserve_for_attempt = reserve

                    def no_client_replacement():
                        pytest.fail("managed inference must keep LiteLLM's own client")

                    monkeypatch.setattr(inference_transport, "get_handler", no_client_replacement)
                stream = await _guarded_stream(route.model)
                await anext(stream)
                assert runtime.provider_finish(stream) == _MISSING

                async def drain():
                    async for _ in stream:
                        assert runtime.provider_finish(stream) == _MISSING

                task = asyncio.create_task(drain())
                try:
                    server.release.set()
                    await asyncio.wait_for(entered.wait(), 1)
                    assert runtime.provider_finish(stream) == _MISSING
                finally:
                    release.set()
                    if fail_settlement:
                        with pytest.raises(RuntimeError if managed else runtime.ComputeUnavailable):
                            await task
                    else:
                        await task
                evidence = runtime.provider_finish(stream)
                if not fail_settlement:
                    assert evidence.pop("dispatch_id")
                assert evidence == (_MISSING if fail_settlement else _confirmed("stop"))
                assert len(server.bodies) == len(ledger.actions) == 1
                (decision,) = scope.terminal_decisions.values()
                assert (decision.path, decision.amount) == ("stream_end", 38)
                assert decision.usage == {"input_tokens": 7, "output_tokens": 31}
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
async def test_guarded_wire_finish_isolated_across_physical_retry_and_route_fallback(monkeypatch):
    rejected_stop = b'data: {"error":{"message":"offline","code":503}}\n\n' + _sse_choice("stop")
    try:
        async with (
            capture("reset_once", sse_body=_TEXT + _sse_choice("length") + _DONE) as (retry, base),
            capture(sse_body=rejected_stop + _DONE) as (rejected, first),
            capture(sse_body=_TEXT + _DONE) as (selected, last),
        ):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            async with _account_scope(service, auto_route=True):
                retried = await _guarded_stream("auto")
                assert [chunk async for chunk in retried]
                assert len(retry.bodies) == 2
                first_finish = runtime.provider_finish(retried)
                assert first_finish["reason"] == "length"
                assert first_finish["provenance"] == "provider_wire"

                fallback = _route(
                    model=route.model, input_price=route.price_ceiling.microusd_per_1m_prompt + 1
                )
                fallback.route_id = "fallback"
                fallback.endpoint = last
                route.endpoint = first
                _qualified_policy(monkeypatch, route=[route, fallback])
                stream = await _guarded_stream("auto")
                assert [chunk async for chunk in stream]
                assert len(rejected.bodies) == len(selected.bodies) == 1
                assert len(ledger.actions) == 3
                selected_finish = runtime.provider_finish(stream)
                assert selected_finish["dispatch_id"] != first_finish["dispatch_id"]
                assert selected_finish.pop("dispatch_id")
                assert selected_finish == _MISSING
                assert runtime.provider_finish(retried) == first_finish
    finally:
        await inference_transport.shutdown()


@pytest.mark.asyncio
async def test_guarded_wire_finish_close_after_sdk_stop_does_not_publish(monkeypatch):
    try:
        async with capture(sse_body=_TEXT + _sse_choice("stop") + _DONE) as (_, base):
            route, ledger, service = guarded_fixture(monkeypatch, base)
            async with _account_scope(service):
                stream = await _guarded_stream(route.model)
                async for chunk in stream:
                    if any(choice.finish_reason == "stop" for choice in chunk.choices):
                        break
                else:
                    pytest.fail("no SDK stop to test early close")
                assert runtime.provider_finish(stream) == _MISSING
                await stream.aclose()
                assert runtime.provider_finish(stream) == _MISSING
                assert len(ledger.actions) == 1
    finally:
        await inference_transport.shutdown()


def test_finish_sse_byte_boundaries_and_observation_limits(monkeypatch):
    valid = (b": keepalive\n\n" + _TEXT + _sse_choice("stop") + _USAGE_TAIL + _DONE).replace(
        b"\n", b"\r\n"
    )
    observer = inference_transport._FinishSSE()
    for byte in valid:
        observer.feed(bytes([byte]))
    assert observer.result == _MISSING
    observer.eof()
    assert observer.result == _confirmed("stop")
    for bound in ("_MAX_SSE_LINE", "_MAX_SSE_BYTES"):
        with monkeypatch.context() as constrained:
            constrained.setattr(inference_transport, bound, 20)
            observer = inference_transport._FinishSSE()
            observer.feed(valid)
            observer.eof()
            assert observer.result == _MISSING
