"""Which routing combinations LiteLLM's parameter mapping refuses, on bundled metadata.

This is pinned debt from #681, not an acceptance of it. The test enumerates
every (profile, model, resolved preset) combination in ``config/model_routing.json``
through the same preset resolution production uses, plus each model's declared
``reasoning_efforts`` ladder under a synthetic ``declared:<effort>`` profile, and
runs LiteLLM's offline parameter mapping (``get_optional_params``) exactly the way
production's call reaches it.

Metadata is pinned. Every measurement installs the model capability table bundled
in the locked LiteLLM version
(``GetModelCostMap.load_local_model_cost_map()``) through the reviewed scoped
helper ``tests.test_judge_qualification.scoped_sdk_metadata``, then restores the
previous state. ``import litellm`` otherwise tries to fetch an upstream table at
import time and silently falls back to the bundled one; this test never reads
whatever that fetch produced, so it is reproducible on an unchanged checkout with
no network access.

What is pinned is therefore the *most conservative* case: the bundled table, which
is exactly what production falls back to when the import-time fetch fails. It is
NOT what production sees when the fetch succeeds - a fetched table knows more
models as reasoning-capable and refuses fewer combinations. Running ``python -m
tests.test_routing_litellm_contract`` prints both tables side by side; only the
bundled column is asserted, and no test depends on the ambient one.

Refusals are pinned by type as well as by combination: a refusal counts only when
LiteLLM raised ``UnsupportedParamsError``. Any other exception on any combination
fails the test and names the combination. The set below must match reality in both
directions - a new refusal fails the test, and so does a refusal that stops
happening (fix the config, then update the pin).

No provider is contacted: ``litellm.completion`` and ``litellm.acompletion`` are
patched to raise for the duration of every measurement.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import litellm
import pytest
from litellm.exceptions import UnsupportedParamsError
from litellm.litellm_core_utils.get_model_cost_map import GetModelCostMap

from orchestrator import model_routing
from tests.test_judge_qualification import scoped_sdk_metadata

ROUTING_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "model_routing.json"

#: Output budget used for the synthetic ``declared:<effort>`` rows. Real profile
#: rows use that profile's ``min_output_tokens``.
DEFAULT_MAX_TOKENS = 1024

#: The only exception type a refusal may raise. A different exception type is a
#: contract break, even if the same combination is still refused.
REFUSAL_ERROR = UnsupportedParamsError

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


@dataclass(frozen=True)
class Outcome:
    """One combination and what LiteLLM did with it."""

    profile: str
    model: str
    effort: str
    #: The exception LiteLLM raised, or ``None`` when the mapping accepted the params.
    error: BaseException | None = None

    @property
    def result(self) -> str:
        """``"ok"`` or the exception class LiteLLM raised."""
        return "ok" if self.error is None else type(self.error).__name__

    @property
    def detail(self) -> str:
        if self.error is None:
            return ""
        text = str(self.error).strip()
        return text.splitlines()[0][:200] if text else ""

    @property
    def label(self) -> tuple[str, str, str]:
        return (self.profile, self.model, self.effort)


def _load_config() -> dict[str, Any]:
    return json.loads(ROUTING_CONFIG_PATH.read_text(encoding="utf-8"))


def _split_provider(model: str) -> tuple[str, str]:
    """``openrouter/a/b`` -> ``("openrouter", "a/b")``; other prefixes pass through."""
    provider, separator, bare = model.partition("/")
    if not separator or not bare:
        return provider, model
    return provider, bare


def _call_params(preset: Mapping[str, Any], max_tokens: int) -> dict[str, Any]:
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
    """Every combination the routing config implies, in config order."""
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


def _measure(profile: str, model: str, effort: str, params: dict[str, Any]) -> Outcome:
    provider, bare_model = _split_provider(model)
    try:
        litellm.get_optional_params(
            model=bare_model,
            custom_llm_provider=provider,
            **params,
        )
    except Exception as exc:
        return Outcome(profile, model, effort, exc)
    return Outcome(profile, model, effort)


def bundled_model_cost_map() -> dict[str, Any]:
    """The capability table bundled with the installed LiteLLM version. Never fetched."""
    return GetModelCostMap.load_local_model_cost_map()


@contextmanager
def pinned_bundled_metadata(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Install the bundled table with the reviewed helper, restoring state on exit.

    The helper also installs the compiled fallback generalization rules and clears
    the case-insensitive lookup cache, so lookups behave exactly as they do when
    the import-time fetch falls back to bundled data. Its ``monkeypatch`` context
    restores every replaced object even if an assertion raises.
    """
    with scoped_sdk_metadata(monkeypatch, bundled_model_cost_map()):
        yield


def _install_refusal(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make any provider call fatal; returns the list of attempted calls."""

    calls: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        calls.append("litellm completion entry point")
        raise AssertionError("the routing contract test must never contact a provider")

    monkeypatch.setattr(litellm, "completion", refuse)
    monkeypatch.setattr(litellm, "acompletion", refuse)
    return calls


@contextmanager
def _no_provider_calls() -> Iterator[list[str]]:
    calls: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        calls.append("litellm completion entry point")
        raise AssertionError("the routing contract test must never contact a provider")

    originals = (litellm.completion, litellm.acompletion)
    litellm.completion = refuse
    litellm.acompletion = refuse
    try:
        yield calls
    finally:
        litellm.completion, litellm.acompletion = originals


def measure_bundled(monkeypatch: pytest.MonkeyPatch) -> list[Outcome]:
    """Measure every combination on bundled metadata, with provider entry points fatal.

    The ambient table and the fallback-rule registry are restored before returning.
    """
    original_map = litellm.model_cost
    with _no_provider_calls() as calls:
        with pinned_bundled_metadata(monkeypatch):
            outcomes = [_measure(*combination) for combination in _combinations()]
    assert calls == [], "a provider completion was attempted"
    assert litellm.model_cost is original_map, "bundled metadata was not restored"
    return outcomes


def measure_ambient() -> list[Outcome]:
    """Informational only: measure on whatever table this process loaded at import.

    That table may be fetched from upstream. No test asserts against this column.
    The case-insensitive key cache is cleared first so it cannot leak bundled
    entries into the ambient lookups.
    """
    from litellm import utils

    utils._model_cost_lowercase_map = None
    with _no_provider_calls() as calls:
        outcomes = [_measure(*combination) for combination in _combinations()]
    assert calls == [], "a provider completion was attempted"
    return outcomes


def _refusals(outcomes: list[Outcome]) -> frozenset[tuple[str, str, str]]:
    return frozenset(outcome.label for outcome in outcomes if outcome.error is not None)


def wrong_refusal_types(outcomes: list[Outcome]) -> list[str]:
    """One line per refusal that raised something other than ``REFUSAL_ERROR``."""
    return [
        f"{outcome.profile} | {outcome.model} | {outcome.effort} refused with "
        f"{outcome.result}, not {REFUSAL_ERROR.__name__}: {outcome.detail}"
        for outcome in outcomes
        if outcome.error is not None and not isinstance(outcome.error, REFUSAL_ERROR)
    ]


def assert_contract(outcomes: list[Outcome]) -> None:
    """Fail, naming each combination, if a refusal raised the wrong exception type."""
    wrong = wrong_refusal_types(outcomes)
    assert not wrong, "LiteLLM refused with an unexpected exception type:\n" + "\n".join(wrong)


def test_refusals_match_the_known_list(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = measure_bundled(monkeypatch)
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


def test_a_different_refusal_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known refusal that raises any other error type must fail, naming it."""
    outcomes = measure_bundled(monkeypatch)
    target = next(outcome for outcome in outcomes if outcome.label in KNOWN_REFUSALS)
    tampered = [
        replace(outcome, error=ValueError("fictional replacement for a refused combination"))
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
    assert REFUSAL_ERROR.__name__ in message
    # The untouched measurements still satisfy the contract.
    assert_contract(outcomes)


def test_enumeration_is_not_empty_and_covers_council(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_refusal(monkeypatch)
    outcomes = measure_bundled(monkeypatch)
    assert calls == [], "a provider completion was attempted"

    config = _load_config()
    profiles = {outcome.profile for outcome in outcomes}
    for profile in config["profiles"]:
        assert profile["profile"] in profiles, f"profile {profile['profile']!r} not enumerated"
    assert "council" in profiles
    assert outcomes, "no routing combinations were enumerated"
    assert len(outcomes) > len(config["models"]), "declared ladder rows are missing"


def main() -> int:
    """Print both tables side by side. Informational; no file is written."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        ambient = {outcome.label: outcome for outcome in measure_ambient()}
        bundled = {outcome.label: outcome for outcome in measure_bundled(monkeypatch)}
    finally:
        monkeypatch.undo()

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
    for title, refusals in (
        ("bundled", bundled_refusals),
        ("ambient, may be fetched", ambient_refusals),
    ):
        print(f"{title} refusals:")
        for profile, model, effort in sorted(refusals):
            print(f"  {profile}\t{model}\t{effort}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
