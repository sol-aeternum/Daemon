"""Pure source/import checks; NOT PostgreSQL qualification."""

import ast
import asyncio
import copy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from orchestrator import attempt_lifecycle as lc


def imports_lifecycle(text):
    return any(
        isinstance(n, ast.Import)
        and any("attempt_lifecycle" in a.name.split(".") for a in n.names)
        or isinstance(n, ast.ImportFrom)
        and (
            "attempt_lifecycle" in (n.module or "").split(".")
            or any(a.name == "attempt_lifecycle" for a in n.names)
        )
        for n in ast.walk(ast.parse(text))
    )


def test_no_importers_dynamic_callers_or_import_time_io(monkeypatch):
    for text in (
        "from orchestrator import attempt_lifecycle",
        "import orchestrator.attempt_lifecycle",
        "from orchestrator.attempt_lifecycle import assert_open",
    ):
        assert imports_lifecycle(text)
    root = Path(__file__).resolve().parents[1]
    # Exact financial adapters and owner-approved fixture-injected inference seams.
    # Production constructors/startup remain unwired (checked separately below).
    adapters = {
        "orchestrator/entitlements/attempt.py",
        "orchestrator/entitlements/service.py",
        "orchestrator/entitlements/errors.py",
        "orchestrator/compute_runtime.py",
        "orchestrator/tasks/runner.py",
        "orchestrator/tasks/inference.py",
    }
    for base in ("orchestrator", "providers", "scripts"):
        for path in (root / base).rglob("*.py"):
            if path.name == "attempt_lifecycle.py":
                continue
            text = path.read_text()
            if path.relative_to(root).as_posix() in adapters:
                assert not any(
                    isinstance(n, ast.Attribute)
                    and n.attr in {"register", "close", "readback"}
                    and isinstance(n.value, ast.Name)
                    and n.value.id in {"lc", "lifecycle"}
                    for n in ast.walk(ast.parse(text))
                )
                continue
            assert not imports_lifecycle(text), str(path)
            assert "attempt_lifecycle_" not in text, str(path)
            assert not any(
                isinstance(n, ast.Attribute)
                and n.attr in {"reserve_for_attempt", "recover_attempt_reservation"}
                for n in ast.walk(ast.parse(text))
            ), str(path)

    def forbidden(*args, **kwargs):
        pytest.fail("import/construction caused I/O")

    monkeypatch.setattr(asyncpg, "connect", forbidden)
    monkeypatch.setattr(asyncpg, "create_pool", forbidden)
    spec = importlib.util.spec_from_file_location("orchestrator.attempt_lifecycle", lc.__file__)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.AttemptLifecycleStore(object())


def test_identity_strict_and_immutable():
    ident = lc.AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
    assert ident.wire()
    for name, value in (
        ("generation", True),
        ("epoch", 0),
        ("task_id", "x"),
        ("generation", 2**63),
    ):
        with pytest.raises(lc.LifecycleError):
            replace(ident, **{name: value}).wire()


@pytest.mark.parametrize(
    "bad",
    [
        "none",
        "fields",
        "bool",
        "foreign",
        "revision",
        "time",
        "receipt",
        "opcode",
        "receipt_time",
        "no_control",
    ],
)
def test_decode_rejects_malformed_or_foreign_results(bad):
    ident = lc.AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
    command = uuid4()
    timestamp = "2026-10-15T06:00:00+00:00"
    wire = json.loads(ident.wire())
    control = {
        k: wire[k]
        for k in ("account_id", "task_id", "epoch", "financial_scope_id", "metadata_scope_id")
    }
    control.update(
        id=str(ident.lifecycle_id),
        identity=wire,
        revision=1,
        closed_at=None,
        attempt_started_at=timestamp,
        created_at=timestamp,
    )
    result = {
        "control": control,
        "receipt": {
            "command_id": str(command),
            "lifecycle_id": str(ident.lifecycle_id),
            "opcode": "register",
            "reason": "",
            "revision": 1,
            "committed_at": timestamp,
        },
        "account_present": True,
        "references_current": True,
    }
    assert lc._decode(json.dumps(result), ident, command) == result
    malformed = copy.deepcopy(result)
    if bad == "none":
        malformed = None
    elif bad == "fields":
        malformed = {}
    elif bad == "bool":
        malformed["references_current"] = 1
    elif bad == "foreign":
        malformed["control"]["account_id"] = str(uuid4())
    elif bad == "revision":
        malformed["control"]["revision"] = True
    elif bad == "time":
        malformed["control"]["created_at"] = "2026-10-15T06:00:00"
    elif bad == "receipt":
        malformed["receipt"]["command_id"] = str(uuid4())
    elif bad == "opcode":
        malformed["receipt"]["opcode"] = "renew"
    elif bad == "receipt_time":
        malformed["receipt"]["committed_at"] = None
    elif bad == "no_control":
        malformed["control"] = None
    with pytest.raises((ValueError, TypeError, KeyError)):
        lc._decode(json.dumps(malformed), ident, command)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preflight", "statement"])
async def test_fake_gate_timeout_error_mapping(phase, monkeypatch):
    class SlowConnection:
        def is_in_transaction(self):
            return True

        async def fetchval(self, sql):
            if phase == "preflight":
                await asyncio.sleep(1)
            return True

        async def execute(self, sql, *args):
            await asyncio.sleep(1)

    monkeypatch.setattr(lc, "OPERATION_S", 0.01)
    ident = lc.AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
    with pytest.raises(lc.LifecycleError) as caught:
        await lc.assert_open(cast(lc.Connection, SlowConnection()), ident)
    assert caught.value.code == "unavailable" and caught.value.identity == ident
