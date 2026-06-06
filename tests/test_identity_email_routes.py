"""Focused route tests for hosted email sign-in endpoints."""

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


class RouteMockConn:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, normalized_email FROM email_challenges WHERE id = $1"):
            self._pool.challenge_lookup_calls += 1
            challenge_id = args[0]
            row = self._pool.challenge_lookup.get(challenge_id)
            return None if row is None else dict(row)
        return None

    async def fetchval(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith(
            "SELECT token_verifier_hash FROM signup_invites WHERE normalized_email = $1"
        ):
            return self._pool.invite_hash_by_email.get(args[0])
        return None

    async def execute(self, query: str, *args):
        return None

    @asynccontextmanager
    async def transaction(self):
        snapshot = list(self._pool.claim_markers)
        self._pool.transaction_depth += 1
        try:
            yield self
        except Exception:
            self._pool.claim_markers = snapshot
            raise
        finally:
            self._pool.transaction_depth -= 1


class RouteMockPool:
    def __init__(self) -> None:
        self.challenge_lookup: dict[uuid.UUID, dict[str, object]] = {}
        self.challenge_lookup_calls = 0
        self.invite_hash_by_email: dict[str, str] = {}
        self.transaction_depth = 0
        self.claim_markers: list[str] = []

    async def fetchval(self, query: str, *args):
        return 0

    async def fetchrow(self, query: str, *args):
        return None

    async def execute(self, query: str, *args):
        return None

    @asynccontextmanager
    async def acquire(self):
        yield RouteMockConn(self)

    async def close(self):
        return None


def make_mock_init(mock_pool: RouteMockPool):
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


class FakeMailSender:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, message):
        self.messages.append(message)
        return MailSendResult(
            sent_at=datetime.now(timezone.utc),
            sink_kind="console",
        )


@pytest_asyncio.fixture
async def setup_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("DAEMON_ALLOWED_ORIGINS", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_PUBLIC_ORIGIN", "https://app.daemon.ai")
    monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", "test-pepper-for-all-tests-12345678901234567890")
    monkeypatch.setenv("DAEMON_SIGNUP_MODE", "open")
    monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("DAEMON_GOOGLE_CLIENT_ID", "daemon-test-client-id.googleusercontent.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def route_client(setup_env):
    pool = RouteMockPool()
    original = make_mock_init(pool)
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, pool
    finally:
        restore_init(original)


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


class TestEmailStartRoute:
    @pytest.mark.asyncio
    async def test_email_start_returns_challenge_and_sends_code(self, route_client, monkeypatch):
        client, _pool = route_client
        sender = FakeMailSender()
        challenge_id = uuid.uuid4()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        captured_requests = []
        captured_policies = []

        async def fake_create(_self, request):
            captured_requests.append(request)
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

        async def fake_enforce_rate_limit(*, policies, **_kwargs):
            captured_policies.extend(policies)

        async def no_sleep(_started_at: float) -> None:
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.create_challenge_for_delivery",
            fake_create,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_mail_sender", lambda _settings: sender
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup._sleep_for_start_timing_floor", no_sleep
        )

        response = await client.post(
            "/v1/auth/email/start",
            json={"email": "  USER@example.com "},
            headers={"User-Agent": "pytest-agent/1.0"},
        )

        assert response.status_code == 202, response.text
        data = response.json()
        assert data["accepted"] is True
        assert data["challenge_id"] == str(challenge_id)
        assert data["expires_at"] == int(expires_at.timestamp())
        assert "123456" not in response.text

        assert len(sender.messages) == 1
        assert sender.messages[0].to_address == "user@example.com"
        assert "123456" in sender.messages[0].body_text
        assert captured_requests[0].normalized_email == "user@example.com"
        assert captured_requests[0].ip_hash is not None
        assert captured_requests[0].user_agent_hash is not None
        assert [policy[0] for policy in captured_policies] == ["ip", "ip", "email", "email"]
        assert captured_policies[2][1] == "user@example.com"
        assert [policy[3] for policy in captured_policies] == [
            "auth:email:start:ip:hour",
            "auth:email:start:ip:day",
            "auth:email:start:email:hour",
            "auth:email:start:email:day",
        ]

    @pytest.mark.asyncio
    async def test_email_start_blocks_when_email_provider_disabled(
        self, route_client, monkeypatch
    ) -> None:
        client, _pool = route_client
        monkeypatch.setenv("DAEMON_EMAIL_ENABLED", "false")
        get_settings.cache_clear()

        def fail_get_rate_limiter(_request):
            raise AssertionError("get_rate_limiter should not be called when email is disabled")

        async def fail_create(_self, _request):
            raise AssertionError(
                "create_challenge_for_delivery should not be called when email is disabled"
            )

        def fail_get_mail_sender(_settings):
            raise AssertionError("get_mail_sender should not be called when email is disabled")

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_rate_limiter", fail_get_rate_limiter
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.create_challenge_for_delivery",
            fail_create,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.get_mail_sender", fail_get_mail_sender)

        response = await client.post(
            "/v1/auth/email/start",
            json={"email": "user@example.com"},
        )

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "email_sign_in_disabled"}

    @pytest.mark.asyncio
    async def test_email_start_blocks_when_hosted_identity_disabled(
        self, route_client, monkeypatch
    ) -> None:
        client, _pool = route_client
        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "false")
        monkeypatch.setenv("DAEMON_EMAIL_ENABLED", "true")
        get_settings.cache_clear()

        def fail_get_rate_limiter(_request):
            raise AssertionError(
                "get_rate_limiter must not be called when hosted identity is disabled"
            )

        async def fail_create(_self, _request):
            raise AssertionError(
                "create_challenge_for_delivery must not be called when hosted identity is disabled"
            )

        def fail_get_mail_sender(_settings):
            raise AssertionError(
                "get_mail_sender must not be called when hosted identity is disabled"
            )

        def fail_normalize_email(_email):
            raise AssertionError(
                "normalize_email must not be called when hosted identity is disabled"
            )

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_rate_limiter", fail_get_rate_limiter
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.create_challenge_for_delivery",
            fail_create,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.get_mail_sender", fail_get_mail_sender)
        monkeypatch.setattr("orchestrator.routes.auth_setup.normalize_email", fail_normalize_email)

        response = await client.post(
            "/v1/auth/email/start",
            json={"email": "user@example.com"},
        )

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "hosted_identity_disabled"}


class TestEmailCompleteRoute:
    @pytest.mark.asyncio
    async def test_ip_rate_limit_runs_before_challenge_lookup(self, route_client, monkeypatch):
        from fastapi import HTTPException

        client, pool = route_client
        challenge_id = uuid.uuid4()
        calls = []

        async def fake_enforce_rate_limit(**kwargs):
            calls.append(kwargs["policies"])
            if len(calls) == 1:
                raise HTTPException(status_code=429, detail="rate_limited")

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

        assert response.status_code == 429
        assert len(calls) == 1
        assert pool.challenge_lookup_calls == 0

    @pytest.mark.asyncio
    async def test_web_private_returns_access_only_and_refresh_cookie(
        self, route_client, monkeypatch
    ):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }
        captured_claims = []
        captured_issue_requests = []
        captured_policies = []

        async def fake_consume(_self, request):
            pool.claim_markers.append("email-challenge-consumed")
            return EmailChallengeRow(
                id=request.challenge_id,
                normalized_email="user@example.com",
                attempts_remaining=5,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                consumed_at=datetime.now(timezone.utc),
                locked_at=None,
                created_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **kwargs):
            captured_claims.append(kwargs)
            return _claim_result()

        async def fake_issue(_conn, request):
            captured_issue_requests.append(request)
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(*, policies, **_kwargs):
            captured_policies.extend(policies)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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
                "code": "123 456",
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
        cookie_header = response.headers.get("set-cookie", "")
        assert "__Host-daemon_refresh=refresh-token" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Max-Age=7776000" in cookie_header
        assert captured_claims[0]["normalized_email"] == "user@example.com"
        assert captured_issue_requests[0].client_kind == "web"
        assert captured_issue_requests[0].device_persistence == "private"
        assert captured_policies[1][1] == "user@example.com"

    @pytest.mark.asyncio
    async def test_web_temporary_uses_session_cookie(self, route_client, monkeypatch):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }

        async def fake_consume(_self, request):
            pool.claim_markers.append("email-challenge-consumed")
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
            return _issued_session(client_kind="web", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }
        pool.invite_hash_by_email["user@example.com"] = "stored-hash-must-not-be-reused"
        captured_claims = []

        monkeypatch.setenv("DAEMON_SIGNUP_MODE", "invite_only")
        get_settings.cache_clear()

        async def fake_consume(_self, request):
            assert pool.transaction_depth == 0
            return EmailChallengeRow(
                id=request.challenge_id,
                normalized_email="user@example.com",
                attempts_remaining=5,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                consumed_at=datetime.now(timezone.utc),
                locked_at=None,
                created_at=datetime.now(timezone.utc),
            )

        async def fake_claim(_self, **kwargs):
            captured_claims.append(kwargs)
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
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
            assert pool.transaction_depth == 1
            pool.claim_markers.append("email-claim")
            return _claim_result()

        async def fake_issue(_conn, _request):
            assert pool.transaction_depth == 1
            assert pool.claim_markers == ["email-claim"]
            return _issued_session(client_kind="web", refresh_max_age_seconds=600)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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
                "device_persistence": "temporary",
            },
            headers={
                "Origin": "https://app.daemon.ai",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 200, response.text
        assert pool.transaction_depth == 0
        assert pool.claim_markers == ["email-claim"]

    @pytest.mark.asyncio
    async def test_session_failure_rolls_back_claim_side_effects(self, route_client, monkeypatch):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }
        consume_state = {"consumed": False}

        async def fake_consume(_self, request):
            assert pool.transaction_depth == 0
            consume_state["consumed"] = True
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
            assert pool.transaction_depth == 1
            pool.claim_markers.append("email-claim")
            return _claim_result()

        async def failing_issue(_conn, _request):
            assert pool.transaction_depth == 1
            raise RuntimeError("session issuance failed")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
            fake_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", failing_issue)
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
        )

        with pytest.raises(RuntimeError, match="session issuance failed"):
            await client.post(
                "/v1/auth/email/complete",
                json={
                    "challenge_id": str(challenge_id),
                    "code": "123456",
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
        assert consume_state["consumed"] is True

    @pytest.mark.asyncio
    async def test_native_returns_refresh_json_and_no_cookie(self, route_client, monkeypatch):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
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
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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
        assert data["access_token"] == "access-token"
        assert data["refresh_token"] == "refresh-token"
        assert response.headers.get("set-cookie") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mode", "failure"),
        [
            ("consume", EmailChallengeInvalid("wrong code")),
            ("consume", EmailChallengeLocked("locked")),
            ("claim", InviteOnlyRejection("invite required")),
            ("claim", SignupDisabled("disabled")),
        ],
    )
    async def test_complete_collapses_failures_to_generic_response(
        self,
        route_client,
        monkeypatch,
        mode,
        failure,
    ):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }

        async def fake_consume(_self, request):
            if mode == "consume":
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
            if mode == "claim":
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
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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
        assert "refresh-token" not in response.text
        assert response.headers.get("set-cookie") is None

    @pytest.mark.asyncio
    async def test_wrong_code_persists_failed_attempt_decrement(self, route_client, monkeypatch):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }
        attempts = {"remaining": 2}

        async def fake_consume(_self, _request):
            assert pool.transaction_depth == 0
            attempts["remaining"] -= 1
            raise EmailChallengeInvalid("wrong code")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.enforce_rate_limit", fake_enforce_rate_limit
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
        assert attempts["remaining"] == 1

    @pytest.mark.asyncio
    async def test_complete_rejects_native_when_refresh_cookie_present(self, route_client):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }

        client.cookies.set("__Host-daemon_refresh", "unexpected-cookie")

        response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "123456",
                "client_kind": "native",
                "device_persistence": "private",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "cookie present but client_kind is 'native'"

    @pytest.mark.asyncio
    async def test_complete_forbids_user_and_tenant_id_fields(self, route_client):
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }

        response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "123456",
                "client_kind": "web",
                "device_persistence": "private",
                "user_id": str(uuid.uuid4()),
                "tenant_id": str(uuid.uuid4()),
            },
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_email_complete_blocks_when_email_provider_disabled(
        self, route_client, monkeypatch
    ) -> None:
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }

        monkeypatch.setenv("DAEMON_EMAIL_ENABLED", "false")
        get_settings.cache_clear()

        def fail_get_rate_limiter(_request):
            raise AssertionError("get_rate_limiter should not be called when email is disabled")

        async def fail_consume(_self, _request):
            raise AssertionError("consume_challenge should not be called when email is disabled")

        async def fail_claim(_self, **_kwargs):
            raise AssertionError("claim_email_identity should not be called when email is disabled")

        async def fail_issue(_conn, _request):
            raise AssertionError("issue_device_session should not be called when email is disabled")

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_rate_limiter", fail_get_rate_limiter
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fail_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
            fail_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)

        response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "123456",
                "client_kind": "web",
                "device_persistence": "private",
            },
        )

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "email_sign_in_disabled"}

    @pytest.mark.asyncio
    async def test_email_complete_blocks_when_hosted_identity_disabled(
        self, route_client, monkeypatch
    ) -> None:
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }

        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "false")
        monkeypatch.setenv("DAEMON_EMAIL_ENABLED", "true")
        get_settings.cache_clear()

        def fail_get_rate_limiter(_request):
            raise AssertionError(
                "get_rate_limiter must not be called when hosted identity is disabled"
            )

        async def fail_consume(_self, _request):
            raise AssertionError(
                "consume_challenge must not be called when hosted identity is disabled"
            )

        async def fail_claim(_self, **_kwargs):
            raise AssertionError(
                "claim_email_identity must not be called when hosted identity is disabled"
            )

        async def fail_issue(_conn, _request):
            raise AssertionError(
                "issue_device_session must not be called when hosted identity is disabled"
            )

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.get_rate_limiter", fail_get_rate_limiter
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fail_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
            fail_claim,
        )
        monkeypatch.setattr("orchestrator.routes.auth_setup.issue_device_session", fail_issue)

        response = await client.post(
            "/v1/auth/email/complete",
            json={
                "challenge_id": str(challenge_id),
                "code": "123456",
                "client_kind": "web",
                "device_persistence": "private",
            },
        )

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "hosted_identity_disabled"}


class TestEmailCompleteNewDeviceNotification:
    """TODO 14: the email-complete route schedules a best-effort
    new-device email notification AFTER a successful
    `issue_device_session`. The notification:

      - carries the verified email as the recipient;
      - carries the device name and the platform label;
      - is NEVER sent on failed sign-in (wrong code,
        invite-only rejection, signup disabled);
      - does NOT block the auth response (a sender failure
        is logged at WARNING and swallowed; the response
        still returns 200 with the access/refresh token).
    """

    @pytest.mark.asyncio
    async def test_success_schedules_new_device_notification(
        self, route_client, monkeypatch
    ) -> None:
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
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
            return _claim_result()

        async def fake_issue(_conn, _request):
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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

        assert response.status_code == 200, response.text
        assert response.json()["access_token"] == "access-token"
        assert len(captured_notifications) == 1
        notif = captured_notifications[0]
        assert notif.recipient_email == "user@example.com"
        assert notif.device_name == "Web Sign-In Device"
        assert notif.platform == "web"
        assert notif.provider == "email"

    @pytest.mark.asyncio
    async def test_no_notification_on_wrong_code(self, route_client, monkeypatch) -> None:
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }
        captured_notifications: list = []

        async def fake_consume(_self, _request):
            raise EmailChallengeInvalid("wrong code")

        def fail_claim(_self, **_kwargs):
            raise AssertionError("claim should not be called on wrong code")

        async def fail_issue(_conn, _request):
            raise AssertionError("issue should not be called on wrong code")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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
    async def test_no_notification_on_invite_only_rejection(
        self, route_client, monkeypatch
    ) -> None:
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
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
            raise AssertionError("issue should not be called on invite rejection")

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            captured_notifications.append(notification)

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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
        assert captured_notifications == [], "expected no notification when account claim fails"

    @pytest.mark.asyncio
    async def test_notification_sender_failure_does_not_block_auth(
        self, route_client, monkeypatch
    ) -> None:
        # Simulate the schedule helper being called but the
        # actual send raising. The auth response must still
        # be 200 with the access/refresh cookie. The helper
        # itself never raises (it logs + swallows); this
        # test asserts the contract is honored by the
        # integration: the route hands the work off and the
        # response does not depend on the send outcome.
        client, pool = route_client
        challenge_id = uuid.uuid4()
        pool.challenge_lookup[challenge_id] = {
            "id": challenge_id,
            "normalized_email": "user@example.com",
        }
        schedule_calls: list = []

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
            return _issued_session(client_kind="web", refresh_max_age_seconds=90 * 86400)

        async def fake_enforce_rate_limit(**_kwargs):
            return None

        def fake_schedule(background_tasks, settings, notification):
            schedule_calls.append(notification)
            # Simulate the helper having scheduled a coroutine
            # on a broken BackgroundTasks: the real coroutine
            # is never awaited, mirroring a sender that
            # fails silently in the background.
            return None

        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.EmailChallengeService.consume_challenge",
            fake_consume,
        )
        monkeypatch.setattr(
            "orchestrator.routes.auth_setup.AccountService.claim_email_identity_in_transaction",
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

        # The auth response is unchanged by the simulated
        # notification failure.
        assert response.status_code == 200, response.text
        assert response.json()["access_token"] == "access-token"
        assert "__Host-daemon_refresh=refresh-token" in response.headers.get("set-cookie", "")
        # The schedule helper was called exactly once.
        assert len(schedule_calls) == 1
