"""Exact negative requirement/owned-operation inventory, not directory admission."""

import pytest

from tests.inference_composition_support import ADMITTED, composition_violations


def test_no_new_directory_or_history_adapter_is_generally_admitted():
    assert ADMITTED == {
        "orchestrator/compute_runtime.py",
        "orchestrator/tasks/store.py",
        "orchestrator/tasks/runner.py",
        "orchestrator/tasks/inference.py",
    }


@pytest.mark.parametrize("action,argument", [("set", "requirement"), ("reset", "history_token")])
def test_requirement_setter_is_only_original_runner(action, argument):
    source = (
        "from orchestrator.tasks._history_binding import required_history\n"
        f"async def run_chat_task():\n required_history.{action}({argument})"
    )
    assert composition_violations({"orchestrator/tasks/runner.py": source}) == []
    for changed in (
        source.replace("run_chat_task", "other"),
        source.replace(f"required_history.{action}", f"getattr(required_history,'{action}')"),
        source.replace(f"required_history.{action}", f"alias.{action}"),
        source.replace(f"({argument})", "(other)"),
    ):
        # A foreign receiver is separately checked by a known-symbol alias.
        if "alias." in changed:
            changed = changed.replace("async def", "alias=required_history\nasync def")
        assert composition_violations({"orchestrator/tasks/runner.py": changed})
    assert composition_violations({"orchestrator/other.py": source})


def test_owned_cancel_alias_and_foreign_receiver_refuse():
    good = "async def _cancel():\n await effects._cancel_owned(operation,auth.user_id,task,deadline,expected)"
    path = "orchestrator/services/restore_history/acceptance_host.py"
    assert composition_violations({path: good}) == []
    for bad in (
        good.replace("effects.", "other."),
        good.replace("_cancel():", "other():"),
        good.replace("effects._cancel_owned", "getattr(effects,'_cancel_owned')"),
    ):
        assert composition_violations({path: bad})


def test_omitted_native_claim_expectation_is_not_h_owned_join():
    path = "orchestrator/services/restore_history/effects.py"
    good = "async def _claim():\n await self._tasks._claim(task,worker_id=worker_id,lease_s=lease_s,expected=expected)"
    assert composition_violations({path: good}) == []
    assert composition_violations({path: good.replace("self._tasks", "other")})
    assert composition_violations({path: good.replace("expected=expected", "expected=None")})


@pytest.mark.parametrize(
    "path,module,symbol",
    [
        ("orchestrator/tasks/_history_binding.py", "effects", "HistoryEffects"),
        ("orchestrator/tasks/runner.py", "model", "HistoryError"),
        ("orchestrator/tasks/store.py", "coordination", "HistoryOperation"),
        ("orchestrator/tasks/composition.py", "text_execution", "HistoryTextExecution"),
    ],
)
def test_new_type_import_exceptions_require_exact_canonical_namespace(path, module, symbol):
    source = f"from orchestrator.services.restore_history.{module} import {symbol}"
    assert composition_violations({path: source}) == []
    for bad in (
        source.replace(f"restore_history.{module}", f"restore_history.evil.{module}"),
        source + " as Alias",
        f"__import__('orchestrator.services.restore_history.{module}')",
    ):
        assert composition_violations({path: bad})
