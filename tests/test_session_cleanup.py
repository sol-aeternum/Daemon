"""Tests for session cleanup job."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import MagicMock

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.config import get_settings
from orchestrator.main import app


SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class MockPool:
    def __init__(self, sessions=None):
        self._sessions = sessions or {}
        self._deleted_ids = []
        self._closed = False
        self._count_sql = None
        self._delete_sql = None
        self._events = []
        self._lock_held = False

    def _candidate_ids(self, grace_days):
        grace_interval = timedelta(days=grace_days)
        now = datetime.now(timezone.utc)
        to_delete = []
        for session_id, session in list(self._sessions.items()):
            refresh_expires_at = session.get("refresh_expires_at")
            revoked_at = session.get("revoked_at")

            if refresh_expires_at is not None and revoked_at is not None:
                if refresh_expires_at < now - grace_interval or revoked_at < now - grace_interval:
                    to_delete.append(session_id)
            elif refresh_expires_at is not None:
                if refresh_expires_at < now - grace_interval:
                    to_delete.append(session_id)
            elif revoked_at is not None and revoked_at < now - grace_interval:
                to_delete.append(session_id)
        return to_delete

    async def fetchval(self, sql, *args):
        if "DELETE FROM sessions" in sql:
            self._delete_sql = sql
            self._events.append("delete")
            assert self._lock_held, "cleanup DELETE must run under session advisory lock"
            to_delete = self._candidate_ids(args[0])
            self._deleted_ids = to_delete
            for session_id in to_delete:
                del self._sessions[session_id]
            return len(to_delete)
        return None

    async def fetchrow(self, sql, *args):
        if "candidate_count" in sql and "total_count" in sql:
            self._count_sql = sql
            return {
                "candidate_count": len(self._candidate_ids(args[0])),
                "total_count": len(self._sessions),
            }
        return None

    async def execute(self, sql, *args):
        return None

    @asynccontextmanager
    async def acquire(self):
        yield MockConn(self)

    async def close(self):
        self._closed = True


class MockConn:
    def __init__(self, pool):
        self._pool = pool

    async def fetchval(self, sql, *args):
        if "DELETE FROM sessions" in sql:
            return await self._pool.fetchval(sql, *args)
        if "SELECT NOW()" in sql:
            return datetime.now(timezone.utc)
        if "COUNT(*)" in sql and "devices" in sql:
            return 1
        return None

    async def fetchrow(self, sql, *args):
        return await self._pool.fetchrow(sql, *args)

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            self._pool._events.append("lock")
            self._pool._lock_held = True
        return None

    @asynccontextmanager
    async def transaction(self):
        try:
            yield self
        finally:
            self._pool._lock_held = False


class SerializingPool:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._refresh_active = 0
        self._deleted_mid_refresh = 0
        self._cleanup_count = 0

    @asynccontextmanager
    async def acquire(self):
        conn = SerializingConn(self)
        yield conn


class SerializingConn:
    def __init__(self, pool):
        self._pool = pool
        self._lock_acquired = False

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            await self._pool._lock.acquire()
            self._lock_acquired = True
        return None

    async def fetchval(self, sql, *args):
        if "DELETE FROM sessions" in sql:
            self._pool._cleanup_count += 1
            if self._pool._refresh_active:
                self._pool._deleted_mid_refresh += 1
                return 1
            return 0
        return None

    async def fetchrow(self, sql, *args):
        if "candidate_count" in sql and "total_count" in sql:
            return {"candidate_count": 0, "total_count": 0}
        return None

    @asynccontextmanager
    async def transaction(self):
        try:
            yield self
        finally:
            if self._lock_acquired:
                self._lock_acquired = False
                self._pool._lock.release()


def _make_session(
    refresh_expires_at: datetime,
    revoked_at: datetime | None = None,
    refresh_consumed_at: datetime | None = None,
):
    return {
        "id": uuid.uuid4(),
        "user_id": SINGLETON_ID,
        "device_id": uuid.uuid4(),
        "client_kind": "web",
        "refresh_token_hash": f"hash_{uuid.uuid4().hex[:16]}",
        "refresh_expires_at": refresh_expires_at,
        "refresh_consumed_at": refresh_consumed_at,
        "revoked_at": revoked_at,
    }


def make_mock_init(mock_pool):
    import orchestrator.main as main_module

    original_init = main_module.init_app_state

    async def mock_init(settings):
        from orchestrator.db import AppState

        state = AppState(settings=settings)
        state.db_pool = mock_pool
        state.redis = None
        state.memory_store = None
        state.video_credits_dal = None
        state.setup_token_hash = None
        return state

    main_module.init_app_state = mock_init
    return original_init


def restore_init(original):
    import orchestrator.main as main_module

    main_module.init_app_state = original


@pytest_asyncio.fixture
async def setup_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ALLOWED_ORIGINS", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_PUBLIC_ORIGIN", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "test-pepper-for-all-tests-12345678901234567890")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestCleanupStaleSessions:
    async def _run_cleanup(self, sessions, grace_days=7, max_delete_fraction=1.0):
        from orchestrator.session_cleanup import cleanup_stale_sessions

        mock_pool = MockPool(sessions=sessions)
        deleted = await cleanup_stale_sessions(
            cast(asyncpg.Pool | None, cast(object, mock_pool)),
            grace_days,
            max_delete_fraction,
        )
        return deleted, mock_pool._deleted_ids, mock_pool

    def _get_ids_remaining(self, sessions, deleted_ids):
        return {sid for sid in sessions if sessions[sid]["id"] not in deleted_ids}

    def _get_ids_deleted(self, sessions, deleted_ids):
        return {sid for sid in sessions if sessions[sid]["id"] in deleted_ids}

    @pytest.mark.asyncio
    async def test_active_session_preserved(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(now + timedelta(days=90)),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions)
        assert deleted == 0
        assert len(deleted_ids) == 0

    @pytest.mark.asyncio
    async def test_expired_within_grace_preserved(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(now - timedelta(days=5)),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions, grace_days=7)
        assert deleted == 0
        assert len(deleted_ids) == 0

    @pytest.mark.asyncio
    async def test_stale_expired_deleted(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(now - timedelta(days=10)),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions, grace_days=7)
        assert deleted == 1
        assert "s1" in deleted_ids

    @pytest.mark.asyncio
    async def test_revoked_within_grace_preserved(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(now + timedelta(days=90), revoked_at=now - timedelta(days=5)),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions, grace_days=7)
        assert deleted == 0
        assert len(deleted_ids) == 0

    @pytest.mark.asyncio
    async def test_stale_revoked_deleted(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(now + timedelta(days=90), revoked_at=now - timedelta(days=10)),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions, grace_days=7)
        assert deleted == 1
        assert "s1" in deleted_ids

    @pytest.mark.asyncio
    async def test_consumed_but_not_expired_preserved(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(
                now + timedelta(days=90),
                refresh_consumed_at=now - timedelta(hours=1),
            ),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions, grace_days=7)
        assert deleted == 0
        assert len(deleted_ids) == 0

    @pytest.mark.asyncio
    async def test_consumed_and_expired_within_grace_preserved(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(
                now - timedelta(days=5),
                refresh_consumed_at=now - timedelta(hours=1),
            ),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions, grace_days=7)
        assert deleted == 0
        assert len(deleted_ids) == 0

    @pytest.mark.asyncio
    async def test_consumed_and_expired_stale_deleted(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(
                now - timedelta(days=10),
                refresh_consumed_at=now - timedelta(hours=1),
            ),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions, grace_days=7)
        assert deleted == 1
        assert "s1" in deleted_ids

    @pytest.mark.asyncio
    async def test_mixed_sessions(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1_active": _make_session(now + timedelta(days=90)),
            "s2_within_grace_expired": _make_session(now - timedelta(days=5)),
            "s3_stale_expired": _make_session(now - timedelta(days=10)),
            "s4_within_grace_revoked": _make_session(
                now + timedelta(days=90), revoked_at=now - timedelta(days=5)
            ),
            "s5_stale_revoked": _make_session(
                now + timedelta(days=90), revoked_at=now - timedelta(days=10)
            ),
            "s6_consumed_unexpired": _make_session(
                now + timedelta(days=90), refresh_consumed_at=now - timedelta(hours=1)
            ),
        }
        deleted, deleted_ids, _ = await self._run_cleanup(sessions, grace_days=7)
        assert deleted == 2
        assert "s3_stale_expired" in deleted_ids
        assert "s5_stale_revoked" in deleted_ids
        remaining = set(sessions.keys()) - set(deleted_ids)
        assert "s1_active" in remaining
        assert "s2_within_grace_expired" in remaining
        assert "s4_within_grace_revoked" in remaining
        assert "s6_consumed_unexpired" in remaining

    @pytest.mark.asyncio
    async def test_cleanup_sql_uses_safe_interval_arithmetic(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1": _make_session(now - timedelta(days=10)),
            "s2": _make_session(now + timedelta(days=90)),
        }
        deleted, deleted_ids, mock_pool = await self._run_cleanup(sessions, grace_days=7)

        assert deleted == 1
        assert "s1" in deleted_ids
        assert mock_pool._count_sql is not None
        assert mock_pool._delete_sql is not None
        assert "|| ' days'" not in mock_pool._count_sql
        assert "|| ' days'" not in mock_pool._delete_sql
        assert "$1 * INTERVAL '1 day'" in mock_pool._count_sql
        assert "$1 * INTERVAL '1 day'" in mock_pool._delete_sql

    @pytest.mark.asyncio
    @pytest.mark.parametrize("grace_days", [0, -1])
    async def test_invalid_grace_days_rejected_before_delete(self, grace_days):
        from orchestrator.session_cleanup import cleanup_stale_sessions

        now = datetime.now(timezone.utc)
        mock_pool = MockPool({"s1": _make_session(now - timedelta(days=10))})

        with pytest.raises(ValueError, match="grace_days"):
            await cleanup_stale_sessions(
                cast(asyncpg.Pool | None, cast(object, mock_pool)), grace_days
            )

        assert mock_pool._deleted_ids == []
        assert mock_pool._count_sql is None
        assert mock_pool._delete_sql is None

    @pytest.mark.asyncio
    async def test_mass_delete_safety_aborts_and_logs(self, caplog):
        from orchestrator.session_cleanup import cleanup_stale_sessions

        now = datetime.now(timezone.utc)
        sessions = {
            **{f"stale_{idx}": _make_session(now - timedelta(days=30)) for idx in range(11)},
            **{f"active_{idx}": _make_session(now + timedelta(days=90)) for idx in range(9)},
        }
        mock_pool = MockPool(sessions)

        with caplog.at_level("CRITICAL"):
            with pytest.raises(RuntimeError, match="safety limit"):
                await cleanup_stale_sessions(
                    cast(asyncpg.Pool | None, cast(object, mock_pool)),
                    7,
                    max_delete_fraction=0.5,
                )

        assert mock_pool._deleted_ids == []
        assert len(mock_pool._sessions) == 20
        assert "Session cleanup aborted" in caplog.text

    @pytest.mark.asyncio
    async def test_null_db_pool_returns_zero(self):
        from orchestrator.session_cleanup import cleanup_stale_sessions

        deleted = await cleanup_stale_sessions(cast(asyncpg.Pool | None, None), 7)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_cleanup_acquires_advisory_lock_before_delete(self):
        from orchestrator.session_cleanup import cleanup_stale_sessions

        now = datetime.now(timezone.utc)
        mock_pool = MockPool({"s1": _make_session(now - timedelta(days=10))})

        deleted = await cleanup_stale_sessions(
            cast(asyncpg.Pool | None, cast(object, mock_pool)),
            7,
        )

        assert deleted == 1
        assert mock_pool._events == ["lock", "delete"]

    @pytest.mark.asyncio
    async def test_concurrent_refreshes_and_cleanup_are_serialized(self):
        from orchestrator.session_cleanup import cleanup_stale_sessions, lock_session_cleanup

        serializing_pool = SerializingPool()

        async def simulated_refresh():
            async with serializing_pool.acquire() as conn:
                async with conn.transaction():
                    await lock_session_cleanup(cast(asyncpg.Connection, cast(object, conn)))
                    serializing_pool._refresh_active += 1
                    await asyncio.sleep(0)
                    serializing_pool._refresh_active -= 1

        async def simulated_cleanup():
            await cleanup_stale_sessions(
                cast(asyncpg.Pool | None, cast(object, serializing_pool)),
                7,
            )

        refresh_tasks = [asyncio.create_task(simulated_refresh()) for _ in range(100)]
        cleanup_task = asyncio.create_task(simulated_cleanup())

        await asyncio.gather(*refresh_tasks, cleanup_task)

        assert serializing_pool._cleanup_count == 1
        assert serializing_pool._deleted_mid_refresh == 0


class TestRealCleanupLoopInterruptible:
    """Tests for real cleanup loop behavior using the actual _session_cleanup_loop.

    These tests verify:
    - The loop does NOT run cleanup immediately on start (waits for interval first)
    - The loop IS interrupted promptly when shutdown_event is set during wait
    - The old asyncio.sleep(interval) implementation would NOT pass these tests
    """

    @pytest.mark.asyncio
    async def test_loop_does_not_run_cleanup_immediately(self):
        """Verify periodic loop waits for interval before first cleanup, not immediately."""
        from orchestrator.session_cleanup import _session_cleanup_loop

        import orchestrator.session_cleanup as sc

        original = sc.cleanup_stale_sessions

        cleanup_run_times = []

        async def mock_cleanup(db_pool, grace_days, max_delete_fraction=0.5):
            cleanup_run_times.append(datetime.now(timezone.utc))
            return 0

        sc.cleanup_stale_sessions = mock_cleanup
        try:
            shutdown_event = asyncio.Event()

            loop_task = asyncio.create_task(
                _session_cleanup_loop(
                    MagicMock(),
                    7,
                    3600,
                    shutdown_event,
                )
            )
            await asyncio.sleep(0.2)
            shutdown_event.set()
            await asyncio.wait_for(loop_task, timeout=1.0)

            assert len(cleanup_run_times) == 0, (
                "Periodic loop should NOT run cleanup immediately on start. "
                f"Cleanup ran {len(cleanup_run_times)} time(s) before shutdown."
            )
        finally:
            sc.cleanup_stale_sessions = original

    @pytest.mark.asyncio
    async def test_loop_stops_promptly_during_long_interval_wait(self):
        """Verify shutdown is prompt even when interval is long.

        The old asyncio.sleep(interval_seconds) would block shutdown for the full
        interval duration. The new asyncio.wait_for(shutdown_event.wait(), timeout)
        pattern allows prompt shutdown.
        """
        from orchestrator.session_cleanup import _session_cleanup_loop

        shutdown_time = None
        interval_seconds = 3600
        shutdown_delay = 0.1

        async def mock_cleanup(db_pool, grace_days, max_delete_fraction=0.5):
            return 0

        import orchestrator.session_cleanup as sc

        original = sc.cleanup_stale_sessions
        sc.cleanup_stale_sessions = mock_cleanup

        try:
            shutdown_event = asyncio.Event()
            start = datetime.now(timezone.utc)

            async def run():
                nonlocal shutdown_time
                loop_task = asyncio.create_task(
                    _session_cleanup_loop(
                        MagicMock(),
                        7,
                        interval_seconds,
                        shutdown_event,
                    )
                )
                await asyncio.sleep(shutdown_delay)
                shutdown_event.set()
                await asyncio.wait_for(loop_task, timeout=2.0)
                shutdown_time = datetime.now(timezone.utc)

            await run()

            assert shutdown_time is not None
            elapsed = (shutdown_time - start).total_seconds()

            assert elapsed < 2.0, (
                f"Shutdown took {elapsed:.1f}s but should complete promptly (<2s) "
                f"even with {interval_seconds}s interval. "
                "The loop may be using blocking asyncio.sleep instead of wait_for."
            )
        finally:
            sc.cleanup_stale_sessions = original


class TestCleanupTaskLifecycle:
    @pytest.mark.asyncio
    async def test_cleanup_task_starts_and_stops(self, setup_env, monkeypatch):
        mock_pool = MockPool()
        original = make_mock_init(mock_pool)
        try:
            startup_cleanup_called = []

            async def mock_cleanup_stale_sessions(pool, grace_days, max_delete_fraction=0.5):
                startup_cleanup_called.append(True)
                return 0

            import orchestrator.main as main_module

            monkeypatch.setattr(main_module, "cleanup_stale_sessions", mock_cleanup_stale_sessions)

            loop_task = None

            async def mock_start_session_cleanup_task(
                pool,
                grace_days,
                interval_seconds,
                max_delete_fraction=0.5,
            ):
                nonlocal loop_task
                shutdown_event = asyncio.Event()

                async def dummy_loop():
                    nonlocal loop_task
                    loop_task = asyncio.current_task()
                    await shutdown_event.wait()

                t = asyncio.create_task(dummy_loop())
                return t, shutdown_event

            monkeypatch.setattr(
                main_module, "start_session_cleanup_task", mock_start_session_cleanup_task
            )

            async with app.router.lifespan_context(app):
                await asyncio.sleep(0.1)

            assert len(startup_cleanup_called) == 1, "Startup cleanup should be called once"

        finally:
            restore_init(original)


class TestSessionCleanupConfig:
    @pytest.mark.parametrize("grace_days", ["0", "-1"])
    def test_invalid_grace_days_rejected_by_settings(self, monkeypatch, grace_days):
        monkeypatch.setenv("DAEMON_SESSION_CLEANUP_GRACE_DAYS", grace_days)
        get_settings.cache_clear()
        try:
            with pytest.raises(ValueError):
                get_settings()
        finally:
            get_settings.cache_clear()


class TestCleanupRetentionPolicy:
    async def _run_cleanup(self, sessions, grace_days=7):
        from orchestrator.session_cleanup import cleanup_stale_sessions

        mock_pool = MockPool(sessions=sessions)
        deleted = await cleanup_stale_sessions(
            cast(asyncpg.Pool | None, cast(object, mock_pool)),
            grace_days,
            max_delete_fraction=1.0,
        )
        return deleted, mock_pool._deleted_ids

    @pytest.mark.asyncio
    async def test_cleanup_deletes_only_stale_rows(self):
        now = datetime.now(timezone.utc)
        sessions = {
            "s1_active": _make_session(now + timedelta(days=90)),
            "s2_recently_expired": _make_session(now - timedelta(days=3)),
            "s3_stale_expired": _make_session(now - timedelta(days=14)),
            "s4_recently_revoked": _make_session(
                now + timedelta(days=90), revoked_at=now - timedelta(days=3)
            ),
            "s5_stale_revoked": _make_session(
                now + timedelta(days=90), revoked_at=now - timedelta(days=14)
            ),
            "s6_consumed_unexpired": _make_session(
                now + timedelta(days=90), refresh_consumed_at=now - timedelta(hours=1)
            ),
            "s7_consumed_recently_expired": _make_session(
                now - timedelta(days=3), refresh_consumed_at=now - timedelta(hours=1)
            ),
            "s8_consumed_stale_expired": _make_session(
                now - timedelta(days=14), refresh_consumed_at=now - timedelta(hours=1)
            ),
        }

        deleted, deleted_ids = await self._run_cleanup(sessions, grace_days=7)

        assert "s3_stale_expired" in deleted_ids
        assert "s5_stale_revoked" in deleted_ids
        assert "s8_consumed_stale_expired" in deleted_ids

        assert "s1_active" not in deleted_ids
        assert "s2_recently_expired" not in deleted_ids
        assert "s4_recently_revoked" not in deleted_ids
        assert "s6_consumed_unexpired" not in deleted_ids
        assert "s7_consumed_recently_expired" not in deleted_ids
