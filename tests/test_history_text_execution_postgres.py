"""Actual authenticated host -> original runner/stream/compute; fake ONLY LiteLLM."""

import asyncio
import traceback
import sys
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from orchestrator import compute_runtime as runtime
from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.models import ChatRequest
from orchestrator.services.deletion_journal.store import initialize
from orchestrator.services.restore_history.acceptance_host import (
    IsolatedAcceptanceHost,
    create_isolated_acceptance_app,
)
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.model import SendPossible, TerminalOutputPrepare
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.services.restore_history.terminal_projection import TerminalHistoryProjection
from orchestrator.services.restore_history.text_execution import HistoryTextExecution
from orchestrator.tasks.store import TaskStore
from orchestrator.tasks import runner
from orchestrator.auth_tokens import hash_token
from orchestrator.entitlements.service import EntitlementService
from tests.restore_history_support import KEY
from tests.test_compute_runtime import _qualified_policy, _route
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_acceptance_host_postgres import install_terminal, post
from tests.test_history_composition_postgres import deadline
from tests.test_history_effects_postgres import install
from tests.test_history_observation_boundary_postgres import device
from tests.test_restore_history_store import owned


@pytest_asyncio.fixture
async def text_host(projection_db, tmp_path, monkeypatch):
    database = projection_db
    install(database)
    install_terminal(database)
    database.sql(
        "BEGIN;"
        + (Path(__file__).resolve().parents[1] / "migrations/066_memory_bitemporal.sql").read_text()
        + "COMMIT;"
    )
    database.sql("GRANT SELECT ON memory_knowledge_clock TO md_app;")
    journal = tmp_path / "j1"
    journal_id = uuid4()
    initialize(journal, journal_id)
    async with database.connection() as conn:
        account = await database.account(conn)
        await conn.execute("INSERT INTO deletion_journal_binding VALUES(true,$1)", journal_id)
    encryption_key = Fernet.generate_key().decode()
    enc = ContentEncryption(encryption_key)
    settings = cast(Any, Settings)(
        _env_file=None,
        durable_chat_enabled=True,
        mock_llm=False,
        daemon_encryption_key=encryption_key,
        daemon_task_acceptor_role="md_acceptor",
        daemon_task_worker_role="md_app",
        daemon_deletion_journal_dir=str(journal),
        daemon_deletion_journal_id=str(journal_id),
        openrouter_api_key="synthetic-no-network",
    )
    route = _route(input_price=1000)
    _qualified_policy(monkeypatch, route=route)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    path = tmp_path / "h"
    identity, ownership = owned(path)
    state = SimpleNamespace(
        db=database,
        account=account,
        path=path,
        identity=identity,
        ownership=ownership,
        calls=[],
        entered=asyncio.Event(),
        release=asyncio.Event(),
        closed=asyncio.Event(),
        unsolicited=False,
        errors=[],
        encryption_key=encryption_key,
        journal=journal,
        journal_id=journal_id,
    )
    from orchestrator import daemon

    monkeypatch.setattr(
        daemon.logger,
        "error",
        lambda *a, **kw: state.errors.append("".join(traceback.format_exception(sys.exception()))),
    )

    async def provider(**kwargs):
        scope = runtime.current_scope()
        assert scope.history_binding is not None
        state.calls.append(scope.inference_identity)
        assert kwargs["num_retries"] == 0 and not kwargs.get("tools")
        assert scope.history_binding.effects is state.execution.effects
        operation = state.execution.history._operation
        # An independent heartbeat may already own its own short H operation.
        # The original mark owner must be gone, and this transport independently
        # acquires H below (would deadlock if H enclosed the provider call).
        assert (
            operation is None
            or operation._task.get_coro().__qualname__ == "HistoryEffects._heartbeat"
        )
        async with database.connection() as conn:
            assert (
                await conn.fetchval("SELECT may_have_dispatched FROM task_inference_recovery")
                is True
            )
            assert await conn.fetchval("SELECT count(*) FROM compute_reservation_metadata") == 1
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM entitlement_reservations WHERE status='open'"
                )
                == 1
            )
            assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 2
        async with state.execution.history.operation(deadline=deadline()) as operation:
            entries = (await operation.verify()).entries
            assert type(entries[-1].command.data) is SendPossible
        hook = getattr(state, "provider_check", None)
        if hook is not None:
            await hook(scope)
        state.entered.set()

        async def stream():
            try:
                if state.unsolicited:
                    yield {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "bad",
                                            "function": {"name": "web_search", "arguments": "{}"},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                else:
                    yield {"choices": [{"delta": {"content": "original partial"}}]}
                    # The original engine persists this first delta before waiting.
                    await state.release.wait()
                    yield {"choices": [{"delta": {"content": " and final"}}]}
                yield {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            finally:
                state.closed.set()

        return stream()

    monkeypatch.setattr(runtime.litellm, "acompletion", provider)

    @asynccontextmanager
    async def make():
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
            projection = TerminalHistoryProjection(
                identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
            )
            history = HistoryCoordinator(path, identity, ownership, profile="terminal-v3")
            reader_history = HistoryCoordinator(path, identity, ownership, profile="terminal-v3")
            worker_history = HistoryCoordinator(path, identity, ownership, profile="terminal-v3")
            tasks = TaskStore(a, enc, MemoryStore(a, enc), cover_new_inference=True)
            reader = HistoryObservation(
                TaskStore(w, enc, MemoryStore(w, enc)), reader_history, projection, KEY
            )
            execution = HistoryTextExecution(w, enc, settings, worker_history, projection)
            host = IsolatedAcceptanceHost(
                HistoryAcceptance(tasks, history, projection, KEY),
                reader,
                fingerprint_key="synthetic-pepper",
                max_body_bytes=4096,
                keepalive_s=1,
                execution=execution,
            )
            state.execution = execution
            state.host, state.app, state.w = host, create_isolated_acceptance_app(host), w
            try:
                await host.open(deadline=deadline())
                assert execution.effects is not None
                native_run = execution.effects._run

                async def observed_run(call, limit):
                    try:
                        return await native_run(call, limit)
                    except BaseException:
                        state.errors.append("".join(traceback.format_exception(sys.exception())))
                        raise

                monkeypatch.setattr(execution.effects, "_run", observed_run)
                yield state
            finally:
                state.release.set()
                try:
                    await host.aclose(deadline=deadline())
                except TimeoutError:
                    if not getattr(state, "expected_shutdown_timeout", False):
                        raise
                    # Test-owned cleanup only after the retained original shutdown
                    # has physically finished. No owner renewal or execution retry.
                    assert host._shutdown_task is not None
                    assert host._shutdown_task.done() and execution._closed
                    assert all(child.done() for child in execution._jobs)
                    await reader_history.aclose(deadline=deadline())
                    await history.aclose(deadline=deadline())
                await a.close()

    state.make = make
    yield state


async def submitted(s, headers, **kwargs):
    return await s.host.submit(
        headers["Authorization"],
        "stable",
        ChatRequest(message="synthetic text", **kwargs),
        deadline=deadline(),
    )


async def jobs(s):
    results = await asyncio.wait_for(asyncio.gather(*s.execution._jobs), 10)
    assert results, "no original runner was scheduled"
    return results


@pytest.mark.asyncio
async def test_original_stream_partial_final_second_device_and_disconnect(text_host):
    s = text_host
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make():
        response = await post(s.app, first, {"message": "synthetic text"})
        assert response.status == 200 and b"event: token" not in response.body
        await asyncio.wait_for(s.entered.wait(), 5)
        await asyncio.sleep(0.2)
        assert not s.errors, s.errors
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            async with asyncio.timeout(5):
                while True:
                    found = await http.get("/tasks/by-key/stable", headers=second)
                    assert found.status_code == 200
                    if found.json()["content"]:
                        break
                    await asyncio.sleep(0.02)
            assert found.json()["content"] == "original partial"
            task_id = found.json()["id"]
            assert task_id == response.headers[b"x-daemon-task-id"].decode()
            s.release.set()
            assert await jobs(s) == ["completed"]
            final = await http.get("/tasks/by-key/stable", headers=second)
            assert final.status_code == 200 and final.json()["status"] == "completed"
            assert final.json()["content"] == "original partial and final"
            assert final.json()["id"] == task_id and len(s.calls) == 1
        async with s.execution.history.operation(deadline=deadline()) as operation:
            entries = (await operation.verify()).entries
            assert type(entries[-1].command.data) is TerminalOutputPrepare


@pytest.mark.asyncio
async def test_unsolicited_tool_call_never_executes_or_dispatches_again(text_host):
    s = text_host
    _, headers = await device(s, s.account)
    s.unsolicited = True
    async with s.make():
        await submitted(s, headers)
        assert await jobs(s) == ["needs_attention"], s.errors
        assert len(s.calls) == 1 and s.closed.is_set()
        assert s.db.sql("SELECT count(*) FROM task_operations") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"model": "explicit"},
        {"provider": "openrouter"},
        {"metadata": {"tools": True}},
        {"research_effort": "research"},
        {"messages": [{"role": "user", "content": "text", "model": "explicit"}]},
    ],
)
async def test_unsupported_choices_reject_before_acceptance(text_host, options):
    from fastapi import HTTPException

    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        with pytest.raises(HTTPException):
            await submitted(s, headers, **options)
        assert s.db.sql("SELECT count(*) FROM tasks") == "0" and not s.calls


@pytest.mark.asyncio
async def test_stop_during_original_stream_closes_local_fake_not_disconnect(text_host, monkeypatch):
    s = text_host
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.03)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.03)
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make():
        accepted = await submitted(s, first)
        await asyncio.wait_for(s.entered.wait(), 5)
        assert not s.closed.is_set()
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            # This case tests stalled-provider Stop after acknowledged partial;
            # the separate Stop/positive-publication race is allowed to latch.
            async with asyncio.timeout(5):
                while True:
                    partial = await http.get("/tasks/by-key/stable", headers=second)
                    if partial.status_code == 200 and partial.json()["content"]:
                        break
                    await asyncio.sleep(0.02)
            stopped = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=second)
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["cancel_requested"] is True
            assert await jobs(s) == ["cancelled"], s.errors
            assert s.closed.is_set() and len(s.calls) == 1
            found = await http.get("/tasks/by-key/stable", headers=second)
            assert found.json()["status"] == "cancelled"
        async with s.execution.history.operation(deadline=deadline()) as operation:
            entries = (await operation.verify()).entries
            assert not any(type(e.command.data) is TerminalOutputPrepare for e in entries)


@pytest.mark.asyncio
async def test_stop_before_dispatch_preserves_stop_and_never_transports(text_host, monkeypatch):
    from orchestrator.services.restore_history.effects import HistoryEffects

    s = text_host
    ready, resume = asyncio.Event(), asyncio.Event()
    native = HistoryEffects.register_attempt

    async def held(self, *args, **kwargs):
        ready.set()
        await resume.wait()
        return await native(self, *args, **kwargs)

    monkeypatch.setattr(HistoryEffects, "register_attempt", held)
    _, headers = await device(s, s.account)
    async with s.make():
        accepted = await submitted(s, headers)
        await asyncio.wait_for(ready.wait(), 5)
        try:
            async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
                stopped = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
                assert stopped.status_code == 200
        finally:
            resume.set()
        assert await jobs(s) == ["history_effect_unavailable"]
        assert not s.calls
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "0"
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"


@pytest.mark.asyncio
async def test_completed_terminal_wins_stop_without_appending_stop(text_host):
    s = text_host
    _, headers = await device(s, s.account)
    s.release.set()
    async with s.make():
        accepted = await submitted(s, headers)
        assert await jobs(s) == ["completed"]
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            stopped = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
            assert stopped.status_code == 409 and stopped.json()["detail"]["status"] == "completed"
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,status",
    [("bad", 401), ("expired", 401), ("session", 401), ("device", 401), ("foreign", 404)],
)
async def test_cancel_actual_bearer_account_session_device_checks(text_host, kind, status):
    s = text_host
    _, headers = await device(s, s.account)
    s.release.set()
    async with s.make():
        accepted = await submitted(s, headers)
        assert await jobs(s) == ["completed"]
        token_hash = hash_token(headers["Authorization"].removeprefix("Bearer "))
        async with s.db.connection() as conn:
            if kind == "expired":
                await conn.execute(
                    "UPDATE sessions SET access_expires_at=now()-interval '1s' WHERE access_token_hash=$1",
                    token_hash,
                )
            elif kind == "session":
                await conn.execute(
                    "UPDATE sessions SET revoked_at=now() WHERE access_token_hash=$1", token_hash
                )
            elif kind == "device":
                await conn.execute(
                    "UPDATE devices SET revoked_at=now() WHERE id IN (SELECT device_id FROM sessions WHERE access_token_hash=$1)",
                    token_hash,
                )
            elif kind == "foreign":
                other = await s.db.account(conn)
                _, headers = await device(s, other)
            else:
                headers = {"Authorization": "Bearer invalid"}
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            result = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
            assert result.status_code == status, result.text
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "3"
        assert len(s.calls) == 1


@pytest.mark.asyncio
async def test_empty_original_budget_never_dispatches(text_host):
    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        service = EntitlementService(s.w)
        policy = await service.resolve(s.account)
        amount = policy.remaining_for(False)
        hold = await service.reserve(s.account, amount, operation="chat", scope_id=uuid4())
        await service.settle(hold, amount)
        await submitted(s, headers)
        assert await jobs(s) == ["failed"], s.errors
        assert not s.calls


@pytest.mark.asyncio
async def test_missing_worker_collaborator_refuses_before_claim(text_host):
    from orchestrator.services.account_control.transaction import ObservationBlocked

    s = text_host
    async with s.make():
        ctx = s.execution.context()
        ctx.pop("history_text_execution")
        with pytest.raises(ObservationBlocked, match="history_execution_required"):
            await runner.run_chat_task(ctx, str(uuid4()))
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "0" and not s.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [False, True])
async def test_actual_process_death_retains_partial_and_fresh_owner_refuses_old_send(
    text_host, tmp_path, completed
):
    s = text_host
    _, headers = await device(s, s.account)
    packet = dict(
        socket=str(s.db.host),
        database=s.db.name,
        encryption_key=s.encryption_key,
        journal=str(s.journal),
        journal_id=str(s.journal_id),
        path=str(s.path),
        history_id=str(s.identity.history_id),
        key_check=s.identity.key_check,
        authorization=headers["Authorization"],
    )
    root = Path(__file__).resolve().parents[1]
    # Exec a new interpreter with only synthetic input; no inherited pools,
    # environment secrets, application DSN, or fork-adopted H executor.
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(root),
        "TMPDIR": os.environ["TMPDIR"],
        "DAEMON_ENCRYPTION_KEY": s.encryption_key,
    }

    async def start(mode):
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.history_text_process_driver",
            mode,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=root,
            env=environment,
        )
        assert child.stdin is not None
        child.stdin.write(json.dumps(packet).encode() + b"\n")
        await child.stdin.drain()
        child.stdin.close()
        return child

    expected_text = "process original partial" + (" and final" if completed else "")
    first = await start("complete" if completed else "start")
    try:
        assert first.stdout is not None
        assert first.stderr is not None
        raw = await asyncio.wait_for(first.stdout.readline(), 20)
        assert raw, (await first.stderr.read()).decode()
        witness = json.loads(raw)
        assert witness["partial"] == expected_text and witness["calls"] == 1
        assert s.db.sql("SELECT may_have_dispatched FROM task_inference_recovery") == "t"
        assert s.db.sql("SELECT count(*) FROM compute_reservation_metadata") == "1"
        packet["task"] = witness["task"]
        if completed:
            await asyncio.wait_for(first.wait(), 5)
    finally:
        if first.returncode is None:
            first.kill()  # Only this exact test-owned child, never a name/process scan.
        await asyncio.wait_for(first.wait(), 5)
    assert first.returncode == (0 if completed else -9)
    second = await start("reopen")
    try:
        stdout, stderr = await asyncio.wait_for(second.communicate(), 20)
        assert second.returncode == 0, stderr.decode()
        receipt = json.loads(stdout)
        assert receipt == {
            "calls": 0,
            "outcome": "history_effect_unavailable",
            "partial": expected_text,
            "status": "completed" if completed else "running",
            "task": packet["task"],
        }
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "1"
    finally:
        if second.returncode is None:
            second.kill()
        await asyncio.wait_for(second.wait(), 5)


@pytest.mark.asyncio
async def test_native_cancel_wait_rechecks_expiry_before_any_stop_mutation(text_host, monkeypatch):
    from orchestrator.tasks.inference import InferenceRecovery

    s = text_host
    _, headers = await device(s, s.account)
    native_entered = asyncio.Event()
    async with s.make():
        accepted = await submitted(s, headers)
        await asyncio.wait_for(s.entered.wait(), 5)
        # Let the original partial publication settle before introducing SQL contention.
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            async with asyncio.timeout(5):
                while not (await http.get("/tasks/by-key/stable", headers=headers)).json()[
                    "content"
                ]:
                    await asyncio.sleep(0.02)
            original = InferenceRecovery.lock

            async def observed_lock(self, *args, **kwargs):
                native_entered.set()
                return await original(self, *args, **kwargs)

            monkeypatch.setattr(InferenceRecovery, "lock", observed_lock)
            async with s.db.connection() as owner:
                expires_after = asyncio.get_running_loop().time() + 0.22
                await owner.execute(
                    "UPDATE sessions SET access_expires_at=clock_timestamp()+interval '200 milliseconds' WHERE access_token_hash=$1",
                    hash_token(headers["Authorization"].removeprefix("Bearer ")),
                )
                tx = owner.transaction()
                await tx.start()
                await owner.fetchrow(
                    "SELECT id FROM tasks WHERE id=$1 FOR UPDATE", accepted.task.task_id
                )
                pending = asyncio.create_task(
                    http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
                )
                try:
                    await asyncio.wait_for(native_entered.wait(), 3)
                    # Stay within the unchanged native 250ms lock_timeout while
                    # crossing the session's wall-clock expiry after auth proof.
                    await asyncio.sleep(max(0, expires_after - asyncio.get_running_loop().time()))
                finally:
                    await tx.rollback()
                refused = await asyncio.wait_for(pending, 5)
                assert refused.status_code == 401, refused.text
            assert s.db.sql("SELECT cancel_requested_at IS NULL FROM tasks") == "t"
            assert s.db.sql("SELECT count(*) FROM task_history_projection") == "2"
            s.release.set()
            assert await jobs(s) == ["completed"]


@pytest.mark.asyncio
async def test_h_append_then_native_stage_failure_latches_no_transport_or_requeue(
    text_host, monkeypatch
):
    from orchestrator.services.restore_history.model import HistoryError

    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():

        async def failed_stage(conn, entry, *, deadline):
            raise ConnectionError("synthetic native projection failure after H append")

        monkeypatch.setattr(s.execution.projection, "stage_effect", failed_stage)
        await submitted(s, headers)
        outcomes = await jobs(s)
        assert outcomes == [s.execution._invocations[0].uncertainty]
        assert not s.calls and s.execution._blocked
        assert s.execution._invocations[0].uncertainty is not None
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT status FROM tasks") == "running"
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations WHERE status='open'") == "1"
        with pytest.raises(HistoryError):
            await runner.run_chat_task(s.execution.context(), s.db.sql("SELECT id FROM tasks"))
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"


@pytest.mark.asyncio
async def test_scope_link_ack_then_registration_failure_is_inert_no_second_registration(
    text_host, monkeypatch
):
    from orchestrator.tasks.inference import InferenceRecovery
    from orchestrator.services.account_control.transaction import ObservationBlocked

    s = text_host
    _, headers = await device(s, s.account)
    calls = []

    async def failed_registration(self, identity, shared):
        calls.append(identity)
        assert s.db.sql("SELECT compute_scope_id FROM task_attempts") == str(
            identity.financial_scope_id
        )
        shared.block("inference_registration_unavailable")
        raise ObservationBlocked("inference_registration_unavailable")

    monkeypatch.setattr(InferenceRecovery, "register", failed_registration)
    async with s.make():
        await submitted(s, headers)
        result = await jobs(s)
        assert result == ["inference_registration_unavailable"]
        assert len(calls) == 1 and not s.calls
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "0"
        assert s.db.sql("SELECT count(*) FROM attempt_lifecycle_control") == "0"
        assert s.db.sql("SELECT status FROM tasks") == "running"


@pytest.mark.asyncio
async def test_lost_acceptance_commit_ack_never_schedules_even_when_task_committed(
    text_host, monkeypatch
):
    from asyncpg.transaction import Transaction

    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        projection = s.host._acceptance._projection
        native_stage, native_exit = projection.stage, Transaction.__aexit__
        armed = False

        async def stage(conn, entry, *, deadline):
            nonlocal armed
            result = await native_stage(conn, entry, deadline=deadline)
            armed = True
            return result

        async def lost(tx, kind, value, traceback):
            nonlocal armed
            if not armed or kind is not None:
                return await native_exit(tx, kind, value, traceback)
            armed = False
            await native_exit(tx, kind, value, traceback)
            raise ConnectionError("synthetic lost A commit ACK")

        with monkeypatch.context() as patch:
            patch.setattr(projection, "stage", stage)
            patch.setattr(Transaction, "__aexit__", lost)
            with pytest.raises(ConnectionError):
                await submitted(s, headers)
        assert not s.execution._jobs and not s.calls
        assert s.db.sql("SELECT count(*) FROM tasks") == "1"
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "0"
        observed = await s.host.lookup(headers["Authorization"], "stable", deadline=deadline())
        assert observed.kind == "task" and not s.execution._jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("old", ["terminal", "stop"])
async def test_fresh_owners_refuse_old_stop_or_terminal_before_claim_and_body_clear(
    text_host, old, monkeypatch
):
    s = text_host
    _, headers = await device(s, s.account)
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.03)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.03)
    async with s.make():
        if old == "terminal":
            s.release.set()
        accepted = await submitted(s, headers)
        if old == "stop":
            await asyncio.wait_for(s.entered.wait(), 5)
            async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
                async with asyncio.timeout(5):
                    while not (await http.get("/tasks/by-key/stable", headers=headers)).json()[
                        "content"
                    ]:
                        await asyncio.sleep(0.02)
                assert (
                    await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
                ).status_code == 200
        assert await jobs(s) == (["completed"] if old == "terminal" else ["cancelled"])
        before = s.db.sql("SELECT content FROM messages WHERE role='assistant'")
    async with s.make():
        assert (
            await runner.run_chat_task(s.execution.context(), str(accepted.task.task_id))
            == "history_effect_unavailable"
        )
        assert s.db.sql("SELECT content FROM messages WHERE role='assistant'") == before
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert len(s.calls) == 1
