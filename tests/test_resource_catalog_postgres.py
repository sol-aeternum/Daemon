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
