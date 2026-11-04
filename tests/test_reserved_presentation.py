"""Narrow presentation construction, ownership and noncomposition regressions."""

import asyncio

import pytest

from orchestrator.resource_store import ResourceError
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.test_reserved_output import publisher


def test_presentation_is_unselected_by_default():
    value = publisher()
    assert value._presentation_item_limit is None
    assert value._presentation_record_limit is None
    assert "orchestrator/resource_store.py" not in ADMITTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["_presentation_snapshot", "_presentation_references", "_present_reserved"]
)
async def test_presentation_seams_refuse_without_owned_connection(method):
    value = publisher()
    args = {
        "_presentation_snapshot": (object(), object()),
        "_presentation_references": (object(), object(), [], {}),
        "_present_reserved": (object(), object(), object()),
    }[method]
    with pytest.raises(ResourceError, match="transaction"):
        await getattr(value, method)(*args)


def test_exact_h_presentation_seams_and_cleanup_only():
    path = "orchestrator/services/restore_history/effects.py"
    calls = (
        "publisher._reserved_transaction()\n "
        "publisher._reserved_snapshot(conn, identity, intent_id)\n "
        "publisher._present_reserved(conn, identity, intent_id)\n "
        "retain_pool_cleanup()"
    )
    assert not composition_violations({path: "async def _present_reserved_output():\n " + calls})
    assert composition_violations({path: "async def other():\n " + calls})
    assert composition_violations(
        {"orchestrator/main.py": "async def _present_reserved_output():\n " + calls}
    )


@pytest.mark.parametrize(
    "expression",
    [
        "publisher._present_reserved(conn, identity, intent_id, callback)",
        "publisher._reserved_snapshot(conn, identity, intent_id, admit=True)",
        "other._present_reserved(conn, identity, intent_id)",
        "alias(conn, identity, intent_id)",
        "getattr(publisher, '_present_reserved')(conn, identity, intent_id, callback)",
        "publisher._presentation_references(conn, identity, pins, task)",
        "retain_pool_cleanup(callback)",
    ],
)
def test_h_presentation_does_not_admit_other_calls(expression):
    source = (
        "async def _present_reserved_output():\n alias = publisher._present_reserved\n "
        + expression
    )
    assert composition_violations({"orchestrator/services/restore_history/effects.py": source})


def test_native_presentation_helper_calls_are_closed():
    path = "orchestrator/resource_store.py"
    calls = (
        "self._reserved_snapshot(conn, identity, intent)\n "
        "self._reserved_namespace(conn, identity, observed, stored)\n "
        "self._presentation_snapshot(conn, identity)\n "
        "self._presentation_references(conn, identity, pins, task)"
    )
    assert not composition_violations({path: "async def _present_reserved():\n " + calls})
    assert composition_violations({path: "async def other():\n " + calls})
    assert composition_violations(
        {path: "async def _present_reserved():\n " + calls.replace("pins, task", "pins, callback")}
    )


def test_import_and_optional_constructor_limits_add_no_service_io(monkeypatch):
    import os
    import fcntl
    from pathlib import Path
    from uuid import uuid4
    import asyncpg
    from cryptography.fernet import Fernet
    from orchestrator.resource_store import ReservedOutputPublisher

    def forbidden(*args, **kwargs):
        pytest.fail("constructor initiated service I/O")

    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(fcntl, "flock", forbidden)
    monkeypatch.setattr(asyncio, "create_task", forbidden)
    for item, records in [(True, 1), (1, None), (0, 1), (101, 1), (1, 101)]:
        with pytest.raises(ResourceError, match="invalid_limits"):
            ReservedOutputPublisher(
                object.__new__(asyncpg.Pool),
                Path("/synthetic/root"),
                Path("/synthetic/journal"),
                uuid4(),
                Fernet(Fernet.generate_key()),
                inventory_limit=10,
                snapshot_byte_limit=400000,
                presentation_item_limit=item,
                presentation_record_limit=records,
            )
