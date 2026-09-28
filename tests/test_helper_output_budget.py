"""Bounded helper outputs are complete or explicitly refused.

PR review #2 confirmed that the automatic reasoning presets can emit far more
than the helpers' tiny 24/50/100-token caps, so an automatic verdict could be
truncated by its own output bound and still look valid: a visible ``YES``
prefix, a plausible title fragment, a bare ``NO``. The approved fix gives the
automatic titles/contradiction/entity calls the 4096-token helper output
budget (subject to account admission), keeps the evaluated effort presets
unchanged, and preserves the historic caps and semantics on explicit pins and
benchmark calls.

These tests pin the three layers that make that safe:

1. **Budget**: automatic calls ask for ``4096``; explicit title/entity pins
   keep 24/100 and the benchmark contradiction keeps 50.
2. **Truncation honesty**: a response cut off by ``finish_reason="length"`` is
   never accepted as a verdict. Titles raise a typed error for worker
   observability; dedup stays advisory but returns a distinguishable error
   reason instead of an indistinguishable legitimate ``NO``; entity checks
   completeness before a visible ``YES`` fragment can confirm a merge.
3. **Exact pins inside automatic scopes**: an explicit model travels with
   ``_exact_model`` so the guard honours it for an automatic account scope
   without bypassing qualification, entitlement or account limits.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator import model_routing
from orchestrator.memory.completion import (
    EMPTY_RESPONSE_REASON,
    TRUNCATED_RESPONSE_REASON,
    read_completeness,
)
from orchestrator.memory.dedup import (
    AUTOMATIC_CONTRADICTION_MAX_TOKENS,
    BENCHMARK_CONTRADICTION_MAX_TOKENS,
    check_contradiction,
)
from orchestrator.memory.entities import (
    AUTOMATIC_CONFIRMATION_MAX_TOKENS,
    EXPLICIT_CONFIRMATION_MAX_TOKENS,
    CandidateMention,
    EntityResolution,
    confirm_merge_llm,
)
from orchestrator.memory.titles import (
    AUTOMATIC_TITLE_MAX_TOKENS,
    EXPLICIT_TITLE_MAX_TOKENS,
    IncompleteTitleResponse,
    generate_conversation_title,
)
from test_model_routing import GLM, LUNA, dispatch_fixture, last_kwargs, named_route


class CompletenessResponse:
    """A litellm-shaped reply with a controllable finish reason."""

    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self._content = content
        self._finish_reason = finish_reason
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]

    def model_dump(self) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {"content": self._content},
                    "finish_reason": self._finish_reason,
                }
            ]
        }


def _entity_resolution() -> EntityResolution:
    return EntityResolution(
        mention=CandidateMention(text="Mike", normalized_key="mike", context="my brother Mike"),
        canonical_name="Michael",
        similarity=0.72,
        merge_decision="ambiguous",
    )


# ---------------------------------------------------------------------------
# 1. The approved helper output budget, and the preserved historic caps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_automatic_title_asks_for_the_helper_output_budget() -> None:
    with patch("orchestrator.memory.titles.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("Understanding The Daemon Project")
        await generate_conversation_title(
            [{"role": "user", "content": "Tell me about the daemon project"}]
        )

    assert llm.call_args.kwargs["max_tokens"] == AUTOMATIC_TITLE_MAX_TOKENS
    assert "model" not in llm.call_args.kwargs
    assert "_exact_model" not in llm.call_args.kwargs


@pytest.mark.asyncio
async def test_explicit_title_pin_keeps_historic_cap_and_exact_flag() -> None:
    with patch("orchestrator.memory.titles.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("Understanding The Daemon Project")
        await generate_conversation_title(
            [{"role": "user", "content": "Tell me about the daemon project"}],
            model="openrouter/test/explicit-model",
        )

    sent = llm.call_args.kwargs
    assert sent["max_tokens"] == EXPLICIT_TITLE_MAX_TOKENS
    assert sent["temperature"] == 0.1
    assert sent["model"] == "openrouter/test/explicit-model"
    assert sent["_exact_model"] is True


@pytest.mark.asyncio
async def test_automatic_contradiction_asks_for_the_helper_output_budget() -> None:
    with patch("orchestrator.memory.dedup.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("NO. The facts agree.")
        await check_contradiction("User drives a Tesla", "User drives a Tesla")

    sent = llm.call_args.kwargs
    assert sent["max_tokens"] == AUTOMATIC_CONTRADICTION_MAX_TOKENS
    assert "model" not in sent
    assert "_exact_model" not in sent


@pytest.mark.asyncio
async def test_benchmark_contradiction_keeps_historic_cap_and_exact_flag() -> None:
    with patch("orchestrator.memory.dedup.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("NO")
        await check_contradiction("User likes Python", "User loves Python", benchmark_mode=True)

    sent = llm.call_args.kwargs
    assert sent["max_tokens"] == BENCHMARK_CONTRADICTION_MAX_TOKENS
    assert sent["seed"] == 42
    assert sent["model"] == "openrouter/deepseek/deepseek-chat-v3-5"
    assert sent["_exact_model"] is True


@pytest.mark.asyncio
async def test_automatic_entity_confirmation_asks_for_the_helper_output_budget() -> None:
    with patch("orchestrator.memory.entities.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("NO: different people entirely.")
        await confirm_merge_llm(_entity_resolution())

    sent = llm.call_args.kwargs
    assert sent["max_tokens"] == AUTOMATIC_CONFIRMATION_MAX_TOKENS
    assert "model" not in sent
    assert "_exact_model" not in sent


@pytest.mark.asyncio
async def test_explicit_entity_pin_keeps_historic_cap_and_exact_flag() -> None:
    with patch("orchestrator.memory.entities.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("NO: different people entirely.")
        await confirm_merge_llm(_entity_resolution(), model="openrouter/test/explicit-model")

    sent = llm.call_args.kwargs
    assert sent["max_tokens"] == EXPLICIT_CONFIRMATION_MAX_TOKENS
    assert sent["temperature"] == 0.1
    assert sent["model"] == "openrouter/test/explicit-model"
    assert sent["_exact_model"] is True


# ---------------------------------------------------------------------------
# 2. Truncated and empty responses are never valid verdicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncated_automatic_title_raises_for_worker_observability() -> None:
    """A bound-cut fragment must fail loudly, not persist a partial title."""
    with patch("orchestrator.memory.titles.guarded_completion") as llm:
        llm.return_value = CompletenessResponse(
            "Understanding The Daemon Pro", finish_reason="length"
        )
        with pytest.raises(IncompleteTitleResponse) as refused:
            await generate_conversation_title(
                [{"role": "user", "content": "Tell me about the daemon project"}]
            )

    # The typed error carries a content-free reason only.
    assert TRUNCATED_RESPONSE_REASON in str(refused.value)
    assert "Understanding" not in str(refused.value)


@pytest.mark.asyncio
async def test_empty_automatic_title_raises_for_worker_observability() -> None:
    with patch("orchestrator.memory.titles.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("")
        with pytest.raises(IncompleteTitleResponse) as refused:
            await generate_conversation_title(
                [{"role": "user", "content": "Tell me about the daemon project"}]
            )

    assert EMPTY_RESPONSE_REASON in str(refused.value)


@pytest.mark.asyncio
async def test_explicit_title_pin_keeps_historic_fragment_semantics() -> None:
    """An explicit pin is a caller-owned benchmark-style choice: no raise."""
    with patch("orchestrator.memory.titles.guarded_completion") as llm:
        llm.return_value = CompletenessResponse(
            "Understanding The Daemon Pro", finish_reason="length"
        )
        title = await generate_conversation_title(
            [{"role": "user", "content": "Tell me about the daemon project"}],
            model="openrouter/test/explicit-model",
        )

    assert title


@pytest.mark.asyncio
async def test_truncated_visible_yes_is_not_a_contradiction_detection() -> None:
    """A cut-off ``YES`` fragment must not read as a legitimate verdict."""
    with patch("orchestrator.memory.dedup.guarded_completion") as llm:
        llm.return_value = CompletenessResponse(
            "YES. Fact B directly contradicts", finish_reason="length"
        )
        detected, explanation = await check_contradiction("Fact A", "Fact B")

    assert detected is False
    # Distinguishable from a legitimate NO: an explicit error reason.
    assert explanation.startswith("error:")
    assert TRUNCATED_RESPONSE_REASON in explanation
    assert "Fact B" not in explanation  # content-free reason only


@pytest.mark.asyncio
async def test_empty_contradiction_reply_is_distinguishable_from_no() -> None:
    with patch("orchestrator.memory.dedup.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("")
        detected, explanation = await check_contradiction("Fact A", "Fact B")

    assert detected is False
    assert explanation.startswith("error:")
    assert EMPTY_RESPONSE_REASON in explanation


@pytest.mark.asyncio
async def test_benchmark_contradiction_keeps_historic_advisory_silence() -> None:
    with patch("orchestrator.memory.dedup.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("YES. Fact B contradicts", finish_reason="length")
        detected, explanation = await check_contradiction("Fact A", "Fact B", benchmark_mode=True)

    assert detected is False
    assert explanation == ""


@pytest.mark.asyncio
async def test_truncated_entity_yes_is_recognized_before_accepting_merge() -> None:
    """Completeness is checked before verdict parsing: no fragment confirms."""
    with patch("orchestrator.memory.entities.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("YES: Same person, M", finish_reason="length")
        confirmed, explanation = await confirm_merge_llm(_entity_resolution())

    assert confirmed is False
    assert TRUNCATED_RESPONSE_REASON in explanation
    assert "Same person" not in explanation  # content-free reason only


@pytest.mark.asyncio
async def test_empty_entity_reply_reports_empty_reason() -> None:
    with patch("orchestrator.memory.entities.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("")
        confirmed, explanation = await confirm_merge_llm(_entity_resolution())

    assert confirmed is False
    assert EMPTY_RESPONSE_REASON in explanation


@pytest.mark.asyncio
async def test_complete_entity_yes_still_confirms() -> None:
    with patch("orchestrator.memory.entities.guarded_completion") as llm:
        llm.return_value = CompletenessResponse("YES: Same person under two names.")
        confirmed, explanation = await confirm_merge_llm(_entity_resolution())

    assert confirmed is True
    assert explanation.startswith("YES")


# ---------------------------------------------------------------------------
# 3. Shared completeness reader contract
# ---------------------------------------------------------------------------


def test_read_completeness_accepts_both_response_styles() -> None:
    attribute_style = CompletenessResponse("done")
    dumped = {
        "choices": [
            {
                "message": {"content": "done"},
                "finish_reason": "stop",
            }
        ]
    }

    class DumpOnlyResponse:
        def model_dump(self) -> dict[str, Any]:
            return dumped

    for response in (attribute_style, DumpOnlyResponse()):
        completeness = read_completeness(response)
        assert completeness.complete
        assert completeness.content == "done"


def test_read_completeness_marks_missing_choice_as_empty() -> None:
    completeness = read_completeness({"choices": []})
    assert not completeness.complete
    assert completeness.reason == EMPTY_RESPONSE_REASON


def test_truncation_wins_over_emptiness() -> None:
    completeness = read_completeness(CompletenessResponse("", finish_reason="length"))
    assert not completeness.complete
    assert completeness.reason == TRUNCATED_RESPONSE_REASON


# ---------------------------------------------------------------------------
# 4. Exact pins inside an automatic account scope, through the real guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_title_pin_is_honoured_inside_an_automatic_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_exact_model`` makes an automatic scope honour an explicit pin."""
    provider = AsyncMock(return_value=CompletenessResponse("Understanding The Daemon Project"))

    with dispatch_fixture(
        monkeypatch,
        [named_route(GLM)],
        routing=None,
        provider=provider,
        auto_route=True,
    ) as (service, _provider, _scope):
        title = await generate_conversation_title(
            [{"role": "user", "content": "Tell me about the daemon project"}],
            model=GLM,
        )

    assert title
    sent = last_kwargs(provider)
    # The pin itself reached the transport, at the historic explicit cap.
    # Dispatching the pinned model inside an ``auto_route`` scope is itself the
    # observable proof that the guard honoured the exact selection.
    assert sent["model"] == GLM
    assert sent["max_tokens"] == EXPLICIT_TITLE_MAX_TOKENS
    # The exact selection still went through qualification and the ledger.
    assert service.reserve.await_count == 1
    assert service.settle.await_count == 1


@pytest.mark.asyncio
async def test_automatic_title_dispatches_luna_at_the_helper_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The automatic title call dispatches on the background candidate at 4096."""
    provider = AsyncMock(return_value=CompletenessResponse("Understanding The Daemon Project"))
    routes = [named_route(LUNA)]

    with dispatch_fixture(
        monkeypatch, routes, routing=None, provider=provider, auto_route=True
    ) as (service, _provider, _scope):
        assert model_routing.load_model_routing().profile("background").ranked_models() == (LUNA,)
        title = await generate_conversation_title(
            [{"role": "user", "content": "Tell me about the daemon project"}]
        )

    assert title
    sent = last_kwargs(provider)
    assert sent["model"] == LUNA
    assert sent["max_tokens"] == AUTOMATIC_TITLE_MAX_TOKENS
    assert "_exact_model" not in sent
    assert service.reserve.await_count == 1
    assert service.settle.await_count == 1
