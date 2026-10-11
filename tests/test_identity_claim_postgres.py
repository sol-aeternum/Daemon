"""Real completion routes/transactions on disposable PostgreSQL, no mail/provider calls."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orchestrator.config import Settings
from orchestrator.db import AppState
from orchestrator.routes import auth_setup
from orchestrator.services.identity.account_service import AccountService
from orchestrator.services.identity.email_challenge import (
    EmailChallengeIssueRequest,
    EmailChallengeService,
)
from orchestrator.services.identity.google_verifier import (
    GoogleNonceIssueRequest,
    GoogleVerifierService,
)
from tests.durable_tasks_support import Env, durable_env_fixture
from orchestrator.services.identity.retention import sweep_identity_records

env = durable_env_fixture()


def _unused_google_verifier(
    id_token: str,
    request: Any,
    audience: str | list[str],
    *,
    certs_url: str | None = None,
) -> dict[str, Any]:
    return {}


async def _prepare(env: Env, monkeypatch, provider: str, *, signup_mode: str = "open"):
    # Pydantic's typed constructor omits BaseSettings' runtime-only _env_file.
    settings: Settings = cast(Any, Settings)(
        _env_file=None,
        daemon_hosted_identity_enabled=True,
        daemon_google_enabled=True,
        daemon_signup_mode=signup_mode,
        daemon_environment="development",
        daemon_cookie_secure=False,
        daemon_public_origin="https://fictional.daemon.test",
        daemon_allowed_origins="https://fictional.daemon.test",
        daemon_auth_pepper="p" * 64,  # Synthetic low-entropy fixture, not a credential.
        daemon_google_client_id="fictional-client.googleusercontent.com",
    )
    monkeypatch.setattr(auth_setup, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_setup, "get_rate_limiter", lambda _request: None)

    async def allow_rate_limit(**_kwargs):
        pass

    # Only external Redis admission/mail/provider signature boundaries are fake.
    # Consumption, identity, attribution, device/session and audit writes are real.
    monkeypatch.setattr(auth_setup, "enforce_rate_limit", allow_rate_limit)
    monkeypatch.setattr(auth_setup, "schedule_device_notification", lambda **_kwargs: None)
    address = "fictional-claim@example.test"
    async with env.pool.acquire() as conn:
        if provider == "email":
            proof, code = await EmailChallengeService(
                cast(Any, conn), settings
            ).create_challenge_for_delivery(
                EmailChallengeIssueRequest(
                    normalized_email=address,
                    ip_hash=None,
                    user_agent_hash=None,
                    ttl_seconds=settings.daemon_email_challenge_ttl_seconds,
                    max_attempts=settings.daemon_email_challenge_max_attempts,
                )
            )
            payload = {"challenge_id": str(proof.id), "code": code}
            table = "email_challenges"
        else:
            proof, nonce = await GoogleVerifierService(
                cast(Any, conn), settings, _unused_google_verifier
            ).issue_nonce(
                GoogleNonceIssueRequest(
                    ip_hash=None,
                    user_agent_hash=None,
                    ttl_seconds=settings.daemon_google_nonce_ttl_seconds,
                )
            )
            claims = {
                "iss": "https://accounts.google.com",
                "aud": settings.daemon_google_client_id,
                "sub": "fictional-retention-subject",
                "email": address,
                "email_verified": True,
                "nonce": nonce,
            }
            monkeypatch.setattr(
                auth_setup, "default_google_id_token_verifier", lambda: lambda *_args: claims
            )
            payload = {
                "challenge_id": str(proof.id),
                "nonce": nonce,
                "id_token": "fictional-id-token",
            }
            table = "google_nonce_challenges"
    app = FastAPI()
    app.state.app_state = AppState(settings=settings, db_pool=env.pool)
    app.include_router(auth_setup.router)
    return app, settings, address, proof.id, table, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["email", "google"])
@pytest.mark.parametrize("client_kind", ["native", "web"])
@pytest.mark.parametrize("existing", [False, True])
async def test_completion_attributes_actual_claim_and_session_atomically(
    env: Env, monkeypatch, provider: str, client_kind: str, existing: bool
) -> None:
    app, _settings, address, proof_id, table, payload = await _prepare(env, monkeypatch, provider)
    # Proof was issued before account creation, so no later email-only backfill.
    if existing:
        async with env.pool.acquire() as conn:
            account = await AccountService(cast(Any, conn)).claim_email_identity(
                normalized_email=address,
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="open",
                invite_token_verifier_hash=None,
            )
        expected_user = account.user.id
    else:
        expected_user = None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://fictional.daemon.test"
    ) as client:
        response = await client.post(
            f"/v1/auth/{provider}/complete",
            headers={"Origin": "https://fictional.daemon.test"},
            json={**payload, "client_kind": client_kind, "device_persistence": "private"},
        )
    assert response.status_code == 200, response.text
    user = await env.pool.fetchrow("SELECT id FROM users WHERE normalized_email=$1", address)
    assert user is not None
    if expected_user is not None:
        assert user["id"] == expected_user
    row = await env.pool.fetchrow(f"SELECT * FROM {table} WHERE id=$1", proof_id)
    assert row is not None and row["consumed_at"] is not None
    assert row["claimed_user_id"] == user["id"]
    if provider == "email":
        assert row["email_ownership_id"] is None
    assert (
        await env.pool.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", user["id"]) == 1
    )
    assert await env.pool.fetchval("SELECT count(*) FROM devices WHERE user_id=$1", user["id"]) == 1
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM account_email_ownership WHERE user_id=$1", user["id"]
        )
        == 1
    )
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM identity_audit_log WHERE user_id=$1", user["id"]
        )
        == 1
    )
    if client_kind == "web":
        assert "refresh_token" not in response.json()
        assert response.headers["clear-site-data"] == '"cache"'
        assert "set-cookie" in response.headers
    else:
        assert "refresh_token" in response.json()
        assert "set-cookie" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["email", "google"])
@pytest.mark.parametrize(
    "failure", ["signup_disabled", "after_session_insert", "proof_removed", "after_attribution"]
)
async def test_completion_failure_keeps_proof_burn_without_partial_account_or_attribution(
    env: Env, monkeypatch, provider: str, failure: str
) -> None:
    app, _settings, address, proof_id, table, payload = await _prepare(
        env,
        monkeypatch,
        provider,
        signup_mode="disabled" if failure == "signup_disabled" else "open",
    )
    ownership_before = await env.pool.fetchval("SELECT count(*) FROM account_email_ownership")
    if failure == "after_session_insert":
        issue = auth_setup.issue_device_session

        async def fail_after_real_session(conn, request):
            await issue(conn, request)
            raise RuntimeError("fictional session failure")

        monkeypatch.setattr(auth_setup, "issue_device_session", fail_after_real_session)
    elif failure == "after_attribution":
        method = "attribute_email_challenge" if provider == "email" else "attribute_google_nonce"
        attribute = getattr(auth_setup, method)
        error = (
            auth_setup.EmailChallengeInvalid
            if provider == "email"
            else auth_setup.GoogleNonceInvalid
        )

        async def reject_after_actual_attribution(conn, proof, user_id):
            await attribute(conn, proof, user_id)
            raise error("fictional attribution failure")

        monkeypatch.setattr(auth_setup, method, reject_after_actual_attribution)
    elif failure == "proof_removed":
        service = EmailChallengeService if provider == "email" else GoogleVerifierService
        method = "consume_challenge" if provider == "email" else "consume_nonce"
        consume = getattr(service, method)

        async def remove_after_burn(self, request):
            result = await consume(self, request)
            # Real sweep on another connection wins between burn and claim.
            from datetime import timedelta

            assert result.consumed_at is not None
            async with env.pool.acquire() as sweeper:
                counts = await sweep_identity_records(
                    sweeper, now=result.consumed_at + timedelta(days=7)
                )
            assert counts[table] == 1
            return result

        monkeypatch.setattr(service, method, remove_after_burn)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://fictional.daemon.test",
    ) as client:
        response = await client.post(
            f"/v1/auth/{provider}/complete",
            json={**payload, "client_kind": "native", "device_persistence": "private"},
        )
        assert response.status_code == (500 if failure == "after_session_insert" else 401)
        replay = await client.post(
            f"/v1/auth/{provider}/complete",
            json={**payload, "client_kind": "native", "device_persistence": "private"},
        )
        assert replay.status_code == 401
    assert (
        await env.pool.fetchval("SELECT id FROM users WHERE normalized_email=$1", address) is None
    )
    assert await env.pool.fetchval("SELECT count(*) FROM sessions") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM devices") == 0
    assert (
        await env.pool.fetchval("SELECT count(*) FROM account_email_ownership") == ownership_before
    )
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM account_email_ownership WHERE normalized_email=$1", address
        )
        == 0
    )
    row = await env.pool.fetchrow(f"SELECT * FROM {table} WHERE id=$1", proof_id)
    if failure == "proof_removed":
        assert row is None
    else:
        assert row is not None and row["consumed_at"] is not None
        assert row["claimed_user_id"] is None


@pytest.mark.asyncio
async def test_invalid_google_token_still_burns_nonce_without_claim(env: Env, monkeypatch) -> None:
    app, _settings, address, proof_id, _table, payload = await _prepare(env, monkeypatch, "google")
    monkeypatch.setattr(auth_setup, "default_google_id_token_verifier", lambda: lambda *_args: {})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://fictional.daemon.test"
    ) as client:
        response = await client.post(
            "/v1/auth/google/complete",
            json={**payload, "client_kind": "native", "device_persistence": "private"},
        )
    assert response.status_code == 401
    row = await env.pool.fetchrow("SELECT * FROM google_nonce_challenges WHERE id=$1", proof_id)
    assert row is not None and row["consumed_at"] is not None and row["claimed_user_id"] is None
    assert (
        await env.pool.fetchval("SELECT id FROM users WHERE normalized_email=$1", address) is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reassign", [False, True])
async def test_email_route_preserves_issuance_but_attributes_actual_account(
    env: Env, monkeypatch, reassign: bool
) -> None:
    address = "fictional-claim@example.test"
    async with env.pool.acquire() as conn:
        await conn.execute("UPDATE users SET normalized_email=$2 WHERE id=$1", env.alice, address)
        issuance = await conn.fetchval(
            "SELECT id FROM account_email_ownership WHERE user_id=$1 AND ended_at IS NULL",
            env.alice,
        )
    app, _settings, _address, proof_id, _table, payload = await _prepare(env, monkeypatch, "email")
    if reassign:
        await env.pool.execute(
            "UPDATE users SET normalized_email='released@example.test' WHERE id=$1", env.alice
        )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://fictional.daemon.test"
    ) as client:
        response = await client.post(
            "/v1/auth/email/complete",
            json={**payload, "client_kind": "native", "device_persistence": "private"},
        )
    assert response.status_code == 200, response.text
    actual = await env.pool.fetchval("SELECT id FROM users WHERE normalized_email=$1", address)
    assert (actual != env.alice) if reassign else (actual == env.alice)
    row = await env.pool.fetchrow("SELECT * FROM email_challenges WHERE id=$1", proof_id)
    assert row["email_ownership_id"] == issuance and row["claimed_user_id"] == actual
    assert await env.pool.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", actual) == 1
