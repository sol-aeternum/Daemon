"""No-I/O construction and exact isolated-only source admission tripwires."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import socket
import subprocess
import threading
from typing import Any

import asyncpg
from cryptography.fernet import Fernet
from fastapi.routing import APIRoute
import pytest

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.services.restore_history.acceptance_host import (
    IsolatedAcceptanceHost,
    create_isolated_acceptance_app,
)
from orchestrator.services.restore_history.acceptance import AcceptanceProjection
from orchestrator.services.restore_history.artifact_projection import ArtifactHistoryProjection
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.services.restore_history.model import HistoryError, Identity, Profile
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.services.restore_history.read_supervisor import HistoryReadSupervisor
from orchestrator.services.restore_history.store import Ownership
from orchestrator.services.restore_history.terminal_projection import TerminalHistoryProjection
from orchestrator.tasks.store import TaskStore
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.restore_history_support import HISTORY, KEY, key_check

HOST = "orchestrator/services/restore_history/acceptance_host.py"
COMPOSITION = "orchestrator/services/restore_history/composition.py"


def objects(profile: Profile = "h1"):
    a: Any = object.__new__(asyncpg.Pool)
    w: Any = object.__new__(asyncpg.Pool)
    enc = ContentEncryption(Fernet.generate_key().decode())
    tasks = TaskStore(a, enc, MemoryStore(a, enc), cover_new_inference=True)
    reads = TaskStore(w, enc, MemoryStore(w, enc))
    identity = Identity(HISTORY, key_check(KEY, HISTORY))
    owners = [
        HistoryCoordinator(
            Path("/synthetic/unopened"), identity, Ownership(1, frozenset({2}), 3), profile=profile
        )
        for _ in range(2)
    ]
    projection = (
        TerminalHistoryProjection(
            identity, acceptor="synthetic_a", worker="synthetic_w", publisher="synthetic_p"
        )
        if profile == "terminal-v3"
        else HistoryProjection(identity, acceptor="synthetic_a", worker="synthetic_w")
    )
    return HistoryAcceptance(tasks, owners[0], projection, KEY), HistoryObservation(
        reads, owners[1], projection, KEY
    )


def make(a, r):
    return IsolatedAcceptanceHost(
        a, r, fingerprint_key="synthetic", max_body_bytes=4096, keepalive_s=1
    )


@pytest.mark.parametrize("profile", ["h1", "terminal-v3"])
def test_host_and_factory_construction_have_no_io_or_default_installation(monkeypatch, profile):
    a, r = objects(profile)

    def forbidden(*args, **kwargs):
        pytest.fail("isolated constructor started I/O")

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
    host = make(a, r)
    app = create_isolated_acceptance_app(host)
    assert not host._ready and host._active == set()
    assert all(h._executor is None and h._store is None for h in (a._history, r._history))
    assert len(app.routes) == 2
    installed: set[tuple[str, tuple[str, ...]]] = set()
    for route in app.routes:
        assert isinstance(route, APIRoute)
        assert route.methods is not None
        installed.add((route.path, tuple(route.methods)))
    assert installed == {
        ("/chat", ("POST",)),
        ("/tasks/by-key/{idempotency_key}", ("GET",)),
    }


@pytest.mark.parametrize(
    "change", ["pool", "resources", "composition", "key", "history", "profile"]
)
def test_wrong_native_binding_is_not_a_host(change):
    a, r = objects()
    if change == "pool":
        r._tasks._pool = r._tasks._memory._pool = a._tasks._pool
    elif change == "resources":
        setattr(a._tasks, "_resource_inputs", object())
    elif change == "composition":
        setattr(r._tasks, "_composition", object())
    elif change == "key":
        r._key = bytes([255]) * 32
    elif change == "history":
        r._history = a._history
    else:
        r._history._profile = "terminal-v3"
    with pytest.raises(HistoryError, match="isolated_host_refused"):
        make(a, r)


def test_artifact_profile_does_not_expand_acceptance():
    a, r = objects()
    expanded = ArtifactHistoryProjection(
        a._projection.expected,
        acceptor="synthetic_a",
        worker="synthetic_w",
        publisher="synthetic_p",
    )
    with pytest.raises(HistoryError, match="acceptance_composition_refused"):
        HistoryAcceptance(a._tasks, a._history, expanded, KEY)
    r._projection = expanded
    with pytest.raises(HistoryError, match="isolated_host_refused"):
        make(a, r)


@pytest.mark.parametrize("profile", ["h1", "terminal-v3"])
@pytest.mark.parametrize(
    "change",
    [
        "a_profile",
        "w_profile",
        "projection_profile",
        "type",
        "identity",
        "acceptor",
        "worker",
        "subclass",
    ],
)
def test_host_rechecks_profile_type_identity_and_principals_at_admission(profile, change):
    a, r = objects(profile)
    host = make(a, r)
    original = r._projection
    other = "terminal-v3" if profile == "h1" else "h1"
    if change in ("a_profile", "w_profile"):
        (a if change == "a_profile" else r)._history._profile = other
    elif change == "projection_profile":
        # Distinct objects avoid accidentally changing both sides together.
        import copy

        r._projection = copy.copy(original)
        r._projection._profile = other
    elif change == "type":
        setattr(
            r,
            "_projection",
            AcceptanceProjection(
                original.expected, acceptor=original.acceptor, worker=original.worker
            ),
        )
    elif change == "subclass":
        derived = type("DerivedProjection", (type(original),), {})
        kwargs = dict(acceptor=original.acceptor, worker=original.worker)
        if profile == "terminal-v3":
            assert isinstance(original, TerminalHistoryProjection)
            kwargs["publisher"] = original.publisher
        r._projection = derived(original.expected, **kwargs)
    else:
        import copy

        r._projection = copy.copy(original)
        if change == "identity":
            from uuid import uuid4

            identity = uuid4()
            r._projection.expected = Identity(identity, key_check(KEY, identity))
        else:
            setattr(r._projection, change, "different_synthetic_role")
    with pytest.raises(HistoryError, match="isolated_host_refused"):
        make(a, r)
    with pytest.raises(HistoryError, match="isolated_host_refused"):
        host._validate()


def test_terminal_publisher_must_match_at_construction_and_admission():
    import copy

    a, r = objects("terminal-v3")
    host = make(a, r)
    r._projection = copy.copy(r._projection)
    assert isinstance(r._projection, TerminalHistoryProjection)
    r._projection.publisher = "different_publisher"
    with pytest.raises(HistoryError, match="isolated_host_refused"):
        make(a, r)
    with pytest.raises(HistoryError, match="isolated_host_refused"):
        host._validate()


@pytest.mark.parametrize("profile", ["h1", "terminal-v3"])
def test_collaborators_require_matching_profile_at_construction(profile):
    a, r = objects(profile)
    a._history._profile = r._history._profile = "h1" if profile == "terminal-v3" else "terminal-v3"
    with pytest.raises(HistoryError, match="acceptance_composition_refused"):
        HistoryAcceptance(a._tasks, a._history, a._projection, KEY)
    with pytest.raises(HistoryError, match="observation_composition_refused"):
        HistoryObservation(r._tasks, r._history, r._projection, KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["a", "w"])
async def test_collaborators_refuse_profile_mutation_before_io(side):
    import time
    from uuid import uuid4

    from orchestrator.auth import AuthenticatedDevice

    a, r = objects("terminal-v3")
    value = a if side == "a" else r
    value._history._profile = "h1"
    with pytest.raises(HistoryError, match="composition_refused"):
        if side == "a":
            await a.accept(deadline=time.monotonic() + 1)
        else:
            await r.lookup(
                AuthenticatedDevice(uuid4(), uuid4(), uuid4()),
                "stable",
                deadline=time.monotonic() + 1,
            )
    assert value._history._executor is value._history._store is None


def test_production_read_supervisor_refuses_terminal_before_owner_or_io(monkeypatch):
    _, r = objects("terminal-v3")

    def forbidden(*args, **kwargs):
        pytest.fail("terminal projection reached production owner creation")

    monkeypatch.setattr(HistoryReadSupervisor, "_make_owner", forbidden)
    with pytest.raises(HistoryError, match="reader_composition_refused"):
        HistoryReadSupervisor(r._tasks, r._history._path, r._projection, r._history._ownership, KEY)


def test_operation_owned_seam_is_exact_and_cannot_be_aliased_or_called_by_runtime():
    source = "async def _submit(self):\n await self._acceptance._accept_owned(operation,deadline,options,lookup,request)"
    assert HOST not in ADMITTED and COMPOSITION not in ADMITTED
    assert composition_violations({HOST: source}) == []
    for wrong in (
        "orchestrator/main.py",
        "orchestrator/db.py",
        "orchestrator/worker/worker.py",
        "orchestrator/services/restore_history/other.py",
    ):
        assert composition_violations({wrong: source})
    for changed in (
        source.replace("def _submit", "def activate"),
        source.replace("deadline,options", "deadline,replacement"),
        source.replace("self._acceptance._accept_owned(", "fn("),
    ):
        if "fn(" in changed:
            changed = "async def _submit(self):\n fn=self._acceptance._accept_owned\n await fn(operation,deadline,options,lookup,request)"
        assert composition_violations({HOST: changed})


@pytest.mark.parametrize(
    "source",
    [
        "from orchestrator.services.restore_history.acceptance_host import IsolatedAcceptanceHost",
        "from orchestrator.services.restore_history.acceptance_host import create_isolated_acceptance_app",
        "IsolatedAcceptanceHost(a,r)",
        "create_isolated_acceptance_app(host)",
        "import importlib\nimportlib.import_module('orchestrator.services.restore_history.acceptance_host')",
    ],
)
def test_default_runtime_cannot_import_or_install_isolated_host(source):
    for path in ("orchestrator/main.py", "orchestrator/db.py", "orchestrator/worker/worker.py"):
        assert composition_violations({path: source})
