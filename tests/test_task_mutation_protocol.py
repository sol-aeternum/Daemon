"""Accepted observation/mutation protocol with real SQL and labelled response faults."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from orchestrator.services.account_control.transaction import (
    ObservationBlocked,
    control_transaction,
)
from orchestrator.tasks import runner
from orchestrator.tasks.store import TaskStore
from tests.durable_tasks_support import LEASE_S, Env, durable_env_fixture
from tests.test_task_generation_publication import FaultPool, claimed, raw

env = durable_env_fixture(initialized=True)
pytestmark = pytest.mark.asyncio


async def test_probe_is_readonly_coherent_and_does_not_wait_for_writer(env: Env):
    claim = await claimed(env)
    before = await raw(env, claim)
    async with env.pool.acquire() as writer, writer.transaction():
        await writer.execute(
            "UPDATE tasks SET cancel_requested_at=clock_timestamp() WHERE id=$1", claim.task_id
        )
        schema = await writer.fetchval("SHOW search_path")
        # Enforce read-only on the actual probe connection, not a mock SQL matcher.
        async with asyncpg.create_pool(
            os.environ["TASKS_TEST_DATABASE_URL"],
            min_size=1,
            max_size=1,
            server_settings={"search_path": schema, "default_transaction_read_only": "on"},
        ) as readonly:
            store = TaskStore(readonly, env.tasks._enc, env.memory)
            async with asyncio.timeout(0.05):
                observation = await store.observe_attempt(claim)
            assert not observation.cancel_requested  # coherent committed snapshot, not staged Stop
            assert not observation.account_suspended and observation.refusal is None
    after = await raw(env, claim)
    assert after[0]["cancel_requested_at"] is not None  # only the independent writer changed it
    assert after[0]["event_seq"] == before[0]["event_seq"]
    assert after[1:] == before[1:]


@pytest.mark.parametrize("phase", ["before_commit", "after_commit", "release"])
@pytest.mark.parametrize("kind", ["partial", "final", "renewal"])
async def test_unknown_closes_admission_before_queued_writer_or_renewal(
    env: Env, monkeypatch, phase, kind
):
    claim = await claimed(env)
    state = runner.AttemptState(claim, lease_deadline=asyncio.get_running_loop().time() + 40)
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.02)
    observed, entered, continue_write = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_observe = env.tasks.observe_attempt

    async def observe(binding):
        result = await real_observe(binding)
        observed.set()
        return result

    monkeypatch.setattr(env.tasks, "observe_attempt", observe)
    idle_execution = asyncio.create_task(asyncio.Event().wait())
    state.execution = idle_execution
    heartbeat = asyncio.create_task(runner._heartbeat(env.tasks, state, idle_execution))
    state.heartbeat = heartbeat
    fault = FaultPool(env.pool, phase)
    store = TaskStore(fault, env.tasks._enc, env.memory)  # type: ignore[arg-type]
    calls = []

    async def writer():
        async with runner._mutation(state, final=kind == "final"):
            entered.set()
            await continue_write.wait()
            if kind == "final":
                await store.complete(claim.task_id, claim.epoch, content="synthetic", binding=claim)
            elif kind == "partial":
                await store.write_partial(
                    claim.task_id, claim.epoch, content="partial", delta_seq=1, binding=claim
                )
            else:
                await store.heartbeat(claim.task_id, claim.epoch, lease_s=LEASE_S, binding=claim)

    async def queued():
        async with runner._mutation(state):
            calls.append("forbidden dispatch")

    write = asyncio.create_task(writer())
    await asyncio.wait_for(entered.wait(), 2)
    queued_write = asyncio.create_task(queued())
    try:
        await asyncio.wait_for(
            observed.wait(), 2
        )  # actual SQL observation under held mutation gate
        deadline = state.lease_deadline
        continue_write.set()
        outcomes = await asyncio.gather(write, queued_write, return_exceptions=True)
        assert all(isinstance(outcome, ObservationBlocked) for outcome in outcomes)
        assert state.blocked == (
            "task_committed_result_unavailable"
            if phase == "release"
            else "task_transition_indeterminate"
        )
        assert state.phase == "closed" and calls == []
        assert state.lease_deadline == deadline  # probe NEVER renews
        assert fault.commits == 1
        assert fault.terminates == int(phase != "release")  # preserve native release transfer
        assert await env.pool.fetchval(
            "SELECT count(*) FROM task_events WHERE task_id=$1 AND kind='finished'", claim.task_id
        ) == int(kind == "final" and phase != "before_commit")
    finally:
        for task in (write, queued_write, heartbeat, idle_execution):
            task.cancel()
        await asyncio.gather(write, queued_write, heartbeat, idle_execution, return_exceptions=True)


async def test_busy_nonfinal_gate_does_not_hide_local_deadline(env: Env, monkeypatch):
    claim = await claimed(env)
    # Read real committed state before controlling the local clock. A SQL timeout
    # is a different safe outcome (blocked, not fenced), which caused #575.
    observation = await env.tasks.observe_attempt(claim)
    now = 1000.0
    state = runner.AttemptState(claim, lease_deadline=now + 1)
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.01)
    observed = []

    async def observe(binding):
        nonlocal now
        assert state.write_lock.locked()
        observed.append(binding)
        now = state.lease_deadline  # expire only after a successful busy-gate read
        return observation

    async def advance(delay):
        nonlocal now
        now += delay
        await asyncio.sleep(0)

    clock = SimpleNamespace(time=lambda: now)
    monkeypatch.setattr(env.tasks, "observe_attempt", observe)
    monkeypatch.setattr(
        runner,
        "asyncio",
        SimpleNamespace(**(vars(asyncio) | {"get_running_loop": lambda: clock, "sleep": advance})),
    )
    # Gate held by another local write, never taken by observer.
    await state.write_lock.acquire()
    execution = asyncio.create_task(asyncio.Event().wait())
    state.execution = execution
    try:
        await asyncio.wait_for(runner._heartbeat(env.tasks, state, execution), 0.3)
        assert observed == [claim] and state.blocked is None
        assert state.fenced and execution.cancelling()
        with pytest.raises(runner.LeaseLost):
            # A new caller after the deadline cannot dispatch even if gate releases.
            state.write_lock.release()
            async with runner._mutation(state):
                raise AssertionError("late mutation")
    finally:
        if state.write_lock.locked():
            state.write_lock.release()
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


@pytest.mark.parametrize("phase", ["body", "commit", "release"])
async def test_original_cancellation_and_phase_survive_owned_transaction(env: Env, phase):
    original = asyncio.CancelledError("synthetic shutdown")
    fault = FaultPool(env.pool, "none")
    acquire, release = fault.acquire, fault.release

    async def acquire_cancel():
        conn = await acquire()
        transaction = conn.transaction

        def tx(**kwargs):
            real = transaction(**kwargs)
            commit = real.commit

            async def cancelled_commit():
                await commit()  # real ACK truth; simulated cancellation before reply reaches owner
                raise original

            if phase == "commit":
                real.commit = cancelled_commit
            return real

        conn.transaction = tx
        return conn

    async def release_cancel(conn):
        await release(conn)
        raise original

    fault.acquire = acquire_cancel
    if phase == "release":
        fault.release = release_cancel
    with pytest.raises(asyncio.CancelledError) as error:
        async with control_transaction(fault) as conn:
            await conn.execute("SELECT 1")
            if phase == "body":
                raise original
    assert error.value is original
    assert getattr(original, "task_commit_attempted") is (phase != "body")
    assert getattr(original, "task_commit_acknowledged") is (phase == "release")
    assert fault.terminates == int(phase != "release")


async def test_valid_renewal_uses_request_time_not_ack_or_cadence(env: Env, monkeypatch):
    claim = await claimed(env)
    loop = asyncio.get_running_loop()
    state = runner.AttemptState(claim, lease_deadline=loop.time() + 39)
    before = state.lease_deadline
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.01)
    real = env.tasks.heartbeat
    times = []

    async def delayed(*args, **kwargs):
        entered = loop.time()
        await asyncio.sleep(
            0.02
        )  # labelled synthetic latency > cadence, well inside existing I/O cap
        result = await real(*args, **kwargs)  # actual validation/renewal SQL
        times.append((entered, loop.time()))
        return result

    monkeypatch.setattr(env.tasks, "heartbeat", delayed)
    execution = asyncio.create_task(asyncio.Event().wait())
    state.execution = execution
    heartbeat = asyncio.create_task(runner._heartbeat(env.tasks, state, execution))
    state.heartbeat = heartbeat
    try:
        async with asyncio.timeout(2):
            while state.lease_deadline == before and state.blocked is None:
                await asyncio.sleep(0)
        assert state.blocked is None and len(times) == 1
        entered, returned = times[0]
        assert before < state.lease_deadline <= entered + runner.LEASE_S - runner.LEASE_SAFETY_S
        assert state.lease_deadline < returned + runner.LEASE_S - runner.LEASE_SAFETY_S - 0.015
        assert runner.HEARTBEAT_IO_TIMEOUT_S == 10.0
    finally:
        heartbeat.cancel()
        execution.cancel()
        await asyncio.gather(heartbeat, execution, return_exceptions=True)


@pytest.mark.parametrize("phase", ["preparation", "begin", "commit"])
async def test_explicit_short_io_cap_closes_before_any_next_dispatch(env: Env, monkeypatch, phase):
    """Synthetic withheld response around real PG prepare/BEGIN/COMMIT.

    In COMMIT case the TEST knows server ACK; the owning helper has not received
    its commit return and must report attempted=True/acknowledged=False.
    """
    claim = await claimed(env)
    before = await raw(env, claim)
    state = runner.AttemptState(claim, lease_deadline=asyncio.get_running_loop().time() + 40)
    deadline = state.lease_deadline
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.01)
    monkeypatch.setattr(runner, "HEARTBEAT_IO_TIMEOUT_S", 0.05)
    reached, hold = asyncio.Event(), asyncio.Event()
    fault = FaultPool(env.pool, "none")
    real_acquire = fault.acquire
    cancellations = []

    async def withheld():
        reached.set()
        try:
            await hold.wait()
        except asyncio.CancelledError as original:
            cancellations.append(original)
            raise  # exact object, never a replacement CancelledError

    async def acquire():
        conn: Any = await real_acquire()
        real_transaction = conn.transaction

        def transaction(**kwargs):
            tx = real_transaction(**kwargs)
            start, commit = tx.start, tx.commit

            async def begin():
                await start()
                if phase == "begin":
                    await withheld()

            async def finish():
                await commit()
                if phase == "commit":
                    await withheld()

            tx.start, tx.commit = begin, finish
            return tx

        conn.transaction = transaction
        real_fetch = conn.fetch

        async def fetch(query, *args):
            if phase == "preparation" and query.startswith("SELECT id,role FROM messages"):
                await conn.real.prepare(
                    query
                )  # actual statement preparation, not invented plaintext
                await withheld()
            return await real_fetch(query, *args)

        conn.fetch = fetch
        return conn

    fault.acquire = acquire
    renewing = TaskStore(fault, env.tasks._enc, env.memory)  # type: ignore[arg-type]
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append("forbidden dispatch")
        raise AssertionError("no second write, provider, publication or settlement authority")

    for method in (
        "write_partial",
        "acknowledge_cancel",
        "fail_attempt",
        "defer_execution",
        "defer_claim",
        "finish_refusal",
        "record_event",
        "begin_execution",
        "begin_operation",
    ):
        monkeypatch.setattr(env.tasks, method, forbidden)
    monkeypatch.setattr(runner, "settle_lost_attempt_holds", forbidden)
    monkeypatch.setattr(runner, "_publish", forbidden)
    execution = asyncio.create_task(asyncio.Event().wait())
    state.execution = execution
    heartbeat = asyncio.create_task(runner._heartbeat(renewing, state, execution))
    state.heartbeat = heartbeat

    async def queued():
        async with runner._mutation(state):
            await forbidden()  # models queued next SQL/provider; admission must prevent dispatch

    queued_write = None
    try:
        await asyncio.wait_for(reached.wait(), 2)
        assert state.write_lock.locked() and state.phase == "mutating"
        queued_write = asyncio.create_task(queued())
        if phase == "commit":
            observed = await env.tasks.observe_attempt(claim)  # real independent coherent read
            assert not observed.cancel_requested and observed.refusal is None
            current = await env.pool.fetchval(
                "SELECT lease_expires_at FROM tasks WHERE id=$1", claim.task_id
            )
            assert (
                current > before[0]["lease_expires_at"]
            )  # physical commit, NOT caller attribution
        await asyncio.wait_for(heartbeat, 2)
        with pytest.raises(ObservationBlocked):
            await queued_write
        assert state.phase == "closed" and state.blocked == "task_observation_indeterminate"
        assert len(cancellations) == 1
        assert getattr(cancellations[0], "task_commit_attempted") is (phase == "commit")
        assert getattr(cancellations[0], "task_commit_acknowledged") is False
        assert state.lease_deadline == deadline and calls == []
        assert await runner._resolve(env.tasks, state) == "task_observation_indeterminate"
        assert calls == [] and execution.cancelling()
        assert fault.commits == int(phase == "commit") and fault.terminates == 1
        after = await raw(env, claim)
        assert after[0]["status"] == "running" and after[0]["event_seq"] == before[0]["event_seq"]
        assert after[1] == before[1]
        assert (
            await env.pool.fetchval(
                "SELECT count(*) FROM task_events WHERE task_id=$1 AND kind='finished'",
                claim.task_id,
            )
            == 0
        )
    finally:
        for task in (heartbeat, execution, queued_write):
            if task is not None:
                task.cancel()
        owned: list[asyncio.Task[Any]] = [
            t for t in (heartbeat, execution, queued_write) if t is not None
        ]
        await asyncio.gather(*owned, return_exceptions=True)
