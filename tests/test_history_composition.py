"""Admission remains uncalled; exact internal seam and inert constructor probes."""

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
from typing import Any

import asyncpg
from cryptography.fernet import Fernet
import pytest

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.restore_history.acceptance import AcceptanceProjection
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.model import HistoryError, Identity
from orchestrator.services.restore_history.store import Ownership
from orchestrator.tasks.store import TaskStore
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.restore_history_support import HISTORY as HISTORY_ID, KEY, key_check


HISTORY = "orchestrator/services/restore_history/composition.py"


@pytest.mark.parametrize("path", ["orchestrator/tasks/store.py", "orchestrator/resource_inputs.py"])
def test_ordinary_staging_seam_admits_only_default_none(path):
    source = "async def accept(self):\n await self._accept_with_stage(stage=None, user_id=user)"
    assert composition_violations({path: source}) == []
    assert composition_violations({path: source.replace("stage=None", "stage=hook")})
    assert composition_violations({path: source.replace("def accept(", "def activate(")})
    assert composition_violations({path: source.replace("stage=None", "stage=None, **opts")})


def test_exact_internal_history_seam_not_general_admission():
    source = (
        "async def _accept(self):\n await self._tasks._accept_with_stage(stage=stage, **options)"
    )
    assert HISTORY not in ADMITTED
    assert composition_violations({HISTORY: source}) == []
    for wrong in ("orchestrator/main.py", "orchestrator/resource_inputs.py"):
        assert composition_violations({wrong: source})
    assert composition_violations({HISTORY: source.replace("stage=stage", "stage=other")})
    assert composition_violations({HISTORY: source.replace("**options", "**replacement")})
    assert composition_violations({HISTORY: source.replace("def _accept(", "def install(")})


def test_cleanup_scope_is_only_admitted_inside_exact_uncalled_history_owner():
    source = "async def _accept(self):\n async with retain_pool_cleanup():\n  pass"
    assert composition_violations({HISTORY: source}) == []
    for path in ("orchestrator/main.py", "orchestrator/tasks/store.py"):
        assert composition_violations({path: source})
    for changed in (
        source.replace("def _accept(", "def install("),
        source.replace("retain_pool_cleanup()", "retain_pool_cleanup(pool)"),
        source.replace("retain_pool_cleanup()", "helper.retain_pool_cleanup()"),
        "async def _accept(self):\n fn=retain_pool_cleanup\n async with fn():\n  pass",
    ):
        assert composition_violations({HISTORY: changed})


@pytest.mark.parametrize("method", ["_accept_with_stage", "_accept_once"])
def test_staging_alias_or_host_call_cannot_activate_callback(method):
    source = f"async def run(self):\n fn = self._tasks.{method}\n await fn(stage=hook)"
    assert composition_violations({"orchestrator/main.py": source})
    assert composition_violations({HISTORY: source})


@pytest.mark.parametrize(
    "method", ["self._resource_inputs._accept_with_stage", "self._accept_once"]
)
def test_exact_native_callback_join_is_not_general_staging_admission(method):
    source = f"async def _accept_with_stage(self):\n await {method}(stage=stage, user_id=user)"
    assert composition_violations({"orchestrator/tasks/store.py": source}) == []
    assert composition_violations({"orchestrator/main.py": source})
    assert composition_violations(
        {"orchestrator/tasks/store.py": source.replace("stage=stage", "stage=other")}
    )


@pytest.mark.parametrize(
    "source",
    [
        "from orchestrator.services.restore_history.composition import HistoryAcceptance",
        "import orchestrator.services.restore_history.composition as h\nh.HistoryAcceptance(t,h,p,k)",
        "import importlib\nh = importlib.import_module('orchestrator.services.restore_history.composition')",
        "HistoryAcceptance(t,h,p,k)",
        "HistoryCoordinator(path,identity,ownership)",
    ],
)
def test_runtime_history_acceptance_import_still_refuses(source):
    assert composition_violations({"orchestrator/db.py": source})


def test_composition_import_and_constructor_have_no_io_or_installation(monkeypatch):
    pool: Any = object.__new__(asyncpg.Pool)
    enc = ContentEncryption(Fernet.generate_key().decode())
    tasks = TaskStore(pool, enc, MemoryStore(pool, enc), cover_new_inference=True)
    identity = Identity(HISTORY_ID, key_check(KEY, HISTORY_ID))
    history = HistoryCoordinator(
        Path("/synthetic/unopened"), identity, Ownership(1, frozenset({2}), 3)
    )
    projection = AcceptanceProjection(identity, acceptor="synthetic_a", worker="synthetic_w")

    def forbidden(*args, **kwargs):
        pytest.fail("history acceptance import/constructor initiated I/O")

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
    key = "orchestrator.services.restore_history._composition_import_probe"
    path = Path(__file__).resolve().parents[1] / HISTORY
    spec = importlib.util.spec_from_file_location(key, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, key, module)
    spec.loader.exec_module(module)
    composed = module.HistoryAcceptance(tasks, history, projection, KEY)
    assert not composed._blocked
    assert history._executor is None and history._store is None
    assert tasks._composition is tasks._resource_inputs is None
    borrowed = object()
    monkeypatch.setattr(tasks, "_pool", borrowed)
    monkeypatch.setattr(tasks._memory, "_pool", borrowed)
    with pytest.raises(HistoryError, match="composition_refused"):
        module.HistoryAcceptance(tasks, history, projection, KEY)
