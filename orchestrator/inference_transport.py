"""Dispatch-local evidence over LiteLLM's own, loop-owned aiohttp pool.

Only a direct ClientConnectorError certifies that an attempt was not sent.
Exception metadata from the SDK is deliberately not evidence. Borrowers (including
LiteLLM's internal connection retry) cannot close the application-owned pool.
Private seam drift disables observation, never inference. Shutdown follows the
server/worker drain and additionally waits for outstanding response bodies.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import AsyncIterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast
from weakref import WeakKeyDictionary

import aiohttp
import httpx
from litellm.exceptions import UnsupportedParamsError
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from litellm import utils

logger = logging.getLogger(__name__)


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
        await self.drained.wait()
        await self.close()
        if self.pool is not None:
            await self.pool.aclose()


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
    if handler is not None:
        await handler.shutdown()
    _handlers.pop(asyncio.get_running_loop(), None)
