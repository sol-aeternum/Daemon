"""Explicit owned networkless PostgreSQL and real journal/bootstrap composition."""

import asyncio
import threading
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.artifact_namespaces import NamespaceController
from orchestrator import artifact_namespaces
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.mixed_store import MixedJournal
from orchestrator.services.deletion_journal.namespace_candidate import NamespaceCreateFields
from orchestrator.services.deletion_journal.store import initialize
from orchestrator.services.deletion_journal.upgrade import promote_container_v2
from tests.compute_metadata_support import metadata_cluster as metadata_cluster
from tests.deletion_journal_support import request
from tests import test_attempt_lifecycle_postgres as lifecycle_tests

SQL = (Path(__file__).resolve().parents[1] / "migrations/052_artifact_namespaces.sql").read_text()
base_db = lifecycle_tests.db


@pytest_asyncio.fixture
async def scenario(base_db, tmp_path):
    db = base_db
    db.sql("BEGIN;" + SQL + "COMMIT;")
    journal, identity = tmp_path / "journal", uuid4()
    initialize(journal, identity)
    promote_container_v2(journal, identity)
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    for child in ("live", "private"):
        (root / child).mkdir(mode=0o700)
    async with db.connection() as owner:
        account = await db.account(owner)
        await owner.execute("INSERT INTO deletion_journal_binding VALUES(true,$1)", identity)
    pool = await asyncpg.create_pool(
        host=str(db.host),
        user="md_owner",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=4,
        command_timeout=5,
    )
    try:
        yield (
            db,
            NamespaceController(pool, root, journal, identity),
            CapturedGeneration(account, 0),
            root,
            journal,
            identity,
        )
    finally:
        try:
            async with asyncio.timeout(5):
                await pool.close()
        finally:
            pool.terminate()


@pytest.mark.asyncio
async def test_roundtrip_concurrent_same_birth_and_no_cascade(scenario):
    db, controller, expected, root, journal, identity = scenario
    first, second = await asyncio.gather(
        controller.ensure_namespace(expected), controller.ensure_namespace(expected)
    )
    assert first == second
    assert await controller.reconcile_all() == (first,)
    with MixedJournal(journal, identity) as mixed:
        assert len(mixed.verify().entries) == 1
    async with db.connection() as owner:
        assert await owner.fetchval("SELECT state FROM artifact_namespaces") == "ready"
        assert await owner.fetchval("SELECT count(*) FROM deletion_journal_applied") == 1
        await owner.execute("DELETE FROM users WHERE id=$1", expected.account_id)
        assert await owner.fetchval("SELECT count(*) FROM artifact_namespaces") == 1


@pytest.mark.asyncio
async def test_projection_rollback_reuses_original_journal_birth(scenario):
    db, controller, expected, root, journal, identity = scenario
    # Actual DB error after inventory INSERT: the whole transaction must roll back.
    db.sql(
        "CREATE FUNCTION fail_marker() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$;"
        "CREATE TRIGGER fail_marker BEFORE INSERT ON deletion_journal_applied FOR EACH ROW EXECUTE FUNCTION fail_marker();"
    )
    with pytest.raises(JournalError, match="unavailable"):
        await controller.ensure_namespace(expected)
    with MixedJournal(journal, identity) as mixed:
        original = mixed.verify().entries
    assert len(original) == 1
    assert not list((root / "live").iterdir())
    assert db.sql("SELECT count(*) FROM artifact_namespaces") == "0"
    db.sql("DROP TRIGGER fail_marker ON deletion_journal_applied")
    result = await controller.ensure_namespace(expected)
    assert isinstance(original[0].command.fields, NamespaceCreateFields)
    assert result.namespace == original[0].command.fields.namespace
    with MixedJournal(journal, identity) as mixed:
        assert mixed.verify().entries == original


@pytest.mark.asyncio
async def test_ready_missing_and_later_lifecycle_refuse_without_mkdir(scenario):
    db, controller, expected, root, journal, identity = scenario
    result = await controller.ensure_namespace(expected)
    (root / "live" / result.namespace / "staging").rmdir()
    with pytest.raises(JournalError, match="unavailable"):
        await controller.ensure_namespace(expected)
    assert not (root / "live" / result.namespace / "staging").exists()
    with MixedJournal(journal, identity) as mixed:
        mixed.append(request(expected.account_id), uuid4(), 1, None)
    db.sql("UPDATE artifact_namespaces SET state='provisioning'")
    with pytest.raises(JournalError, match="recovery_required"):
        await controller.reconcile_all()
    assert not (root / "live" / result.namespace / "staging").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["row_only", "marker_only", "digest"])
async def test_marker_inventory_contradictions(scenario, damage):
    db, controller, expected, root, journal, identity = scenario
    await controller.ensure_namespace(expected)
    if damage == "row_only":
        db.sql("DELETE FROM deletion_journal_applied")
    elif damage == "marker_only":
        db.sql("DELETE FROM artifact_namespaces")
    else:
        db.sql("UPDATE artifact_namespaces SET birth_digest=decode(repeat('00',32),'hex')")
    with pytest.raises(JournalError, match="marker_mismatch"):
        await controller.ensure_namespace(expected)


@pytest.mark.asyncio
async def test_stale_original_generation_after_real_lock_wait(scenario):
    db, controller, expected, root, journal, identity = scenario
    async with db.connection() as owner:
        async with owner.transaction():
            await owner.execute(
                "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                expected.account_id,
            )
            pending = asyncio.create_task(controller.ensure_namespace(expected))
            # Observe a real lock waiter, not just an elapsed scheduling delay.
            async with asyncio.timeout(3):
                while not await owner.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted)"
                ):
                    await asyncio.sleep(0.005)
            await owner.execute(
                "UPDATE entitlement_accounts SET content_generation=1 WHERE user_id=$1",
                expected.account_id,
            )
        with pytest.raises(WriteGuardRefusal, match="stale_generation"):
            await pending
    assert not list((root / "live").iterdir())


@pytest.mark.asyncio
async def test_acl_default_grants_refuse_atomically(base_db):
    db = base_db
    db.sql("ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO md_app")
    result = db.sql("BEGIN;" + SQL + "COMMIT;", success=False)
    assert "namespace_unsafe_default_privileges" in result
    assert db.sql("SELECT to_regclass('public.artifact_namespaces') IS NULL") == "t"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
@pytest.mark.parametrize("phase", ["inventory", "ready"])
async def test_real_commit_wire_uncertainty_requires_original_birth_readback(
    scenario, fault, phase, monkeypatch
):
    db, controller, expected, root, journal, identity = scenario
    db.bridge.commit_fault_role = "md_owner"
    if phase == "inventory":
        db.bridge.commit_fault = fault
    else:
        topology = artifact_namespaces._topology

        def arm_after_topology(*args, **kwargs):
            topology(*args, **kwargs)
            db.bridge.commit_fault = fault

        monkeypatch.setattr(artifact_namespaces, "_topology", arm_after_topology)
    try:
        pending = asyncio.create_task(controller.ensure_namespace(expected))
        done, _ = await asyncio.wait({pending}, timeout=12)
        if not done:
            # Preserve await locations before terminating only this fixture's pool.
            stacks = []
            for task in asyncio.all_tasks():
                frame = task.get_coro()
                chain = []
                while frame is not None:
                    code = getattr(frame, "cr_code", None)
                    if code is not None:
                        chain.append(code.co_name)
                    frame = getattr(frame, "cr_await", None)
                stacks.append(" -> ".join(chain))
            controller._pool.terminate()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            pytest.fail("namespace wire timeout: " + "; ".join(stacks))
        with pytest.raises(JournalError, match="unavailable"):
            await pending
        assert db.bridge.commit_seen.is_set()
        if fault == "after":
            assert db.bridge.discarded_commit_bytes > 0
    finally:
        db.bridge.commit_fault = None
        monkeypatch.undo()
    present = phase == "ready" or fault == "after"
    assert bool(list((root / "live").iterdir())) == (phase == "ready")
    assert db.sql("SELECT count(*) FROM artifact_namespaces") == str(int(present))
    assert db.sql("SELECT count(*) FROM deletion_journal_applied") == str(int(present))
    if present:
        assert db.sql("SELECT state FROM artifact_namespaces") == (
            "ready" if phase == "ready" and fault == "after" else "provisioning"
        )
    with MixedJournal(journal, identity) as mixed:
        original = mixed.verify().entries
    result = await controller.ensure_namespace(expected)
    assert isinstance(original[0].command.fields, NamespaceCreateFields)
    assert result.namespace == original[0].command.fields.namespace
    with MixedJournal(journal, identity) as mixed:
        assert mixed.verify().entries == original


@pytest.mark.asyncio
async def test_cancellation_waits_for_owned_physical_work_and_never_marks_ready(
    scenario, monkeypatch
):
    db, controller, expected, root, journal, identity = scenario
    started, release = threading.Event(), threading.Event()
    original = artifact_namespaces._topology

    def paused(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise OSError("fixture_timeout")
        return original(*args, **kwargs)

    monkeypatch.setattr(artifact_namespaces, "_topology", paused)
    task = asyncio.create_task(controller.ensure_namespace(expected))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        # Cancellation is delivered but the owned I/O future still has to finish.
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert db.sql("SELECT state FROM artifact_namespaces") == "provisioning"
    monkeypatch.setattr(artifact_namespaces, "_topology", original)
    await controller.ensure_namespace(expected)
    assert db.sql("SELECT state FROM artifact_namespaces") == "ready"


@pytest.mark.asyncio
async def test_restored_missing_projection_revalidates_physical_inventory(scenario):
    db, controller, expected, root, journal, identity = scenario
    first = await controller.ensure_namespace(expected)
    db.sql("BEGIN;DELETE FROM artifact_namespaces;DELETE FROM deletion_journal_applied;COMMIT;")
    assert await controller.reconcile_all() == (first,)
    # Lost markers do not manufacture a different generation or namespace.
    with MixedJournal(journal, identity) as mixed:
        assert len(mixed.verify().entries) == 1
