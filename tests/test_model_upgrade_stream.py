from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from orchestrator.config import Settings
from scripts import model_routing_followup as follow
from scripts import model_upgrade_stream as stream


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_reason", ["stop", "content_filter", "length", "transport_error", "admission_error"]
)
async def test_production_probe_observes_tool_roundtrip_and_settles_each_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    finish_reason: str,
) -> None:
    fixtures = follow.load_fixtures(follow.DEFAULT_FIXTURES_PATH)
    case = fixtures.by_id()["D05"]
    route = SimpleNamespace(
        route_id="test-eu",
        model="openrouter/openai/gpt-6.1-sol",
        endpoint="https://openrouter.ai/api/v1",
        transport=SimpleNamespace(provider_only=("azure/eu",)),
    )
    attempt = SimpleNamespace(
        attempt_id="stream-high",
        candidate_label="sol61",
        case_id="D05",
        effort="high",
        max_calls=2,
        max_tool_calls=1,
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    ledger = {"spent": 0}
    state: dict[str, Any] = {"attempts": {}, "account_ledger_baseline": {"exposure_microusd": 0}}
    ctx = SimpleNamespace(
        cases={"D05": case},
        routes={"sol61": route},
        state=state,
        state_path=private / "state.json",
        account=uuid.uuid4(),
        pool=None,
        service=SimpleNamespace(resolve=None),
        period="2026-09",
        schedule=(attempt,),
        profile=replace(follow.DEFAULT_PROFILE, total_attempts=1, dispatch_bound=2),
        account_ceiling_microusd=25_000_000,
    )

    async def resolve(_account: Any) -> object:
        return object()

    async def exposure(*_args: Any) -> Any:
        return follow.reliability.LedgerExposure(ledger["spent"], 0, 0)

    async def preflight(_ctx: Any) -> None:
        pass

    ctx.service.resolve = resolve
    monkeypatch.setattr(follow, "_attempt_preflight", preflight)
    monkeypatch.setattr(follow, "_self_check", lambda _ctx: None)
    monkeypatch.setattr(follow, "ensure_funded_window", lambda *_args: None)

    def verify(*_args: Any) -> int:
        if finish_reason == "admission_error":
            raise follow.PolicyViolation("synthetic admission rejection")
        return 50

    monkeypatch.setattr(follow, "verify_candidate", verify)
    monkeypatch.setattr(follow, "ceiling_bound", lambda _r: 50)
    monkeypatch.setattr(follow.reliability, "read_ledger_exposure", exposure)
    monkeypatch.setattr(
        stream,
        "get_settings",
        lambda: Settings(openrouter_api_key="synthetic-test-only"),
    )
    monkeypatch.setattr(
        stream.model_routing,
        "current_routing",
        lambda: SimpleNamespace(
            selected_route_id=route.route_id,
            selected_model=route.model,
        ),
    )
    scopes = []

    @asynccontextmanager
    async def account(*_args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        assert kwargs["expected_period"] == "2026-09"
        scope = SimpleNamespace(settled={})
        scopes.append(scope)
        try:
            yield scope
        finally:
            scope.settled[uuid.uuid4()] = 10
            ledger["spent"] += 10

    monkeypatch.setattr(stream.compute_runtime, "account_compute", account)
    seen = []
    closed = []

    async def guarded(**params: Any) -> Any:
        checkpoint = json.loads(ctx.state_path.read_text())
        persisted = checkpoint["attempts"][attempt.attempt_id]["calls"][-1]
        assert persisted["status"] == "dispatched_unknown"
        assert persisted["account_charge_microusd"] is None
        assert params["reasoning_effort"] == "high" and params["max_tokens"] == 4096
        assert params["_route_id"] == route.route_id and 0 < params["_dispatch_timeout_s"] <= 90
        seen.append(params)
        number = len(seen)

        if finish_reason == "transport_error":
            raise RuntimeError("synthetic transport failure")

        async def chunks() -> Any:
            try:
                if number == 1:
                    yield {
                        "model": "openai/gpt-6.1-sol",
                        "provider": "Azure",
                        "choices": [
                            {
                                "delta": {
                                    "reasoning_details": [
                                        {"type": "reasoning.encrypted", "data": "opaque-test"}
                                    ],
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "policy-call",
                                            "function": {
                                                "name": "fetch_policy",
                                                "arguments": '{"id":"POL-18"}',
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                    }
                else:
                    yield {
                        "model": "openai/gpt-6.1-sol",
                        "provider": "Azure",
                        "choices": [
                            {
                                "delta": {
                                    "content": "Five calendar days; storm closure suspends inspections; restart unspecified."
                                },
                                "finish_reason": finish_reason,
                            }
                        ],
                    }
                yield {
                    "model": "openai/gpt-6.1-sol",
                    "provider": "Azure",
                    "choices": [],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "cost": 0.00001},
                }
            finally:
                closed.append(number)

        return chunks()

    monkeypatch.setattr(stream.compute_runtime, "guarded_completion", guarded)
    if finish_reason in {"transport_error", "admission_error"}:
        with pytest.raises(follow.FollowupError, match="investigation required"):
            await stream.run_stream_attempt(ctx, attempt)  # type: ignore[arg-type]
        row = state["attempts"][attempt.attempt_id]
        assert row["status"] == "stopped" and row["stop"]
        assert row["final_response"] is None
        assert len(seen) == (1 if finish_reason == "transport_error" else 0)
        with pytest.raises(follow.FollowupError, match="requires investigation"):
            follow.phase_admission(
                state, (cast(follow.Attempt, attempt),), "diagnostic", profile=ctx.profile
            )
        return
    await stream.run_stream_attempt(ctx, attempt)  # type: ignore[arg-type]
    row = state["attempts"][attempt.attempt_id]
    assert len(seen) == len(scopes) == 2 and closed == [1, 2]
    assert [c["account_charge_microusd"] for c in row["calls"]] == [10, 10]
    assert ledger["spent"] == 20 and row["tool_call_count"] == 1
    assert row["stream_observation"] == {"reasoning_details_chunks": 1, "continuation_details": 0}
    assert "POL-18" in seen[1]["messages"][-1]["content"]
    # The production loop currently discards opaque metadata. The probe reports
    # this difference instead of preserving it on the app's behalf.
    assert "reasoning_details" not in seen[1]["messages"][-2]
    assert row["status"] == ("completed" if finish_reason == "stop" else "failed")
    assert (row["final_response"] is not None) == (finish_reason == "stop")
    if finish_reason != "stop":
        assert row["task_failures"][0]["kind"] == "truncated_response"
    follow.require_exclusive(state, await exposure())


@pytest.mark.asyncio
async def test_injected_dispatch_preserves_production_defaults_and_closes_stream() -> None:
    from orchestrator.config import ProviderConfig
    from orchestrator.tools.completion import completion_with_tools
    from orchestrator.tools.registry import ToolRegistry

    captured = []
    closed = []

    async def dispatch(**params: Any) -> Any:
        captured.append(params)

        async def chunks() -> Any:
            try:
                yield {"choices": [{"delta": {"content": "synthetic answer"}}]}
            finally:
                closed.append(True)

        return chunks()

    events = [
        e
        async for e in completion_with_tools(
            Settings(),
            ProviderConfig(
                name="openrouter", model="openrouter/openai/gpt-6-sol", requires_auth=False
            ),
            [{"role": "user", "content": "synthetic task"}],
            ToolRegistry(),
            actual_model="openrouter/openai/gpt-6-sol",
            completion_dispatch=dispatch,
        )
    ]
    assert captured[0]["stream"] is True and "max_tokens" not in captured[0]
    assert captured[0]["model"] == "openrouter/openai/gpt-6-sol"
    assert [e["type"] for e in events] == ["content_delta", "content_done", "done"]
    assert closed == [True]
