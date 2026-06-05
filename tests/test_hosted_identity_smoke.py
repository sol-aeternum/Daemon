"""End-to-end smoke tests for hosted identity flows (TODO 21).

These tests exercise the real FastAPI route layer through ASGI (no
unit-only fake consumer paths). They cover the cross-cutting
acceptance criteria for the hosted identity surface:

  - Hosted email web sign-in through `/v1/auth/email/start` and
    `/v1/auth/email/complete`. Web completion returns an access
    token + sets the `__Host-daemon_refresh` cookie + omits the
    refresh token from the JSON body.
  - Native email completion contract: access + refresh tokens in
    the JSON body, no `Set-Cookie` header.
  - Hosted Google sign-in through `/v1/auth/google/start` and
    `/v1/auth/google/complete`. The first complete call succeeds;
    replaying the same challenge is rejected generically.
  - A Google ID token presented as `Authorization: Bearer <id>`
    on `/v1/auth/devices` is rejected (provider tokens are not
    API auth).
  - Device list/revoke: a device created via an identity sign-in
    appears in `/v1/auth/devices`; revoking it invalidates the
    access token used to call the device list.
  - Notification sink: a successful new-identity sign-in schedules
    a new-device email notification; a failed sign-in does not.

The tests are hermetic: a hand-rolled `SmokeMockPool` implements
the small asyncpg surface the routes touch, the
`google-auth` library is replaced with a stub verifier, and the
`schedule_device_notification` helper is replaced with a
capturing sink. No real Postgres, Redis, SMTP, Google, or external
network is involved.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings
from orchestrator.main import app
from orchestrator.services.identity import (
    ClaimResult,
    EmailChallengeInvalid,
    EmailChallengeLocked,
    EmailChallengeRow,
    InviteOnlyRejection,
    IssuedSession,
    MailSendResult,
    SignupDisabled,
    TenantRow,
    UserRow,
)


# ============================================================================
# Mock connection / pool (email + google + device list)
# ============================================================================


class _Record(dict):
    """Dict-like record supporting both `record["col"]` and `record.col`."""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


class SmokeMockConn:
    """In-memory asyncpg stand-in for the full smoke flow.

    Handles the queries the route layer emits for:
      * email challenge lookup (`SELECT ... FROM email_challenges`)
      * Google nonce insert/select/consume
      * signup invite lookup
      * device list (`SELECT id, display_name, ... FROM devices`)
      * device revoke (`UPDATE devices`, `UPDATE sessions`)
      * access-token verify (JOIN sessions + devices)
    """

    def __init__(self, pool: "SmokeMockPool") -> None:
        self._pool = pool

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, normalized_email FROM email_challenges WHERE id = $1"):
            challenge_id = args[0]
            row = self._pool.email_challenges.get(challenge_id)
            return None if row is None else dict(row)
        if q.startswith(
            "SELECT token_verifier_hash FROM signup_invites WHERE normalized_email = $1"
        ):
            return self._pool.invite_hash_by_email.get(args[0])
        if q.startswith("INSERT INTO google_nonce_challenges") and "RETURNING" in q:
            return self._handle_nonce_insert(args)
        if q.startswith(
            "SELECT id, nonce_verifier_hash, user_id_proposed, "
            "expires_at, consumed_at, created_at FROM google_nonce_challenges"
        ):
            return self._handle_nonce_select(args)
        if q.startswith("UPDATE google_nonce_challenges SET consumed_at"):
            return self._handle_nonce_consume(args)
        if q.startswith(
            "SELECT s.user_id, s.device_id, s.id AS session_id, "
            "s.access_expires_at, s.revoked_at AS session_revoked_at, "
            "d.revoked_at AS device_revoked_at FROM sessions s"
        ):
            return self._handle_session_lookup(args)
        if q.startswith("SELECT id, user_id, revoked_at FROM devices WHERE id"):
            return self._handle_device_revoke_lookup(args)
        return None

    async def fetchval(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith(
            "SELECT token_verifier_hash FROM signup_invites WHERE normalized_email = $1"
        ):
            return self._pool.invite_hash_by_email.get(args[0])
        if q.startswith("SELECT NOW()"):
            return datetime.now(timezone.utc)
        if "COUNT(*)" in q and "devices" in q and "revoked_at IS NULL" in q:
            user_id = args[0]
            count = 0
            for d in self._pool.devices.values():
                if d.get("user_id") == user_id and d.get("revoked_at") is None:
                    count += 1
            return count
        return 0

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith(
            "SELECT id, display_name, platform, created_at, last_seen_at, revoked_at FROM devices"
        ):
            user_id = args[0]
            include_revoked = "revoked_at IS NULL" not in q
            result = []
            for d in self._pool.devices.values():
                if d.get("user_id") != user_id:
                    continue
                if not include_revoked and d.get("revoked_at") is not None:
                    continue
                result.append(d)
            return result
        return []

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        if "UPDATE devices SET last_seen_at" in q:
            # Throttled last-seen update; no-op for the mock.
            return None
        if "UPDATE devices SET revoked_at" in q:
            target_id = args[0]
            for d in self._pool.devices.values():
                if d.get("id") == target_id:
                    d["revoked_at"] = datetime.now(timezone.utc)
            return None
        if "UPDATE sessions SET revoked_at" in q:
            target_id = args[0]
            for s in self._pool.sessions.values():
                if s.get("device_id") == target_id:
                    s["revoked_at"] = datetime.now(timezone.utc)
            return None
        return None

    @asynccontextmanager
    async def transaction(self):
        self._pool.transaction_depth += 1
        try:
            yield self
        finally:
            self._pool.transaction_depth -= 1

    # ----- Google nonce handlers -----

    def _handle_nonce_insert(self, args) -> _Record:
        nonce_verifier_hash = args[0]
        user_id_proposed = args[1]
        expires_at = args[2]
        ip_hash = args[3]
        user_agent_hash = args[4]
        challenge_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "id": challenge_id,
            "nonce_verifier_hash": nonce_verifier_hash,
            "user_id_proposed": user_id_proposed,
            "expires_at": expires_at,
            "consumed_at": None,
            "created_at": now,
            "ip_hash": ip_hash,
            "user_agent_hash": user_agent_hash,
        }
        self._pool.nonce_store.append(row)
        return _Record(
            {
                "id": row["id"],
                "nonce_verifier_hash": row["nonce_verifier_hash"],
                "user_id_proposed": row["user_id_proposed"],
                "expires_at": row["expires_at"],
                "consumed_at": None,
                "created_at": row["created_at"],
            }
        )

    def _handle_nonce_select(self, args):
        presented_verifier = args[0]
        for row in self._pool.nonce_store:
            if row["nonce_verifier_hash"] == presented_verifier:
                return _Record(
                    {
                        "id": row["id"],
                        "nonce_verifier_hash": row["nonce_verifier_hash"],
                        "user_id_proposed": row["user_id_proposed"],
                        "expires_at": row["expires_at"],
                        "consumed_at": row["consumed_at"],
                        "created_at": row["created_at"],
                    }
                )
        return None

    def _handle_nonce_consume(self, args):
        presented_verifier = args[0]
        now = datetime.now(timezone.utc)
        for row in self._pool.nonce_store:
            if row["nonce_verifier_hash"] != presented_verifier:
                continue
            if row["consumed_at"] is not None:
                return None
            if row["expires_at"] <= now:
                return None
            row["consumed_at"] = now
            return _Record(
                {
                    "id": row["id"],
                    "nonce_verifier_hash": row["nonce_verifier_hash"],
                    "user_id_proposed": row["user_id_proposed"],
                    "expires_at": row["expires_at"],
                    "consumed_at": row["consumed_at"],
                    "created_at": row["created_at"],
                }
            )
        return None

    # ----- session lookup (for _verify_access_token) -----

    def _handle_session_lookup(self, args):
        token_hash = args[0]
        session = self._pool.sessions_by_token_hash.get(token_hash)
        if session is None:
            return None
        if session.get("revoked_at") is not None:
            return None
        access_expires_at = session["access_expires_at"]
        if access_expires_at <= datetime.now(timezone.utc):
            return None
        device = self._pool.devices.get(str(session["device_id"]))
        if device is None or device.get("revoked_at") is not None:
            return None
        return {
            "user_id": session["user_id"],
            "device_id": session["device_id"],
            "session_id": session["id"],
            "access_expires_at": session["access_expires_at"],
            "session_revoked_at": session.get("revoked_at"),
            "device_revoked_at": device.get("revoked_at"),
        }

    def _handle_device_revoke_lookup(self, args):
        target_id, user_id = args[0], args[1]
        for d in self._pool.devices.values():
            if d.get("id") == target_id and d.get("user_id") == user_id:
                return {
                    "id": d["id"],
                    "user_id": d["user_id"],
                    "revoked_at": d.get("revoked_at"),
                }
        return None


class SmokeMockPool:
    """In-memory pool backing the smoke flow."""

    def __init__(self) -> None:
        self.email_challenges: dict[uuid.UUID, dict[str, Any]] = {}
        self.nonce_store: list[dict[str, Any]] = []
        self.invite_hash_by_email: dict[str, str] = {}
        self.devices: dict[str, dict[str, Any]] = {}
        self.sessions: dict[uuid.UUID, dict[str, Any]] = {}
        self.sessions_by_token_hash: dict[str, dict[str, Any]] = {}
        self.transaction_depth: int = 0

    async def fetchval(self, query: str, *args):
        return 0

    async def fetchrow(self, query: str, *args):
        return None

    async def execute(self, query: str, *args):
        return None

    @asynccontextmanager
    async def acquire(self):
        yield SmokeMockConn(self)

    async def close(self):
        return None


# ============================================================================
# App-state init mock
# ============================================================================


def make_mock_init(mock_pool: SmokeMockPool):
    import orchestrator.main as main_module

    original_init = main_module.init_app_state

    async def mock_init(settings):
        from orchestrator.db import AppState

        state = AppState(settings=settings)
        state.db_pool = cast(Any, mock_pool)
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


# ============================================================================
# Stub Google verifier (replaces google-auth library)
# ============================================================================


class _StubGoogleLibrary:
    def __init__(
        self,
        *,
        claims: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._claims = claims
        self._error = error
        self.calls: list[tuple[str, Any, Any]] = []

    def __call__(self, id_token, request, audience, *, certs_url=None):
        self.calls.append((id_token, request, audience))
        if self._error is not None:
            raise self._error
        assert self._claims is not None
        return self._claims


def _good_claims(
    *,
    sub: str = "google-sub-123",
    aud: str = "daemon-test-client-id.googleusercontent.com",
    iss: str = "https://accounts.google.com",
    email: str = "user@example.com",
    email_verified: bool = True,
    nonce: str | None = None,
) -> dict[str, Any]:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    return {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "iat": now_ts,
        "exp": now_ts + 600,
        "nonce": nonce or "",
    }


# ============================================================================
# Factories
# ============================================================================


def _claim_result() -> ClaimResult:
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    return ClaimResult(
        user=UserRow(
            id=user_id,
            normalized_email="user@example.com",
            email_verified_at=datetime.now(timezone.utc),
        ),
        tenant=TenantRow(
            id=tenant_id,
            owner_user_id=user_id,
            kind="personal",
            name="Personal",
        ),
        membership_role="owner",
        is_new_user=False,
        is_new_tenant=False,
        is_new_membership=False,
    )


def _issued_session(*, client_kind: str, refresh_max_age_seconds: int | None) -> IssuedSession:
    now = datetime.now(timezone.utc)
    return IssuedSession(
        access_token=f"access-{client_kind}",
        refresh_token=f"refresh-{client_kind}",
        access_expires_at=now + timedelta(minutes=30),
        refresh_expires_at=now + timedelta(days=90),
        session_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        client_kind=client_kind,
        refresh_transport="cookie" if client_kind == "web" else "body",
        refresh_max_age_seconds=refresh_max_age_seconds,
    )


def _seed_google_nonce(pool: SmokeMockPool, plaintext: str) -> uuid.UUID:
    from orchestrator.auth_pepper import validate_and_get_pepper
    from orchestrator.services.identity.google_verifier import compute_nonce_verifier

    settings = get_settings()
    pepper = validate_and_get_pepper(settings)
    verifier = compute_nonce_verifier(plaintext, pepper)
    challenge_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    pool.nonce_store.append(
        {
            "id": challenge_id,
            "nonce_verifier_hash": verifier,
            "user_id_proposed": None,
            "expires_at": now + timedelta(minutes=10),
            "consumed_at": None,
            "created_at": now,
        }
    )
    return challenge_id


def _install_session_for_token(
    pool: SmokeMockPool,
    issued: IssuedSession,
    *,
    user_id: uuid.UUID,
) -> None:
    """Wire an IssuedSession into the MockPool so that the access
    token will verify through `_verify_access_token` and the
    device/session rows are queryable for `/v1/auth/devices`.
    """
    from orchestrator.auth_tokens import hash_token

    device = {
        "id": issued.device_id,
        "user_id": user_id,
        "display_name": "Web Sign-In Device",
        "platform": issued.client_kind,
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=1),
        "last_seen_at": datetime.now(timezone.utc),
        "revoked_at": None,
    }
    pool.devices[str(issued.device_id)] = device
    session = {
        "id": issued.session_id,
        "user_id": user_id,
        "device_id": issued.device_id,
        "client_kind": issued.client_kind,
        "access_token_hash": hash_token(issued.access_token),
        "access_expires_at": issued.access_expires_at,
        "revoked_at": None,
    }
    pool.sessions[issued.session_id] = session
    pool.sessions_by_token_hash[hash_token(issued.access_token)] = session


# ============================================================================
# Fixtures
# ============================================================================


@pytest_asyncio.fixture
async def setup_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ALLOWED_ORIGINS", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_PUBLIC_ORIGIN", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "test-pepper-for-all-tests-12345678901234567890")
    monkeypatch.setenv("DAEMON_GOOGLE_CLIENT_ID", "daemon-test-client-id.googleusercontent.com")
    monkeypatch.setenv("DAEMON_SIGNUP_MODE", "open")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def route_client(setup_env):
    pool = SmokeMockPool()
    original = make_mock_init(pool)
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, pool
    finally:
        restore_init(original)


# ============================================================================
# /v1/auth/email/start + /v1/auth/email/complete — web smoke
# ============================================================================


class TestEmailWeb:
    @pytest.mark.asyncio
    async def test_email_web_full_flow_with_device_list_revoke_and_notification(
        self, route_client, monkeypatch
    ):
        """Hosted email web sign-in smoke:

        1. `/v1/auth/email/start` returns 202 with challenge_id.
        2. `/v1/auth/email/complete` (web, private) returns
           access_token in the JSON body AND sets the
           `__Host-daemon_refresh` cookie, and does NOT return
           a refresh token in the body.
        3. A successful sign-in schedules a new-device email
           notification with the verified recipient.
        4. The device created during sign-in appears in
           `/v1/auth/devices` when listing with the issued
           access token.
        5. Revoking that device via `DELETE /v1/auth/devices/{id}`
           invalidates the access token used to call the list.
        """
        client, pool = route_client
        sender = FakeMailSender()
        captured_notifications: list = []
        issued_sessions: list[IssuedSession] = []
        # Single shared identity for the smoke flow: the fake account
        # claim result AND the seeded device/session row MUST resolve
        # to the same user/tenant UUIDs, otherwise the "tenant/user/
        # device DB rows" continuity proof is weak.
        claim = _claim_result()

        # --- email start ---
        challenge_id = uuid.uuid4()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        async def fake_create(_self, request):
            return (
                EmailChallengeRow(
                    id=challenge_id,
                    normalized_email="user@example.com",
                    attempts_remaining=5,
                    expires_at=expires_at,
                    consumed_at=None,
                    locked_at=None,
                    created_at=datetime.now(timezone.utc),
                ),
                "123456",
            )

        async def fake_consume(_self, request):
            return EmailChallengeRow(
                id=request.challenge_id,
                normalized_email="user@example.com",
                attempts_remaining=5,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                consumed_at=datetime.now(timezone.utc),
                locked_at=None,
                created_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            return claim

        async def fake_issue(_conn, request):
            session = _issued_session(
                client_kind=request.client_kind,
                refresh_max_age_seconds=90 * 86400,
            )
            issued_sessions.append(session)
            return session

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        async def no_sleep(_started_at: float) -> None:
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.create_challenge_for_delivery",
            fake_create,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fake_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup._sleep_for_start_timing_floor", no_sleep
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.get_mail_sender", lambda _s: sender)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        # 1) start
        start_response = await client.post(
            "/v1/auth/email/start",
            json={"email": "user@example.com"},
            headers={"User-Agent": "pytest-agent/1.0"},
        )
        assert start_response.status_code == 202, start_response.text
        start_data = start_response.json()
        assert start_data["accepted"] is True
        assert start_data["challenge_id"] == str(challenge_id)
        assert start_data["expires_at"] == int(expires_at.timestamp())
        # Code must not be in the start response body.
        assert "123456" not in start_response.text
        # The code IS delivered through the mail sender.
        assert len(sender.messages) == 1
        assert sender.messages[0].to_address == "user@example.com"
        assert "123456" in sender.messages[0].body_text

        # 2) complete (web, private) -- access_token + cookie, no refresh body
        complete_response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "123456",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert complete_response.status_code == 200, complete_response.text
        complete_data = complete_response.json()
        assert complete_data["access_token"] == "access-web"
        # Web contract: no refresh token in body, refresh cookie set.
        assert "refresh_token" not in complete_data
        cookie_header = complete_response.headers.get("set-cookie", "")
        assert "__Host-daemon_refresh=refresh-web" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Max-Age=7776000" in cookie_header
        # ID tokens, codes, and other secret material must not leak.
        assert "123456" not in complete_response.text
        assert "refresh-web" not in complete_response.text

        # 3) Notification sink: one new-device email, recipient == verified email.
        assert len(captured_notifications) == 1
        notif = captured_notifications[0]
        assert notif.recipient_email == "user@example.com"
        assert notif.provider == "email"
        assert notif.platform == "web"

        # 4) The issued access token lets us see the device in /v1/auth/devices.
        issued = issued_sessions[0]
        # Reuse the same `claim` returned by fake_claim so the seeded
        # device/session row resolves to the same user_id that the
        # identity-completion path produced.
        _install_session_for_token(pool, issued, user_id=claim.user.id)
        access_token = issued.access_token

        devices_response = await client.get(
            "/v1/auth/devices",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert devices_response.status_code == 200, devices_response.text
        devices_data = devices_response.json()
        device_ids = [d["id"] for d in devices_data["devices"]]
        assert str(issued.device_id) in device_ids
        current_device = next(
            d for d in devices_data["devices"] if d["id"] == str(issued.device_id)
        )
        assert current_device["current"] is True
        assert current_device["revoked"] is False

        # 5) Revoke the device. Two devices exist (the issued one + the
        # existing single active device) so we can revoke a non-last device.
        # To keep this minimal and focused we revoke via the route: the
        # revoke handler decrements active count and updates state.
        # Add a second active device so the revoke target is NOT the last.
        other_device_id = uuid.uuid4()
        pool.devices[str(other_device_id)] = {
            "id": other_device_id,
            "user_id": claim.user.id,
            "display_name": "Other Device",
            "platform": "native",
            "created_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "last_seen_at": datetime.now(timezone.utc),
            "revoked_at": None,
        }

        revoke_response = await client.delete(
            f"/v1/auth/devices/{issued.device_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert revoke_response.status_code == 204, revoke_response.text
        # The device row is marked revoked in the mock pool.
        assert pool.devices[str(issued.device_id)]["revoked_at"] is not None
        # Sessions for the device are revoked, so the access token no
        # longer authorizes API calls.
        after_revoke = await client.get(
            "/v1/auth/devices",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert after_revoke.status_code == 401, after_revoke.text


# ============================================================================
# /v1/auth/email/complete — native completion contract
# ============================================================================


class TestEmailNativeCompletionContract:
    @pytest.mark.asyncio
    async def test_native_complete_returns_refresh_json_and_no_cookie(
        self, route_client, monkeypatch
    ):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.email_challenges[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }

        async def fake_consume(_self, request):
            return EmailChallengeRow(
                id=request.challenge_id,
                normalized_email="user@example.com",
                attempts_remaining=5,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                consumed_at=datetime.now(timezone.utc),
                locked_at=None,
                created_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="native", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fake_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "123456",
                "client_kind": "native",
                "device_persistence": "temporary",
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()
        # Native contract: refresh in body, no cookie.
        assert data["access_token"] == "access-native"
        assert data["refresh_token"] == "refresh-native"
        assert response.headers.get("set-cookie") is None


# ============================================================================
# Notification sink — failed sign-in must NOT trigger notification
# ============================================================================


class TestEmailNotificationSinkOnFailure:
    @pytest.mark.asyncio
    async def test_no_notification_on_wrong_code(self, route_client, monkeypatch):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.email_challenges[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }
        captured_notifications: list = []

        async def fake_consume(_self, _request):
            raise EmailChallengeInvalid("wrong code")

        def fail_claim(_self, **_kwargs):
            raise AssertionError("claim must not be called on wrong code")

        async def fail_issue(_conn, _request):
            raise AssertionError("issue must not be called on wrong code")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity", fail_claim
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "000000",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "code_invalid_or_expired"}
        assert captured_notifications == [], "expected no notification on failed sign-in"

    @pytest.mark.asyncio
    async def test_no_notification_on_invite_only_rejection(self, route_client, monkeypatch):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.email_challenges[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }
        captured_notifications: list = []

        async def fake_consume(_self, request):
            return EmailChallengeRow(
                id=request.challenge_id,
                normalized_email="user@example.com",
                attempts_remaining=5,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                consumed_at=datetime.now(timezone.utc),
                locked_at=None,
                created_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            raise InviteOnlyRejection("invite required")

        async def fail_issue(_conn, _request):
            raise AssertionError("issue must not be called on invite rejection")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity", fake_claim
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "123456",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "code_invalid_or_expired"}
        assert captured_notifications == [], "expected no notification when claim fails"


# ============================================================================
# /v1/auth/google/start + /v1/auth/google/complete — nonce/replay smoke
# ============================================================================


class TestGoogleReplay:
    @pytest.mark.asyncio
    async def test_google_replay_first_complete_succeeds_replay_rejected_and_provider_token_not_bearer(
        self, route_client, monkeypatch
    ):
        """Hosted Google sign-in smoke:

        1. `/v1/auth/google/start` returns 202 with challenge_id + nonce.
        2. First `/v1/auth/google/complete` succeeds and returns the
           access token + refresh cookie (web) and schedules a
           new-device notification.
        3. Replaying the same challenge+nonce is rejected generically.
        4. The Google ID token is rejected as `Authorization: Bearer`
           on `/v1/auth/devices` — provider tokens are NOT API auth.
        """
        client, pool = route_client
        captured_notifications: list = []

        # Pre-seed a nonce so the consume path is exercised.
        plaintext = "plaintext-google-nonce-SMOKE"
        challenge_id = _seed_google_nonce(pool, plaintext)

        claims = _good_claims(nonce=plaintext)
        stub = _StubGoogleLibrary(claims=claims)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.default_google_id_token_verifier",
            lambda: stub,
        )

        async def fake_claim(_self, **_kwargs):
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fake_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        # 1) start
        start_response = await client.post(
            "/v1/auth/google/start", headers={"User-Agent": "pytest-agent/1.0"}
        )
        assert start_response.status_code == 202, start_response.text
        start_data = start_response.json()
        assert "challenge_id" in start_data
        assert "nonce" in start_data
        assert "expires_at" in start_data
        uuid.UUID(start_data["challenge_id"])
        # No provider tokens / user_id / tenant_id in start response.
        assert "id_token" not in start_data
        assert "user_id" not in start_data
        assert "tenant_id" not in start_data

        # 2) first complete — succeeds
        first = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id),
                "nonce": plaintext,
                "id_token": "id-token-from-google",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert first.status_code == 200, first.text
        first_data = first.json()
        assert first_data["access_token"] == "access-web"
        assert "refresh_token" not in first_data
        assert isinstance(first_data["expires_at"], int)
        first_cookie = first.headers.get("set-cookie", "")
        assert "__Host-daemon_refresh=refresh-web" in first_cookie
        assert "HttpOnly" in first_cookie
        # ID token must never appear in body or cookie.
        assert "id-token-from-google" not in first.text
        assert "id-token-from-google" not in first_cookie
        # Notification was scheduled for the verified email.
        assert len(captured_notifications) == 1
        assert captured_notifications[0].recipient_email == "user@example.com"
        assert captured_notifications[0].provider == "google"

        # Nonce was consumed exactly once.
        assert pool.nonce_store[0]["consumed_at"] is not None

        # 3) replay rejected generically
        replay = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id),
                "nonce": plaintext,
                "id_token": "id-token-from-google",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert replay.status_code == 401, replay.text
        assert replay.json() == {"detail": "google_sign_in_failed"}
        assert "id-token-from-google" not in replay.text
        assert replay.headers.get("set-cookie") is None
        # No additional notification on the replay path.
        assert len(captured_notifications) == 1

        # 4) provider token not accepted as API auth on /v1/auth/devices
        bearer_response = await client.get(
            "/v1/auth/devices",
            headers={"Authorization": "Bearer id-token-from-google"},
        )
        assert bearer_response.status_code == 401, bearer_response.text
        assert "id-token-from-google" not in bearer_response.text

    @pytest.mark.asyncio
    async def test_google_native_completion_returns_refresh_json_and_no_cookie(
        self, route_client, monkeypatch
    ):
        client, pool = route_client
        plaintext = "plaintext-google-nonce-NATIVE"
        challenge_id = _seed_google_nonce(pool, plaintext)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.default_google_id_token_verifier",
            lambda: _StubGoogleLibrary(claims=_good_claims(nonce=plaintext)),
        )

        async def fake_claim(_self, **_kwargs):
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="native", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fake_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id),
                "nonce": plaintext,
                "id_token": "id-token",
                "client_kind": "native",
                "device_persistence": "temporary",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        # Native contract: refresh in body, no cookie.
        assert data["access_token"] == "access-native"
        assert data["refresh_token"] == "refresh-native"
        assert response.headers.get("set-cookie") is None
        assert "id-token" not in response.text


# ============================================================================
# Generic failure collapses to a non-oracle response (email + google)
# ============================================================================


class TestGenericFailureCollapse:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure_source", "failure"),
        [
            ("consume", EmailChallengeInvalid("wrong code")),
            ("consume", EmailChallengeLocked("locked")),
            ("claim", InviteOnlyRejection("invite required")),
            ("claim", SignupDisabled("disabled")),
        ],
    )
    async def test_email_complete_collapses_failures_to_generic_response(
        self, route_client, monkeypatch, failure_source, failure
    ):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.email_challenges[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }

        async def fake_consume(_self, request):
            if failure_source == "consume":
                raise failure
            return EmailChallengeRow(
                id=request.challenge_id,
                normalized_email="user@example.com",
                attempts_remaining=5,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                consumed_at=datetime.now(timezone.utc),
                locked_at=None,
                created_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            if failure_source == "claim":
                raise failure
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fake_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "123456",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "code_invalid_or_expired"}
        assert "refresh-web" not in response.text
        assert response.headers.get("set-cookie") is None


# ============================================================================
# Test mail sender (capture for start-route assertion)
# ============================================================================


class FakeMailSender:
    def __init__(self) -> None:
        self.messages: list = []

    async def send(self, message):
        self.messages.append(message)
        return MailSendResult(
            sent_at=datetime.now(timezone.utc),
            sink_kind="console",
        )
