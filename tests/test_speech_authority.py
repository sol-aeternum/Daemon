"""Read-only speech authority; no credentials or real account fixtures."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import uuid

import pytest

from orchestrator.auth import AuthenticatedDevice
from orchestrator.speech.authority import SpeechAuthority
from orchestrator.speech.contracts import SpeechError


class Pool:
    def __init__(self, *rows, delay: float = 0):
        self.rows = list(rows)
        self.calls = []
        self.delay = delay
        self.execute = AsyncMock()

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetchrow(self, query, *arguments):
        self.calls.append((query, arguments))
        await asyncio.sleep(self.delay)
        row = self.rows.pop(0)
        if isinstance(row, Exception):
            raise row
        return row


def lease_row(seconds: float = 60):
    now = datetime.now(timezone.utc)
    return {"checked_at": now, "access_expires_at": now + timedelta(seconds=seconds)}


def lease(pool):
    auth = AuthenticatedDevice(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    return SpeechAuthority(pool, auth, "fictional-sha256"), auth


@pytest.mark.asyncio
async def test_immutable_identity_read_only_and_no_denial_retry():
    pool = Pool(lease_row(), None)
    authority, auth = lease(pool)
    await authority.refresh()
    await authority.checkpoint()
    with pytest.raises(SpeechError, match="speech_authorization_lost"):
        await authority.refresh()
    with pytest.raises(SpeechError, match="speech_authorization_lost"):
        await authority.wait_failure()
    assert len(pool.calls) == 2
    for query, arguments in pool.calls:
        assert "UPDATE" not in query
        assert "refresh_consumed_at" not in query
        assert arguments == ("fictional-sha256", auth.user_id, auth.device_id, auth.session_id)
        assert "s.revoked_at IS NULL AND d.revoked_at IS NULL" in query
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_transient_retry_is_bounded_and_does_not_replace_identity():
    pool = Pool(OSError("fictional connection loss"), lease_row())
    authority, _ = lease(pool)
    await authority.refresh()
    await authority.checkpoint()
    assert len(pool.calls) == 2
    assert pool.calls[0][1] == pool.calls[1][1]


@pytest.mark.asyncio
async def test_two_read_errors_fail_closed_with_sanitized_code():
    pool = Pool(OSError("private"), OSError("private"))
    authority, _ = lease(pool)
    with pytest.raises(SpeechError, match="^speech_authorization_unavailable$"):
        await authority.refresh()
    assert len(pool.calls) == 2
    with pytest.raises(SpeechError):
        await authority.checkpoint()


@pytest.mark.asyncio
async def test_query_latency_never_extends_expiry():
    authority, _ = lease(Pool(lease_row(0.01), delay=0.025))
    with pytest.raises(SpeechError, match="speech_authorization_lost"):
        await authority.refresh()


@pytest.mark.asyncio
async def test_watch_expiry_interrupts_blocked_refresh(monkeypatch):
    monkeypatch.setattr("orchestrator.speech.authority.REFRESH_SECONDS", 0.001)
    pool = Pool(lease_row(0.04), lease_row(60))
    authority, _ = lease(pool)
    await authority.refresh()
    pool.delay = 1
    authority._monitor = asyncio.create_task(authority._watch())
    try:
        with pytest.raises(SpeechError, match="speech_authorization_lost"):
            await asyncio.wait_for(authority.wait_failure(), timeout=0.2)
    finally:
        await authority.close()


@pytest.mark.asyncio
async def test_read_budget_and_gate_while_unresolved(monkeypatch):
    monkeypatch.setattr("orchestrator.speech.authority.REFRESH_BUDGET_SECONDS", 0.025)
    pool = Pool(lease_row(), lease_row())
    authority, _ = lease(pool)
    await authority.refresh()
    pool.delay = 1
    refresh = asyncio.create_task(authority.refresh())
    await asyncio.sleep(0)
    check = asyncio.create_task(authority.checkpoint())
    await asyncio.sleep(0.005)
    assert not check.done()
    for task in (refresh, check):
        with pytest.raises(SpeechError, match="speech_authorization_unavailable"):
            await task


@pytest.mark.asyncio
async def test_cancelled_failure_waiter_does_not_cancel_authority_future():
    authority, _ = lease(Pool(lease_row(), None))
    await authority.refresh()
    waiter = asyncio.create_task(authority.wait_failure())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    with pytest.raises(SpeechError):
        await authority.refresh()
    with pytest.raises(SpeechError, match="speech_authorization_lost"):
        await authority.wait_failure()
