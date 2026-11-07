"""Negative-only type classification; no runtime admission expansion."""

import importlib.util
import os
from pathlib import Path
import socket
import subprocess

import asyncpg
import pytest

from orchestrator.services.restore_history.artifact_projection import ArtifactHistoryProjection
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.model import HistoryError, Identity, key_check
from orchestrator.services.restore_history.store import HistoryStore, Ownership
from orchestrator.tasks.store import TaskStore
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.restore_history_support import HISTORY, KEY


@pytest.mark.parametrize(
    "source",
    [
        "ArtifactHistoryProjection(identity, acceptor='a', worker='w', publisher='p')",
        "from orchestrator.services.restore_history.artifact_projection import ArtifactHistoryProjection as P\nP(identity, acceptor='a', worker='w', publisher='p')",
        "import orchestrator.services.restore_history.artifact_projection as p\np.ArtifactHistoryProjection(identity, acceptor='a', worker='w', publisher='p')",
    ],
)
def test_new_projection_is_not_an_admitted_runtime_consumer(source):
    path = "orchestrator/services/restore_history/artifact_projection.py"
    assert path not in ADMITTED
    assert composition_violations({"orchestrator/unapproved.py": source})


def test_projection_import_and_constructor_have_no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("projection import/constructor performed I/O")

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
        / "orchestrator/services/restore_history/artifact_projection.py"
    )
    spec = importlib.util.spec_from_file_location(
        "orchestrator.services.restore_history._artifact_probe", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obj = module.ArtifactHistoryProjection(
        Identity(HISTORY, key_check(KEY, HISTORY)), acceptor="a", worker="w", publisher="p"
    )
    assert obj.profile == "artifact-v2" and obj.publisher == "p"


@pytest.mark.parametrize("publisher", ["a", "w", "", True])
def test_projection_principals_must_be_distinct(publisher):
    with pytest.raises(HistoryError, match="projection_config"):
        ArtifactHistoryProjection(
            Identity(HISTORY, key_check(KEY, HISTORY)),
            acceptor="a",
            worker="w",
            publisher=publisher,
        )


def test_foreign_projection_subclass_and_profile_mismatch_refuse_before_io(tmp_path):
    identity = Identity(HISTORY, key_check(KEY, HISTORY))
    owner = Ownership(os.getuid(), frozenset({os.getuid()}), os.getgid())
    history = HistoryCoordinator(tmp_path, identity, owner, profile="artifact-v2")

    class Foreign(ArtifactHistoryProjection):
        pass

    # A real TaskStore type reaches the discriminating projection/profile
    # guards, without constructing any native service or acquiring a pool.
    tasks = TaskStore.__new__(TaskStore)
    with pytest.raises(HistoryError, match="effects_composition_refused"):
        HistoryEffects(tasks, history, Foreign(identity, acceptor="a", worker="w", publisher="p"))
    with pytest.raises(HistoryError, match="effects_composition_refused"):
        HistoryEffects(
            tasks,
            HistoryCoordinator(tmp_path, identity, owner),
            ArtifactHistoryProjection(identity, acceptor="a", worker="w", publisher="p"),
        )


@pytest.mark.parametrize("profile", [True, None, "other"])
def test_invalid_profile_fails_before_store_io(tmp_path, monkeypatch, profile):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid profile reached filesystem")

    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(HistoryError, match="invalid_profile"):
        HistoryStore(
            tmp_path,
            Identity(HISTORY, key_check(KEY, HISTORY)),
            Ownership(os.getuid(), frozenset({os.getuid()}), os.getgid()),
            deadline=1,
            profile=profile,
        )
