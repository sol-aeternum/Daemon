"""Restricted P authority on owned SQL/POSIX fixtures; no application database."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from tests import test_resource_catalog_postgres as c
from tests.compute_metadata_support import ROOT, metadata_cluster as metadata_cluster

lifecycle_db = c.lifecycle_db
db = c.db
reservation_scenario = c.reservation_scenario
reservation_key = c.reservation_key
resource_scenario = c.resource_scenario
resource_key = c.resource_key
input_scenario = c.input_scenario
catalog_scenario = c.catalog_scenario
SQL = ROOT / "migrations/058_resource_output_presentations.sql"
SIGNATURES = (
    "public.resource_output_presentation_snapshot(pg_catalog.jsonb)",
    "public.resource_output_presentation_insert(pg_catalog.jsonb,pg_catalog.uuid,pg_catalog.jsonb,pg_catalog.text,pg_catalog.timestamptz,pg_catalog.int4)",
)


@pytest_asyncio.fixture
async def output(catalog_scenario):
    database, catalog, presentations, tasks, expected, key, scenario = catalog_scenario
    _, store, identity, command, *_ = scenario
    publication = await store.publish_output(
        identity, command.binding.intent_id, c.accepted.outputs.CONTENT
    )
    database.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    # Roles belong solely to the already-owned synthetic cluster. Never an app DSN.
    database.cluster.sql(
        "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='output_p') THEN CREATE ROLE output_p LOGIN; CREATE ROLE output_inheritor LOGIN IN ROLE output_p; END IF; END $$;"
    )
    database.sql(";".join("GRANT EXECUTE ON FUNCTION " + s + " TO output_p" for s in SIGNATURES))
    pins = [
        {"resource_id": str(publication.resource_id), "version_id": str(publication.version_id)}
    ]
    database.bridge.commit_fault_role = "output_p"
    async with database.connection("output_p") as conn:
        yield database, conn, identity, pins, catalog, presentations, tasks, expected, key


async def snapshot(conn, identity):
    return json.loads(
        await conn.fetchval(
            "SELECT public.resource_output_presentation_snapshot($1::pg_catalog.jsonb)",
            identity.wire(),
        )
    )


def packet(store, identity, observed, pins):
    ident = uuid4()
    created = datetime.fromisoformat(observed["observed_at"])
    token = store._seal(
        {
            "schema_version": 1,
            "account_id": str(identity.account_id),
            "generation": identity.generation,
            "original_task_id": str(identity.task_id),
            "original_message_id": observed["task"]["result_message_id"],
            "source_deleted": False,
            "envelope": {
                "schema_version": 1,
                "component_library_version": 1,
                "snapshot_at": created.isoformat(),
                "component": "file_results",
                "items": pins,
            },
        }
    )
    return ident, created, token


async def insert(conn, identity, pins, command, limit=20):
    ident, created, token = command
    return json.loads(
        await conn.fetchval(
            "SELECT public.resource_output_presentation_insert($1::pg_catalog.jsonb,$2,$3::pg_catalog.jsonb,$4,$5,$6)",
            identity.wire(),
            ident,
            json.dumps(pins),
            token,
            created,
            limit,
        )
    )


@pytest.mark.asyncio
async def test_p_known_commit_native_reads_and_exact_replay(output):
    database, conn, identity, pins, catalog, presentations, *_ = output
    async with conn.transaction():
        observed = await snapshot(conn, identity)
        assert observed["records"] == []
        assert UUID(observed["task"]["id"]) == identity.task_id
        command = packet(catalog.store, identity, observed, pins)
        result = await insert(conn, identity, pins, command)
        assert result == {
            "presentation_id": str(command[0]),
            "inserted": True,
            "outcome": "pending_commit",
        }
        assert database.sql("SELECT count(*) FROM resource_presentations") == "0"
    assert (await presentations.page(output[7], identity.task_id))["items"][0]["items"] == pins
    database.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    async with conn.transaction():
        assert not (await insert(conn, identity, pins, command))["inserted"]
    assert database.sql("SELECT count(*) FROM resource_presentations") == "1"


@pytest.mark.asyncio
async def test_rollback_and_no_new_presentation_after_expiry(output):
    database, conn, identity, pins, catalog, *_ = output
    tx = conn.transaction()
    await tx.start()
    command = packet(catalog.store, identity, await snapshot(conn, identity), pins)
    await insert(conn, identity, pins, command)
    await tx.rollback()
    assert database.sql("SELECT count(*) FROM resource_presentations") == "0"
    database.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await insert(conn, identity, pins, command)
    assert database.sql("SELECT count(*) FROM resource_presentations") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["foreign_account", "foreign_task", "generation", "cancel", "suspend", "closed"]
)
async def test_current_authority_refuses_before_insertion(output, change):
    database, conn, identity, pins, catalog, *_ = output
    command = packet(catalog.store, identity, await snapshot(conn, identity), pins)
    if change == "foreign_account":
        identity = replace(identity, account_id=uuid4())
    elif change == "foreign_task":
        identity = replace(identity, task_id=uuid4())
    elif change == "generation":
        database.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    elif change == "cancel":
        database.sql("UPDATE tasks SET cancel_requested_at=clock_timestamp()")
    elif change == "suspend":
        database.sql("UPDATE entitlement_accounts SET status='suspended'")
    else:
        database.sql("UPDATE attempt_lifecycle_control SET closed_at=clock_timestamp(),revision=2")
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await insert(conn, identity, pins, command)
    assert database.sql("SELECT count(*) FROM resource_presentations") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["extra_key", "foreign_pin", "duplicate_pin", "wrong_generation", "wrong_message"]
)
async def test_exact_owned_pins_required(output, change):
    database, conn, identity, pins, catalog, *_ = output
    command = packet(catalog.store, identity, await snapshot(conn, identity), pins)
    if change == "extra_key":
        pins = [{**pins[0], "unexpected": "value"}]
    elif change == "foreign_pin":
        pins = [{**pins[0], "version_id": str(uuid4())}]
    elif change == "duplicate_pin":
        pins = pins * 2
    elif change == "wrong_generation":
        database.sql("UPDATE resource_references SET generation=generation+1", success=False)
        # FK prevents this corruption; a supplied foreign generation must still refuse.
        identity = replace(identity, generation=identity.generation + 1)
    else:
        database.sql("UPDATE resource_references SET message_id=NULL")
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await insert(conn, identity, pins, command)
    assert database.sql("SELECT count(*) FROM resource_presentations") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["resource_presentations", "resource_references", "tasks", "attempt_lifecycle_control"],
)
async def test_p_has_no_direct_table_privileges(output, target):
    _, conn, *_ = output
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch(
            {
                "resource_presentations": "SELECT * FROM resource_presentations",
                "resource_references": "SELECT * FROM resource_references",
                "tasks": "SELECT * FROM tasks",
                "attempt_lifecycle_control": "SELECT * FROM attempt_lifecycle_control",
            }[target]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_app", "output_inheritor", "md_owner"])
async def test_no_inherited_or_native_principal_admission(output, role):
    database, _, identity, *_ = output
    with pytest.raises(asyncpg.PostgresError):
        async with database.connection(role) as conn:
            await snapshot(conn, identity)


@pytest.mark.asyncio
async def test_privilege_drift_fails_closed(output):
    database, conn, identity, *_ = output
    database.sql("GRANT SELECT ON resource_presentations TO output_p")
    with pytest.raises(asyncpg.PostgresError):
        await snapshot(conn, identity)


@pytest.mark.asyncio
async def test_sequence_mutation_privilege_is_not_read_only(output):
    database, conn, identity, *_ = output
    database.sql(
        "CREATE SEQUENCE output_test_sequence; GRANT USAGE ON SEQUENCE output_test_sequence TO output_p"
    )
    with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
        await snapshot(conn, identity)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["extra_grantee", "grant_option", "helper", "set_role"])
async def test_entrypoint_and_helper_acl_drift_refuses(output, drift):
    database, conn, identity, *_ = output
    if drift == "extra_grantee":
        database.sql("GRANT EXECUTE ON FUNCTION " + SIGNATURES[0] + " TO md_app")
    elif drift == "grant_option":
        database.sql(
            "GRANT EXECUTE ON FUNCTION " + SIGNATURES[0] + " TO output_p WITH GRANT OPTION"
        )
    elif drift == "helper":
        database.sql(
            "GRANT EXECUTE ON FUNCTION public.resource_output_presentation_scope(jsonb) TO md_app"
        )
    else:
        await conn.execute("SET ROLE output_p")
    with pytest.raises(asyncpg.PostgresError):
        await snapshot(conn, identity)


@pytest.mark.asyncio
async def test_immutable_conflict_and_limit_preserve_original(output):
    database, conn, identity, pins, catalog, *_ = output
    command = packet(catalog.store, identity, await snapshot(conn, identity), pins)
    async with conn.transaction():
        await insert(conn, identity, pins, command, limit=1)
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await insert(conn, identity, pins, (command[0], command[1], "different"))
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await insert(conn, identity, pins, (uuid4(), command[1], command[2]), limit=1)
    async with conn.transaction():
        assert not (await insert(conn, identity, pins, command, limit=1))["inserted"]
    assert database.sql("SELECT count(*) FROM resource_presentations") == "1"


@pytest.mark.asyncio
async def test_concurrent_exact_operation_serializes(output):
    database, conn, identity, pins, catalog, *_ = output
    command = packet(catalog.store, identity, await snapshot(conn, identity), pins)
    async with database.connection("output_p") as other:

        async def second():
            async with other.transaction():
                return await insert(other, identity, pins, command)

        child = None
        try:
            async with conn.transaction():
                assert (await insert(conn, identity, pins, command))["inserted"]
                child = asyncio.create_task(second())
                await asyncio.sleep(0.03)
                assert not child.done()
            assert not (await child)["inserted"]
        finally:
            if child is not None and not child.done():
                child.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await child
    assert database.sql("SELECT count(*) FROM resource_presentations") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_unknown_commit_remains_unknown_and_snapshot_is_observation(output, fault):
    database, conn, identity, pins, catalog, *_ = output
    command = packet(catalog.store, identity, await snapshot(conn, identity), pins)
    transaction = conn.transaction()
    await transaction.start()
    pending = await insert(conn, identity, pins, command)
    assert pending["outcome"] == "pending_commit"
    database.bridge.commit_fault_role = "output_p"
    database.bridge.commit_seen.clear()
    database.bridge.commit_fault = fault
    try:
        with pytest.raises((asyncpg.PostgresError, TimeoutError)):
            async with asyncio.timeout(10):
                await transaction.commit()
    finally:
        database.bridge.commit_fault = None
        conn.terminate()
    assert database.bridge.commit_seen.is_set()
    if fault == "after":
        assert database.bridge.discarded_commit_bytes > 0
    async with database.connection("output_p") as fresh, fresh.transaction():
        observed = await snapshot(fresh, identity)
        assert len(observed["records"]) == (1 if fault == "after" else 0)
    assert pending["outcome"] == "pending_commit"  # No late-result/readback promotion.


@pytest.mark.asyncio
async def test_unsafe_default_acl_rolls_back_whole_payload(catalog_scenario):
    database = catalog_scenario[0]
    database.sql("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO md_app")
    try:
        assert "resource_output_unsafe_default_privileges" in database.sql(
            "BEGIN;" + SQL.read_text() + "COMMIT;", success=False
        )
        assert (
            database.sql(
                "SELECT count(*) FROM pg_proc WHERE proname LIKE 'resource_output_presentation_%'"
            )
            == "0"
        )
    finally:
        database.sql("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM md_app")


@pytest.mark.asyncio
async def test_temporary_objects_cannot_redirect_privileged_scope(output):
    database, conn, identity, *_ = output
    await conn.execute(
        "CREATE TEMP TABLE resource_presentations(secret text); CREATE DOMAIN pg_temp.uuid AS text; CREATE DOMAIN pg_temp.jsonb AS text; CREATE TEMP TABLE pg_proc(oid oid); CREATE TEMP TABLE pg_roles(oid oid);"
    )
    with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
        await snapshot(conn, identity)
    assert database.sql("SELECT count(*) FROM resource_presentations") == "0"


@pytest.mark.asyncio
async def test_waiter_cancel_does_not_change_presentation(output):
    database, conn, identity, pins, catalog, *_ = output
    command = packet(catalog.store, identity, await snapshot(conn, identity), pins)
    # PostgreSQL hides another login's activity from the ordinary owner role.
    # Observe only the exact owned fixture PID with its synthetic administrator;
    # no monitoring capability is added to P or any application principal.
    async with database.connection() as owner, database.connection("postgres") as monitor:
        async with owner.transaction():
            await owner.execute(
                "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                identity.account_id,
            )

            async def stage_owned_transaction():
                async with conn.transaction():
                    return await insert(conn, identity, pins, command)

            child = asyncio.create_task(stage_owned_transaction())
            try:
                async with asyncio.timeout(2):
                    while not await monitor.fetchval(
                        "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                        conn.get_server_pid(),
                    ):
                        assert not child.done()
                        await asyncio.sleep(0.01)
                assert not child.done()
                child.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await child
            finally:
                if not child.done():
                    child.cancel()
                await asyncio.gather(child, return_exceptions=True)
    assert database.sql("SELECT count(*) FROM resource_presentations") == "0"
