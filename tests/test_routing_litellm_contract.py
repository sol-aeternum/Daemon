"""Which routing combinations LiteLLM's parameter mapping refuses, on bundled metadata.

This is pinned debt from #681, not an acceptance of it. The test enumerates every
(profile, model, resolved preset) combination in ``config/model_routing.json``
through the same preset resolution production uses, plus each model's declared
``reasoning_efforts`` ladder under a synthetic ``declared:<effort>`` profile, and
runs LiteLLM's offline parameter mapping (``get_optional_params``) exactly the way
production's call reaches it.

The measurement runs in a fresh subprocess, never in the pytest process:

* ``import litellm`` may fetch an upstream model table at import time unless
  ``LITELLM_LOCAL_MODEL_COST_MAP=True`` is set first. So this module imports LiteLLM
  only inside the subprocess entry points, and the subprocess environment sets that
  variable. Nothing here sets it at import time, because that would leak into every
  other test sharing the pytest process.
* Before LiteLLM is imported, the subprocess replaces ``socket.socket.connect`` and
  ``socket.create_connection`` with functions that record and refuse every attempt.
  The JSON it prints reports those attempts, so the test proves the measurement was
  offline instead of assuming it.

Metadata is pinned: the measurement installs the capability table bundled with the
locked LiteLLM version (``GetModelCostMap.load_local_model_cost_map()``) through the
reviewed scoped helper ``tests.test_judge_qualification.scoped_sdk_metadata``, then
restores the previous state. What is pinned is therefore the *most conservative*
case: the bundled table, which is exactly what production falls back to when the
import-time fetch fails. It is NOT what production sees when the fetch succeeds - a
fetched table knows more models as reasoning-capable and refuses fewer combinations.

Running ``python -m tests.test_routing_litellm_contract`` prints both tables side by
side. The ``bundled`` column is the asserted one. The ``ambient`` column is
informational and reflects whatever table the process loaded at import; only in that
human-readable mode, and only when ``LITELLM_LOCAL_MODEL_COST_MAP`` is not set, may
an upstream fetch happen. No test reads the ambient column, and no environment
variable is set for it.

Refusals are pinned by type as well as by combination: a refusal counts only when
LiteLLM raised ``litellm.exceptions.UnsupportedParamsError``. Any other exception on
any combination fails the test and names the combination. The set below must match
reality in both directions - a new refusal fails the test, and so does a refusal that
stops happening (fix the config, then update the pin).

No provider is contacted: within the subprocess, ``litellm.completion`` and
``litellm.acompletion`` are replaced with functions that record and raise for the
duration of every measurement.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
ROUTING_CONFIG_PATH: Final[Path] = REPO_ROOT / "config" / "model_routing.json"

#: The subprocess entry point; its module import must not touch LiteLLM.
MODULE_UNDER_TEST: Final[str] = "tests.test_routing_litellm_contract"

#: Setting this *before* LiteLLM's import keeps it on the bundled model table. It is
#: set in the measurement subprocess environment only, never at module import here.
LOCAL_MODEL_COST_MAP_ENV: Final[str] = "LITELLM_LOCAL_MODEL_COST_MAP"

#: Output budget used for the synthetic ``declared:<effort>`` rows. Real profile
#: rows use that profile's ``min_output_tokens``.
DEFAULT_MAX_TOKENS: Final[int] = 1024

#: The only exception type a refusal may raise. A different exception type is a
#: contract break, even if the same combination is still refused.
REFUSAL_ERROR: Final[str] = "litellm.exceptions.UnsupportedParamsError"
REFUSAL_ERROR_NAME: Final[str] = "UnsupportedParamsError"

#: The result recorded for a combination the mapping accepted.
OK: Final[str] = "ok"

#: Exactly the (profile, model, effort) combinations LiteLLM refuses when the
#: model capability table bundled with the locked LiteLLM version is installed.
#: This is the most conservative case - the table production falls back to when
#: the import-time fetch fails - not the fetched table production usually sees,
#: which refuses fewer of these. Pinned debt from #681; see the module docstring.
#: Regenerate by running ``python -m tests.test_routing_litellm_contract`` and
#: taking the ``bundled`` column; never hand-edit it to look better.
KNOWN_REFUSALS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("background", "openrouter/openai/gpt-6-luna", "medium"),
        ("council", "openrouter/deepseek/deepseek-v4.1-flash", "high"),
        ("council", "openrouter/google/gemini-3.8-flash", "high"),
        ("council", "openrouter/openai/gpt-6-astra", "high"),
        ("council", "openrouter/openai/gpt-6-luna", "medium"),
        ("council", "openrouter/openai/gpt-6.1-sol", "high"),
        ("council", "openrouter/x-ai/grok-4.7", "high"),
        ("council", "openrouter/z-ai/glm-5.3", "high"),
        ("council", "openrouter/z-ai/glm-5.3-flash", "high"),
        ("declared:high", "openrouter/deepseek/deepseek-v4.1-flash", "high"),
        ("declared:high", "openrouter/google/gemini-3.8-flash", "high"),
        ("declared:high", "openrouter/openai/gpt-6-astra", "high"),
        ("declared:high", "openrouter/openai/gpt-6-luna", "high"),
        ("declared:high", "openrouter/openai/gpt-6.1-sol", "high"),
        ("declared:high", "openrouter/x-ai/grok-4.7", "high"),
        ("declared:high", "openrouter/z-ai/glm-5.3", "high"),
        ("declared:high", "openrouter/z-ai/glm-5.3-flash", "high"),
        ("declared:low", "openrouter/deepseek/deepseek-v4.1-flash", "low"),
        ("declared:low", "openrouter/google/gemini-3.8-flash", "low"),
        ("declared:low", "openrouter/openai/gpt-6-astra", "low"),
        ("declared:low", "openrouter/openai/gpt-6-luna", "low"),
        ("declared:low", "openrouter/openai/gpt-6.1-sol", "low"),
        ("declared:low", "openrouter/x-ai/grok-4.7", "low"),
        ("declared:low", "openrouter/z-ai/glm-5.3", "low"),
        ("declared:low", "openrouter/z-ai/glm-5.3-flash", "low"),
        ("declared:max", "openrouter/deepseek/deepseek-v4.1-flash", "max"),
        ("declared:max", "openrouter/openai/gpt-6-astra", "max"),
        ("declared:max", "openrouter/openai/gpt-6-luna", "max"),
        ("declared:max", "openrouter/openai/gpt-6.1-sol", "max"),
        ("declared:max", "openrouter/z-ai/glm-5.3", "max"),
        ("declared:max", "openrouter/z-ai/glm-5.3-flash", "max"),
        ("declared:medium", "openrouter/google/gemini-3.8-flash", "medium"),
        ("declared:medium", "openrouter/openai/gpt-6-astra", "medium"),
        ("declared:medium", "openrouter/openai/gpt-6-luna", "medium"),
        ("declared:medium", "openrouter/openai/gpt-6.1-sol", "medium"),
        ("declared:medium", "openrouter/x-ai/grok-4.7", "medium"),
        ("declared:none", "openrouter/openai/gpt-6-astra", "none"),
        ("declared:none", "openrouter/openai/gpt-6-luna", "none"),
        ("declared:xhigh", "openrouter/openai/gpt-6-astra", "xhigh"),
        ("declared:xhigh", "openrouter/openai/gpt-6-luna", "xhigh"),
        ("declared:xhigh", "openrouter/openai/gpt-6.1-sol", "xhigh"),
        ("declared:xhigh", "openrouter/x-ai/grok-4.7", "xhigh"),
        ("memory_decomposition", "openrouter/openai/gpt-6-luna", "none"),
        ("reasoning", "openrouter/openai/gpt-6-astra", "high"),
        ("reasoning", "openrouter/openai/gpt-6.1-sol", "high"),
        ("reasoning", "openrouter/z-ai/glm-5.3", "high"),
        ("research", "openrouter/openai/gpt-6-luna", "medium"),
        ("routine", "openrouter/openai/gpt-6-luna", "medium"),
    }
)

USAGE = """\
usage: python -m tests.test_routing_litellm_contract [--json | --negative-control]

With no arguments, print the bundled and ambient tables side by side. The ambient
column is informational and may be fetched from upstream: only this human-readable
mode, and only when LITELLM_LOCAL_MODEL_COST_MAP is not set, can make a network
request. Set LITELLM_LOCAL_MODEL_COST_MAP=True to keep it offline too.

--json              measure on bundled metadata in an offline-guarded subprocess and
                    print the outcomes as JSON. This is what the tests read.
--negative-control  run the same socket guard, deliberately attempt one connection,
                    and print the result as JSON to prove the guard records attempts.
"""


@dataclass(frozen=True)
class Outcome:
    """One combination and what LiteLLM did with it, as serialised by the subprocess."""

    profile: str
    model: str
    effort: str
    #: ``"ok"`` or the exception class LiteLLM raised.
    result: str = OK
    #: First line of the exception message, truncated; empty when accepted.
    detail: str = ""
    #: Qualified class name of the raised exception; empty when accepted.
    qualified_error: str = ""

    @property
    def label(self) -> tuple[str, str, str]:
        return (self.profile, self.model, self.effort)

    @property
    def refused(self) -> bool:
        return self.result != OK


def _load_config() -> dict[str, Any]:
    return json.loads(ROUTING_CONFIG_PATH.read_text(encoding="utf-8"))


def _split_provider(model: str) -> tuple[str, str]:
    """``openrouter/a/b`` -> ``("openrouter", "a/b")``; other prefixes pass through."""
    provider, separator, bare = model.partition("/")
    if not separator or not bare:
        return provider, model
    return provider, bare


def _call_params(preset: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """The parameters production sends LiteLLM for this preset.

    ``compute_runtime`` builds ``call = {**resolved, "model": ..., "max_tokens":
    output_tokens, ...}`` where ``resolved`` is
    ``apply_model_parameter_presets`` output. So ``reasoning_effort`` and
    ``include_reasoning`` travel in the same kwargs, and LiteLLM does not strip
    ``include_reasoning`` before ``get_optional_params`` (it lands in the
    provider's non-default parameters). ``max_tokens`` is always sent.
    ``response_format`` is only sent when the caller supplied one, so none is
    invented here.
    """
    params: dict[str, Any] = {
        "max_tokens": max_tokens,
        "reasoning_effort": preset["reasoning_effort"],
    }
    if "include_reasoning" in preset:
        params["include_reasoning"] = preset["include_reasoning"]
    return params


def _combinations() -> list[tuple[str, str, str, dict[str, Any]]]:
    """Every combination the routing config implies, in config order.

    Imports ``orchestrator.model_routing`` here, not at module level: this function
    is only ever called inside the measurement subprocess.
    """
    from orchestrator import model_routing

    config = _load_config()
    output_tokens = {
        profile["profile"]: int(profile["min_output_tokens"]) for profile in config["profiles"]
    }
    combinations: list[tuple[str, str, str, dict[str, Any]]] = []
    for profile in config["profiles"]:
        name = profile["profile"]
        for group in profile["groups"]:
            for model in group["models"]:
                preset = model_routing.model_parameter_presets(model, name)
                effort = preset.get("reasoning_effort")
                combinations.append(
                    (
                        name,
                        model,
                        effort if isinstance(effort, str) else "<none>",
                        _call_params(preset, output_tokens[name]),
                    )
                )
    for entry in config["models"]:
        model = entry["model"]
        for effort in entry["reasoning_efforts"]:
            combinations.append(
                (
                    f"declared:{effort}",
                    model,
                    effort,
                    _call_params({"reasoning_effort": effort}, DEFAULT_MAX_TOKENS),
                )
            )
    return combinations


def _qualified_name(exc: BaseException) -> str:
    cls = type(exc)
    return f"{cls.__module__}.{cls.__qualname__}"


def _detail(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:200] if text else ""


def _measure(profile: str, model: str, effort: str, params: dict[str, Any]) -> Outcome:
    import litellm

    provider, bare_model = _split_provider(model)
    try:
        litellm.get_optional_params(
            model=bare_model,
            custom_llm_provider=provider,
            **params,
        )
    except Exception as exc:
        return Outcome(
            profile,
            model,
            effort,
            result=type(exc).__name__,
            detail=_detail(exc),
            qualified_error=_qualified_name(exc),
        )
    return Outcome(profile, model, effort)


def _install_socket_guard(attempts: list[str]) -> None:
    """Record and refuse outbound connections. Must run before LiteLLM is imported."""
    import socket

    def guarded_connect(sock: Any, address: Any) -> None:
        attempts.append(f"socket.socket.connect({address!r})")
        raise OSError("network access is blocked during the routing contract measurement")

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(f"socket.create_connection({address!r})")
        raise OSError("network access is blocked during the routing contract measurement")

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.create_connection = guarded_create_connection


def bundled_model_cost_map() -> dict[str, Any]:
    """The capability table bundled with the installed LiteLLM version. Never fetched."""
    from litellm.litellm_core_utils.get_model_cost_map import GetModelCostMap

    return GetModelCostMap.load_local_model_cost_map()


@contextmanager
def _no_provider_calls(calls: list[str]) -> Iterator[None]:
    """Replace LiteLLM's completion entry points with recorders that raise."""
    import litellm

    def refuse(*args: Any, **kwargs: Any) -> Any:
        calls.append("litellm completion entry point")
        raise AssertionError("the routing contract test must never contact a provider")

    originals = (litellm.completion, litellm.acompletion)
    litellm.completion = refuse
    litellm.acompletion = refuse
    try:
        yield
    finally:
        litellm.completion, litellm.acompletion = originals


def measure_bundled() -> tuple[list[Outcome], list[str]]:
    """Measure every combination on bundled metadata, with provider entry points fatal.

    The ambient table and the fallback-rule registry are restored before returning.
    Returns the outcomes and any provider entry-point attempts. Raises if metadata
    was not restored. Only ever called inside the measurement subprocess.
    """
    import litellm

    from tests.test_judge_qualification import scoped_sdk_metadata

    calls: list[str] = []
    monkeypatch = pytest.MonkeyPatch()
    original_map = litellm.model_cost
    try:
        with _no_provider_calls(calls):
            with scoped_sdk_metadata(monkeypatch, bundled_model_cost_map()):
                outcomes = [_measure(*combination) for combination in _combinations()]
    finally:
        monkeypatch.undo()
    assert litellm.model_cost is original_map, "bundled metadata was not restored"
    return outcomes, calls


def measure_ambient() -> tuple[list[Outcome], list[str]]:
    """Informational only: measure on whatever table this process loaded at import.

    That table may be fetched from upstream. No test asserts against this column.
    The case-insensitive key cache is cleared first so it cannot leak bundled
    entries into the ambient lookups.
    """
    from litellm import utils

    utils._model_cost_lowercase_map = None
    calls: list[str] = []
    with _no_provider_calls(calls):
        outcomes = [_measure(*combination) for combination in _combinations()]
    return outcomes, calls


def _refusals(outcomes: list[Outcome]) -> frozenset[tuple[str, str, str]]:
    return frozenset(outcome.label for outcome in outcomes if outcome.refused)


def wrong_refusal_types(outcomes: list[Outcome]) -> list[str]:
    """One line per refusal that did not raise ``REFUSAL_ERROR``."""
    return [
        f"{outcome.profile} | {outcome.model} | {outcome.effort} refused with "
        f"{outcome.result}, not {REFUSAL_ERROR_NAME}: {outcome.detail}"
        for outcome in outcomes
        if outcome.refused and outcome.qualified_error != REFUSAL_ERROR
    ]


def assert_contract(outcomes: list[Outcome]) -> None:
    """Fail, naming each combination, if a refusal raised the wrong exception type."""
    wrong = wrong_refusal_types(outcomes)
    assert not wrong, "LiteLLM refused with an unexpected exception type:\n" + "\n".join(wrong)


def _json_measurement() -> dict[str, Any]:
    """The contract measurement, with the socket guard installed before LiteLLM."""
    attempts: list[str] = []
    _install_socket_guard(attempts)

    from litellm.exceptions import UnsupportedParamsError

    outcomes, calls = measure_bundled()
    refusal_error_type = (
        f"{UnsupportedParamsError.__module__}.{UnsupportedParamsError.__qualname__}"
    )
    return {
        "mode": "contract",
        "refusal_error_type": refusal_error_type,
        "offline_attempts": attempts,
        "provider_calls": calls,
        "outcomes": [
            {
                "profile": outcome.profile,
                "model": outcome.model,
                "effort": outcome.effort,
                "result": outcome.result,
                "detail": outcome.detail,
                "qualified_error": outcome.qualified_error,
            }
            for outcome in outcomes
        ],
    }


def _negative_control_measurement() -> dict[str, Any]:
    """Run the same guard, then deliberately attempt one connection."""
    import socket

    attempts: list[str] = []
    _install_socket_guard(attempts)
    blocked = False
    try:
        socket.create_connection(("127.0.0.1", 9))
    except OSError:
        blocked = True
    return {"mode": "negative-control", "offline_attempts": attempts, "blocked": blocked}


def _print_tables() -> int:
    """Print both tables side by side. Informational; no file is written.

    The ambient column may be fetched from upstream. That is the only mode where a
    fetch is possible, and only while ``LITELLM_LOCAL_MODEL_COST_MAP`` is unset.
    """
    bundled_outcomes, bundled_calls = measure_bundled()
    ambient_outcomes, ambient_calls = measure_ambient()
    bundled = {outcome.label: outcome for outcome in bundled_outcomes}
    ambient = {outcome.label: outcome for outcome in ambient_outcomes}

    labels = sorted(set(bundled) | set(ambient))
    print("profile\tmodel\teffort\tbundled\tambient, may be fetched")
    for label in labels:
        left = bundled.get(label)
        right = ambient.get(label)
        print(
            "\t".join(
                (
                    *label,
                    left.result if left else "-",
                    right.result if right else "-",
                )
            )
        )

    bundled_refusals = _refusals(list(bundled.values()))
    ambient_refusals = _refusals(list(ambient.values()))
    print(
        f"\n{len(bundled)} combinations; bundled: {len(bundled_refusals)} refused; "
        f"ambient, may be fetched: {len(ambient_refusals)} refused"
    )
    print(
        f"provider entry points called: bundled={len(bundled_calls)}, ambient={len(ambient_calls)}"
    )
    for title, refusals in (
        ("bundled", bundled_refusals),
        ("ambient, may be fetched", ambient_refusals),
    ):
        print(f"{title} refusals:")
        for profile, model, effort in sorted(refusals):
            print(f"  {profile}\t{model}\t{effort}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m tests.test_routing_litellm_contract``."""
    args = sys.argv[1:] if argv is None else argv
    if "--help" in args or "-h" in args:
        print(USAGE)
        return 0
    if "--json" in args:
        # LiteLLM prints provider advice to stdout while rejecting parameters; keep
        # stdout machine-readable so the parent can parse exactly one JSON document.
        with redirect_stdout(sys.stderr):
            payload = _json_measurement()
        print(json.dumps(payload))
        return 0
    if "--negative-control" in args:
        print(json.dumps(_negative_control_measurement()))
        return 0
    return _print_tables()


def _run_module(*args: str) -> dict[str, Any]:
    """Run the measurement in a fresh interpreter with the bundled table pinned."""
    environment = os.environ.copy()
    environment[LOCAL_MODEL_COST_MAP_ENV] = "True"
    # Same dotenv-loading guard tests/conftest.py applies; the subprocess does not
    # load conftest, and LiteLLM's import would otherwise read a colocated .env.
    environment["LITELLM_MODE"] = "PRODUCTION"
    completed = subprocess.run(
        [sys.executable, "-m", MODULE_UNDER_TEST, *args],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert completed.returncode == 0, (
        f"the offline measurement subprocess failed ({completed.returncode}):\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "the measurement subprocess printed something other than JSON:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        ) from exc
    assert isinstance(payload, dict), f"unexpected subprocess payload: {payload!r}"
    return payload


@pytest.fixture(scope="session")
def contract_measurement() -> dict[str, Any]:
    """The single offline measurement every contract test reads; never re-run."""
    return _run_module("--json")


def _outcomes(payload: dict[str, Any]) -> list[Outcome]:
    return [Outcome(**row) for row in payload["outcomes"]]


def _assert_offline(payload: dict[str, Any]) -> None:
    """The measurement must have attempted no connection and no provider call."""
    assert payload["mode"] == "contract"
    assert payload["offline_attempts"] == [], (
        "the routing contract measurement attempted a network connection: "
        f"{payload['offline_attempts']}"
    )
    assert payload["provider_calls"] == [], "a provider completion was attempted"
    assert payload["refusal_error_type"] == REFUSAL_ERROR


def test_refusals_match_the_known_list(contract_measurement: dict[str, Any]) -> None:
    _assert_offline(contract_measurement)
    outcomes = _outcomes(contract_measurement)
    assert_contract(outcomes)
    observed = _refusals(outcomes)
    detail = {outcome.label: f"{outcome.result}: {outcome.detail}" for outcome in outcomes}
    added = sorted(observed - KNOWN_REFUSALS)
    repaired = sorted(KNOWN_REFUSALS - observed)
    assert observed == KNOWN_REFUSALS, (
        "LiteLLM refusals changed on the bundled metadata table; update KNOWN_REFUSALS "
        "and fix the cause.\n"
        f"new refusals ({len(added)}): {[(item, detail[item]) for item in added]}\n"
        f"no longer refused ({len(repaired)}): {repaired}"
    )


def test_a_different_refusal_type_is_rejected(contract_measurement: dict[str, Any]) -> None:
    """A known refusal that raises any other error type must fail, naming it."""
    _assert_offline(contract_measurement)
    outcomes = _outcomes(contract_measurement)
    target = next(outcome for outcome in outcomes if outcome.label in KNOWN_REFUSALS)
    tampered = [
        replace(
            outcome,
            result="ValueError",
            detail="fictional replacement for a refused combination",
            qualified_error="builtins.ValueError",
        )
        if outcome.label == target.label
        else outcome
        for outcome in outcomes
    ]
    # The combination is still refused, so only the type check can catch this.
    assert _refusals(tampered) == _refusals(outcomes)
    with pytest.raises(AssertionError) as caught:
        assert_contract(tampered)
    message = str(caught.value)
    assert f"{target.profile} | {target.model} | {target.effort}" in message
    assert "ValueError" in message
    assert REFUSAL_ERROR_NAME in message
    # The untouched measurements still satisfy the contract.
    assert_contract(outcomes)


def test_the_offline_guard_blocks_and_reports_a_connection() -> None:
    """Negative control: the same guard records a deliberate connection attempt."""
    payload = _run_module("--negative-control")
    assert payload["mode"] == "negative-control"
    assert payload["blocked"] is True
    assert len(payload["offline_attempts"]) == 1, payload["offline_attempts"]
    assert "socket.create_connection" in payload["offline_attempts"][0]


def test_enumeration_is_not_empty_and_covers_council(
    contract_measurement: dict[str, Any],
) -> None:
    _assert_offline(contract_measurement)
    outcomes = _outcomes(contract_measurement)

    config = _load_config()
    profiles = {outcome.profile for outcome in outcomes}
    for profile in config["profiles"]:
        assert profile["profile"] in profiles, f"profile {profile['profile']!r} not enumerated"
    assert "council" in profiles
    assert outcomes, "no routing combinations were enumerated"
    assert len(outcomes) > len(config["models"]), "declared ladder rows are missing"


if __name__ == "__main__":
    raise SystemExit(main())
