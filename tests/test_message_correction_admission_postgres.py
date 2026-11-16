"""Prospective source admission on owned disposable PostgreSQL; no inference.

TASKS_TEST_DATABASE_URL must come from an explicitly owned fixture, never the
application URL. Production SQL, account guards and native/task writers are real.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from orchestrator import main
from orchestrator.home_suggestions.contracts import bound_context, fingerprint
from orchestrator.memory.startup import require_revision_schema
from orchestrator.services.account_control.guard import WriteGuardRefusal
from tests.durable_tasks_support import durable_env_fixture, migration_sql_for_schema, task_capture
from tests.test_task_api_postgres import api as api, _events

env = durable_env_fixture(initialized=True)


async def live(env, *, status="complete", capture=None, conversation=None):
    if conversation is None:
        conversation = (await env.memory.create_conversation(env.alice, title="Synthetic"))["id"]
    return await env.memory.insert_live_user_message(
        conversation,
        env.alice,
        "Fictional source A.",
        status=status,
        captured_generation=capture or await task_capture(env),
    )


async def source(env, mid):
    row = await env.pool.fetchrow(
        "SELECT source_revision,correction_origin_generation,correction_source_revision "
        "FROM messages WHERE id=$1",
        mid,
    )
    return tuple(row.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "assistant", "system", "tool"])
@pytest.mark.parametrize("status", [None, "complete", "completed", "streaming"])
async def test_generic_and_import_metadata_never_admit(env, role, status):
    conv = await env.memory.create_conversation(env.alice)
    row = await env.memory.insert_message(
        conv["id"],
        env.alice,
        role,
        "Imported fiction.",
        status=status,
        metadata={"source": "import", "correction_origin_generation": 0},
    )
    assert await source(env, row["id"]) == (1, None, None)
    await env.memory.update_message(row["id"], status="complete")
    assert (await source(env, row["id"]))[1:] == (None, None)


@pytest.mark.asyncio
async def test_streaming_first_complete_freezes_exact_revision_once(env):
    row = await live(env, status="streaming")
    assert await source(env, row["id"]) == (1, 0, None)
    await env.memory.update_message(row["id"], content="Fictional source B.")
    assert await source(env, row["id"]) == (2, 0, None)
    await env.memory.update_message(row["id"], status="complete")
    assert await source(env, row["id"]) == (3, 0, 3)
    for status in ("streaming", "cancelled", "complete"):
        await env.memory.update_message(row["id"], status=status)
    assert await source(env, row["id"]) == (6, 0, 3)


@pytest.mark.asyncio
async def test_complete_insert_and_content_aba_cannot_restore_frozen_version(env):
    row = await live(env)
    assert await source(env, row["id"]) == (1, 0, 1)
    await env.memory.update_message(row["id"], content="Fictional source B.")
    await env.memory.update_message(row["id"], content=row["content"])
    assert await source(env, row["id"]) == (3, 0, 1)
    # Restored plaintext is not restored source identity/version.
    assert (
        await env.memory.get_owned_message(
            row["id"], user_id=env.alice, conversation_id=row["conversation_id"]
        )
    )["content"] == row["content"]


@pytest.mark.asyncio
async def test_noop_and_closed_token_maintenance_allowlist(env):
    row = await live(env)
    await env.pool.execute(
        "UPDATE messages SET content=content,status=status,metadata=metadata,tokens_in=4,tokens_out=8 "
        "WHERE id=$1",
        row["id"],
    )
    assert await source(env, row["id"]) == (1, 0, 1)
    await env.memory.update_message(row["id"], metadata={"source_context": "changed"})
    assert await source(env, row["id"]) == (2, 0, 1)
    # New fields are semantic by default, without changing a trigger field list.
    await env.pool.execute("ALTER TABLE messages ADD COLUMN future_source_context integer")
    await env.pool.execute("UPDATE messages SET future_source_context=1 WHERE id=$1", row["id"])
    assert await source(env, row["id"]) == (3, 0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("source_revision", 2),
        ("source_revision", None),
        ("correction_source_revision", 2),
        ("correction_source_revision", None),
        ("correction_origin_generation", 1),
        ("correction_origin_generation", None),
    ],
)
async def test_forged_managed_field_updates_fail_even_with_semantic_edit(env, field, value):
    row = await live(env)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await env.pool.execute(
            f"UPDATE messages SET {field}=$2,metadata='{{\"changed\":true}}' WHERE id=$1",
            row["id"],
            value,
        )
    assert await source(env, row["id"]) == (1, 0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "user_id", "conversation_id", "role"])
async def test_admitted_identity_is_immutable_but_generic_identity_is_not_restricted(env, field):
    row = await live(env)
    other = await env.memory.create_conversation(env.bob)
    value = {
        "id": uuid4(),
        "user_id": env.bob,
        "conversation_id": other["id"],
        "role": "assistant",
    }[field]
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="identity_immutable"):
        await env.pool.execute(f"UPDATE messages SET {field}=$2 WHERE id=$1", row["id"], value)
    generic = await env.memory.insert_message(
        row["conversation_id"], env.alice, "user", "Generic source.", status="complete"
    )
    assert (
        await env.pool.execute(f"UPDATE messages SET {field}=$2 WHERE id=$1", generic["id"], value)
        == "UPDATE 1"
    )


@pytest.mark.asyncio
async def test_generic_row_cannot_be_promoted_by_update_or_completion(env):
    row = await live(env)
    generic = await env.memory.insert_message(
        row["conversation_id"], env.alice, "user", "Legacy fiction."
    )
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="admission_immutable"):
        await env.pool.execute(
            "UPDATE messages SET correction_origin_generation=0,status='complete' WHERE id=$1",
            generic["id"],
        )
    await env.memory.update_message(generic["id"], status="complete")
    assert await source(env, generic["id"]) == (2, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [",source_revision", ",correction_source_revision"])
async def test_forged_insert_versions_fail(env, extra):
    conv = await env.memory.create_conversation(env.alice)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="revision_managed"):
        await env.pool.execute(
            "INSERT INTO messages(conversation_id,user_id,role,content" + extra + ") "
            "VALUES($1,$2,'user','synthetic-ciphertext',2)",
            conv["id"],
            env.alice,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["foreign_conversation", "assistant", "system", "tool"])
async def test_database_validates_admitted_source_not_sql_callsite(env, kind):
    owner = env.bob if kind == "foreign_conversation" else env.alice
    conv = await env.memory.create_conversation(owner)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="source_invalid"):
        await env.pool.execute(
            "INSERT INTO messages(conversation_id,user_id,role,content,correction_origin_generation) "
            "VALUES($1,$2,$3,'synthetic-ciphertext',0)",
            conv["id"],
            env.alice,
            "user" if kind == "foreign_conversation" else kind,
        )
    # Explicit trust-boundary control: a privileged same-DB writer CAN forge
    # matching birth admission. Neither a GUC nor this trigger authenticates it.
    row = await env.pool.fetchrow(
        "INSERT INTO messages(conversation_id,user_id,role,content,status,correction_origin_generation) "
        "VALUES($1,$2,'user','synthetic-ciphertext','complete',0) RETURNING id",
        conv["id"],
        owner,
    )
    assert await source(env, row["id"]) == (1, 0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "foreign", "stale", "suspended", "malformed"])
async def test_original_capture_refusals_never_insert(env, kind):
    conv = await env.memory.create_conversation(env.alice)
    capture = await task_capture(env)
    if kind == "missing":
        capture = None
    elif kind == "foreign":
        capture = await task_capture(env, env.bob)
    elif kind == "malformed":
        object.__setattr__(capture, "generation", True)
    else:
        await env.pool.execute(
            "UPDATE entitlement_accounts SET content_generation=1,status=$2 WHERE user_id=$1",
            env.alice,
            "suspended" if kind == "suspended" else "active",
        )
    with pytest.raises(WriteGuardRefusal):
        await env.memory.insert_live_user_message(
            conv["id"], env.alice, "Refused source.", captured_generation=capture
        )
    assert await env.pool.fetchval("SELECT count(*) FROM messages") == 0


@pytest.mark.asyncio
async def test_caller_transaction_rollback_and_missing_transaction(env):
    conv = await env.memory.create_conversation(env.alice)
    capture = await task_capture(env)
    async with env.pool.acquire() as conn:
        with pytest.raises(WriteGuardRefusal, match="transaction_required"):
            await env.memory.insert_live_user_message(
                conv["id"], env.alice, "No transaction.", captured_generation=capture, conn=conn
            )
        with pytest.raises(RuntimeError, match="synthetic rollback"):
            async with conn.transaction(isolation="read_committed"):
                await env.memory.insert_live_user_message(
                    conv["id"], env.alice, "Rolled back.", captured_generation=capture, conn=conn
                )
                raise RuntimeError("synthetic rollback")
        assert await conn.fetchval("SELECT count(*) FROM messages") == 0
    row = await live(env, conversation=conv["id"], capture=capture)
    assert await source(env, row["id"]) == (1, 0, 1)


async def accept(env, *, key="source-admission", capture=None):
    return await env.tasks.accept(
        user_id=env.alice,
        conversation_id=None,
        new_conversation_title="Synthetic",
        pipeline="cloud",
        user_message="Accepted source.",
        task_input={"message": "Accepted source."},
        request_hash="a" * 64,
        request_canonical="synthetic canonical",
        idempotency_key=key,
        assistant_model=None,
        captured_generation=capture,
    )


@pytest.mark.asyncio
async def test_durable_new_acceptance_admits_user_only_and_replay_never_refreshes(env):
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=7 WHERE user_id=$1", env.alice
    )
    first = await accept(env, capture=await task_capture(env))
    assert await source(env, first.user_message_id) == (1, 7, 1)
    assert (await source(env, first.result_message_id))[1:] == (None, None)
    await env.pool.execute(
        "UPDATE entitlement_accounts SET content_generation=8 WHERE user_id=$1", env.alice
    )
    replay = await accept(env)
    assert not replay.created and replay.task_id == first.task_id
    assert await source(env, first.user_message_id) == (1, 7, 1)
    assert await env.pool.fetchval("SELECT count(*) FROM messages") == 2


@pytest.mark.asyncio
async def test_legacy_task_replay_does_not_promote_generic_persisted_user(env):
    conv = await env.memory.create_conversation(env.alice)
    user = await env.memory.insert_message(
        conv["id"], env.alice, "user", "Legacy.", status="complete"
    )
    reply = await env.memory.insert_message(
        conv["id"], env.alice, "assistant", "", status="streaming"
    )
    await env.pool.execute(
        "INSERT INTO tasks(user_id,conversation_id,user_message_id,result_message_id,input_ciphertext,"
        "input_version,idempotency_key,request_hash) VALUES($1,$2,$3,$4,$5,1,'source-admission',$6)",
        env.alice,
        conv["id"],
        user["id"],
        reply["id"],
        env.tasks._seal({"message": "Accepted source.", "_request": "synthetic canonical"}),
        "a" * 64,
    )
    replay = await accept(env)
    assert not replay.created and replay.user_message_id == user["id"]
    assert await source(env, user["id"]) == (1, None, None)


@pytest.mark.asyncio
async def test_actual_copied_home_prompt_stays_ineligible(env):
    await env.memory.merge_user_preferences(env.alice, {"home_suggestions_enabled": True})
    conv = await env.memory.create_conversation(env.alice, title="Synthetic source")
    await env.memory.insert_message(conv["id"], env.alice, "user", "Fiction.", status="complete")
    enabled, epoch, sources = await env.memory.home_suggestion_snapshot(env.alice)
    assert enabled and sources
    destination = await env.memory.bind_home_suggestion(
        env.alice,
        epoch=epoch,
        expected_fingerprint=fingerprint(sources),
        prompt="Generated follow-up.",
        context=bound_context({"id": "a" * 32, "source_index": 0}, sources),
    )
    mid = await env.pool.fetchval("SELECT id FROM messages WHERE conversation_id=$1", destination)
    assert await source(env, mid) == (1, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_native_uses_original_capture_without_admission_fallback(api, monkeypatch, stale):
    capture = AsyncMock(wraps=main.capture_generation)
    monkeypatch.setattr(main, "capture_generation", capture)
    original = api.env.memory.insert_live_user_message
    provider = AsyncMock(side_effect=AssertionError("refused admission cannot dispatch"))
    if stale:

        async def interleaved(*args, **kwargs):
            await api.env.pool.execute(
                "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                api.env.alice,
            )
            return await original(*args, **kwargs)

        monkeypatch.setattr(api.env.memory, "insert_live_user_message", interleaved)
        monkeypatch.setattr("orchestrator.daemon.completion_with_tools", provider)
    response = await api.client.post(
        "/chat", headers={"X-Daemon-Client-Features": ""}, json={"message": "Fictional live turn."}
    )
    assert response.status_code == 200
    capture.assert_awaited_once()
    events = _events(response.text)
    done = next(e["data"] for kind, e in events if kind == "done")
    if stale:
        assert done == {"ok": False}
    else:
        assert done["status"] == "completed" and not any(kind == "error" for kind, _ in events)
    users = await api.env.pool.fetch("SELECT * FROM messages WHERE role='user'")
    if stale:
        assert users == []
        provider.assert_not_called()
    else:
        assert len(users) == 1
        assert await source(api.env, users[0]["id"]) == (1, 0, 1)
        assert not await api.env.pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM messages WHERE role!='user' AND correction_origin_generation IS NOT NULL)"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_native_storage_fault_degrades_without_persisting_a_reply(api, monkeypatch, fault):
    """A storage fault is not a refusal: the turn runs unsaved and writes no reply.

    ``after_commit`` is the uncertain-commit case: the admission committed but
    the caller saw a failure. The admitted question stays, with no reply attached.
    """
    original = api.env.memory.insert_live_user_message

    async def faulty(*args, **kwargs):
        if kwargs.get("conn") is not None:
            # The store re-enters itself inside its own transaction; fault only
            # the caller's view, after that transaction has committed.
            return await original(*args, **kwargs)
        if fault == "after_commit":
            await original(*args, **kwargs)
        raise asyncpg.PostgresConnectionError("synthetic connection loss")

    monkeypatch.setattr(api.env.memory, "insert_live_user_message", faulty)
    response = await api.client.post(
        "/chat", headers={"X-Daemon-Client-Features": ""}, json={"message": "Fictional live turn."}
    )
    assert response.status_code == 200
    events = _events(response.text)
    assert not any(kind in ("error", "conversation") for kind, _ in events)
    assert next(e for kind, e in events if kind == "done").get("ok") is not False
    assert any(kind == "token" for kind, _ in events)
    rows = await api.env.pool.fetch("SELECT id,role FROM messages")
    if fault == "before_commit":
        assert rows == []
    else:
        assert [row["role"] for row in rows] == ["user"]
        assert await source(api.env, rows[0]["id"]) == (1, 0, 1)
    assert await api.env.pool.fetchval("SELECT count(*) FROM tasks") == 0


@pytest.mark.asyncio
async def test_upgrade_leaves_existing_complete_and_null_status_rows_ineligible(env):
    schema = "tasks_test_" + uuid4().hex
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    async with env.pool.acquire() as conn, conn.transaction():
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET LOCAL search_path TO "{schema}",public')
        for path in sorted(migrations.glob("*.sql")):
            if path.name < "071_message_correction_admission.sql":
                await conn.execute(migration_sql_for_schema(path, schema))
        owner, conv = uuid4(), uuid4()
        await conn.execute("INSERT INTO users(id,username) VALUES($1,$2)", owner, str(owner))
        await conn.execute(
            "INSERT INTO conversations(id,user_id,pipeline) VALUES($1,$2,'cloud')", conv, owner
        )
        await conn.execute(
            "INSERT INTO messages(conversation_id,user_id,role,content,status) "
            "VALUES($1,$2,'user','synthetic-ciphertext','complete'),($1,$2,'user','synthetic-ciphertext',NULL)",
            conv,
            owner,
        )
        await conn.execute((migrations / "071_message_correction_admission.sql").read_text())
        assert await conn.fetchval(
            "SELECT bool_and(source_revision=1 AND correction_origin_generation IS NULL "
            "AND correction_source_revision IS NULL) FROM messages"
        )
        await conn.execute("UPDATE messages SET status='complete'")
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM messages WHERE correction_source_revision IS NOT NULL)"
        )
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.asyncio
async def test_startup_requires_admission_schema_and_enabled_trigger(env):
    await require_revision_schema(env.pool)
    await env.pool.execute("ALTER TABLE messages DISABLE TRIGGER message_source_revision_guard")
    with pytest.raises(RuntimeError, match="071"):
        await require_revision_schema(env.pool)
    await env.pool.execute("ALTER TABLE messages ENABLE TRIGGER message_source_revision_guard")
    await require_revision_schema(env.pool)
    await env.pool.execute("ALTER TABLE messages ALTER COLUMN source_revision DROP DEFAULT")
    with pytest.raises(RuntimeError, match="071"):
        await require_revision_schema(env.pool)
