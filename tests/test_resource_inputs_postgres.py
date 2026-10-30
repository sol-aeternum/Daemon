"""Actual C1 batch + task + POSIX acceptance on owned isolated PostgreSQL only."""

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import asyncpg
from cryptography.fernet import Fernet
import pytest
import pytest_asyncio

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.resource_inputs import ResourceInputs
from orchestrator.resource_store import ResourceError, ResourceStore
from orchestrator.services.account_control.guard import WriteGuardRefusal
from orchestrator.tasks.inputs import chat_request_fingerprint
from orchestrator.tasks.store import ConversationBusy, IdempotencyConflict, TaskNotFound, TaskStore
from tests import test_resource_store_postgres as outputs
from tests.compute_metadata_support import ROOT, metadata_cluster as metadata_cluster
from tests.test_resource_input_codec import LIMITS, attachments

lifecycle_db = outputs.lifecycle_db
db = outputs.db
reservation_scenario = outputs.reservation_scenario
reservation_key = outputs.reservation_key
resource_scenario = outputs.resource_scenario
resource_key = outputs.resource_key


@pytest_asyncio.fixture
async def input_scenario(resource_scenario, resource_key):
    db, previous, ident, _, expected, root, _, _, _, journal, journal_id = resource_scenario
    db.sql("BEGIN;" + (ROOT / "migrations/054_resource_input_batches.sql").read_text() + "COMMIT;")
    key = resource_key
    pool = previous._namespaces._pool
    memory = MemoryStore(pool, ContentEncryption(key.decode()))
    tasks = TaskStore(pool, memory.encryption, memory)
    store = ResourceStore(pool, root, journal, journal_id, Fernet(key))
    inputs = ResourceInputs(store, tasks, key, LIMITS)
    await inputs.verify_lookup_identity()
    yield db, inputs, tasks, memory, expected, key, resource_scenario


def submission(
    expected,
    *,
    key: str | None = "submission",
    pepper="synthetic-pepper",
    message="hello",
    conversation=None,
) -> dict[str, Any]:
    fingerprint = chat_request_fingerprint(
        key=pepper,
        conversation_id=conversation,
        message=message,
        attachments=attachments(),
        model=None,
        provider=None,
        metadata=None,
        disable_memory_write=True,
    )
    return dict(
        user_id=expected.account_id,
        conversation_id=conversation,
        new_conversation_title=None,
        pipeline="cloud",
        user_message=message,
        task_input={"original": "processing unchanged"},
        request_hash=fingerprint.digest,
        idempotency_key=key,
        assistant_model=None,
        request_canonical=fingerprint.canonical,
        captured_generation=expected,
    )


@pytest.mark.asyncio
async def test_real_atomic_acceptance_ordered_refs_and_pepper_replay(input_scenario):
    db, inputs, tasks, memory, expected, *_ = input_scenario
    args = submission(expected)
    task = await inputs.accept(**args)
    assert task.created
    assert db.sql("SELECT count(*) FROM resource_versions") == "3"
    assert db.sql("SELECT count(*) FROM resource_publications") == "2"
    assert (
        db.sql("SELECT string_agg(ordinal::text,',' ORDER BY ordinal) FROM resource_references")
        == "0,1,2"
    )
    assert db.sql("SELECT insertion_counter FROM resource_catalog_heads") == "3"
    assert db.sql("SELECT state FROM resource_input_batches") == "accepted"
    assert (
        db.sql(
            "SELECT measured_bytes IS NULL FROM resource_versions WHERE representation='metadata_only'"
        )
        == "t"
    )
    async with db.connection() as owner:
        snapshot = tasks._open(
            await owner.fetchval("SELECT input_ciphertext FROM tasks WHERE id=$1", task.task_id)
        )
    assert snapshot["_request"] == args["request_canonical"]
    assert snapshot["origin_account_generation"] == expected.generation
    assert snapshot["original"] == "processing unchanged"
    async with db.connection() as owner:
        rows = await owner.fetch("SELECT * FROM resource_versions ORDER BY insertion_ordinal")
    for row, original in zip(rows, attachments(), strict=True):
        if row["representation"] == "metadata_only":
            with pytest.raises(ResourceError, match="original_not_received"):
                await inputs._store.read(expected, row["resource_id"], row["version_id"])
        else:
            content = await inputs._store.read(expected, row["resource_id"], row["version_id"])
            if row["representation"] == "submitted_text":
                assert content.content == original["text_content"].encode()
            await inputs._store.repair(expected, row["resource_id"], row["version_id"])
    replay = await inputs.accept(**submission(expected, pepper="rotated-pepper"))
    assert replay.task_id == task.task_id and not replay.created
    with pytest.raises(IdempotencyConflict):
        await inputs.accept(**submission(expected, message="different canonical"))
    assert db.sql("SELECT count(*) FROM resource_versions") == "3"


@pytest.mark.asyncio
async def test_parallel_identical_and_conflicting_submissions(input_scenario):
    db, inputs, _, _, expected, *_ = input_scenario
    args = submission(expected)
    first, second = await asyncio.gather(inputs.accept(**args), inputs.accept(**args))
    assert first.task_id == second.task_id
    assert db.sql("SELECT count(*) FROM resource_input_batches") == "1"
    other = await asyncio.gather(
        inputs.accept(**args),
        inputs.accept(**submission(expected, message="different")),
        return_exceptions=True,
    )
    assert isinstance(other[1], IdempotencyConflict)


@pytest.mark.asyncio
async def test_no_key_fresh_submissions_and_original_batch_resume(input_scenario):
    db, inputs, _, _, expected, *_ = input_scenario
    args = submission(expected, key=None)
    batch = await inputs.reserve(expected, args["request_canonical"], None)
    task = await inputs.accept(**args, batch_id=batch)
    assert (await inputs.accept(**args, batch_id=batch)).task_id == task.task_id
    distinct = await inputs.accept(**args)
    assert distinct.task_id != task.task_id
    assert db.sql("SELECT count(*) FROM resource_input_batches") == "2"


@pytest.mark.asyncio
async def test_preparing_identity_survives_pepper_not_encryption_rotation(input_scenario):
    db, inputs, tasks, memory, expected, key, scenario = input_scenario
    args = submission(expected)
    batch = await inputs.reserve(expected, args["request_canonical"], args["idempotency_key"])
    assert (
        await inputs.reserve(
            expected, submission(expected, pepper="rotated")["request_canonical"], "submission"
        )
        == batch
    )
    _, old, _, _, _, root, _, _, _, journal, journal_id = scenario
    new_key = Fernet.generate_key()
    pool = old._namespaces._pool
    new_memory = MemoryStore(pool, ContentEncryption(new_key.decode()))
    changed = ResourceInputs(
        ResourceStore(pool, root, journal, journal_id, Fernet(new_key)),
        TaskStore(pool, new_memory.encryption, new_memory),
        new_key,
        LIMITS,
    )
    with pytest.raises(ResourceError, match="lookup_key_changed"):
        await changed.verify_lookup_identity()
    assert db.sql("SELECT count(*) FROM resource_input_batches") == "1"


@pytest.mark.asyncio
async def test_original_capture_stale_after_preparation_no_task_ack(input_scenario):
    db, inputs, _, _, expected, *_ = input_scenario
    args = submission(expected)
    batch = await inputs.reserve(expected, args["request_canonical"], "submission")
    await inputs.prepare(expected, batch, args["request_canonical"])
    db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    with pytest.raises(WriteGuardRefusal):
        await inputs.accept(**args)
    assert db.sql("SELECT state FROM resource_input_batches") == "preparing"
    assert db.sql("SELECT count(*) FROM resource_references") == "0"


@pytest.mark.asyncio
async def test_busy_conversation_keeps_tracked_ids_and_no_partial_task(input_scenario):
    db, inputs, _, _, expected, _, scenario = input_scenario
    ident = scenario[2]
    async with db.connection() as owner:
        conversation = await owner.fetchval(
            "SELECT conversation_id FROM tasks WHERE id=$1", ident.task_id
        )
    args = submission(expected, conversation=conversation)
    with pytest.raises(ConversationBusy):
        await inputs.accept(**args)
    assert db.sql("SELECT count(*) FROM resource_versions") == "0"
    batch_before = db.sql("SELECT batch_id::text||task_id::text FROM resource_input_batches")
    db.sql(
        "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL"
    )
    task = await inputs.accept(**args)
    assert task.created
    assert (
        db.sql("SELECT batch_id::text||task_id::text FROM resource_input_batches") == batch_before
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_actual_final_commit_loss_preserves_original_atomic_identity(input_scenario, fault):
    db, inputs, _, _, expected, *_ = input_scenario
    args = submission(expected)
    batch = await inputs.reserve(expected, args["request_canonical"], "submission")
    await inputs.prepare(expected, batch, args["request_canonical"])
    # Arm ONLY at actual final task insertion, after preliminary read/preparation commits.
    original = inputs._tasks._insert_accepted

    async def armed(*a, **kw):
        pending = await original(*a, **kw)
        db.bridge.commit_fault = fault
        return pending

    inputs._tasks._insert_accepted = armed
    try:
        async with asyncio.timeout(10):
            with pytest.raises(ResourceError, match="uncertain"):
                await inputs.accept(**args, batch_id=batch)
        assert db.bridge.commit_seen.is_set()
    finally:
        db.bridge.commit_fault = None
        inputs._tasks._insert_accepted = original
    assert db.sql("SELECT count(*) FROM resource_versions") == ("3" if fault == "after" else "0")
    resolved = await inputs.accept(**args, batch_id=batch)
    assert str(resolved.task_id) == db.sql("SELECT task_id FROM resource_input_batches")
    assert db.sql("SELECT count(*) FROM resource_references") == "3"


@pytest.mark.asyncio
async def test_schema_state_nullability_and_acl_refuse(input_scenario):
    db, inputs, _, _, expected, *_ = input_scenario
    args = submission(expected)
    await inputs.reserve(expected, args["request_canonical"], "submission")
    async with db.connection() as owner:
        for sql in (
            "UPDATE resource_input_batches SET request_ciphertext=NULL",
            "UPDATE resource_input_batches SET state='source_deleted'",
            "UPDATE resource_input_batches SET state='accepted'",
            "UPDATE resource_input_batches SET lookup_key_id='short'::bytea",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                async with owner.transaction():
                    await owner.execute(sql)
    for table in ("resource_input_batches", "resource_input_intents"):
        assert "permission denied" in db.sql(f"SELECT * FROM {table}", role="md_app", success=False)


@pytest.mark.asyncio
async def test_deleted_key_never_recreates_or_acknowledges_missing_task(input_scenario):
    db, inputs, _, _, expected, *_ = input_scenario
    args = submission(expected)
    await inputs.accept(**args)
    # SQL marker setup only. Concrete source-deletion paths qualify separately.
    db.sql(
        "UPDATE resource_input_batches SET state='source_deleted',request_ciphertext=NULL,accepted_task_id=NULL"
    )
    for candidate in (args, submission(expected, message="changed")):
        with pytest.raises(IdempotencyConflict):
            await inputs.accept(**candidate)
    assert db.sql("SELECT count(*) FROM resource_input_batches") == "1"


@pytest.mark.asyncio
async def test_foreign_and_wrong_accepted_task_pointers_cannot_ack(input_scenario):
    db, inputs, _, _, expected, _, scenario = input_scenario
    args = submission(expected)
    accepted = await inputs.accept(**args)
    # Same-account task with different original input must not satisfy the pointer.
    other_task = scenario[2].task_id
    db.sql(
        f"UPDATE resource_input_batches SET task_id='{other_task}',accepted_task_id='{other_task}'"
    )
    with pytest.raises(ResourceError):
        await inputs.accept(**args)
    async with db.connection() as owner:
        foreign = await db.account(owner)
        await owner.execute("UPDATE tasks SET user_id=$2 WHERE id=$1", other_task, foreign)
    with pytest.raises(ResourceError):
        await inputs.accept(**args)
    assert accepted.task_id != other_task


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["tasks", "memory"])
async def test_actual_store_chat_delete_erases_canonical_and_retains_resources(
    input_scenario, path
):
    db, inputs, tasks, memory, expected, *_ = input_scenario
    await inputs.bind()
    args = submission(expected)
    task = await tasks.accept(**args)
    async with db.connection() as owner:
        versions = await owner.fetch("SELECT * FROM resource_versions ORDER BY insertion_ordinal")
    if path == "tasks":
        assert await tasks.delete_conversation(expected.account_id, task.conversation_id) == (
            True,
            None,
        )
    else:
        assert await memory.delete_conversation(task.conversation_id, user_id=expected.account_id)
    assert db.sql("SELECT count(*) FROM resource_versions") == "3"
    assert (
        db.sql(
            "SELECT request_ciphertext IS NULL AND accepted_task_id IS NULL AND state='source_deleted' FROM resource_input_batches"
        )
        == "t"
    )
    assert (
        db.sql(
            "SELECT bool_and(task_id IS NULL AND conversation_id IS NULL AND message_id IS NULL) FROM resource_references"
        )
        == "t"
    )
    assert db.sql("SELECT filter_revision FROM resource_catalog_heads") == "1"
    async with db.connection() as owner:
        provenance = [
            inputs._store._open(r["provenance_ciphertext"])
            for r in await owner.fetch("SELECT * FROM resource_references")
        ]
    assert all(
        p["source_deleted"] and p["original_task_id"] == str(task.task_id) for p in provenance
    )
    assert all("hello" not in str(p) for p in provenance)
    for row in versions:
        if row["representation"] != "metadata_only":
            assert (
                await inputs._store.read(expected, row["resource_id"], row["version_id"])
            ).content
    await inputs.verify_lookup_identity()  # Restart identity check includes deleted markers.
    for candidate in (args, submission(expected, message="changed")):
        with pytest.raises(IdempotencyConflict):
            await tasks.accept(**candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["tasks", "memory"])
async def test_actual_deletion_running_409_preserves_canonical_and_links(input_scenario, path):
    db, inputs, tasks, memory, expected, *_ = input_scenario
    await inputs.bind()
    task = await tasks.accept(**submission(expected))
    async with db.connection() as owner:
        await owner.execute(
            "UPDATE tasks SET status='running',lease_owner='synthetic-worker',lease_expires_at=clock_timestamp()+interval '1 minute' WHERE id=$1",
            task.task_id,
        )
    if path == "tasks":
        assert await tasks.delete_conversation(expected.account_id, task.conversation_id) == (
            False,
            task.task_id,
        )
    else:
        with pytest.raises(ConversationBusy):
            await memory.delete_conversation(task.conversation_id, user_id=expected.account_id)
    assert (
        db.sql(
            "SELECT state='accepted' AND request_ciphertext IS NOT NULL FROM resource_input_batches"
        )
        == "t"
    )
    assert db.sql("SELECT bool_and(task_id IS NOT NULL) FROM resource_references") == "t"
    assert db.sql("SELECT filter_revision FROM resource_catalog_heads") == "0"


@pytest.mark.asyncio
async def test_foreign_input_read_and_deletion_owner_denied(input_scenario, monkeypatch):
    db, inputs, tasks, memory, expected, *_ = input_scenario
    await inputs.bind()
    task = await tasks.accept(**submission(expected))
    async with db.connection() as owner:
        other = await db.account(owner)
        row = await owner.fetchrow("SELECT * FROM resource_versions LIMIT 1")
    with pytest.raises(TaskNotFound):
        await memory.delete_conversation(task.conversation_id, user_id=other)
    with pytest.raises(ValueError, match="owner_required"):
        await memory.delete_conversation(task.conversation_id)
    from orchestrator.services.account_control.guard import CapturedGeneration

    with pytest.raises(ResourceError, match="not_found"):
        await inputs._store.read(
            CapturedGeneration(other, 0), row["resource_id"], row["version_id"]
        )
    assert db.sql("SELECT count(*) FROM resource_references") == "3"


@pytest.mark.asyncio
async def test_source_detachment_rollback_is_atomic(input_scenario):
    db, inputs, tasks, memory, expected, *_ = input_scenario
    await inputs.bind()
    task = await tasks.accept(**submission(expected))
    db.sql(
        "CREATE FUNCTION refuse_source_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$;"
        "CREATE TRIGGER refuse_source_delete BEFORE DELETE ON conversations FOR EACH ROW EXECUTE FUNCTION refuse_source_delete();"
    )
    with pytest.raises(ResourceError):
        await tasks.delete_conversation(expected.account_id, task.conversation_id)
    assert (
        db.sql(
            "SELECT state='accepted' AND request_ciphertext IS NOT NULL FROM resource_input_batches"
        )
        == "t"
    )
    assert db.sql("SELECT bool_and(task_id IS NOT NULL) FROM resource_references") == "t"
    assert db.sql("SELECT filter_revision FROM resource_catalog_heads") == "0"


@pytest.mark.asyncio
async def test_output_survives_real_taskstore_detachment_too(input_scenario):
    db, inputs, tasks, _, expected, _, scenario = input_scenario
    _, output, ident, command, *_ = scenario
    pub = await output.publish_output(ident, command.binding.intent_id, outputs.CONTENT)
    await inputs.bind()
    async with db.connection() as owner:
        conv = await owner.fetchval("SELECT conversation_id FROM tasks WHERE id=$1", ident.task_id)
        await owner.execute(
            "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL WHERE id=$1",
            ident.task_id,
        )
    assert await tasks.delete_conversation(expected.account_id, conv) == (True, None)
    assert (await output.read(expected, pub.resource_id, pub.version_id)).content == outputs.CONTENT


@pytest.mark.asyncio
async def test_cancel_preparation_drains_io_and_keeps_only_tracked_stages(
    input_scenario, monkeypatch
):
    from orchestrator import resource_inputs as module

    db, inputs, _, _, expected, *_ = input_scenario
    args = submission(expected)
    batch = await inputs.reserve(expected, args["request_canonical"], "submission")
    started, release = threading.Event(), threading.Event()
    original = module.prepare_verified

    def paused(*a):
        started.set()
        if not release.wait(5):
            raise OSError("synthetic_timeout")
        return original(*a)

    monkeypatch.setattr(module, "prepare_verified", paused)
    producer = asyncio.create_task(inputs.prepare(expected, batch, args["request_canonical"]))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        producer.cancel()
        await asyncio.sleep(0)
        assert not producer.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await producer
    assert db.sql("SELECT count(*) FROM resource_versions") == "0"
    assert db.sql("SELECT count(*) FROM resource_input_intents") == "3"


@pytest.mark.asyncio
async def test_capacity_refusal_before_effects_and_no_key_change_bypass(input_scenario):
    db, inputs, _, _, expected, *_ = input_scenario
    async with inputs._slot(expected.account_id), inputs._slot(expected.account_id):
        with pytest.raises(ResourceError, match="capacity"):
            await inputs.accept(**submission(expected))
    assert db.sql("SELECT count(*) FROM resource_input_batches") == "0"
    args = submission(expected, key=None)
    await inputs.accept(**args)
    db.sql("UPDATE resource_input_batches SET lookup_key_id=decode(repeat('ff',32),'hex')")
    with pytest.raises(ResourceError, match="lookup_key_changed"):
        await inputs.accept(**submission(expected, key="fresh-key"))
    assert db.sql("SELECT count(*) FROM resource_input_batches") == "1"


@pytest.mark.asyncio
async def test_pending_submission_chat_erasure_and_default_factory_cannot_bypass(input_scenario):
    from fastapi import HTTPException
    from orchestrator.routes.tasks import task_store
    from orchestrator.services.account_control.transaction import ObservationBlocked

    db, inputs, tasks, memory, expected, _, scenario = input_scenario
    await inputs.bind()
    async with db.connection() as owner:
        conv = await owner.fetchval(
            "SELECT conversation_id FROM tasks WHERE id=$1", scenario[2].task_id
        )
    args = submission(expected, conversation=conv)
    with pytest.raises(ConversationBusy):
        await tasks.accept(**args)
    db.sql(
        "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL"
    )
    assert await tasks.delete_conversation(expected.account_id, conv) == (True, None)
    assert (
        db.sql(
            "SELECT state='source_deleted' AND request_ciphertext IS NULL FROM resource_input_batches"
        )
        == "t"
    )
    with pytest.raises(IdempotencyConflict):
        await tasks.accept(**args)
    state: Any = SimpleNamespace(task_composition=None, db_pool=tasks._pool, memory_store=memory)
    assert task_store(state) is tasks
    state.db_pool = object()
    with pytest.raises(HTTPException) as error:
        task_store(state)
    assert error.value.status_code == 503
    unbound = TaskStore(tasks._pool, memory.encryption, memory)
    with pytest.raises(ObservationBlocked):
        await unbound.accept(**submission(expected, key="not-a-bypass"))
    with pytest.raises(ObservationBlocked):
        await unbound.delete_conversation(expected.account_id, conv)


@pytest.mark.asyncio
async def test_default_uncomposed_acceptance_origin_and_pepper_replay_unchanged(input_scenario):
    db, _, tasks, _, expected, *_ = input_scenario
    task = await tasks.accept(**submission(expected))
    rotated = submission(expected, pepper="rotated")
    rotated["captured_generation"] = None
    replay = await tasks.accept(**rotated)
    assert replay.task_id == task.task_id and not replay.created
    assert db.sql("SELECT count(*) FROM resource_input_batches") == "0"
    assert await tasks.delete_conversation(expected.account_id, task.conversation_id) == (
        True,
        None,
    )


@pytest.mark.asyncio
async def test_composed_actual_inference_role_preflight_not_bypassed(input_scenario):
    from orchestrator.services.account_control.transaction import ObservationBlocked

    db, inputs, tasks, _, expected, *_ = input_scenario
    tasks._cover_new_inference = True
    with pytest.raises(ObservationBlocked):
        await inputs.accept(**submission(expected))
    assert db.sql("SELECT count(*) FROM resource_versions") == "0"
    assert db.sql("SELECT state FROM resource_input_batches") == "preparing"


@pytest.mark.asyncio
async def test_native_early_replay_waits_for_delete_then_conflicts(input_scenario, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from uuid import uuid4
    from orchestrator.auth import AuthenticatedDevice
    from orchestrator.config import Settings
    from orchestrator.db import AppState
    from orchestrator.main import _durable_replay
    from orchestrator.models import ChatRequest

    db, inputs, tasks, memory, expected, key, *_ = input_scenario
    await inputs.bind()
    pepper = "synthetic-pepper-for-native-replay-only-0000"
    task = await tasks.accept(**submission(expected, pepper=pepper))
    settings_kwargs: dict[str, Any] = {
        "_env_file": None,
        "daemon_auth_pepper": pepper,
        "daemon_encryption_key": key.decode(),
    }
    settings = Settings(**settings_kwargs)
    state = AppState(settings=settings, db_pool=tasks._pool, memory_store=memory)
    auth = AuthenticatedDevice(expected.account_id, uuid4(), uuid4())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/chat",
            "headers": [(b"idempotency-key", b"submission")],
        }
    )
    payload = ChatRequest(message="hello", attachments=attachments(), disable_memory_write=True)
    response = await _durable_replay(payload, request, settings, state, auth)
    assert response is not None and response.headers["X-Daemon-Task-Id"] == str(task.task_id)
    started, release = asyncio.Event(), asyncio.Event()
    original = inputs._detach

    async def paused(*a):
        await original(*a)  # Actual joined writes remain uncommitted under account lock.
        started.set()
        await release.wait()

    monkeypatch.setattr(inputs, "_detach", paused)
    deletion = asyncio.create_task(
        tasks.delete_conversation(expected.account_id, task.conversation_id)
    )
    replay = None
    try:
        await asyncio.wait_for(started.wait(), 3)
        replay = asyncio.create_task(_durable_replay(payload, request, settings, state, auth))
        async with db.connection() as observer:
            async with asyncio.timeout(3):
                while not await observer.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)"
                ):
                    await asyncio.sleep(0.005)
        assert not replay.done()
    finally:
        release.set()
        assert await deletion == (True, None)
    assert replay is not None
    with pytest.raises(HTTPException) as error:
        await replay
    assert error.value.status_code == 409
    assert db.sql("SELECT count(*) FROM resource_versions") == "3"


@pytest.mark.asyncio
async def test_forged_boolean_generation_refused_before_preparation(input_scenario):
    from orchestrator.services.account_control.guard import CapturedGeneration

    db, inputs, _, _, expected, _, scenario = input_scenario
    args = submission(expected)
    batch = await inputs.reserve(expected, args["request_canonical"], "submission")
    forged = CapturedGeneration(expected.account_id, expected.generation)
    object.__setattr__(forged, "generation", False)  # False compares equal to SQL generation0.
    with pytest.raises(WriteGuardRefusal, match="invalid_capture"):
        await inputs.reserve(forged, args["request_canonical"], "submission")
    with pytest.raises(WriteGuardRefusal, match="invalid_capture"):
        await inputs.prepare(forged, batch, args["request_canonical"])
    with pytest.raises(WriteGuardRefusal, match="invalid_capture"):
        await inputs._prepare(forged, batch, args["request_canonical"])
    assert not list((scenario[5] / "live" / scenario[6] / "staging").iterdir())
    assert db.sql("SELECT count(*) FROM resource_versions") == "0"
