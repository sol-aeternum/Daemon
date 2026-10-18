"""Production non-composition and new adapter's import/construction I/O negatives."""

import ast
import asyncio
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys

import asyncpg
import pytest

from tests.inference_composition_support import composition_violations


ROOT = Path(__file__).resolve().parents[1]


def test_production_inventory_and_disabled_constructor_defaults():
    sources = {
        p.relative_to(ROOT).as_posix(): p.read_text()
        for base in ("orchestrator", "providers", "scripts")
        for p in (ROOT / base).rglob("*.py")
    }
    assert composition_violations(sources) == []
    tree = ast.parse(sources["orchestrator/tasks/store.py"])
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TaskStore")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    defaults = dict(zip((a.arg for a in init.args.kwonlyargs), init.args.kw_defaults))
    recovery = defaults["inference_recovery"]
    coverage = defaults["cover_new_inference"]
    assert recovery is not None and coverage is not None
    assert ast.literal_eval(recovery) is None
    assert ast.literal_eval(coverage) is False


@pytest.mark.parametrize(
    "source",
    [
        "from orchestrator.tasks.inference import InferenceRecovery as R\nR(pool)",
        "import orchestrator.tasks.inference as m\nm.InferenceRecovery(pool)",
        "from orchestrator.tasks import inference as m\ngetattr(m, 'InferenceRecovery')(pool)",
        "import importlib as il\nm = il.import_module('orchestrator.tasks.inference')\nC = getattr(m, 'InferenceRecovery')\nC(pool)",
        "m = __import__('orchestrator.tasks.inference')\ngetattr(m, 'InferenceRecovery')(pool)",
        "from orchestrator.tasks.store import TaskStore as S\nAlias = S\nAlias(a,b,c,inference_recovery=x)",
        "import orchestrator.tasks.store as s\ngetattr(s, 'TaskStore')(a,b,c,cover_new_inference=True)",
        "getattr(consumer, 'abandoned_pass')()",
        "setattr(store, '_inference', collaborator)",
        "store._inference = collaborator",
        "compose(inference_recovery=collaborator)",
    ],
)
@pytest.mark.parametrize(
    "site", ["orchestrator/runtime_extra.py", "providers/alternate.py", "scripts/other_startup.py"]
)
def test_discriminating_alternate_alias_dynamic_installation_negatives(source, site):
    assert composition_violations({site: source})
    assert composition_violations({"tests/explicit_fixture.py": source}) == []


@pytest.mark.parametrize("recovery,coverage", [("factory()", "False"), ("None", "True")])
def test_changed_default_negative(recovery, coverage):
    source = f"class TaskStore:\n def __init__(self, *, inference_recovery={recovery}, cover_new_inference={coverage}): pass\n"
    assert composition_violations({"orchestrator/tasks/store.py": source})


def test_observer_constants_are_not_a_consumer_import_exemption():
    path = "orchestrator/tasks/observe.py"
    assert (
        composition_violations({path: "from orchestrator.tasks.inference import CODE, NOTICE"})
        == []
    )
    assert composition_violations(
        {path: "from orchestrator.tasks.inference import InferenceRecovery"}
    )
    assert composition_violations({path: "import orchestrator.tasks.inference as alias"})


def test_new_adapter_import_and_construction_start_no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("new adapter import/construction initiated I/O")

    # Dependencies are already imported; execute the NEW adapter body in isolation.
    # Filesystem loader reads remain necessary, not blanket filesystem containment.
    for owner, attr in (
        (asyncpg, "connect"),
        (asyncpg, "create_pool"),
        (socket, "socket"),
        (socket, "create_connection"),
        (subprocess, "Popen"),
        (asyncio, "create_task"),
        (asyncio, "create_subprocess_exec"),
    ):
        monkeypatch.setattr(owner, attr, forbidden)
    key = "orchestrator.tasks._inference_import_probe"
    spec = importlib.util.spec_from_file_location(key, ROOT / "orchestrator/tasks/inference.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, key, module)
    spec.loader.exec_module(module)
    module.InferenceRecovery(object())
