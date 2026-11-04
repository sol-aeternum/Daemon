"""H-owned restricted P/POSIX publisher; synthetic fixtures, no runtime host."""

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from orchestrator import resource_store as registry
from orchestrator.artifact_namespaces import NamespaceController
from orchestrator.artifact_reservations import (
    DescriptorCodec,
    JobBinding,
    IntentBinding,
    OutputReservations,
    SIGNATURES,
)
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.deletion_journal.store import initialize
from orchestrator.services.deletion_journal.upgrade import promote_container_v2
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.deletion_journal.errors import JournalError
from tests.test_history_effects_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    scenario as scenario,
    effects as effects,
    running,
    deadline,
    entries,
)
from tests.test_resource_output_presentations_postgres import SIGNATURES as PRESENTATIONS
from tests.test_resource_output_catalog_postgres import SIGNATURES as CATALOG
from tests.history_cleanup_support import held_reset
from tests.test_history_coordination import available

ROOT = Path(__file__).resolve().parents[1]
CONTENT = b"column\nsynthetic\n"


@pytest_asyncio.fixture
async def reserved(effects, tmp_path):
    s = effects
    for name in (
        "058_resource_output_presentations.sql",
        "059_resource_output_catalog.sql",
        "060_resource_output_namespace_inventory.sql",
    ):
        s.db.sql("BEGIN;" + (ROOT / "migrations" / name).read_text() + "COMMIT;")
    s.db.cluster.sql(
        "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='reserved_p') THEN CREATE ROLE reserved_p LOGIN; CREATE ROLE reserved_r LOGIN; END IF; END $$;"
    )
    s.db.sql(
        "GRANT USAGE ON SCHEMA public TO reserved_r,reserved_p;"
        + ";".join(
            "GRANT EXECUTE ON FUNCTION public.artifact_output_" + name + " TO reserved_r"
            for name in SIGNATURES[3:]
        )
        + ";"
        + ";".join(
            "GRANT EXECUTE ON FUNCTION " + name + " TO reserved_p"
            for name in (
                *PRESENTATIONS,
                *CATALOG,
                "public.resource_output_namespace_inventory(pg_catalog.uuid,pg_catalog.int4)",
            )
        )
    )
    await running(s)
    cipher = s.tasks._enc._cipher
    codec = DescriptorCodec(cipher)
    async with s.db.connection() as owner:
        attempt = await owner.fetchval(
            "SELECT id FROM task_attempts WHERE task_id=$1 AND epoch=$2",
            s.claim.task_id,
            s.claim.epoch,
        )
    job = JobBinding(uuid4(), s.account, 0, s.claim.task_id, uuid4())
    binding = IntentBinding(
        uuid4(), 0, uuid4(), uuid4(), s.attempt.lifecycle_id, attempt, s.claim.epoch, len(CONTENT)
    )
    s.command = codec.encode(
        job, binding, filename="synthetic.csv", sha256=hashlib.sha256(CONTENT).hexdigest()
    )
    reservation = OutputReservations(codec)
    async with s.db.connection("reserved_r") as conn, conn.transaction():
        await reservation.register_output_job(conn, s.attempt, job)
        await reservation.reserve_output_intent(conn, s.attempt, s.command)
    s.journal, s.journal_id = tmp_path / "j1", uuid4()
    initialize(s.journal, s.journal_id)
    promote_container_v2(s.journal, s.journal_id)
    s.root = tmp_path / "objects"
    s.root.mkdir(mode=0o700)
    for name in ("live", "private"):
        (s.root / name).mkdir(mode=0o700)
    async with s.db.connection() as owner:
        await owner.execute("INSERT INTO deletion_journal_binding VALUES(true,$1)", s.journal_id)
    owner_pool = await asyncpg.create_pool(
        host=str(s.db.host),
        user="md_owner",
        password="",
        database=s.db.name,
        ssl=False,
        min_size=0,
        max_size=2,
        command_timeout=2,
    )
    pool = await asyncpg.create_pool(
        host=str(s.db.host),
        user="reserved_p",
        password="",
        database=s.db.name,
        ssl=False,
        min_size=0,
        max_size=4,
        command_timeout=2,
    )
    try:
        namespace = await NamespaceController(
            owner_pool, s.root, s.journal, s.journal_id
        ).ensure_namespace(CapturedGeneration(s.account, 0))
        s.namespace = namespace.namespace
        s.native_store = registry.ResourceStore(owner_pool, s.root, s.journal, s.journal_id, cipher)
        s.publisher = registry.ReservedOutputPublisher(
            pool,
            s.root,
            s.journal,
            s.journal_id,
            cipher,
            inventory_limit=10,
            snapshot_byte_limit=400000,
        )
        s.db.bridge.commit_fault_role = "reserved_p"
        yield s
    finally:
        for value in (pool, owner_pool):
            try:
                async with asyncio.timeout(5):
                    await value.close()
            finally:
                value.terminate()


async def publish(s, *, content=CONTENT, identity=None):
    return await s.effects.publish_reserved_output(
        s.publisher,
        identity or s.attempt,
        s.command.binding.intent_id,
        content,
        deadline=deadline(),
    )


def rows(s):
    return s.db.sql(
        "SELECT (SELECT count(*) FROM resource_versions)||':'||(SELECT count(*) FROM resource_publications)||':'||(SELECT count(*) FROM resource_references)||':'||(SELECT count(*) FROM resource_presentations)"
    )


def files(s):
    # Namespace setup owns this pre-existing lock; count artifact bytes only.
    return sorted(
        p.relative_to(s.root).as_posix()
        for p in s.root.rglob("*")
        if p.is_file() and p.name != ".prepare.lock"
    )


@pytest.mark.asyncio
async def test_known_commit_encrypted_file_replay_and_no_added_authority(reserved):
    s = reserved
    before = entries(s)
    result = await publish(s)
    assert result.version_id == s.command.binding.version_id and rows(s) == "1:1:1:0"
    stored = s.publisher._stored(s.command, s.namespace)
    assert registry.read_verified(s.root, s.publisher._envelope, stored) == CONTENT
    native = await s.native_store.read(
        CapturedGeneration(s.account, 0), result.resource_id, result.version_id
    )
    assert native.content == CONTENT and native.publication == result
    assert files(s) and all(CONTENT not in p.read_bytes() for p in s.root.rglob("*") if p.is_file())
    s.db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    assert await publish(s) == result
    assert rows(s) == "1:1:1:0" and entries(s) == before
    async with s.db.connection("reserved_p") as conn:
        for table in (
            "tasks",
            "artifact_output_intents",
            "resource_publications",
            "task_history_projection",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchval("SELECT count(*) FROM " + table)
        assert not await conn.fetchval(
            "SELECT has_function_privilege(current_user,'artifact_output_register(jsonb,jsonb)','EXECUTE')"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "content",
        "snapshot_budget",
        "marker",
        "namespace",
        "suspended",
        "generation",
        "cancel",
        "origin",
        "foreign_identity",
    ],
)
async def test_refusal_before_file_or_catalog_effect_without_repair(reserved, change):
    s = reserved
    identity = s.attempt
    content = CONTENT
    if change == "content":
        content = b"changed"
    elif change == "snapshot_budget":
        s.publisher._snapshot_byte_limit = 1
    elif change == "marker":
        s.db.sql("DELETE FROM deletion_journal_applied")
    elif change == "namespace":
        s.db.sql("UPDATE artifact_namespaces SET state='uncertain'")
    elif change == "suspended":
        s.db.sql("UPDATE entitlement_accounts SET status='suspended'")
    elif change == "generation":
        s.db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    elif change == "cancel":
        s.db.sql("UPDATE tasks SET cancel_requested_at=clock_timestamp()")
    elif change == "origin":
        s.db.sql("UPDATE tasks SET input_ciphertext='invalid'")
    elif change == "foreign_identity":
        identity = replace(identity, account_id=uuid4())
    expected = {
        "content": (registry.ResourceError, "conflict"),
        "snapshot_budget": (registry.ResourceError, "snapshot_capacity"),
        "marker": (JournalError, "marker_mismatch"),
        "namespace": (asyncpg.PostgresError, "resource_output_refused"),
        "suspended": (asyncpg.PostgresError, "resource_output_refused"),
        "generation": (asyncpg.PostgresError, "resource_output_refused"),
        "cancel": (asyncpg.PostgresError, "lifecycle_refused"),
        "origin": (ValueError, "Invalid ciphertext"),
        "foreign_identity": (HistoryError, "effects_acceptance_refused"),
    }
    error, message = expected[change]
    with pytest.raises(error, match=message):
        await publish(s, content=content, identity=identity)
    assert rows(s) == "0:0:0:0" and not files(s)
    assert s.effects._blocked and s.history._blocked
    with pytest.raises(HistoryError, match="blocked"):
        await publish(s)


@pytest.mark.asyncio
async def test_historical_stop_refuses_before_file_or_catalog(reserved):
    s = reserved
    from tests.restore_history_support import KEY

    await s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=deadline())
    with pytest.raises(HistoryError, match="historical_stop"):
        await publish(s)
    assert rows(s) == "0:0:0:0" and not files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_lost_commit_acknowledgement_never_promotes_or_replaces(reserved, monkeypatch, fault):
    s = reserved
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
            await publish(s)
    finally:
        s.db.bridge.commit_fault = None
    assert fired and s.db.bridge.commit_seen.is_set()
    assert rows(s) == ("1:1:1:0" if fault == "after" else "0:0:0:0")
    assert files(s) and s.publisher._blocked and s.effects._blocked and s.history._blocked
    with pytest.raises(HistoryError, match="blocked"):
        await publish(s)


@pytest.mark.asyncio
async def test_stage_failure_preserves_orphan_without_completing_it(reserved, monkeypatch):
    s = reserved
    original = s.publisher._seal
    calls = 0

    def fail(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic stage boundary")
        return original(value)

    monkeypatch.setattr(s.publisher, "_seal", fail)
    with pytest.raises(RuntimeError, match="stage boundary"):
        await publish(s)
    assert files(s) and rows(s) == "0:0:0:0" and s.publisher._blocked
    with pytest.raises(HistoryError, match="blocked"):
        await publish(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline", "failure"])
async def test_actual_p_pool_reset_drains_inside_h_and_never_returns_late_success(
    reserved, monkeypatch, mode
):
    s = reserved
    async with held_reset(s.publisher._pool, monkeypatch, 1, mode == "failure") as (
        entered,
        proceed,
    ):
        task = asyncio.create_task(publish(s))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if mode == "cancel":
                task.cancel("original publisher cancellation")
                await asyncio.sleep(0.03)
                task.cancel("repeated publisher cancellation")
            elif mode == "deadline":
                await asyncio.sleep(2.05)
                task.cancel("repeated after deadline")
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
                assert caught.value.args == ("original publisher cancellation",)
            assert rows(s) == "1:1:1:0" and s.effects._blocked and available(s.path, "OP_LOCK")
            with pytest.raises(HistoryError, match="blocked"):
                await publish(s)
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "deadline"])
async def test_cancelled_physical_file_work_retains_h_and_sql_ownership(
    reserved, monkeypatch, mode
):
    s = reserved
    entered, proceed = threading.Event(), threading.Event()
    native = registry.prepare_verified

    def held(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return native(*args, **kwargs)

    monkeypatch.setattr(registry, "prepare_verified", held)
    task = asyncio.create_task(publish(s))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        if mode == "cancel":
            task.cancel("original file cancellation")
            await asyncio.sleep(0.03)
            task.cancel("repeat")
        else:
            await asyncio.sleep(2.05)
            task.cancel("repeated after physical deadline")
        await asyncio.sleep(0.03)
        assert (
            not task.done()
            and not available(s.path, "OP_LOCK")
            and s.publisher._active_task is not None
        )
        proceed.set()
        with pytest.raises(asyncio.CancelledError if mode == "cancel" else TimeoutError) as caught:
            await task
        if mode == "cancel":
            assert caught.value.args == ("original file cancellation",)
        assert rows(s) == "0:0:0:0" and files(s) and s.publisher._blocked
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_borrowed_savepoint_refused_without_catalog_or_files(reserved, monkeypatch):
    s = reserved
    native = asyncpg.Pool.acquire

    async def borrowed(pool, *args, **kwargs):
        conn = await native(pool, *args, **kwargs)
        if pool is s.publisher._pool:
            await conn.execute("BEGIN")
        return conn

    monkeypatch.setattr(asyncpg.Pool, "acquire", borrowed)
    with pytest.raises(registry.ResourceError, match="transaction"):
        await publish(s)
    assert rows(s) == "0:0:0:0" and not files(s) and s.publisher._blocked


@pytest.mark.asyncio
async def test_stop_waits_for_original_publication_then_prevents_next_effect(reserved, monkeypatch):
    s = reserved
    entered, proceed = threading.Event(), threading.Event()
    native = registry.prepare_verified

    def held(*args, **kwargs):
        result = native(*args, **kwargs)
        entered.set()
        assert proceed.wait(5)
        return result

    monkeypatch.setattr(registry, "prepare_verified", held)
    publication = asyncio.create_task(publish(s))
    stop = None
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        from tests.restore_history_support import KEY

        stop = asyncio.create_task(
            s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=deadline())
        )
        await asyncio.sleep(0.03)
        assert not stop.done() and not available(s.path, "OP_LOCK")
        proceed.set()
        assert (await publication).version_id == s.command.binding.version_id
        assert (await stop).accepted and rows(s) == "1:1:1:0"
        with pytest.raises(HistoryError, match="historical_stop"):
            await publish(s)
        assert rows(s) == "1:1:1:0"
    finally:
        proceed.set()
        await asyncio.gather(publication, *([stop] if stop else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_missing_committed_bytes_never_repairs_or_republishes(reserved):
    s = reserved
    await publish(s)
    # Owned synthetic fixture models a missing original file, not app cleanup.
    original = [
        p for p in (s.root / "private" / s.namespace).iterdir() if p.name != ".prepare.lock"
    ]
    assert len(original) == 1
    original[0].unlink()
    with pytest.raises(registry.StorageError):
        await publish(s)
    assert rows(s) == "1:1:1:0" and s.effects._blocked


@pytest.mark.asyncio
async def test_mismatched_encrypted_committed_provenance_refuses(reserved):
    s = reserved
    await publish(s)
    # Fixture owner can model trusted-catalog corruption; P cannot mutate tables.
    s.db.sql("UPDATE resource_references SET provenance_ciphertext='invalid'")
    with pytest.raises(registry.ResourceError):
        await publish(s)
    assert rows(s) == "1:1:1:0" and s.effects._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["accept", "send", "stop"])
async def test_restored_sql_missing_protected_history_refuses_before_p(reserved, monkeypatch, kind):
    s = reserved
    from tests.test_history_effects_postgres import mark
    from tests.restore_history_support import KEY

    if kind == "send":
        await mark(s)
    elif kind == "stop":
        await s.effects.request_cancel(s.account, s.claim.task_id, key=KEY, deadline=deadline())
    async with s.db.connection() as conn, conn.transaction():
        await conn.execute(
            "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable"
        )
        await conn.execute(
            "DELETE FROM task_history_projection WHERE seq=$1", 1 if kind == "accept" else 2
        )
        await conn.execute(
            "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable"
        )

    async def forbidden(*args, **kwargs):
        pytest.fail("restored H mismatch reached P snapshot")

    monkeypatch.setattr(s.publisher, "_reserved_snapshot", forbidden)
    with pytest.raises(
        HistoryError,
        match="projection_unavailable" if kind == "accept" else "effects_reconciliation_required",
    ):
        await publish(s)
    assert rows(s) == "0:0:0:0" and not files(s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "job_boolean",
        "extra_task",
        "orphan_version",
        "orphan_reference",
        "namespace_digest",
        "duplicate_key",
    ],
)
async def test_closed_snapshot_decoder_refuses_injected_inconsistency_before_files(
    reserved, monkeypatch, damage
):
    s = reserved
    native = asyncpg.Connection.fetchval

    async def fetch(conn, query, *args, **kwargs):
        result = await native(conn, query, *args, **kwargs)
        if (
            query.startswith("WITH observed AS MATERIALIZED")
            and "resource_output_catalog_" in query
        ):
            assert isinstance(result, str)
            raw = json.loads(result)
            if damage == "job_boolean":
                raw["job"]["account_generation"] = False
            elif damage == "extra_task":
                raw["task"]["decoded_input"] = "untrusted"
            elif damage == "orphan_version":
                raw["version"] = {}
            elif damage == "orphan_reference":
                raw["output_references"] = [{}]
            elif damage == "namespace_digest":
                raw["namespace"]["birth_digest"] = "\\x" + "00" * 32
            result = json.dumps(raw)
            if damage == "duplicate_key":
                result = '{"source_kind":"output",' + result[1:]
        return result

    monkeypatch.setattr(asyncpg.Connection, "fetchval", fetch)
    with pytest.raises(
        registry.ResourceError,
        match="namespace" if damage == "namespace_digest" else "invalid_snapshot",
    ):
        await publish(s)
    assert rows(s) == "0:0:0:0" and not files(s) and s.effects._blocked


@pytest.mark.asyncio
async def test_contradictory_stage_result_aborts_without_readback_promotion(reserved, monkeypatch):
    s = reserved
    native = asyncpg.Connection.fetchval

    async def fetch(conn, query, *args, **kwargs):
        result = await native(conn, query, *args, **kwargs)
        if query.startswith("SELECT public.resource_output_catalog_stage"):
            assert isinstance(result, str)
            raw = json.loads(result)
            raw["version_id"] = str(uuid4())
            result = json.dumps(raw)
        return result

    monkeypatch.setattr(asyncpg.Connection, "fetchval", fetch)
    with pytest.raises(registry.ResourceError, match="invalid_snapshot"):
        await publish(s)
    assert rows(s) == "0:0:0:0" and files(s) and s.publisher._blocked


@pytest.mark.asyncio
async def test_existing_uncommitted_files_are_evidence_not_missing_phase_authority(reserved):
    s = reserved
    stored = s.publisher._stored(s.command, s.namespace)
    registry.prepare_verified(s.root, s.publisher._envelope, stored, CONTENT)
    before = files(s)
    with pytest.raises(registry.StorageError, match="needs_reconciliation"):
        await publish(s)
    assert files(s) == before and rows(s) == "0:0:0:0" and s.publisher._blocked


@pytest.mark.asyncio
async def test_explicit_snapshot_and_admit_branches_observe_same_original_locked_state(reserved):
    s = reserved
    from orchestrator.services.connection_cleanup import retain_pool_cleanup

    async with s.history.operation(deadline=deadline()), retain_pool_cleanup():
        async with s.publisher._reserved_transaction() as conn:
            observed = await s.publisher._reserved_snapshot(
                conn, s.attempt, s.command.binding.intent_id
            )
            admitted = await s.publisher._reserved_snapshot(
                conn, s.attempt, s.command.binding.intent_id, admit=True
            )
            assert observed == admitted
            assert observed["_command"] == s.command
            assert observed["publication"] is None and not observed["output_references"]
    assert rows(s) == "0:0:0:0" and not files(s)


@pytest.mark.asyncio
async def test_expired_snapshot_observation_cannot_fall_back_from_live_admission(reserved):
    s = reserved
    from orchestrator.services.connection_cleanup import retain_pool_cleanup

    s.db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
    observed = None
    with pytest.raises(asyncpg.PostgresError, match="lifecycle_refused"):
        async with s.history.operation(deadline=deadline()), retain_pool_cleanup():
            async with s.publisher._reserved_transaction() as conn:
                observed = await s.publisher._reserved_snapshot(
                    conn, s.attempt, s.command.binding.intent_id
                )
                await s.publisher._reserved_snapshot(
                    conn, s.attempt, s.command.binding.intent_id, admit=True
                )
    assert observed is not None and observed["_command"] == s.command
    assert rows(s) == "0:0:0:0" and not files(s) and s.publisher._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["result_message", "conversation"])
async def test_locked_output_source_must_match_original_history_acceptance(reserved, changed):
    s = reserved
    conversation = s.claim.conversation_id if changed == "result_message" else uuid4()
    message = uuid4()
    # Owned fixture owner models contradictory/restored native source pointers;
    # both new targets remain legitimate same-account rows under production FKs.
    # P cannot change these rows, and retained H/projection facts remain untouched.
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
        await publish(s)
    assert rows(s) == "0:0:0:0" and not files(s)
    assert s.effects._blocked and s.publisher._blocked
