from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass, field

import asyncpg
from arq.connections import ArqRedis, RedisSettings, create_pool as arq_create_pool
from fastapi import Request

from orchestrator.config import Settings
from orchestrator.database_url import resolve_database_url
from orchestrator.redis_account import validate_redis_account_key
from db.video_credits import VideoCreditsDAL
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.tasks.composition import TaskComposition, qualify_task_composition
from orchestrator.tasks.startup import task_startup_selected, require_legacy_task_schema
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.services.deletion_journal.barrier import (
    configured_journal,
    require_recovery_barrier,
)
from orchestrator.services.deletion_journal.errors import JournalError

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    settings: Settings
    db_pool: asyncpg.Pool | None = field(default=None)
    redis: ArqRedis | None = field(default=None)
    memory_store: MemoryStore | None = field(default=None)
    video_credits_dal: VideoCreditsDAL | None = field(default=None)
    # Legacy compatibility only. First-boot setup token hashes live in system_state.
    setup_token_hash: str | None = field(default=None)
    # Backend owns A's pool; ordinary state and task control always use W.
    task_acceptor_pool: asyncpg.Pool | None = field(default=None)
    task_composition: TaskComposition | None = field(default=None)


async def init_app_state(settings: Settings) -> AppState:
    # Configuration failure must not be swallowed as optional Redis degradation.
    validate_redis_account_key(settings)
    configured_journal(settings)
    composed = task_startup_selected(settings, "http")
    state = AppState(settings=settings)
    database_url = resolve_database_url(settings.database_url)

    if database_url:
        try:

            async def init_connection(conn):
                await conn.execute("SET ivfflat.probes = 10")

            state.db_pool = await asyncpg.create_pool(
                dsn=database_url,
                min_size=5,
                max_size=20,
                init=init_connection,
            )
            logger.info("PostgreSQL pool created")
        except Exception:
            raise JournalError("database_required") from None
    else:
        raise JournalError("database_required")

    try:
        if composed:
            encryption = ContentEncryption(settings.daemon_encryption_key)
            try:
                state.task_acceptor_pool = await asyncpg.create_pool(
                    dsn=settings.daemon_task_acceptor_database_url,
                    min_size=2,
                    max_size=5,
                    init=init_connection,
                )
            except Exception:
                raise JournalError("database_required") from None
            bundle = await qualify_task_composition(
                enabled=True,
                acceptor_pool=state.task_acceptor_pool,
                worker_pool=state.db_pool,
                encryption=encryption,
                settings=settings,
                acceptor_role=settings.daemon_task_acceptor_role,
                worker_role=settings.daemon_task_worker_role,
            )
            if bundle is None:
                raise ObservationBlocked("inference_composition_refused")
            state.task_composition = bundle
            state.memory_store = bundle.tasks._memory
        else:
            await require_legacy_task_schema(state.db_pool)
            await require_recovery_barrier(state.db_pool, settings)
            encryption = ContentEncryption(settings.daemon_encryption_key)
            state.memory_store = MemoryStore(state.db_pool, encryption)
        state.video_credits_dal = VideoCreditsDAL(state.db_pool)
    except BaseException:
        await _close_database_pools(state)
        raise

    if settings.redis_url:
        try:
            state.redis = await arq_create_pool(RedisSettings.from_dsn(settings.redis_url))
            logger.info("Redis connection created")
        except Exception:
            logger.warning("Failed to connect to Redis — running without Redis", exc_info=True)
    else:
        logger.info("REDIS_URL not set — running without Redis")

    return state


async def close_app_state(state: AppState) -> None:
    try:
        await _close_database_pools(state)
    finally:
        if state.redis is not None:
            await state.redis.close()
            logger.info("Redis connection closed")


async def _close_database_pools(state: AppState) -> None:
    # Both are externally owned by this host, never by the qualification factory.
    # Attempt each close even if another fails; cleanup failure never becomes ready.
    pools = [p for p in (state.task_acceptor_pool, state.db_pool) if p is not None]
    results = await asyncio.gather(*(p.close() for p in pools), return_exceptions=True)
    if any(isinstance(result, BaseException) for result in results):
        raise JournalError("unavailable") from None


def get_app_state(request: Request) -> AppState:
    return request.app.state.app_state  # type: ignore[no-any-return]


async def check_db_health(state: AppState) -> dict[str, str]:
    result: dict[str, str] = {}
    if state.db_pool is not None:
        try:
            async with state.db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            result["postgres"] = "ok"
        except Exception:
            logger.warning("PostgreSQL health check failed", exc_info=True)
            result["postgres"] = "error"
    else:
        result["postgres"] = "not_configured"

    if state.redis is not None:
        try:
            pong = await state.redis.ping()
            result["redis"] = "ok" if pong else "error"
        except Exception:
            logger.warning("Redis health check failed", exc_info=True)
            result["redis"] = "error"
    else:
        result["redis"] = "not_configured"

    return result
