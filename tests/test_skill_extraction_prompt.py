from __future__ import annotations

from orchestrator.skill_evaluator_prompts import (
    build_skill_creation_prompt,
    build_skill_refinement_prompt,
)


def test_creation_prompt_requires_fixed_skill_contract() -> None:
    prompt = build_skill_creation_prompt(
        user_request="Investigate a failing API integration and make it reusable.",
        assistant_response="I traced the API calls, normalized retries, and verified the fix.",
        tool_trace="1. web_search args={...}\n2. http_request args={...}",
        tool_call_count=7,
        conversation_summary="The user was fixing a backend integration issue.",
    )

    assert '"name"' in prompt
    assert '"description"' in prompt
    assert '"trigger_conditions"' in prompt
    assert '"skill_markdown"' in prompt
    assert "used 7 tool calls" in prompt
    assert "stable reusable skill name in Title Case" in prompt
    assert "exact structure, in this order" in prompt
    assert "## Purpose" in prompt
    assert "## When To Use" in prompt
    assert "## Workflow" in prompt
    assert "## Verification" in prompt
    assert "## Guardrails" in prompt
    assert "Every required section must be present and non-empty." in prompt
    assert "Workflow` must be a reusable numbered procedure" in prompt
    assert "Verification` must contain concrete checks or commands" in prompt
    assert "Do not include frontmatter" in prompt


def test_refinement_prompt_includes_patch_contract() -> None:
    prompt = build_skill_refinement_prompt(
        user_request="Debug the flaky sync worker.",
        assistant_response="I found the race, added a lock, and verified the worker flow.",
        tool_trace="1. read args={...}\n2. grep args={...}",
        existing_skill_name="Worker Repair",
        existing_skill_description="Repair a flaky background worker.",
        existing_skill_markdown="# Worker Repair\n\n## Purpose\n\nFix the worker.",
        candidate_name="Worker Repair",
        candidate_description="Repair flaky workers by tracing retries and queue state.",
        candidate_trigger_conditions="Use when a background worker fails intermittently.",
        candidate_skill_markdown="# Worker Repair\n\n## Workflow\n\n1. Inspect retries.",
    )

    assert '"decision": "NO_CHANGE" | "PATCH"' in prompt
    assert '"old_text"' in prompt
    assert '"new_text"' in prompt
    assert "old_text must appear verbatim" in prompt
    assert "Existing skill markdown:" in prompt
    assert "Candidate skill extracted from this turn:" in prompt
    assert "Use when a background worker fails intermittently." in prompt
