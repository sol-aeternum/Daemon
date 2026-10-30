"""Real owned PG/journal/POSIX read catalog and encrypted presentations."""

import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from orchestrator.resource_catalog import ResourceCatalog
from orchestrator.resource_presentations import ResourcePresentations
from orchestrator.resource_store import ResourceError
from orchestrator.services.account_control.guard import CapturedGeneration
from tests import test_resource_inputs_postgres as accepted
from tests.compute_metadata_support import ROOT, metadata_cluster as metadata_cluster

lifecycle_db = accepted.lifecycle_db
db = accepted.db
reservation_scenario = accepted.reservation_scenario
reservation_key = accepted.reservation_key
resource_scenario = accepted.resource_scenario
resource_key = accepted.resource_key
input_scenario = accepted.input_scenario


@pytest_asyncio.fixture
async def catalog_scenario(input_scenario):
    db, inputs, tasks, memory, expected, key, scenario = input_scenario
    db.sql("BEGIN;" + (ROOT / "migrations/055_resource_presentations.sql").read_text() + "COMMIT;")
    await inputs.bind()
    catalog = ResourceCatalog(inputs, scan_limit=100)
    presentations = ResourcePresentations(catalog, item_limit=20, record_limit=20)
    yield db, catalog, presentations, tasks, expected, key, scenario


@pytest.mark.asyncio
async def test_pages_watermark_equal_time_and_association_filters(catalog_scenario):
    db, catalog, _, tasks, expected, *_ = catalog_scenario
    for i in range(8):
        await tasks.accept(**accepted.submission(expected, key=f"key-{i}"))
    db.sql("UPDATE resource_versions SET created_at='2026-01-01T00:00:00Z'")
    first = await catalog.page(expected, {})
    assert len(first["items"]) == 20 and first["next_cursor"]
    assert all(i["reference"] is None for i in first["items"])
    await tasks.accept(**accepted.submission(expected, key="post-watermark"))
    second = await catalog.page(expected, {"cursor": first["next_cursor"]})
    assert len(second["items"]) == 4 and second["next_cursor"] is None
    assert not set(i["version_id"] for i in first["items"]) & set(
        i["version_id"] for i in second["items"]
    )
    async with db.connection() as conn:
        task_id = await conn.fetchval("SELECT accepted_task_id FROM resource_input_batches LIMIT 1")
    page = await catalog.page(expected, {"role": "input", "task_id": str(task_id)})
    assert len(page["items"]) == 3
    page = await catalog.page(expected, {"role": "output", "task_id": str(task_id)})
    assert page["items"] == []
    page = await catalog.page(expected, {}, task=task_id)
    assert [i["reference"]["ordinal"] for i in page["items"]] == [0, 1, 2]


@pytest.mark.asyncio
async def test_revision_availability_change_and_source_delete(catalog_scenario):
    db, catalog, _, tasks, expected, _, scenario = catalog_scenario
    task = await tasks.accept(**accepted.submission(expected))
    first = await catalog.page(expected, {"limit": "1", "availability": "available"})
    assert first["next_cursor"]
    # Actual immutable private copy corruption, no fake availability update.
    paths = list((scenario[5] / "private" / scenario[6]).iterdir())
    private = next(
        p for p in paths if p.name != "namespace.lock" and p.is_file() and p.stat().st_size > 0
    )
    private.write_bytes(b"corrupt")
    with pytest.raises(ResourceError, match="restart_required"):
        await catalog.page(
            expected, {"limit": "1", "availability": "available", "cursor": first["next_cursor"]}
        )
    # Separate fresh page detects source revision, regardless of query membership.
    before = await catalog.page(expected, {"limit": "1", "task_id": str(task.task_id)})
    assert await tasks.delete_conversation(expected.account_id, task.conversation_id) == (
        True,
        None,
    )
    with pytest.raises(ResourceError, match="restart_required"):
        await catalog.page(
            expected, {"limit": "1", "task_id": str(task.task_id), "cursor": before["next_cursor"]}
        )


@pytest.mark.asyncio
async def test_cursor_account_query_types_bounds_and_empty_account(catalog_scenario):
    db, catalog, _, tasks, expected, *_ = catalog_scenario
    await tasks.accept(**accepted.submission(expected))
    page = await catalog.page(expected, {"limit": "1"})
    for params in (
        {"limit": "2", "cursor": page["next_cursor"]},
        {"cursor": "x" * 9000},
        {"search": "private"},
        {"limit": "999"},
    ):
        with pytest.raises(ResourceError):
            await catalog.page(expected, params)
    async with db.connection() as conn:
        foreign = await db.account(conn)
    other = CapturedGeneration(foreign, 0)
    assert (await catalog.page(other, {}))["items"] == []
    with pytest.raises(ResourceError, match="invalid_cursor"):
        await catalog.page(other, {"limit": "1", "cursor": page["next_cursor"]})
    row = page["items"][0]
    with pytest.raises(ResourceError, match="not_found"):
        await catalog.metadata(other, UUID(row["resource_id"]), UUID(row["version_id"]))
    with pytest.raises(ResourceError, match="capacity"):
        await ResourceCatalog(catalog.inputs, scan_limit=1).page(expected, {})
    token = catalog.store._open(page["next_cursor"])
    token["generation"] = False
    with pytest.raises(ResourceError, match="invalid_cursor"):
        await catalog.page(expected, {"limit": "1", "cursor": catalog.store._seal(token)})


@pytest.mark.asyncio
async def test_presentations_actual_pins_replay_and_detachment(catalog_scenario):
    db, catalog, presentations, tasks, expected, _, scenario = catalog_scenario
    _, output, ident, command, *_ = scenario
    pub = await output.publish_output(ident, command.binding.intent_id, accepted.outputs.CONTENT)
    pins = [{"resource_id": str(pub.resource_id), "version_id": str(pub.version_id)}]
    first = await presentations.record(ident, pins)
    page = await presentations.page(expected, ident.task_id)
    assert page["items"][0]["items"] == pins
    assert set(page["items"][0]) == {
        "schema_version",
        "component_library_version",
        "snapshot_at",
        "component",
        "items",
    }
    db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    assert await presentations.record(ident, pins) == first
    async with db.connection() as conn:
        conv = await conn.fetchval("SELECT conversation_id FROM tasks WHERE id=$1", ident.task_id)
    db.sql(
        "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL"
    )
    assert await tasks.delete_conversation(expected.account_id, conv) == (True, None)
    assert db.sql("SELECT source_message_id IS NULL FROM resource_presentations") == "t"
    async with db.connection() as conn:
        token = await conn.fetchval("SELECT envelope_ciphertext FROM resource_presentations")
    assert catalog.store._open(token)["source_deleted"] is True
    assert (await output.read(expected, pub.resource_id, pub.version_id)).content


@pytest.mark.asyncio
async def test_foreign_presentation_pin_and_acl_refuse(catalog_scenario):
    db, _, presentations, _, _, _, scenario = catalog_scenario
    with pytest.raises(ResourceError):
        await presentations.record(
            scenario[2], [{"resource_id": str(uuid4()), "version_id": str(uuid4())}]
        )
    assert db.sql("SELECT count(*) FROM resource_presentations") == "0"
    assert "permission denied" in db.sql(
        "SELECT * FROM resource_presentations", role="md_app", success=False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_presentation_unknown_commit_fresh_original_resolution(
    catalog_scenario, fault, monkeypatch
):
    db, _, presentations, _, _, _, scenario = catalog_scenario
    _, output, ident, command, *_ = scenario
    pub = await output.publish_output(ident, command.binding.intent_id, accepted.outputs.CONTENT)
    original = output._live
    calls = 0

    async def armed(conn, identity):
        nonlocal calls
        await original(conn, identity)
        calls += 1
        if calls == 2:
            db.bridge.commit_fault = fault

    monkeypatch.setattr(presentations.store, "_live", armed)
    pins = [{"resource_id": str(pub.resource_id), "version_id": str(pub.version_id)}]
    try:
        async with asyncio.timeout(10):
            with pytest.raises(ResourceError):
                await presentations.record(ident, pins)
    finally:
        db.bridge.commit_fault = None
        monkeypatch.setattr(presentations.store, "_live", original)
    assert db.sql("SELECT count(*) FROM resource_presentations") == (
        "1" if fault == "after" else "0"
    )
    a = await presentations.record(ident, pins)
    assert await presentations.record(ident, pins) == a


@pytest.mark.asyncio
async def test_observed_fixture_memory_is_bounded_not_production_capacity(catalog_scenario):
    import resource
    import tracemalloc

    _, catalog, _, tasks, expected, *_ = catalog_scenario
    await tasks.accept(**accepted.submission(expected))
    tracemalloc.start()
    try:
        async with catalog.inputs._slot(expected.account_id):
            page = await catalog.page(expected, {})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(page["items"]) == 3 and peak < 16 * 1024 * 1024
    # Whole-process high-water RSS includes imports and PG bridge; report, not a
    # fabricated incremental RSS or 128MiB production-capacity qualification.
    print(
        f"owned tiny-input catalog: traced_peak_bytes={peak}, process_maxrss_kib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}"
    )


@pytest.mark.asyncio
async def test_presentation_detaches_with_zero_resource_references(catalog_scenario):
    db, _, presentations, tasks, expected, _, scenario = catalog_scenario
    identity = scenario[2]
    await presentations.record(identity, [])
    assert db.sql("SELECT count(*) FROM resource_references") == "0"
    async with db.connection() as conn:
        conversation = await conn.fetchval(
            "SELECT conversation_id FROM tasks WHERE id=$1", identity.task_id
        )
    db.sql(
        "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL"
    )
    assert await tasks.delete_conversation(expected.account_id, conversation) == (True, None)
    assert db.sql("SELECT source_message_id IS NULL FROM resource_presentations") == "t"


@pytest.mark.asyncio
async def test_presentation_last_slot_serializes_and_replay_survives_capacity(catalog_scenario):
    db, _, presentations, _, expected, _, scenario = catalog_scenario
    _, output, identity, command, *_ = scenario
    pub = await output.publish_output(identity, command.binding.intent_id, accepted.outputs.CONTENT)
    pins = [{"resource_id": str(pub.resource_id), "version_id": str(pub.version_id)}]
    presentations.record_limit = 1
    results = await asyncio.gather(
        presentations.record(identity, []),
        presentations.record(identity, pins),
        return_exceptions=True,
    )
    assert sum(isinstance(result, UUID) for result in results) == 1
    assert sum(isinstance(result, ResourceError) for result in results) == 1
    assert (
        str(next(result for result in results if isinstance(result, ResourceError))) == "capacity"
    )
    assert db.sql("SELECT count(*) FROM resource_presentations") == "1"
    winner = 0 if isinstance(results[0], UUID) else 1
    # The owned PG transport drains terminated connections on this event loop.
    # Use async SQL so the rejected writer's disconnect can release its locks.
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
    assert await presentations.record(identity, [[], pins][winner]) == results[winner]
    assert len((await presentations.page(expected, identity.task_id))["items"]) == 1


async def _long_presentation_conversation(catalog_scenario):
    db, catalog, presentations, tasks, expected, _, scenario = catalog_scenario
    presentations.record_limit = 2
    await presentations.record(scenario[2], [])
    async with db.connection() as conn:
        original = await conn.fetchrow("SELECT * FROM resource_presentations")
        conversation = await conn.fetchval(
            "SELECT conversation_id FROM tasks WHERE id=$1", scenario[2].task_id
        )
    template = catalog.store._open(original["envelope_ciphertext"])
    for i in range(4):
        db.sql(
            "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL"
        )
        task = await tasks.accept(
            **accepted.submission(expected, key=f"long-{i}", conversation=conversation)
        )
        # Seed committed encrypted empty-pin envelopes for separate real accepted
        # tasks. This fixture qualifies deletion, not producer admission.
        async with db.connection() as conn:
            message = await conn.fetchval(
                "SELECT result_message_id FROM tasks WHERE id=$1", task.task_id
            )
            value = {
                **template,
                "original_task_id": str(task.task_id),
                "original_message_id": str(message),
            }
            await conn.execute(
                "INSERT INTO resource_presentations VALUES($1,$2,$3,$4,$5,$6)",
                uuid4(),
                expected.account_id,
                expected.generation,
                message,
                catalog.store._seal(value),
                original["created_at"],
            )
    db.sql(
        "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL"
    )
    return conversation


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["tasks", "memory"])
@pytest.mark.parametrize("fault", [None, "late_corruption", "late_update"])
async def test_batched_presentation_deletion_atomicity(catalog_scenario, entry, fault):
    db, catalog, presentations, tasks, expected, *_ = catalog_scenario
    conversation = await _long_presentation_conversation(catalog_scenario)
    assert (
        db.sql(
            "SELECT max(n) FROM (SELECT count(*) n FROM resource_presentations GROUP BY source_message_id) x"
        )
        == "1"
    )
    async with db.connection() as conn:
        rows = await conn.fetch("SELECT * FROM resource_presentations ORDER BY presentation_id")
        last = rows[-1]["presentation_id"]
        if fault == "late_corruption":
            await conn.execute(
                "UPDATE resource_presentations SET envelope_ciphertext='invalid' WHERE presentation_id=$1",
                last,
            )
    if fault == "late_update":
        db.sql(
            "CREATE FUNCTION reject_last_detach() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"IF NEW.presentation_id='{last}'::uuid AND NEW.source_message_id IS NULL THEN "
            "RAISE EXCEPTION 'synthetic late update'; END IF; RETURN NEW; END $$;"
            "CREATE TRIGGER reject_last_detach BEFORE UPDATE ON resource_presentations "
            "FOR EACH ROW EXECUTE FUNCTION reject_last_detach();"
        )

    async def delete():
        if entry == "tasks":
            assert await tasks.delete_conversation(expected.account_id, conversation) == (
                True,
                None,
            )
        else:
            assert await tasks._memory.delete_conversation(
                conversation, user_id=expected.account_id
            )

    if fault:
        with pytest.raises(ResourceError):
            await delete()
        assert (
            db.sql(
                "SELECT count(*) FROM resource_presentations WHERE source_message_id IS NOT NULL"
            )
            == "5"
        )
        assert db.sql(f"SELECT count(*) FROM conversations WHERE id='{conversation}'") == "1"
        # The first pass must detect late corruption before even attempting an
        # update. For the update fault, the earlier successful updates rolled back.
    else:
        await delete()
        assert (
            db.sql("SELECT count(*) FROM resource_presentations WHERE source_message_id IS NULL")
            == "5"
        )
        async with db.connection() as conn:
            detached = await conn.fetch(
                "SELECT * FROM resource_presentations ORDER BY presentation_id"
            )
        for before, after in zip(rows, detached, strict=True):
            old = catalog.store._open(before["envelope_ciphertext"])
            new = catalog.store._open(after["envelope_ciphertext"])
            assert new == {**old, "source_deleted": True}


@pytest.mark.asyncio
async def test_pre_j1_schema_simulation_with_actual_c1_dependents(catalog_scenario):
    from tests.durable_tasks_support import Env
    from tests.test_deletion_journal_postgres import (
        test_whole_pre_j1_schema_distinct_from_partial_schema,
    )

    _, catalog, _, tasks, expected, *_ = catalog_scenario
    # Reuse the exact CI regression on the already owned, network-none public
    # schema with original C1 migrations; never consult TASKS_TEST_DATABASE_URL.
    env = Env(catalog.store._namespaces._pool, tasks, tasks._memory, expected.account_id, uuid4())
    await test_whole_pre_j1_schema_distinct_from_partial_schema(env)
