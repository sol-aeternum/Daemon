"""The chat routing chart is generated from configuration and must not go stale.

If this fails after a routing change, regenerate the chart:

    PYTHONPATH=. uv run python scripts/render_chat_routing.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import render_chat_routing as chart


def test_chart_and_document_match_current_configuration() -> None:
    assert chart.main(["--check"]) == 0, (
        "docs/CHAT_ROUTING.md or docs/CHAT_ROUTING.svg is stale; "
        "run: PYTHONPATH=. uv run python scripts/render_chat_routing.py"
    )


def test_every_configured_chat_candidate_is_documented() -> None:
    facts = chart.collect()
    document = chart.DOC_PATH.read_text(encoding="utf-8")
    for groups in facts.profiles.values():
        for group in groups:
            for candidate in group.candidates:
                assert f"`{candidate.model.removeprefix(chart.OPENROUTER)}`" in document


def test_output_is_deterministic() -> None:
    facts = chart.collect()
    assert chart.render_svg(facts) == chart.render_svg(chart.collect())
    assert chart.render_section(facts) == chart.render_section(chart.collect())


def _remapped_research(tmp_path: Path) -> Path:
    data = json.loads(chart.ROUTING_PATH.read_text(encoding="utf-8"))
    for profile in data["profiles"]:
        if profile["profile"] == "research":
            profile["groups"] = [
                {"group": "flash", "models": ["openrouter/deepseek/deepseek-v4.1-flash"]}
            ]
    path = tmp_path / "model_routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_model_change_makes_the_check_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remapped = chart.collect(_remapped_research(tmp_path), chart.POLICY_PATH)
    monkeypatch.setattr(chart, "collect", lambda: remapped)
    assert chart.main(["--check"]) == 1


def test_remapping_research_leaves_routine_and_reasoning_unchanged(tmp_path: Path) -> None:
    current = chart.collect()
    remapped = chart.collect(_remapped_research(tmp_path), chart.POLICY_PATH)
    assert remapped.lines("routine") == current.lines("routine")
    assert remapped.lines("reasoning") == current.lines("reasoning")
    assert remapped.lines("research") != current.lines("research")
    svg = chart.render_svg(remapped)
    assert "Routine candidates" in svg and "Research candidates" in svg
