"""Dispatch-local evidence over LiteLLM's own, loop-owned aiohttp pool.

Only a direct ClientConnectorError certifies that an attempt was not sent.
Exception metadata from the SDK is deliberately not evidence. Borrowers (including
LiteLLM's internal connection retry) cannot close the application-owned pool.
Private seam drift disables observation, never inference. Shutdown follows the
server/worker drain and additionally waits for outstanding response bodies.

Finish evidence is independent: it observes the raw body of the HTTP response
that the locked OpenRouter stream iterator actually received.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, TypedDict, cast
from weakref import WeakKeyDictionary

import aiohttp
import httpx
from litellm.exceptions import UnsupportedParamsError
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from litellm import utils

logger = logging.getLogger(__name__)
SHUTDOWN_DRAIN_TIMEOUT_S = 2.0
SHUTDOWN_CLOSE_TIMEOUT_S = 2.0


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

    @property
    def charge(self) -> bool:
        return self.broken or not self.attempts or not all(a.not_sent for a in self.attempts)

    @property
    def statuses(self) -> list[int]:
        return [] if self.broken else [a.status for a in self.attempts if a.status is not None]

    def release_path(self, exc: BaseException) -> str | None:
        if self.broken:
            return None
        if not self.attempts:
            # Locked LiteLLM chat validation raise, not merely a matching class.
            if isinstance(exc, UnsupportedParamsError):
                tb = exc.__traceback__
                while tb is not None:
                    code = tb.tb_frame.f_code
                    if (
                        code.co_filename == utils.__file__
                        and code.co_name == "_check_valid_arg"
                        and tb.tb_lineno == 4210
                    ):
                        return "dispatch_not_sent_local"
                    tb = tb.tb_next
            return None
        if all(a.not_sent for a in self.attempts):
            return "dispatch_not_sent"
        if any(a.status is not None for a in self.attempts) and all(
            a.not_sent
            or a.status in {429, 502, 503}
            or (a.status is not None and 400 <= a.status < 500 and a.status != 408)
            for a in self.attempts
        ):
            return "dispatch_provider_rejected"
        return None


current: ContextVar[Evidence | None] = ContextVar("inference_transport_evidence", default=None)


FinishReason = Literal["stop", "length", "content_filter", "tool_calls"]


class ProviderFinish(TypedDict):
    state: Literal["confirmed", "missing", "conflicting", "not_attempted"]
    reason: NotRequired[FinishReason]
    provenance: NotRequired[Literal["provider_wire"]]


_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls"})
_MAX_SSE_LINE = 64 * 1024
_MAX_SSE_BYTES = 32 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError
        fields[key] = value
    return fields


def _reject_constant(value: str) -> None:
    raise ValueError


class _FinishSSE:
    """A bounded, strict subset of OpenRouter's Chat Completions SSE."""

    def __init__(self) -> None:
        self.result: ProviderFinish = {"state": "missing"}
        self._line = bytearray()
        self._bytes = 0
        self._id: bytes | None = None
        self._pending = False
        self._done = False
        self._disabled = False
        self._reasons: set[FinishReason] = set()

    def discard(self) -> None:
        self._disabled = True
        self._line.clear()
        self._id = None
        self._reasons.clear()

    def feed(self, chunk: bytes) -> None:
        if self._disabled:
            return
        try:
            self._bytes += len(chunk)
            if self._bytes > _MAX_SSE_BYTES:
                raise ValueError
            offset = 0
            while offset < len(chunk):
                end = chunk.find(b"\n", offset)
                limit = len(chunk) if end == -1 else end
                if len(self._line) + limit - offset > _MAX_SSE_LINE:
                    raise ValueError
                self._line.extend(chunk[offset:limit])
                if end == -1:
                    break
                line = bytes(self._line).removesuffix(b"\r")
                self._line.clear()
                self._read_line(line)
                offset = end + 1
        except Exception:
            # Unsupported/malformed frames, or inspection failure: do not log
            # supplied bytes or influence the provider iterator.
            self.discard()

    def _read_line(self, line: bytes) -> None:
        if not line:
            self._pending = False
            return
        # HTTPX's text line decoder also splits on Unicode separators. Do not
        # attest to frames this path could have interpreted as separate lines.
        text = line.decode("utf-8")
        if text.splitlines() != [text]:
            raise ValueError
        if text.startswith(":") and "[DONE]" not in text:
            return  # OpenRouter processing comments / keepalive.
        if not text.startswith("data:") or self._pending or self._done:
            raise ValueError
        self._pending = True
        payload = text[5:].removeprefix(" ")
        if payload == "[DONE]":
            self._done = True
            return
        if "[DONE]" in payload:
            raise ValueError  # The locked SDK treats the substring as a sentinel.
        frame = json.loads(
            payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        if (
            not isinstance(frame, dict)
            or frame.get("object") != "chat.completion.chunk"
            or "error" in frame
        ):
            raise ValueError
        response_id = frame.get("id")
        if not isinstance(response_id, str) or not 0 < len(response_id) <= 512:
            raise ValueError
        digest = hashlib.sha256(response_id.encode("utf-8")).digest()
        if self._id is not None and digest != self._id:
            raise ValueError
        self._id = digest
        choices = frame.get("choices")
        if not isinstance(choices, list):
            raise ValueError
        if not choices and isinstance(frame.get("usage"), dict):
            return  # Usage is not a choice or a finish reason.
        if len(choices) != 1 or not isinstance(choice := choices[0], dict):
            raise ValueError
        index = choice.get("index")
        delta = choice.get("delta")
        reason = choice.get("finish_reason")
        if type(index) is not int or index != 0 or not isinstance(delta, dict):
            raise ValueError
        if reason is None:
            if self._reasons:
                raise ValueError  # A generation choice followed a terminal choice.
        elif isinstance(reason, str) and reason in _FINISH_REASONS:
            if self._reasons and reason in self._reasons and delta:
                raise ValueError  # Do not confirm a terminal reason with resumed output.
            self._reasons.add(cast(FinishReason, reason))
        else:
            raise ValueError

    def eof(self) -> None:
        if not self._disabled and self._done and not self._pending and not self._line:
            if len(self._reasons) > 1:
                self.result = {"state": "conflicting"}
            elif self._reasons:
                self.result = {
                    "state": "confirmed",
                    "reason": next(iter(self._reasons)),
                    "provenance": "provider_wire",
                }
        self.discard()


class _FinishBody(httpx.AsyncByteStream):
    def __init__(self, body: httpx.AsyncByteStream, observer: _FinishSSE) -> None:
        self.body = body
        self.observer = observer

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.body:
                self.observer.feed(chunk)
                yield chunk
            # An exception or consumer close is not raw EOF, even if LiteLLM
            # translates it into SDK exhaustion after seeing a finish reason.
            self.observer.eof()
        finally:
            self.observer.discard()

    async def aclose(self) -> None:
        self.observer.discard()
        await self.body.aclose()


def observe_stream_finish(sdk_stream: Any) -> _FinishSSE | None:
    """Tap the selected response, without selecting or replacing its client.

    Locked LiteLLM's make_async_call_stream_helper attaches the exact HTTPX
    response to the OpenRouter line iterator before any body reads. Its
    CustomStreamWrapper retains that iterator. No SDK-normalized finish fields,
    request bodies, shared-state correlation, extra pulls or close calls.
    """
    try:
        streaming_handler = importlib.import_module("litellm.litellm_core_utils.streaming_handler")
        if (
            type(sdk_stream) is not streaming_handler.CustomStreamWrapper
            or sdk_stream.custom_llm_provider != "openrouter"
        ):
            return None
        openrouter = importlib.import_module("litellm.llms.openrouter.chat.transformation")
        iterator = sdk_stream.completion_stream
        if type(iterator) is not openrouter.OpenRouterChatCompletionStreamingHandler:
            return None
        response = iterator.http_response
        if (
            type(response) is not httpx.Response
            or response.status_code != 200
            or response.is_stream_consumed
            or response.is_closed
            or response.request.method != "POST"
            or not response.request.url.path.endswith("/chat/completions")
        ):
            return None
        media_type = [
            part.strip() for part in response.headers.get("content-type", "").lower().split(";")
        ]
        if (
            media_type[0] != "text/event-stream"
            or media_type[1:] not in ([], ["charset=utf-8"], ['charset="utf-8"'])
            or response.headers.get("content-encoding", "identity").strip().lower() != "identity"
            or not isinstance(response.stream, httpx.AsyncByteStream)
            or isinstance(response.stream, _FinishBody)
        ):
            return None
        observer = _FinishSSE()
        response.stream = _FinishBody(response.stream, observer)
        return observer
    except Exception:
        # A private seam change only makes this observation unavailable.
        return None


class _Body(httpx.AsyncByteStream):
    def __init__(self, body: httpx.AsyncByteStream, owner: Handler) -> None:
        self.body = body
        self.owner = owner
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.body:
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                await self.body.aclose()
            finally:
                self.owner.finished()


class Forwarder(httpx.AsyncBaseTransport):
    def __init__(self, owner: Handler) -> None:
        self.owner = owner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        evidence = current.get()
        marker = request.extensions.pop("daemon_attempt", None)
        if isinstance(marker, tuple) and marker[0] is evidence:
            attempt = marker[1]
        else:
            attempt = Attempt()
            if evidence is not None:
                evidence.broken = True
        self.owner.started()
        try:
            assert self.owner.pool is not None
            response = await self.owner.pool.handle_async_request(request)
        except BaseException as exc:
            self.owner.finished()
            try:
                attempt.error = type(exc).__name__
                attempt.cause = type(exc.__cause__).__name__
                attempt.not_sent = isinstance(exc.__cause__, aiohttp.ClientConnectorError)
            except Exception:
                if evidence is not None:
                    evidence.broken = True
            raise
        try:
            attempt.status = response.status_code
        except Exception:
            if evidence is not None:
                evidence.broken = True
        response.stream = _Body(cast(httpx.AsyncByteStream, response.stream), self.owner)
        return response

    async def aclose(self) -> None:
        self.owner.borrower_closes += 1


class Handler(AsyncHTTPHandler):
    def __init__(self) -> None:
        self.pool: Any = None
        self.borrower_closes = 0
        self.active = 0
        self.drained = asyncio.Event()
        self.drained.set()
        super().__init__()

    def started(self) -> None:
        self.active += 1
        self.drained.clear()

    def finished(self) -> None:
        self.active -= 1
        if self.active == 0:
            self.drained.set()

    def create_client(
        self,
        timeout: float | httpx.Timeout | None,
        event_hooks: Mapping[str, list[Any]] | None,
        ssl_verify: Any = None,
        shared_session: Any = None,
    ) -> httpx.AsyncClient:
        # Keep LiteLLM's SSL, proxy, headers, cookies and retry configuration.
        hooks = {key: list(value) for key, value in (event_hooks or {}).items()}
        hooks.setdefault("request", []).append(self.observe_request)
        client = super().create_client(timeout, hooks, ssl_verify, shared_session)
        original = getattr(client, "_transport", None)
        transport_type = getattr(
            importlib.import_module("litellm.llms.custom_httpx.aiohttp_transport"),
            "LiteLLMAiohttpTransport",
        )
        if not isinstance(original, transport_type) or not callable(
            getattr(original, "client", None)
        ):
            raise RuntimeError("inference transport factory changed")
        if self.pool is None:
            self.pool = original
        forwarder = Forwarder(self)
        client._transport = forwarder
        return client

    @staticmethod
    async def observe_request(request: httpx.Request) -> None:
        evidence = current.get()
        if evidence is not None:
            try:
                attempt = Attempt()
                evidence.attempts.append(attempt)
                request.extensions["daemon_attempt"] = (evidence, attempt)
            except Exception:
                evidence.broken = True

    async def single_connection_post_request(
        self,
        url: str,
        client: httpx.AsyncClient,
        data: Any = None,
        json: Any = None,
        params: Any = None,
        headers: Any = None,
        stream: bool = False,
        content: Any = None,
    ) -> Any:
        try:
            return await super().single_connection_post_request(
                url=url,
                client=client,
                data=data,
                json=json,
                params=params,
                headers=headers,
                stream=stream,
                content=content,
            )
        except httpx.HTTPStatusError as failure:
            # LiteLLM's fresh-client retry is inside an except branch, so its
            # HTTPStatusError skips post()'s sibling response-body cleanup. The
            # borrower close deliberately cannot close the pool. Dispose only
            # this failed response, not concurrent successful streams. This
            # exception is a resource handle, never accounting evidence.
            try:
                await failure.response.aclose()
            except Exception:
                logger.warning("Failed inference retry response cleanup")
            raise

    async def shutdown(self) -> None:
        try:
            try:
                await asyncio.wait_for(self.drained.wait(), SHUTDOWN_DRAIN_TIMEOUT_S)
            except TimeoutError:
                logger.warning("Inference transport drain timed out (active=%d)", self.active)
        finally:
            # An abandoned body cannot retain the application pool forever.
            # Attempt the owning pool close even if borrower/client close fails.
            try:
                await asyncio.wait_for(self.close(), SHUTDOWN_CLOSE_TIMEOUT_S)
            finally:
                if self.pool is not None:
                    try:
                        await asyncio.wait_for(self.pool.aclose(), SHUTDOWN_CLOSE_TIMEOUT_S)
                    except BaseException:
                        logger.warning(
                            "Inference transport pool close failed (active=%d)", self.active
                        )
                        raise


_handlers: WeakKeyDictionary[asyncio.AbstractEventLoop, Handler | None] = WeakKeyDictionary()


def get_handler() -> Handler | None:
    """Startup qualification and lazy dispatch fallback; never refuse a call."""
    loop = asyncio.get_running_loop()
    if loop not in _handlers:
        try:
            for name in (
                "_create_async_transport",
                "_create_aiohttp_transport",
                "create_client",
                "single_connection_post_request",
            ):
                if not callable(getattr(AsyncHTTPHandler, name, None)):
                    raise RuntimeError("inference transport factory unavailable")
            handler = Handler()
            if not isinstance(handler.client._transport, Forwarder):
                raise RuntimeError("inference transport observer unavailable")
            _handlers[loop] = handler
        except Exception:
            # No exception text, headers, URLs or request content.
            logger.warning("Inference transport observation unavailable; using unknown accounting")
            _handlers[loop] = None
    return _handlers[loop]


async def shutdown() -> None:
    handler = _handlers.get(asyncio.get_running_loop())
    try:
        if handler is not None:
            await handler.shutdown()
    finally:
        _handlers.pop(asyncio.get_running_loop(), None)
