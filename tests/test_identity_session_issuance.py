"""Tests for the identity-aware device session issuance helper (TODO 9).

These tests exercise the reusable backend-only service helper
`orchestrator.services.identity.issue_device_session` directly via
a hand-rolled `MockConn`. No HTTP route is added at this TODO;
identity session issuance MUST be gated on a verified identity
proof (email code consumption or Google ID-token verification),
and those routes are TODO 11 / TODO 13. Helper-level tests are
sufficient for the TODO 9 deliverable; route-level coverage is
deferred to the proof-owning completion routes.

Coverage matches the TODO 9 acceptance criteria:

  - helper input validation rejects invalid `client_kind` and
    `device_persistence` before any DB writes
  - transport decision: web + private -> cookie, web + temporary
    + ttl=0 -> session cookie (no Max-Age), web + temporary +
    ttl>0 -> explicit short cookie, native -> body
  - tenant_id is persisted on the new device row and the new
    session row; None is also accepted for the singleton backfill
  - client_kind is persisted correctly on the session row
  - tokens are stored as SHA-256 hashes, not plaintext
  - refresh rotation / reuse detection is exercised by the
    existing /v1/auth/refresh tests in `tests/test_refresh_flow.py`
    (no changes to that endpoint; helper returns rows that match
    the existing schema, so the rotation path is unaffected)
  - the `DevicePersistence` Literal type guard catches mid-edit
    regressions on the literal set
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from orchestrator.auth_tokens import hash_token
from orchestrator.services.identity import (
    DevicePersistence,
    IssueSessionRequest,
    IssuedSession,
    issue_device_session,
)


SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_TENANT_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


# ============================================================================
# Helper-layer tests (no HTTP — direct helper call with a hand-rolled MockConn)
# ============================================================================


class HelperMockConn:
    """Minimal in-memory connection for testing the helper directly.

    The helper issues exactly two `INSERT ... RETURNING id` statements
    (one for `devices`, one for `sessions`); we record both for the
    test to assert on. The mock also accepts being entered via
    `async with conn.transaction():` so the helper's "caller owns the
    transaction" contract is satisfied.

    Method signatures use `query: str` to match the
    `SupportsSessionIssuanceQueries` Protocol parameter name (the
    Protocol's parameter name is part of the structural type
    contract under basedpyright).
    """

    def __init__(self) -> None:
        self.device_inserts: list[tuple] = []
        self.session_inserts: list[tuple] = []
        self._next_device_id = uuid.uuid4()
        self._next_session_id = uuid.uuid4()
        self._in_transaction = False

    async def fetchval(self, query: str, *args: object) -> uuid.UUID:
        if "INSERT INTO devices" in query and "RETURNING id" in query:
            self.device_inserts.append(args)
            return self._next_device_id
        if "INSERT INTO sessions" in query and "RETURNING id" in query:
            self.session_inserts.append(args)
            return self._next_session_id
        raise AssertionError(f"unmocked fetchval query: {query!r}")

    @asynccontextmanager
    async def transaction(self):
        self._in_transaction = True
        try:
            yield self
        finally:
            self._in_transaction = False


class TestHelperInputValidation:
    @pytest.mark.asyncio
    async def test_invalid_client_kind_raises(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="cli",  # type: ignore[arg-type]
            device_persistence="private",
            device_name="Test",
        )
        with pytest.raises(Exception) as exc_info:
            await issue_device_session(conn, request)
        assert "client_kind" in str(exc_info.value).lower()
        assert conn.device_inserts == []
        assert conn.session_inserts == []

    @pytest.mark.asyncio
    async def test_invalid_device_persistence_raises(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="web",
            device_persistence="ephemeral",  # type: ignore[arg-type]
            device_name="Test",
        )
        with pytest.raises(Exception) as exc_info:
            await issue_device_session(conn, request)
        assert "device_persistence" in str(exc_info.value).lower()
        assert conn.device_inserts == []
        assert conn.session_inserts == []


class TestHelperTransportDecision:
    @pytest.mark.asyncio
    async def test_web_private_returns_cookie_transport(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="web",
            device_persistence="private",
            device_name="Test Device",
            platform="macos",
            private_refresh_ttl_days=90,
        )
        async with conn.transaction():
            issued = await issue_device_session(conn, request)
        assert isinstance(issued, IssuedSession)
        assert issued.client_kind == "web"
        assert issued.refresh_transport == "cookie"
        # 90 days = 7,776,000 seconds
        assert issued.refresh_max_age_seconds == 90 * 86400
        assert issued.access_token
        assert issued.refresh_token
        assert issued.access_token != issued.refresh_token

    @pytest.mark.asyncio
    async def test_web_temporary_with_zero_ttl_uses_session_cookie(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="web",
            device_persistence="temporary",
            device_name="Public Computer",
            platform="macos",
            temporary_refresh_ttl_seconds=0,
        )
        async with conn.transaction():
            issued = await issue_device_session(conn, request)
        assert issued.refresh_transport == "cookie"
        assert issued.refresh_max_age_seconds is None
        # DB-side cap falls back to the defensive 1-hour window.
        # refresh_expires_at should be roughly now + 1h.
        now = datetime.now(timezone.utc)
        delta = (issued.refresh_expires_at - now).total_seconds()
        assert 3500 <= delta <= 3700, f"expected ~1h DB cap, got {delta}s"

    @pytest.mark.asyncio
    async def test_web_temporary_with_short_ttl_uses_explicit_max_age(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="web",
            device_persistence="temporary",
            device_name="Public Computer",
            temporary_refresh_ttl_seconds=600,
        )
        async with conn.transaction():
            issued = await issue_device_session(conn, request)
        assert issued.refresh_transport == "cookie"
        assert issued.refresh_max_age_seconds == 600

    @pytest.mark.asyncio
    async def test_native_returns_body_transport(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="native",
            device_persistence="private",
            device_name="iPhone",
            platform="ios",
        )
        async with conn.transaction():
            issued = await issue_device_session(conn, request)
        assert issued.client_kind == "native"
        assert issued.refresh_transport == "body"

    @pytest.mark.asyncio
    async def test_native_temporary_still_returns_body(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="native",
            device_persistence="temporary",
            device_name="iPhone",
            platform="ios",
            temporary_refresh_ttl_seconds=600,
        )
        async with conn.transaction():
            issued = await issue_device_session(conn, request)
        assert issued.refresh_transport == "body"
        # Native persistence is cookie-agnostic; refresh_max_age
        # reflects the DB-side cap for completeness, not a cookie TTL.
        assert issued.refresh_max_age_seconds == 600


class TestHelperPersistenceToDeviceAndSession:
    @pytest.mark.asyncio
    async def test_tenant_id_persisted_on_device_and_session(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="web",
            device_persistence="private",
            device_name="Test Device",
            platform="macos",
        )
        async with conn.transaction():
            await issue_device_session(conn, request)
        # INSERT INTO devices (user_id, tenant_id, display_name, platform)
        assert len(conn.device_inserts) == 1
        dev_args = conn.device_inserts[0]
        assert dev_args[0] == SINGLETON_ID
        assert dev_args[1] == TEST_TENANT_ID
        assert dev_args[2] == "Test Device"
        assert dev_args[3] == "macos"
        # INSERT INTO sessions (user_id, device_id, client_kind,
        #                       device_persistence, tenant_id,
        #                       access_token_hash, access_expires_at,
        #                       refresh_token_hash, refresh_expires_at,
        #                       created_at)
        assert len(conn.session_inserts) == 1
        sess_args = conn.session_inserts[0]
        assert sess_args[0] == SINGLETON_ID
        assert sess_args[2] == "web"
        assert sess_args[3] == "private"
        assert sess_args[4] == TEST_TENANT_ID

    @pytest.mark.asyncio
    async def test_temporary_persistence_persisted_on_session(self) -> None:
        """Helper must store the requested device_persistence on the
        session row so refresh rotation can preserve it (B1 fix)."""
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="web",
            device_persistence="temporary",
            device_name="Public Computer",
            platform="macos",
            temporary_refresh_ttl_seconds=0,
        )
        async with conn.transaction():
            await issue_device_session(conn, request)
        sess_args = conn.session_inserts[0]
        assert sess_args[3] == "temporary"

    @pytest.mark.asyncio
    async def test_native_session_uses_native_client_kind(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="native",
            device_persistence="private",
            device_name="iPhone",
            platform="ios",
        )
        async with conn.transaction():
            await issue_device_session(conn, request)
        sess_args = conn.session_inserts[0]
        assert sess_args[2] == "native"

    @pytest.mark.asyncio
    async def test_tokens_are_unique_sha256_hashed_in_db(self) -> None:
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=TEST_TENANT_ID,
            client_kind="web",
            device_persistence="private",
            device_name="Test Device",
        )
        async with conn.transaction():
            issued = await issue_device_session(conn, request)
        sess_args = conn.session_inserts[0]
        # access_token_hash and refresh_token_hash must NOT match the
        # plaintext tokens (defensive: the helper must not write
        # plaintext to the DB).
        assert sess_args[5] != issued.access_token.encode("utf-8")
        assert sess_args[7] != issued.refresh_token.encode("utf-8")
        # And the hash of the access token should match the stored value.
        assert sess_args[5] == hash_token(issued.access_token)
        assert sess_args[7] == hash_token(issued.refresh_token)

    @pytest.mark.asyncio
    async def test_none_tenant_id_passes_through(self) -> None:
        """Singleton backfill path may pass tenant_id=None; the helper
        stores NULL until a future migration promotes to NOT NULL."""
        conn = HelperMockConn()
        request = IssueSessionRequest(
            user_id=SINGLETON_ID,
            tenant_id=None,
            client_kind="web",
            device_persistence="private",
            device_name="Test Device",
        )
        async with conn.transaction():
            await issue_device_session(conn, request)
        dev_args = conn.device_inserts[0]
        assert dev_args[1] is None
        sess_args = conn.session_inserts[0]
        assert sess_args[4] is None


class TestDevicePersistenceTypeAlias:
    def test_persistence_alias_accepts_both_values(self) -> None:
        # Literal types are static-only; this is a guard against
        # accidentally changing the literal mid-edit.
        literal_values = DevicePersistence.__args__
        assert "private" in literal_values
        assert "temporary" in literal_values
