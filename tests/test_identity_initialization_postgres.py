"""Birth-only production identity/SQL/auth/HTTP contracts on owned PostgreSQL.

No fresh-user entitlement preseed and no initialization-seam mock. Only external
proof verification, admission transport and streaming providers are scripted.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from orchestrator import compute_runtime, main
from orchestrator.auth import require_device_auth
from orchestrator.entitlements.errors import (
    UnknownAccount,
    ReservationReceipt,
    ReservationRecoveryUnresolved,
)
from orchestrator.entitlements.models import SubscriptionEvent
from orchestrator.entitlements.plans import Plan
from orchestrator.entitlements.policy import load_policy
from orchestrator.entitlements.service import EntitlementService
from orchestrator.routes import auth_setup
from orchestrator.services.deletion_journal.barrier import require_recovery_barrier
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.store import Journal, initialize
from orchestrator.services.identity import initialization
from orchestrator.services.identity.account_service import AccountService
from tests.deletion_journal_support import request
from tests.durable_tasks_support import Env, durable_env_fixture, seed_entitlement
from tests.qualified_compute import install_qualified_compute
from tests.test_identity_claim_postgres import _prepare

env = durable_env_fixture()


async def complete(app, provider, payload):
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://fictional.daemon.test",
    ) as client:
        return await client.post(
            f"/v1/auth/{provider}/complete",
            json={**payload, "client_kind": "native", "device_persistence": "private"},
        )


async def account_row(env, uid):
    row = await env.pool.fetchrow("SELECT * FROM entitlement_accounts WHERE user_id=$1", uid)
    return dict(row) if row is not None else None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["email", "google"])
@pytest.mark.parametrize("durable", [False, True])
async def test_new_identity_real_auth_first_chat_has_original_capture(
    env: Env, monkeypatch, provider, durable
):
    app, settings, address, _, _, payload = await _prepare(env, monkeypatch, provider)
    assert (
        await env.pool.fetchval("SELECT id FROM users WHERE normalized_email=$1", address) is None
    )
    assert await env.pool.fetchval("SELECT count(*) FROM entitlement_accounts") == 0
    signed_in = await complete(app, provider, payload)
    assert signed_in.status_code == 200, signed_in.text
    uid = await env.pool.fetchval("SELECT id FROM users WHERE normalized_email=$1", address)
    before = await account_row(env, uid)
    assert before is not None
    policy = load_policy()
    assert before["plan"] == "free" and before["plan_source"] == "default"
    assert before["status"] == "active" and before["content_generation"] == 0
    assert before["trial_budget_microusd"] == policy.trial.budget_for_new_account()
    assert before["trial_extended_agents"] == policy.trial.extended_agents_for_new_account()
    assert before["trial_consumed_microusd"] == before["trial_reserved_microusd"] == 0
    settings.durable_chat_enabled = durable
    app.state.app_state.memory_store = env.memory
    app.dependency_overrides[main.get_settings] = lambda: settings
    app.add_api_route("/chat", main.chat, methods=["POST"])
    assert require_device_auth not in app.dependency_overrides
    real_service = compute_runtime.EntitlementService
    install_qualified_compute(monkeypatch)
    # Route qualification is synthetic, but account resolve/admission stays real.
    monkeypatch.setattr(compute_runtime, "EntitlementService", real_service)

    async def unthrottled(**_kwargs):
        pass

    monkeypatch.setattr(main, "_enforce_chat_rate_limit", unthrottled)
    captured = []
    capture = main.capture_generation

    async def record_capture(conn, owner):
        result = await capture(conn, owner)
        captured.append(result)
        return result

    monkeypatch.setattr(main, "capture_generation", record_capture)

    async def scripted_stream(*_args, **kwargs):
        assert kwargs["summary_generation"] == captured[0]
        yield 'data: {"type":"done"}\n\n'

    async def scripted_observer(*_args, **_kwargs):
        yield 'data: {"type":"done"}\n\n'

    monkeypatch.setattr(main, "stream_sse_chat", scripted_stream)
    monkeypatch.setattr(main, "observe_task", scripted_observer)
    headers = {"Authorization": "Bearer " + signed_in.json()["access_token"]}
    if durable:
        headers["X-Daemon-Client-Features"] = "task-cancel, task-reset"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://fictional.daemon.test"
    ) as client:
        reply = await client.post(
            "/chat", headers=headers, json={"message": "fictional first chat"}
        )
        assert reply.status_code == 200, reply.text
        unauthenticated = await client.post("/chat", json={"message": "no credential"})
        assert unauthenticated.status_code == 401
    assert len(captured) == 1 and captured[0].account_id == uid and captured[0].generation == 0
    if durable:
        row = await env.pool.fetchrow("SELECT input_ciphertext FROM tasks WHERE user_id=$1", uid)
        task_input = json.loads(env.memory.encryption.decrypt(row["input_ciphertext"]))
        assert task_input["summary_origin_account_id"] == str(uid)
        assert task_input["summary_origin_account_generation"] == 0
    assert await account_row(env, uid) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["email", "google"])
async def test_removed_existing_entitlement_is_not_recreated_by_signin_or_consumers(
    env, monkeypatch, provider
):
    app, settings, address, _, _, payload = await _prepare(env, monkeypatch, provider)
    async with env.pool.acquire() as conn:
        claimed = await AccountService(cast(Any, conn), settings).claim_email_identity(
            normalized_email=address,
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
    uid = claimed.user.id
    await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", uid)
    reply = await complete(app, provider, payload)
    assert reply.status_code == 503
    assert await env.pool.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", uid) == 0
    assert await account_row(env, uid) is None
    service = EntitlementService(env.pool)
    for action in (
        lambda: service.resolve(uid),
        lambda: service.reserve(uid, 0, operation="chat"),
        lambda: service.apply_subscription_event(
            SubscriptionEvent("fictional", uid, Plan.PRO, "admin")
        ),
    ):
        with pytest.raises(UnknownAccount):
            await action()
    assert await account_row(env, uid) is None
    assert await env.pool.fetchval("SELECT count(*) FROM entitlement_subscription_events") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM entitlement_reservations") == 0


@pytest.mark.asyncio
async def test_receipt_recovery_never_recreates_missing_account(env):
    await seed_entitlement(env.pool, env.alice)
    service = EntitlementService(env.pool)
    reservation = await service.reserve(env.alice, 0, operation="chat")
    receipt = ReservationReceipt(reservation, env.alice, None, None, None, None, False, False)
    await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", env.alice)
    with pytest.raises(ReservationRecoveryUnresolved):
        await service.recover_reservation(receipt)
    assert await account_row(env, env.alice) is None
    assert await env.pool.fetchval("SELECT count(*) FROM entitlement_reservations") == 1


@pytest.mark.asyncio
async def test_concurrent_proven_births_converge_without_reset_and_isolate_identity(
    env, monkeypatch
):
    _, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")

    async def claim(address):
        async with env.pool.acquire() as conn:
            return await AccountService(cast(Any, conn), settings).claim_email_identity(
                normalized_email=address,
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="open",
                invite_token_verifier_hash=None,
            )

    first, second = await asyncio.gather(claim("race@example.test"), claim("race@example.test"))
    assert first.user.id == second.user.id
    assert sum([first.is_new_user, second.is_new_user]) == 1
    assert await env.pool.fetchval("SELECT count(*) FROM entitlement_accounts") == 1
    uid = first.user.id
    await env.pool.execute(
        "UPDATE entitlement_accounts SET status='suspended',content_generation=9,plan='pro',"
        "trial_consumed_microusd=7 WHERE user_id=$1",
        uid,
    )
    await env.pool.execute(
        "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", uid, uuid4()
    )
    before = await account_row(env, uid)
    repeated = await claim("race@example.test")
    assert not repeated.is_new_user and await account_row(env, uid) == before
    other = await claim("other@example.test")
    other_state = await account_row(env, other.user.id)
    assert other_state is not None
    assert other.user.id != uid and other_state["content_generation"] == 0
    assert await account_row(env, uid) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["after_insert", "cancel", "failed_commit"])
async def test_proven_birth_failure_rolls_back_defaults_identity_and_reuses_pool(
    env, monkeypatch, failure
):
    app, settings, address, _, _, payload = await _prepare(env, monkeypatch, "email")
    if failure == "failed_commit":
        # Deferred constraint trigger fails actual PostgreSQL COMMIT, not a fake
        # transaction exit. No identity/default/session may be acknowledged.
        await env.pool.execute(
            "CREATE FUNCTION refuse_birth_commit() RETURNS trigger LANGUAGE plpgsql AS $$"
            "BEGIN RAISE EXCEPTION 'fictional deferred commit refusal'; END $$;"
            "CREATE CONSTRAINT TRIGGER refuse_birth AFTER INSERT ON entitlement_accounts "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION refuse_birth_commit()"
        )
        result = await complete(app, "email", payload)
        assert result.status_code == 500
    else:
        issue = auth_setup.issue_device_session

        async def fail(conn, request):
            await issue(conn, request)
            if failure == "cancel":
                raise asyncio.CancelledError
            raise HTTPException(503, "service_unavailable")

        monkeypatch.setattr(auth_setup, "issue_device_session", fail)
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await complete(app, "email", payload)
        else:
            assert (await complete(app, "email", payload)).status_code == 503
    assert (
        await env.pool.fetchval("SELECT id FROM users WHERE normalized_email=$1", address) is None
    )
    for table in ("entitlement_accounts", "devices", "sessions"):
        assert await env.pool.fetchval(f"SELECT count(*) FROM {table}") == 0
    assert (
        await env.pool.fetchval(
            "SELECT count(*) FROM email_challenges WHERE consumed_at IS NOT NULL"
        )
        == 1
    )
    async with env.pool.acquire() as conn:
        assert not conn.is_in_transaction() and await conn.fetchval("SELECT 1") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history", ["unapplied", "applied_retired", "unavailable", "misconfigured"]
)
async def test_journal_readiness_and_retired_uuid_refuse_without_writes(
    env, monkeypatch, tmp_path, history
):
    _, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")
    uid, jid = uuid4(), uuid4()
    path = tmp_path / "journal"
    initialize(path, jid)
    settings.daemon_deletion_journal_dir = str(path)
    settings.daemon_deletion_journal_id = str(jid)
    await require_recovery_barrier(env.pool, settings)
    if history in {"unapplied", "applied_retired"}:
        with Journal(path, jid) as journal:
            entry = journal.append(request(uid), uuid4(), None, None)
        if history == "applied_retired":
            # Exact synthetic marker solely to discriminate retained UUID history;
            # it is not an application applier or lifecycle qualification claim.
            await env.pool.execute(
                "INSERT INTO deletion_journal_applied "
                "(journal_id,seq,event_id,account_id,operation_id,entry_digest) VALUES($1,$2,$3,$4,$5,$6)",
                jid,
                entry.seq,
                entry.event_id,
                uid,
                entry.command.operation_id,
                entry.digest,
            )
    elif history == "unavailable":
        settings.daemon_deletion_journal_dir = str(tmp_path / "absent")
    else:
        settings.daemon_deletion_journal_id = ""
    monkeypatch.setattr(initialization, "uuid4", lambda: uid)
    async with env.pool.acquire() as conn:
        with pytest.raises((JournalError, initialization.IdentityInitializationRefused)):
            await AccountService(cast(Any, conn), settings).claim_email_identity(
                normalized_email="journal@example.test",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="open",
                invite_token_verifier_hash=None,
            )
        assert not conn.is_in_transaction()
    assert await env.pool.fetchval("SELECT id FROM users WHERE id=$1", uid) is None
    assert await account_row(env, uid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["email", "google"])
async def test_ready_empty_journal_allows_proven_birth_without_marker_or_binding_writes(
    env, monkeypatch, tmp_path, provider
):
    app, settings, address, _, _, payload = await _prepare(env, monkeypatch, provider)
    path, jid = tmp_path / "journal", uuid4()
    initialize(path, jid)
    settings.daemon_deletion_journal_dir = str(path)
    settings.daemon_deletion_journal_id = str(jid)
    await require_recovery_barrier(env.pool, settings)
    before = [dict(row) for row in await env.pool.fetch("SELECT * FROM deletion_journal_binding")]
    reply = await complete(app, provider, payload)
    assert reply.status_code == 200, reply.text
    uid = await env.pool.fetchval("SELECT id FROM users WHERE normalized_email=$1", address)
    assert await account_row(env, uid) is not None
    assert [
        dict(row) for row in await env.pool.fetch("SELECT * FROM deletion_journal_binding")
    ] == before
    assert await env.pool.fetchval("SELECT count(*) FROM deletion_journal_applied") == 0
    with Journal(path, jid) as journal:
        assert journal.verify().entries == []


@pytest.mark.asyncio
async def test_migration_seeded_singleton_is_not_birth_authority(env, monkeypatch):
    _, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")
    async with env.pool.acquire() as conn, conn.transaction():
        with pytest.raises(
            initialization.IdentityInitializationRefused, match="missing_entitlement"
        ):
            await auth_setup._find_or_create_singleton_user(conn, settings)
    assert await account_row(env, initialization.SINGLETON_ID) is None


@pytest.mark.asyncio
async def test_real_setup_http_refuses_seeded_missing_entitlement_without_session(
    env, monkeypatch, tmp_path
):
    from orchestrator.auth_runtime_state import SETUP_TOKEN_HASH_KEY, set_runtime_value
    from orchestrator.auth_tokens import hash_token

    app, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")
    settings.daemon_deployment_mode = "self_hosted"
    settings.daemon_setup_token_file = str(tmp_path / "synthetic-setup-token")
    token = "synthetic-setup-proof-not-a-production-credential"
    async with env.pool.acquire() as conn:
        await set_runtime_value(conn, SETUP_TOKEN_HASH_KEY, hash_token(token))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://fictional.daemon.test"
    ) as client:
        reply = await client.post(
            "/v1/auth/setup",
            headers={"Origin": "https://fictional.daemon.test", "Sec-Fetch-Site": "same-origin"},
            json={"setup_token": token},
        )
    assert reply.status_code == 503 and reply.json() == {"detail": "service_unavailable"}
    assert "set-cookie" not in reply.headers
    assert await env.pool.fetchval("SELECT count(*) FROM devices") == 0
    assert await env.pool.fetchval("SELECT count(*) FROM sessions") == 0
    assert await account_row(env, initialization.SINGLETON_ID) is None
    assert await env.pool.fetchval(
        "SELECT value FROM system_state WHERE key=$1", SETUP_TOKEN_HASH_KEY
    ) == hash_token(token)


@pytest.mark.asyncio
async def test_missing_fixed_singleton_cannot_be_resurrected_without_history(env, monkeypatch):
    _, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")
    uid = initialization.SINGLETON_ID
    # Remove only this disposable fixture's migration seed and tenant. The
    # absence of retained history must NOT make this reserved identity mintable.
    await env.pool.execute("DELETE FROM tenants WHERE owner_user_id=$1", uid)
    await env.pool.execute("DELETE FROM users WHERE id=$1", uid)
    async with env.pool.acquire() as conn, conn.transaction():
        with pytest.raises(initialization.IdentityInitializationRefused, match="missing_identity"):
            await auth_setup._find_or_create_singleton_user(conn, settings)
    monkeypatch.setattr(initialization, "uuid4", lambda: uid)
    async with env.pool.acquire() as conn:
        with pytest.raises(initialization.IdentityInitializationRefused, match="reserved_identity"):
            await AccountService(cast(Any, conn), settings).claim_email_identity(
                normalized_email="no-resurrection@example.test",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="open",
                invite_token_verifier_hash=None,
            )
    assert await env.pool.fetchval("SELECT id FROM users WHERE id=$1", uid) is None
    assert await account_row(env, uid) is None


@pytest.mark.asyncio
async def test_birth_requires_real_transaction_and_read_committed(env, monkeypatch):
    _, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")
    async with env.pool.acquire() as conn:
        kwargs: dict[str, Any] = dict(
            normalized_email="transaction@example.test",
            username="transaction",
            email_verified_at=datetime.now(timezone.utc),
        )
        with pytest.raises(
            initialization.IdentityInitializationRefused, match="transaction_required"
        ):
            await initialization.insert_hosted_identity(conn, settings, **kwargs)
        async with conn.transaction(isolation="repeatable_read"):
            with pytest.raises(
                initialization.IdentityInitializationRefused, match="unsupported_isolation"
            ):
                await initialization.insert_hosted_identity(conn, settings, **kwargs)


async def blocked(observer, waiter, blocker):
    async with asyncio.timeout(5):
        while not await observer.fetchval("SELECT $2=ANY(pg_blocking_pids($1))", waiter, blocker):
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_account_initialization_lock_is_held_through_actual_commit(env, monkeypatch):
    from orchestrator.services.account_control.store import account_lock_key

    _, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")
    uid = uuid4()
    monkeypatch.setattr(initialization, "uuid4", lambda: uid)
    async with (
        env.pool.acquire() as owner,
        env.pool.acquire() as control,
        env.pool.acquire() as observer,
    ):
        transaction = owner.transaction()
        await transaction.start()
        try:
            born = await AccountService(
                cast(Any, owner), settings
            ).claim_email_identity_in_transaction(
                normalized_email="commit-wait@example.test",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="open",
                invite_token_verifier_hash=None,
            )
            assert born.user.id == uid
            assert (
                await observer.fetchval(
                    "SELECT user_id FROM entitlement_accounts WHERE user_id=$1", uid
                )
                is None
            )
            owner_pid, waiter_pid = (
                await owner.fetchval("SELECT pg_backend_pid()"),
                await control.fetchval("SELECT pg_backend_pid()"),
            )

            async def fence():
                async with control.transaction():
                    await control.execute("SELECT pg_advisory_xact_lock($1)", account_lock_key(uid))
                    row = await control.fetchrow(
                        "SELECT * FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", uid
                    )
                    assert row is not None and row["content_generation"] == 0
                    await control.execute(
                        "UPDATE entitlement_accounts SET status='suspended',content_generation=1 WHERE user_id=$1",
                        uid,
                    )

            pending = asyncio.create_task(fence())
            try:
                await blocked(observer, waiter_pid, owner_pid)
                assert not pending.done()
                await transaction.commit()
                await pending
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        finally:
            if owner.is_in_transaction():
                await transaction.rollback()
    after = await account_row(env, uid)
    assert after is not None
    assert after["status"] == "suspended" and after["content_generation"] == 1


@pytest.mark.asyncio
async def test_cancellation_during_real_account_advisory_wait_leaves_no_birth(env, monkeypatch):
    from orchestrator.services.account_control.store import account_lock_key

    _, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")
    uid = uuid4()
    monkeypatch.setattr(initialization, "uuid4", lambda: uid)
    async with (
        env.pool.acquire() as control,
        env.pool.acquire() as claimant,
        env.pool.acquire() as observer,
    ):
        async with control.transaction():
            await control.execute("SELECT pg_advisory_xact_lock($1)", account_lock_key(uid))
            owner_pid, waiter_pid = (
                await control.fetchval("SELECT pg_backend_pid()"),
                await claimant.fetchval("SELECT pg_backend_pid()"),
            )
            pending = asyncio.create_task(
                AccountService(cast(Any, claimant), settings).claim_email_identity(
                    normalized_email="cancel-wait@example.test",
                    email_verified_at=datetime.now(timezone.utc),
                    signup_mode="open",
                    invite_token_verifier_hash=None,
                )
            )
            try:
                await blocked(observer, waiter_pid, owner_pid)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert not claimant.is_in_transaction() and await claimant.fetchval("SELECT 1") == 1
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
    assert await env.pool.fetchval("SELECT id FROM users WHERE id=$1", uid) is None
    assert await account_row(env, uid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["suspended", "cause", "deletion", "reset", "closed_missing"])
async def test_existing_control_evidence_never_authorizes_birth(env, monkeypatch, tmp_path, kind):
    from tests.test_account_control_persistence import persist

    _, settings, _, _, _, _ = await _prepare(env, monkeypatch, "email")
    uid = env.alice
    await seed_entitlement(env.pool, uid)
    if kind == "suspended":
        await env.pool.execute(
            "UPDATE entitlement_accounts SET status='suspended',content_generation=8 WHERE user_id=$1",
            uid,
        )
    elif kind == "cause":
        await env.pool.execute(
            "INSERT INTO account_suspension_causes VALUES($1,$2,'operator')", uid, uuid4()
        )
    else:
        path, jid = tmp_path / "journal", uuid4()
        initialize(path, jid)
        settings.daemon_deletion_journal_dir = str(path)
        settings.daemon_deletion_journal_id = str(jid)
        await require_recovery_barrier(env.pool, settings)
        await persist(env, (path, jid), request(uid, reset=kind == "reset"), uuid4())
        if kind == "closed_missing":
            await env.pool.execute(
                "UPDATE account_deletions SET closed_at=requested_at WHERE account_id=$1", uid
            )
            await env.pool.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", uid)
    before = await account_row(env, uid)
    operations = [
        dict(row)
        for row in await env.pool.fetch("SELECT * FROM account_deletions WHERE account_id=$1", uid)
    ]
    causes = [
        dict(row)
        for row in await env.pool.fetch(
            "SELECT * FROM account_suspension_causes WHERE account_id=$1", uid
        )
    ]
    monkeypatch.setattr(initialization, "uuid4", lambda: uid)
    async with env.pool.acquire() as conn:
        with pytest.raises(initialization.IdentityInitializationRefused, match="account_history"):
            await AccountService(cast(Any, conn), settings).claim_email_identity(
                normalized_email="not-authority@example.test",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="open",
                invite_token_verifier_hash=None,
            )
    assert await account_row(env, uid) == before
    assert [
        dict(row)
        for row in await env.pool.fetch("SELECT * FROM account_deletions WHERE account_id=$1", uid)
    ] == operations
    assert [
        dict(row)
        for row in await env.pool.fetch(
            "SELECT * FROM account_suspension_causes WHERE account_id=$1", uid
        )
    ] == causes
