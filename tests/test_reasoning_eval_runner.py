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


def _row(charge: int | None, *, usage: bool = True, status: str = "completed") -> dict[str, Any]:
    return {
        "status": status,
        "calls": [
            {
                "number": 1,
                "status": "completed",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50} if usage else {},
                "reasoning_tokens": 20,
                "account_charge_microusd": charge,
                "reservation_bound_microusd": 5_000,
                "provider_cost_usd": None,
            }
        ],
        "final_response": "An answer." if status == "completed" else None,
        "tool_steps": [],
        "tool_call_count": 0,
        "task_failures": [],
        "stop": {"category": "transport"} if status == "stopped" else None,
        "latency_seconds": 1.5,
    }


def _document(
    plan: tuple[Any, ...],
    charges: dict[str, int | None] | None = None,
    *,
    include: Any = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A results document for the calibration schedule, with recorded rows chosen by
    ``include`` (all by default) and individual rows replaced by ``overrides``."""
    manifest, fixtures, schedule, profile = plan
    attempts: dict[str, Any] = {}
    for attempt in schedule:
        if include is not None and not include(attempt):
            continue
        attempts[attempt.attempt_id] = _row((charges or {}).get(attempt.configuration, 1_000))
    attempts.update(overrides or {})
    state = {"identity": {"fixtures_sha256": fixtures.sha256}, "attempts": attempts}
    return runner.results_document(state, fixtures, schedule, profile, manifest)


def _summary(plan: tuple[Any, ...], document: dict[str, Any]) -> dict[str, Any]:
    return runner.summarize(document, plan[1], plan[2])


def test_fully_covered_calibration_projects_pilot_spend(plan) -> None:
    summary = _summary(plan, _document(plan))
    row = summary["configurations"]["luna-low"]
    assert (row["planned_attempts"], row["recorded_attempts"], row["missing_attempts"]) == (
        20,
        20,
        0,
    )
    assert row["account_charge_microusd_mean_per_attempt"] == 1_000
    assert row["reasoning_tokens"]["mean"] == 20
    assert row["f_unknown_usage"] == 0.0 and row["f_full_hold"] == 0.0
    projection = summary["pilot_projection"]
    assert projection["complete"] is True and projection["incomplete_reasons"] == []
    assert projection["cases"] == 40 and projection["repeats"] == 3
    assert projection["at_mean_microusd"] == 1_000 * 40 * 3 * 5


def _assert_withheld(summary: dict[str, Any]) -> None:
    projection = summary["pilot_projection"]
    assert projection["complete"] is False
    assert projection["incomplete_reasons"]
    assert projection["at_mean_microusd"] is None and projection["at_max_microusd"] is None


def test_empty_interrupted_report_never_projects_zero(plan) -> None:
    summary = _summary(plan, _document(plan, include=lambda attempt: False))
    _assert_withheld(summary)
    assert set(summary["configurations"]) == set(CONFIGURATIONS)
    assert all(row["missing_attempts"] == 20 for row in summary["configurations"].values())
    assert summary["account_charge_microusd_total"] is None


def test_one_recorded_configuration_does_not_complete_the_projection(plan) -> None:
    summary = _summary(plan, _document(plan, include=lambda a: a.configuration == "luna-low"))
    _assert_withheld(summary)
    assert summary["configurations"]["luna-low"]["pilot_projection_microusd"] is not None
    assert summary["configurations"]["sol-6.1-high"]["recorded_attempts"] == 0


def test_missing_dev_cases_withhold_the_projection(plan) -> None:
    summary = _summary(plan, _document(plan, include=lambda a: a.case_id != "SYN-2"))
    _assert_withheld(summary)
    row = summary["configurations"]["sonnet-5-high"]
    assert row["missing_attempt_ids"] == ["SYN-2-sonnet-5-high-r1"]
    assert row["pilot_projection_microusd"] is None


def test_stopped_attempt_withholds_the_projection(plan) -> None:
    stopped = {"EVD-1-luna-low-r1": _row(1_000, status="stopped")}
    summary = _summary(plan, _document(plan, overrides=stopped))
    _assert_withheld(summary)
    assert summary["configurations"]["luna-low"]["stopped_attempts"] == 1


def test_unknown_charge_withholds_the_projection(plan) -> None:
    summary = _summary(plan, _document(plan, {"sol-6.1-high": None}))
    _assert_withheld(summary)
    row = summary["configurations"]["sol-6.1-high"]
    assert row["f_unknown_charge"] == 1.0 and row["attempts_with_unknown_charge"] == 20
    assert row["f_unknown_usage"] == 0.0
    assert row["pilot_projection_microusd"] is None


def test_failed_attempt_settled_at_full_hold_is_measured_separately(plan) -> None:
    failed = {"COD-2-luna-high-r1": _row(5_000, usage=False, status="failed")}
    summary = _summary(plan, _document(plan, overrides=failed))
    row = summary["configurations"]["luna-high"]
    # Unknown provider usage settled at the full hold: the charge is known, so the
    # projection can include it, but the unknown-usage risk is reported on its own.
    assert row["calls_without_provider_usage"] == 1 and row["f_unknown_usage"] == 0.05
    assert row["calls_settled_at_full_hold"] == 1 and row["f_unknown_charge"] == 0.0
    assert row["account_charge_microusd_max_per_attempt"] == 5_000
    assert summary["pilot_projection"]["complete"] is True


def test_review_packet_is_blinded_and_mapped_separately(plan) -> None:
    document = _document(plan, {})
    packet, verdicts, mapping = runner.review_packet(document, plan[1])
    for hidden in ("luna", "sonnet", "sol-6", "effort", "microusd"):
        assert hidden not in packet.lower()
    assert len(mapping) == len(verdicts) == 100
    assert set(mapping) == {item["label"] for item in verdicts}
    assert {item["verdict"] for item in verdicts} == {"pending"}
    assert all(item["reviewer"] is None for item in verdicts)


def test_r2_manifest_differs_from_the_stopped_run_only_in_identity() -> None:
    first = json.loads(MANIFEST.read_text(encoding="utf-8"))
    second = json.loads(MANIFEST.with_name("b2_calibration_20261002_r2.json").read_text("utf-8"))
    assert second["experiment"] == "b2-calibration-20261002-r2"
    for key in ("period", "corpus", "splits", "repeats", "candidates", "configurations", "bounds"):
        assert second[key] == first[key]


def test_every_repository_migration_must_be_applied() -> None:
    names = sorted(path.name for path in runner.MIGRATIONS_DIR.glob("*.sql"))
    assert names and runner.pending_migrations(names) == []
    assert runner.pending_migrations(names[:-1]) == [names[-1]]
    assert not any(name.startswith("rollback") for name in names)


class _Conn:
    def __init__(self, tracked: bool, applied: list[str]) -> None:
        self.tracked, self.applied = tracked, applied

    async def fetchval(self, _query: str) -> bool:
        return self.tracked

    async def fetch(self, _query: str) -> list[dict[str, str]]:
        return [{"filename": name} for name in self.applied]


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> Any:
        conn = self.conn

        class _Context:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *_: object) -> None:
                return None

        return _Context()


@pytest.mark.asyncio
async def test_schema_behind_the_code_is_refused_before_any_dispatch() -> None:
    names = sorted(path.name for path in runner.MIGRATIONS_DIR.glob("*.sql"))
    await runner.require_current_schema(_Pool(_Conn(True, names)))
    with pytest.raises(runner.EvalError, match="behind the code: 1 unapplied"):
        await runner.require_current_schema(_Pool(_Conn(True, names[:-1])))
    with pytest.raises(runner.EvalError, match="no migration record"):
        await runner.require_current_schema(_Pool(_Conn(False, [])))
