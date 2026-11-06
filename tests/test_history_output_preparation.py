"""Closed preparation reader/encoder admission; no runtime installation."""

import ast
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.resource_store import ResourceError
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.test_artifact_reservations import reservation_references_allowed
from tests.test_reserved_output import publisher

OWNER = "orchestrator/services/restore_history/effects.py"
NATIVE = "orchestrator/services/restore_history/native_publication.py"
TYPES = (
    "from orchestrator.artifact_reservations import "
    "OutputReservations, IntentCommand, JobBinding, IntentBinding\n"
)
ENCODER = "from orchestrator.resource_csv import encode_csv\n"


def csv_references_allowed(path, source):
    if path in (OWNER, NATIVE) and "resource_csv" in source:
        if source.count("resource_csv") != 1:
            return False
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False
        imports = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module == "orchestrator.resource_csv"
        ]
        if len(imports) != 1:
            return False
        node = imports[0]
        if (
            node not in tree.body
            or node.level != 0
            or len(node.names) != 1
            or node.names[0].name != "encode_csv"
            or node.names[0].asname is not None
        ):
            return False
        segment = ast.get_source_segment(source, node)
        if segment is None:
            return False
        source = source.replace(segment, "", 1)
    return "resource_csv" not in source


def test_only_exact_preparation_imports_are_selected_without_admitting_a_host():
    for path in (OWNER, NATIVE):
        assert path not in ADMITTED
        assert reservation_references_allowed(path, TYPES)
        assert csv_references_allowed(path, ENCODER)
    assert not reservation_references_allowed("orchestrator/main.py", TYPES)
    assert not csv_references_allowed("orchestrator/main.py", ENCODER)


@pytest.mark.parametrize(
    "source",
    [
        TYPES.replace("JobBinding", "JobBinding as Job"),
        TYPES.replace(", IntentBinding", ""),
        TYPES.rstrip() + ", DescriptorCodec\n",
        "def other():\n " + TYPES,
    ],
)
def test_preparation_type_exception_does_not_expand_to_other_imports(source):
    assert not reservation_references_allowed(OWNER, source)


@pytest.mark.parametrize(
    "source",
    [
        ENCODER.replace("encode_csv", "encode_csv as encode"),
        ENCODER.replace("encode_csv", "ResourceCSV"),
        ENCODER.rstrip() + ", ResourceCSV\n",
        "import orchestrator.resource_csv",
        "from orchestrator import resource_csv",
        "def other():\n " + ENCODER,
        ENCODER + "other='resource_csv'\n",
        "__import__('orchestrator.resource_csv')",
    ],
)
@pytest.mark.parametrize("path", [OWNER, NATIVE])
def test_pure_encoder_exception_refuses_aliases_classes_and_dynamic_imports(source, path):
    assert not csv_references_allowed(path, source)


def test_production_encoder_inventory_keeps_exact_imports_without_runtime_admission():
    root = Path(__file__).resolve().parents[1]
    for directory in ("orchestrator", "providers", "scripts"):
        for path in (root / directory).rglob("*.py"):
            if path == root / "orchestrator/resource_csv.py":
                continue
            assert csv_references_allowed(path.relative_to(root).as_posix(), path.read_text()), str(
                path
            )


def test_exact_preparation_calls_preserve_owner_and_cap():
    code = "async def _prepare_output():\n publisher._reserved_transaction()\n publisher._preparation_snapshot(conn, identity)\n retain_pool_cleanup()"
    assert not composition_violations({OWNER: code})
    assert composition_violations({OWNER: code.replace("_prepare_output", "other")})
    assert composition_violations({"orchestrator/main.py": code})
    assert not composition_violations(
        {OWNER: "async def prepare_output():\n encode_csv(rows, byte_limit=8388608)"}
    )


@pytest.mark.parametrize(
    "call",
    [
        "publisher._preparation_snapshot(conn, identity, callback)",
        "publisher._preparation_snapshot(conn, identity, stage=callback)",
        "other._preparation_snapshot(conn, identity)",
        "alias(conn, identity)",
    ],
)
def test_preparation_sql_seam_refuses_callbacks_and_aliases(call):
    assert composition_violations(
        {OWNER: "async def _prepare_output():\n alias=publisher._preparation_snapshot\n " + call}
    )


@pytest.mark.parametrize(
    "call",
    [
        "encode_csv(rows, byte_limit=8388609)",
        "encode_csv(rows, byte_limit=limit)",
        "encode_csv(rows, callback, byte_limit=8388608)",
        "alias(rows, byte_limit=8388608)",
    ],
)
def test_encoder_seam_cannot_expand_budget_or_become_a_callback(call):
    assert composition_violations(
        {OWNER: "async def prepare_output():\n alias=encode_csv\n " + call}
    )


@pytest.mark.asyncio
async def test_preparation_reader_refuses_unowned_connection_before_sql():
    with pytest.raises(ResourceError, match="transaction"):
        await publisher()._preparation_snapshot(
            object(), AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [None, "version", "extra", "missing", "attempt", "task", "bool", "duplicate", "capacity"],
)
async def test_closed_preparation_snapshot_decoder(monkeypatch, damage):
    value = publisher()
    identity = AttemptIdentity(uuid4(), uuid4(), 0, uuid4(), 1, uuid4(), uuid4(), uuid4())
    raw: dict[str, Any] = {
        "schema_version": 1,
        "attempt_id": str(uuid4()),
        "task": {
            "id": str(identity.task_id),
            "user_id": str(identity.account_id),
            "conversation_id": str(uuid4()),
            "result_message_id": str(uuid4()),
            "input_version": 1,
            "input_ciphertext": "synthetic-ciphertext",
        },
    }
    if damage == "version":
        raw["schema_version"] = True
    if damage == "extra":
        raw["extra"] = 1
    if damage == "missing":
        del raw["attempt_id"]
    if damage == "attempt":
        raw["attempt_id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    if damage == "task":
        raw["task"]["id"] = str(uuid4())
    if damage == "bool":
        raw["task"]["input_version"] = True
    token = json.dumps(raw)
    if damage == "duplicate":
        token = token.replace('"schema_version": 1', '"schema_version": 1,"schema_version": 1')
    if damage == "capacity":
        value._snapshot_byte_limit = 1

    class Connection:
        async def fetchval(self, sql, *args):
            assert "resource_output_preparation_snapshot" in sql
            return token

    # Pure decoder coverage only. Actual current connection/role authority is
    # qualified separately with real restricted PostgreSQL connections.
    monkeypatch.setattr(value, "_owned_connection", lambda conn: None)
    if damage is None:
        decoded = await value._preparation_snapshot(Connection(), identity)
        assert str(decoded["attempt_id"]) == raw["attempt_id"]
        assert decoded["task"]["id"] == identity.task_id
    else:
        with pytest.raises(ResourceError):
            await value._preparation_snapshot(Connection(), identity)
