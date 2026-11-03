"""Restricted catalog P on owned real PostgreSQL/POSIX; no runtime composition."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from tests import test_resource_output_presentations_postgres as p
from tests.compute_metadata_support import ROOT, metadata_cluster as metadata_cluster

lifecycle_db = p.lifecycle_db
db = p.db
reservation_scenario = p.reservation_scenario
reservation_key = p.reservation_key
resource_scenario = p.resource_scenario
resource_key = p.resource_key
input_scenario = p.input_scenario
catalog_scenario = p.catalog_scenario
SQL = ROOT / "migrations/059_resource_output_catalog.sql"
SIGNATURES = (
    "public.resource_output_catalog_snapshot(pg_catalog.jsonb,pg_catalog.uuid)",
    "public.resource_output_catalog_admit(pg_catalog.jsonb,pg_catalog.uuid)",
    "public.resource_output_catalog_stage(pg_catalog.jsonb,pg_catalog.uuid,pg_catalog.jsonb)",
)


@pytest_asyncio.fixture
async def output(catalog_scenario):
    database, catalog, presentations, tasks, expected, key, scenario = catalog_scenario
    _, store, identity, command, *_ = scenario
    database.sql("BEGIN;" + p.SQL.read_text() + SQL.read_text() + "COMMIT;")
    database.cluster.sql(
        "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='catalog_p') THEN CREATE ROLE catalog_p LOGIN; CREATE ROLE catalog_inheritor LOGIN IN ROLE catalog_p; END IF; END $$;"
    )
    database.sql(
        ";".join(
            "GRANT EXECUTE ON FUNCTION " + s + " TO catalog_p" for s in (*p.SIGNATURES, *SIGNATURES)
        )
    )
    database.bridge.commit_fault_role = "catalog_p"
    async with database.connection("catalog_p") as conn:
        yield database, conn, identity, command, store, catalog, presentations, expected


async def snapshot(conn, identity, intent, *, admit=False):
    function = "admit" if admit else "snapshot"
    return json.loads(
        await conn.fetchval(
            f"SELECT public.resource_output_catalog_{function}($1::pg_catalog.jsonb,$2)",
            identity.wire(),
            intent,
        )
    )


def packet(store, command, observed):
    stored = store._stored(command, observed["namespace"]["namespace"])
    return {
        "reference_id": str(uuid4()),
        "metadata_ciphertext": store._seal(
            store._metadata(command, store._descriptors.decode(command))
        ),
        "binding_ciphertext": store._seal(store._binding_fields(stored, observed["source_kind"])),
        "provenance_ciphertext": store._seal(
            store._provenance(
                command,
                observed["task"]["conversation_id"],
                observed["task"]["result_message_id"],
                observed["next_output_ordinal"],
                False,
            )
        ),
    }


async def stage(conn, identity, intent, value):
    return json.loads(
        await conn.fetchval(
            "SELECT public.resource_output_catalog_stage($1::pg_catalog.jsonb,$2,$3::pg_catalog.jsonb)",
            identity.wire(),
            intent,
            json.dumps(value),
        )
    )


@pytest.mark.asyncio
async def test_owned_catalog_commit_native_read_and_expired_exact_replay(output):
    database, conn, identity, command, store, _, presentations, expected = output
    intent = command.binding.intent_id
    async with conn.transaction():
        observed = await snapshot(conn, identity, intent, admit=True)
        assert observed["publication"] is None
        assert observed["intent"]["descriptor_ciphertext"] == command.descriptor_ciphertext
        value = packet(store, command, observed)
        # Existing backend-owned authenticated storage path, not a stage receipt.
        from orchestrator.resource_storage import prepare_verified
        from orchestrator.services.deletion_journal.barrier import _io

        stored = store._stored(command, observed["namespace"]["namespace"])
        await _io(
            lambda: prepare_verified(
                store._root, store._envelope, stored, p.c.accepted.outputs.CONTENT
            )
        )
        result = await stage(conn, identity, intent, value)
        assert result["outcome"] == "pending_commit" and result["inserted"]
        assert (
            database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'")
            == "0"
        )
    read = await store.read(expected, command.binding.resource_id, command.binding.version_id)
    assert read.content == p.c.accepted.outputs.CONTENT
    pins = [
        {
            "resource_id": str(command.binding.resource_id),
            "version_id": str(command.binding.version_id),
        }
    ]
    ident = await presentations.record(identity, pins)
    assert ident is not None
    database.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    async with conn.transaction():
        assert not (await stage(conn, identity, intent, value))["inserted"]
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await snapshot(conn, identity, intent, admit=True)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "1"
    )


@pytest.mark.asyncio
async def test_rollback_keeps_all_catalog_rows_and_counter_absent(output):
    database, conn, identity, command, store, *_ = output
    before = database.sql(
        "SELECT count(*)||':'||COALESCE(max(insertion_counter),0) FROM resource_catalog_heads"
    )
    tx = conn.transaction()
    await tx.start()
    value = packet(store, command, await snapshot(conn, identity, command.binding.intent_id))
    await stage(conn, identity, command.binding.intent_id, value)
    await tx.rollback()
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )
    assert (
        database.sql(
            "SELECT count(*)||':'||COALESCE(max(insertion_counter),0) FROM resource_catalog_heads"
        )
        == before
    )
    assert database.sql("SELECT count(*) FROM artifact_output_intents") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "account",
        "task",
        "generation",
        "cancel",
        "suspend",
        "closed",
        "expired",
        "namespace",
        "missing_source",
        "intent",
    ],
)
async def test_stale_or_foreign_authority_has_no_catalog_effect(output, change):
    database, conn, identity, command, store, *_ = output
    intent = command.binding.intent_id
    value = packet(store, command, await snapshot(conn, identity, intent))
    if change == "account":
        identity = replace(identity, account_id=uuid4())
    elif change == "task":
        identity = replace(identity, task_id=uuid4())
    elif change == "generation":
        database.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    elif change == "cancel":
        database.sql("UPDATE tasks SET cancel_requested_at=clock_timestamp()")
    elif change == "suspend":
        database.sql("UPDATE entitlement_accounts SET status='suspended'")
    elif change == "closed":
        database.sql("UPDATE attempt_lifecycle_control SET closed_at=clock_timestamp(),revision=2")
    elif change == "expired":
        database.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    elif change == "namespace":
        database.sql("UPDATE artifact_namespaces SET state='uncertain'")
    elif change == "missing_source":
        database.sql(
            "UPDATE messages SET role='user' WHERE id=(SELECT result_message_id FROM tasks LIMIT 1)"
        )
    else:
        intent = uuid4()
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await stage(conn, identity, intent, value)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["extra", "null", "boolean", "long", "reference", "noncanonical"]
)
async def test_closed_packet_bounds(output, change):
    database, conn, identity, command, store, *_ = output
    value = packet(store, command, await snapshot(conn, identity, command.binding.intent_id))
    if change == "extra":
        value["extra"] = "x"
    elif change == "null":
        value["metadata_ciphertext"] = None
    elif change == "boolean":
        value["binding_ciphertext"] = True
    elif change == "long":
        value["provenance_ciphertext"] = "x" * 131073
    elif change == "reference":
        value["reference_id"] = str(uuid4()).upper()
    else:
        del value["binding_ciphertext"]
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await stage(conn, identity, command.binding.intent_id, value)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["reference_id", "metadata_ciphertext", "binding_ciphertext", "provenance_ciphertext"]
)
async def test_immutable_replay_conflicts(output, field):
    database, conn, identity, command, store, *_ = output
    value = packet(store, command, await snapshot(conn, identity, command.binding.intent_id))
    async with conn.transaction():
        await stage(conn, identity, command.binding.intent_id, value)
    changed = {**value, field: str(uuid4())}
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await stage(conn, identity, command.binding.intent_id, changed)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_app", "catalog_inheritor", "md_owner"])
async def test_native_owner_or_inherited_login_refused(output, role):
    database, _, identity, command, *_ = output
    with pytest.raises(asyncpg.PostgresError):
        async with database.connection(role) as conn:
            await snapshot(conn, identity, command.binding.intent_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grant",
    [
        "GRANT SELECT ON tasks TO catalog_p",
        "GRANT UPDATE(status) ON tasks TO catalog_p",
        "GRANT EXECUTE ON FUNCTION resource_output_catalog_scope(jsonb,uuid) TO catalog_p",
        "GRANT EXECUTE ON FUNCTION resource_output_catalog_snapshot(jsonb,uuid) TO md_app",
        "GRANT EXECUTE ON FUNCTION resource_output_catalog_admit(jsonb,uuid) TO catalog_p WITH GRANT OPTION",
    ],
)
async def test_capability_drift_refuses(output, grant):
    database, conn, identity, command, *_ = output
    database.sql(grant)
    with pytest.raises(asyncpg.PostgresError):
        await snapshot(conn, identity, command.binding.intent_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table", ["tasks", "resource_versions", "resource_catalog_heads", "artifact_output_intents"]
)
async def test_no_direct_table_authority(output, table):
    _, conn, *_ = output
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch(f"SELECT * FROM {table}")


@pytest.mark.asyncio
async def test_concurrent_identical_stage_commits_only_once(output):
    database, conn, identity, command, store, *_ = output
    intent = command.binding.intent_id
    value = packet(store, command, await snapshot(conn, identity, intent))
    async with database.connection("catalog_p") as other:

        async def second():
            async with other.transaction():
                return await stage(other, identity, intent, value)

        child = None
        try:
            async with conn.transaction():
                assert (await stage(conn, identity, intent, value))["inserted"]
                child = asyncio.create_task(second())
                await asyncio.sleep(0.03)
                assert not child.done()
            assert not (await child)["inserted"]
        finally:
            if child is not None:
                if not child.done():
                    child.cancel()
                await asyncio.gather(child, return_exceptions=True)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_lost_commit_is_not_success_or_retry_authority(output, fault):
    database, conn, identity, command, store, *_ = output
    intent = command.binding.intent_id
    value = packet(store, command, await snapshot(conn, identity, intent))
    tx = conn.transaction()
    await tx.start()
    pending = await stage(conn, identity, intent, value)
    database.bridge.commit_seen.clear()
    database.bridge.commit_fault = fault
    try:
        with pytest.raises((asyncpg.PostgresError, TimeoutError)):
            async with asyncio.timeout(10):
                await tx.commit()
    finally:
        database.bridge.commit_fault = None
        conn.terminate()
    assert database.bridge.commit_seen.is_set()
    async with database.connection("catalog_p") as fresh, fresh.transaction():
        observed = await snapshot(fresh, identity, intent)
        assert (observed["publication"] is not None) == (fault == "after")
    assert pending["outcome"] == "pending_commit"


@pytest.mark.asyncio
async def test_unsafe_defaults_rollback_catalog_payload(catalog_scenario):
    database = catalog_scenario[0]
    database.sql("BEGIN;" + p.SQL.read_text() + "COMMIT;")
    database.sql("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO md_app")
    try:
        assert "resource_output_unsafe_default_privileges" in database.sql(
            "BEGIN;" + SQL.read_text() + "COMMIT;", success=False
        )
        assert (
            database.sql(
                "SELECT count(*) FROM pg_proc WHERE proname LIKE 'resource_output_catalog_%'"
            )
            == "0"
        )
    finally:
        database.sql("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM md_app")


@pytest.mark.asyncio
async def test_append_uses_original_resource_and_next_reference_ordinal(output):
    from orchestrator.artifact_reservations import JobBinding
    from orchestrator.resource_storage import prepare_verified
    from orchestrator.services.deletion_journal.barrier import _io

    database, conn, identity, command, store, _, _, expected = output
    original = await store.publish_output(
        identity, command.binding.intent_id, p.c.accepted.outputs.CONTENT
    )
    job = JobBinding(uuid4(), identity.account_id, identity.generation, identity.task_id, uuid4())
    r0 = p.c.accepted.outputs.OutputReservations(store._descriptors)
    async with database.connection("md_app") as restricted, restricted.transaction():
        await r0.register_output_job(restricted, identity, job)
    content = b"column\nappended\n"
    intent = await store.reserve_version(
        identity, job, original.resource_id, 0, content, "next.csv"
    )
    async with database.connection() as owner:
        append_command, kind = await store._intent(owner, expected, intent)
    async with conn.transaction():
        observed = await snapshot(conn, identity, intent, admit=True)
        assert kind == observed["source_kind"] == "append"
        assert observed["next_output_ordinal"] == 1
        stored = store._stored(append_command, observed["namespace"]["namespace"])
        await _io(lambda: prepare_verified(store._root, store._envelope, stored, content))
        result = await stage(conn, identity, intent, packet(store, append_command, observed))
        assert result["inserted"]
    assert (
        await store.read(expected, original.resource_id, append_command.binding.version_id)
    ).content == content
    assert database.sql("SELECT count(*) FROM resources") == "1"
    assert (
        database.sql(
            "SELECT string_agg(ordinal::text,',' ORDER BY ordinal) FROM resource_references WHERE role='output'"
        )
        == "0,1"
    )


@pytest.mark.asyncio
async def test_cancelled_catalog_waiter_leaves_only_original_reservation(output):
    database, conn, identity, command, store, *_ = output
    intent = command.binding.intent_id
    value = packet(store, command, await snapshot(conn, identity, intent))
    async with database.connection() as owner, database.connection("postgres") as monitor:
        async with owner.transaction():
            await owner.execute(
                "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                identity.account_id,
            )

            async def waiting_stage():
                async with conn.transaction():
                    return await stage(conn, identity, intent, value)

            child = asyncio.create_task(waiting_stage())
            try:
                async with asyncio.timeout(2):
                    while not await monitor.fetchval(
                        "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                        conn.get_server_pid(),
                    ):
                        assert not child.done()
                        await asyncio.sleep(0.01)
                child.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await child
            finally:
                if not child.done():
                    child.cancel()
                await asyncio.gather(child, return_exceptions=True)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )
    assert database.sql("SELECT count(*) FROM artifact_output_intents") == "1"


@pytest.mark.asyncio
async def test_lock_wait_timeout_does_not_become_catalog_success(output):
    database, conn, identity, command, *_ = output
    async with database.connection() as owner, owner.transaction():
        await owner.execute(
            "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", identity.account_id
        )
        with pytest.raises(asyncpg.LockNotAvailableError):
            async with conn.transaction():
                await snapshot(conn, identity, command.binding.intent_id, admit=True)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )


@pytest.mark.asyncio
async def test_catalog_insert_lock_timeout_rolls_back_earlier_inserts(output):
    database, conn, identity, command, store, *_ = output
    intent = command.binding.intent_id
    value = packet(store, command, await snapshot(conn, identity, intent))
    database.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()+interval '1 second'")
    async with database.connection() as owner, database.connection("postgres") as monitor:
        tx = owner.transaction()
        await tx.start()
        await owner.execute("LOCK TABLE resource_publications IN SHARE MODE")
        child = None

        async def delayed_stage():
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout='2s'")
                # Function-local 250ms prevents an unbounded lock wait; lease
                # expiry can also happen below that ceiling after admission.
                return await stage(conn, identity, intent, value)

        try:
            child = asyncio.create_task(delayed_stage())
            async with asyncio.timeout(2):
                while not await monitor.fetchval(
                    "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                    conn.get_server_pid(),
                ):
                    assert not child.done()
                    await asyncio.sleep(0.01)
            # Retain the table lock until the unchanged function lock deadline.
            with pytest.raises(asyncpg.LockNotAvailableError):
                await child
        finally:
            await tx.rollback()
            if child is not None:
                if not child.done():
                    child.cancel()
                await asyncio.gather(child, return_exceptions=True)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )


@pytest.mark.asyncio
async def test_temp_namespace_refused_before_privileged_old_helpers(output):
    database, conn, identity, command, *_ = output
    await conn.execute("CREATE DOMAIN pg_temp.uuid AS text; CREATE TEMP TABLE pg_roles(oid oid)")
    with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
        await snapshot(conn, identity, command.binding.intent_id)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability", ["artifact_output_register", "task_history_acceptance_stage"]
)
async def test_p_cannot_acquire_reservation_or_history_capability(output, capability):
    database, conn, identity, command, *_ = output
    # These are synthetic fixture grants only. The production payload grants none.
    async with database.connection() as owner:
        if capability.startswith("task_history_"):
            # The resource fixture is intentionally based on 048..055, so use a
            # synthetic canary with the protected prefix; no H protocol altered.
            await owner.execute(
                "CREATE FUNCTION public.task_history_acceptance_stage() RETURNS void LANGUAGE sql AS $$ SELECT $$"
            )
        signatures = await owner.fetch(
            "SELECT oid::regprocedure::text AS signature FROM pg_proc WHERE proname=$1", capability
        )
        assert signatures
        for row in signatures:
            await owner.execute(f"GRANT EXECUTE ON FUNCTION {row['signature']} TO catalog_p")
    with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
        await snapshot(conn, identity, command.binding.intent_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "cancel", "generation"])
async def test_final_native_check_rejects_post_insert_fault_and_rolls_back(output, change):
    database, conn, identity, command, store, *_ = output
    intent = command.binding.intent_id
    value = packet(store, command, await snapshot(conn, identity, intent))
    statement = {
        "expired": "UPDATE public.tasks SET lease_expires_at=clock_timestamp()-interval '1 second'",
        "cancel": "UPDATE public.tasks SET cancel_requested_at=clock_timestamp()",
        "generation": "UPDATE public.entitlement_accounts SET content_generation=content_generation+1",
    }[change]
    # Deterministic synthetic fault exactly after native admission and INSERT,
    # before the final lifecycle assertion; no sleeps or modified production SQL.
    database.sql(
        "CREATE FUNCTION public.fixture_catalog_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        + statement
        + "; RETURN NEW; END $$; CREATE TRIGGER fixture_catalog_fault AFTER INSERT ON public.resource_references FOR EACH ROW EXECUTE FUNCTION public.fixture_catalog_fault()"
    )
    before = database.sql(
        "SELECT count(*)||':'||COALESCE(max(insertion_counter),0) FROM resource_catalog_heads"
    )
    with pytest.raises(asyncpg.PostgresError, match="lifecycle_refused"):
        async with conn.transaction():
            await stage(conn, identity, intent, value)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )
    assert (
        database.sql(
            "SELECT count(*)||':'||COALESCE(max(insertion_counter),0) FROM resource_catalog_heads"
        )
        == before
    )


@pytest.mark.asyncio
async def test_stop_serializing_ahead_of_stage_prevents_publication(output):
    database, conn, identity, command, store, *_ = output
    intent = command.binding.intent_id
    value = packet(store, command, await snapshot(conn, identity, intent))
    async with database.connection() as owner, database.connection("postgres") as monitor:
        tx = owner.transaction()
        await tx.start()
        await owner.execute(
            "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", identity.account_id
        )

        async def waiting():
            async with conn.transaction():
                return await stage(conn, identity, intent, value)

        child = asyncio.create_task(waiting())
        committed = False
        try:
            async with asyncio.timeout(2):
                while not await monitor.fetchval(
                    "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                    conn.get_server_pid(),
                ):
                    assert not child.done()
                    await asyncio.sleep(0.01)
            await owner.execute(
                "UPDATE tasks SET cancel_requested_at=clock_timestamp() WHERE id=$1",
                identity.task_id,
            )
            await tx.commit()
            committed = True
            with pytest.raises(asyncpg.PostgresError, match="lifecycle_refused"):
                await child
        finally:
            if not committed:
                await tx.rollback()
            if not child.done():
                child.cancel()
            await asyncio.gather(child, return_exceptions=True)
    assert (
        database.sql("SELECT count(*) FROM resource_publications WHERE source_kind='output'") == "0"
    )
