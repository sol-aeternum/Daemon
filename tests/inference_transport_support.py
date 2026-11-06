"""Real owned-loopback peers for the production inference transport tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, cast

import litellm
import pytest


@pytest.fixture(autouse=True)
def offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect
    original_resolve = socket.getaddrinfo

    def connect(sock: socket.socket, address: Any) -> Any:
        assert isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}, address
        return original_connect(sock, address)

    def resolve(host: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(host, bytes):
            host = host.decode("ascii")
        assert host in {"127.0.0.1", "::1", "send-evidence.invalid"}, host
        if host == "send-evidence.invalid":
            # Never ask the host's potentially external DNS resolver. The other
            # cases use real sockets; this case qualifies error mapping only.
            raise socket.gaierror(socket.EAI_NONAME, "fictional local DNS failure")
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)


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
            if self.mode in {"close", "read_timeout"}:
                if self.mode == "read_timeout":
                    await self.release.wait()
                return
            raise AssertionError("unsupported peer mode")
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
        result = cast(Any, await asyncio.wait_for(litellm.acompletion(**call), timeout=4))
        if stream:
            try:
                async for _ in result:
                    pass
            finally:
                await result.aclose()
        return None
    except BaseException as exc:
        return exc
