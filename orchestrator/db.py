from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass, field
from contextlib import asynccontextmanager

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
from orchestrator.services.restore_history.configuration import configured_history
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.runtime import (
    HistoryRuntime,
    physical_completion,
    require_unselected_history,
    startup_deadline,
)

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
    # H selects observation-only startup; no mutating collaborator is installed.
    history_observation: object | None = field(default=None)
    history_runtime: HistoryRuntime | None = field(default=None)
    _history_shutdown_task: asyncio.Task[None] | None = field(default=None, repr=False)


async def init_app_state(settings: Settings) -> AppState:
    # Configuration failure must not be swallowed as optional Redis degradation.
    validate_redis_account_key(settings)
    configured_journal(settings)
    composed = task_startup_selected(settings, "http")
    history_config = configured_history(settings, "http")
    if history_config is not None and not composed:
        raise HistoryError("history_configuration_required")
    state = AppState(settings=settings)
    database_url = resolve_database_url(settings.database_url)
    deadline = startup_deadline()

    @asynccontextmanager
    async def recovery_scope():
        if state.db_pool is None:
            raise HistoryError("history_startup_refused")
        if state.history_runtime is None:
            await require_unselected_history(state.db_pool)
            yield
        else:
            async with state.history_runtime.qualify_before_recovery(
                state.db_pool, deadline=deadline
            ):
                yield

    try:
        if history_config is not None:
            state.history_runtime = HistoryRuntime(history_config, settings)
            await state.history_runtime.open(deadline=deadline)
        if database_url:

            async def init_connection(conn):
                await conn.execute("SET ivfflat.probes = 10")

            try:
                pool = asyncpg.create_pool(
                    dsn=database_url,
                    # One retained initializer connection: asyncpg's multi-minimum
                    # gather can leave sibling connects running after one fails.
                    min_size=1 if history_config is not None else 5,
                    max_size=20,
                    init=init_connection,
                )
                if history_config is not None:
                    state.db_pool = pool  # retain even partial/late initialization
                    await physical_completion(pool, deadline=deadline)
                else:
                    state.db_pool = await pool
            except Exception:
                # Preserve the native sanitized startup failure; never expose a
                # driver exception containing connection/configuration values.
                raise JournalError("database_required") from None
            logger.info("PostgreSQL pool created")
        else:
            raise JournalError("database_required")

        if composed:
            encryption = ContentEncryption(settings.daemon_encryption_key)
            try:
                pool = asyncpg.create_pool(
                    dsn=settings.daemon_task_acceptor_database_url,
                    min_size=1 if history_config is not None else 2,
                    max_size=5,
                    init=init_connection,
                )
                if history_config is not None:
                    state.task_acceptor_pool = pool
                    await physical_completion(pool, deadline=deadline)
                else:
                    state.task_acceptor_pool = await pool
            except Exception:
                raise JournalError("database_required") from None
            async with recovery_scope():
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
            async with recovery_scope():
                await require_recovery_barrier(state.db_pool, settings)
            encryption = ContentEncryption(settings.daemon_encryption_key)
            state.memory_store = MemoryStore(state.db_pool, encryption)
        state.video_credits_dal = VideoCreditsDAL(state.db_pool)
        if state.history_runtime is not None:
            if state.task_composition is None:
                raise HistoryError("history_startup_refused")
            await state.history_runtime.install_reader(
                state.task_composition.tasks, deadline=deadline
            )
            state.history_observation = state.history_runtime.reader
    except BaseException as original:
        try:
            await close_app_state(state)
        except BaseException as cleanup:
            original.add_note("Startup cleanup: " + type(cleanup).__name__)
        raise

    if settings.redis_url and history_config is None:
        try:
            state.redis = await arq_create_pool(RedisSettings.from_dsn(settings.redis_url))
            logger.info("Redis connection created")
        except Exception:
            logger.warning("Failed to connect to Redis — running without Redis", exc_info=True)
    else:
        logger.info("REDIS_URL not set — running without Redis")

    return state


async def close_app_state(state: AppState) -> None:
    if state.history_runtime is not None:
        # Drain read/H owners before their SQL pools. Retain the full close task
        # through repeated cancellation; no pool reset can outlive H ownership.
        state.history_runtime._begin_shutdown()
        if state._history_shutdown_task is None:
            call = _close_history_state(state)
            try:
                state._history_shutdown_task = asyncio.create_task(call)
            except BaseException as original:
                try:
                    call.close()  # Known unstarted coroutine, never descriptor retry.
                except BaseException as cleanup:
                    original.add_note("Unstarted shutdown cleanup: " + type(cleanup).__name__)
                raise
        await physical_completion(state._history_shutdown_task, deadline=startup_deadline())
        return
    try:
        await _close_database_pools(state)
    finally:
        if state.redis is not None:
            await state.redis.close()
            logger.info("Redis connection closed")


async def _close_history_state(state: AppState) -> None:
    if state.history_runtime is None:
        raise HistoryError("history_shutdown_refused")
    first: BaseException | None = None
    deadline = startup_deadline()
    for call in (
        state.history_runtime.drain_reader(deadline=deadline),
        _close_database_pools(state),
        state.history_runtime.aclose(deadline=deadline),
    ):
        try:
            await call
        except BaseException as error:
            if first is None:
                first = error
            else:
                first.add_note("Host cleanup: " + type(error).__name__)
    if first is not None:
        raise first


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
