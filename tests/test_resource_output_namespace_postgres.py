"""P complete inventory on owned PostgreSQL/POSIX, never application storage."""

import asyncio
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.namespace_inventory import namespace_states, read_output_inventory
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.mixed_store import MixedJournal
from tests import test_resource_output_catalog_postgres as c
from tests.compute_metadata_support import ROOT, metadata_cluster as metadata_cluster
from tests.deletion_journal_support import request

lifecycle_db = c.lifecycle_db
db = c.db
reservation_scenario = c.reservation_scenario
reservation_key = c.reservation_key
resource_scenario = c.resource_scenario
resource_key = c.resource_key
input_scenario = c.input_scenario
catalog_scenario = c.catalog_scenario
output = c.output
SQL = ROOT / "migrations/060_resource_output_namespace_inventory.sql"
SIGNATURE = "public.resource_output_namespace_inventory(pg_catalog.uuid,pg_catalog.int4)"


@pytest_asyncio.fixture
async def inventory(output):
    database, *_ = output
    database.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    database.sql("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO catalog_p")
    yield output


def verified(store):
    owner = store._namespaces
    with MixedJournal(owner._journal_path, owner._journal_id) as journal:
        return journal.verify()


async def read(output, *, limit=10):
    _, conn, _, _, store, *_ = output
    return await read_output_inventory(conn, store._namespaces._journal_id, record_limit=limit)


@pytest.mark.asyncio
async def test_global_complete_inventory_without_table_or_native_authority(inventory):
    database, conn, _, _, store, *_ = inventory
    first = await read(inventory)
    assert len(first.markers) == len(first.namespaces) == 1
    async with database.connection() as owner:
        other = await database.account(owner)
    await store._namespaces.ensure_namespace(CapturedGeneration(other, 0))
    complete = await read(inventory)
    assert len(complete.markers) == len(complete.namespaces) == 2
    assert {r.account_id for r in complete.namespaces} == {first.namespaces[0].account_id, other}
    states = namespace_states(complete.journal_id, verified(store), complete)
    assert len(states) == 2 and set(states.values()) == {"ready"}
    # Bounded endpoint never silently returns the first account as a complete view.
    with pytest.raises(asyncpg.PostgresError):
        await read(inventory, limit=1)
    async with conn.transaction(readonly=True):
        assert await read(inventory) == complete
    for statement in (
        "SELECT count(*) FROM artifact_namespaces",
        "SELECT count(*) FROM deletion_journal_applied",
        "SELECT count(*) FROM deletion_journal_binding",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval(statement)
    assert not await conn.fetchval(
        "SELECT has_table_privilege(current_user,'artifact_namespaces','INSERT,UPDATE,DELETE')"
    )
    assert not await conn.fetchval(
        "SELECT has_function_privilege(current_user,'public.resource_output_namespace_principal()','EXECUTE')"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["row", "marker", "digest", "foreign_account"])
async def test_restored_or_conflicting_projection_refuses_without_repair(inventory, damage):
    database, _, _, _, store, *_ = inventory
    if damage == "row":
        database.sql("DELETE FROM artifact_namespaces")
    elif damage == "marker":
        database.sql("DELETE FROM deletion_journal_applied")
    elif damage == "digest":
        database.sql("UPDATE artifact_namespaces SET birth_digest=decode(repeat('00',32),'hex')")
    else:
        database.sql("UPDATE deletion_journal_applied SET account_id=gen_random_uuid()")
    before = database.sql("SELECT count(*) FROM artifact_namespaces")
    observed = await read(inventory)
    with pytest.raises(JournalError, match="marker_mismatch"):
        namespace_states(observed.journal_id, verified(store), observed)
    assert database.sql("SELECT count(*) FROM artifact_namespaces") == before


@pytest.mark.asyncio
async def test_unapplied_birth_and_lifecycle_history_not_hidden(inventory):
    database, _, _, _, store, *_ = inventory
    view = verified(store)
    database.sql("DELETE FROM artifact_namespaces; DELETE FROM deletion_journal_applied")
    observed = await read(inventory)
    assert namespace_states(observed.journal_id, view, observed) == {}
    # No adoption/backfill; native caller must separately require its ready row.
    assert database.sql("SELECT count(*) FROM artifact_namespaces") == "0"
    owner = store._namespaces
    with MixedJournal(owner._journal_path, owner._journal_id) as journal:
        journal.append(request(view.entries[0].command.account_id), uuid4(), 1, None)
        lifecycle_view = journal.verify()
    with pytest.raises(JournalError, match="recovery_required"):
        namespace_states(observed.journal_id, lifecycle_view, observed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "second_grantee",
        "grant_option",
        "helper_grant",
        "table_grant",
        "column_grant",
        "owner_member",
        "temp",
        "repeatable_read",
        "inherited",
    ],
)
async def test_dirty_or_indirect_principal_refused(inventory, change):
    database, conn, _, _, store, *_ = inventory
    if change == "second_grantee":
        database.sql("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO md_third")
    elif change == "grant_option":
        database.sql("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO catalog_p WITH GRANT OPTION")
    elif change == "helper_grant":
        database.sql(
            "GRANT EXECUTE ON FUNCTION public.resource_output_namespace_principal() TO catalog_p"
        )
    elif change == "table_grant":
        database.sql("GRANT SELECT ON artifact_namespaces TO catalog_p")
    elif change == "column_grant":
        database.sql("GRANT SELECT(namespace) ON artifact_namespaces TO catalog_p")
    elif change == "owner_member":
        database.cluster.sql("GRANT md_owner TO catalog_p")
    elif change == "temp":
        database.cluster.sql('GRANT TEMP ON DATABASE "' + database.name + '" TO catalog_p')
        await conn.execute("CREATE TEMP TABLE inventory_shadow(x integer)")
    try:
        if change == "repeatable_read":
            async with conn.transaction(isolation="repeatable_read"):
                with pytest.raises(asyncpg.PostgresError):
                    await read(inventory)
        elif change == "inherited":
            async with database.connection("catalog_inheritor") as inherited:
                with pytest.raises(asyncpg.PostgresError):
                    await read_output_inventory(
                        inherited, store._namespaces._journal_id, record_limit=10
                    )
        else:
            with pytest.raises(asyncpg.PostgresError):
                await read(inventory)
    finally:
        if change == "owner_member":
            database.cluster.sql("REVOKE md_owner FROM catalog_p")
    if change == "owner_member":
        assert len((await read(inventory)).namespaces) == 1


@pytest.mark.asyncio
async def test_wrong_binding_empty_binding_and_invalid_sql_bounds(inventory):
    database, conn, _, _, store, *_ = inventory
    jid = store._namespaces._journal_id
    for identity, cap in ((uuid4(), 1), (None, 1), (jid, None), (jid, 0), (jid, 10001)):
        with pytest.raises(asyncpg.PostgresError):
            await conn.fetchval(
                "SELECT public.resource_output_namespace_inventory($1,$2)", identity, cap
            )
    database.sql(
        "DELETE FROM artifact_namespaces; DELETE FROM deletion_journal_applied; DELETE FROM deletion_journal_binding"
    )
    with pytest.raises(asyncpg.PostgresError):
        await read(inventory)


@pytest.mark.asyncio
async def test_statement_snapshot_is_observation_not_later_write_fence(inventory):
    database, _, _, _, store, *_ = inventory
    async with database.connection() as owner:
        transaction = owner.transaction()
        await transaction.start()
        await owner.execute("UPDATE artifact_namespaces SET state='uncertain'")
        before = await read(inventory)
        assert before.namespaces[0].state == "ready"
        await transaction.commit()
        after = await read(inventory)
        assert after.namespaces[0].state == "uncertain"
    assert namespace_states(after.journal_id, verified(store), after) == {
        after.namespaces[0].namespace: "uncertain"
    }
    assert before != after  # Prior receipt never locks or authorizes future effects.


@pytest.mark.asyncio
async def test_cancelled_native_read_cannot_return_late_snapshot(inventory):
    database, conn, _, _, store, *_ = inventory
    async with database.connection() as owner:
        async with owner.transaction():
            await owner.execute("LOCK TABLE artifact_namespaces IN ACCESS EXCLUSIVE MODE")
            pending = asyncio.create_task(read(inventory))
            try:
                async with asyncio.timeout(3):
                    while not await owner.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted)"
                    ):
                        await asyncio.sleep(0.005)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    # Observation adapter retries nothing and performs no mutation. Future owned
    # caller, not this supplied-connection helper, must drain actual pool release.
    assert not conn.is_in_transaction()
    assert len((await read(inventory)).namespaces) == 1


@pytest.mark.asyncio
async def test_unsafe_default_acl_migration_is_atomic(output):
    database, *_ = output
    database.sql(
        "ALTER DEFAULT PRIVILEGES FOR ROLE md_owner GRANT EXECUTE ON FUNCTIONS TO md_third"
    )
    error = database.sql("BEGIN;" + SQL.read_text() + "COMMIT;", success=False)
    assert "resource_output_unsafe_default_privileges" in error
    assert (
        database.sql(
            "SELECT count(*) FROM pg_proc WHERE proname LIKE 'resource_output_namespace_%'"
        )
        == "0"
    )


@pytest.mark.asyncio
async def test_native_inventory_table_lock_timeout_is_refusal(inventory):
    database, *_ = inventory
    async with database.connection() as owner, owner.transaction():
        await owner.execute("LOCK TABLE artifact_namespaces IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(asyncpg.LockNotAvailableError):
            await read(inventory)
    assert len((await read(inventory)).namespaces) == 1
