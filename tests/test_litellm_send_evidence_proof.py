"""Offline #681 transport evidence proof; deliberately no production integration.

Only loopback HTTP and the reserved .invalid DNS namespace are allowed here.
No provider credentials, database, or LiteLLM internal mocks are used.

Result/limits (locked LiteLLM 1.100.4): the per-call handler factory plus
httpcore trace preserves sent evidence across the hidden fresh-client retry.
The default aiohttp transport ignores that trace; hooks alone are NOT enough.
Missing/broken coverage means charge, never release. Local validation without
transport entry is consequently a strict xfail, not a claimed negative proof.
TCP connect timeout is unproved. Header-send START is conservative: it denotes
possible request transmission, not receipt, billing, or successful body upload.

Installed-source map (relative to site-packages/litellm):
* main.py:3347-3416 passes the per-call client to the OpenRouter HTTP handler.
* main.py:1231-1232 returns ctx.client, not litellm.aclient_session. The latter
  is used by the OpenAI SDK path (llms/openai/common_utils.py:297-298), NOT this
  OpenRouter path. A raw httpx client fails the AsyncHTTPHandler type filter
  (llms/custom_httpx/llm_http_handler.py:580,603) and falls back to the cache.
* llms/custom_httpx/llm_http_handler.py:416-426,841-847 selects client vs cache
  for non-stream/stream; http_handler.py:1515-1566 keys the shared cache by
  provider and params. A per-dispatch handler here deliberately bypasses it.
* http_handler.py:687-704 retries ConnectError/RemoteProtocolError with a fresh
  client even when compute_runtime.py:2583 passes num_retries=0. Factory hooks
  survive that retry. Attaching a transport only to handler.client does not.
* http_handler.py:1039-1077 defaults to aiohttp. This proof's factory explicitly
  uses HTTPX/httpcore for every client, with no global LiteLLM flag changes.
* compute_runtime.py:2665-2668 supplies remaining timeout plus asyncio deadline;
  keep BOTH in production. The factory preserves the supplied per-request
  timeout for the retry, but per-call pools lose existing cross-dispatch reuse.
  Shared pooling with context-local evidence is a separate, unproved design.

Classification: compute_runtime.py:239-289 correctly recognizes 429/503 and
read timeouts. Refused/DNS/TLS/protocol errors here surface as APIError(500),
classified unspecified/nonretryable, NOT connection_failed. Validation is
invalid_request; cancellation is unspecified (normally handled as cancellation).
No exception category alone can decide whether request transmission began.
No production file is changed. Parent review/integration remains required.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any, cast

import httpx
import litellm
import pytest
from litellm.exceptions import APIError, BadRequestError
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

from orchestrator.compute_runtime import _classify_dispatch_failure


@pytest.fixture(autouse=True)
def offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforce the proof's network boundary, including SDK background work."""
    original_connect = socket.socket.connect
    original_resolve = socket.getaddrinfo

    def connect(sock: socket.socket, address: Any) -> Any:
        assert isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}, address
        return original_connect(sock, address)

    def resolve(host: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(host, bytes):
            host = host.decode("ascii")
        assert host in {"127.0.0.1", "::1", "send-evidence.invalid"}, host
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)


@dataclass
class SendEvidence:
    """Monotonic per-dispatch evidence; unknown instrumentation must charge."""

    broken: bool = False
    attempts: list[list[str]] = field(default_factory=list)
    statuses: list[int] = field(default_factory=list)
    break_trace: bool = False
    clients: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def sent(self) -> bool:
        return any(
            name.endswith("send_request_headers.started")
            for attempt in self.attempts
            for name in attempt
        )

    @property
    def charge(self) -> bool:
        # Absence of evidence is NOT evidence of absence. Only an observed
        # connect/TLS failure before header sending certifies a negative attempt.
        return (
            self.broken
            or self.sent
            or not self.attempts
            or len(self.attempts) < self.clients
            or any(
                not any(
                    name in {"connection.connect_tcp.failed", "connection.start_tls.failed"}
                    for name in attempt
                )
                for attempt in self.attempts
            )
        )

    async def request(self, request: httpx.Request) -> None:
        events: list[str] = []
        self.attempts.append(events)

        async def trace(name: str, info: dict[str, Any]) -> None:
            try:
                if self.break_trace:
                    raise RuntimeError("synthetic observer failure")
                events.append(name)
                if "exception" in info:
                    self.failures.append(type(info["exception"]).__name__)
            except Exception:
                self.broken = True
                raise

        request.extensions["trace"] = trace

    async def response(self, response: httpx.Response) -> None:
        self.statuses.append(response.status_code)


class ObservedHandler(AsyncHTTPHandler):
    """Force httpcore on BOTH clients without mutating global LiteLLM state.

    A custom transport installed only on ``handler.client`` is insufficient:
    http_handler.py:692 creates another client through this factory. A plain
    httpx.AsyncClient passed as ``client=`` is not an AsyncHTTPHandler either.
    This per-dispatch factory deliberately sacrifices the shared client cache.
    """

    def __init__(self, evidence: SendEvidence) -> None:
        self.evidence = evidence
        super().__init__(
            timeout=0.2,
            event_hooks={"request": [evidence.request], "response": [evidence.response]},
        )

    def create_client(
        self,
        timeout: float | httpx.Timeout | None,
        event_hooks: Mapping[str, list[Any]] | None,
        ssl_verify: Any = None,
        shared_session: Any = None,
    ) -> httpx.AsyncClient:
        self.evidence.clients += 1
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0),
            timeout=timeout,
            event_hooks=event_hooks,
            trust_env=False,
            follow_redirects=True,
        )


def observed_handler(evidence: SendEvidence) -> ObservedHandler:
    return ObservedHandler(evidence)


@dataclass
class Peer:
    mode: str
    bodies: list[bytes] = field(default_factory=list)
    received: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    server: asyncio.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        try:
            if self.mode == "immediate":
                return
            if self.mode == "tls":
                await reader.read(4096)
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            header = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in header.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            self.bodies.append(await reader.readexactly(length))
            self.received.set()
            if self.mode == "crux":
                assert self.server is not None
                self.server.close()
                # Python 3.14 wait_closed also waits for accepted connections;
                # awaiting it inside this handler would deadlock on ourselves.
                return
            if self.mode == "close":
                return
            if self.mode == "read_timeout":
                await self.release.wait()
                return
            status = int(self.mode) if self.mode in {"429", "503"} else 200
            if status != 200:
                body = json.dumps({"error": {"message": "offline", "code": status}}).encode()
                content_type = "application/json"
            elif json.loads(self.bodies[-1]).get("stream"):
                chunk = {
                    "id": "offline",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "openai/gpt-4.1-mini",
                    "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}],
                }
                body = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                body = json.dumps(
                    {
                        "id": "offline",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "openai/gpt-4.1-mini",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                ).encode()
                content_type = "application/json"
            writer.write(
                f"HTTP/1.1 {status} Offline\r\nContent-Type: {content_type}\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
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
async def peer(mode: str) -> AsyncIterator[tuple[Peer, str]]:
    state = Peer(mode)
    state.server = await asyncio.start_server(state.handle, "127.0.0.1", 0)
    port = state.server.sockets[0].getsockname()[1]
    try:
        yield state, f"http://127.0.0.1:{port}/v1"
    finally:
        state.release.set()
        state.server.close()
        await state.server.wait_closed()
        if state.tasks:
            await asyncio.gather(*state.tasks)


async def completion(
    base: str, stream: bool, client: Any, **overrides: Any
) -> BaseException | None:
    call: dict[str, Any] = {
        "model": "openrouter/openai/gpt-4.1-mini",
        "messages": [{"role": "user", "content": "offline proof"}],
        "api_base": base,
        "api_key": "fictional-offline-key",
        "max_tokens": 8,
        "num_retries": 0,
        "timeout": 0.2,
        "stream": stream,
        "provider": {"order": ["OpenAI"], "allow_fallbacks": False},
        "extra_body": {"reasoning": {"effort": "low", "exclude": False}},
        "client": client,
    }
    if stream:
        call["stream_options"] = {"include_usage": True}
    call.update(overrides)
    try:
        result = await asyncio.wait_for(litellm.acompletion(**call), timeout=4)
        if stream:
            async for _ in cast(AsyncIterator[Any], result):
                pass
        return None
    except BaseException as exc:
        return exc


def report(case: str, stream: bool, evidence: SendEvidence, exc: BaseException | None) -> None:
    classified = (
        _classify_dispatch_failure(exc, deadline_bound=False, output_released=False)
        if exc is not None
        else None
    )
    print(
        f"PROOF {case} stream={stream} exception={type(exc).__name__} "
        f"charge={evidence.charge} sent={evidence.sent} status={evidence.statuses} "
        f"classification={classified} attempts={evidence.attempts}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "mode", ["close", "immediate", "crux", "429", "503", "read_timeout", "tls"]
)
async def test_socket_failures(mode: str, stream: bool) -> None:
    assert version("litellm") == "1.100.4"
    evidence = SendEvidence()
    handler = observed_handler(evidence)
    try:
        async with peer(mode) as (server, base):
            if mode == "tls":
                base = base.replace("http:", "https:")
            exc = await completion(base, stream, handler)
            report(mode, stream, evidence, exc)
            assert exc is not None
            assert evidence.charge is (mode != "tls")
            if mode not in {"tls", "immediate"}:
                assert server.bodies
                assert evidence.sent
            if mode == "crux":
                assert len(server.bodies) == 1
                assert len(evidence.attempts) == 2
                assert "connection.connect_tcp.failed" in evidence.attempts[1]
                assert evidence.failures == ["RemoteProtocolError", "ConnectError"]
            if mode in {"429", "503"}:
                assert evidence.statuses == [int(mode)]
                assert getattr(exc, "status_code", None) == int(mode)
    finally:
        await handler.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["refused", "dns"])
async def test_preconnect(mode: str, stream: bool) -> None:
    evidence = SendEvidence()
    handler = observed_handler(evidence)
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        base = (
            f"http://127.0.0.1:{bound.getsockname()[1]}/v1"
            if mode == "refused"
            else "http://send-evidence.invalid/v1"
        )
        try:
            exc = await completion(base, stream, handler)
            report(mode, stream, evidence, exc)
            assert type(exc) is APIError
            assert not evidence.charge
            assert len(evidence.attempts) == 2
            classified = _classify_dispatch_failure(
                exc, deadline_bound=False, output_released=False
            )
            assert classified.category == "unspecified" and not classified.retryable
        finally:
            await handler.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_body_unchanged(stream: bool) -> None:
    evidence = SendEvidence()
    handler = observed_handler(evidence)
    baseline = AsyncHTTPHandler(timeout=0.2)
    try:
        async with peer("ok") as (server, base):
            assert await completion(base, stream, baseline) is None
            assert await completion(base, stream, handler) is None
            assert server.bodies[0] == server.bodies[1]
            body = json.loads(server.bodies[1])
            assert body["provider"] == {"order": ["OpenAI"], "allow_fallbacks": False}
            assert body["reasoning"] == {"effort": "low", "exclude": False}
            assert body.get("stream", False) is stream
            assert evidence.sent and evidence.charge
            report("body-parity", stream, evidence, None)
    finally:
        await handler.close()
        await baseline.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_concurrent(stream: bool) -> None:
    sent, refused = SendEvidence(), SendEvidence()
    one, two = observed_handler(sent), observed_handler(refused)
    try:
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            async with peer("crux") as (_, base):
                errors = await asyncio.gather(
                    completion(base, stream, one),
                    completion(f"http://127.0.0.1:{bound.getsockname()[1]}/v1", stream, two),
                )
                assert all(error is not None for error in errors)
                assert sent.charge and sent.sent
                assert not refused.charge and not refused.sent
                assert len(sent.attempts) == len(refused.attempts) == 2
    finally:
        await one.close()
        await two.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["before", "read"])
async def test_cancellation(mode: str, stream: bool) -> None:
    evidence = SendEvidence()
    handler = observed_handler(evidence)
    try:
        async with peer("read_timeout") as (server, base):
            task = asyncio.create_task(completion(base, stream, handler, timeout=5))
            if mode == "read":
                await asyncio.wait_for(server.received.wait(), timeout=3)
            task.cancel()
            try:
                exc = await task
            except asyncio.CancelledError as cancelled:
                exc = cancelled
            report("cancel-" + mode, stream, evidence, exc)
            assert isinstance(exc, asyncio.CancelledError)
            if mode == "read":
                assert evidence.sent and evidence.charge
            else:
                assert not server.bodies and not evidence.attempts
                # Cancellation before the coroutine starts cannot attest to
                # observer coverage. The candidate conservatively keeps charge.
                assert evidence.charge
    finally:
        await handler.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.xfail(
    strict=True, reason="no transport entry cannot distinguish validation from observer bypass"
)
async def test_local_validation_not_sent(stream: bool) -> None:
    evidence = SendEvidence()
    handler = observed_handler(evidence)
    try:
        async with peer("ok") as (server, base):
            exc = await completion(base, stream, handler, model="no-provider-prefix")
            report("validation", stream, evidence, exc)
            assert isinstance(exc, BadRequestError)
            assert not server.bodies and not evidence.attempts
            assert not evidence.charge
    finally:
        await handler.close()


@pytest.mark.xfail(
    strict=True, reason="reliable TCP connect timeout not established using owned loopback sockets"
)
def test_connect_timeout_not_proved() -> None:
    pytest.fail(
        "No deterministic loopback TCP connect-timeout simulation; no external blackhole probe"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["broken", "bypassed", "aiohttp"])
async def test_observer_fails_closed(mode: str) -> None:
    evidence = SendEvidence(break_trace=mode == "broken")
    handler: AsyncHTTPHandler = (
        AsyncHTTPHandler(
            timeout=0.2,
            event_hooks={"request": [evidence.request], "response": [evidence.response]},
        )
        if mode == "aiohttp"
        else observed_handler(evidence)
    )
    if mode == "bypassed":
        handler.client.event_hooks["request"] = []
        handler.client.event_hooks["response"] = []
    try:
        async with peer("ok") as (server, base):
            exc = await completion(base, False, handler)
            report(mode, False, evidence, exc)
            assert evidence.charge
            if mode == "broken":
                assert exc is not None and evidence.broken
                assert not server.bodies
            else:
                assert exc is None and server.bodies
                assert not evidence.sent
    finally:
        await handler.close()


def test_missing_retry_observation_fails_closed() -> None:
    # Synthetic observer-state fault, not a mocked LiteLLM/socket failure: an
    # observed first connect failure cannot excuse a fresh unobserved client.
    evidence = SendEvidence(clients=2, attempts=[["connection.connect_tcp.failed"]])
    assert evidence.charge
