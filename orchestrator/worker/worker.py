from __future__ import annotations

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportMissingImports=false

import asyncio
import logging
from typing import Any, cast

import asyncpg
from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import Worker, func

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore

from orchestrator.worker.jobs import (
    cleanup_generated_files,
    cleanup_generated_images,
    consolidate_memories,
    extract_memories,
    garbage_collect,
    generate_conversation_title_job,
    generate_title,
    generate_summary_job,
    resolve_entities_job,
    run_consolidation_nudge_job,
    run_dreaming_job,
    run_scheduled_dreaming_job,
    run_skill_evaluation_job,
)
from orchestrator.worker.settings import WorkerSettings

logger = logging.getLogger(__name__)

WorkerContext = dict[str, object]


def _unsupported_consolidation_interval_error(interval: int) -> ValueError:
    return ValueError(f"consolidation_interval_days must be one of 1, 7; got {interval}")


def _build_consolidation_cron_job(interval: int) -> Any:
    if interval == 1:
        # Daily at 2 AM UTC
        return cron(
            consolidate_memories,
            hour=2,
            minute=0,
        )
    if interval == 7:
        # Weekly at 2 AM UTC on Sundays
        return cron(
            consolidate_memories,
            hour=2,
            minute=0,
            weekday=6,  # Sunday (arq: 0=Monday, 6=Sunday)
        )
    raise _unsupported_consolidation_interval_error(interval)


async def on_startup(ctx: WorkerContext) -> None:
    app_settings = get_settings()
    ctx["settings"] = app_settings
    ctx["encryption"] = ContentEncryption(app_settings.daemon_encryption_key)
    ctx["store"] = None

    if not app_settings.database_url:
        logger.info("DATABASE_URL not configured; worker memory jobs degraded")
        return

    ctx["db_pool"] = await asyncpg.create_pool(
        dsn=app_settings.database_url,
        min_size=2,
        max_size=10,
    )
    ctx["store"] = MemoryStore(ctx["db_pool"], ctx["encryption"])
    logger.info("Worker DB pool created")


async def on_shutdown(ctx: WorkerContext) -> None:
    db_pool = cast(asyncpg.Pool | None, ctx.get("db_pool"))
    if db_pool is not None:
        await db_pool.close()
        logger.info("Worker DB pool closed")


_worker_settings = WorkerSettings.from_app_settings(get_settings())

try:
    _ = asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Build cron jobs based on settings
cron_jobs: list[Any] = []
if _worker_settings.consolidation_enabled:
    interval = _worker_settings.consolidation_interval_days
    cron_jobs.append(_build_consolidation_cron_job(interval))
    if interval == 1:
        logger.info(f"Memory consolidation scheduled: daily at 2 AM UTC (interval={interval} days)")
    elif interval == 7:
        logger.info(
            f"Memory consolidation scheduled: weekly on Sunday at 2 AM UTC (interval={interval} days)"
        )
    else:
        raise _unsupported_consolidation_interval_error(interval)

if _worker_settings.dreaming_enabled:
    cron_jobs.append(
        cron(
            run_scheduled_dreaming_job,
            minute=0,
        )
    )
    logger.info(
        "Dreaming scheduled: hourly sweep; users run when their configured local hour matches %s:00 (fallback: server schedule when no timezone is stored)",
        _worker_settings.dream_schedule_hour,
    )

if _worker_settings.consolidation_nudge_enabled:
    cron_jobs.append(
        cron(
            run_consolidation_nudge_job,
            minute=0,
        )
    )
    logger.info(
        "Skill consolidation nudge scheduled: hourly sweep to process users who have exceeded %s conversations since last nudge",
        _worker_settings.consolidation_nudge_conversation_interval,
    )

worker = Worker(
    functions=[
        func(extract_memories, max_tries=_worker_settings.retry_attempts),
        func(generate_title, max_tries=_worker_settings.retry_attempts),
        func(generate_conversation_title_job, max_tries=_worker_settings.retry_attempts),
        func(generate_summary_job, max_tries=_worker_settings.retry_attempts),
        func(garbage_collect, max_tries=_worker_settings.retry_attempts),
        func(cleanup_generated_files, max_tries=_worker_settings.retry_attempts),
        func(cleanup_generated_images, max_tries=_worker_settings.retry_attempts),
        func(consolidate_memories, max_tries=_worker_settings.retry_attempts),
        func(run_dreaming_job, max_tries=_worker_settings.retry_attempts),
        func(run_scheduled_dreaming_job, max_tries=_worker_settings.retry_attempts),
        func(resolve_entities_job, max_tries=_worker_settings.retry_attempts),
        func(run_skill_evaluation_job, max_tries=_worker_settings.retry_attempts),
        func(run_consolidation_nudge_job, max_tries=_worker_settings.retry_attempts),
    ],
    redis_settings=RedisSettings.from_dsn(_worker_settings.redis_url),
    on_startup=on_startup,
    on_shutdown=on_shutdown,
    max_jobs=_worker_settings.max_jobs,
    job_timeout=_worker_settings.job_timeout,
    cron_jobs=cron_jobs,
)


def main() -> None:
    worker.run()


if __name__ == "__main__":
    main()
