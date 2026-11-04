"""Actual restricted P/SQL/POSIX presentation phase; no installed runtime host."""

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from uuid import uuid4

import asyncpg
from asyncpg import connect_utils
import pytest
import pytest_asyncio

from orchestrator import resource_store as registry
from orchestrator.artifact_reservations import OutputReservations
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.account_control.guard import CapturedGeneration
from tests import test_reserved_output_postgres as r
from tests.history_cleanup_support import held_reset
from tests.test_history_coordination import available

metadata_cluster = r.metadata_cluster
db = r.db
projection_db = r.projection_db
scenario = r.scenario
effects = r.effects
reserved = r.reserved
ROOT = Path(__file__).resolve().parents[1]
SIGNATURE = "public.resource_output_presentation_references(pg_catalog.jsonb,pg_catalog.jsonb)"


@pytest_asyncio.fixture
async def presenter(reserved):
    s = reserved
    s.db.sql(
        "BEGIN;"
        + (ROOT / "migrations/061_resource_output_presentation_references.sql").read_text()
        + "COMMIT;"
    )
    s.db.sql("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO reserved_p")
    s.publisher = registry.ReservedOutputPublisher(
        s.publisher._pool,
        s.root,
        s.journal,
        s.journal_id,
        s.tasks._enc._cipher,
        inventory_limit=10,
        snapshot_byte_limit=400000,
        presentation_item_limit=10,
        presentation_record_limit=10,
    )
    yield s


async def present(s, intent=None):
    return await s.effects.present_reserved_output(
        s.publisher, s.attempt, intent or s.command.binding.intent_id, deadline=r.deadline()
    )


def count(s):
    return s.db.sql("SELECT count(*) FROM resource_presentations")


def pins(s):
    return [
        {
            "resource_id": str(s.command.binding.resource_id),
            "version_id": str(s.command.binding.version_id),
        }
    ]


async def raw_refs(conn, identity, items):
    return json.loads(
        await conn.fetchval(
            "SELECT public.resource_output_presentation_references($1::pg_catalog.jsonb,$2::pg_catalog.jsonb)",
            identity.wire(),
            json.dumps(items),
        )
    )


async def second_output(s):
    binding = replace(
        s.command.binding,
        intent_id=uuid4(),
        output_slot=1,
        resource_id=uuid4(),
        version_id=uuid4(),
    )
    command = s.publisher._descriptors.encode(
        s.command.job, binding, filename="second.csv", sha256=hashlib.sha256(r.CONTENT).hexdigest()
    )
    async with s.db.connection("reserved_r") as conn, conn.transaction():
        await OutputReservations(s.publisher._descriptors).reserve_output_intent(
            conn, s.attempt, command
        )
    await s.effects.publish_reserved_output(
        s.publisher, s.attempt, command.binding.intent_id, r.CONTENT, deadline=r.deadline()
    )
    return command


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", ["UTC", "Pacific/Auckland", "America/St_Johns"])
async def test_known_presentation_commit_exact_observation_and_native_format(presenter, zone):
    s = presenter
    # Only this newly owned synthetic role; restore its default even on failure.
    s.db.sql("ALTER ROLE reserved_p SET TimeZone = '" + zone + "'", role="postgres")
    try:
        await s.publisher._pool.expire_connections()
        async with s.publisher._pool.acquire() as conn:
            assert await conn.fetchval("SHOW TimeZone") == zone
        await r.publish(s)
        before = r.entries(s)
        ident = await present(s)
        assert count(s) == "1" and r.rows(s) == "1:1:1:1" and r.entries(s) == before
        async with s.db.connection() as owner:
            row = await owner.fetchrow(
                "SELECT * FROM resource_presentations WHERE presentation_id=$1", ident
            )
        value = s.publisher._presentation_open(row, CapturedGeneration(s.account, 0), item_limit=10)
        assert value["original_task_id"] == str(s.claim.task_id)
        assert value["envelope"]["items"] == pins(s)
        assert value["envelope"]["snapshot_at"].endswith("+00:00")
        s.db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
        assert await present(s) == ident and count(s) == "1"
        # Reopening the same immutable card under another session timezone also
        # remains observation, without a fresh record or storage operation.
        s.db.sql("ALTER ROLE reserved_p SET TimeZone = 'UTC'", role="postgres")
        await s.publisher._pool.expire_connections()
        assert await present(s) == ident and count(s) == "1"
    finally:
        s.db.sql("ALTER ROLE reserved_p RESET TimeZone", role="postgres")


@pytest.mark.asyncio
async def test_other_retained_pins_are_authenticated_without_their_intents(presenter):
    s = presenter
    await r.publish(s)
    other = await second_output(s)
    first = await present(s, other.binding.intent_id)
    second = await present(s)
    assert first != second and count(s) == "2"
    assert await present(s) == second and count(s) == "2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["ciphertext", "generation", "ordinal", "schema", "job", "attempt", "extra"]
)
async def test_damaged_other_reference_blocks_new_presentation(presenter, damage):
    s = presenter
    await r.publish(s)
    other = await second_output(s)
    await present(s, other.binding.intent_id)
    async with s.db.connection() as owner:
        row = await owner.fetchrow(
            "SELECT * FROM resource_references WHERE version_id=$1", other.binding.version_id
        )
        token = "synthetic-invalid-provenance"
        if damage != "ciphertext":
            value = s.publisher._open(row["provenance_ciphertext"])
            if damage == "schema":
                value["schema_version"] = 2
            elif damage == "job":
                value["original_job_id"] = str(uuid4())
            elif damage == "attempt":
                value["original_attempt_id"] = "invalid-uuid"
            elif damage == "extra":
                value["unexpected"] = True
            else:
                value[damage] += 1
            token = s.publisher._seal(value)
        await owner.execute(
            "UPDATE resource_references SET provenance_ciphertext=$1 WHERE reference_id=$2",
            token,
            row["reference_id"],
        )
    with pytest.raises(registry.ResourceError):
        await present(s)
    assert count(s) == "1" and s.publisher._blocked and s.effects._blocked and s.history._blocked


@pytest.mark.asyncio
async def test_missing_catalog_phase_cannot_be_completed_by_presentation(presenter):
    s = presenter
    with pytest.raises(registry.ResourceError, match="incomplete"):
        await present(s)
    assert r.rows(s) == "0:0:0:0" and not r.files(s)


@pytest.mark.asyncio
async def test_unselected_presentation_limits_do_not_enable_the_phase(presenter):
    s = presenter
    await r.publish(s)
    s.publisher._presentation_item_limit = s.publisher._presentation_record_limit = None
    with pytest.raises(HistoryError, match="artifact_composition_refused"):
        await present(s)
    assert count(s) == "0" and not s.effects._blocked


@pytest.mark.asyncio
async def test_lost_h_cleanup_ack_cannot_return_a_known_card(presenter, monkeypatch):
    s = presenter
    await r.publish(s)
    native = s.history._exit
    fired = False

    def lost(original):
        nonlocal fired
        was_owned = s.history._context is not None
        native(original)
        if was_owned and original is None and not fired:
            fired = True
            raise RuntimeError("synthetic history release acknowledgement")

    monkeypatch.setattr(s.history, "_exit", lost)
    with pytest.raises(RuntimeError, match="history release acknowledgement"):
        await present(s)
    assert fired and count(s) == "1" and s.effects._blocked and s.history._blocked
    assert available(s.path, "OP_LOCK")
    with pytest.raises(HistoryError, match="blocked"):
        await present(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline"])
@pytest.mark.parametrize("hold_cancel", [False, True])
async def test_interrupted_real_insert_rolls_back_without_replacing_catalog(
    presenter, monkeypatch, mode, hold_cancel
):
    s = presenter
    await r.publish(s)
    entered, release = asyncio.Event(), asyncio.Event()
    native_cancel = connect_utils._cancel

    async def held_cancel(**kwargs):
        entered.set()
        await release.wait()
        return await native_cancel(**kwargs)

    if hold_cancel:
        monkeypatch.setattr(connect_utils, "_cancel", held_cancel)
    # Test-only trigger in the owned synthetic database: acquire an observable
    # transaction lock, then pause the actual server insert. No timeout changes.
    async with s.db.connection() as owner:
        await owner.execute(
            "CREATE FUNCTION public.synthetic_card_pause() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN PERFORM pg_advisory_xact_lock(17001,17002); PERFORM pg_sleep(5); RETURN NEW; END $$; "
            "REVOKE ALL ON FUNCTION public.synthetic_card_pause() FROM PUBLIC; "
            "CREATE TRIGGER synthetic_card_pause BEFORE INSERT ON public.resource_presentations "
            "FOR EACH ROW EXECUTE FUNCTION public.synthetic_card_pause()"
        )
        task = asyncio.create_task(present(s))
        try:
            pid = None
            async with asyncio.timeout(5):
                while True:
                    if s.publisher._connection is not None:
                        pid = s.publisher._connection.get_server_pid()
                        if await owner.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND classid=17001 AND objid=17002)",
                            pid,
                        ):
                            break
                    await asyncio.sleep(0.005)
            assert not task.done() and not available(s.path, "OP_LOCK")
            if mode == "cancel":
                task.cancel("original insert cancellation")
            if hold_cancel:
                async with asyncio.timeout(4):
                    await entered.wait()
                assert not task.done() and not available(s.path, "OP_LOCK")
                if mode == "cancel":
                    task.cancel("repeated cancellation during native cleanup")
                await asyncio.sleep(0.02)
                assert not task.done() and not available(s.path, "OP_LOCK")
                release.set()
            error = asyncio.CancelledError if mode == "cancel" else TimeoutError
            with pytest.raises(error) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original insert cancellation",)
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND classid=17001 AND objid=17002)",
                pid,
            )
            assert count(s) == "0" and r.rows(s) == "1:1:1:0"
            assert s.publisher._blocked and s.effects._blocked and s.history._blocked
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["version", "rows", "source", "extra"])
async def test_corrupt_reference_packet_never_stages_a_card(presenter, monkeypatch, damage):
    s = presenter
    await r.publish(s)
    native = asyncpg.Connection.fetchval

    async def corrupt(conn, query, *args, **kwargs):
        payload = await native(conn, query, *args, **kwargs)
        if "public.resource_output_presentation_references(" in query:
            assert isinstance(payload, str)
            value = json.loads(payload)
            if damage == "version":
                value["schema_version"] = True
            elif damage == "rows":
                value["output_references"] *= 2
            elif damage == "source":
                row = value["output_references"][0]
                row["message_id"] = str(uuid4())
                provenance = s.publisher._open(row["provenance_ciphertext"])
                provenance["original_message_id"] = row["message_id"]
                row["provenance_ciphertext"] = s.publisher._seal(provenance)
            else:
                value["unexpected"] = True
            return json.dumps(value)
        return payload

    monkeypatch.setattr(asyncpg.Connection, "fetchval", corrupt)
    with pytest.raises(registry.ResourceError, match="invalid_snapshot"):
        await present(s)
    assert count(s) == "0" and s.effects._blocked


@pytest.mark.asyncio
async def test_historical_stop_preserves_catalog_but_prevents_card(presenter):
    s = presenter
    await r.publish(s)
    from tests.restore_history_support import KEY

    await s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=r.deadline())
    with pytest.raises(HistoryError, match="historical_stop"):
        await present(s)
    assert r.rows(s) == "1:1:1:0" and r.files(s)


@pytest.mark.asyncio
async def test_stop_waits_for_known_presentation_cleanup(presenter, monkeypatch):
    s = presenter
    await r.publish(s)
    from tests.restore_history_support import KEY

    async with held_reset(s.publisher._pool, monkeypatch, 1, False) as (entered, proceed):
        publication = asyncio.create_task(present(s))
        stop = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            stop = asyncio.create_task(
                s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=r.deadline())
            )
            await asyncio.sleep(0.03)
            assert not publication.done() and not stop.done()
            proceed.set()
            assert await publication is not None
            await stop
            assert count(s) == "1"
            with pytest.raises(HistoryError, match="historical_stop"):
                await present(s)
        finally:
            proceed.set()
            await asyncio.gather(
                publication, *([stop] if stop is not None else []), return_exceptions=True
            )


@pytest.mark.asyncio
async def test_presentation_cannot_commit_a_borrowed_savepoint(presenter, monkeypatch):
    s = presenter
    await r.publish(s)
    native = asyncpg.Pool.acquire

    async def borrowed(pool, *args, **kwargs):
        conn = await native(pool, *args, **kwargs)
        if pool is s.publisher._pool:
            await conn.execute("BEGIN")
        return conn

    monkeypatch.setattr(asyncpg.Pool, "acquire", borrowed)
    with pytest.raises(registry.ResourceError, match="transaction"):
        await present(s)
    assert count(s) == "0" and s.publisher._blocked and r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["result_message", "conversation"])
async def test_presentation_requires_original_h_source_targets(presenter, changed):
    s = presenter
    conversation = s.claim.conversation_id if changed == "result_message" else uuid4()
    message = uuid4()
    async with s.db.connection() as owner, owner.transaction():
        if changed == "conversation":
            await owner.execute(
                "INSERT INTO conversations(id,user_id,pipeline,title) SELECT $1,user_id,pipeline,title FROM conversations WHERE id=$2",
                conversation,
                s.claim.conversation_id,
            )
        await owner.execute(
            "INSERT INTO messages(id,conversation_id,user_id,role,content) SELECT $1,$2,user_id,role,content FROM messages WHERE id=$3",
            message,
            conversation,
            s.claim.result_message_id,
        )
        await owner.execute(
            "UPDATE tasks SET conversation_id=$1,result_message_id=$2 WHERE id=$3",
            conversation,
            message,
            s.claim.task_id,
        )
    with pytest.raises(HistoryError, match="effects_origin_refused"):
        await present(s)
    assert r.rows(s) == "0:0:0:0" and not r.files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["suspended", "generation", "cancel", "origin", "marker", "projection", "snapshot_budget"],
)
async def test_current_source_and_history_refusal_preserves_incomplete_phase(
    presenter, monkeypatch, change
):
    s = presenter
    await r.publish(s)
    if change == "suspended":
        s.db.sql("UPDATE entitlement_accounts SET status='suspended'")
    elif change == "generation":
        s.db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    elif change == "cancel":
        s.db.sql("UPDATE tasks SET cancel_requested_at=clock_timestamp()")
    elif change == "origin":
        s.db.sql("UPDATE tasks SET input_ciphertext='invalid'")
    elif change == "marker":
        s.db.sql("DELETE FROM deletion_journal_applied")
    elif change == "projection":
        # Owned synthetic restore fault, using the existing immutable-projection
        # probe pattern. Application immutability and production SQL stay intact.
        async with s.db.connection() as owner, owner.transaction():
            await owner.execute(
                "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable"
            )
            await owner.execute("DELETE FROM task_history_projection")
            await owner.execute(
                "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable"
            )
    else:
        s.publisher._snapshot_byte_limit = 1
    if change == "projection":

        async def forbidden(*args, **kwargs):
            pytest.fail("restored projection mismatch reached P")

        monkeypatch.setattr(s.publisher, "_reserved_snapshot", forbidden)
    with pytest.raises(
        (asyncpg.PostgresError, registry.ResourceError, HistoryError, JournalError, ValueError)
    ):
        await present(s)
    assert count(s) == "0" and s.effects._blocked and s.history._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_unknown_presentation_commit_is_not_success_or_retry(presenter, monkeypatch, fault):
    s = presenter
    await r.publish(s)
    from asyncpg.transaction import Transaction

    native = Transaction.commit
    fired = False

    async def commit(tx):
        nonlocal fired
        if tx._connection is getattr(s.publisher._connection, "_con", None):
            fired = True
            s.db.bridge.commit_fault = fault
        try:
            return await native(tx)
        finally:
            s.db.bridge.commit_fault = None

    monkeypatch.setattr(Transaction, "commit", commit)
    try:
        with pytest.raises((asyncpg.PostgresError, TimeoutError)):
            await present(s)
    finally:
        s.db.bridge.commit_fault = None
    assert fired and count(s) == ("1" if fault == "after" else "0")
    assert s.publisher._blocked and s.effects._blocked and s.history._blocked
    with pytest.raises(HistoryError, match="blocked"):
        await present(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_actual_presentation_reset_drains_before_h_release(presenter, monkeypatch, mode):
    s = presenter
    await r.publish(s)
    async with held_reset(s.publisher._pool, monkeypatch, 1, mode == "failure") as (
        entered,
        proceed,
    ):
        task = asyncio.create_task(present(s))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original presentation cancellation")
                await asyncio.sleep(0.03)
                task.cancel("repeat")
            elif mode == "deadline":
                await asyncio.sleep(2.05)
                task.cancel("repeat after deadline")
            await asyncio.sleep(0.03)
            assert (
                not task.done()
                and not available(s.path, "OP_LOCK")
                and not available(s.path, "RUNTIME_LOCK")
            )
            proceed.set()
            error = (
                asyncio.CancelledError
                if mode == "cancel"
                else TimeoutError
                if mode == "deadline"
                else RuntimeError
            )
            with pytest.raises(error) as caught:
                await task
            if mode == "cancel":
                assert caught.value.args == ("original presentation cancellation",)
            assert count(s) == "1" and s.publisher._blocked and s.effects._blocked
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_physical_presentation_read_retains_ownership_through_cancellation(
    presenter, monkeypatch
):
    s = presenter
    await r.publish(s)
    entered, proceed = threading.Event(), threading.Event()
    native = registry.read_verified

    def held(*args, **kwargs):
        value = native(*args, **kwargs)
        entered.set()
        assert proceed.wait(5)
        return value

    monkeypatch.setattr(registry, "read_verified", held)
    task = asyncio.create_task(present(s))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        task.cancel("original physical cancellation")
        await asyncio.sleep(0.03)
        task.cancel("repeat")
        assert not task.done() and not available(s.path, "OP_LOCK")
        proceed.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("original physical cancellation",)
        assert count(s) == "0" and s.publisher._blocked
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "foreign",
        "extra",
        "noncanonical",
        "capacity",
        "duplicate_pins",
        "null",
        "scalar",
    ],
)
async def test_reference_reader_complete_or_refused(presenter, change):
    s = presenter
    await r.publish(s)
    items = pins(s)
    if change == "missing":
        s.db.sql("DELETE FROM resource_references")
    elif change == "duplicate":
        s.db.sql(
            "INSERT INTO resource_references SELECT gen_random_uuid(),account_id,resource_id,version_id,generation,role,ordinal+1,task_id,conversation_id,message_id,job_id,provenance_ciphertext,created_at FROM resource_references"
        )
    elif change == "foreign":
        items[0]["resource_id"] = str(uuid4())
    elif change == "extra":
        items[0]["extra"] = "forbidden"
    elif change == "noncanonical":
        items[0]["resource_id"] = items[0]["resource_id"].replace("-", "")
    elif change == "duplicate_pins":
        items *= 2
    elif change == "null":
        items = None
    elif change == "scalar":
        items = {}
    else:
        items *= 101
    async with s.db.connection("reserved_p") as conn:
        with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
            await raw_refs(conn, s.attempt, items)


@pytest.mark.asyncio
async def test_reference_reader_empty_compatibility_and_original_order(presenter):
    s = presenter
    await r.publish(s)
    other = await second_output(s)
    items = [
        {
            "resource_id": str(other.binding.resource_id),
            "version_id": str(other.binding.version_id),
        },
        *pins(s),
    ]
    async with s.db.connection("reserved_p") as conn:
        assert await raw_refs(conn, s.attempt, []) == {"schema_version": 1, "output_references": []}
        observed = await raw_refs(conn, s.attempt, items)
        assert [x["version_id"] for x in observed["output_references"]] == [
            p["version_id"] for p in items
        ]
        for table in ("resource_references", "resource_presentations"):
            assert not await conn.fetchval(
                "SELECT has_table_privilege(current_user,$1,'SELECT,INSERT,UPDATE,DELETE')", table
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["grantee", "grant_option", "helper", "table", "column", "membership", "sixth_missing"],
)
async def test_seventh_capability_preserves_clean_principal(presenter, change):
    s = presenter
    sql = {
        "grantee": "GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO md_third",
        "grant_option": "GRANT EXECUTE ON FUNCTION "
        + SIGNATURE
        + " TO reserved_p WITH GRANT OPTION",
        "helper": "GRANT EXECUTE ON FUNCTION public.resource_output_reference_principal() TO reserved_p",
        "table": "GRANT SELECT ON resource_references TO reserved_p",
        "column": "GRANT SELECT(provenance_ciphertext) ON resource_references TO reserved_p",
        "membership": "GRANT md_owner TO reserved_p",
        "sixth_missing": "REVOKE EXECUTE ON FUNCTION public.resource_output_namespace_inventory(pg_catalog.uuid,pg_catalog.int4) FROM reserved_p",
    }[change]
    if change == "membership":
        s.db.cluster.sql(sql)
    else:
        s.db.sql(sql)
    try:
        async with s.db.connection("reserved_p") as conn:
            with pytest.raises(asyncpg.PostgresError, match="resource_output_refused"):
                await raw_refs(conn, s.attempt, [])
    finally:
        if change == "membership":
            s.db.cluster.sql("REVOKE md_owner FROM reserved_p")
