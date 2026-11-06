"""Which routing combinations LiteLLM's parameter mapping refuses.

This is pinned debt from #681, not an acceptance of it. The test enumerates
every (profile, model, resolved preset) combination in ``config/model_routing.json``
through the same preset resolution production uses, plus each model's declared
``reasoning_efforts`` ladder under a synthetic ``declared:<effort>`` profile, and
runs LiteLLM's offline parameter mapping (``get_optional_params``) exactly the way
production's call reaches it.

No provider is contacted: ``litellm.completion`` and ``litellm.acompletion`` are
patched to raise for the duration of every measurement. The pinned set below must
match reality in both directions - a new refusal fails the test, and so does a
refusal that stops happening (fix the config, then update the pin).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import litellm
import pytest

from orchestrator import model_routing

ROUTING_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "model_routing.json"

#: Output budget used for the synthetic ``declared:<effort>`` rows. Real profile
#: rows use that profile's ``min_output_tokens``.
DEFAULT_MAX_TOKENS = 1024

#: Exactly the (profile, model, effort) combinations LiteLLM refuses today.
#: Pinned debt from #681; see the module docstring. Every entry raises
#: ``UnsupportedParamsError`` because LiteLLM's OpenRouter mapping only accepts
#: ``reasoning_effort`` for models it already knows as reasoning-capable.
KNOWN_REFUSALS: frozenset[tuple[str, str, str]] = frozenset(
    {
        (
            "council",
            "openrouter/deepseek/deepseek-v4.1-flash",
            "high",
        ),
        ("council", "openrouter/x-ai/grok-4.7", "high"),
        ("council", "openrouter/z-ai/glm-5.3", "high"),
        ("council", "openrouter/z-ai/glm-5.3-flash", "high"),
        ("declared:high", "openrouter/deepseek/deepseek-v4.1-flash", "high"),
        ("declared:high", "openrouter/x-ai/grok-4.7", "high"),
        ("declared:high", "openrouter/z-ai/glm-5.3", "high"),
        ("declared:high", "openrouter/z-ai/glm-5.3-flash", "high"),
        ("declared:low", "openrouter/deepseek/deepseek-v4.1-flash", "low"),
        ("declared:low", "openrouter/x-ai/grok-4.7", "low"),
        ("declared:low", "openrouter/z-ai/glm-5.3", "low"),
        ("declared:low", "openrouter/z-ai/glm-5.3-flash", "low"),
        ("declared:max", "openrouter/deepseek/deepseek-v4.1-flash", "max"),
        ("declared:max", "openrouter/z-ai/glm-5.3", "max"),
        ("declared:max", "openrouter/z-ai/glm-5.3-flash", "max"),
        ("declared:medium", "openrouter/x-ai/grok-4.7", "medium"),
        ("declared:xhigh", "openrouter/x-ai/grok-4.7", "xhigh"),
        ("reasoning", "openrouter/z-ai/glm-5.3", "high"),
    }
)


@dataclass(frozen=True)
class Outcome:
    """One combination and what LiteLLM did with it."""

    profile: str
    model: str
    effort: str
    #: ``"ok"`` or the exception class LiteLLM raised.
    result: str
    detail: str = ""


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
        detail = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
        return Outcome(profile, model, effort, type(exc).__name__, detail)
    return Outcome(profile, model, effort, "ok")


def _measure_all() -> list[Outcome]:
    return [_measure(*combination) for combination in _combinations()]


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


def measure_all() -> list[Outcome]:
    """Measure every combination with provider entry points patched to fail."""
    with _no_provider_calls() as calls:
        outcomes = _measure_all()
    assert calls == [], "a provider completion was attempted"
    return outcomes


def _refusals(outcomes: list[Outcome]) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        (outcome.profile, outcome.model, outcome.effort)
        for outcome in outcomes
        if outcome.result != "ok"
    )


def test_refusals_match_the_known_list() -> None:
    outcomes = measure_all()
    observed = _refusals(outcomes)
    detail = {tuple((o.profile, o.model, o.effort)): f"{o.result}: {o.detail}" for o in outcomes}
    added = sorted(observed - KNOWN_REFUSALS)
    repaired = sorted(KNOWN_REFUSALS - observed)
    assert observed == KNOWN_REFUSALS, (
        "LiteLLM refusals changed; update KNOWN_REFUSALS and fix the cause.\n"
        f"new refusals ({len(added)}): {[(item, detail[item]) for item in added]}\n"
        f"no longer refused ({len(repaired)}): {repaired}"
    )


def test_enumeration_is_not_empty_and_covers_council(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_refusal(monkeypatch)
    outcomes = measure_all()
    assert calls == [], "a provider completion was attempted"

    config = _load_config()
    profiles = {outcome.profile for outcome in outcomes}
    for profile in config["profiles"]:
        assert profile["profile"] in profiles, f"profile {profile['profile']!r} not enumerated"
    assert "council" in profiles
    assert outcomes, "no routing combinations were enumerated"
    assert len(outcomes) > len(config["models"]), "declared ladder rows are missing"


def main() -> int:
    """Print the full contract table, sorted by profile then model. No file is written."""
    outcomes = measure_all()
    for outcome in sorted(outcomes, key=lambda item: (item.profile, item.model, item.effort)):
        print(f"{outcome.profile}\t{outcome.model}\t{outcome.effort}\t{outcome.result}")
    refusals = _refusals(outcomes)
    print(f"\n{len(outcomes)} combinations, {len(refusals)} refused")
    if refusals:
        print("refused:")
        for profile, model, effort in sorted(refusals):
            print(f"  {profile}\t{model}\t{effort}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
