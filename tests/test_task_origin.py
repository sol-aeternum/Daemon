"""Synthetic S0 contracts and the exact approved task-store runtime import seam."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID

import pytest

from orchestrator.tasks import _origin
from orchestrator.tasks._origin import (
    OriginalTaskOrigin,
    TaskOriginContext,
    TaskOriginIndeterminate,
    TaskOriginRefusal,
    _classify_task_origin,
)
from orchestrator.tasks.inputs import CHAT_INPUT_VERSION

OWNER = UUID("11111111-1111-4111-8111-111111111111")
FOREIGN = UUID("22222222-2222-4222-8222-222222222222")
CONTEXT = TaskOriginContext(OWNER)
MAX = 2**63 - 1


def classify(value, version=CHAT_INPUT_VERSION):
    return _classify_task_origin(json.dumps(value), input_version=version, context=CONTEXT)


def payload(account=str(OWNER), generation=0):
    return {"origin_account_id": account, "origin_account_generation": generation}


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"message": "fictional"},
        {"summary_origin_account_id": str(OWNER), "summary_origin_account_generation": 0},
        {"summary_origin_account_id": str(OWNER)},
        {"origin_account_id": str(OWNER)},
        {"origin_account_generation": 0},
        {"origin_account_id": None},
        {"origin_account_generation": None},
    ],
)
def test_missing_general_origin_is_blocked_without_summary_fallback(value):
    assert classify(value) == TaskOriginRefusal("task_origin_missing")


@pytest.mark.parametrize("value", [None, [], [payload()], "fictional", 0, True, 1.0])
def test_authenticated_decoded_nonobjects_are_invalid_not_missing(value):
    assert classify(value) == TaskOriginRefusal("task_origin_invalid")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{",
        '{"origin_account_id":',
        "{} trailing",
        '{"x":1,"x":2}',
        '{"origin_account_id":"%s","origin_account_id":"%s",'
        '"origin_account_generation":0}' % (FOREIGN, OWNER),
        '{"other":NaN}',
        '{"other":Infinity}',
        '{"other":-Infinity}',
    ],
)
def test_malformed_or_ambiguous_authenticated_json_is_invalid(raw):
    assert _classify_task_origin(raw, input_version=1, context=CONTEXT) == TaskOriginRefusal(
        "task_origin_invalid"
    )


@pytest.mark.parametrize("version", [0, 2, -1, None, True, False, 1.0, "1", [], {}])
def test_only_exact_supported_integer_input_version(version):
    assert classify(payload(), version) == TaskOriginRefusal("task_origin_invalid")


@pytest.mark.parametrize("account", [None, True, 42, 1.0, [], {}, "", "not-a-uuid"])
def test_uuid_type_and_syntax_are_strict(account):
    assert classify(payload(account)) == TaskOriginRefusal("task_origin_invalid")


@pytest.mark.parametrize("generation", [None, -1, MAX + 1, True, False, 0.0, 1.0, "0", [], {}])
def test_generation_type_and_range_are_strict(generation):
    assert classify(payload(generation=generation)) == TaskOriginRefusal("task_origin_invalid")


@pytest.mark.parametrize("generation", [0, 1, 7, MAX])
@pytest.mark.parametrize("account", [str(OWNER), OWNER.hex, "{" + str(OWNER) + "}"])
def test_original_uuid_and_generation_boundaries_are_preserved(account, generation):
    assert classify(payload(account, generation)) == OriginalTaskOrigin(OWNER, generation)


@pytest.mark.parametrize("generation", [0, 9, MAX])
def test_well_formed_foreign_owner_is_distinct(generation):
    assert classify(payload(str(FOREIGN), generation)) == TaskOriginRefusal("task_origin_foreign")


def test_contradictory_error_precedence():
    missing = {"origin_account_id": "invalid", "summary_origin_account_generation": 0}
    assert classify(missing, True) == TaskOriginRefusal("task_origin_missing")
    assert classify(payload(str(FOREIGN), -1)) == TaskOriginRefusal("task_origin_invalid")
    assert classify(payload(str(FOREIGN)), 2) == TaskOriginRefusal("task_origin_invalid")
    assert _classify_task_origin("null", input_version=False, context=CONTEXT) == TaskOriginRefusal(
        "task_origin_invalid"
    )
    # Summary fields and attempt epochs cannot override valid or invalid general data.
    value = {**payload(generation=7), "summary_origin_account_id": str(FOREIGN), "lease_epoch": 8}
    assert classify(value) == OriginalTaskOrigin(OWNER, 7)
    value["origin_account_id"] = None
    value["summary_origin_account_id"] = str(OWNER)
    assert classify(value) == TaskOriginRefusal("task_origin_invalid")


@pytest.mark.parametrize("field", ["origin_account_id", "origin_account_generation"])
def test_present_null_is_invalid_not_missing(field):
    value = payload()
    value[field] = None
    assert classify(value) == TaskOriginRefusal("task_origin_invalid")


def test_results_and_context_are_deterministic_hashable_frozen_data_only():
    raw = json.dumps({**payload(generation=7), "message": "fictional"})
    first = _classify_task_origin(raw, input_version=1, context=CONTEXT)
    assert first == _classify_task_origin(raw, input_version=1, context=CONTEXT)
    assert hash(first) == hash(OriginalTaskOrigin(OWNER, 7))
    for value, field, replacement in [
        (first, "generation", 8),
        (CONTEXT, "task_owner_id", FOREIGN),
        (TaskOriginRefusal("task_origin_missing"), "code", "task_origin_foreign"),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)
        assert not hasattr(value, "__dict__")
    assert TaskOriginIndeterminate() == TaskOriginIndeterminate()
    assert not hasattr(first, "revalidate")
    assert not hasattr(first, "status")
    assert not hasattr(first, "settled")


@pytest.mark.parametrize("raw", [None, b"{}", {}, RuntimeError("fictional upstream failure")])
def test_unobserved_upstream_failures_are_not_plaintext_or_bad_origins(raw):
    with pytest.raises(TypeError):
        _classify_task_origin(raw, input_version=1, context=CONTEXT)


def test_operational_parser_failure_is_not_semantic_refusal():
    for error in [MemoryError("synthetic"), RecursionError("synthetic"), RuntimeError("synthetic")]:
        with patch.object(_origin.json, "loads", side_effect=error), pytest.raises(type(error)):
            _classify_task_origin("{}", input_version=1, context=CONTEXT)


@pytest.mark.parametrize("digits", [20, 5000])
@pytest.mark.parametrize("sign", ["", "-"])
def test_oversized_json_integers_do_not_escape_origin_classification(digits, sign):
    number = sign + "1" * digits
    raw = '{"origin_account_id":"%s","origin_account_generation":%s}' % (OWNER, number)
    assert _classify_task_origin(raw, input_version=1, context=CONTEXT) == TaskOriginRefusal(
        "task_origin_invalid"
    )
    unrelated = '{"other":%s}' % number
    assert _classify_task_origin(unrelated, input_version=1, context=CONTEXT) == TaskOriginRefusal(
        "task_origin_missing"
    )
    matching = json.dumps(payload(generation=7))[:-1] + ',"other":%s}' % number
    assert _classify_task_origin(matching, input_version=1, context=CONTEXT) == OriginalTaskOrigin(
        OWNER, 7
    )


def test_contract_constructors_reject_ill_typed_data_and_open_codes():
    with pytest.raises(TypeError):
        TaskOriginContext(cast(Any, str(OWNER)))
    with pytest.raises(TypeError):
        _classify_task_origin("{}", input_version=1, context=cast(Any, None))
    with pytest.raises(ValueError):
        TaskOriginRefusal(cast(Any, "new_policy"))
    for account, generation in [(str(OWNER), 0), (OWNER, True), (OWNER, -1), (OWNER, MAX + 1)]:
        with pytest.raises(ValueError):
            OriginalTaskOrigin(account, generation)


def imports_origin(tree):
    return any(
        isinstance(node, ast.ImportFrom)
        and (
            node.module is not None
            and (node.module == "_origin" or node.module.endswith("._origin"))
            or any(alias.name == "_origin" for alias in node.names)
        )
        or isinstance(node, ast.Import)
        and any(alias.name.endswith("._origin") for alias in node.names)
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize(
    "source",
    [
        "from ._origin import TaskOriginContext",
        "from orchestrator.tasks._origin import OriginalTaskOrigin",
        "import orchestrator.tasks._origin as data",
        "from . import _origin",
        "from orchestrator.tasks import (\n    _origin,\n)",
        "from orchestrator.tasks import  _origin",
    ],
)
def test_import_tripwire_covers_relative_and_data_only_imports(source):
    assert imports_origin(ast.parse(source))


def _assert_origin_import_boundary(path, text):
    if path == "orchestrator/tasks/store.py":
        nodes = [
            node
            for node in ast.walk(ast.parse(text))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and imports_origin(ast.Module(body=[node], type_ignores=[]))
        ]
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ast.ImportFrom)
        assert node.level == 0 and node.module == "orchestrator.tasks._origin"
        assert {(alias.name, alias.asname) for alias in node.names} == {
            ("OriginalTaskOrigin", None),
            ("TaskOriginContext", None),
            ("TaskOriginRefusal", None),
            ("_classify_task_origin", None),
        }
    else:
        assert "_classify_task_origin" not in text, path
        assert "tasks._origin" not in text, path
        assert "import _origin" not in text, path
        assert not imports_origin(ast.parse(text)), path


@pytest.mark.parametrize(
    "path,text",
    [
        ("orchestrator/unapproved.py", "from orchestrator.tasks._origin import OriginalTaskOrigin"),
        ("scripts/unapproved.py", "from orchestrator.tasks import _origin"),
        ("providers/unapproved.py", "from ._origin import _classify_task_origin"),
        ("orchestrator/tasks/store.py", "from orchestrator.tasks._origin import *"),
        ("orchestrator/tasks/store.py", "import orchestrator.tasks._origin"),
    ],
)
def test_runtime_adoption_does_not_admit_other_importers_or_symbols(path, text):
    with pytest.raises(AssertionError):
        _assert_origin_import_boundary(path, text)


def test_source_boundary_is_stdlib_only_and_runtime_importer_is_exact():
    root = Path(__file__).resolve().parents[1]
    source = root / "orchestrator/tasks/_origin.py"
    tree = ast.parse(source.read_text())
    imports = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports == {"__future__", "json", "dataclasses", "typing", "uuid"}
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
    # A bounded regression tripwire, not a runtime-security or whole-program proof.
    for directory in ("orchestrator", "providers", "scripts"):
        for path in (root / directory).rglob("*.py"):
            if path != source:
                text = path.read_text()
                _assert_origin_import_boundary(path.relative_to(root).as_posix(), text)
