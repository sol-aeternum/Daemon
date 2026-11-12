"""Authenticated terminal-profile prerequisites, never an installed execution graph."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from orchestrator import resource_store as registry
from orchestrator.artifact_namespaces import NamespaceController
from orchestrator.artifact_reservations import (
    DescriptorCodec,
    IntentBinding,
    JobBinding,
    OutputReservations,
    SIGNATURES,
)
from orchestrator.entitlements.service import EntitlementService
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.deletion_journal.store import initialize
from orchestrator.services.deletion_journal.upgrade import promote_container_v2
from orchestrator.services.restore_history.acceptance_host import (
    IsolatedAcceptanceHost,
    create_isolated_acceptance_app,
)
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.services.restore_history.model import (
    AcceptPrepare,
    ArtifactPrepare,
    HistoryError,
    Profile,
    SendPossible,
    StopIntent,
    TerminalOutputPrepare,
    encode_entry,
)
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.services.restore_history.store import HistoryStore
from orchestrator.services.restore_history.terminal_projection import TerminalHistoryProjection
from orchestrator.tasks._invocation import InferenceInvocation
from orchestrator.tasks.inference import InferenceRecovery
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import KEY
from tests.test_history_acceptance_host_postgres import install_terminal, submit
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_composition_postgres import deadline
from tests.test_history_effects_postgres import install, running, mark
from tests.test_history_observation_boundary_postgres import device
from tests.test_resource_output_catalog_postgres import SIGNATURES as CATALOG
from tests.test_resource_output_presentations_postgres import SIGNATURES as PRESENTATIONS
from tests.test_restore_history_store import owned


@pytest_asyncio.fixture
async def terminal_host(projection_db, tmp_path, monkeypatch):
    database = projection_db
    install(database)
    install_terminal(database)
    async with database.connection() as conn:
        account = await database.account(conn)
    path = tmp_path / "h"
    identity, ownership = owned(path)
    enc = ContentEncryption(Fernet.generate_key().decode())
    calls = [AsyncMock(side_effect=AssertionError("host installed execution")) for _ in range(3)]
    monkeypatch.setattr("litellm.acompletion", calls[0])
    monkeypatch.setattr("litellm.aembedding", calls[1])
    monkeypatch.setattr("arq.connections.ArqRedis.enqueue_job", calls[2])
    s = SimpleNamespace(
        db=database,
        account=account,
        path=path,
        identity=identity,
        ownership=ownership,
        root=tmp_path / "objects",
        journal=tmp_path / "j1",
        enc=enc,
    )

    @asynccontextmanager
    async def make(profile: Profile = "terminal-v3"):
        async with database.pool() as w:
            a = await asyncpg.create_pool(
                host=str(database.host),
                user="md_acceptor",
                password="",
                database=database.name,
                ssl=False,
                min_size=0,
                max_size=2,
            )
            tasks = TaskStore(a, enc, MemoryStore(a, enc), cover_new_inference=True)
            reads = TaskStore(w, enc, MemoryStore(w, enc))
            projection = (
                TerminalHistoryProjection(
                    identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
                )
                if profile == "terminal-v3"
                else HistoryProjection(identity, acceptor="md_acceptor", worker="md_app")
            )
            history = HistoryCoordinator(path, identity, ownership, profile=profile)
            reader_history = HistoryCoordinator(path, identity, ownership, profile=profile)
            acceptance = HistoryAcceptance(tasks, history, projection, KEY)
            reader = HistoryObservation(reads, reader_history, projection, KEY)
            host = IsolatedAcceptanceHost(
                acceptance,
                reader,
                fingerprint_key="synthetic-pepper",
                max_body_bytes=4096,
                keepalive_s=1,
            )
            v = SimpleNamespace(
                host=host,
                app=create_isolated_acceptance_app(host),
                a=a,
                w=w,
                tasks=tasks,
                reader=reader,
                acceptance=acceptance,
                projection=projection,
            )
            try:
                await host.open(deadline=deadline())
                yield v
            finally:
                await host.aclose(deadline=deadline())
                await asyncio.wait_for(a.close(), 5)

    s.make = make
    yield s
    for call in calls:
        call.assert_not_called()


def entries(s):
    with HistoryStore(
        s.path, s.identity, s.ownership, deadline=deadline(), profile="terminal-v3"
    ) as h:
        return h.verify(deadline=deadline()).entries


async def original_claim(s, v, accepted):
    """Only original native test authority; never installed in the host."""
    recovery = InferenceRecovery(v.w)
    recovery.principals = ("md_app", "md_acceptor")
    worker = TaskStore(v.w, s.enc, MemoryStore(v.w, s.enc), inference_recovery=recovery)
    value = SimpleNamespace(
        db=s.db,
        account=s.account,
        accepted=accepted,
        tasks=v.tasks,
        worker=worker,
        worker_pool=v.w,
        recovery=recovery,
        invocation=InferenceInvocation(),
        effects=HistoryEffects(worker, v.acceptance._history, v.projection),
    )
    await running(value)
    return value


async def complete(value):
    return await value.effects.complete(
        value.claim.task_id,
        value.claim.epoch,
        binding=value.claim,
        content="synthetic original final",
        deadline=deadline(),
    )


@asynccontextmanager
async def artifact(s, value):
    """Existing R0/P/native publication contracts; no transport or executor."""
    s.db.cluster.sql(
        "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='reserved_r') "
        "THEN CREATE ROLE reserved_r LOGIN; END IF; END $$;"
    )
    s.db.sql(
        "GRANT USAGE ON SCHEMA public TO reserved_r;"
        + ";".join(
            "GRANT EXECUTE ON FUNCTION public.artifact_output_" + f + " TO reserved_r"
            for f in SIGNATURES[3:]
        )
        + ";"
        + ";".join(
            "GRANT EXECUTE ON FUNCTION " + f + " TO reserved_p"
            for f in (
                *PRESENTATIONS,
                *CATALOG,
                "public.resource_output_namespace_inventory(pg_catalog.uuid,pg_catalog.int4)",
                "public.resource_output_presentation_references(pg_catalog.jsonb,pg_catalog.jsonb)",
            )
        )
    )
    journal_id = uuid4()
    initialize(s.journal, journal_id)
    promote_container_v2(s.journal, journal_id)
    s.root.mkdir(mode=0o700)
    for name in ("live", "private"):
        (s.root / name).mkdir(mode=0o700)
    async with s.db.connection() as conn:
        await conn.execute("INSERT INTO deletion_journal_binding VALUES(true,$1)", journal_id)
        attempt_id = await conn.fetchval(
            "SELECT id FROM task_attempts WHERE task_id=$1 AND epoch=$2",
            value.claim.task_id,
            value.claim.epoch,
        )
    async with AsyncExitStack() as stack:
        pools = {}
        for role in ("md_owner", "reserved_p", "reserved_r"):
            pool = await asyncpg.create_pool(
                host=str(s.db.host),
                user=role,
                password="",
                database=s.db.name,
                ssl=False,
                min_size=0,
                max_size=2,
            )
            stack.push_async_callback(pool.close)
            pools[role] = pool
        await NamespaceController(
            pools["md_owner"], s.root, s.journal, journal_id
        ).ensure_namespace(CapturedGeneration(s.account, 0))
        codec = DescriptorCodec(s.enc._cipher)
        publisher = registry.ReservedOutputPublisher(
            pools["reserved_p"],
            s.root,
            s.journal,
            journal_id,
            s.enc._cipher,
            inventory_limit=10,
            snapshot_byte_limit=400000,
            presentation_item_limit=10,
            presentation_record_limit=10,
        )
        command = codec.encode(
            JobBinding(uuid4(), s.account, 0, value.claim.task_id, uuid4()),
            IntentBinding(
                uuid4(),
                0,
                uuid4(),
                uuid4(),
                value.attempt.lifecycle_id,
                attempt_id,
                value.claim.epoch,
                17,
            ),
            filename="synthetic.csv",
            sha256=hashlib.sha256(b"column\nsynthetic\n").hexdigest(),
        )
        await value.effects.reserve_output(
            publisher,
            OutputReservations(codec),
            pools["reserved_r"],
            value.attempt,
            command,
            deadline=deadline(),
        )
        publication = await value.effects.publish_reserved_output(
            publisher,
            value.attempt,
            command.binding.intent_id,
            b"column\nsynthetic\n",
            deadline=deadline(),
        )
        yield publication


@pytest.mark.asyncio
async def test_original_partial_and_completed_text_reopen_on_second_device_and_new_pools(
    terminal_host,
):
    s = terminal_host
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make() as v:
        accepted = await submit(v, first)
        task_id = str(accepted.task.task_id)
        value = await original_claim(s, v, accepted.task)
        await value.effects.write_partial(
            value.claim.task_id,
            value.claim.epoch,
            binding=value.claim,
            content="original partial",
            delta_seq=1,
            deadline=deadline(),
        )
        async with AsyncClient(transport=ASGITransport(v.app), base_url="http://test") as http:
            partial = await http.get("/tasks/by-key/stable", headers=second)
            assert partial.status_code == 200
            assert (
                partial.json()["id"] == task_id and partial.json()["content"] == "original partial"
            )
        assert await complete(value) is TaskStatus.COMPLETED
        async with AsyncClient(transport=ASGITransport(v.app), base_url="http://test") as http:
            found = await http.get("/tasks/by-key/stable", headers=second)
            assert (
                found.status_code == 200 and found.json()["content"] == "synthetic original final"
            )
        old_pools = v.a, v.w
    assert all(pool._closed for pool in old_pools)
    before = entries(s)
    async with s.make() as fresh:
        assert fresh.a not in old_pools and fresh.w not in old_pools
        async with AsyncClient(transport=ASGITransport(fresh.app), base_url="http://test") as http:
            found = await http.get("/tasks/by-key/stable", headers=second)
            assert found.status_code == 200 and found.json()["id"] == task_id
            assert found.json()["status"] == "completed"
            assert found.json()["content"] == "synthetic original final"
            # Claim allocates an attempt row, not an execution-start count.
            assert found.json()["attempt_count"] == value.claim.attempt_count == 0
        assert entries(s) == before
        with pytest.raises(HistoryError, match="historical_acceptance"):
            await submit(fresh, second)
        assert fresh.acceptance._blocked
    assert s.db.sql("SELECT count(*) FROM tasks") == "1"
    assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"


@pytest.mark.asyncio
async def test_all_mixed_facts_cross_page_boundary_and_allow_only_fresh_v1_acceptance(
    terminal_host,
):
    s = terminal_host
    _, headers = await device(s, s.account)
    async with s.make() as v:
        for index in range(30):
            await submit(v, headers, key=f"prefix-{index}")
        completed = await submit(v, headers, key="completed")
        original = await original_claim(s, v, completed.task)
        async with artifact(s, original):
            assert await complete(original) is TaskStatus.COMPLETED
        # This fixture reserved compute but made no provider call. Finish its
        # original zero-cost hold before preparing another attempt; the real
        # free-plan concurrency ceiling must remain enforced.
        assert (await EntitlementService(v.w).release(original.reservation)).applied
        stopped = await submit(v, headers, key="stopped")
        sending = await original_claim(s, v, stopped.task)
        await mark(sending)
        assert (
            await sending.effects.request_cancel(
                s.account, sending.claim.task_id, key=KEY, deadline=deadline()
            )
        ).accepted
        before = entries(s)
        assert len(before) > 32
        assert {type(e.command.data) for e in before} == {
            AcceptPrepare,
            SendPossible,
            StopIntent,
            ArtifactPrepare,
            TerminalOutputPrepare,
        }
        fresh = await submit(v, headers, key="after-mixed")
        assert fresh.task.created
        after = entries(s)
        assert after[:-1] == before and type(after[-1].command.data) is AcceptPrepare
        assert encode_entry(
            after[-1].history_id, after[-1].seq, after[-1].prev_digest, after[-1].command
        ) == encode_entry(
            after[-1].history_id,
            after[-1].seq,
            after[-1].prev_digest,
            after[-1].command,
            profile="terminal-v3",
        )
        assert (
            await v.host.lookup(headers["Authorization"], "completed", deadline=deadline())
        ).kind == "task"
    # Startup's third comparison site must validate every mixed page too.
    async with s.make() as reconstructed:
        assert (
            await reconstructed.host.lookup(
                headers["Authorization"], "after-mixed", deadline=deadline()
            )
        ).kind == "task"


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["missing", "extra"])
async def test_terminal_projection_mismatch_never_repairs_acknowledges_or_accepts(
    terminal_host, loss
):
    s = terminal_host
    _, headers = await device(s, s.account)
    async with s.make() as v:
        accepted = await submit(v, headers)
        value = await original_claim(s, v, accepted.task)
        saved = {name: (s.path / name).read_bytes() for name in ("LOG", "HEAD")}
        assert await complete(value) is TaskStatus.COMPLETED
        if loss == "missing":
            s.db.sql(
                "ALTER TABLE task_history_projection DISABLE TRIGGER task_history_projection_immutable;"
                "DELETE FROM task_history_projection WHERE kind='TERMINAL_OUTPUT_PREPARE';"
                "ALTER TABLE task_history_projection ENABLE TRIGGER task_history_projection_immutable;"
            )
        else:
            # Owned synthetic H rollback: SQL keeps the actual committed terminal.
            for name, content in saved.items():
                (s.path / name).write_bytes(content)
        assert (
            await v.host.lookup(headers["Authorization"], "stable", deadline=deadline())
        ).kind == "recovery"
        with pytest.raises(HistoryError, match="acceptance_reconciliation_required"):
            await submit(v, headers, key="must-not-create")
        assert v.acceptance._blocked
    with pytest.raises(HistoryError, match="reader_reconciliation_required"):
        async with s.make():
            pytest.fail("mismatched host opened")
    assert s.db.sql("SELECT count(*) FROM tasks") == "1"
    assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"


@pytest.mark.asyncio
async def test_h1_host_refuses_actual_expanded_schema_without_downgrade(terminal_host):
    with pytest.raises(HistoryError, match="projection_catalog_refused"):
        async with terminal_host.make(profile="h1"):
            pytest.fail("H1 selected expanded schema")
    assert terminal_host.db.sql("SELECT count(*) FROM tasks") == "0"
