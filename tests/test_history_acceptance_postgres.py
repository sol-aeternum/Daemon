"""Restricted-role source candidate only; no runtime H/SQL coordinator or live DSN."""

import hashlib
import json
import struct
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.restore_history.model import (
    DOMAIN,
    AcceptPrepare,
    Command,
    canonical,
    commitments,
    decode_entry,
    encode_entry,
)
from orchestrator.tasks.inputs import chat_request_fingerprint
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import HISTORY, KEY, ZERO, accept, key_check
from tests.test_inference_recovery_postgres import db as db, metadata_cluster as metadata_cluster

CANDIDATE = (
    Path(__file__).resolve().parents[1] / "migrations/pending/restore_history_acceptance.sql"
)
PROJECT = "SELECT * FROM public.task_history_acceptance_project($1)"
PAGE = "SELECT * FROM public.task_history_acceptance_page($1,$2,$3)"


@pytest_asyncio.fixture
async def projection_db(db, request):
    db.sql("BEGIN;" + CANDIDATE.read_text() + "COMMIT;")
    if getattr(request, "param", True):
        async with db.connection() as conn:
            await conn.execute(
                "INSERT INTO task_history_binding(history_id,version,key_id,key_check,request_schema) "
                "VALUES($1,1,1,$2,'chat-canonical-3715-v1')",
                HISTORY,
                key_check(KEY, HISTORY),
            )
    db.sql(
        "GRANT EXECUTE ON FUNCTION public.task_history_acceptance_project(bytea) TO md_acceptor;"
        "GRANT EXECUTE ON FUNCTION public.task_history_acceptance_page(uuid,bigint,text) TO md_acceptor,md_app;"
    )
    return db


def frame_with(payload):
    raw = canonical(payload)
    prefix = struct.pack(">I", len(raw))
    return prefix + raw + hashlib.sha256(DOMAIN + prefix + raw).digest()


async def native_task(db, *, account=None, key=None, generation=0, seq=1, previous=ZERO, hook=None):
    async with db.connection() as owner:
        if account is None:
            account = await db.account(owner)
    pool = await asyncpg.create_pool(
        host=str(db.host),
        user="md_acceptor",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=1,
    )
    try:
        enc = ContentEncryption(Fernet.generate_key().decode())
        tasks = TaskStore(pool, enc, MemoryStore(pool, enc), cover_new_inference=True)
        fingerprint = chat_request_fingerprint(
            key="synthetic-pepper",
            conversation_id=None,
            message="synthetic",
            attachments=None,
            model=None,
            provider=None,
            metadata=None,
            disable_memory_write=False,
        )
        key = key or uuid4().hex
        lookup, request = commitments(KEY, account, key, fingerprint.canonical.encode())
        original = tasks._append_event
        prepared = []

        async def event(conn, task_id, kind, payload=None):
            value = await original(conn, task_id, kind, payload)
            if kind == "accepted":
                row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
                command = Command(
                    uuid4(),
                    AcceptPrepare(
                        account,
                        generation,
                        task_id,
                        row["conversation_id"],
                        row["user_message_id"],
                        row["result_message_id"],
                        1,
                        1,
                        lookup,
                        request,
                        "chat-canonical-3715-v1",
                    ),
                )
                frame = encode_entry(HISTORY, seq, previous, command)
                entry = decode_entry(frame)
                prepared.append(entry)
                if hook is not None:
                    await hook(conn, frame)
            return value

        tasks._append_event = event
        options: dict[str, Any] = dict(
            user_id=account,
            conversation_id=None,
            new_conversation_title="fixture",
            pipeline="cloud",
            user_message="synthetic",
            task_input={"message": "synthetic"},
            request_hash=fingerprint.digest,
            request_canonical=fingerprint.canonical,
            idempotency_key=key,
            assistant_model=None,
            captured_generation=CapturedGeneration(account, generation),
        )
        result = await tasks.accept(**options)
        assert len(prepared) == 1
        return SimpleNamespace(
            entry=prepared[0],
            frame=encode_entry(HISTORY, seq, previous, prepared[0].command),
            task=result,
            account=account,
            key=key,
        )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_sql_decoder_matches_h1_bytes_and_rejects_alternatives(projection_db):
    frame = encode_entry(HISTORY, 1, ZERO, accept())
    payload = json.loads(frame[4:-32])
    bad = []
    for obj, key, value in [
        ("root", "v", 1.0),
        ("root", "seq", True),
        ("root", "history_id", "AAAAAAAA-0000-0000-0000-000000000001"),
        ("data", "key_id", 1.0),
        ("data", "commitment_version", True),
        ("data", "account_generation", 1.0),
        ("data", "request_commitment", "x" * 64),
        ("data", "task_id", "AAAAAAAA-0000-0000-0000-000000000002"),
        ("root", "kind", "SEND_POSSIBLE"),
        ("root", "actor", "worker"),
    ]:
        changed = json.loads(json.dumps(payload))
        target = changed if obj == "root" else changed["data"]
        target[key] = value
        bad.append(frame_with(changed))
    changed = json.loads(json.dumps(payload))
    changed["data"]["unexpected"] = "no"
    bad.append(frame_with(changed))
    duplicate = frame[4:-32].replace(b'"v":1', b'"v":1,"v":1')
    prefix = struct.pack(">I", len(duplicate))
    bad.append(prefix + duplicate + hashlib.sha256(DOMAIN + prefix + duplicate).digest())
    bad.extend([b"", frame[:-1], frame[:-1] + bytes([frame[-1] ^ 1]), frame + b"x"])
    async with projection_db.connection() as conn:
        assert (
            json.loads(
                await conn.fetchval("SELECT public.task_history_decode_acceptance($1)", frame)
            )
            == payload
        )
        for candidate in bad:
            with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                await conn.fetchval("SELECT public.task_history_decode_acceptance($1)", candidate)


@pytest.mark.asyncio
async def test_original_transaction_stages_projection_and_duplicate_is_historical(projection_db):
    staged = []

    async def hook(conn, frame):
        assert conn.is_in_transaction()
        staged.append(dict(await conn.fetchrow(PROJECT, frame)))

    value = await native_task(projection_db, hook=hook)
    assert staged[0]["created"] is True and bytes(staged[0]["frame"]) == value.frame
    async with projection_db.connection("md_acceptor") as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(PROJECT, value.frame)
            assert existing["created"] is False
    for role in ["md_acceptor", "md_app"]:
        async with projection_db.connection(role) as conn:
            rows = await conn.fetch(PAGE, HISTORY, 0, key_check(KEY, HISTORY))
            assert len(rows) == 1 and bytes(rows[0]["frame"]) == value.frame
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 1


@pytest.mark.asyncio
async def test_projection_rolls_back_with_native_acceptance(projection_db):
    async def hook(conn, frame):
        assert (await conn.fetchrow(PROJECT, frame))["created"] is True
        raise RuntimeError("synthetic original transaction failure")

    with pytest.raises(RuntimeError, match="synthetic original transaction failure"):
        await native_task(projection_db, hook=hook)
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM tasks") == 0
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 0
        assert await conn.fetchval("SELECT count(*) FROM messages") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_app", "md_third", "md_owner"])
async def test_wrong_principal_cannot_project(projection_db, role):
    value = await native_task(projection_db)
    async with projection_db.connection(role) as conn:
        with pytest.raises(asyncpg.PostgresError):
            await conn.fetchrow(PROJECT, value.frame)
    # Even accidental W EXECUTE does not replace A's incompatible native authority.
    if role == "md_app":
        projection_db.sql(
            "GRANT EXECUTE ON FUNCTION task_history_acceptance_project(bytea) TO md_app"
        )
        async with projection_db.connection(role) as conn:
            with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                await conn.fetchrow(PROJECT, value.frame)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_app", "md_acceptor", "md_third"])
async def test_no_direct_table_or_internal_function_access(projection_db, role):
    async with projection_db.connection(role) as conn:
        for sql in [
            "SELECT * FROM task_history_binding",
            "SELECT * FROM task_history_acceptance",
            "DELETE FROM task_history_acceptance",
            "TRUNCATE task_history_binding",
            "SELECT public.task_history_decode_acceptance($1)",
        ]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(
                    sql, *([encode_entry(HISTORY, 1, ZERO, accept())] if "$1" in sql else [])
                )


@pytest.mark.asyncio
async def test_projection_and_binding_immutable_and_survive_native_cascade(projection_db):
    async def hook(conn, frame):
        await conn.fetchrow(PROJECT, frame)

    value = await native_task(projection_db, hook=hook)
    async with projection_db.connection() as conn:
        for table in ["task_history_binding", "task_history_acceptance"]:
            for sql in [
                f"DELETE FROM {table}",
                f"TRUNCATE {table} CASCADE",
                f"UPDATE {table} SET history_id=history_id",
            ]:
                with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                    await conn.execute(sql)
        await conn.execute("DELETE FROM users WHERE id=$1", value.account)
        assert await conn.fetchval("SELECT count(*) FROM tasks") == 0
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 1
    async with projection_db.connection("md_app") as conn:
        assert (
            bytes((await conn.fetch(PAGE, HISTORY, 0, key_check(KEY, HISTORY)))[0]["frame"])
            == value.frame
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "account_id",
        "account_generation",
        "conversation_id",
        "user_message_id",
        "result_message_id",
        "task_id",
    ],
)
async def test_wrong_native_identity_refused(projection_db, field):
    value = await native_task(projection_db)
    data = replace(
        value.entry.command.data, **{field: 1 if field == "account_generation" else uuid4()}
    )
    frame = encode_entry(HISTORY, 1, ZERO, Command(uuid4(), data))
    async with projection_db.connection("md_acceptor") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetchrow(PROJECT, frame)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["generation", "suspended", "cause"])
async def test_current_account_control_refuses(projection_db, state):
    value = await native_task(projection_db)
    async with projection_db.connection() as conn:
        if state == "generation":
            await conn.execute(
                "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                value.account,
            )
        elif state == "suspended":
            await conn.execute(
                "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1",
                value.account,
            )
        else:
            await conn.execute(
                "INSERT INTO account_suspension_causes(account_id,cause_id,cause_code) VALUES($1,$2,'operator')",
                value.account,
                uuid4(),
            )
    async with projection_db.connection("md_acceptor") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetchrow(PROJECT, value.frame)


@pytest.mark.asyncio
@pytest.mark.parametrize("projection_db", [False], indirect=True)
async def test_missing_binding_and_wrong_history_refused(projection_db):
    value = await native_task(projection_db)
    async with projection_db.connection("md_acceptor") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetchrow(PROJECT, value.frame)
    async with projection_db.connection("md_app") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetch(PAGE, HISTORY, 0, key_check(KEY, HISTORY))


def test_candidate_not_automatically_discovered_or_numbered():
    root = CANDIDATE.parent.parent
    assert CANDIDATE not in list(root.glob("*.sql"))
    assert not CANDIDATE.name.startswith("056_")


def adapter_for(expected=None):
    from orchestrator.services.restore_history.acceptance import AcceptanceProjection
    from orchestrator.services.restore_history.model import Identity

    return AcceptanceProjection(
        expected or Identity(HISTORY, key_check(KEY, HISTORY)),
        acceptor="md_acceptor",
        worker="md_app",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,writing", [("md_acceptor", True), ("md_acceptor", False), ("md_app", False)]
)
async def test_uncalled_current_connection_qualification(projection_db, role, writing):
    import asyncio

    adapter = adapter_for()
    async with projection_db.connection(role) as conn:
        async with conn.transaction():
            await adapter.qualify(
                conn, writing=writing, deadline=asyncio.get_running_loop().time() + 5
            )
            assert conn.is_in_transaction()
        assert not conn.is_in_transaction()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,writing", [("md_app", True), ("md_third", False), ("md_owner", False)]
)
async def test_adapter_no_wrong_principal_or_owner_fallback(projection_db, role, writing):
    import asyncio
    from orchestrator.services.restore_history.model import HistoryError

    async with projection_db.connection(role) as conn:
        with pytest.raises(HistoryError):
            async with conn.transaction():
                await adapter_for().qualify(
                    conn, writing=writing, deadline=asyncio.get_running_loop().time() + 5
                )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    [
        "GRANT EXECUTE ON FUNCTION task_history_acceptance_project(bytea) TO md_app",
        "GRANT EXECUTE ON FUNCTION task_history_acceptance_page(uuid,bigint,text) TO PUBLIC",
        "GRANT SELECT(key_check) ON task_history_binding TO md_acceptor",
        "GRANT UPDATE(account_generation) ON task_history_acceptance TO md_app",
        "ALTER FUNCTION task_history_acceptance_project(bytea) SET search_path=public",
        "ALTER TABLE task_history_acceptance DISABLE TRIGGER task_history_acceptance_immutable",
    ],
)
async def test_adapter_rechecks_current_catalog_after_previous_success(projection_db, drift):
    import asyncio
    from orchestrator.services.restore_history.model import HistoryError

    adapter = adapter_for()
    async with projection_db.connection("md_acceptor") as conn:
        async with conn.transaction():
            await adapter.qualify(
                conn, writing=True, deadline=asyncio.get_running_loop().time() + 5
            )
        projection_db.sql(drift)
        with pytest.raises(HistoryError):
            async with conn.transaction():
                await adapter.qualify(
                    conn, writing=True, deadline=asyncio.get_running_loop().time() + 5
                )


@pytest.mark.asyncio
async def test_native_key_namespace_and_generation_do_not_overwrite(projection_db):
    async def hook(conn, frame):
        await conn.fetchrow(PROJECT, frame)

    first = await native_task(projection_db, key="synthetic-stable-key", hook=hook)
    second = await native_task(
        projection_db, key=first.key, seq=2, previous=first.entry.digest, hook=hook
    )
    assert first.account != second.account
    async with projection_db.connection() as conn:
        await conn.execute("DELETE FROM conversations WHERE id=$1", first.task.conversation_id)
        await conn.execute(
            "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1", first.account
        )
    with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
        await native_task(
            projection_db,
            account=first.account,
            key=first.key,
            generation=1,
            seq=3,
            previous=second.entry.digest,
            hook=hook,
        )
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 2


@pytest.mark.asyncio
async def test_concurrent_identical_projection_has_only_one_created_result(projection_db):
    import asyncio

    value = await native_task(projection_db)

    async def project():
        async with projection_db.connection("md_acceptor") as conn:
            async with conn.transaction():
                return (await conn.fetchrow(PROJECT, value.frame))["created"]

    assert sorted(await asyncio.gather(project(), project())) == [False, True]
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 1


@pytest.mark.asyncio
async def test_adapter_stages_on_original_transaction_and_preserves_stricter_limits(projection_db):
    import asyncio

    observed = []

    async def hook(conn, frame):
        await conn.execute("SET LOCAL lock_timeout='80ms'; SET LOCAL statement_timeout='800ms'")
        value = await adapter_for().stage(
            conn, decode_entry(frame), deadline=asyncio.get_running_loop().time() + 5
        )
        assert conn.is_in_transaction() and value.inserted
        assert await conn.fetchval("SHOW lock_timeout") == "80ms"
        assert await conn.fetchval("SHOW statement_timeout") == "800ms"
        observed.append(value.fact)

    value = await native_task(projection_db, hook=hook)
    assert observed[0].frame == value.frame
    async with projection_db.connection("md_app") as conn:
        async with conn.transaction():
            facts = await adapter_for().page(
                conn, after_seq=0, deadline=asyncio.get_running_loop().time() + 5
            )
            assert facts == tuple(observed)
            assert conn.is_in_transaction()
        assert not conn.is_in_transaction()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_check", [None, "", "A" * 64, "a" * 63, "0" * 64])
async def test_reader_requires_exact_key_check_even_when_empty(projection_db, bad_check):
    async with projection_db.connection("md_app") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetch(PAGE, HISTORY, 0, bad_check)
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 0


@pytest.mark.asyncio
async def test_adapter_wrong_key_or_history_never_stages(projection_db):
    import asyncio
    from orchestrator.services.restore_history.model import HistoryError, Identity

    value = await native_task(projection_db)
    expected = [Identity(HISTORY, "0" * 64), Identity(uuid4(), key_check(KEY, HISTORY))]
    for identity in expected:
        async with projection_db.connection("md_acceptor") as conn:
            with pytest.raises(HistoryError):
                async with conn.transaction():
                    await adapter_for(identity).stage(
                        conn, value.entry, deadline=asyncio.get_running_loop().time() + 5
                    )
    async with projection_db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_acceptance") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    [
        "ALTER TABLE task_history_acceptance ADD COLUMN unexpected text NOT NULL DEFAULT 'x'",
        "DO $$ DECLARE c record; BEGIN FOR c IN SELECT conname FROM pg_constraint "
        "WHERE conrelid='task_history_acceptance'::regclass AND contype='u' AND conkey=ARRAY[6,13,14]::smallint[] "
        "LOOP EXECUTE format('ALTER TABLE task_history_acceptance DROP CONSTRAINT %I',c.conname); END LOOP; END $$",
    ],
)
async def test_projection_unique_constraint_and_columns_drift(projection_db, drift):
    import asyncio
    from orchestrator.services.restore_history.model import HistoryError

    adapter = adapter_for()
    async with projection_db.connection("md_acceptor") as conn:
        async with conn.transaction():
            await adapter.qualify(
                conn, writing=True, deadline=asyncio.get_running_loop().time() + 5
            )
        projection_db.sql(drift)
        with pytest.raises(HistoryError):
            async with conn.transaction():
                await adapter.qualify(
                    conn, writing=True, deadline=asyncio.get_running_loop().time() + 5
                )


@pytest.mark.asyncio
async def test_reader_wrong_history_and_no_two_argument_fallback(projection_db):
    async with projection_db.connection("md_app") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetch(PAGE, uuid4(), 0, key_check(KEY, HISTORY))
        with pytest.raises(asyncpg.UndefinedFunctionError):
            await conn.fetch(
                "SELECT * FROM task_history_acceptance_page($1::uuid,$2::bigint)", HISTORY, 0
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("projection_db", [False], indirect=True)
async def test_adapter_does_not_learn_expected_key_from_database(projection_db):
    import asyncio
    from orchestrator.services.restore_history.model import HistoryError

    async with projection_db.connection() as conn:
        await conn.execute(
            "INSERT INTO task_history_binding(history_id,version,key_id,key_check,request_schema) "
            "VALUES($1,1,1,$2,'chat-canonical-3715-v1')",
            HISTORY,
            "0" * 64,
        )
    async with projection_db.connection("md_app") as conn:
        with pytest.raises(HistoryError):
            async with conn.transaction():
                await adapter_for().page(
                    conn, after_seq=0, deadline=asyncio.get_running_loop().time() + 5
                )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,drift,writing",
    [
        ("md_acceptor", "GRANT SELECT ON compute_scope_metadata TO md_acceptor", True),
        (
            "md_acceptor",
            "GRANT INSERT(task_id) ON task_inference_recovery TO md_acceptor WITH GRANT OPTION",
            True,
        ),
        ("md_app", "GRANT SELECT ON compute_scope_metadata TO md_app", False),
    ],
)
async def test_sql_entrypoint_refuses_native_capability_drift(projection_db, role, drift, writing):
    value = await native_task(projection_db)
    projection_db.sql(drift)
    async with projection_db.connection(role) as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            if writing:
                await conn.fetchrow(PROJECT, value.frame)
            else:
                await conn.fetch(PAGE, HISTORY, 0, key_check(KEY, HISTORY))


@pytest.mark.asyncio
@pytest.mark.parametrize("parent,writing", [("md_acceptor", True), ("md_app", False)])
async def test_sql_entrypoint_rejects_inherited_execute_for_another_login(
    projection_db, parent, writing
):
    value = await native_task(projection_db)
    cluster = projection_db.cluster
    cluster.sql(f"GRANT {parent} TO md_third")
    try:
        async with projection_db.connection("md_third") as conn:
            with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                if writing:
                    await conn.fetchrow(PROJECT, value.frame)
                else:
                    await conn.fetch(PAGE, HISTORY, 0, key_check(KEY, HISTORY))
    finally:
        cluster.sql(f"REVOKE {parent} FROM md_third")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    [
        "GRANT EXECUTE ON FUNCTION task_history_acceptance_project(bytea) TO md_third",
        "GRANT EXECUTE ON FUNCTION task_history_decode_acceptance(bytea) TO md_third",
        "GRANT SELECT ON task_history_binding TO md_third",
    ],
)
async def test_sql_entrypoint_rejects_global_projection_acl_drift(projection_db, drift):
    value = await native_task(projection_db)
    projection_db.sql(drift)
    async with projection_db.connection("md_acceptor") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetchrow(PROJECT, value.frame)


@pytest.mark.asyncio
@pytest.mark.parametrize("parent,writing", [("md_acceptor", True), ("md_app", False)])
async def test_temporary_catalog_cannot_impersonate_service_login(projection_db, parent, writing):
    value = await native_task(projection_db)
    cluster = projection_db.cluster
    cluster.sql(f"GRANT {parent} TO md_third")
    try:
        async with projection_db.connection("md_third") as conn:
            await conn.execute(
                "CREATE TEMP TABLE pg_roles AS SELECT oid,rolname,rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_catalog.pg_roles"
            )
            await conn.execute("GRANT SELECT ON pg_temp.pg_roles TO md_owner")
            await conn.execute("DELETE FROM pg_temp.pg_roles WHERE rolname=$1", parent)
            await conn.execute(
                "UPDATE pg_temp.pg_roles SET oid=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=$1) WHERE rolname=SESSION_USER",
                parent,
            )
            with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                if writing:
                    await conn.fetchrow(PROJECT, value.frame)
                else:
                    await conn.fetch(PAGE, HISTORY, 0, key_check(KEY, HISTORY))
    finally:
        cluster.sql(f"REVOKE {parent} FROM md_third")


@pytest.mark.asyncio
async def test_temporary_type_cannot_execute_inside_definer(projection_db):
    value = await native_task(projection_db)
    async with projection_db.connection("md_acceptor") as conn:
        await conn.execute("CREATE TEMP TABLE shadow_marker(id int)")
        await conn.execute(
            "CREATE FUNCTION pg_temp.shadow_probe(text) RETURNS boolean LANGUAGE plpgsql AS $$ BEGIN IF CURRENT_USER='md_owner' THEN RAISE EXCEPTION 'privileged_shadow_callback'; END IF; RETURN true; END $$"
        )
        await conn.execute("CREATE DOMAIN pg_temp.name AS text CHECK(pg_temp.shadow_probe(VALUE))")
        assert (await conn.fetchrow(PROJECT, value.frame))["created"] is True
