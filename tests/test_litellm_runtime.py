"""Startup pin checks and transport adaptation, with no provider execution."""

from __future__ import annotations

import io
import logging
import sys
import threading
from unittest.mock import Mock

import pytest

from orchestrator import runtime, safe_logging
from orchestrator.litellm_runtime import (
    LocalModelCostMapRequired,
    openrouter_reasoning_params,
    require_local_model_cost_map,
)


@pytest.fixture(autouse=True)
def restore_logging_state(monkeypatch):
    """Startup reconfigures process-wide sinks/hooks; never leak them to later tests."""
    loggers = [logging.getLogger()] + [
        value
        for value in logging.root.manager.loggerDict.values()
        if isinstance(value, logging.Logger)
    ]
    saved = [(logger, logger.handlers[:], logger.propagate, logger.level) for logger in loggers]
    last = logging.lastResort
    monkeypatch.setattr(safe_logging, "_stream", safe_logging._stream)
    monkeypatch.setattr(safe_logging, "_managed_level", safe_logging._managed_level)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    try:
        yield
    finally:
        for logger, handlers, propagate, level in saved:
            logger.handlers[:] = handlers
            logger.propagate = propagate
            logger.setLevel(level)
        logging.lastResort = last


@pytest.mark.parametrize("role", ["backend", "worker"])
@pytest.mark.parametrize("value", [None, "False", "True"])
def test_managed_service_checks_pin_before_imports(monkeypatch, role, value):
    from litellm.litellm_core_utils import get_model_cost_map as cost_map

    if value is None:
        monkeypatch.delenv("LITELLM_LOCAL_MODEL_COST_MAP", raising=False)
    else:
        monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", value)
    check = Mock(
        return_value={
            "source": "local",
            "is_env_forced": True,
            "url": None,
            "fallback_reason": None,
        }
    )
    monkeypatch.setattr(cost_map, "get_model_cost_map_source_info", check)
    imports = Mock(side_effect=RuntimeError("import sentinel"))
    monkeypatch.setattr(runtime.importlib, "import_module", imports)
    if value == "True":
        with pytest.raises(RuntimeError, match="import sentinel"):
            runtime.prepare(role, io.StringIO())
        check.assert_called_once_with()
        imports.assert_called_once_with("orchestrator.config")
    else:
        with pytest.raises(LocalModelCostMapRequired, match="pinned local model table"):
            runtime.prepare(role, io.StringIO())
        check.assert_not_called()
        imports.assert_not_called()


@pytest.mark.parametrize(
    "info",
    [
        {"source": "remote", "is_env_forced": False, "url": "untrusted", "fallback_reason": None},
        {
            "source": "local",
            "is_env_forced": False,
            "url": "untrusted",
            "fallback_reason": "untrusted",
        },
        {},
    ],
)
def test_env_alone_cannot_bless_an_already_imported_table(monkeypatch, info):
    from litellm.litellm_core_utils import get_model_cost_map as cost_map

    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setattr(cost_map, "get_model_cost_map_source_info", lambda: info)
    with pytest.raises(LocalModelCostMapRequired) as error:
        require_local_model_cost_map()
    assert str(error.value) == "LiteLLM requires the pinned local model table"


def test_pin_failure_emits_only_a_fixed_safe_stage(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(runtime.sys, "stderr", output)

    def fail(*args):
        raise LocalModelCostMapRequired("content must never be logged")

    with pytest.raises(SystemExit) as error:
        runtime.launch("backend", fail)
    assert error.value.code == 1
    assert '"stage":"litellm_model_table"' in output.getvalue().replace(" ", "")
    assert "content must never" not in output.getvalue()


def test_reasoning_adapter_is_narrow_and_does_not_mutate_transport():
    provider = {"only": ["fixture"], "zdr": True}
    original = {
        "reasoning_effort": "max",
        "include_reasoning": True,
        "extra_body": {"provider": provider},
    }
    adapted = openrouter_reasoning_params(original, "openrouter/test/model")
    assert adapted == {
        "include_reasoning": True,
        "extra_body": {"provider": provider, "reasoning": {"effort": "xhigh"}},
    }
    assert original["reasoning_effort"] == "max"
    assert original["extra_body"] == {"provider": provider}
    assert openrouter_reasoning_params(original, "azure/test") is original
    without_effort = {"include_reasoning": True}
    assert openrouter_reasoning_params(without_effort, "openrouter/test") is without_effort
