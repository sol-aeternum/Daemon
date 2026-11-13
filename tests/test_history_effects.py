"""Closed uncalled seams and constructor/import no-I/O probes."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any, cast
from uuid import uuid4
from types import SimpleNamespace

import asyncpg
from cryptography.fernet import Fernet
import pytest

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.services.restore_history.model import Identity
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.store import Ownership
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.tasks._invocation import InferenceInvocation
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.inference import InferenceRecovery
from orchestrator.tasks.store import CancelOutcome, Claim, TaskStore
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.restore_history_support import HISTORY, KEY, key_check

EFFECTS = "orchestrator/services/restore_history/effects.py"


def test_only_exact_type_import_is_permitted_without_consumer_admission():
    source = "from orchestrator.tasks.inference import InferenceRecovery"
    assert EFFECTS not in ADMITTED
    assert composition_violations({EFFECTS: source}) == []
    for changed in (
        source + " as R",
        source + ", check_principal",
        source + "\nInferenceRecovery(pool)",
        source + "\nx._inference = recovery",
        "import orchestrator.tasks.inference as recovery",
    ):
        assert composition_violations({EFFECTS: changed})
    assert composition_violations({"orchestrator/main.py": source})


@pytest.mark.parametrize(
    "method,owner,native,path",
    [
        ("_mark_with_stage", "_mark", "mark", "orchestrator/tasks/inference.py"),
        (
            "_request_cancel_with_stage",
            "_cancel_owned",
            "request_cancel",
            "orchestrator/tasks/store.py",
        ),
    ],
)
def test_effect_staging_seams_allow_only_default_none_and_exact_uncalled_join(
    method, owner, native, path
):
    ordinary_args = "identity,reservation,state" if native == "mark" else "user_id,task_id"
    ordinary = f"async def {native}(self):\n await self.{method}({ordinary_args},stage=None)"
    assert composition_violations({path: ordinary}) == []
    assert composition_violations({path: ordinary.replace("stage=None", "stage=hook")})
    receiver = "recovery" if native == "mark" else "self._tasks"
    joined_args = "identity,reservation,invocation_state" if native == "mark" else "account,task"
    extra = ",authorization=authorization" if native == "request_cancel" else ""
    joined = (
        f"async def {owner}(self):\n await {receiver}.{method}({joined_args},stage=stage{extra})"
    )
    assert composition_violations({EFFECTS: joined}) == []
    for changed in (
        joined.replace("stage=stage", "stage=hook"),
        joined.replace(joined_args, "a,b,c"),
        joined.replace("def " + owner, "def activate"),
        joined.replace(f"await {receiver}.{method}", f"alias = {receiver}.{method}\n await alias"),
    ):
        assert composition_violations({EFFECTS: changed})
    assert composition_violations({"orchestrator/main.py": joined})


@pytest.mark.parametrize("method", ["_mark", "_cancel", "_complete", "_write_partial"])
def test_cleanup_scope_is_inside_exact_uncalled_effect_methods(method):
    source = f"async def {method}(self):\n async with retain_pool_cleanup():\n  pass"
    assert composition_violations({EFFECTS: source}) == []
    assert composition_violations({EFFECTS: source.replace(method, "activate")})
    assert composition_violations({"orchestrator/tasks/store.py": source})
    assert composition_violations(
        {EFFECTS: source.replace("retain_pool_cleanup()", "retain_pool_cleanup(extra=True)")}
    )
    assert composition_violations(
        {EFFECTS: source.replace("retain_pool_cleanup()", "alias.retain_pool_cleanup()")}
    )


@pytest.mark.parametrize("symbol", ["HistoryProjection", "HistoryEffects"])
def test_new_history_constructors_are_not_runtime_admission(symbol):
    assert composition_violations({"orchestrator/main.py": f"{symbol}(a,b,c)"})
    assert composition_violations({"orchestrator/main.py": f"Alias = {symbol}\nAlias(a,b,c)"})


def test_mark_transaction_stage_alias_and_alternate_owner_are_not_admitted():
    good = "async def _mark_with_stage(self):\n await self._owned(sql,args,stage=original_result)"
    assert composition_violations({"orchestrator/tasks/inference.py": good}) == []
    for bad in (
        good.replace("def _mark_with_stage", "def activate"),
        good.replace("stage=original_result", "stage=hook"),
        "async def activate(self):\n fn=self._owned\n await fn(sql,args,stage=hook)",
    ):
        assert composition_violations({"orchestrator/tasks/inference.py": bad})
    assert composition_violations({EFFECTS: good})


def test_effect_import_and_constructor_do_not_open_or_install_anything(monkeypatch):
    pool: Any = object.__new__(asyncpg.Pool)
    enc = ContentEncryption(Fernet.generate_key().decode())
    recovery = InferenceRecovery(pool)
    recovery.principals = ("synthetic_w", "synthetic_a")
    tasks = TaskStore(pool, enc, MemoryStore(pool, enc), inference_recovery=recovery)
    identity = Identity(HISTORY, key_check(KEY, HISTORY))
    history = HistoryCoordinator(
        Path("/synthetic/unopened"), identity, Ownership(1, frozenset({2}), 3)
    )
    projection = HistoryProjection(identity, acceptor="synthetic_a", worker="synthetic_w")

    def forbidden(*args, **kwargs):
        pytest.fail("effect import/constructor initiated I/O")

    for owner, attr in (
        (os, "open"),
        (fcntl, "flock"),
        (asyncio, "create_task"),
        (threading.Thread, "start"),
        (ThreadPoolExecutor, "__init__"),
        (asyncpg, "connect"),
        (asyncpg, "create_pool"),
        (subprocess, "Popen"),
        (socket, "socket"),
    ):
        monkeypatch.setattr(owner, attr, forbidden)
    name = "orchestrator.services.restore_history._effects_probe"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / EFFECTS
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    result = module.HistoryEffects(tasks, history, projection)
    assert not result._blocked and history._store is history._executor is None
    assert tasks._composition is tasks._resource_inputs is None


def unopened_effects():
    pool: Any = object.__new__(asyncpg.Pool)
    encryption = ContentEncryption(Fernet.generate_key().decode())
    recovery = InferenceRecovery(pool)
    recovery.principals = ("synthetic_w", "synthetic_a")
    tasks = TaskStore(pool, encryption, MemoryStore(pool, encryption), inference_recovery=recovery)
    identity = Identity(HISTORY, key_check(KEY, HISTORY))
    history = HistoryCoordinator(
        Path("/synthetic/unopened"), identity, Ownership(1, frozenset({2}), 3)
    )
    projection = HistoryProjection(identity, acceptor="synthetic_a", worker="synthetic_w")
    account, task = uuid4(), uuid4()
    attempt = AttemptIdentity(uuid4(), account, 0, task, 1, uuid4(), uuid4(), uuid4())
    return SimpleNamespace(
        effects=HistoryEffects(tasks, history, projection),
        history=history,
        account=account,
        task=task,
        attempt=attempt,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["send", "stop", "partial", "complete"])
@pytest.mark.parametrize("damage", ["pid", "loop", "blocked"])
async def test_owner_refusal_precedes_invocation_callback_or_key_work(monkeypatch, method, damage):
    s = unopened_effects()
    calls = []
    state = InferenceInvocation(send_check=lambda: calls.append("send_check"))
    verify_key = Identity.verify_key

    def observed_key(owner, key):
        calls.append("key_check")
        verify_key(owner, key)

    monkeypatch.setattr(Identity, "verify_key", observed_key)
    if damage == "pid":
        monkeypatch.setattr(s.history, "_pid", -1)
    elif damage == "loop":
        monkeypatch.setattr(s.history, "_loop", object())
    else:
        monkeypatch.setattr(s.history, "_blocked", True)
    with pytest.raises(HistoryError):
        if method == "send":
            await s.effects.mark(
                s.attempt, uuid4(), state, effect_kind="completion", deadline=time.monotonic() + 10
            )
        elif method == "stop":
            await s.effects.request_cancel(
                s.account, s.task, key=KEY, deadline=time.monotonic() + 10
            )
        elif method == "partial":
            await s.effects.write_partial(
                s.task,
                1,
                binding=cast(Claim, None),
                content="synthetic",
                delta_seq=1,
                deadline=time.monotonic() + 10,
            )
        else:
            await s.effects.complete(
                s.task,
                1,
                binding=cast(Claim, None),
                content="synthetic",
                deadline=time.monotonic() + 10,
            )
    assert calls == []
    assert s.history._executor is s.history._store is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["send", "stop", "partial", "complete"])
async def test_public_entry_freezes_total_budget_before_callbacks_and_supervision(
    monkeypatch, method
):
    s = unopened_effects()
    observed = []

    async def supervised(call, deadline):
        observed.append((deadline, time.monotonic()))
        if method == "send":
            return uuid4()
        if method == "stop":
            return CancelOutcome(TaskStatus.RUNNING, True)
        return TaskStatus.COMPLETED

    monkeypatch.setattr(s.effects, "_run", supervised)
    limit = time.monotonic() + 30
    if method == "send":
        await s.effects.mark(
            s.attempt, uuid4(), InferenceInvocation(), effect_kind="completion", deadline=limit
        )
    elif method == "stop":
        await s.effects.request_cancel(s.account, s.task, key=KEY, deadline=limit)
    elif method == "partial":
        await s.effects.write_partial(
            s.task, 1, binding=cast(Claim, None), content="synthetic", delta_seq=1, deadline=limit
        )
    else:
        await s.effects.complete(
            s.task, 1, binding=cast(Claim, None), content="synthetic", deadline=limit
        )
    assert len(observed) == 1 and observed[0][0] <= observed[0][1] + 2
    assert s.history._executor is s.history._store is None


@pytest.mark.asyncio
async def test_final_invocation_check_cannot_turn_late_completion_into_success(monkeypatch):
    from orchestrator.services.restore_history import effects as module

    s = unopened_effects()
    checks = 0
    late = False
    original_time = module._check_time

    def local_check():
        nonlocal checks, late
        checks += 1
        if checks == 2:
            late = True

    def check_time(deadline):
        original_time(deadline)
        if late:
            raise HistoryError("synthetic_final_check_completed_late", uncertain=True)

    async def supervised(call, deadline):
        return uuid4()

    monkeypatch.setattr(s.effects, "_run", supervised)
    monkeypatch.setattr(module, "_check_time", check_time)
    state = InferenceInvocation(send_check=local_check)
    with pytest.raises(HistoryError, match="final_check_completed_late"):
        await s.effects.mark(
            s.attempt, uuid4(), state, effect_kind="completion", deadline=time.monotonic() + 10
        )
    assert checks == 2 and state.uncertainty == "history_send_unavailable"
