"""Regression test for the MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL constant.

Covers issue #10: the constant was missing from `orchestrator.prompts`, which
caused `tests/benchmark_longmemeval/abstention_sweep.py:16` to fail at import.
This test asserts:

1. The constant is importable from `orchestrator.prompts`.
2. The text matches the verbatim text from the archived Oracle disposition.
3. ``DAEMON_PROMPT_VERSION`` was bumped from 3 to 4 in the same change.
4. The shared ``apply_guardrail`` helper (used by both the benchmark sweep and
   this test) inserts the guardrail before the ``\\n\\nAnswer:`` marker and is
   idempotent when the guardrail is already present.

The helper lives in ``tests/benchmark_longmemeval/_guardrail.py`` so that this
test can import it without dragging the rest of the abstention_sweep dependency
graph (which includes ``tests.longmemeval.evaluate`` and
``orchestrator.eval.fact_harness``) into the test process.
"""

from __future__ import annotations

from orchestrator.prompts import (
    DAEMON_PROMPT_VERSION,
    MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL,
)
from tests.benchmark_longmemeval._guardrail import apply_guardrail


def test_guardrail_constant_is_importable() -> None:
    """The constant must be importable from orchestrator.prompts (issue #10)."""
    assert isinstance(MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL, str)
    assert MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL.strip() == MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL


def test_guardrail_constant_matches_archived_text() -> None:
    """The verbatim text must match the archived Oracle disposition."""
    expected = (
        "When a question depends on retrieved memory or recent context, "
        "treat that memory as evidence rather than permission to guess.\n"
        "If the available memory does not directly answer the question, "
        "say that you do not know or that the available memory is insufficient.\n"
        "Do not fill gaps with nearby but non-answering details, inferred "
        "timelines, or best guesses.\n"
        "Only answer confidently when the memory evidence directly supports "
        "the answer."
    )
    assert MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL == expected


def test_daemon_prompt_version_was_bumped() -> None:
    """The version bump from 3 to 4 accompanies the guardrail addition."""
    assert DAEMON_PROMPT_VERSION >= 4


def test_apply_guardrail_inserts_before_answer_marker() -> None:
    """The helper inserts the guardrail before the final Answer marker."""
    base = "Some prior system prompt.\n\nAnswer: the model's response"
    result = apply_guardrail(base)
    assert MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL.strip() in result
    assert result.startswith("Some prior system prompt.\n\n")
    assert result.endswith("the model's response")


def test_apply_guardrail_is_idempotent() -> None:
    """The helper is idempotent when the guardrail is already present."""
    base_with = "Some prior prompt.\n\n" + MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL + "\n\nAnswer: text"
    assert apply_guardrail(base_with) == base_with


def test_apply_guardrail_appends_when_no_answer_marker() -> None:
    """The helper appends the guardrail at the end when no Answer marker is present."""
    base_no_marker = "Some prior prompt with no answer marker."
    assert (
        apply_guardrail(base_no_marker)
        == base_no_marker + "\n\n" + MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL
    )
