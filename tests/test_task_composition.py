"""Explicit isolated composition defaults; no DB/network or startup side effects."""

import asyncio
import socket
import subprocess
import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

import asyncpg
import pytest

from orchestrator.tasks.composition import qualify_task_composition, qualify_worker_composition
from orchestrator.services.account_control.transaction import ObservationBlocked


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [qualify_task_composition, qualify_worker_composition])
async def test_default_and_partial_composition_start_no_io(monkeypatch, factory):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled or partial composition attempted I/O")

    for owner, attr in (
        (asyncpg, "connect"),
        (asyncpg, "create_pool"),
        (socket, "socket"),
        (subprocess, "Popen"),
        (asyncio, "create_task"),
    ):
        monkeypatch.setattr(owner, attr, forbidden)
    assert await factory() is None
    for options in (
        {"enabled": True},
        {"enabled": False, "worker_pool": object()},
        {"enabled": "on"},
        {"enabled": True, "worker_pool": "postgresql://not-admitted"},
    ):
        with pytest.raises(ObservationBlocked, match="inference_composition_refused"):
            await factory(**cast(Any, options))


def test_composition_module_import_has_no_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("composition import initiated I/O")

    for owner, attr in (
        (asyncpg, "connect"),
        (asyncpg, "create_pool"),
        (socket, "socket"),
        (subprocess, "Popen"),
        (asyncio, "create_task"),
    ):
        monkeypatch.setattr(owner, attr, forbidden)
    name = "orchestrator.tasks._composition_import_probe"
    path = Path(__file__).resolve().parents[1] / "orchestrator/tasks/composition.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
