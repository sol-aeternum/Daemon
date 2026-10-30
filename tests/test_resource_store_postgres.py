"""Explicit real owned PostgreSQL/journal/POSIX registry, never application DSNs."""

import asyncio
import hashlib
import threading
from dataclasses import replace
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator import resource_store as registry
from orchestrator.artifact_namespaces import NamespaceController
from orchestrator.artifact_reservations import DescriptorCodec, JobBinding, OutputReservations
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal
from orchestrator.services.deletion_journal.store import initialize
from orchestrator.services.deletion_journal.upgrade import promote_container_v2
from tests.compute_metadata_support import ROOT, metadata_cluster as metadata_cluster
from tests.deletion_journal_support import request
from orchestrator.services.deletion_journal.mixed_store import MixedJournal
from tests import test_artifact_reservations_postgres as reservations

lifecycle_db = reservations.lifecycle_db
db = reservations.db
reservation_scenario = reservations.scenario
reservation_key = reservations.reservation_key
CONTENT = b"column\nprivate-value\n"


@pytest.fixture
def resource_key(reservation_key):
    return reservation_key


@pytest_asyncio.fixture
async def resource_scenario(db, reservation_scenario, tmp_path, resource_key):
    identity, original, reservations_store, descriptor, conversation = reservation_scenario
    descriptor = DescriptorCodec(Fernet(resource_key))
    reservations_store = OutputReservations(descriptor)
    for name in ("052_artifact_namespaces.sql", "053_resource_registry.sql"):
        db.sql("BEGIN;" + (ROOT / "migrations" / name).read_text() + "COMMIT;")
    # Reuse the actual registered lifecycle and exact restricted reservation path.
    command = descriptor.encode(
        original.job,
        replace(original.binding, measured_bytes=len(CONTENT)),
        filename="private-output.csv",
        sha256=hashlib.sha256(CONTENT).hexdigest(),
    )
    async with db.connection("md_app") as app:
        await reservations.reserve(app, reservations_store, identity, command)
    journal, journal_id = tmp_path / "journal", uuid4()
    initialize(journal, journal_id)
    promote_container_v2(journal, journal_id)
    root = tmp_path / "objects"
    root.mkdir(mode=0o700)
    for name in ("live", "private"):
        (root / name).mkdir(mode=0o700)
    async with db.connection() as owner:
        await owner.execute("INSERT INTO deletion_journal_binding VALUES(true,$1)", journal_id)
    # Bridge classifies the connection's startup role, including pooled reuse.
    db.bridge.commit_fault_role = "md_owner"
    pool = await asyncpg.create_pool(
        host=str(db.host),
        user="md_owner",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=4,
        command_timeout=2,
    )
    expected = CapturedGeneration(identity.account_id, identity.generation)
    try:
        ns = await NamespaceController(pool, root, journal, journal_id).ensure_namespace(expected)
        # The injected synthetic cipher is identical to the actual reserved descriptor.
        store = registry.ResourceStore(pool, root, journal, journal_id, descriptor._cipher)
        yield (
            db,
            store,
            identity,
            command,
            expected,
            root,
            ns.namespace,
            descriptor,
            reservations_store,
            journal,
            journal_id,
        )
    finally:
        try:
            async with asyncio.timeout(5):
                await pool.close()
        finally:
            pool.terminate()


@pytest.mark.asyncio
async def test_committed_private_read_then_repair_and_expired_history(resource_scenario):
    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    assert not (root / "live" / ns / pub.version_id.hex).exists()
    assert (await store.read(expected, pub.resource_id, pub.version_id)).content == CONTENT
    await store.repair(expected, pub.resource_id, pub.version_id)
    assert (root / "live" / ns / pub.version_id.hex).samefile(
        root / "private" / ns / command.binding.intent_id.hex
    )
    db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    assert await store.publish_output(ident, command.binding.intent_id, CONTENT) == pub
    await store.repair(expected, pub.resource_id, pub.version_id)
    assert db.sql("SELECT insertion_counter FROM resource_catalog_heads") == "1"
    assert db.sql("SELECT count(*) FROM resource_versions") == "1"
    assert db.sql("SELECT count(*) FROM resource_references") == "1"


@pytest.mark.asyncio
async def test_append_version_pins_existing_resource_and_retains_original(resource_scenario):
    db, store, ident, first, expected, root, ns, descriptor, r0, *_ = resource_scenario
    v1 = await store.publish_output(ident, first.binding.intent_id, CONTENT)
    job = JobBinding(uuid4(), ident.account_id, ident.generation, ident.task_id, uuid4())
    async with db.connection("md_app") as app:
        async with app.transaction():
            await r0.register_output_job(app, ident, job)
    second_bytes = b"column\nsecond value\n"
    intent = await store.reserve_version(ident, job, v1.resource_id, 0, second_bytes, "second.csv")
    assert (
        await store.reserve_version(ident, job, v1.resource_id, 0, second_bytes, "second.csv")
        == intent
    )
    with pytest.raises(registry.ResourceError, match="conflict"):
        await store.reserve_version(ident, job, v1.resource_id, 0, CONTENT, "changed.csv")
    v2 = await store.publish_output(ident, intent, second_bytes)
    assert v1.resource_id == v2.resource_id and v1.version_id != v2.version_id
    assert (await store.read(expected, v1.resource_id, v1.version_id)).content == CONTENT
    assert (await store.read(expected, v2.resource_id, v2.version_id)).content == second_bytes
    assert db.sql("SELECT count(*) FROM resources") == "1"
    assert db.sql("SELECT count(*) FROM resource_versions") == "2"
    assert db.sql("SELECT insertion_ordinal FROM resources") == "1"
    assert db.sql("SELECT insertion_counter FROM resource_catalog_heads") == "2"
    assert (
        db.sql("SELECT string_agg(ordinal::text,',' ORDER BY ordinal) FROM resource_references")
        == "0,1"
    )


@pytest.mark.asyncio
async def test_concurrent_delivery_commits_one_pinned_version(resource_scenario):
    db, store, ident, command, *_ = resource_scenario
    results = await asyncio.gather(
        *(store.publish_output(ident, command.binding.intent_id, CONTENT) for _ in range(2))
    )
    assert results[0] == results[1]
    assert db.sql("SELECT count(*) FROM resource_versions") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["expired", "cancel", "descriptor", "stale"])
async def test_invalid_authority_denied_before_staging(resource_scenario, damage):
    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    if damage == "expired":
        db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    elif damage == "cancel":
        db.sql("UPDATE tasks SET cancel_requested_at=clock_timestamp()")
    elif damage == "descriptor":
        db.sql("UPDATE artifact_output_intents SET descriptor_ciphertext='invalid'")
    else:
        db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    with pytest.raises((registry.ResourceError, WriteGuardRefusal)):
        await store.publish_output(ident, command.binding.intent_id, CONTENT)
    assert not list((root / "live" / ns / "staging").iterdir())
    assert db.sql("SELECT count(*) FROM resource_versions") == "0"


@pytest.mark.asyncio
async def test_foreign_or_mismatched_version_denied_before_storage(resource_scenario, monkeypatch):
    db, store, ident, command, expected, *_ = resource_scenario
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    async with db.connection() as owner:
        other = await db.account(owner)

    def forbidden(*args):
        pytest.fail("foreign metadata path reached storage")

    monkeypatch.setattr(registry, "read_verified", forbidden)
    for capture, resource, version in [
        (CapturedGeneration(other, 0), pub.resource_id, pub.version_id),
        (expected, uuid4(), pub.version_id),
        (expected, pub.resource_id, uuid4()),
    ]:
        with pytest.raises(registry.ResourceError, match="not_found"):
            await store.read(capture, resource, version)


@pytest.mark.asyncio
async def test_commit_rollback_preserves_tracked_private_and_counter(resource_scenario):
    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    db.sql(
        "CREATE FUNCTION fail_publication() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$;"
        "CREATE TRIGGER fail_publication BEFORE INSERT ON resource_publications FOR EACH ROW EXECUTE FUNCTION fail_publication();"
    )
    with pytest.raises(registry.ResourceError, match="uncertain"):
        await store.publish_output(ident, command.binding.intent_id, CONTENT)
    assert db.sql("SELECT count(*) FROM resource_versions") == "0"
    assert db.sql("SELECT count(*) FROM resource_catalog_heads") == "0"
    assert (root / "private" / ns / command.binding.intent_id.hex).exists()
    db.sql("DROP TRIGGER fail_publication ON resource_publications")
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    assert pub.version_id == command.binding.version_id
    assert db.sql("SELECT insertion_counter FROM resource_catalog_heads") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_real_unknown_commit_fresh_original_publication_readback(resource_scenario, fault):
    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    db.bridge.commit_fault_role = "md_owner"
    db.bridge.commit_fault = fault
    try:
        async with asyncio.timeout(8):
            with pytest.raises(registry.ResourceError, match="uncertain"):
                await store.publish_output(ident, command.binding.intent_id, CONTENT)
        assert db.bridge.commit_seen.is_set()
        if fault == "after":
            assert db.bridge.discarded_commit_bytes > 0
    finally:
        db.bridge.commit_fault = None
    assert db.sql("SELECT count(*) FROM resource_publications") == (
        "1" if fault == "after" else "0"
    )
    assert not (root / "live" / ns / command.binding.version_id.hex).exists()
    if fault == "after":
        assert (
            await store.read(expected, command.binding.resource_id, command.binding.version_id)
        ).content == CONTENT
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    assert pub.version_id == command.binding.version_id
    assert db.sql("SELECT insertion_counter FROM resource_catalog_heads") == "1"


@pytest.mark.asyncio
async def test_cancellation_drains_staging_before_unlock_and_no_catalog_ack(
    resource_scenario, monkeypatch
):
    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    started, release = threading.Event(), threading.Event()
    original = registry.prepare_verified

    def paused(*args):
        started.set()
        if not release.wait(5):
            raise OSError("synthetic_timeout")
        original(*args)

    monkeypatch.setattr(registry, "prepare_verified", paused)
    task = asyncio.create_task(store.publish_output(ident, command.binding.intent_id, CONTENT))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert db.sql("SELECT count(*) FROM resource_publications") == "0"
    assert (root / "private" / ns / command.binding.intent_id.hex).exists()


@pytest.mark.asyncio
async def test_later_unsupported_journal_history_blocks_reads_and_new_publication(
    resource_scenario, monkeypatch
):
    db, store, ident, command, expected, root, ns, descriptor, r0, journal, journal_id = (
        resource_scenario
    )
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    with MixedJournal(journal, journal_id) as mixed:
        mixed.append(request(ident.account_id), uuid4(), 1, None)
    with pytest.raises(registry.ResourceError, match="uncertain"):
        await store.read(expected, pub.resource_id, pub.version_id)
    with pytest.raises(registry.ResourceError, match="uncertain"):
        await store.repair(expected, pub.resource_id, pub.version_id)


@pytest.mark.asyncio
async def test_corrupt_private_stays_uncertain_not_permanent_missing(resource_scenario):
    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    (root / "private" / ns / command.binding.intent_id.hex).write_bytes(b"corrupt")
    with pytest.raises(registry.ResourceError, match="uncertain"):
        await store.read(expected, pub.resource_id, pub.version_id)
    assert db.sql("SELECT count(*) FROM resource_publications") == "1"


def test_installed_sql_matches_reviewed_candidate_except_header():
    # Exact declarations pinned by the external pre-change qualification are also
    # covered by PostgreSQL tests here; no runtime DDL generation.
    sql = (ROOT / "migrations/053_resource_registry.sql").read_text()
    assert "SECURITY DEFINER" not in sql and "GRANT " not in sql
    assert "ON DELETE RESTRICT" in sql and "DEFERRABLE INITIALLY DEFERRED" in sql


@pytest.mark.asyncio
async def test_append_cannot_reuse_original_r0_operation_slot(resource_scenario):
    db, store, ident, command, expected, *_ = resource_scenario
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    with pytest.raises(registry.ResourceError, match="conflict"):
        await store.reserve_version(ident, command.job, pub.resource_id, 0, CONTENT, "v2.csv")
    assert db.sql("SELECT count(*) FROM resource_version_intents") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["conversation", "message", "message_owner"])
async def test_foreign_source_rows_refused_before_any_stage(resource_scenario, source):
    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    async with db.connection() as owner:
        foreign = await db.account(owner)
        conversation, message = uuid4(), uuid4()
        await owner.execute(
            "INSERT INTO conversations(id,user_id,pipeline) VALUES($1,$2,'cloud')",
            conversation,
            foreign,
        )
        message_conversation = conversation
        if source == "message_owner":
            message_conversation = await owner.fetchval(
                "SELECT conversation_id FROM tasks WHERE id=$1", ident.task_id
            )
        await owner.execute(
            "INSERT INTO messages(id,conversation_id,user_id,role,content) VALUES($1,$2,$3,'assistant','synthetic')",
            message,
            message_conversation,
            foreign,
        )
        if source == "conversation":
            await owner.execute(
                "UPDATE tasks SET conversation_id=$2 WHERE id=$1", ident.task_id, conversation
            )
        else:
            await owner.execute(
                "UPDATE tasks SET result_message_id=$2 WHERE id=$1", ident.task_id, message
            )
    with pytest.raises(registry.ResourceError, match="refused"):
        await store.publish_output(ident, command.binding.intent_id, CONTENT)
    assert not list((root / "live" / ns / "staging").iterdir())
    assert db.sql("SELECT count(*) FROM resource_publications") == "0"


@pytest.mark.asyncio
async def test_lease_expiring_during_real_fs_work_rolls_back_catalog(
    resource_scenario, monkeypatch
):
    import time

    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()+interval '0.5 second'")
    original = registry.prepare_verified

    def slow(*args):
        original(*args)
        time.sleep(0.65)

    monkeypatch.setattr(registry, "prepare_verified", slow)
    with pytest.raises(registry.ResourceError, match="uncertain"):
        await store.publish_output(ident, command.binding.intent_id, CONTENT)
    assert db.sql("SELECT count(*) FROM resource_versions") == "0"
    assert db.sql("SELECT count(*) FROM resource_references") == "0"
    assert db.sql("SELECT count(*) FROM resource_catalog_heads") == "0"
    assert (root / "private" / ns / command.binding.intent_id.hex).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["binding", "metadata", "provenance"])
async def test_authenticated_record_substitution_is_denied(resource_scenario, damage):
    db, store, ident, command, expected, *_ = resource_scenario
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    if damage == "binding":
        table, column = "resource_publications", "binding_ciphertext"
    elif damage == "metadata":
        table, column = "resource_versions", "metadata_ciphertext"
    else:
        table, column = "resource_references", "provenance_ciphertext"
    async with db.connection() as owner:
        token = await owner.fetchval(f"SELECT {column} FROM {table}")
        value = store._open(token)
        value["account_id"] = str(uuid4())
        await owner.execute(f"UPDATE {table} SET {column}=$1", store._seal(value))
    with pytest.raises(registry.ResourceError, match="uncertain"):
        await store.read(expected, pub.resource_id, pub.version_id)


@pytest.mark.asyncio
async def test_deferred_source_delete_refuses_until_explicit_detach(resource_scenario):
    db, store, ident, command, expected, *_ = resource_scenario
    await store.publish_output(ident, command.binding.intent_id, CONTENT)
    async with db.connection() as owner:
        conversation = await owner.fetchval(
            "SELECT conversation_id FROM tasks WHERE id=$1", ident.task_id
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with owner.transaction():
                await owner.execute("DELETE FROM conversations WHERE id=$1", conversation)
        assert await owner.fetchval("SELECT count(*) FROM resource_publications") == 1


@pytest.mark.asyncio
async def test_registry_schema_metadata_only_and_composite_constraints(resource_scenario):
    db, store, ident, command, expected, *_ = resource_scenario
    pub = await store.publish_output(ident, command.binding.intent_id, CONTENT)
    async with db.connection() as owner:
        statement = "INSERT INTO resource_versions(version_id,account_id,resource_id,generation,representation,kind,mime,measured_bytes,metadata_ciphertext,insertion_ordinal) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'synthetic-ciphertext',2)"
        for representation, kind, mime, size in [
            ("metadata_only", "file", "application/octet-stream", 0),
            ("generated_file", "file", "text/csv", None),
            ("generated_file", "file", "text/csv", 8388609),
            ("submitted_text", "text", "text/html", 1),
        ]:
            with pytest.raises(asyncpg.CheckViolationError):
                async with owner.transaction():
                    await owner.execute(
                        statement,
                        uuid4(),
                        ident.account_id,
                        pub.resource_id,
                        ident.generation,
                        representation,
                        kind,
                        mime,
                        size,
                    )
        other = await db.account(owner)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with owner.transaction():
                await owner.execute(
                    statement,
                    uuid4(),
                    other,
                    pub.resource_id,
                    0,
                    "metadata_only",
                    "file",
                    "application/octet-stream",
                    None,
                )
        async with owner.transaction():
            await owner.execute(
                statement,
                uuid4(),
                ident.account_id,
                pub.resource_id,
                ident.generation,
                "metadata_only",
                "file",
                "application/octet-stream",
                None,
            )
        await owner.execute("DELETE FROM users WHERE id=$1", ident.account_id)
        assert await owner.fetchval("SELECT count(*) FROM resource_versions") == 0
        assert await owner.fetchval("SELECT count(*) FROM artifact_namespaces") == 1


@pytest.mark.asyncio
async def test_global_resource_unique_wait_rechecks_real_expired_lease(resource_scenario):
    db, store, ident, command, expected, root, ns, *_ = resource_scenario
    async with db.connection() as blocker, db.connection() as observer:
        foreign = await db.account(blocker)
        tx = blocker.transaction()
        await tx.start()
        await blocker.execute(
            "INSERT INTO resources(resource_id,account_id,insertion_ordinal) VALUES($1,$2,1)",
            command.binding.resource_id,
            foreign,
        )
        db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()+interval '1 second'")
        task = asyncio.create_task(store.publish_output(ident, command.binding.intent_id, CONTENT))
        try:
            async with asyncio.timeout(2):
                while not await observer.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING(pid) WHERE NOT l.granted AND a.datname=current_database())"
                ):
                    await asyncio.sleep(0.005)
            # PostgreSQL wall clock, while the actual invisible unique contender owns its row.
            await observer.execute("SELECT pg_sleep(1.05)")
        finally:
            await tx.rollback()
        with pytest.raises(registry.ResourceError, match="uncertain"):
            await task
    assert db.sql("SELECT count(*) FROM resources") == "0"
    assert db.sql("SELECT count(*) FROM resource_publications") == "0"
    assert db.sql("SELECT count(*) FROM resource_catalog_heads") == "0"
    assert (root / "private" / ns / command.binding.intent_id.hex).exists()
