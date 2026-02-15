from __future__ import annotations

import logging
from dataclasses import dataclass, field

import asyncpg
from arq.connections import ArqRedis, RedisSettings, create_pool as arq_create_pool
from fastapi import Request

from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    settings: Settings
    db_pool: asyncpg.Pool | None = field(default=None)
    redis: ArqRedis | None = field(default=None)
    memory_store: MemoryStore | None = field(default=None)


async def init_app_state(settings: Settings) -> AppState:
    state = AppState(settings=settings)

    if settings.database_url:
        try:
            state.db_pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=5,
                max_size=20,
            )
            logger.info("PostgreSQL pool created")
        except Exception:
            logger.warning(
                "Failed to connect to PostgreSQL — running without DB", exc_info=True
            )
    else:
        logger.info("DATABASE_URL not set — running without DB")

    if state.db_pool is not None:
        encryption = ContentEncryption(settings.daemon_encryption_key)
        state.memory_store = MemoryStore(state.db_pool, encryption)

    if settings.redis_url:
        try:
            state.redis = await arq_create_pool(
                RedisSettings.from_dsn(settings.redis_url)
            )
            logger.info("Redis connection created")
        except Exception:
            logger.warning(
                "Failed to connect to Redis — running without Redis", exc_info=True
            )
    else:
        logger.info("REDIS_URL not set — running without Redis")

    return state


async def close_app_state(state: AppState) -> None:
    if state.db_pool is not None:
        await state.db_pool.close()
        logger.info("PostgreSQL pool closed")
    if state.redis is not None:
        await state.redis.close()
        logger.info("Redis connection closed")


def get_app_state(request: Request) -> AppState:
    return request.app.state.app_state  # type: ignore[no-any-return]


async def check_db_health(state: AppState) -> dict[str, str]:
    result: dict[str, str] = {}
    if state.db_pool is not None:
        try:
            async with state.db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            result["postgres"] = "ok"
        except Exception as exc:
            result["postgres"] = f"error: {exc}"
    else:
        result["postgres"] = "not_configured"

    if state.redis is not None:
        try:
            pong = await state.redis.ping()
            result["redis"] = "ok" if pong else "error: no pong"
        except Exception as exc:
            result["redis"] = f"error: {exc}"
    else:
        result["redis"] = "not_configured"

    return result
