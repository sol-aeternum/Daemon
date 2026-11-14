"""Bounded residual admission, races and physical ownership regressions."""

import asyncio
import os
from dataclasses import replace
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from asyncpg.transaction import Transaction
from httpx import ASGITransport, AsyncClient
import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.model import (
    HistoryError,
    SendPossible,
    StopIntent,
)
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.text_execution import HistoryTextExecution
from orchestrator.tasks import runner
from orchestrator.tasks._invocation import InferenceInvocation
from orchestrator.tasks._history_binding import required_history
from orchestrator.tasks.store import OriginalTaskExpectation, IdempotencyConflict
from orchestrator.attempt_lifecycle import AttemptIdentity
from tests.test_history_text_execution_postgres import (
    text_host as text_host,
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    device,
    submitted,
    jobs,
    deadline,
)


async def partial(s, headers):
    await asyncio.wait_for(s.entered.wait(), 5)
    async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
        async with asyncio.timeout(5):
            while True:
                value = await http.get("/tasks/by-key/stable", headers=headers)
                if value.status_code == 200 and value.json()["content"]:
                    return value.json()
                await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_sql_only_possible_dispatch_quarantines_and_accounts_under_h(text_host, monkeypatch):
    s = text_host
    _, headers = await device(s, s.account)
    entered, proceed = asyncio.Event(), asyncio.Event()
    native_claim, native_settle = HistoryEffects.claim, runner.settle_lost_attempt_holds
    accounting = []

    async def held(self, *args, **kwargs):
        entered.set()
        await proceed.wait()
        return await native_claim(self, *args, **kwargs)

    async def checked(store, pool, task):
        assert required_history.get() is s.execution.worker
        operation = s.execution.history._operation
        assert operation is not None and operation._task is asyncio.current_task()
        accounting.append(task)
        return await native_settle(store, pool, task)

    monkeypatch.setattr(HistoryEffects, "claim", held)
    monkeypatch.setattr(runner, "settle_lost_attempt_holds", checked)
    async with s.make():
        accepted = await submitted(s, headers)
        await asyncio.wait_for(entered.wait(), 5)
        worker = s.execution.worker
        claim = await worker.tasks.claim(
            accepted.task.task_id, worker_id="synthetic-old", lease_s=45
        )
        identity = AttemptIdentity(
            uuid4(), s.account, 0, claim.task_id, claim.epoch, uuid4(), uuid4(), uuid4()
        )
        await worker.tasks.record_compute_scope(
            claim.task_id, claim.epoch, identity.financial_scope_id, binding=claim
        )
        shared = InferenceInvocation()
        await worker.recovery.register(identity, shared)
        hold = await runtime.EntitlementService(s.w).reserve_for_attempt(
            identity, 100, operation="chat"
        )
        await worker.recovery.mark(identity, hold.id, shared)
        s.db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1s'")
        proceed.set()
        assert await jobs(s) == ["needs_attention"]
        assert accounting == [claim.task_id] and not s.calls
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT status FROM entitlement_reservations") == "settled"
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["inherit", "crossed", "downgrade"])
async def test_actual_compute_nested_binding_cannot_cross_or_downgrade(text_host, mode):
    s = text_host
    _, headers = await device(s, s.account)
    checked = []

    async def check(scope):
        checked.append(mode)
        binding = scope.history_binding
        if mode != "inherit":
            bad_binding = (
                replace(binding, state=InferenceInvocation())
                if mode == "crossed"
                else cast(Any, False)
            )
            with pytest.raises(ObservationBlocked, match="history_binding_unavailable"):
                async with runtime.account_compute(s.w, s.account, history_binding=bad_binding):
                    pytest.fail("nested downgrade admitted")
            raise ObservationBlocked("history_binding_unavailable")
        async with runtime.account_compute(s.w, s.account) as nested:
            assert nested.history_binding is binding
            assert nested.history_requirement is s.execution.worker
            assert nested.inference_identity is scope.inference_identity
            assert nested.inference_invocation is scope.inference_invocation

    s.provider_check = check
    s.release.set()
    async with s.make():
        await submitted(s, headers)
        result = await jobs(s)
        assert result == (["completed"] if mode == "inherit" else ["history_binding_unavailable"])
        assert checked == [mode] and len(s.calls) == 1
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["account", "generation", "conversation", "result_message"])
async def test_original_expectation_mismatch_refuses_inside_native_lock_before_mutation(
    text_host, monkeypatch, field
):
    s = text_host
    _, headers = await device(s, s.account)
    native = s.make
    async with native():
        claim_entered, proceed = asyncio.Event(), asyncio.Event()
        original_claim = s.execution.effects.claim

        async def held(*args, **kwargs):
            claim_entered.set()
            await proceed.wait()
            return await original_claim(*args, **kwargs)

        monkeypatch.setattr(s.execution.effects, "claim", held)
        accepted = await submitted(s, headers)
        await asyncio.wait_for(claim_entered.wait(), 5)
        expected = OriginalTaskExpectation(
            s.account,
            0,
            accepted.task.task_id,
            accepted.task.conversation_id,
            accepted.task.user_message_id,
            accepted.task.result_message_id,
            True,
        )
        expected = replace(expected, **{field: 1 if field == "generation" else uuid4()})
        before = s.db.sql("SELECT content FROM messages WHERE role='assistant'")
        with pytest.raises(ObservationBlocked, match="history_original_task_mismatch"):
            await s.execution.worker.tasks._claim(
                accepted.task.task_id, worker_id="negative-test", lease_s=45, expected=expected
            )
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "0"
        assert s.db.sql("SELECT content FROM messages WHERE role='assistant'") == before
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "0"
        assert not s.calls
        proceed.set()
        s.release.set()
        assert await jobs(s) == ["completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_execution_duplicate_or_conflict_never_schedules_a_second_original(
    text_host, conflict
):
    from orchestrator.models import ChatRequest

    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        first = await submitted(s, headers)
        await partial(s, headers)
        with pytest.raises(IdempotencyConflict if conflict else HistoryError):
            await s.host.submit(
                headers["Authorization"],
                "stable",
                ChatRequest(message="different" if conflict else "synthetic text"),
                deadline=deadline(),
            )
        observed = await s.host.lookup(headers["Authorization"], "stable", deadline=deadline())
        assert observed.kind == "task" and observed.snapshot.task_id == first.task.task_id
        assert len(s.execution._jobs) == 1 and len(s.calls) == 1
        assert s.db.sql("SELECT count(*) FROM tasks") == "1"
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        s.release.set()
        assert await jobs(s) == ["completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["embedding", "tool"])
async def test_forbidden_provider_dispatch_injected_into_actual_preparation(
    text_host, monkeypatch, kind
):
    from orchestrator.memory import injection
    from orchestrator import daemon, main
    from orchestrator.services import skill_index

    s = text_host
    _, headers = await device(s, s.account)
    memory = AsyncMock(side_effect=AssertionError("memory context in text preparation"))
    skills = AsyncMock(side_effect=AssertionError("skills index in text preparation"))
    monkeypatch.setattr(injection, "build_memory_context", memory)
    # Both the request path and the task runner resolve the index here.
    monkeypatch.setattr(skill_index, "build_skill_index", skills)
    monkeypatch.setattr(main, "build_skill_index", skills)
    followup = AsyncMock(side_effect=AssertionError("background trust followup"))
    monkeypatch.setattr(
        daemon,
        "_lazy_import_trust_signals",
        lambda: SimpleNamespace(apply_implicit_positive_signal=followup),
    )
    original = runner._text_system_prompt

    async def injected(memory_store, claim):
        await original(memory_store, claim)
        assert required_history.get() is s.execution.worker
        if kind == "embedding":
            async with runtime.metered_embedding_call(cast(Any, None), 1):
                pytest.fail("embedding yielded")
        else:
            async with runtime.metered_tool_call(cast(Any, None)):
                pytest.fail("tool yielded")

    monkeypatch.setattr(runner, "_text_system_prompt", injected)
    async with s.make():
        await submitted(s, headers)
        assert await jobs(s) == ["failed"]
        assert not s.calls
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "0"
        memory.assert_not_called()
        skills.assert_not_called()
        followup.assert_not_called()


@pytest.mark.asyncio
async def test_successful_original_text_path_never_calls_memory_skills_tools_or_followups(
    text_host, monkeypatch
):
    from orchestrator.memory import injection
    from orchestrator import daemon, main
    from orchestrator.services import skill_index

    s = text_host
    _, headers = await device(s, s.account)
    s.release.set()
    memory, skills, trust, enqueue = [
        AsyncMock(side_effect=AssertionError("forbidden text side effect")) for _ in range(4)
    ]
    registry = Mock(side_effect=AssertionError("default tools constructed"))
    monkeypatch.setattr(injection, "build_memory_context", memory)
    # Both the request path and the task runner resolve the index here.
    monkeypatch.setattr(skill_index, "build_skill_index", skills)
    monkeypatch.setattr(main, "build_skill_index", skills)
    monkeypatch.setattr(daemon, "create_default_registry", registry)
    monkeypatch.setattr(
        daemon,
        "_lazy_import_trust_signals",
        lambda: SimpleNamespace(apply_implicit_positive_signal=trust),
    )
    monkeypatch.setattr(daemon, "enqueue_account_job", enqueue)
    async with s.make():
        await submitted(s, headers)
        assert await jobs(s) == ["completed"]
        assert len(s.calls) == 1
        for spy in (memory, skills, trust, registry, enqueue):
            spy.assert_not_called()
        assert s.db.sql("SELECT count(*) FROM memory_knowledge_clock") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["stop", "terminal"])
async def test_actual_stop_terminal_commit_race_under_h(text_host, monkeypatch, winner):
    s = text_host
    _, headers = await device(s, s.account)
    held, release, terminal_ready, terminal_go, terminal_started = [
        asyncio.Event() for _ in range(5)
    ]
    staged = []
    async with s.make():
        projection = s.execution.projection
        native_terminal, native_effect = projection.stage_terminal, projection.stage_effect
        native_complete = s.execution.effects.complete

        async def stage_terminal(conn, entry, *, deadline):
            result = await native_terminal(conn, entry, deadline=deadline)
            staged.append("terminal")
            if winner == "terminal":
                held.set()
                await release.wait()
            return result

        async def stage_effect(conn, entry, *, deadline):
            result = await native_effect(conn, entry, deadline=deadline)
            if type(entry.command.data) is StopIntent:
                staged.append("stop")
                held.set()
                await release.wait()
            return result

        async def complete(*args, **kwargs):
            if winner == "stop":
                # Financial stream settlement has finished before this sink call.
                # Hold only the test contender, not H or a native transaction.
                terminal_ready.set()
                await terminal_go.wait()
                terminal_started.set()
            return await native_complete(*args, **kwargs)

        monkeypatch.setattr(projection, "stage_terminal", stage_terminal)
        monkeypatch.setattr(projection, "stage_effect", stage_effect)
        monkeypatch.setattr(s.execution.effects, "complete", complete)
        accepted = await submitted(s, headers)
        await partial(s, headers)
        s.release.set()
        if winner == "stop":
            await asyncio.wait_for(terminal_ready.wait(), 1)
        else:
            await asyncio.wait_for(held.wait(), 1)
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            stop = asyncio.create_task(
                http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
            )
            try:
                if winner == "stop":
                    await asyncio.wait_for(held.wait(), 1)
                    terminal_go.set()
                    await asyncio.wait_for(terminal_started.wait(), 1)
                else:
                    # The competitor is queued while terminal owns H and SQL.
                    await asyncio.sleep(0.02)
                assert not stop.done()
                release.set()
                response = await asyncio.wait_for(stop, 5)
                outcomes = await jobs(s)
            finally:
                release.set()
                terminal_go.set()
                await asyncio.gather(stop, return_exceptions=True)
        if winner == "terminal":
            assert response.status_code == 409 and outcomes == ["completed"]
            assert staged == ["terminal"]
        else:
            assert response.status_code in (200, 503)
            assert outcomes != ["completed"] and staged == ["stop"]
            assert s.db.sql("SELECT cancel_requested_at IS NOT NULL FROM tasks") == "t"
            assert s.db.sql("SELECT status FROM tasks") != "completed"
        assert len(s.calls) == 1 and s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,after_ack", [("mark", False), ("mark", True), ("terminal", False), ("terminal", True)]
)
async def test_owned_native_commit_faults_retain_original_negative_evidence(
    text_host, monkeypatch, boundary, after_ack
):
    s = text_host
    _, headers = await device(s, s.account)
    s.release.set()
    async with s.make():
        projection = s.execution.projection
        stage_name = "stage_effect" if boundary == "mark" else "stage_terminal"
        native_stage, native_commit = getattr(projection, stage_name), Transaction.commit
        armed = []

        async def stage(conn, entry, *, deadline):
            result = await native_stage(conn, entry, deadline=deadline)
            armed.append(conn._con)
            return result

        async def commit(tx):
            if not armed or tx._connection is not armed[0]:
                return await native_commit(tx)
            armed.clear()
            if after_ack:
                await native_commit(tx)
            raise ConnectionError("synthetic native COMMIT acknowledgement unavailable")

        monkeypatch.setattr(projection, stage_name, stage)
        monkeypatch.setattr(Transaction, "commit", commit)
        await submitted(s, headers)
        outcomes = await jobs(s)
        assert outcomes == [s.execution._invocations[0].uncertainty]
        assert outcomes != ["completed"] and s.execution._blocked
        assert len(s.calls) == (0 if boundary == "mark" else 1)
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT status FROM tasks") == (
            "completed" if boundary == "terminal" and after_ack else "running"
        )
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == str(
            (2 if boundary == "mark" else 3) - (0 if after_ack else 1)
        )
        if boundary == "mark":
            assert s.db.sql("SELECT status FROM entitlement_reservations") == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["worker_reset", "history_release", "stop_auth_reset"])
async def test_acknowledged_effect_cleanup_failure_cannot_promote_receipt(
    text_host, monkeypatch, boundary
):
    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        projection = s.execution.projection
        armed, fired = [], []
        native_stage = projection.stage_effect
        native_reset = s.w._reset
        native_exit = HistoryCoordinator._exit

        async def stage(conn, entry, *, deadline):
            result = await native_stage(conn, entry, deadline=deadline)
            if boundary != "stop_auth_reset" and type(entry.command.data) is SendPossible:
                armed.append(conn._con)
            return result

        async def reset(conn):
            if native_reset is not None:
                await native_reset(conn)
            else:
                await conn.execute(conn.get_reset_query())
            if armed and boundary != "history_release" and conn is armed[0]:
                armed.clear()
                fired.append(boundary)
                raise RuntimeError("synthetic actual holder reset failure after ACK")

        def exit_history(owner, original):
            native_exit(owner, original)
            if owner is s.execution.history and boundary == "history_release" and armed:
                armed.clear()
                fired.append(boundary)
                raise OSError("synthetic H release acknowledgement unavailable")

        monkeypatch.setattr(projection, "stage_effect", stage)
        monkeypatch.setattr(s.w, "_reset", reset)
        monkeypatch.setattr(HistoryCoordinator, "_exit", exit_history)
        if boundary == "stop_auth_reset":
            accepted = await submitted(s, headers)
            await partial(s, headers)
            native_auth = s.host._auth

            async def auth(conn, *args):
                result = await native_auth(conn, *args)
                if result is not None:
                    armed.append(conn._con)
                return result

            monkeypatch.setattr(s.host, "_auth", auth)
            async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
                stopped = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
                assert stopped.status_code == 503
            assert s.db.sql("SELECT cancel_requested_at IS NOT NULL FROM tasks") == "t"
            s.release.set()  # Drain this test's exact local fake read, not a remote-stop claim.
        else:
            await submitted(s, headers)
        outcomes = await jobs(s)
        assert fired == [boundary] and outcomes != ["completed"]
        assert s.execution._invocations[0].uncertainty is not None
        assert len(s.calls) == (1 if boundary == "stop_auth_reset" else 0)
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == (
            "3" if boundary == "stop_auth_reset" else "2"
        )


@pytest.mark.asyncio
async def test_shutdown_retains_all_exact_children_until_physical_cleanup_after_timeout():
    # Isolate lifecycle mechanics: no fake dispatch/recovery authority is used.
    execution: Any = object.__new__(HistoryTextExecution)
    execution._pid = os.getpid()
    execution._loop = None
    execution._closing = execution._closed = execution._blocked = False
    execution._ready = True
    execution._shutdown_task = None
    execution._invocations = []
    execution.effects = None
    execution.history = SimpleNamespace(aclose=AsyncMock(), _blocked=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def failed():
        raise RuntimeError("synthetic failed owned child")

    async def physical():
        try:
            await asyncio.Future()
        finally:
            entered.set()
            await release.wait()

    first, second = asyncio.create_task(failed()), asyncio.create_task(physical())
    await asyncio.sleep(0)
    execution._jobs = {first, second}
    execution._job_tasks = {first: "first", second: "second"}
    execution._outcomes = {}
    for child in execution._jobs:
        child.add_done_callback(execution._finished)
    close = asyncio.create_task(execution.aclose(deadline=time.monotonic() + 0.03))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.05)
        assert not close.done() and execution._closing and not execution._ready
        owner = execution._shutdown_task
        execution.history.aclose.assert_not_called()
        assert len(execution._jobs) == 2
        release.set()
        with pytest.raises(TimeoutError):
            await close
        assert execution._shutdown_task is owner and first.done() and second.done()
        assert execution._blocked and execution._outcomes["first"] == "failed_owner"
        execution.history.aclose.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(close, first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_host_shutdown_deadline_retains_actual_native_reset_before_h_and_pools(
    text_host, monkeypatch
):
    from tests.test_history_coordination import available

    s = text_host
    _, headers = await device(s, s.account)
    entered, release = asyncio.Event(), asyncio.Event()
    s.expected_shutdown_timeout = True
    async with s.make():
        armed = []
        projection = s.execution.projection
        native_stage, native_reset = projection.stage_effect, s.w._reset

        async def stage(conn, entry, *, deadline):
            result = await native_stage(conn, entry, deadline=deadline)
            if type(entry.command.data) is SendPossible:
                armed.append(conn._con)
            return result

        async def reset(conn):
            if armed and conn is armed[0]:
                armed.clear()
                entered.set()
                await release.wait()
            if native_reset is not None:
                await native_reset(conn)
            else:
                await conn.execute(conn.get_reset_query())

        monkeypatch.setattr(projection, "stage_effect", stage)
        monkeypatch.setattr(s.w, "_reset", reset)
        await submitted(s, headers)
        await asyncio.wait_for(entered.wait(), 5)
        close = asyncio.create_task(s.host.aclose(deadline=time.monotonic() + 0.04))
        try:
            await asyncio.sleep(0.08)
            assert not close.done() and not s.host._ready and s.host._closing
            assert not available(s.path, "OP_LOCK")
            assert not s.w._closed and not s.host._acceptance._tasks._pool._closed
            assert not s.execution.history._closed
            assert len(s.execution._jobs) == 1
            release.set()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(close, 5)
            assert all(child.done() for child in s.execution._jobs)
            assert s.execution._closed and s.execution._blocked
            assert s.host._reader._history._closed
            assert s.host._acceptance._history._closed
            assert not s.host._closed
            assert available(s.path, "OP_LOCK")
            assert not s.w._closed and not s.calls
            assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        finally:
            release.set()
            await asyncio.gather(close, return_exceptions=True)


@pytest.mark.asyncio
async def test_text_execution_rejects_single_connection_before_history_open(text_host, monkeypatch):
    s = text_host
    async with s.make():
        execution = s.execution
        opened = AsyncMock(side_effect=AssertionError("history opened before capacity refusal"))
        monkeypatch.setattr(execution.history, "open", opened)
        async with s.db.pool(max_size=1) as pool:
            with pytest.raises(HistoryError, match="text_execution_refused"):
                HistoryTextExecution(
                    pool,
                    execution.encryption,
                    execution.settings,
                    execution.history,
                    execution.projection,
                )
            opened.assert_not_called()
            assert not pool._closed
        assert not s.calls and s.execution.worker is not None


@pytest.mark.asyncio
async def test_host_failed_runner_still_closes_all_histories_without_retry(text_host):
    s = text_host
    original = RuntimeError("synthetic original runner failure")
    with pytest.raises(RuntimeError) as repeated:
        async with s.make():
            assert s.w.get_max_size() == 2

            async def failed():
                raise original

            child = asyncio.create_task(failed())
            s.execution._jobs.add(child)
            s.execution._job_tasks[child] = "synthetic-failed-owner"
            child.add_done_callback(s.execution._finished)
            await asyncio.sleep(0)
            with pytest.raises(RuntimeError) as first:
                await s.host.aclose(deadline=deadline())
            assert first.value is original
            shutdown = s.host._shutdown_task
            assert shutdown.done() and child.done()
            assert s.execution.history._closed
            assert s.host._reader._history._closed
            assert s.host._acceptance._history._closed
            assert not s.host._closed and not s.w._closed
            assert not s.host._acceptance._tasks._pool._closed
            with pytest.raises(RuntimeError) as again:
                await s.host.aclose(deadline=deadline())
            assert again.value is original and s.host._shutdown_task is shutdown
    assert repeated.value is original
