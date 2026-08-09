from __future__ import annotations

import pytest

from orchestrator.config import Settings
from orchestrator.worker.settings import WorkerSettings
from orchestrator.worker.worker import _build_consolidation_cron_job


def test_consolidation_interval_one_schedules_daily_at_2_utc() -> None:
    job = _build_consolidation_cron_job(1)

    assert job.hour == 2
    assert job.minute == 0
    assert job.weekday is None


def test_consolidation_interval_seven_schedules_weekly_sunday_at_2_utc() -> None:
    job = _build_consolidation_cron_job(7)

    assert job.hour == 2
    assert job.minute == 0
    assert job.weekday == 6


@pytest.mark.parametrize("interval", [30, -1])
def test_worker_settings_reject_unsupported_consolidation_intervals(interval: int) -> None:
    with pytest.raises(ValueError, match=rf"consolidation_interval_days.*got {interval}"):
        WorkerSettings(consolidation_interval_days=interval)


def test_worker_settings_from_app_settings_rejects_monthly_interval() -> None:
    settings = Settings(consolidation_interval_days=30)

    with pytest.raises(ValueError, match="consolidation_interval_days must be one of 1, 7; got 30"):
        WorkerSettings.from_app_settings(settings)
