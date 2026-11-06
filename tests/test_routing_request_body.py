"""Real pinned LiteLLM mapping + HTTP serialization; no socket can connect.

Run separately on Python 3.11+ as well as under pytest. Only HTTP transports are
faked, not completion, optional-parameter mapping or request transformation.
"""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import json
import os
import subprocess
import sys
from typing import Any

import pytest

from tests.test_routing_litellm_contract import (
    REPO_ROOT,
    _combinations,
    _install_socket_guard,
    _load_config,
)


async def _measure() -> dict[str, Any]:
    attempts: list[str] = []
    _install_socket_guard(attempts)
    import httpx
    import litellm
    from litellm.llms.custom_httpx.aiohttp_transport import LiteLLMAiohttpTransport

    from orchestrator.litellm_runtime import require_local_model_cost_map

    require_local_model_cost_map()
    bodies: list[dict[str, Any]] = []

    async def fake_http(self: Any, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "gen-offline",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def refuse_sync(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unexpected synchronous HTTP request")

    rows = []
    # Use real, newly constructed httpx clients so no cached client or provider
    # mock can short-circuit the serializer. Guard every real socket as well.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_http)
        patch.setattr(LiteLLMAiohttpTransport, "handle_async_request", fake_http)
        patch.setattr(httpx.HTTPTransport, "handle_request", refuse_sync)
        async with httpx.AsyncClient() as client:
            for profile, model, effort, params in _combinations():
                before = len(bodies)
                provider = {
                    "only": ["offline-fixture"],
                    "order": ["offline-fixture"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                    "data_collection": "deny",
                    "zdr": True,
                    "max_price": {"prompt": 1, "completion": 1},
                }
                # The adapter has already run, just as after production merges
                # its approved transport block. Preserve that block on the wire.
                params = {
                    **params,
                    "extra_body": {"provider": provider, **params.get("extra_body", {})},
                }
                await litellm.acompletion(
                    model=model,
                    messages=[{"role": "user", "content": "fictional offline probe"}],
                    api_base="https://openrouter.ai/api/v1",
                    api_key="offline-fixture-not-a-credential",
                    num_retries=0,
                    timeout=10,
                    client=client,
                    **params,
                )
                assert len(bodies) == before + 1
                body = bodies[-1]
                assert body["provider"] == provider
                rows.append({"profile": profile, "model": model, "effort": effort, "body": body})
    return {"offline_attempts": attempts, "rows": rows}


@pytest.fixture(scope="module")
def request_bodies() -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.test_routing_request_body"],
        cwd=REPO_ROOT,
        env={**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_MODE": "PRODUCTION"},
        text=True,
        capture_output=True,
        timeout=240,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_every_production_and_declared_effort_reaches_the_wire(
    request_bodies: dict[str, Any],
) -> None:
    assert request_bodies["offline_attempts"] == []
    rows = request_bodies["rows"]
    config = _load_config()
    expected = {
        (profile["profile"], model)
        for profile in config["profiles"]
        for group in profile["groups"]
        for model in group["models"]
    }
    assert {
        (row["profile"], row["model"]) for row in rows if not row["profile"].startswith("declared:")
    } == expected
    assert any(row["effort"] == "max" for row in rows)
    assert any(row["effort"] == "none" for row in rows)
    for row in rows:
        body = row["body"]
        assert body["model"] == row["model"].removeprefix("openrouter/")
        assert "reasoning_effort" not in body
        assert "extra_body" not in body
        assert "allowed_openai_params" not in body
        assert body["reasoning"] == {"effort": "xhigh" if row["effort"] == "max" else row["effort"]}
        if row["profile"] == "council":
            assert body["include_reasoning"] is True
        else:
            assert "include_reasoning" not in body


if __name__ == "__main__":
    with redirect_stdout(sys.stderr):
        payload = asyncio.run(_measure())
    print(json.dumps(payload))
