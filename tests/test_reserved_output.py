"""Closed typed output publication; no runtime construction/admission."""

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
import pytest

from orchestrator.resource_store import ReservedOutputPublisher, ResourceError
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.services.connection_cleanup import CleanupUnavailable
from tests.inference_composition_support import ADMITTED, composition_violations


def publisher():
    pool: Any = object.__new__(asyncpg.Pool)
    return ReservedOutputPublisher(
        pool,
        Path("/synthetic/objects"),
        Path("/synthetic/journal"),
        uuid4(),
        Fernet(Fernet.generate_key()),
        inventory_limit=10,
        snapshot_byte_limit=400000,
    )


def test_constructor_no_io(monkeypatch):
    import os
    import fcntl

    def forbidden(*a, **kw):
        pytest.fail("constructor initiated I/O")

    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(fcntl, "flock", forbidden)
    monkeypatch.setattr(asyncio, "create_task", forbidden)
    assert publisher()._blocked is False


@pytest.mark.asyncio
async def test_transaction_refuses_without_existing_cleanup_owner():
    value = publisher()
    with pytest.raises(CleanupUnavailable):
        async with value._reserved_transaction():
            pytest.fail("unowned transaction entered")


def test_runtime_constructor_still_not_admitted():
    assert "orchestrator/resource_store.py" not in ADMITTED
    assert composition_violations(
        {
            "orchestrator/main.py": "ReservedOutputPublisher(a,b,c,d,e,inventory_limit=1,snapshot_byte_limit=1)"
        }
    )


@pytest.mark.asyncio
async def test_method_refuses_unowned_connection_before_sql():
    value = publisher()
    with pytest.raises(ResourceError, match="transaction"):
        await value._reserved_snapshot(
            object(),
            AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4()),
            uuid4(),
        )


@pytest.mark.parametrize(
    "expression",
    [
        "publisher._reserved_transaction(callback)",
        "publisher._reserved_snapshot(conn, identity, intent_id, admit=True)",
        "publisher._publish_reserved(conn, identity, intent_id, content, callback)",
        "other._publish_reserved(conn, identity, intent_id, content)",
        "alias(conn, identity, intent_id, content)",
    ],
)
def test_only_exact_typed_internal_calls_are_admitted(expression):
    path = "orchestrator/services/restore_history/effects.py"
    code = (
        "async def _publish_reserved_output():\n alias = publisher._publish_reserved\n "
        + expression
    )
    assert composition_violations({path: code})


def test_exact_seams_do_not_admit_other_methods_modules_or_callbacks():
    path = "orchestrator/services/restore_history/effects.py"
    calls = "publisher._reserved_transaction()\n publisher._reserved_snapshot(conn, identity, intent_id)\n publisher._publish_reserved(conn, identity, intent_id, content)"
    assert not composition_violations({path: "async def _publish_reserved_output():\n " + calls})
    assert composition_violations({path: "async def other():\n " + calls})
    assert composition_violations(
        {"orchestrator/main.py": "async def _publish_reserved_output():\n " + calls}
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("inventory_limit", True),
        ("inventory_limit", 10001),
        ("snapshot_byte_limit", 0),
        ("snapshot_byte_limit", 2**31),
    ],
)
def test_explicit_snapshot_and_inventory_budgets_are_not_coerced(field, value):
    params = dict(inventory_limit=10, snapshot_byte_limit=400000)
    params[field] = value
    with pytest.raises(ResourceError):
        ReservedOutputPublisher(
            object.__new__(asyncpg.Pool),
            Path("/synthetic/root"),
            Path("/synthetic/journal"),
            uuid4(),
            Fernet(Fernet.generate_key()),
            **params,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_native", [False, True])
async def test_original_base_exception_survives_failed_connection_termination(
    monkeypatch, missing_native
):
    from contextlib import asynccontextmanager
    from orchestrator import resource_store
    from orchestrator.services.connection_cleanup import retain_pool_cleanup

    class Connection:
        def __init__(self):
            if not missing_native:
                from types import SimpleNamespace

                self._con = SimpleNamespace(_cancellations=set())

        def is_in_transaction(self):
            return False

        def transaction(self, **kwargs):
            return self

        async def start(self):
            pass

        def terminate(self):
            raise RuntimeError("synthetic termination failure")

    @asynccontextmanager
    async def connected(pool):
        yield Connection()

    monkeypatch.setattr(resource_store, "pool_connection", connected)
    value = publisher()
    original = asyncio.CancelledError("original native cancellation")
    async with retain_pool_cleanup():
        with pytest.raises(asyncio.CancelledError) as caught:
            async with value._reserved_transaction():
                raise original
    assert caught.value is original and value._blocked
    assert value._active_task is None and value._connection is None
    assert "Native publisher termination also failed" in original.__notes__
    assert (
        any("cancellation cleanup failed" in note for note in original.__notes__) is missing_native
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("admit", [False, True])
@pytest.mark.parametrize(
    "payload,code",
    [(None, "snapshot_capacity"), ("€", "snapshot_capacity"), ("{}", "invalid_snapshot")],
)
async def test_literal_query_branch_preserves_parameters_budget_and_refusal(admit, payload, code):
    from orchestrator.services.connection_cleanup import retain_pool_cleanup

    value = publisher()
    value._snapshot_byte_limit = 2
    identity = AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
    intent = uuid4()
    calls = []

    class Connection:
        async def fetchval(self, query, *args):
            calls.append((query, args))
            return payload

    conn = Connection()
    value._active_task, value._connection = asyncio.current_task(), conn
    async with retain_pool_cleanup():
        with pytest.raises(ResourceError, match=code):
            await value._reserved_snapshot(conn, identity, intent, admit=admit)
    assert len(calls) == 1  # Refusal never falls back to the other function.
    query, args = calls[0]
    name = "admit" if admit else "snapshot"
    assert query.startswith(
        "WITH observed AS MATERIALIZED (SELECT public.resource_output_catalog_"
        + name
        + "($1::pg_catalog.jsonb,$2::pg_catalog.uuid)"
    )
    assert "pg_catalog.octet_length(value::pg_catalog.text)<=$3::pg_catalog.int4" in query
    assert args == (identity.wire(), intent, 2)


def test_publisher_module_import_and_constructor_have_no_service_io(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import fcntl
    import importlib.util
    import os
    import socket
    import subprocess
    import sys
    import threading

    pool: Any = object.__new__(asyncpg.Pool)
    cipher = Fernet(Fernet.generate_key())
    root, journal, identity = Path("/synthetic/objects"), Path("/synthetic/journal"), uuid4()

    def forbidden(*args, **kwargs):
        pytest.fail("publisher import/constructor initiated service I/O")

    for owner, attribute in (
        (os, "open"),
        (os, "mkdir"),
        (os, "fsync"),
        (fcntl, "flock"),
        (asyncio, "create_task"),
        (threading.Thread, "start"),
        (ThreadPoolExecutor, "__init__"),
        (asyncpg, "connect"),
        (asyncpg, "create_pool"),
        (subprocess, "Popen"),
        (socket, "socket"),
    ):
        monkeypatch.setattr(owner, attribute, forbidden)
    name = "orchestrator._reserved_output_import_probe"
    path = Path(__file__).resolve().parents[1] / "orchestrator/resource_store.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    result = module.ReservedOutputPublisher(
        pool, root, journal, identity, cipher, inventory_limit=1, snapshot_byte_limit=400000
    )
    assert result._pool is pool and not result._blocked
    assert result._active_task is result._connection is None
