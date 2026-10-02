"""Tests for the B2 reasoning evaluation runner (no database, no provider calls)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.entitlements.policy import load_inference_policy
from scripts import model_routing_followup as follow
from scripts import reasoning_eval as runner

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "reasoning_eval" / "b2_calibration_20261002.json"
POLICY = ROOT / "config" / "inference_policy.production.json"
CONFIGURATIONS = ("luna-low", "luna-medium", "luna-high", "sonnet-5-high", "sol-6.1-high")


@pytest.fixture(scope="module")
def plan() -> tuple[Any, ...]:
    return runner.load_plan(MANIFEST)


def _manifest_variant(tmp_path: Path, **changes: Any) -> Path:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw.update(changes)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _routes(manifest: runner.Manifest) -> dict[str, Any]:
    inference = load_inference_policy(POLICY)
    return {c["label"]: inference.routes[c["route_id"]] for c in manifest.candidates}


def test_calibration_schedule_covers_every_dev_case_once_per_configuration(plan) -> None:
    manifest, fixtures, schedule, profile = plan
    dev = [case for case in fixtures.cases if case.stage == "dev"]
    assert len(dev) == 20
    assert len(schedule) == 100
    pairs = {(a.case_id, a.configuration) for a in schedule}
    assert pairs == {(case.case_id, label) for case in dev for label in CONFIGURATIONS}
    assert {a.effort for a in schedule if a.configuration == "luna-medium"} == {"medium"}
    assert profile.experiment == "reasoning-eval/b2-calibration-20261002"
    assert profile.incremental_cap_microusd == 4_650_000
    # Two tool cases use up to three calls; every other case answers in one.
    assert profile.dispatch_bound == (18 * 1 + 2 * 3) * 5


def test_configuration_order_rotates_between_cases(plan) -> None:
    _, _, schedule, _ = plan
    firsts = [schedule[index].configuration for index in range(0, 25, 5)]
    assert firsts == list(CONFIGURATIONS)


def test_tool_cases_get_tool_rounds_and_plain_cases_one_call(plan) -> None:
    _, fixtures, _, _ = plan
    cases = fixtures.by_id()
    assert (cases["RBT-1"].max_calls, cases["RBT-1"].max_tool_calls) == (3, 4)
    assert (cases["EVD-1"].max_calls, cases["EVD-1"].max_tool_calls) == (1, 0)


def test_multi_turn_history_is_sent_before_the_prompt(plan) -> None:
    manifest, fixtures, schedule, _ = plan
    case = fixtures.by_id()["FUP-2"]
    assert case.history
    attempt = next(a for a in schedule if a.case_id == "FUP-2" and a.configuration == "luna-high")
    params = runner.first_request(case, _routes(manifest)["luna"], attempt)
    assert params["messages"][:-1] == [dict(turn) for turn in case.history]
    assert params["messages"][-1] == {"role": "user", "content": case.prompt}
    assert params["reasoning_effort"] == "high"


def test_followup_fixtures_keep_a_single_opening_message() -> None:
    fixtures = follow.load_fixtures(follow.DEFAULT_FIXTURES_PATH)
    case = fixtures.cases[0]
    assert case.history == ()
    assert follow.initial_messages(case) == [{"role": "user", "content": case.prompt}]


def test_planning_bound_fits_the_approved_cap(plan) -> None:
    manifest, fixtures, schedule, _ = plan
    routes = _routes(manifest)
    totals = runner.planning_bound(schedule, fixtures.by_id(), routes)
    assert set(totals) == set(CONFIGURATIONS)
    assert sum(totals.values()) <= manifest.incremental_cap_microusd
    tool_attempt = next(a for a in schedule if a.case_id == "RBT-1")
    route = routes[tool_attempt.candidate_label]
    single = runner.attempt_bound(fixtures.by_id()["RBT-1"], route, tool_attempt)
    assert single > 2 * follow.ceiling_bound(route)


def test_corpus_digest_drift_is_refused(tmp_path: Path, plan) -> None:
    manifest = plan[0]
    altered = tmp_path / "corpus.json"
    altered.write_bytes(manifest.corpus_path.read_bytes() + b"\n")
    with pytest.raises(runner.EvalError, match="digest drift"):
        runner.load_corpus(altered, manifest.corpus_sha256)


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": True},
        {"splits": ["dev", "dev"]},
        {"repeats": 0},
        {"bounds": {"incremental_cap_microusd": 25_000_001}},
        {"configurations": [{"label": "x", "candidate": "missing", "effort": "low"}]},
        {"period": "2026-13"},
    ],
)
def test_invalid_manifests_are_refused(tmp_path: Path, changes: dict[str, Any]) -> None:
    with pytest.raises((runner.EvalError, ValueError)):
        runner.load_manifest(_manifest_variant(tmp_path, **changes))


def _document(plan: tuple[Any, ...], charges: dict[str, int | None]) -> dict[str, Any]:
    manifest, fixtures, schedule, profile = plan
    attempts: dict[str, Any] = {}
    for attempt in schedule:
        charge = charges.get(attempt.configuration, 1_000)
        attempts[attempt.attempt_id] = {
            "status": "completed",
            "calls": [
                {
                    "number": 1,
                    "status": "completed",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                    "reasoning_tokens": 20,
                    "account_charge_microusd": charge,
                    "reservation_bound_microusd": 5_000,
                    "provider_cost_usd": None,
                }
            ],
            "final_response": "An answer.",
            "tool_steps": [],
            "tool_call_count": 0,
            "task_failures": [],
            "latency_seconds": 1.5,
        }
    state = {"identity": {"fixtures_sha256": fixtures.sha256}, "attempts": attempts}
    return runner.results_document(state, fixtures, schedule, profile, manifest)


def test_summary_projects_pilot_spend_from_measured_charges(plan) -> None:
    document = _document(plan, {})
    summary = runner.summarize(document, plan[1])
    row = summary["configurations"]["luna-low"]
    assert row["attempts"] == 20
    assert row["account_charge_microusd_mean_per_attempt"] == 1_000
    assert row["reasoning_tokens"]["mean"] == 20
    projection = summary["pilot_projection"]
    assert projection["cases"] == 40 and projection["repeats"] == 3
    assert projection["at_mean_microusd"] == 1_000 * 40 * 3 * 5


def test_unknown_charge_withholds_the_projection(plan) -> None:
    summary = runner.summarize(_document(plan, {"sol-6.1-high": None}), plan[1])
    assert summary["configurations"]["sol-6.1-high"]["f_unknown"] == 1.0
    assert summary["configurations"]["sol-6.1-high"]["pilot_projection_microusd"] is None
    assert summary["pilot_projection"]["complete"] is False
    assert summary["pilot_projection"]["at_mean_microusd"] is None


def test_review_packet_is_blinded_and_mapped_separately(plan) -> None:
    document = _document(plan, {})
    packet, verdicts, mapping = runner.review_packet(document, plan[1])
    for hidden in ("luna", "sonnet", "sol-6", "effort", "microusd"):
        assert hidden not in packet.lower()
    assert len(mapping) == len(verdicts) == 100
    assert set(mapping) == {item["label"] for item in verdicts}
    assert {item["verdict"] for item in verdicts} == {"pending"}
    assert all(item["reviewer"] is None for item in verdicts)
