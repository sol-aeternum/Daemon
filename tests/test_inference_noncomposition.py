"""Production non-composition and new adapter's import/construction I/O negatives."""

import ast
import asyncio
import importlib.util
import fcntl
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import UUID

import asyncpg
import pytest

from tests.inference_composition_support import ADMITTED, composition_violations


ROOT = Path(__file__).resolve().parents[1]

HISTORY_ADAPTER = "orchestrator/services/restore_history/acceptance.py"
HISTORY_HELPERS = (
    "from orchestrator.tasks.inference import "
    "ROLE_PRECONDITION, acceptor_preflight, check_principal\n"
)


def test_history_helper_exception_is_exact_and_does_not_admit_the_path():
    assert HISTORY_ADAPTER not in ADMITTED
    assert composition_violations({HISTORY_ADAPTER: HISTORY_HELPERS}) == []
    assert composition_violations({"orchestrator/other.py": HISTORY_HELPERS})


@pytest.mark.parametrize(
    "source",
    [
        HISTORY_HELPERS.replace("check_principal", "InferenceRecovery"),
        HISTORY_HELPERS.replace("check_principal", "check_principal as check"),
        HISTORY_HELPERS.replace(", check_principal", ""),
        HISTORY_HELPERS.replace("orchestrator.tasks.inference", "...tasks.inference"),
        HISTORY_HELPERS.replace("orchestrator.tasks.inference", ".orchestrator.tasks.inference"),
        HISTORY_HELPERS.rstrip() + ", InferenceRecovery\n",
        "import orchestrator.tasks.inference as m",
        "from orchestrator.tasks import inference as m",
        "import importlib as il\nil.import_module('orchestrator.tasks.inference')",
        "__import__('orchestrator.tasks.inference')",
        HISTORY_HELPERS + "InferenceRecovery(pool)",
        HISTORY_HELPERS + "compose(inference_recovery=collaborator)",
        HISTORY_HELPERS + "store._inference = collaborator",
        HISTORY_HELPERS + "state.task_composition = bundle",
        HISTORY_HELPERS + "qualify_task_composition(enabled=True)",
    ],
)
def test_history_helper_exception_preserves_consumer_and_activation_restrictions(source):
    assert composition_violations({HISTORY_ADAPTER: source})


@pytest.mark.parametrize(
    "source",
    [
        "from orchestrator.services.restore_history.acceptance import AcceptanceProjection",
        "from orchestrator.services.restore_history.coordination import HistoryCoordinator",
        "import orchestrator.services.restore_history.acceptance as h",
        "from orchestrator.services import restore_history as h",
        "from .services.restore_history.acceptance import AcceptanceProjection as A",
        "from .services import restore_history as h",
        "import importlib as il\nh = il.import_module('orchestrator.services.restore_history.acceptance')",
        "h = __import__('orchestrator.services.restore_history.acceptance')",
        "import importlib as il\nf = il.import_module\nh = f('orchestrator.services.restore_history.acceptance')",
        "import importlib as il\nh = getattr(il, 'import_module')('orchestrator.services.restore_history.acceptance')",
        "AcceptanceProjection(identity, acceptor='a', worker='w')",
        "from orchestrator.services.restore_history.acceptance import AcceptanceProjection as A\nB = A\nB(identity, acceptor='a', worker='w')",
    ],
)
@pytest.mark.parametrize(
    "site", ["orchestrator/extra.py", "orchestrator/db.py", "orchestrator/worker/worker.py"]
)
def test_uncalled_history_runtime_import_and_construction_negatives(source, site):
    if site == "orchestrator/worker/worker.py":
        source = source.replace("from .services", "from ..services")
    assert composition_violations({site: source})
    assert composition_violations({"tests/explicit_fixture.py": source}) == []


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
        "from orchestrator.tasks.composition import qualify_task_composition as Q\nQ(enabled=True)",
        "state.task_composition = bundle",
        "setattr(memory, '_task_composition', bundle)",
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


def test_history_adapter_import_and_construction_start_no_io(monkeypatch):
    # Preload dependencies, then re-execute the adapter body under the probes.
    # The loader may read Python source; H storage, network and process I/O may
    # not begin. This is a discriminating probe, not a Python I/O sandbox.
    from orchestrator.services.restore_history import acceptance
    from orchestrator.services.restore_history.model import Identity

    def forbidden(*args, **kwargs):
        pytest.fail("history adapter import/construction initiated I/O")

    for owner, attr in (
        (asyncpg, "connect"),
        (asyncpg, "create_pool"),
        (socket, "socket"),
        (socket, "create_connection"),
        (subprocess, "Popen"),
        (asyncio, "create_task"),
        (asyncio, "create_subprocess_exec"),
        (os, "open"),
        (fcntl, "flock"),
    ):
        monkeypatch.setattr(owner, attr, forbidden)
    key = "orchestrator.services.restore_history._acceptance_import_probe"
    spec = importlib.util.spec_from_file_location(key, ROOT / HISTORY_ADAPTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, key, module)
    spec.loader.exec_module(module)
    expected = Identity(UUID(int=1), "0" * 64)
    adapter = module.AcceptanceProjection(expected, acceptor="synthetic_a", worker="synthetic_w")
    assert adapter.expected is expected
    assert adapter.acceptor == "synthetic_a"
    assert adapter.worker == "synthetic_w"
    assert acceptance.AcceptanceProjection is not module.AcceptanceProjection


def test_history_coordinator_import_and_construction_start_no_io(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from orchestrator.services.restore_history import coordination
    from orchestrator.services.restore_history.model import Identity
    from orchestrator.services.restore_history.store import Ownership

    def forbidden(*args, **kwargs):
        pytest.fail("history coordinator import/construction initiated I/O")

    for owner, attr in (
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
        monkeypatch.setattr(owner, attr, forbidden)
    key = "orchestrator.services.restore_history._coordination_import_probe"
    spec = importlib.util.spec_from_file_location(
        key, ROOT / "orchestrator/services/restore_history/coordination.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, key, module)
    spec.loader.exec_module(module)
    owner = module.HistoryCoordinator(
        Path("/synthetic/unopened"),
        Identity(UUID(int=1), "0" * 64),
        Ownership(1, frozenset({2}), 3),
    )
    assert owner._executor is None and owner._store is None
    assert coordination.HistoryCoordinator is not module.HistoryCoordinator


@pytest.mark.parametrize(
    "site,function",
    [
        ("orchestrator/db.py", "init_app_state"),
        ("orchestrator/worker/worker.py", "on_startup"),
    ],
)
@pytest.mark.parametrize(
    "damage", ["wrong_factory", "wrong_function", "extra_acceptor", "alias", "crossed_pool"]
)
def test_only_exact_approved_startup_factory_invocations_are_admitted(site, function, damage):
    source = (ROOT / site).read_text()
    if damage == "wrong_factory":
        original = "qualify_task_composition(" if "db.py" in site else "qualify_worker_composition("
        replacement = (
            "qualify_worker_composition(" if "db.py" in site else "qualify_task_composition("
        )
        source = source.replace(original, replacement)
    elif damage == "wrong_function":
        source = source.replace(f"async def {function}(", "async def unrelated_startup(")
    elif damage == "extra_acceptor":
        source = source.replace(
            "enabled=True,", "enabled=True, acceptor_dsn=settings.database_url,"
        )
    elif damage == "alias":
        source += "\nfrom orchestrator.tasks.composition import qualify_worker_composition as Q\nQ(enabled=True)\n"
    else:
        source = source.replace(
            "worker_pool=state.db_pool", "worker_pool=state.task_acceptor_pool"
        ).replace("worker_pool=db_pool", "worker_pool=other_pool")
    assert any(
        "factory activation" in finding for finding in composition_violations({site: source})
    )


@pytest.mark.parametrize(
    "path",
    [
        "orchestrator/db.py",
        "orchestrator/worker/worker.py",
        "orchestrator/main.py",
        "orchestrator/routes/tasks.py",
    ],
)
@pytest.mark.parametrize(
    "symbol", ["HistoryAcceptance", "HistoryEffects", "HistoryCoordinator", "HistoryProjection"]
)
def test_observation_startup_does_not_admit_mutating_imports_or_construction(path, symbol):
    source = (
        f"from orchestrator.services.restore_history.composition import {symbol}\nowner={symbol}()"
    )
    assert composition_violations({path: source})


@pytest.mark.parametrize(
    "source",
    [
        "from orchestrator.services.restore_history.runtime import HistoryRuntime as Alias\n",
        "import orchestrator.services.restore_history.runtime as runtime\n",
        "from orchestrator.services.restore_history.runtime import *\n",
        "from orchestrator.services.restore_history.runtime import HistoryRuntime, HistoryEffects\n",
        "state.history_observation = supplied\n",
        "async def other():\n    state.history_runtime = HistoryRuntime(history_config, settings)\n",
    ],
)
def test_startup_read_exception_does_not_allow_alias_or_foreign_binding(source):
    assert composition_violations({"orchestrator/db.py": source})


SOURCE_EXECUTION = "orchestrator/services/restore_history/source_execution.py"


def _source_phase(outer, inner, *lines, klass="SourceRun"):
    body = "".join(f"            {line}\n" for line in lines)
    return (
        f"class {klass}:\n    async def {outer}(self):\n        async def {inner}(limit):\n{body}"
    )


def test_source_run_type_import_is_exact_and_path_bound():
    approved = "from orchestrator.services.restore_history.source_execution import SourceRun\n"
    path = "orchestrator/tasks/_history_binding.py"
    assert composition_violations({path: "if TYPE_CHECKING:\n    " + approved}) == []
    assert (
        composition_violations(
            {path: "class ComputeHistoryBinding:\n    def validate(self):\n        " + approved}
        )
        == []
    )
    assert composition_violations({path: approved})
    for hostile in (
        approved.replace(" SourceRun", " SourceRun as S"),
        approved.replace(" SourceRun", " SourceRun, SourceReadPossible"),
        approved.replace(
            "orchestrator.services.restore_history.source_execution",
            "orchestrator.services.restore_history.model",
        ),
    ):
        assert composition_violations({path: "if TYPE_CHECKING:\n    " + hostile})
    assert composition_violations({"orchestrator/tasks/other.py": approved})


def test_source_execution_import_sets_are_exact():
    assert (
        composition_violations(
            {
                "orchestrator/tasks/composition.py": (
                    "from orchestrator.services.restore_history.text_execution import "
                    "HistoryResearchExecution, HistorySourcesExecution, HistoryTextExecution\n"
                ),
                "orchestrator/tasks/runner.py": (
                    "from orchestrator.services.restore_history.text_execution import "
                    "HistoryResearchExecution, HistorySourcesExecution\n"
                ),
            }
        )
        == []
    )
    # The pre-source research-only import remains admitted.
    assert (
        composition_violations(
            {
                "orchestrator/tasks/runner.py": (
                    "from orchestrator.services.restore_history.text_execution import "
                    "HistoryResearchExecution\n"
                )
            }
        )
        == []
    )
    for path, hostile in (
        (
            "orchestrator/tasks/composition.py",
            "from orchestrator.services.restore_history.text_execution import "
            "HistoryResearchExecution, HistorySourcesExecution, HistoryTextExecution, "
            "HistoryObservation\n",
        ),
        (
            "orchestrator/tasks/composition.py",
            "from orchestrator.services.restore_history.text_execution import "
            "HistoryResearchExecution, HistorySourcesExecution\n",
        ),
        (
            "orchestrator/tasks/runner.py",
            "from orchestrator.services.restore_history.text_execution import "
            "HistoryResearchExecution, HistorySourcesExecution as S\n",
        ),
        (
            "orchestrator/tasks/runner.py",
            "from orchestrator.services.restore_history.text_execution import "
            "HistoryResearchExecution, HistoryTextExecution\n",
        ),
    ):
        assert composition_violations({path: hostile})


def test_source_phase_cleanup_is_bound_to_its_owner_and_class():
    approved = _source_phase("mark", "prepare", "async with retain_pool_cleanup():", "    pass")
    assert composition_violations({SOURCE_EXECUTION: approved}) == []
    aliased = (
        "from orchestrator.services.connection_cleanup import retain_pool_cleanup as rpc\n"
        + _source_phase("mark", "prepare", "async with rpc():", "    pass")
    )
    for hostile in (
        _source_phase("publish", "prepare", "async with retain_pool_cleanup():", "    pass"),
        _source_phase(
            "mark", "prepare", "async with retain_pool_cleanup():", "    pass", klass="Other"
        ),
        "class SourceRun:\n    async def mark(self):\n        await retain_pool_cleanup()\n",
        _source_phase("mark", "prepare", "async with retain_pool_cleanup(limit):", "    pass"),
        aliased,
    ):
        assert composition_violations({SOURCE_EXECUTION: hostile})


def test_source_phase_worker_seam_is_exact():
    worker = (
        "async with effects._tasks._worker("
        "self.claim.task_id, self.claim.epoch, self.claim) as (conn, _):"
    )
    assert (
        composition_violations(
            {SOURCE_EXECUTION: _source_phase("mark", "prepare", worker, "    pass")}
        )
        == []
    )
    for hostile in (
        _source_phase(
            "mark",
            "prepare",
            "async with effects._tasks._worker(self.claim.task_id, self.claim.epoch) as (conn, _):",
            "    pass",
        ),
        _source_phase(
            "mark",
            "prepare",
            "async with effects._tasks._worker("
            "self.claim.task_id, self.claim.epoch, self.claim, "
            "allow_interrupted=True) as (conn, _):",
            "    pass",
        ),
        _source_phase("publish", "prepare", worker, "    pass"),
        _source_phase("mark", "stage", worker, "    pass"),
        _source_phase("publish", "stage", worker, "    pass"),
        _source_phase("mark", "prepare", worker, "    pass", klass="Other"),
    ):
        assert composition_violations({SOURCE_EXECUTION: hostile})


@pytest.mark.parametrize(
    "path,source",
    [
        (
            "orchestrator/tasks/inference.py",
            "class InferenceRecovery:\n"
            "    async def lock(self, conn, identity):\n"
            "        await self.require_open_in_transaction(conn, identity)\n",
        ),
        (
            "orchestrator/tasks/inference.py",
            "class Other:\n"
            "    async def register(self, conn, identity):\n"
            "        await self.require_open_in_transaction(conn, identity)\n",
        ),
        (
            "orchestrator/tasks/inference.py",
            "class InferenceRecovery:\n"
            "    async def register(self, conn, identity):\n"
            "        await self.require_open_in_transaction(conn, identity, None)\n",
        ),
        (
            "orchestrator/tasks/inference.py",
            "class InferenceRecovery:\n"
            "    async def _owned(self, conn, identity):\n"
            "        await self.require_open_in_transaction(conn, identity, stage=None)\n",
        ),
        (
            SOURCE_EXECUTION,
            _source_phase(
                "dispatch",
                "recheck",
                "await getattr(binding.recovery, 'require_open_in_transaction')"
                "(conn, binding.identity)",
            ),
        ),
        (
            SOURCE_EXECUTION,
            _source_phase(
                "publish",
                "recheck",
                "await binding.recovery.require_open_in_transaction(conn, binding.identity)",
            ),
        ),
        (
            SOURCE_EXECUTION,
            _source_phase(
                "dispatch",
                "recheck",
                "await binding.recovery.require_open_in_transaction(conn, binding.identity)",
                klass="Other",
            ),
        ),
    ],
)
def test_lifecycle_recheck_adapter_negatives(path, source):
    assert composition_violations({path: source})


def test_lifecycle_recheck_adapter_admits_the_three_approved_sites():
    assert (
        composition_violations(
            {
                "orchestrator/tasks/inference.py": (
                    "class InferenceRecovery:\n"
                    "    async def _owned(self, conn, identity):\n"
                    "        await self.require_open_in_transaction(conn, identity)\n"
                    "    async def register(self, conn, identity):\n"
                    "        await self.require_open_in_transaction(conn, identity)\n"
                ),
                SOURCE_EXECUTION: _source_phase(
                    "dispatch",
                    "recheck",
                    "await binding.recovery.require_open_in_transaction(conn, binding.identity)",
                ),
            }
        )
        == []
    )


def test_compute_history_binding_argument_shape_is_exact():
    approved = (
        "execution_owner.original_source(claim) "
        "if type(execution_owner) is HistorySourcesExecution else None"
    )
    template = (
        "async def _execute(state, claim, execution_owner):\n"
        "    state.history_binding = ComputeHistoryBinding(\n"
        "        requirement, execution_owner.effects, store._inference, identity, shared,\n"
        "        execution_owner.search_tool if research else None,\n"
        "        execution_owner.model_transport if research else None,\n"
        "        {source_run},\n"
        "    )\n"
    )
    path = "orchestrator/tasks/runner.py"
    assert composition_violations({path: template.format(source_run=approved)}) == []
    for altered in (
        "None",
        approved + " if research else None",
        "execution_owner.original_source(claim)",
        approved + ", claim",
        approved + ", source_run=None",
    ):
        assert composition_violations({path: template.format(source_run=altered)})
