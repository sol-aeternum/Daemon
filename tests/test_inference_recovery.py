"""Passive fence and non-composition contracts, not remote-cessation evidence."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.tasks._invocation import InferenceInterrupted, InferenceInvocation, invocation
from orchestrator.tasks.inference import InferenceRecovery


def test_uncertainty_first_reason_is_permanent_but_known_call_failure_is_distinct():
    state = InferenceInvocation()
    state.call_failed = True
    state.require_effect()  # sound DB authority may quarantine/account, never send
    with pytest.raises(InferenceInterrupted):
        state.require_send()
    state.block("unknown_commit")
    state.block("later_release")
    assert state.uncertainty == "unknown_commit"
    with pytest.raises(AttributeError):
        state.uncertainty = None  # type: ignore[misc]
    with pytest.raises(ObservationBlocked):
        state.require_effect()


@pytest.mark.asyncio
async def test_scheduled_child_and_parent_inherit_one_latch():
    state = InferenceInvocation()
    token = invocation.set(state)
    proceed = asyncio.Event()

    async def child():
        await proceed.wait()
        assert invocation.get() is state
        state.require_effect()

    try:
        job = asyncio.create_task(child())
        state.block("unknown_release")
        proceed.set()
        with pytest.raises(ObservationBlocked):
            await job
        assert invocation.get() is state
    finally:
        invocation.reset(token)


@pytest.mark.asyncio
async def test_nested_scope_and_scope_finally_start_no_settlement_after_uncertainty(monkeypatch):
    service = SimpleNamespace(settle=AsyncMock(), reconcile_expired_reservations=AsyncMock())
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
    state = InferenceInvocation()
    account = uuid4()
    identity = AttemptIdentity(uuid4(), account, 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
    token = invocation.set(state)
    try:
        async with runtime.account_compute(
            object(), account, inference_identity=identity, inference_recovery=cast(Any, object())
        ) as scope:
            reservation = SimpleNamespace(id=uuid4())
            scope.outstanding[reservation.id] = runtime.ReservationHold(reservation, 100)
            state.block("mark_release_unknown")
            with pytest.raises(ObservationBlocked):
                async with runtime.account_compute(object(), account):
                    pytest.fail("nested scope admitted after closure")
            with pytest.raises(ObservationBlocked):
                await scope.settle(reservation, 100)
        service.settle.assert_not_awaited()
        service.reconcile_expired_reservations.assert_not_awaited()
    finally:
        invocation.reset(token)


def test_production_task_composition_cannot_install_inference_collaborator():
    root = Path(__file__).resolve().parents[1]
    locations = ("orchestrator/worker/worker.py", "orchestrator/routes/tasks.py")
    count = 0
    for name in locations:
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "InferenceRecovery"
                if node.func.id == "TaskStore":
                    count += 1
                    assert len(node.args) == 3 and not node.keywords
        assert "abandoned_pass" not in (root / name).read_text()
    assert count == 2


@pytest.mark.asyncio
async def test_abandoned_page_fixed_cap_same_absolute_deadline_and_uncertainty_stops(monkeypatch):
    account = uuid4()
    page = [
        AttemptIdentity(uuid4(), account, 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
        for _ in range(32)
    ]
    import json

    recovery = InferenceRecovery(object())
    deadlines = []
    identities = []

    async def call(sql, args, *, deadline, state, identity=None):
        deadlines.append(deadline)
        if "abandoned_page" in sql:
            return [json.loads(p.wire()) for p in page]
        identities.append(args[0])
        if len(identities) == 2:
            state.block("close_ack_unknown")
            raise ObservationBlocked()
        return {"identity": json.loads(args[0]), "closed": True, "changed": True}

    monkeypatch.setattr(recovery, "_owned", call)
    assert await recovery.abandoned_pass() == (1, True)
    assert len(identities) == 2 and len(set(deadlines)) == 1
    assert recovery.after_id == page[0].lifecycle_id
