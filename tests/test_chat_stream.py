from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_chat_stream_emits_done(monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ["LLM_PROVIDER"] = "openrouter"
    os.environ["OPENROUTER_API_KEY"] = "test"
    # Ensure the auth gate is disabled for this test even if a local `.env`
    # sets DAEMON_API_KEY.
    os.environ["DAEMON_API_KEY"] = ""

    from orchestrator import config as config_mod

    config_mod.get_settings.cache_clear()

    async def fake_acompletion(*args, **kwargs):
        async def gen():
            yield {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}
            yield {"choices": [{"delta": {"content": " world"}, "finish_reason": None}]}
            yield {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }

        return gen()

    from orchestrator import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod.litellm, "acompletion", fake_acompletion)

    from orchestrator.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/chat", json={"message": "hello"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        saw_done = False
        async for line in resp.aiter_lines():
            if line.strip() == "event: done":
                saw_done = True
                break

        assert saw_done
