from __future__ import annotations

from datetime import datetime, timezone

from orchestrator.daemon import with_runtime_datetime_context


def test_runtime_datetime_context_is_appended() -> None:
    base_prompt = "You are a helpful assistant."
    fixed_now = datetime(2026, 3, 8, 6, 30, 0, tzinfo=timezone.utc)

    enriched = with_runtime_datetime_context(base_prompt, now_utc=fixed_now)

    assert base_prompt in enriched
    assert "<runtime-datetime-context>" in enriched
    assert "Current date: 2026-03-08" in enriched
    assert "Current UTC datetime: 2026-03-08 06:30:00 UTC" in enriched


def test_runtime_datetime_context_replaces_existing_block() -> None:
    existing_prompt = (
        "You are a helpful assistant.\n\n"
        "<runtime-datetime-context>\n"
        "- Current date: 2025-01-01\n"
        "- Current time: 00:00:00 UTC\n"
        "- Current UTC datetime: 2025-01-01 00:00:00 UTC\n"
        "- Use this as authoritative temporal context for this response."
    )
    fixed_now = datetime(2026, 3, 8, 6, 30, 0, tzinfo=timezone.utc)

    refreshed = with_runtime_datetime_context(existing_prompt, now_utc=fixed_now)

    assert refreshed.count("<runtime-datetime-context>") == 1
    assert "2025-01-01" not in refreshed
    assert "Current UTC datetime: 2026-03-08 06:30:00 UTC" in refreshed
