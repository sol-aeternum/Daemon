"""Exact W map on owned PostgreSQL: actual ordinary routes, SQL and lock powers.

External proof/rate/notification/network boundaries alone are scripted. Schema,
principals, persistence, encryption, auth/session and financial algorithms are real.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from db.video_credits import VideoCreditsDAL
from orchestrator import main
from orchestrator.config import get_settings
from orchestrator.entitlements.service import EntitlementService
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.services.identity.account_service import AccountService
from orchestrator.services.identity.email_challenge import (
    EmailChallengeIssueRequest,
    EmailChallengeService,
)
from orchestrator.services.identity.retention import sweep_identity_records
from orchestrator.services.web_snapshots import WebSnapshotStore
from orchestrator.skills_projection import SkillProjectionStore
from orchestrator.worker.audit import WorkerJobFailure, persist_worker_job_failure
from tests.exact_worker_privileges import MAP
from tests.test_inference_recovery_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_task_startup_postgres import services as services


@pytest.mark.asyncio
async def test_exact_effective_table_column_sequence_and_function_capabilities(db):
    async with db.connection("md_app") as w:
        assert (
            await w.fetchval("SELECT CURRENT_USER=SESSION_USER AND CURRENT_USER='md_app'") is True
        )
        assert await w.fetchval("SELECT pg_has_role('md_app','md_acceptor','MEMBER')") is False
        assert await w.fetchval("SELECT pg_has_role('md_app','md_owner','MEMBER')") is False
        for entry in MAP["ordinary_tables"]:
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert await w.fetchval(
                    "SELECT has_table_privilege(CURRENT_USER,$1,$2)", entry["table"], privilege
                ) is (privilege in entry["table_privileges"]), (entry["table"], privilege)
            columns = await w.fetch(
                "SELECT attname FROM pg_attribute WHERE attrelid=$1::regclass AND attnum>0 AND NOT attisdropped",
                entry["table"],
            )
            for column in columns:
                assert await w.fetchval(
                    "SELECT has_column_privilege(CURRENT_USER,$1,$2,'UPDATE')",
                    entry["table"],
                    column["attname"],
                ) is (
                    "UPDATE" in entry["table_privileges"]
                    or column["attname"] in entry["update_columns"]
                )
        for table in MAP["protected_tables_no_direct_privileges"]:
            assert (
                await w.fetchval(
                    "SELECT has_table_privilege(CURRENT_USER,$1,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
                    table,
                )
                is False
            )
            assert (
                await w.fetchval(
                    "SELECT has_any_column_privilege(CURRENT_USER,$1,'SELECT,INSERT,UPDATE,REFERENCES')",
                    table,
                )
                is False
            )
        sequences = await w.fetch(
            "SELECT c.oid::regclass::text AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S'"
        )
        for row in sequences:
            name = "public." + row["name"].removeprefix("public.")
            assert await w.fetchval(
                "SELECT has_sequence_privilege(CURRENT_USER,$1,'USAGE')", name
            ) is (name in MAP["sequences_usage_only"])
            assert (
                await w.fetchval("SELECT has_sequence_privilege(CURRENT_USER,$1,'UPDATE')", name)
                is False
            )
        for function in MAP["worker_public_entrypoints_execute"]:
            assert (
                await w.fetchval(
                    "SELECT has_function_privilege(CURRENT_USER,$1,'EXECUTE')", function
                )
                is True
            )
        for helper in (
            "compute_metadata_check_account(uuid)",
            "attempt_lifecycle_identity(jsonb)",
            "attempt_lifecycle_lock(jsonb)",
            "attempt_lifecycle_live(jsonb)",
            "task_inference_recovery_immutable()",
        ):
            assert (
                await w.fetchval(
                    "SELECT has_function_privilege(CURRENT_USER,$1,'EXECUTE')", "public." + helper
                )
                is False
            )
        assert (
            await w.fetchval(
                "SELECT has_table_privilege(CURRENT_USER,'public._migrations','SELECT,INSERT,UPDATE,DELETE')"
            )
            is False
        )
        assert (
            await w.fetchval(
                "SELECT has_table_privilege(CURRENT_USER,'public.deletion_journal_binding','INSERT,UPDATE,DELETE')"
            )
            is False
        )


@pytest_asyncio.fixture
async def ordinary(services, monkeypatch, tmp_path, caplog):
    from orchestrator.routes import (
        auth_setup,
        conversations,
        entitlements,
        memories,
        users,
        video_credits,
    )
    from orchestrator.routes.tasks import router as task_router

    value = services.settings
    value.daemon_deployment_mode = "hosted"
    value.daemon_hosted_identity_enabled = True
    value.daemon_google_enabled = False
    value.daemon_signup_mode = "open"
    value.daemon_cookie_secure = False
    value.daemon_public_origin = "https://fixture.daemon.test"
    value.daemon_allowed_origins = "https://fixture.daemon.test"
    value.daemon_setup_token_file = str(tmp_path / "setup.token")
    monkeypatch.setenv("DAEMON_AUTH_PEPPER", value.daemon_auth_pepper)
    get_settings.cache_clear()
    monkeypatch.setattr(main, "get_settings", lambda: value)
    monkeypatch.setattr(main, "load_repo_contents", lambda: {})
    monkeypatch.setattr(auth_setup, "get_settings", lambda: value)
    monkeypatch.setattr(auth_setup, "get_rate_limiter", lambda _: None)
    monkeypatch.setattr(auth_setup, "enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(auth_setup, "schedule_device_notification", lambda **_: None)
    finished = [asyncio.Event(), asyncio.Event()]
    for index, name in enumerate(("_backfill_skill_projections", "_sync_repo_skills")):
        original = getattr(main, name)

        async def tracked(pool, original=original, event=finished[index]):
            try:
                await original(pool)
            finally:
                event.set()

        monkeypatch.setattr(main, name, tracked)
    app = FastAPI(lifespan=main.lifespan)
    for router in (
        auth_setup.router,
        conversations.router,
        entitlements.router,
        memories.router,
        users.router,
        video_credits.router,
        task_router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: value
    try:
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in finished)), 3)
            assert not any(
                "Failed to backfill" in r.message
                or "backfill failed" in r.message
                or "setup check failed" in r.message
                for r in caplog.records
            )
            state = app.state.app_state
            assert state.db_pool is not None and state.memory_store is not None
            assert await state.db_pool.fetchval("SELECT CURRENT_USER") == "md_app"
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url=value.daemon_public_origin
            ) as client:
                yield SimpleNamespace(
                    app=app, state=state, settings=value, client=client, db=services.db
                )
    finally:
        get_settings.cache_clear()


async def sign_in(ordinary, address=None):
    address = address or ("fixture-" + uuid4().hex + "@example.test")
    async with ordinary.state.db_pool.acquire() as conn:
        proof, code = await EmailChallengeService(
            cast(Any, conn), ordinary.settings
        ).create_challenge_for_delivery(
            EmailChallengeIssueRequest(
                normalized_email=address,
                ip_hash=None,
                user_agent_hash=None,
                ttl_seconds=600,
                max_attempts=5,
            )
        )
    response = await ordinary.client.post(
        "/v1/auth/email/complete",
        json={
            "challenge_id": str(proof.id),
            "code": code,
            "client_kind": "native",
            "device_persistence": "private",
        },
    )
    assert response.status_code == 200, response.text
    tokens = response.json()
    uid = await ordinary.state.db_pool.fetchval(
        "SELECT id FROM users WHERE normalized_email=$1", address
    )
    return uid, tokens, {"Authorization": "Bearer " + tokens["access_token"]}


@pytest.mark.asyncio
async def test_actual_lifespan_newborn_auth_settings_history_memory_refresh_and_logout(ordinary):
    uid, tokens, headers = await sign_in(ordinary)
    store = ordinary.state.memory_store
    conversation = await store.create_conversation(uid, title="Synthetic private conversation")
    await store.insert_message(
        conversation["id"], uid, "user", "Encrypted synthetic content", status="complete"
    )
    memory = await store.insert_memory(
        uid, "Synthetic owner memory", "fact", "manual", status="pending"
    )
    response = await ordinary.client.get(
        "/conversations/" + str(conversation["id"]), headers=headers
    )
    assert response.status_code == 200 and "Encrypted synthetic content" in response.text
    assert (await ordinary.client.get("/users/me/entitlements", headers=headers)).status_code == 200
    assert (
        await ordinary.client.patch(
            "/users/me/settings",
            headers=headers,
            json={"preferences": {"custom_instructions": "Synthetic preference"}},
        )
    ).status_code == 200
    assert (
        "Synthetic preference"
        in (await ordinary.client.get("/users/me/settings", headers=headers)).text
    )
    assert (
        await ordinary.client.post(
            f"/memories/{memory['id']}/confirm", headers=headers, json={"status": "confirmed"}
        )
    ).status_code == 200
    other, _, other_headers = await sign_in(ordinary)
    assert (
        await ordinary.client.get(
            "/conversations/" + str(conversation["id"]), headers=other_headers
        )
    ).status_code == 404
    assert (
        await ordinary.client.post(
            f"/memories/{memory['id']}/confirm", headers=other_headers, json={"status": "rejected"}
        )
    ).status_code == 404
    assert other != uid
    rotated = await ordinary.client.post(
        "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"], "client_kind": "native"}
    )
    assert rotated.status_code == 200, rotated.text
    new_headers = {"Authorization": "Bearer " + rotated.json()["access_token"]}
    assert (await ordinary.client.get("/v1/auth/devices", headers=new_headers)).status_code == 200
    assert (await ordinary.client.post("/v1/auth/logout", headers=new_headers)).status_code == 204
    assert (await ordinary.client.get("/users/me/settings", headers=new_headers)).status_code == 401
    async with ordinary.db.connection() as owner:
        cipher = await owner.fetchval(
            "SELECT content FROM messages WHERE conversation_id=$1", conversation["id"]
        )
        assert cipher != "Encrypted synthetic content"


async def seed_historical_spend(ordinary, uid) -> UUID:
    # Synthetic fixture setup only: exercise actual W refund independently of the
    # separately failing runtime debit SQL. Never claim that debit succeeded.
    async with ordinary.db.connection() as owner:
        await owner.execute(
            "INSERT INTO video_credit_balances(user_id,balance) VALUES($1,7) "
            "ON CONFLICT(user_id) DO UPDATE SET balance=7",
            uid,
        )
        return await owner.fetchval(
            "INSERT INTO video_credit_transactions(user_id,type,amount,description) "
            "VALUES($1,'spend',3,'Synthetic historical fixture') RETURNING id",
            uid,
        )


@pytest.mark.asyncio
async def test_actual_billing_credit_refund_projection_audit_and_retention(ordinary):
    uid, _, _ = await sign_in(ordinary)
    pool = ordinary.state.db_pool
    service = EntitlementService(pool)
    hold = await service.reserve(uid, 100, operation="chat")
    await service.settle(hold, 40)
    assert (
        await pool.fetchval(
            "SELECT actual_microusd FROM entitlement_reservations WHERE id=$1", hold.id
        )
        == 40
    )
    credits = VideoCreditsDAL(pool)
    assert (await credits.credit_credits(uid, 10, "admin_grant", "Synthetic fixture")).success
    spend_id = await seed_historical_spend(ordinary, uid)
    assert (await credits.refund_transaction(spend_id)).success
    assert await credits.get_balance(uid) == 10
    projection = SkillProjectionStore(pool)
    await projection.upsert_projection(
        "fixture-skill", "Synthetic", "Fixture", "/synthetic/skill", "0" * 64
    )
    assert await projection.get_projection("fixture-skill") is not None
    await projection.touch_usage("fixture-skill")
    assert await projection.delete_projection("fixture-skill")
    await persist_worker_job_failure(
        {"db_pool": pool},
        WorkerJobFailure(
            "fixture-job",
            "fixture",
            "fixture",
            "[]",
            "{}",
            "SyntheticError",
            "closed-code",
            None,
            1,
        ),
    )
    async with pool.acquire() as conn:
        counts = await sweep_identity_records(conn)
    assert set(counts) == {
        "identity_audit_log",
        "email_challenges",
        "google_nonce_challenges",
        "signup_invites",
    }


@pytest.mark.asyncio
async def test_actual_snapshot_create_and_selected_row_lock(ordinary):
    uid, _, _ = await sign_in(ordinary)
    conversation = await ordinary.state.memory_store.create_conversation(
        uid, title="Snapshot fixture"
    )
    snapshots = WebSnapshotStore(
        ordinary.state.db_pool,
        ContentEncryption(ordinary.settings.daemon_encryption_key),
        ordinary.settings,
    )
    value = await snapshots.create(
        uid,
        conversation["id"],
        source_url="https://example.test/synthetic",
        content="Fixture only",
        extract_mode="text",
        extraction_version="fixture-v1",
    )
    selected = []

    async def on_selected(conn, identity):
        assert await conn.fetchval("SELECT CURRENT_USER") == "md_app"
        selected.append(identity)

    found = await snapshots.find_latest(
        uid,
        conversation["id"],
        "https://example.test/synthetic",
        "text",
        "fixture-v1",
        on_selected=on_selected,
    )
    assert found is not None and found.id == value.id and selected == [value.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,column,other",
    [
        ("account_deletions", "id", "account_id"),
        ("account_email_ownership", "ended_at", "normalized_email"),
        ("identity_audit_log", "id", "event_type"),
        ("video_credit_transactions", "id", "amount"),
        ("web_snapshots", "id", "content_encrypted"),
    ],
)
async def test_column_lock_grants_retain_real_update_power_but_not_other_column_updates(
    db, table, column, other
):
    async with db.connection("md_app") as w:
        # Permission checks still run on an empty predicate. This is actual UPDATE
        # authority, not a special read-only row-lock grant or a no-mutation proof.
        assert (
            await w.execute(f"UPDATE public.{table} SET {column}={column} WHERE false")
            == "UPDATE 0"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await w.execute(f"UPDATE public.{table} SET {other}={other} WHERE false")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,column",
    [
        ("account_deletions", "id"),
        ("account_email_ownership", "ended_at"),
        ("identity_audit_log", "id"),
        ("video_credit_transactions", "id"),
        ("web_snapshots", "id"),
    ],
)
async def test_five_existing_operations_fail_closed_without_their_column_update(
    ordinary, table, column
):
    uid, _, _ = await sign_in(ordinary)
    pool = ordinary.state.db_pool
    conversation = await ordinary.state.memory_store.create_conversation(
        uid, title="Permission fixture"
    )
    snapshots = WebSnapshotStore(
        pool, ContentEncryption(ordinary.settings.daemon_encryption_key), ordinary.settings
    )
    if table == "web_snapshots":
        await snapshots.create(
            uid,
            conversation["id"],
            source_url="https://example.test/permission",
            content="Fixture",
            extract_mode="text",
            extraction_version="fixture-v1",
        )
    credits = VideoCreditsDAL(pool)
    spend_id = None
    if table == "video_credit_transactions":
        spend_id = await seed_historical_spend(ordinary, uid)
    ordinary.db.sql(f"REVOKE UPDATE({column}) ON public.{table} FROM md_app")

    async def selected(conn, identity):
        pytest.fail("snapshot hook ran despite refused row lock")

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        if table == "account_deletions":
            async with pool.acquire() as conn:
                await AccountService(cast(Any, conn), ordinary.settings).claim_email_identity(
                    normalized_email="new-" + uuid4().hex + "@example.test",
                    email_verified_at=datetime.now(timezone.utc),
                    signup_mode="open",
                    invite_token_verifier_hash=None,
                )
        elif table == "account_email_ownership":
            async with pool.acquire() as conn:
                await AccountService(
                    cast(Any, conn), ordinary.settings
                ).update_user_normalized_email(uid, "changed-" + uuid4().hex + "@example.test")
        elif table == "identity_audit_log":
            async with pool.acquire() as conn:
                await sweep_identity_records(conn)
        elif table == "video_credit_transactions":
            assert spend_id is not None
            await credits.refund_transaction(spend_id)
        else:
            await snapshots.find_latest(
                uid,
                conversation["id"],
                "https://example.test/permission",
                "text",
                "fixture-v1",
                on_selected=selected,
            )
