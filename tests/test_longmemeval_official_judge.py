"""Synthetic-only measurement tests; no provider, database or holdout content."""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.longmemeval import evaluate
from tests.longmemeval.official_judge import (
    build_official_prompt,
    official_label,
    score_dual_judgments,
)


@pytest.mark.parametrize(
    ("kind", "phrase"),
    [
        ("single-session-user", "only contains a subset"),
        ("single-session-assistant", "only contains a subset"),
        ("multi-session", "only contains a subset"),
        ("temporal-reasoning", "do not penalize off-by-one errors"),
        ("knowledge-update", "previous information along with an updated answer"),
        ("single-session-preference", "does not need to reflect all the points"),
    ],
)
def test_category_specific_prompt(kind: str, phrase: str) -> None:
    prompt = build_official_prompt(kind, "synthetic question", "gold", "reply", question_id="q")
    assert phrase in prompt
    assert "Question: synthetic question" in prompt
    assert "Model Response: reply" in prompt


def test_abstention_precedes_type_and_uses_upstream_substring() -> None:
    prompt = build_official_prompt("unknown", "Q", "A", "H", question_id="q_abs_extra")
    assert "unanswerable question" in prompt
    assert "Explanation: A" in prompt
    with pytest.raises(ValueError):
        build_official_prompt("unknown", "Q", "A", "H", question_id="q")


@pytest.mark.parametrize(
    ("raw", "label"), [(" YES\n", True), ("no", False), ("", False), ("not yes", True)]
)
def test_upstream_label_predicate_is_not_silently_tightened(raw: str, label: bool) -> None:
    assert official_label(raw) is label


def test_unobserved_official_and_abstention_are_null_not_fake_zero() -> None:
    report = score_dual_judgments([{"question_id": "q", "category": "MR", "judgment": "correct"}])
    assert report["official"]["strict_accuracy"] is None
    assert report["official"]["missing"] == 1
    assert report["abstention_subset"]["generic"]["total"] == 0
    assert report["abstention_subset"]["generic"]["strict_accuracy"] is None
    assert report["paired"]["strict_disagreement_rate"] is None


def test_paired_score_keeps_partial_strict_and_abs_as_overlapping_subset() -> None:
    report = score_dual_judgments(
        [
            {
                "question_id": "q",
                "category": "TR",
                "judgment": "partially_correct",
                "official_judgment": "correct",
            },
            {
                "question_id": "q_abs",
                "category": "TR",
                "judgment": "correct",
                "official_judgment": "incorrect",
                "official_judge_raw_output": "unclear",
                "official_judge_finish_reason": "length",
            },
        ]
    )
    assert report["official"]["strict_accuracy"] == 0.5
    assert report["official_by_category"]["TR"]["total"] == 2
    assert report["abstention_subset"]["official"]["total"] == 1
    assert report["abstention_subset"]["official"]["strict_accuracy"] == 0
    assert report["paired"]["strict_disagreements"] == 2
    assert report["paired"]["strict_disagreement_rate"] == 1
    assert report["official_unexpected_output_count"] == 1
    assert report["official_length_finish_count"] == 1


@pytest.mark.asyncio
async def test_official_call_preserves_raw_output_usage_and_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = " yes\n"
    response = {
        "choices": [{"message": {"content": raw}, "finish_reason": "length"}],
        "system_fingerprint": "fp_synthetic",
        "model": "served-snapshot",
        "usage": {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100},
    }

    async def call(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["bm_call_key"] == "official_judge"
        assert kwargs["max_tokens"] == 10
        assert kwargs["temperature"] == 0
        evaluate._capture_benchmark_metadata(response, key="official_judge")
        return response

    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", call)
    result = await evaluate.judge_answer_official(
        "Q", "H", "A", question_id="q_abs", question_type="multi-session"
    )
    assert result["official_judgment"] == "correct"
    assert result["official_judge_raw_output"] == raw
    assert result["official_judge_finish_reason"] == "length"
    assert result["official_judge_fingerprint"] == "fp_synthetic"
    assert result["official_judge_model"] == "served-snapshot"
    assert result["official_judge_usage"]["completion_tokens"] == 10


@pytest.mark.asyncio
async def test_official_missing_response_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", AsyncMock(return_value=None))
    with pytest.raises(evaluate.BenchmarkProviderError):
        await evaluate.judge_answer_official(
            "Q", "H", "A", question_id="q", question_type="multi-session"
        )


def test_generic_abs_placeholder_is_preserved_but_separate_subset_is_measured() -> None:
    from orchestrator.eval.fact_harness import build_score_payload

    score = build_score_payload([{"question_id": "q_abs", "category": "KU", "judgment": "correct"}])
    assert score["accuracy"]["ABS"] == 0  # unchanged historical metric
    assert "ABS" not in score["category_counts"]
    assert score["dual_judgments"]["abstention_subset"]["generic"]["strict_accuracy"] == 1


@pytest.mark.parametrize(
    ("kind", "digest"),
    [
        ("single-session-user", "b772fe65f857abfaf9888f2b111b8b30f2f4229394bd8f3c1cc55057a2212768"),
        (
            "single-session-assistant",
            "b772fe65f857abfaf9888f2b111b8b30f2f4229394bd8f3c1cc55057a2212768",
        ),
        ("multi-session", "b772fe65f857abfaf9888f2b111b8b30f2f4229394bd8f3c1cc55057a2212768"),
        ("temporal-reasoning", "cfc401722911f6d47a1107392425e9647b521c43674f65e3418a60044d9e8595"),
        ("knowledge-update", "50ead92bdefe84ac00b0309876582631a7cb39dfe4c409f0849ae2b1430ca53a"),
        (
            "single-session-preference",
            "90fc3b6e71932479f180efbc1ae975402bede42c4b55bff59b776fc9e52a3bec",
        ),
        ("abstention", "d3ee92f3a3023d94fd0af4a1dc43338c03905eeee03be2f709df911a1e04032b"),
    ],
)
def test_prompts_match_pinned_upstream_digests(kind: str, digest: str) -> None:
    # Primary independently compared every case byte-for-byte with the pinned
    # public upstream function before freezing these synthetic golden hashes.
    prompt = build_official_prompt(
        kind, "Q", "A", "H", question_id="q_abs" if kind == "abstention" else "q"
    )
    assert hashlib.sha256(prompt.encode()).hexdigest() == digest


def test_generic_prompt_is_byte_unchanged() -> None:
    # Independently extracted the pre-Phase-1 f-string at a85fb723 with Q/H/A.
    prompt = evaluate.build_generic_judge_prompt("Q", "H", "A")
    assert hashlib.sha256(prompt.encode()).hexdigest() == (
        "da1fc00ff2a5b44efc8f219f9722f993040c21ee9b601758e09eaab249325672"
    )


@pytest.mark.asyncio
async def test_official_route_uses_judge_pin_and_disables_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluate, "BENCHMARK_JUDGE_MODEL", "openrouter/synthetic-judge")
    monkeypatch.setattr(evaluate, "BENCHMARK_ANSWER_MODEL", "openrouter/synthetic-reader")
    call = AsyncMock(
        return_value={"choices": [{"message": {"content": "yes"}, "finish_reason": "stop"}]}
    )
    monkeypatch.setattr("litellm.acompletion", call)
    await evaluate.judge_answer_official(
        "Q", "H", "A", question_id="q", question_type="multi-session"
    )
    params = call.call_args.kwargs
    assert params["model"] == "openrouter/synthetic-judge"
    assert params["seed"] == 42
    assert params["num_retries"] == 0
    assert params["max_tokens"] == 10
    assert params["extra_body"]["provider"] == {"order": ["azure"], "allow_fallbacks": False}
