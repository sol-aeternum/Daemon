"""Exact fixture-only SQL catalog and protected retirement metadata."""

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.entitlements.service import EntitlementService
from orchestrator.memory.store import MemoryStore
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.model import (
    Command,
    HistoryError,
    Identity,
    InboxOriginalRetired,
    SendPossible,
    TerminalOutputPrepare,
    encode_entry,
    decode_entry,
    key_check,
)
from orchestrator.services.restore_history.projection import compare_projections
from orchestrator.services.restore_history.terminal_projection import (
    InboxRetiredHistoryProjection,
    TerminalHistoryProjection,
)
from orchestrator.tasks._invocation import InferenceInvocation
from orchestrator.tasks.inference import InferenceRecovery
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import HISTORY, KEY, ZERO
from tests.test_history_acceptance_host_postgres import install_terminal
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_composition_postgres import deadline, scenario as scenario, submission
from tests.test_history_effects_postgres import install, mark, running

SQL = Path(__file__).parent / "fixtures" / "inbox_retired_v1.sql"


def install_retired(database):
    install(database)
    install_terminal(database)
    database.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    database.sql(
        "GRANT EXECUTE ON FUNCTION task_history_retired_project(bytea) TO md_app;"
        "GRANT EXECUTE ON FUNCTION task_history_retired_page(uuid,bigint,text) TO md_app,md_acceptor;"
    )


@pytest_asyncio.fixture
async def retired_projection(projection_db):
    database = projection_db
    install_retired(database)
    identity = Identity(HISTORY, key_check(KEY, HISTORY))
    projection = InboxRetiredHistoryProjection(
        identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    return database, projection


@pytest.mark.asyncio
async def test_retirement_acl_and_old_profile_refusal(retired_projection):
    database, projection = retired_projection
    old = TerminalHistoryProjection(
        projection.expected, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    command = Command(uuid4(), InboxOriginalRetired(uuid4(), uuid4()))
    frame = encode_entry(HISTORY, 4, ZERO, command, profile=projection.profile)
    entry = decode_entry(frame, profile=projection.profile)
    for role in ("md_app", "md_acceptor"):
        async with database.connection(role) as conn:
            async with conn.transaction():
                assert await projection.page(conn, after_seq=0, deadline=deadline()) == ()
            async with conn.transaction():
                with pytest.raises(HistoryError, match="projection_catalog_refused"):
                    await old.qualify(conn, writing=False, deadline=deadline())
            async with conn.transaction():
                with pytest.raises(HistoryError):
                    await projection.stage_retired(conn, entry, deadline=deadline())
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchval("SELECT task_history_decode_retired($1)", frame)
    async with database.connection() as conn:
        assert await conn.fetchval("SELECT task_history_decode_retired($1)->>'kind'", frame) == (
            "INBOX_ORIGINAL_RETIRED"
        )
        assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 0
    # Direct W invocation is guarded even without the Python projection's check.
    async with database.connection("md_app") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetchrow("SELECT * FROM task_history_retired_project($1)", frame)
    database.sql("GRANT EXECUTE ON FUNCTION task_history_retired_project(bytea) TO md_acceptor;")
    async with database.connection("md_app") as conn:
        async with conn.transaction():
            with pytest.raises(HistoryError, match="projection_catalog_refused"):
                await projection.page(conn, after_seq=0, deadline=deadline())
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetch(
                "SELECT * FROM task_history_retired_page($1,0,$2)",
                HISTORY,
                projection.expected.key_check,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signature",
    (
        "task_history_decode_retired(bytea)",
        "task_history_retired_project(bytea)",
        "task_history_retired_page(uuid,bigint,text)",
        "task_history_check_caller(boolean)",
        "task_history_decode_effect(bytea)",
    ),
)
async def test_retirement_refuses_body_substitution(retired_projection, signature):
    database, projection = retired_projection
    # Same owner, signature, function flags and ACLs; even a harmless source
    # substitution must refuse before reading or staging any metadata.
    async with database.connection() as conn:
        source, definition = await conn.fetchrow(
            "SELECT prosrc,pg_get_functiondef(oid) FROM pg_catalog.pg_proc "
            "WHERE oid=$1::regprocedure",
            "public." + signature,
        )
        assert definition.count(source) == 1
        await conn.execute(definition.replace(source, source + "\n-- synthetic source drift\n"))
    for role in ("md_app", "md_acceptor"):
        async with database.connection(role) as conn, conn.transaction():
            with pytest.raises(HistoryError, match="projection_catalog_refused"):
                await projection.page(conn, after_seq=0, deadline=deadline())


@pytest.mark.asyncio
async def test_retirement_native_projection_and_subsequent_acceptance(scenario):
    s = scenario
    await s.history.aclose(deadline=deadline())
    install_retired(s.db)
    s.projection = InboxRetiredHistoryProjection(
        s.identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    s.history = HistoryCoordinator(s.path, s.identity, s.ownership, profile=s.projection.profile)
    await s.history.open(deadline=deadline())
    try:
        s.accept = HistoryAcceptance(s.tasks, s.history, s.projection, KEY)
        s.accepted = await s.accept.accept(deadline=deadline(), **submission(s.account))
        async with s.db.pool() as pool:
            s.worker_pool = pool
            s.recovery = InferenceRecovery(pool)
            s.recovery.principals = ("md_app", "md_acceptor")
            s.worker = TaskStore(
                pool,
                s.tasks._enc,
                MemoryStore(pool, s.tasks._enc),
                inference_recovery=s.recovery,
            )
            s.effects = HistoryEffects(s.worker, s.history, s.projection)
            s.invocation = InferenceInvocation()
            await running(s)
            assert (
                await s.worker.begin_execution(
                    s.claim.task_id, s.claim.epoch, s.attempt.financial_scope_id, binding=s.claim
                )
                == 1
            )
            await mark(s)
            assert (
                await s.effects.complete(
                    s.claim.task_id,
                    s.claim.epoch,
                    binding=s.claim,
                    content="synthetic text",
                    message_fields={},
                    deadline=deadline(),
                )
                is TaskStatus.COMPLETED
            )
            async with s.history.operation(deadline=deadline()) as operation:
                state = await operation.verify()
                send = state.entries[1]
                terminal = state.entries[2]
                assert isinstance(send.command.data, SendPossible)
                assert isinstance(terminal.command.data, TerminalOutputPrepare)
                command = Command(
                    uuid4(),
                    InboxOriginalRetired(terminal.command.record_id, send.command.record_id),
                )
                frame = encode_entry(
                    s.identity.history_id, 4, state.digest, command, profile=s.projection.profile
                )
                async with s.db.connection("md_app") as conn:
                    with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                        await conn.fetchrow("SELECT * FROM task_history_retired_project($1)", frame)
                await EntitlementService(pool).settle(s.reservation, 10)

                # Direct W staging must independently refuse invalid native
                # evidence, even if Python's original owner had not checked.
                async def refused(candidate=frame):
                    async with s.db.connection("md_app") as conn:
                        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
                            await conn.fetchrow(
                                "SELECT * FROM task_history_retired_project($1)", candidate
                            )

                for original, foreign in (
                    (terminal.command.record_id, uuid4()),
                    (uuid4(), send.command.record_id),
                    (state.entries[0].command.record_id, send.command.record_id),
                ):
                    invalid = Command(uuid4(), InboxOriginalRetired(original, foreign))
                    await refused(
                        encode_entry(
                            s.identity.history_id,
                            4,
                            state.digest,
                            invalid,
                            profile=s.projection.profile,
                        )
                    )
                async with s.db.connection() as conn:
                    attempt = await conn.fetchrow(
                        "SELECT id,partial_ciphertext,execution_started_at "
                        "FROM task_attempts WHERE task_id=$1",
                        s.claim.task_id,
                    )
                    finished = await conn.fetchrow(
                        "SELECT seq,created_at FROM task_events WHERE task_id=$1 AND kind='finished'",
                        s.claim.task_id,
                    )
                    assert attempt is not None and finished is not None
                    await conn.execute(
                        "UPDATE task_attempts SET partial_ciphertext='synthetic-wrong' WHERE id=$1",
                        attempt["id"],
                    )
                    await refused()
                    await conn.execute(
                        "UPDATE task_attempts SET partial_ciphertext=$2,"
                        "execution_started_at=ended_at+interval '1 second' WHERE id=$1",
                        attempt["id"],
                        attempt["partial_ciphertext"],
                    )
                    await refused()
                    await conn.execute(
                        "UPDATE task_attempts SET execution_started_at=$2 WHERE id=$1",
                        attempt["id"],
                        attempt["execution_started_at"],
                    )
                    await conn.execute(
                        "UPDATE task_events SET created_at=created_at+interval '1 second' "
                        "WHERE task_id=$1 AND seq=$2",
                        s.claim.task_id,
                        finished["seq"],
                    )
                    await refused()
                    await conn.execute(
                        "UPDATE task_events SET created_at=$3 WHERE task_id=$1 AND seq=$2",
                        s.claim.task_id,
                        finished["seq"],
                        finished["created_at"],
                    )
                    await conn.execute(
                        "UPDATE entitlement_reservations SET period_key='2099-01' WHERE id=$1",
                        s.reservation.id,
                    )
                    await refused()
                    await conn.execute(
                        "UPDATE entitlement_reservations SET period_key=$2 WHERE id=$1",
                        s.reservation.id,
                        s.reservation.period_key,
                    )
                    extra = await conn.fetchval(
                        "INSERT INTO task_attempts(task_id,epoch,worker_id) VALUES($1,$2,'synthetic') "
                        "RETURNING id",
                        s.claim.task_id,
                        s.claim.epoch + 1,
                    )
                    await refused()
                    await conn.execute("DELETE FROM task_attempts WHERE id=$1", extra)
                entry = (await operation.append(command)).entry
                async with s.db.connection("md_app") as conn, conn.transaction():
                    assert (
                        await s.projection.stage_retired(conn, entry, deadline=deadline())
                    ).inserted
                await refused()
            # Both ordinary v1 and terminal-v3 writes still append after the
            # retirement: another native acceptance, Send and completed result.
            s.accepted = await s.accept.accept(
                deadline=deadline(), **submission(s.account, key="distinct-client-key")
            )
            async with s.db.pool() as pool:
                s.worker_pool = pool
                s.recovery = InferenceRecovery(pool)
                s.recovery.principals = ("md_app", "md_acceptor")
                s.worker = TaskStore(
                    pool,
                    s.tasks._enc,
                    MemoryStore(pool, s.tasks._enc),
                    inference_recovery=s.recovery,
                )
                s.effects = HistoryEffects(s.worker, s.history, s.projection)
                s.invocation = InferenceInvocation()
                await running(s)
                assert (
                    await s.worker.begin_execution(
                        s.claim.task_id,
                        s.claim.epoch,
                        s.attempt.financial_scope_id,
                        binding=s.claim,
                    )
                    == 1
                )
                await mark(s)
                assert (
                    await s.effects.complete(
                        s.claim.task_id,
                        s.claim.epoch,
                        binding=s.claim,
                        content="distinct synthetic text",
                        message_fields={},
                        deadline=deadline(),
                    )
                    is TaskStatus.COMPLETED
                )
                await EntitlementService(pool).settle(s.reservation, 10)
            async with s.history.operation(deadline=deadline()) as operation:
                state = await operation.verify()
            assert len(state.entries) == 7
            for role in ("md_app", "md_acceptor"):
                async with s.db.connection(role) as conn, conn.transaction():
                    facts = await s.projection.page(conn, after_seq=0, deadline=deadline())
                    assert len(facts) == 7
                    report = compare_projections(
                        s.identity.history_id, state, facts, profile=s.projection.profile
                    )
                    assert not (
                        report.missing
                        or report.mismatched
                        or report.extra
                        or report.restore_required
                    )
                    assert (
                        await s.projection.page(conn, after_seq=3, deadline=deadline()) == facts[3:]
                    )
    finally:
        await s.history.aclose(deadline=deadline())
