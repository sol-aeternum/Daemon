"""Discriminating terminal-only seam and runtime noncomposition checks."""

import importlib.util
import os
from pathlib import Path
import socket
import subprocess

import asyncpg
import pytest

from orchestrator.services.restore_history.model import Identity, key_check
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.restore_history_support import HISTORY, KEY


@pytest.mark.parametrize(
    "source",
    [
        "TerminalHistoryProjection(identity, acceptor='a', worker='w', publisher='p')",
        "from orchestrator.services.restore_history.terminal_projection import TerminalHistoryProjection as T\nT(identity, acceptor='a',worker='w',publisher='p')",
        "import orchestrator.services.restore_history.terminal_projection as t\nt.TerminalHistoryProjection(identity,acceptor='a',worker='w',publisher='p')",
    ],
)
def test_terminal_projection_is_not_a_runtime_consumer(source):
    assert "orchestrator/services/restore_history/terminal_projection.py" not in ADMITTED
    assert composition_violations({"orchestrator/unapproved.py": source})


@pytest.mark.parametrize("method", ["_stage_completed_body", "_completion_control", "_worker"])
def test_foreign_terminal_seams_and_aliases_refuse(method):
    for source in (
        f"s.{method}(conn,row)",
        f"from orchestrator.tasks.store import TaskStore\nf=TaskStore.{method}\nf(conn,row)",
    ):
        assert any(
            "terminal transaction seam" in x
            for x in composition_violations({"orchestrator/unapproved.py": source})
        )


def test_terminal_projection_import_and_constructor_do_no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("terminal import/constructor performed I/O")

    for obj, name in (
        (os, "open"),
        (socket, "socket"),
        (subprocess, "run"),
        (asyncpg, "connect"),
        (asyncpg, "create_pool"),
    ):
        monkeypatch.setattr(obj, name, forbidden)
    path = (
        Path(__file__).resolve().parents[1]
        / "orchestrator/services/restore_history/terminal_projection.py"
    )
    spec = importlib.util.spec_from_file_location(
        "orchestrator.services.restore_history._terminal_probe", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    projection = module.TerminalHistoryProjection(
        Identity(HISTORY, key_check(KEY, HISTORY)), acceptor="a", worker="w", publisher="p"
    )
    assert projection.profile == "terminal-v3"
