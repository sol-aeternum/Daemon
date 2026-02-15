from __future__ import annotations

from dataclasses import dataclass

from orchestrator.config import Settings


@dataclass
class WorkerSettings:
    redis_url: str = "redis://localhost:6379"
    max_jobs: int = 10
    job_timeout: int = 300
    retry_attempts: int = 3

    @classmethod
    def from_app_settings(cls, settings: Settings) -> "WorkerSettings":
        return cls(redis_url=settings.redis_url or cls.redis_url)
