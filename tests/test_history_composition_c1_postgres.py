"""Restricted synthetic A joins the real C1 final transaction and H prepare."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import asyncpg
from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.resource_inputs import ResourceInputs
from orchestrator.resource_store import ResourceError, ResourceStore
from orchestrator.services.restore_history.acceptance import AcceptanceProjection
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.tasks.store import TaskStore
from tests.compute_metadata_support import ROOT
from tests.restore_history_support import HISTORY, KEY, key_check
from tests.test_history_composition_postgres import deadline, entries, independent_owner
from tests.test_resource_input_codec import LIMITS
from tests.test_resource_inputs_postgres import (
    db as db,
    input_scenario as input_scenario,
    lifecycle_db as lifecycle_db,
    metadata_cluster as metadata_cluster,
    reservation_key as reservation_key,
    reservation_scenario as reservation_scenario,
    resource_key as resource_key,
    resource_scenario as resource_scenario,
    submission,
)
from tests.test_restore_history_store import owned


@pytest_asyncio.fixture
async def c1_scenario(input_scenario, tmp_path, request):
    db, _, _, _, expected, key, resources = input_scenario
    for name in ("055_resource_presentations.sql", "056_restore_history_acceptance.sql"):
        db.sql("BEGIN;" + (ROOT / "migrations" / name).read_text() + "COMMIT;")
    # Synthetic principal only. No broad table grant or protected owner fallback.
    db.cluster.sql(
        "DO $$ BEGIN CREATE ROLE md_acceptor LOGIN NOSUPERUSER NOCREATEROLE; "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;",
        database=db.name,
    )
    db.sql(
        "GRANT USAGE ON SCHEMA public TO md_acceptor;"
        "GRANT SELECT ON tasks,conversations,messages,task_events,entitlement_accounts,"
        "account_deletions,account_suspension_causes,deletion_journal_binding,"
        "deletion_journal_applied,artifact_namespaces,resource_input_batches,"
        "resource_input_intents,resource_catalog_heads,resources,resource_versions,"
        "resource_publications,resource_references TO md_acceptor;"
        "GRANT INSERT ON tasks,conversations,messages,task_events,resource_input_batches,"
        "resource_input_intents,resource_catalog_heads,resources,resource_versions,"
        "resource_publications,resource_references TO md_acceptor;"
        "GRANT UPDATE(request_hash,event_seq,updated_at) ON tasks TO md_acceptor;"
        "GRANT UPDATE(id) ON conversations TO md_acceptor;"
        "GRANT UPDATE(created_at) ON messages TO md_acceptor;"
        "GRANT UPDATE(user_id) ON entitlement_accounts TO md_acceptor;"
        "GRANT UPDATE(id) ON account_deletions TO md_acceptor;"
        "GRANT UPDATE(state) ON artifact_namespaces TO md_acceptor;"
        "GRANT UPDATE(state,accepted_task_id) ON resource_input_batches TO md_acceptor;"
        "GRANT UPDATE(intent_id) ON resource_input_intents TO md_acceptor;"
        "GRANT UPDATE(insertion_counter) ON resource_catalog_heads TO md_acceptor;"
        "GRANT INSERT(task_id,account_id,account_generation,protocol_version) "
        "ON task_inference_recovery TO md_acceptor;"
        "GRANT EXECUTE ON FUNCTION task_history_acceptance_project(bytea) TO md_acceptor;"
        "GRANT EXECUTE ON FUNCTION task_history_acceptance_page(uuid,bigint,text) "
        "TO md_acceptor,md_app;"
    )
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO task_history_binding(history_id,version,key_id,key_check,request_schema) "
            "VALUES($1,1,1,$2,'chat-canonical-3715-v1')",
            HISTORY,
            key_check(KEY, HISTORY),
        )
    # Bridge fixes role classification when each socket starts. Arm its COMMIT
    # loss later, but classify the actual A login before creating its pool.
    db.bridge.commit_fault_role = "md_acceptor"
    pool = await asyncpg.create_pool(
        host=str(db.host),
        user="md_acceptor",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=2,
        command_timeout=getattr(request, "param", 2),
    )
    encryption = ContentEncryption(key.decode())
    tasks = TaskStore(pool, encryption, MemoryStore(pool, encryption), cover_new_inference=True)
    _, _, _, _, _, root, _, _, _, journal, journal_id = resources
    store = ResourceStore(pool, root, journal, journal_id, Fernet(key))
    inputs = ResourceInputs(store, tasks, key, LIMITS)
    await inputs.bind()
    path = tmp_path / "h"
    identity, ownership = owned(path)
    history = HistoryCoordinator(path, identity, ownership)
    await history.open(deadline=deadline())
    projection = AcceptanceProjection(identity, acceptor="md_acceptor", worker="md_app")
    s = SimpleNamespace(
        db=db,
        account=expected.account_id,
        expected=expected,
        tasks=tasks,
        inputs=inputs,
        pool=pool,
        path=path,
        identity=identity,
        ownership=ownership,
        history=history,
        projection=projection,
        accept=HistoryAcceptance(tasks, history, projection, KEY),
        channel_timeout=getattr(request, "param", 2),
    )
    try:
        yield s
    finally:
        await history.aclose(deadline=deadline())
        await asyncio.wait_for(pool.close(), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_real_c1_task_inputs_and_history_projection_commit_atomically(
    c1_scenario, monkeypatch, fail
):
    s = c1_scenario
    initial_tasks = int(s.db.sql("SELECT count(*) FROM tasks"))
    original = s.projection.stage
    observed = []

    async def stage(conn, entry, *, deadline):
        assert conn.is_in_transaction()
        assert await conn.fetchval("SELECT count(*) FROM resource_versions") == 3
        assert await conn.fetchval("SELECT count(*) FROM resource_publications") == 2
        assert await conn.fetchval("SELECT count(*) FROM resource_references") == 3
        assert await conn.fetchval("SELECT state FROM resource_input_batches") == "accepted"
        observed.append(entry.command.data.task_id)
        result = await original(conn, entry, deadline=deadline)
        if fail:
            raise ConnectionError("synthetic C1/H1 rollback after projection")
        return result

    monkeypatch.setattr(s.projection, "stage", stage)
    if fail:
        with pytest.raises(ResourceError) as caught:
            await s.accept.accept(deadline=deadline(), **submission(s.expected))
        assert isinstance(caught.value.__context__, ConnectionError)
    else:
        accepted = await s.accept.accept(deadline=deadline(), **submission(s.expected))
        assert accepted.created and observed == [accepted.task_id]
    assert len(observed) == len(entries(s)) == 1
    assert int(s.db.sql("SELECT count(*) FROM tasks")) == initial_tasks + int(not fail)
    for table, expected in (
        ("resource_versions", 3),
        ("resource_publications", 2),
        ("resource_references", 3),
        ("task_history_acceptance", 1),
    ):
        assert int(s.db.sql("SELECT count(*) FROM " + table)) == (0 if fail else expected)
    assert s.db.sql("SELECT state FROM resource_input_batches") == (
        "preparing" if fail else "accepted"
    )
    async with independent_owner(s) as other:
        with pytest.raises(HistoryError, match="historical_acceptance"):
            await other.accept(deadline=deadline(), **submission(s.expected))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
@pytest.mark.parametrize("c1_scenario", [2, None], indirect=True)
async def test_real_c1_wire_commit_loss_cannot_ack_or_replace(c1_scenario, monkeypatch, fault):
    s = c1_scenario
    initial = int(s.db.sql("SELECT count(*) FROM tasks"))
    original = s.projection.stage

    async def stage(conn, entry, *, deadline):
        result = await original(conn, entry, deadline=deadline)
        s.db.bridge.commit_fault_role = "md_acceptor"
        s.db.bridge.commit_fault = fault
        return result

    monkeypatch.setattr(s.projection, "stage", stage)
    error = ResourceError if s.channel_timeout is not None else TimeoutError
    try:
        with pytest.raises(error):
            async with asyncio.timeout(10):
                await s.accept.accept(
                    deadline=asyncio.get_running_loop().time() + 5, **submission(s.expected)
                )
        assert s.db.bridge.commit_seen.is_set()
        if fault == "after":
            assert s.db.bridge.discarded_commit_bytes > 0
    finally:
        s.db.bridge.commit_fault = None
    committed = fault == "after"
    assert int(s.db.sql("SELECT count(*) FROM tasks")) == initial + int(committed)
    for table, expected in (
        ("resource_versions", 3),
        ("resource_publications", 2),
        ("resource_references", 3),
        ("task_history_acceptance", 1),
    ):
        assert int(s.db.sql("SELECT count(*) FROM " + table)) == (expected if committed else 0)
    assert s.db.sql("SELECT state FROM resource_input_batches") == (
        "accepted" if committed else "preparing"
    )
    assert len(entries(s)) == 1
    async with independent_owner(s) as other:
        with pytest.raises(HistoryError, match="historical_acceptance"):
            await other.accept(deadline=deadline(), **submission(s.expected))


@pytest.mark.asyncio
async def test_c1_real_unique_failure_after_prepare_never_enters_readback(c1_scenario, monkeypatch):
    s = c1_scenario
    account = s.inputs._account
    original_stage = s.projection.stage
    calls = 0
    at_failure = None
    initial = int(s.db.sql("SELECT count(*) FROM tasks"))

    @asynccontextmanager
    async def counted(owner):
        nonlocal calls
        calls += 1
        async with account(owner) as conn:
            yield conn

    async def stage(conn, entry, *, deadline):
        nonlocal at_failure
        await original_stage(conn, entry, deadline=deadline)
        at_failure = calls
        # Real server-side uniqueness failure after H and SQL projection were
        # staged. The unchanged native C1 fallback would open another account
        # transaction; this composed attempt must stop after original rollback.
        await conn.execute(
            "INSERT INTO resource_input_batches SELECT * FROM resource_input_batches"
        )
        raise AssertionError("duplicate batch unexpectedly inserted")

    monkeypatch.setattr(s.inputs, "_account", counted)
    monkeypatch.setattr(s.projection, "stage", stage)
    with pytest.raises(ResourceError) as caught:
        await s.accept.accept(deadline=deadline(), **submission(s.expected))
    assert isinstance(caught.value.__context__, asyncpg.UniqueViolationError)
    assert at_failure is not None and calls == at_failure
    assert int(s.db.sql("SELECT count(*) FROM tasks")) == initial
    assert s.db.sql("SELECT count(*) FROM task_history_acceptance") == "0"
    assert s.db.sql("SELECT count(*) FROM resource_references") == "0"
    assert s.db.sql("SELECT state FROM resource_input_batches") == "preparing"
    assert len(entries(s)) == 1
