from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orchestrator.config import Settings
from orchestrator.daemon import with_runtime_datetime_context
from orchestrator.timezones import extract_timezone_name


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


@pytest.mark.parametrize(
    ("user_timezone", "default_timezone", "expected_date", "expected_time"),
    [
        (None, "UTC", "2026-01-01", "01:30:00 UTC"),
        (None, "America/Los_Angeles", "2025-12-31", "17:30:00 PST"),
        ("Asia/Tokyo", "America/Los_Angeles", "2026-01-01", "10:30:00 JST"),
        ("Invalid/Timezone", "America/Los_Angeles", "2025-12-31", "17:30:00 PST"),
        ("/etc/passwd", "UTC", "2026-01-01", "01:30:00 UTC"),
        (None, "Invalid/Timezone", "2026-01-01", "01:30:00 UTC"),
        (" ", " ", "2026-01-01", "01:30:00 UTC"),
    ],
)
def test_runtime_datetime_timezone_precedence(
    user_timezone, default_timezone, expected_date, expected_time
) -> None:
    enriched = with_runtime_datetime_context(
        "Base prompt",
        now_utc=datetime(2026, 1, 1, 1, 30, tzinfo=timezone.utc),
        user_timezone=user_timezone,
        default_timezone=default_timezone,
    )
    assert f"Current date: {expected_date}" in enriched
    assert f"Current time: {expected_time}" in enriched
    assert "Current UTC datetime: 2026-01-01 01:30:00 UTC" in enriched


@pytest.mark.parametrize(("month", "expected_time"), [(1, "12:00:00 ACDT"), (7, "11:00:00 ACST")])
def test_runtime_datetime_respects_daylight_saving(month, expected_time) -> None:
    enriched = with_runtime_datetime_context(
        "Base prompt",
        now_utc=datetime(2026, month, 1, 1, 30, tzinfo=timezone.utc),
        user_timezone="Australia/Adelaide",
    )
    assert f"Current time: {expected_time}" in enriched


@pytest.mark.parametrize(
    ("user_settings", "expected"),
    [
        (None, None),
        ({}, None),
        ({"timezone": " Asia/Tokyo ", "time_zone": "UTC"}, "Asia/Tokyo"),
        ({"time_zone": "Asia/Tokyo"}, "Asia/Tokyo"),
        ({"preferences": {"timezone": "Asia/Tokyo"}}, "Asia/Tokyo"),
        ({"preferences": {"time_zone": "Asia/Tokyo"}}, "Asia/Tokyo"),
        ({"timezone": 42, "preferences": {"time_zone": "UTC"}}, "UTC"),
        ({"timezone": " ", "preferences": "invalid"}, None),
    ],
)
def test_timezone_preference_uses_existing_worker_aliases(user_settings, expected) -> None:
    assert extract_timezone_name(user_settings) == expected


def test_default_timezone_is_configurable(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DAEMON_DEFAULT_TIMEZONE", raising=False)
    assert Settings().daemon_default_timezone == "UTC"
    monkeypatch.setenv("DAEMON_DEFAULT_TIMEZONE", "Australia/Adelaide")
    assert Settings().daemon_default_timezone == "Australia/Adelaide"
