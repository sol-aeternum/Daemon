"""The observation import is read-only admission, not H startup or execution."""

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
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.services.restore_history.model import Identity
from orchestrator.services.restore_history import observation
from orchestrator.services.restore_history.store import Ownership
from orchestrator.tasks.store import TaskStore
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.restore_history_support import HISTORY, KEY, key_check

IMPORT = "from orchestrator.services.restore_history.observation import HistoricalObservation, HistoryObservation\n"
ROUTE = "orchestrator/routes/tasks.py"


def test_only_exact_observation_import_is_admitted_without_startup_or_construction():
    assert ROUTE not in ADMITTED
    assert composition_violations({ROUTE: IMPORT}) == []
    for path in ("orchestrator/db.py", "orchestrator/worker/worker.py", "orchestrator/other.py"):
        assert composition_violations({path: IMPORT})
    for source in (
        IMPORT.replace("HistoryObservation\n", "HistoryObservation as Observer\n"),
        IMPORT.replace("HistoryObservation\n", "HistoryObservation, HistoryCoordinator\n"),
        IMPORT.replace("orchestrator.services", "..services"),
        IMPORT + "HistoryObservation(tasks,history,projection,key)",
        IMPORT + "state.history_observation=observer",
        IMPORT + "AppState(settings=settings, history_observation=observer)",
        IMPORT + "state.task_composition=bundle",
        "import importlib\nimportlib.import_module('orchestrator.services.restore_history.observation')",
    ):
        assert composition_violations({ROUTE: source})


def test_observation_cleanup_scope_cannot_be_activated_by_another_host():
    path = "orchestrator/services/restore_history/observation.py"
    source = "async def _lookup(self):\n async with retain_pool_cleanup():\n  pass"
    assert composition_violations({path: source}) == []
    assert composition_violations({ROUTE: source})
    assert composition_violations({path: source.replace("def _lookup", "def activate")})


def test_observation_import_and_constructor_start_no_io_and_install_nothing(monkeypatch):
    pool: Any = object.__new__(asyncpg.Pool)
    enc = ContentEncryption(Fernet.generate_key().decode())
    tasks = TaskStore(pool, enc, MemoryStore(pool, enc))
    identity = Identity(HISTORY, key_check(KEY, HISTORY))
    history = HistoryCoordinator(
        Path("/synthetic/unopened"), identity, Ownership(1, frozenset({2}), 3)
    )
    projection = HistoryProjection(identity, acceptor="synthetic_a", worker="synthetic_w")

    def forbidden(*args, **kwargs):
        pytest.fail("observation constructor started I/O")

    for owner, name in (
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
        monkeypatch.setattr(owner, name, forbidden)
    name = "orchestrator.services.restore_history._observation_import_probe"
    path = (
        Path(__file__).resolve().parents[1] / "orchestrator/services/restore_history/observation.py"
    )
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    module.HistoryObservation(tasks, history, projection, KEY)
    assert module.HistoryObservation is not observation.HistoryObservation
    assert history._executor is history._store is None
    assert tasks._composition is tasks._resource_inputs is None
