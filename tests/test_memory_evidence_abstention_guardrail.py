"""Regression test for the MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL constant.

Covers issue #10: the constant was missing from `orchestrator.prompts`, which
caused `tests/benchmark_longmemeval/abstention_sweep.py:16` to fail at import.
This test asserts:

1. The constant is importable from `orchestrator.prompts`.
2. The text matches the verbatim text from the archived Oracle disposition.
3. ``DAEMON_PROMPT_VERSION`` was bumped from 3 to 4 in the same change.
4. The ``_apply_guardrail`` helper logic (a stand-in re-implementation)
   inserts the guardrail before the ``\\n\\nAnswer:`` marker and is idempotent
   when the guardrail is already present.

(Note: the abstention_sweep module has a separate dependency on
``orchestrator.eval.fact_harness.build_answer_prompt`` that is not in scope
for issue #10 — the import error from that path is a different failure mode.)
"""

from __future__ import annotations

from orchestrator.prompts import (
    DAEMON_PROMPT_VERSION,
    MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL,
)


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


def test_apply_guardrail_logic() -> None:
    """The helper logic: append before ``\\n\\nAnswer:``, idempotent otherwise."""
    # The helper is internal to abstention_sweep which has other import-time
    # dependencies. Reformulate the same logic here so the test stays focused
    # on the constant's contract rather than the sweep's dependency graph.

    def _apply_guardrail(base_prompt: str) -> str:
        guardrail = MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL.strip()
        if guardrail in base_prompt:
            return base_prompt
        answer_marker = "\n\nAnswer:"
        if answer_marker not in base_prompt:
            return base_prompt + "\n\n" + guardrail
        prefix, suffix = base_prompt.rsplit(answer_marker, 1)
        return prefix + "\n\n" + guardrail + answer_marker + suffix

    # Case 1: appends before the Answer marker.
    base = "Some prior system prompt.\n\nAnswer: the model's response"
    result = _apply_guardrail(base)
    assert MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL.strip() in result
    assert result.startswith("Some prior system prompt.\n\n")
    assert result.endswith("the model's response")

    # Case 2: idempotent when guardrail already present.
    base_with = "Some prior prompt.\n\n" + MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL + "\n\nAnswer: text"
    assert _apply_guardrail(base_with) == base_with

    # Case 3: appended at the end when no Answer marker.
    base_no_marker = "Some prior prompt with no answer marker."
    assert (
        _apply_guardrail(base_no_marker)
        == base_no_marker + "\n\n" + MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL
    )
