from __future__ import annotations

from dataclasses import dataclass

from orchestrator.config import Settings


@dataclass
class WorkerSettings:
    redis_url: str = "redis://localhost:6379"
    max_jobs: int = 10
    job_timeout: int = 300
    retry_attempts: int = 3
    consolidation_enabled: bool = True
    consolidation_interval_days: int = 7
    dreaming_enabled: bool = True
    dream_schedule_hour: int = 3
    consolidation_nudge_enabled: bool = True
    consolidation_nudge_conversation_interval: int = 15
    consolidation_nudge_stale_days: int = 30
    consolidation_nudge_min_skills: int = 3

    @classmethod
    def from_app_settings(cls, settings: Settings) -> "WorkerSettings":
        return cls(
            redis_url=settings.redis_url or cls.redis_url,
            consolidation_enabled=settings.consolidation_enabled,
            consolidation_interval_days=settings.consolidation_interval_days,
            dreaming_enabled=settings.dreaming_enabled,
            dream_schedule_hour=settings.dream_schedule_hour,
            consolidation_nudge_enabled=settings.consolidation_nudge_enabled
            if hasattr(settings, "consolidation_nudge_enabled")
            else cls.consolidation_nudge_enabled,
            consolidation_nudge_conversation_interval=settings.consolidation_nudge_conversation_interval
            if hasattr(settings, "consolidation_nudge_conversation_interval")
            else cls.consolidation_nudge_conversation_interval,
            consolidation_nudge_stale_days=settings.consolidation_nudge_stale_days
            if hasattr(settings, "consolidation_nudge_stale_days")
            else cls.consolidation_nudge_stale_days,
            consolidation_nudge_min_skills=settings.consolidation_nudge_min_skills
            if hasattr(settings, "consolidation_nudge_min_skills")
            else cls.consolidation_nudge_min_skills,
        )
