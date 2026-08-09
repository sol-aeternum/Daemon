"""Standalone helper module for the MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL.

This module is intentionally decoupled from the heavier ``abstention_sweep``
imports (which pull in ``tests.longmemeval.evaluate`` and
``orchestrator.eval.fact_harness``) so that the helper can be unit-tested
without dragging the benchmark harness into the test process. Both
``abstention_sweep._apply_guardrail`` and the regression test
``tests/test_memory_evidence_abstention_guardrail.py`` import this helper.
"""

from __future__ import annotations

from orchestrator.prompts import MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL


def apply_guardrail(base_prompt: str) -> str:
    """Insert ``MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`` before ``\\n\\nAnswer:``.

    Idempotent: if the guardrail text is already present anywhere in
    ``base_prompt``, return ``base_prompt`` unchanged. If the answer marker
    is absent, append the guardrail at the end of the prompt with a
    leading blank line. Otherwise, splice the guardrail in immediately
    before the final ``\\n\\nAnswer:`` marker.
    """
    guardrail = MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL.strip()
    if guardrail in base_prompt:
        return base_prompt
    answer_marker = "\n\nAnswer:"
    if answer_marker not in base_prompt:
        return base_prompt + "\n\n" + guardrail
    prefix, suffix = base_prompt.rsplit(answer_marker, 1)
    return prefix + "\n\n" + guardrail + answer_marker + suffix
