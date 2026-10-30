"""Synthetic boundary diagnostics, not protected projection/runtime integration.

The acceptance hook below exists only in this test. It proves the H prepare can
survive native rollback/unknown COMMIT, while current native admission still lacks
an H lookup. The missing protection is asserted as a gap, never release success.
"""

import time
from dataclasses import replace
from uuid import uuid4
from typing import Any

from asyncpg.transaction import Transaction
import pytest
from cryptography.fernet import Fernet

from orchestrator.entitlements.service import EntitlementService
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.restore_history.model import (
    AcceptPrepare,
    Command,
    HistoryError,
    SendPossible,
    StopIntent,
    commitments,
)
from orchestrator.services.restore_history.projection import compare_projections
from orchestrator.tasks._invocation import InferenceInvocation
from orchestrator.tasks.inputs import chat_request_fingerprint
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import HISTORY, KEY
from tests.test_inference_recovery_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    seed,
)
from tests.test_restore_history_store import open_store, owned


class LostAcknowledgement(ConnectionError):
    """The oracle knows the server branch; the original caller does not."""


def acceptance(account, task, key, canonical):
    lookup, request = commitments(KEY, account, key, canonical.encode())
    return Command(
        uuid4(),
        AcceptPrepare(
            account,
            0,
            task["id"],
            task["conversation_id"],
            task["user_message_id"],
            task["result_message_id"],
            1,
            1,
            lookup,
            request,
            "chat-canonical-3715-v1",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["prepare_ack_lost", "commit_before", "commit_after"])
async def test_prepare_survives_native_rollback_or_unknown_commit(db, tmp_path, monkeypatch, fault):
    path = tmp_path / "h"
    identity, ownership = owned(path)
    async with db.connection() as owner:
        account = await db.account(owner)
    async with db.connection("md_acceptor") as conn:
        # This connection is owned by this one synthetic caller; no other task
        # borrows it while the native transaction exit is fault-injected.
        class Pool:
            def __getattr__(self, name):
                return getattr(conn, name)

            def acquire(self):
                class Lease:
                    async def __aenter__(self):
                        return conn

                    async def __aexit__(self, *_args):
                        return None

                return Lease()

        enc = ContentEncryption(Fernet.generate_key().decode())
        pool: Any = Pool()
        tasks = TaskStore(pool, enc, MemoryStore(pool, enc), cover_new_inference=True)
        fingerprint = chat_request_fingerprint(
            key="synthetic-pepper",
            conversation_id=None,
            message="synthetic",
            attachments=None,
            model=None,
            provider=None,
            metadata=None,
            disable_memory_write=False,
        )
        client_key = uuid4().hex
        options: dict[str, Any] = dict(
            user_id=account,
            conversation_id=None,
            new_conversation_title="fixture",
            pipeline="cloud",
            user_message="synthetic",
            task_input={"message": "synthetic"},
            request_hash=fingerprint.digest,
            request_canonical=fingerprint.canonical,
            idempotency_key=client_key,
            assistant_model=None,
            captured_generation=CapturedGeneration(account, 0),
        )
        original_event = tasks._append_event
        original_exit = Transaction.__aexit__
        prepared = []
        armed = False
        with open_store(path, identity, ownership) as history:

            async def event(conn, task_id, kind, payload=None):
                nonlocal armed
                value = await original_event(conn, task_id, kind, payload)
                if kind == "accepted":
                    row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
                    command = acceptance(account, row, client_key, fingerprint.canonical)
                    prepared.append(history.append(command, deadline=time.monotonic() + 5))
                    armed = True
                    if fault == "prepare_ack_lost":
                        raise LostAcknowledgement("synthetic append result lost")
                return value

            async def exit_transaction(tx, kind, value, traceback):
                nonlocal armed
                if not armed or kind is not None:
                    return await original_exit(tx, kind, value, traceback)
                armed = False
                lost = LostAcknowledgement("synthetic COMMIT acknowledgement lost")
                if fault == "commit_before":
                    await original_exit(tx, type(lost), lost, None)
                else:
                    await original_exit(tx, None, None, None)
                raise lost

            monkeypatch.setattr(tasks, "_append_event", event)
            monkeypatch.setattr(Transaction, "__aexit__", exit_transaction)
            with pytest.raises(LostAcknowledgement):
                await tasks.accept(**options)
            monkeypatch.setattr(Transaction, "__aexit__", original_exit)
            monkeypatch.setattr(tasks, "_append_event", original_event)
            assert len(prepared) == 1 and prepared[0].created
            command = prepared[0].entry.command
            original_task = command.data.task_id
            assert len(history.verify(deadline=time.monotonic() + 5).entries) == 1
            assert not history.append(command, deadline=time.monotonic() + 5).created
            count = await conn.fetchval("SELECT count(*) FROM tasks WHERE id=$1", original_task)
            assert count == (1 if fault == "commit_after" else 0)
            # Deliberately observe the CURRENT unintegrated native path. Missing
            # history lookup creates fresh work after rollback: a readiness gap.
            replay = await tasks.accept(**options)
            assert replay.created is (fault != "commit_after")
            assert (replay.task_id == original_task) is (fault == "commit_after")
            replacement = replace(command.data, task_id=replay.task_id)
            with pytest.raises(HistoryError, match="conflict"):
                history.append(Command(uuid4(), replacement), deadline=time.monotonic() + 5)
            db.cluster.record(
                "h1_native_acceptance_gap",
                fault=fault,
                original_committed=count == 1,
                native_replay_created=replay.created,
                h_prepare_retained=True,
                protected_projection_installed=False,
                provider_calls=0,
            )


@pytest.mark.asyncio
async def test_history_survives_database_restore_after_native_mark_and_stop(db, tmp_path):
    cluster = db.cluster
    assert db.name in cluster.databases and cluster.container is not None
    snapshot = "md_hcut_" + uuid4().hex
    assert cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{db.name}'") == "0"
    cluster.sql(f'CREATE DATABASE "{snapshot}" TEMPLATE "{db.name}"')
    cluster.databases.add(snapshot)
    path = tmp_path / "h"
    identity, ownership = owned(path)
    try:
        with open_store(path, identity, ownership) as history:
            async with db.connection() as owner, db.pool() as pool:
                tasks, recovery, claim, attempt = await seed(db, owner, pool)
                row = await owner.fetchrow("SELECT * FROM tasks WHERE id=$1", claim.task_id)
                # Native seed accepted before this diagnostic observation. This
                # append is historical only, not proof of a fresh admission ACK.
                fingerprint = chat_request_fingerprint(
                    key="synthetic-pepper",
                    conversation_id=None,
                    message="synthetic",
                    attachments=None,
                    model=None,
                    provider=None,
                    metadata=None,
                    disable_memory_write=False,
                )
                prepare = acceptance(
                    attempt.account_id, row, row["idempotency_key"], fingerprint.canonical
                )
                first = history.append(prepare, deadline=time.monotonic() + 5)
                hold = await EntitlementService(pool).reserve_for_attempt(
                    attempt, 100, operation="chat"
                )
                await recovery.mark(attempt, hold.id, InferenceInvocation())
                binding = await owner.fetchrow(
                    "SELECT * FROM compute_reservation_metadata WHERE reservation_id=$1", hold.id
                )
                possible = Command(
                    uuid4(),
                    SendPossible(
                        attempt.account_id,
                        attempt.generation,
                        attempt.task_id,
                        attempt.epoch,
                        attempt.lifecycle_id,
                        attempt.financial_scope_id,
                        attempt.metadata_scope_id,
                        attempt.producer_instance_id,
                        hold.id,
                        binding["runtime_identity"],
                        binding["dispatch_nonce"],
                        "completion",
                    ),
                )
                second = history.append(possible, deadline=time.monotonic() + 5)
                stop = Command(uuid4(), StopIntent(attempt.account_id, 0, attempt.task_id, "owner"))
                third = history.append(stop, deadline=time.monotonic() + 5)
                assert (await tasks.request_cancel(attempt.account_id, attempt.task_id)).accepted
                assert await owner.fetchval(
                    "SELECT cancel_requested_at IS NOT NULL FROM tasks WHERE id=$1", claim.task_id
                )
            assert (
                cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{db.name}'")
                == "0"
            )
            cluster.sql(f'DROP DATABASE "{db.name}"')
            cluster.sql(f'CREATE DATABASE "{db.name}" TEMPLATE "{snapshot}"')
            async with db.connection() as restored:
                assert await restored.fetchval("SELECT count(*) FROM tasks") == 0
                assert await restored.fetchval("SELECT count(*) FROM task_inference_recovery") == 0
                assert await restored.fetchval("SELECT count(*) FROM entitlement_reservations") == 0
            state = history.verify(deadline=time.monotonic() + 5)
            assert state.entries == (first.entry, second.entry, third.entry)
            assert compare_projections(HISTORY, state, ()).missing == (1, 2, 3)
            assert not history.append(possible, deadline=time.monotonic() + 5).created
            with pytest.raises(HistoryError, match="effect_stopped"):
                history.append(
                    Command(
                        uuid4(),
                        replace(
                            possible.data,
                            reservation_id=uuid4(),
                            runtime_identity=uuid4(),
                            dispatch_nonce=uuid4(),
                        ),
                    ),
                    deadline=time.monotonic() + 5,
                )
            cluster.record(
                "h1_retained_after_synthetic_restore",
                accepted=1,
                possible_sends=1,
                stops=1,
                missing_projections=3,
                provider_calls=0,
                runtime_integration_installed=False,
            )
    finally:
        cluster.sql(f'DROP DATABASE "{snapshot}"')
        cluster.databases.remove(snapshot)
