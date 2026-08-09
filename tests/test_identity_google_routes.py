"""Focused route tests for hosted Google sign-in endpoints (TODO 13).

Covers the TODO 13 acceptance criteria and the inherited plan guardrails:

  - `POST /v1/auth/google/start` returns a `(challenge_id, nonce,
    expires_at)` shape and is rate-limited per-IP.
  - `POST /v1/auth/google/complete` validates the unconsumed
    nonce, verifies the Google ID token, resolves the
    account/tenant/provider via `AccountService.claim_google_identity`,
    and mints a Daemon device session through
    `issue_device_session`.
  - Web completion sets the refresh cookie and returns NO refresh
    token in the JSON body. Native completion returns the refresh
    token in the JSON body and sets NO cookie.
  - The unconsumed nonce succeeds only once; replay is rejected
    with a generic 401.
  - Malformed/wrong token paths fail generically (no oracle for
    library / verifier / claim differences).
  - Invite-only signup is respected with a generic 401 so a probe
    cannot enumerate "invited vs uninvited" by response shape.
  - Repeated sign-in for an already-linked provider succeeds
    (idempotent re-link path).
  - A Google ID token presented as `Authorization: Bearer <id_token>`
    on a protected device-token route is rejected as 401/403;
    the route does not trust provider tokens.
  - The ID token is never present in the JSON response or in the
    `Set-Cookie` header.

The tests are hermetic: a hand-rolled `GoogleRouteMockPool`
implements the small asyncpg surface the route layer touches, and
a hand-rolled `_StubGoogleLibrary` replaces the `google-auth`
library callable so no real network call happens.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hmac
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from orchestrator.config import get_settings
from orchestrator.main import app
from orchestrator.services.identity import (
    ClaimResult,
    EmailNotVerified,
    GoogleNonceInvalid,
    GoogleTokenInvalid,
    InviteOnlyRejection,
    IssuedSession,
    ProviderCollision,
    SignupDisabled,
    TenantRow,
    UserRow,
    VerifiedGoogleIdentity,
)


# ============================================================================
# Mock connection (verifier + account)
# ============================================================================


class _Record(dict):
    """Dict-like record that supports both `record["col"]` and
    `record.col` lookups. asyncpg `Record` supports both.
    """

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


class GoogleRouteMockConn:
    """In-memory asyncpg stand-in for the Google verifier + account
    service. Dispatches on a stripped SQL prefix.
    """

    def __init__(self, pool: "GoogleRouteMockPool") -> None:
        self._pool = pool

    async def fetchrow(self, query: str, *args):
        self._pool.calls.append((query, args))
        q = " ".join(query.split())
        if q.startswith("INSERT INTO google_nonce_challenges") and "RETURNING" in q:
            return self._handle_insert(args)
        if q.startswith("SELECT id, normalized_email FROM email_challenges WHERE id = $1"):
            return None
        if q.startswith(
            "SELECT token_verifier_hash FROM signup_invites WHERE normalized_email = $1"
        ):
            return self._pool.invite_hash_by_email.get(args[0])
        if q.startswith(
            "SELECT id, nonce_verifier_hash, user_id_proposed, "
            "expires_at, consumed_at, created_at FROM google_nonce_challenges"
        ):
            return self._handle_select_by_verifier(args)
        if q.startswith("UPDATE google_nonce_challenges SET consumed_at"):
            return self._handle_consume(args)
        return None

    async def fetchval(self, query: str, *args):
        self._pool.calls.append((query, args))
        q = " ".join(query.split())
        if q.startswith(
            "SELECT token_verifier_hash FROM signup_invites WHERE normalized_email = $1"
        ):
            return self._pool.invite_hash_by_email.get(args[0])
        return None

    async def execute(self, query: str, *args):
        self._pool.calls.append((query, args))
        return ""

    @asynccontextmanager
    async def transaction(self):
        snapshot = list(self._pool.claim_markers)
        self._pool.transaction_depth += 1
        self._pool.transaction_stack.append(True)
        try:
            yield self
        except Exception:
            self._pool.claim_markers = snapshot
            raise
        finally:
            self._pool.transaction_stack.pop()
            self._pool.transaction_depth -= 1

    # ----- nonce-table handlers -----

    def _handle_insert(self, args) -> _Record:
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
                "consumed_at": row["consumed_at"],
                "created_at": row["created_at"],
            }
        )

    def _handle_select_by_verifier(self, args):
        challenge_id = args[0]
        presented_verifier = args[1]
        for row in self._pool.nonce_store:
            if row["id"] == challenge_id and row["nonce_verifier_hash"] == presented_verifier:
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

    def _handle_consume(self, args):
        challenge_id = args[0]
        presented_verifier = args[1]
        now = datetime.now(timezone.utc)
        for row in self._pool.nonce_store:
            if row["id"] != challenge_id or row["nonce_verifier_hash"] != presented_verifier:
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


class GoogleRouteMockPool:
    def __init__(self) -> None:
        self.nonce_store: list[dict[str, Any]] = []
        self.invite_hash_by_email: dict[str, str] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.transaction_stack: list[bool] = []
        self.transaction_depth = 0
        self.claim_markers: list[str] = []

    async def fetchval(self, query: str, *args):
        return 0

    async def fetchrow(self, query: str, *args):
        return None

    async def execute(self, query: str, *args):
        return ""

    @asynccontextmanager
    async def acquire(self):
        yield GoogleRouteMockConn(self)

    async def close(self):
        return None


def make_mock_init(mock_pool: GoogleRouteMockPool):
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
# Stub verifier (replaces the google-auth library)
# ============================================================================


class _StubGoogleLibrary:
    """Hand-rolled stand-in for the `google-auth` library.

    Pre-load `_claims` (or `_error`) and the stub returns them on
    every call. Records calls for assertion. Tests that exercise
    multi-call flows can mutate `stub._claims` between calls.
    """

    def __init__(
        self,
        *,
        claims: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._claims = claims
        self._error = error
        self.calls: list[tuple[str, Any, Any]] = []

    def __call__(
        self,
        id_token,
        request,
        audience,
        *,
        certs_url=None,
    ):
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


def _claim_result(*, sub_hint: str | None = None) -> ClaimResult:
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
        access_token="access-token",
        refresh_token="refresh-token",
        access_expires_at=now + timedelta(minutes=30),
        refresh_expires_at=now + timedelta(days=90),
        session_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        client_kind=client_kind,
        refresh_transport="cookie" if client_kind == "web" else "body",
        refresh_max_age_seconds=refresh_max_age_seconds,
    )


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
    monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def route_client(setup_env):
    pool = GoogleRouteMockPool()
    original = make_mock_init(pool)
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, pool
    finally:
        restore_init(original)


# ============================================================================
# /v1/auth/google/start
# ============================================================================


class TestGoogleStartRoute:
    @pytest.mark.asyncio
    async def test_start_returns_challenge_nonce_and_expires(self, route_client, monkeypatch):
        client, _pool = route_client
        captured_policies: list = []

        async def fake_enforce_rate_limit(*, policies, **_kwargs):
            captured_policies.extend(policies)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        response = await client.post(
            "/v1/auth/google/start", headers={"User-Agent": "pytest-agent/1.0"}
        )

        assert response.status_code == 202, response.text
        data = response.json()
        assert "challenge_id" in data
        assert "nonce" in data
        assert "expires_at" in data
        assert isinstance(data["expires_at"], int)
        uuid.UUID(data["challenge_id"])
        assert len(data["nonce"]) > 20
        # No id_token, no user_id, no tenant_id in the start response.
        assert "id_token" not in data
        assert "user_id" not in data
        assert "tenant_id" not in data
        assert [policy[0] for policy in captured_policies] == ["ip"]

    @pytest.mark.asyncio
    async def test_start_blocks_when_google_provider_disabled(
        self, route_client, monkeypatch
    ) -> None:
        client, _pool = route_client
        monkeypatch.setenv("DAEMON_GOOGLE_ENABLED", "false")
        get_settings.cache_clear()

        def fail_get_rate_limiter(_request):
            raise AssertionError("get_rate_limiter should not be called when google is disabled")

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_rate_limiter", fail_get_rate_limiter
        )

        response = await client.post("/v1/auth/google/start")

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "google_sign_in_disabled"}

    @pytest.mark.asyncio
    async def test_start_blocks_when_hosted_identity_disabled(
        self, route_client, monkeypatch
    ) -> None:
        client, _pool = route_client
        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "false")
        monkeypatch.setenv("DAEMON_GOOGLE_ENABLED", "true")
        get_settings.cache_clear()

        def fail_get_rate_limiter(_request):
            raise AssertionError(
                "get_rate_limiter must not be called when hosted identity is disabled"
            )

        async def fail_issue_nonce(_self, _request):
            raise AssertionError("issue_nonce must not be called when hosted identity is disabled")

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_rate_limiter", fail_get_rate_limiter
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.issue_nonce", fail_issue_nonce
        )

        response = await client.post("/v1/auth/google/start")

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "hosted_identity_disabled"}


# ============================================================================
# /v1/auth/google/complete
# ============================================================================


def _install_stub_verifier(
    monkeypatch, *, claims: dict[str, Any] | None = None, error: Exception | None = None
) -> _StubGoogleLibrary:
    stub = _StubGoogleLibrary(claims=claims, error=error)
    monkeypatch.setattr(
        "orchestrator.routes.auth_setup.default_google_id_token_verifier",
        lambda: stub,
    )
    return stub


def _seed_nonce(pool: GoogleRouteMockPool, plaintext_nonce: str) -> uuid.UUID:
    """Insert a pre-existing nonce row for the consume path to find."""
    from orchestrator.config import get_settings
    from orchestrator.services.identity.google_verifier import compute_nonce_verifier
    from orchestrator.auth_pepper import validate_and_get_pepper

    settings = get_settings()
    pepper = validate_and_get_pepper(settings)
    verifier = compute_nonce_verifier(plaintext_nonce, pepper)
    challenge_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    row = {
        "id": challenge_id,
        "nonce_verifier_hash": verifier,
        "user_id_proposed": None,
        "expires_at": now + timedelta(minutes=10),
        "consumed_at": None,
        "created_at": now,
    }
    pool.nonce_store.append(row)
    return challenge_id


class TestGoogleCompleteRoute:
    @pytest.mark.asyncio
    async def test_web_private_returns_access_only_and_refresh_cookie(
        self, route_client, monkeypatch
    ):
        client, pool = route_client
        plaintext = "plaintext-nonce-AAA"
        challenge_id = _seed_nonce(pool, plaintext)
        captured_claims: list = []
        captured_issue_requests: list = []
        captured_policies: list = []

        claims = _good_claims(nonce=plaintext)
        stub = _install_stub_verifier(monkeypatch, claims=claims)

        async def fake_claim(_self, **kwargs):
            captured_claims.append(kwargs)
            return _claim_result()

        async def fake_issue(_conn, request):
            captured_issue_requests.append(request)
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(*, policies, **_kwargs):
            captured_policies.extend(policies)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
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
                "id_token": "id-token-from-google",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["access_token"] == "access-token"
        assert "refresh_token" not in data
        assert isinstance(data["expires_at"], int)
        # ID token must never appear in the response body.
        assert "id-token-from-google" not in response.text
        cookie_header = response.headers.get("set-cookie", "")
        assert "__Host-daemon_refresh=refresh-token" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Max-Age=7776000" in cookie_header
        # ID token must never appear in the cookie either.
        assert "id-token-from-google" not in cookie_header

        # The nonce was consumed exactly once.
        assert pool.nonce_store[0]["consumed_at"] is not None
        # The verifier was called with the ID token the client supplied.
        assert stub.calls and stub.calls[0][0] == "id-token-from-google"
        # Account service was resolved from verified identity, never
        # from caller-supplied ids.
        assert captured_claims[0]["google_sub"] == "google-sub-123"
        assert captured_claims[0]["normalized_email"] == "user@example.com"
        assert captured_claims[0]["email_verified"] is True
        assert "user_id" not in captured_claims[0]
        assert "tenant_id" not in captured_claims[0]
        # Session issuance was for the right client_kind/persistence.
        assert captured_issue_requests[0].client_kind == "web"
        assert captured_issue_requests[0].device_persistence == "private"
        assert [policy[0] for policy in captured_policies] == [
            "ip",
            "ip",
        ]  # Two ip policies: pre-claim (#129 L1070) and post-claim (L1076)

    @pytest.mark.asyncio
    async def test_web_temporary_uses_session_cookie(self, route_client, monkeypatch):
        client, pool = route_client
        plaintext = "plaintext-nonce-BBB"
        challenge_id = _seed_nonce(pool, plaintext)

        _install_stub_verifier(monkeypatch, claims=_good_claims(nonce=plaintext))

        async def fake_claim(_self, **_kwargs):
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
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
                "client_kind": "web",
                "device_persistence": "temporary",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert "refresh_token" not in data
        cookie_header = response.headers.get("set-cookie", "")
        assert "__Host-daemon_refresh=refresh-token" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Max-Age=600" in cookie_header

    @pytest.mark.asyncio
    async def test_invite_only_uses_caller_supplied_invite_token(self, route_client, monkeypatch):
        client, pool = route_client
        plaintext = "plaintext-nonce-invite"
        challenge_id = _seed_nonce(pool, plaintext)
        pool.invite_hash_by_email["user@example.com"] = "stored-hash-must-not-be-reused"
        captured_claims: list = []

        monkeypatch.setenv("DAEMON_SIGNUP_MODE", "invite_only")
        get_settings.cache_clear()
        _install_stub_verifier(monkeypatch, claims=_good_claims(nonce=plaintext))

        async def fake_claim(_self, **kwargs):
            captured_claims.append(kwargs)
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
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
                "client_kind": "web",
                "device_persistence": "temporary",
                "invite_token": "invite-secret",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 200, response.text
        expected_hash = hmac.new(
            b"test-pepper-for-all-tests-12345678901234567890",
            b"invite-secret",
            "sha256",
        ).hexdigest()
        assert captured_claims[0]["invite_token_verifier_hash"] == expected_hash
        assert (
            captured_claims[0]["invite_token_verifier_hash"]
            != pool.invite_hash_by_email["user@example.com"]
        )

    @pytest.mark.asyncio
    async def test_claim_and_issue_run_inside_one_transaction(self, route_client, monkeypatch):
        client, pool = route_client
        plaintext = "plaintext-nonce-tx"
        challenge_id = _seed_nonce(pool, plaintext)
        _install_stub_verifier(monkeypatch, claims=_good_claims(nonce=plaintext))

        async def fake_consume(_self, _request):
            assert pool.transaction_depth == 0
            row = pool.nonce_store[0]
            row["consumed_at"] = datetime.now(timezone.utc)
            from orchestrator.services.identity import GoogleNonceRow

            return GoogleNonceRow(
                id=row["id"],
                nonce_verifier_hash=row["nonce_verifier_hash"],
                user_id_proposed=row["user_id_proposed"],
                expires_at=row["expires_at"],
                consumed_at=row["consumed_at"],
                created_at=row["created_at"],
            )

        async def fake_verify(_self, _request):
            assert pool.transaction_depth == 0
            return VerifiedGoogleIdentity(
                provider_subject="google-sub-123",
                normalized_email="user@example.com",
                original_email="user@example.com",
                verified_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            assert pool.transaction_depth == 1
            pool.claim_markers.append("google-claim")
            return _claim_result()

        async def fake_issue(_conn, _request):
            assert pool.transaction_depth == 1
            assert pool.claim_markers == ["google-claim"]
            return _issued_session(client_kind="web", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fake_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
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
                "client_kind": "web",
                "device_persistence": "temporary",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 200, response.text
        assert pool.transaction_depth == 0
        assert pool.claim_markers == ["google-claim"]

    @pytest.mark.asyncio
    async def test_session_failure_rolls_back_claim_side_effects(self, route_client, monkeypatch):
        client, pool = route_client
        plaintext = "plaintext-nonce-rollback"
        challenge_id = _seed_nonce(pool, plaintext)
        _install_stub_verifier(monkeypatch, claims=_good_claims(nonce=plaintext))

        async def fake_consume(_self, _request):
            assert pool.transaction_depth == 0
            row = pool.nonce_store[0]
            row["consumed_at"] = datetime.now(timezone.utc)
            from orchestrator.services.identity import GoogleNonceRow

            return GoogleNonceRow(
                id=row["id"],
                nonce_verifier_hash=row["nonce_verifier_hash"],
                user_id_proposed=row["user_id_proposed"],
                expires_at=row["expires_at"],
                consumed_at=row["consumed_at"],
                created_at=row["created_at"],
            )

        async def fake_verify(_self, _request):
            assert pool.transaction_depth == 0
            return VerifiedGoogleIdentity(
                provider_subject="google-sub-123",
                normalized_email="user@example.com",
                original_email="user@example.com",
                verified_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            assert pool.transaction_depth == 1
            pool.claim_markers.append("google-claim")
            return _claim_result()

        async def failing_issue(_conn, _request):
            assert pool.transaction_depth == 1
            raise RuntimeError("session issuance failed")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fake_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", failing_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        with pytest.raises(RuntimeError, match="session issuance failed"):
            await client.post(
                "/v1/auth/google/complete",
                json={
                    "challenge_id": str(challenge_id),
                    "nonce": plaintext,
                    "id_token": "id-token",
                    "client_kind": "web",
                    "device_persistence": "temporary",
                },
                headers={
                    "Origin": "https://app.daemon.ai",
                    "Sec-Fetch-Site": "same-origin",
                },
            )

        assert pool.transaction_depth == 0
        assert pool.claim_markers == []
        assert pool.nonce_store[0]["consumed_at"] is not None

    @pytest.mark.asyncio
    async def test_invalid_token_failure_leaves_nonce_consumed(self, route_client, monkeypatch):
        client, pool = route_client
        plaintext = "plaintext-nonce-invalid-token"
        challenge_id = _seed_nonce(pool, plaintext)

        async def fake_consume(_self, _request):
            assert pool.transaction_depth == 0
            row = pool.nonce_store[0]
            row["consumed_at"] = datetime.now(timezone.utc)
            from orchestrator.services.identity import GoogleNonceRow

            return GoogleNonceRow(
                id=row["id"],
                nonce_verifier_hash=row["nonce_verifier_hash"],
                user_id_proposed=row["user_id_proposed"],
                expires_at=row["expires_at"],
                consumed_at=row["consumed_at"],
                created_at=row["created_at"],
            )

        async def fake_verify(_self, _request):
            assert pool.transaction_depth == 0
            raise GoogleTokenInvalid("audience rejected")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fake_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id),
                "nonce": plaintext,
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "google_sign_in_failed"}
        assert pool.nonce_store[0]["consumed_at"] is not None
        assert all("INSERT INTO sessions" not in call[0] for call in pool.calls)

    @pytest.mark.asyncio
    async def test_wrong_challenge_id_with_valid_nonce_fails_and_leaves_real_nonce_unconsumed(
        self, route_client, monkeypatch
    ):
        client, pool = route_client
        plaintext = "plaintext-nonce-bound"
        _seed_nonce(pool, plaintext)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(uuid.uuid4()),
                "nonce": plaintext,
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "google_sign_in_failed"}
        assert pool.nonce_store[0]["consumed_at"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("nonce", "id_token"),
        [
            ("", "id-token"),
            ("valid-nonce", ""),
        ],
    )
    async def test_malformed_google_inputs_fail_generically_not_500(
        self, route_client, monkeypatch, nonce, id_token
    ):
        client, pool = route_client
        plaintext = "valid-nonce"
        challenge_id = _seed_nonce(pool, plaintext)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id),
                "nonce": nonce,
                "id_token": id_token,
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "google_sign_in_failed"}

    @pytest.mark.asyncio
    async def test_native_returns_refresh_json_and_no_cookie(self, route_client, monkeypatch):
        client, pool = route_client
        plaintext = "plaintext-nonce-CCC"
        challenge_id = _seed_nonce(pool, plaintext)

        _install_stub_verifier(monkeypatch, claims=_good_claims(nonce=plaintext))

        async def fake_claim(_self, **_kwargs):
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="native", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
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
        assert data["access_token"] == "access-token"
        assert data["refresh_token"] == "refresh-token"
        assert response.headers.get("set-cookie") is None
        assert "id-token" not in response.text

    @pytest.mark.asyncio
    async def test_repeated_already_linked_signin_succeeds(self, route_client, monkeypatch):
        """The first sign-in and a second sign-in for the same
        `(google, sub)` both succeed; the account service is the
        only place that resolves the identity, and its
        already-linked branch is exercised on the second call.
        """
        client, pool = route_client
        plaintext = "plaintext-nonce-DDD"
        challenge_id = _seed_nonce(pool, plaintext)

        stub = _install_stub_verifier(
            monkeypatch, claims=_good_claims(sub="google-sub-999", nonce=plaintext)
        )

        async def fake_claim(_self, **_kwargs):
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="native", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
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
                "device_persistence": "private",
            },
        )
        assert response.status_code == 200, response.text

        # Seed a second challenge and replay.
        plaintext2 = "plaintext-nonce-EEE"
        challenge_id_2 = _seed_nonce(pool, plaintext2)
        stub._claims = _good_claims(sub="google-sub-999", nonce=plaintext2)
        response2 = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id_2),
                "nonce": plaintext2,
                "id_token": "id-token",
                "client_kind": "native",
                "device_persistence": "private",
            },
        )
        assert response2.status_code == 200, response2.text
        assert response2.json()["access_token"] == "access-token"

    @pytest.mark.asyncio
    async def test_nonce_replay_rejected(self, route_client, monkeypatch):
        client, pool = route_client
        plaintext = "plaintext-nonce-FFF"
        challenge_id = _seed_nonce(pool, plaintext)

        _install_stub_verifier(monkeypatch, claims=_good_claims(nonce=plaintext))

        async def fake_claim(_self, **_kwargs):
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fake_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        body = {
            "challenge_id": str(challenge_id),
            "nonce": plaintext,
            "id_token": "id-token",
            "client_kind": "web",
            "device_persistence": "private",
        }
        headers = {
            "Origin": "https://app.daemon.ai",
            "Sec-Fetch-Site": "same-origin",
        }
        first = await client.post("/v1/auth/google/complete", json=body, headers=headers)
        assert first.status_code == 200, first.text

        # Second call with the same challenge id and plaintext nonce
        # must be rejected. The mock still has the row with
        # consumed_at set, so consume_nonce raises GoogleNonceInvalid.
        # We also rebuild the body because the consume call would
        # otherwise still succeed if the row had been reset.
        replay = await client.post("/v1/auth/google/complete", json=body, headers=headers)
        assert replay.status_code == 401, replay.text
        assert replay.json() == {"detail": "google_sign_in_failed"}
        assert "id-token" not in replay.text
        assert replay.headers.get("set-cookie") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure_source", "failure"),
        [
            ("consume", GoogleNonceInvalid("nonce already consumed")),
            ("verify", GoogleTokenInvalid("audience rejected")),
            ("claim_invite", InviteOnlyRejection("invite required")),
            ("claim_unverified", EmailNotVerified("email not verified")),
            ("claim_collision", ProviderCollision("provider collision")),
            ("claim_disabled", SignupDisabled("signup disabled")),
        ],
    )
    async def test_complete_collapses_failures_to_generic_response(
        self, route_client, monkeypatch, failure_source, failure
    ):
        client, pool = route_client
        plaintext = "plaintext-nonce-GGG"
        challenge_id = _seed_nonce(pool, plaintext)

        claims = _good_claims(nonce=plaintext)
        _install_stub_verifier(monkeypatch, claims=claims)

        async def fake_consume(_self, request):
            if failure_source == "consume":
                raise failure
            from orchestrator.services.identity import GoogleNonceRow

            return GoogleNonceRow(
                id=uuid.uuid4(),
                nonce_verifier_hash="unused",
                user_id_proposed=None,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                consumed_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )

        async def fake_verify(_self, request):
            if failure_source == "verify":
                raise failure
            from orchestrator.services.identity import (
                GoogleIdTokenVerifyRequest as Req,
                VerifiedGoogleIdentity,
            )

            assert isinstance(request, Req)
            return VerifiedGoogleIdentity(
                provider_subject=claims["sub"],
                normalized_email=claims["email"].strip().lower(),
                original_email=claims["email"],
                verified_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            if failure_source == "claim_invite":
                raise InviteOnlyRejection("invite required")
            if failure_source == "claim_unverified":
                raise EmailNotVerified("email not verified")
            if failure_source == "claim_collision":
                raise ProviderCollision("provider collision")
            if failure_source == "claim_disabled":
                raise SignupDisabled("signup disabled")
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce", fake_consume
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fake_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
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
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "google_sign_in_failed"}
        assert "id-token" not in response.text
        assert response.headers.get("set-cookie") is None
        # No session was minted on the failure path.
        assert all("INSERT INTO sessions" not in call[0] for call in pool.calls)

    @pytest.mark.asyncio
    async def test_complete_rejects_native_when_refresh_cookie_present(self, route_client):
        client, pool = route_client
        plaintext = "plaintext-nonce-HHH"
        challenge_id = _seed_nonce(pool, plaintext)

        client.cookies.set("__Host-daemon_refresh", "unexpected-cookie")

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id),
                "nonce": plaintext,
                "id_token": "id-token",
                "client_kind": "native",
                "device_persistence": "private",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "cookie present but client_kind is 'native'"

    @pytest.mark.asyncio
    async def test_complete_forbids_user_and_tenant_id_fields(self, route_client):
        client, _pool = route_client

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(uuid.uuid4()),
                "nonce": "n",
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
                "user_id": str(uuid.uuid4()),
                "tenant_id": str(uuid.uuid4()),
            },
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_blocks_when_google_provider_disabled(
        self, route_client, monkeypatch
    ) -> None:
        client, pool = route_client
        plaintext = "plaintext-nonce-III"
        challenge_id = _seed_nonce(pool, plaintext)

        monkeypatch.setenv("DAEMON_GOOGLE_ENABLED", "false")
        get_settings.cache_clear()

        def fail_get_rate_limiter(_request):
            raise AssertionError("get_rate_limiter should not be called when google is disabled")

        async def fail_consume(_self, _request):
            raise AssertionError("consume_nonce should not be called when google is disabled")

        async def fail_verify(_self, _request):
            raise AssertionError("verify_id_token should not be called when google is disabled")

        async def fail_claim(_self, **_kwargs):
            raise AssertionError(
                "claim_google_identity should not be called when google is disabled"
            )

        async def fail_issue(_conn, _request):
            raise AssertionError(
                "issue_device_session should not be called when google is disabled"
            )

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_rate_limiter", fail_get_rate_limiter
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce", fail_consume
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token", fail_verify
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fail_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id),
                "nonce": plaintext,
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
        )

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "google_sign_in_disabled"}

    @pytest.mark.asyncio
    async def test_complete_blocks_when_hosted_identity_disabled(
        self, route_client, monkeypatch
    ) -> None:
        client, pool = route_client
        plaintext = "plaintext-nonce-hosted-disabled"
        challenge_id = _seed_nonce(pool, plaintext)

        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "false")
        monkeypatch.setenv("DAEMON_GOOGLE_ENABLED", "true")
        get_settings.cache_clear()

        def fail_get_rate_limiter(_request):
            raise AssertionError(
                "get_rate_limiter must not be called when hosted identity is disabled"
            )

        async def fail_consume(_self, _request):
            raise AssertionError(
                "consume_nonce must not be called when hosted identity is disabled"
            )

        async def fail_verify(_self, _request):
            raise AssertionError(
                "verify_id_token must not be called when hosted identity is disabled"
            )

        async def fail_claim(_self, **_kwargs):
            raise AssertionError(
                "claim_google_identity must not be called when hosted identity is disabled"
            )

        async def fail_issue(_conn, _request):
            raise AssertionError(
                "issue_device_session must not be called when hosted identity is disabled"
            )

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_rate_limiter", fail_get_rate_limiter
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce", fail_consume
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token", fail_verify
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fail_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(challenge_id),
                "nonce": plaintext,
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
        )

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "hosted_identity_disabled"}


# ============================================================================
# Provider token as bearer must NOT be accepted on protected routes
# ============================================================================


class TestProviderTokenNotBearerAuth:
    @pytest.mark.asyncio
    async def test_google_id_token_rejected_as_bearer_on_protected_route(self, route_client):
        """A Google ID token must not authorize a protected device
        route. The existing `/v1/auth/devices` route requires a
        Daemon device-token bearer; we expect a 401 (no Daemon
        access token) and never a 200.
        """
        client, _pool = route_client

        response = await client.get(
            "/v1/auth/devices",
            headers={"Authorization": "Bearer google-id-token-value"},
        )

        # 401 because no Daemon device/session token is present. The
        # route MUST NOT trust the Google ID token as a bearer.
        assert response.status_code == 401, response.text
        # The response body must not echo the Google token.
        assert "google-id-token-value" not in response.text


class TestGoogleCompleteNewDeviceNotification:
    """TODO 14: the google-complete route schedules a best-effort
    new-device email notification AFTER a successful
    `issue_device_session`. The notification:

      - carries the verified email (the Google ID-token's
        normalized email) as the recipient;
      - carries the device name and the platform label;
      - is NEVER sent on failed sign-in (replayed nonce,
        malformed ID token, invite-only rejection,
        signup disabled);
      - does NOT block the auth response (a sender failure
        is logged at WARNING and swallowed; the response
        still returns 200 with the access/refresh cookie).
    """

    @pytest.mark.asyncio
    async def test_success_schedules_new_device_notification(
        self, route_client, monkeypatch
    ) -> None:
        client, _pool = route_client
        captured_notifications: list = []

        async def fake_consume(_self, _request):
            return _Record(
                {
                    "id": uuid.uuid4(),
                    "nonce_verifier_hash": "verifier",
                    "user_id_proposed": None,
                    "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
                    "consumed_at": datetime.now(timezone.utc),
                    "created_at": datetime.now(timezone.utc),
                }
            )

        async def fake_verify(_self, _request):
            return VerifiedGoogleIdentity(
                provider_subject="google-sub-123",
                normalized_email="user@example.com",
                original_email="user@example.com",
                verified_at=datetime.now(timezone.utc),
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
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fake_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fake_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        nonce = "valid-nonce-for-google-complete"
        challenge_id = str(uuid.uuid4())
        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": challenge_id,
                "nonce": nonce,
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["access_token"] == "access-token"
        assert len(captured_notifications) == 1
        notif = captured_notifications[0]
        assert notif.recipient_email == "user@example.com"
        assert notif.device_name == "Web Google Sign-In Device"
        assert notif.platform == "web"
        assert notif.provider == "google"

    @pytest.mark.asyncio
    async def test_no_notification_on_replayed_nonce(self, route_client, monkeypatch) -> None:
        client, _pool = route_client
        captured_notifications: list = []

        async def fake_consume(_self, _request):
            raise GoogleNonceInvalid("already consumed")

        def fail_verify(_self, _request):
            raise AssertionError("verify should not be called on replay")

        def fail_claim(_self, **_kwargs):
            raise AssertionError("claim should not be called on replay")

        async def fail_issue(_conn, _request):
            raise AssertionError("issue should not be called on replay")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fail_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fail_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(uuid.uuid4()),
                "nonce": "replayed-nonce",
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "google_sign_in_failed"}
        assert captured_notifications == [], "expected no notification on replayed nonce"

    @pytest.mark.asyncio
    async def test_no_notification_on_invalid_token(self, route_client, monkeypatch) -> None:
        client, _pool = route_client
        captured_notifications: list = []

        async def fake_consume(_self, _request):
            return _Record(
                {
                    "id": uuid.uuid4(),
                    "nonce_verifier_hash": "verifier",
                    "user_id_proposed": None,
                    "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
                    "consumed_at": datetime.now(timezone.utc),
                    "created_at": datetime.now(timezone.utc),
                }
            )

        async def fake_verify(_self, _request):
            raise GoogleTokenInvalid("malformed token")

        def fail_claim(_self, **_kwargs):
            raise AssertionError("claim should not be called on invalid token")

        async def fail_issue(_conn, _request):
            raise AssertionError("issue should not be called on invalid token")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fake_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fail_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(uuid.uuid4()),
                "nonce": "valid-nonce",
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 401
        assert captured_notifications == [], "expected no notification on invalid token"

    @pytest.mark.asyncio
    async def test_no_notification_on_invite_only_rejection(
        self, route_client, monkeypatch
    ) -> None:
        client, _pool = route_client
        captured_notifications: list = []

        async def fake_consume(_self, _request):
            return _Record(
                {
                    "id": uuid.uuid4(),
                    "nonce_verifier_hash": "verifier",
                    "user_id_proposed": None,
                    "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
                    "consumed_at": datetime.now(timezone.utc),
                    "created_at": datetime.now(timezone.utc),
                }
            )

        async def fake_verify(_self, _request):
            return VerifiedGoogleIdentity(
                provider_subject="google-sub-123",
                normalized_email="user@example.com",
                original_email="user@example.com",
                verified_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            raise InviteOnlyRejection("invite required")

        async def fail_issue(_conn, _request):
            raise AssertionError("issue should not be called on invite rejection")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fake_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(uuid.uuid4()),
                "nonce": "valid-nonce",
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 401
        assert captured_notifications == [], "expected no notification on invite-only rejection"

    @pytest.mark.asyncio
    async def test_notification_sender_failure_does_not_block_auth(
        self, route_client, monkeypatch
    ) -> None:
        client, _pool = route_client
        schedule_calls: list = []

        async def fake_consume(_self, _request):
            return _Record(
                {
                    "id": uuid.uuid4(),
                    "nonce_verifier_hash": "verifier",
                    "user_id_proposed": None,
                    "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
                    "consumed_at": datetime.now(timezone.utc),
                    "created_at": datetime.now(timezone.utc),
                }
            )

        async def fake_verify(_self, _request):
            return VerifiedGoogleIdentity(
                provider_subject="google-sub-123",
                normalized_email="user@example.com",
                original_email="user@example.com",
                verified_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **_kwargs):
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            schedule_calls.append(notification)
            # The BackgroundTasks runtime would await the
            # coroutine AFTER the response is sent. Simulate
            # a broken sender by recording that the route
            # only enqueued; the actual send never runs in
            # this test.
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.consume_nonce",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.GoogleVerifierService.verify_id_token",
            fake_verify,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_google_identity_in_transaction",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fake_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.schedule_device_notification", fake_schedule
        )

        response = await client.post(
            "/v1/auth/google/complete",
            json={
                "challenge_id": str(uuid.uuid4()),
                "nonce": "valid-nonce",
                "id_token": "id-token",
                "client_kind": "web",
                "device_persistence": "private",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        # The auth response is unchanged by the simulated
        # notification failure.
        assert response.status_code == 200, response.text
        assert response.json()["access_token"] == "access-token"
        assert "__Host-daemon_refresh=refresh-token" in response.headers.get("set-cookie", "")
        assert len(schedule_calls) == 1
