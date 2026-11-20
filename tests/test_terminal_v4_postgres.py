"""Explicit v4 migration and native transactions on a private synthetic cluster."""

from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import time
from uuid import uuid4

import asyncpg
from asyncpg.transaction import Transaction
import pytest
import pytest_asyncio

from orchestrator.auth import AuthenticatedDevice
from orchestrator.services.account_control.transaction import TransactionUnavailable
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.model import (
    Command,
    HistoryError,
    NonsuccessOutputPrepare,
    decode_entry,
    encode_entry,
)
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.services.restore_history.terminal_projection import TerminalV4HistoryProjection
from orchestrator.tasks._invocation import invocation
from orchestrator.tasks.states import TaskStatus
from orchestrator.turn_finish import answer_finish
from tests import test_terminal_history_postgres as legacy
from tests.restore_history_support import KEY
from tests.test_history_observation_boundary_postgres import device

metadata_cluster = legacy.metadata_cluster
db = legacy.db
projection_db = legacy.projection_db
scenario = legacy.scenario
effects = legacy.effects
reserved = legacy.reserved
presenter = legacy.presenter
reservation_owner = legacy.reservation_owner
preparer = legacy.preparer
phase_owner = legacy.phase_owner
artifact_owner = legacy.artifact_owner
terminal_owner = legacy.terminal_owner

SQL = Path(__file__).resolve().parents[1] / "migrations/opt_in/074_terminal_v4.sql"
PROFILE = "terminal-v4"
CONTENT = "synthetic unfinished answer"


def deadline():
    return time.monotonic() + 5


def projection(s):
    return TerminalV4HistoryProjection(
        s.identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )


def fields(kind="length"):
    provider = (
        {"state": "missing"}
        if kind == "missing"
        else {
            "state": "confirmed",
            "provenance": "provider_wire",
            "reason": kind,
            "dispatch_id": "trial",
        }
    )
    return {"metadata": {"turn_finish": answer_finish(provider).model_dump(exclude_none=True)}}


@pytest_asyncio.fixture
async def v4_owner(terminal_owner):
    s = terminal_owner
    await s.history.aclose(deadline=deadline())
    s.old_projection = s.projection
    s.db.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    s.projection = projection(s)
    s.history = HistoryCoordinator(s.path, s.identity, s.ownership, profile=PROFILE)
    await s.history.open(deadline=deadline())
    s.effects = HistoryEffects(s.worker, s.history, s.projection)
    assert (
        await s.worker.begin_execution(
            s.claim.task_id, s.claim.epoch, s.attempt.financial_scope_id, binding=s.claim
        )
        == 1
    )
    try:
        yield s
    finally:
        await s.history.aclose(deadline=deadline())


async def publish(s, kind="length"):
    token = invocation.set(s.invocation)
    try:
        return await s.effects.publish_non_success(
            s.claim, content=CONTENT, message_fields=fields(kind), deadline=deadline()
        )
    finally:
        invocation.reset(token)


@asynccontextmanager
async def independent_reader(s):
    ident, _ = await device(s, s.account)
    async with s.db.connection() as conn:
        session = await conn.fetchval("SELECT id FROM sessions WHERE device_id=$1", ident)
    owner = HistoryCoordinator(s.path, s.identity, s.ownership, profile=PROFILE)
    await owner.open(deadline=deadline())
    try:
        reader = HistoryObservation(s.worker, owner, s.projection, KEY)

        async def lookup():
            return await reader.lookup(
                AuthenticatedDevice(s.account, ident, session),
                "stable-client-key",
                deadline=deadline(),
            )

        yield lookup
    finally:
        await owner.aclose(deadline=deadline())


async def terminal_entry(s):
    async with s.history.operation(deadline=deadline()) as op:
        state = await op.verify()
    a = state.entries[0]
    return decode_entry(
        encode_entry(
            s.identity.history_id,
            len(state.entries) + 1,
            state.digest,
            Command(
                uuid4(),
                NonsuccessOutputPrepare(
                    s.account,
                    0,
                    s.claim.task_id,
                    s.claim.result_message_id,
                    a.command.record_id,
                    s.claim.epoch,
                    "native-nonsuccess-body-v1",
                    "failed",
                    "provider_length",
                ),
            ),
            profile=PROFILE,
        ),
        profile=PROFILE,
    )


@pytest.mark.asyncio
async def test_v4_exact_qualification_refuses_old_schema(terminal_owner):
    s = terminal_owner
    assert SQL not in SQL.parents[1].glob("*.sql")
    async with s.db.connection("md_app") as conn, conn.transaction():
        await s.projection.qualify(conn, writing=False, deadline=deadline())
        with pytest.raises(HistoryError, match="projection_catalog_refused"):
            await projection(s).qualify(conn, writing=False, deadline=deadline())
    with pytest.raises(HistoryError, match="terminal_finish_profile_refused"):
        await publish(s)
    assert s.db.sql("SELECT status FROM tasks") == "running"
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"


@pytest.mark.asyncio
async def test_old_qualification_and_extra_grants_refused(v4_owner):
    s = v4_owner
    async with s.db.connection("md_app") as conn, conn.transaction():
        await s.projection.qualify(conn, writing=False, deadline=deadline())
        with pytest.raises(HistoryError, match="projection_catalog_refused"):
            await s.old_projection.qualify(conn, writing=False, deadline=deadline())
    s.db.sql("GRANT EXECUTE ON FUNCTION task_history_terminal_project(bytea) TO reserved_p;")
    try:
        async with s.db.connection("reserved_p") as conn, conn.transaction():
            with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                await conn.fetch("SELECT * FROM task_history_terminal_project($1)", b"invalid")
        with pytest.raises(HistoryError, match="projection_catalog_refused"):
            await publish(s)
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
    finally:
        s.db.sql("REVOKE EXECUTE ON FUNCTION task_history_terminal_project(bytea) FROM reserved_p;")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["md_acceptor", "reserved_p"])
async def test_v4_neither_a_nor_p_can_stage_or_decode(v4_owner, role):
    s = v4_owner
    for query in (
        "SELECT * FROM task_history_terminal_project($1)",
        "SELECT task_history_decode_nonsuccess($1)",
    ):
        async with s.db.connection(role) as conn, conn.transaction():
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(query, b"invalid")


@pytest.mark.asyncio
async def test_v4_material_control_precedence_is_retained(v4_owner):
    s = v4_owner
    async with s.db.connection() as conn:
        await conn.execute(
            "INSERT INTO task_operations(task_id,epoch,tool_name,effect_class,outcome,completed_at) "
            "VALUES($1,$2,'synthetic_tool','material','succeeded',now())",
            s.claim.task_id,
            s.claim.epoch,
        )
    assert await publish(s) is TaskStatus.NEEDS_ATTENTION
    assert s.db.sql("SELECT terminal_code FROM tasks") == "uncertain_effect"
    assert (
        s.db.sql("SELECT metadata->'uncertain_tools' FROM messages WHERE role='assistant'")
        == '["synthetic_tool"]'
    )
    async with independent_reader(s) as lookup:
        assert (await lookup()).kind == "recovery"


@pytest.mark.asyncio
async def test_v4_stop_before_returned_answer_blocks_without_projection(v4_owner):
    s = v4_owner
    outcome = await s.effects.request_cancel(
        s.account, s.claim.task_id, key=KEY, deadline=deadline()
    )
    assert outcome.accepted
    with pytest.raises(HistoryError, match="historical_stop"):
        await publish(s)
    assert (
        s.db.sql(
            "SELECT count(*) FROM task_history_projection WHERE kind='NONSUCCESS_OUTPUT_PREPARE'"
        )
        == "0"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,expected,reason,outcome",
    [
        ("length", TaskStatus.FAILED, "provider_length", "failed_terminal"),
        ("missing", TaskStatus.NEEDS_ATTENTION, "finish_evidence_missing", "needs_attention"),
        ("content_filter", TaskStatus.FAILED, "provider_content_filter", "failed_terminal"),
    ],
)
async def test_v4_atomic_terminal_and_independent_observation(
    v4_owner, provider, expected, reason, outcome
):
    s = v4_owner
    assert await publish(s, provider) is expected
    for role in ("md_app", "md_acceptor"):
        async with s.db.connection(role) as conn, conn.transaction():
            page = await s.projection.page(conn, after_seq=0, deadline=deadline())
            assert len(page) == 2
            fact = decode_entry(page[-1].frame, profile=PROFILE).command.data
            assert isinstance(fact, NonsuccessOutputPrepare) and (fact.status, fact.reason) == (
                expected.value,
                reason,
            )
    assert (
        s.db.sql("SELECT outcome||':'||terminal_code FROM task_attempts") == outcome + ":" + reason
    )
    async with independent_reader(s) as lookup:
        observation = await lookup()
        assert observation.kind == "task" and observation.snapshot is not None
        assert (
            observation.snapshot.status is expected and observation.snapshot.terminal_code == reason
        )
        assert observation.snapshot.content == CONTENT
        assert observation.snapshot.turn_finish is not None
        s.db.sql("UPDATE task_attempts SET terminal_code='other';")
        assert (await lookup()).kind == "recovery"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value", [("epoch", 9), ("account_generation", 1), ("result_message_id", uuid4())]
)
async def test_v4_sql_rejects_wrong_original_binding_and_rolls_back(
    v4_owner, monkeypatch, key, value
):
    s = v4_owner
    native = s.projection.stage_terminal

    async def stage(conn, entry, *, deadline):
        command = replace(entry.command, data=replace(entry.command.data, **{key: value}))
        wrong = decode_entry(
            encode_entry(entry.history_id, entry.seq, entry.prev_digest, command, profile=PROFILE),
            profile=PROFILE,
        )
        return await native(conn, wrong, deadline=deadline)

    monkeypatch.setattr(s.projection, "stage_terminal", stage)
    with pytest.raises(TransactionUnavailable) as caught:
        await publish(s)
    assert not caught.value.commit_attempted
    assert isinstance(caught.value.__cause__, HistoryError)
    assert caught.value.__cause__.code == "projection_unavailable"
    assert s.db.sql("SELECT status FROM tasks") == "running"
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
    assert s.history._blocked
    async with independent_reader(s) as lookup:
        assert (await lookup()).kind == "recovery"
    with pytest.raises(HistoryError, match="blocked"):
        await publish(s)


@pytest.mark.asyncio
async def test_v4_committed_native_rows_cannot_be_backfilled(v4_owner):
    s = v4_owner
    entry = await terminal_entry(s)
    assert (
        await s.worker.publish_non_success(
            s.claim.task_id,
            s.claim.epoch,
            content=CONTENT,
            message_fields=fields(),
            binding=s.claim,
        )
        is TaskStatus.FAILED
    )
    async with s.db.connection("md_app") as conn, conn.transaction():
        with pytest.raises(HistoryError, match="projection_unavailable"):
            await s.projection.stage_terminal(conn, entry, deadline=deadline())
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE messages SET metadata=jsonb_set(metadata,'{turn_finish,provider,state}','\"missing\"') WHERE role='assistant'",
        "UPDATE task_attempts SET outcome='needs_attention'",
        "event_kind",
        "event_time",
    ],
)
async def test_v4_incoherent_active_native_rows_refuse_sql(v4_owner, monkeypatch, mutation):
    s = v4_owner
    native = s.projection.stage_terminal
    append_event = s.worker._append_event
    changed = False

    async def corrupt(conn, entry, *, deadline):
        nonlocal changed
        await conn.execute(mutation)  # fixture writes still belong to the active tx
        changed = True
        return await native(conn, entry, deadline=deadline)

    async def wrong_event(conn, task_id, kind, payload):
        nonlocal changed
        assert kind == "finished"
        if mutation == "event_kind":
            seq = await append_event(conn, task_id, "other", payload)
        else:
            seq = await conn.fetchval(
                "UPDATE tasks SET event_seq=event_seq+1 WHERE id=$1 RETURNING event_seq", task_id
            )
            await conn.execute(
                "INSERT INTO task_events(task_id,seq,kind,payload_ciphertext,created_at) "
                "VALUES($1,$2,$3,$4,now()+interval '1 second')",
                task_id,
                seq,
                kind,
                s.worker._seal(payload),
            )
        changed = True
        return seq

    if mutation.startswith("event_"):
        monkeypatch.setattr(s.worker, "_append_event", wrong_event)
    else:
        monkeypatch.setattr(s.projection, "stage_terminal", corrupt)
    with pytest.raises(TransactionUnavailable) as caught:
        await publish(s)
    assert changed and not caught.value.commit_attempted
    assert isinstance(caught.value.__cause__, HistoryError)
    assert caught.value.__cause__.code == "projection_unavailable"
    assert s.db.sql("SELECT status FROM tasks") == "running"
    assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before", "after"])
async def test_v4_lost_commit_ack_never_retries(v4_owner, monkeypatch, fault):
    s = v4_owner
    native_stage = s.projection.stage_terminal
    native_commit = Transaction.commit
    ready = False
    fired = 0

    async def stage(*args, **kwargs):
        nonlocal ready
        result = await native_stage(*args, **kwargs)
        ready = True
        return result

    async def commit(tx):
        nonlocal fired, ready
        if ready and tx._connection._params.user == "md_app":
            fired += 1
            ready = False
            s.db.bridge.commit_fault_role = "md_app"
            s.db.bridge.commit_fault = fault
        try:
            return await native_commit(tx)
        finally:
            s.db.bridge.commit_fault = None

    monkeypatch.setattr(s.projection, "stage_terminal", stage)
    monkeypatch.setattr(Transaction, "commit", commit)
    with pytest.raises((TransactionUnavailable, TimeoutError)):
        await publish(s)
    assert s.history._blocked
    assert s.db.sql("SELECT status FROM tasks") == ("failed" if fault == "after" else "running")
    async with independent_reader(s) as lookup:
        assert (await lookup()).kind == ("task" if fault == "after" else "recovery")
    with pytest.raises(HistoryError, match="blocked"):
        await publish(s)
    assert fired == 1
