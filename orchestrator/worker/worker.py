from __future__ import annotations

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportMissingImports=false

import asyncio
import logging
from typing import cast

import asyncpg
from arq.connections import RedisSettings
from arq.worker import Worker, func

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore

from .jobs import (
    extract_memories,
    garbage_collect,
    generate_conversation_title_job,
    generate_summary_job,
    generate_title,
)
from .settings import WorkerSettings

logger = logging.getLogger(__name__)

WorkerContext = dict[str, object]


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

worker = Worker(
    functions=[
        func(extract_memories, max_tries=_worker_settings.retry_attempts),
        func(generate_title, max_tries=_worker_settings.retry_attempts),
        func(
            generate_conversation_title_job, max_tries=_worker_settings.retry_attempts
        ),
        func(generate_summary_job, max_tries=_worker_settings.retry_attempts),
        func(garbage_collect, max_tries=_worker_settings.retry_attempts),
    ],
    redis_settings=RedisSettings.from_dsn(_worker_settings.redis_url),
    on_startup=on_startup,
    on_shutdown=on_shutdown,
    max_jobs=_worker_settings.max_jobs,
    job_timeout=_worker_settings.job_timeout,
)


def main() -> None:
    worker.run()


if __name__ == "__main__":
    main()
