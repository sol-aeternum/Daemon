"""No-network contract tests for the model-upgrade spec layer.

Every test is offline and monkeypatch-free: the layer under test is pure, so the
tests exercise real manifests against the real follow-up corpus file and plain
dict documents. Nothing here contacts a provider, a database or the network.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from scripts import model_routing_followup as follow
from scripts import model_upgrade_spec as upgrade

REPO_ROOT = follow.REPO_ROOT
FIXTURES_REL = "tests/fixtures/model_routing_followup.json"

HIGH_CASE_IDS = tuple(follow.DIAGNOSTIC_CASE_IDS) + tuple(follow.HELD_OUT_CASE_IDS)


def manifest_document(fixtures_sha256: str) -> dict[str, Any]:
    """The approved plan shape: two candidates, high+xhigh settings, four probes."""
    return {
        "schema_version": 1,
        "experiment": "model-sol61-upgrade",
        "period": "2026-09",
        "fixtures": {"path": FIXTURES_REL, "sha256": fixtures_sha256},
        "candidates": [
            {
                "label": "incumbent",
                "model": "openrouter/openai/gpt-6-sol",
                "route_id": "route-incumbent",
                "provider_pin": "azure/eu",
            },
            {
                "label": "challenger",
                "model": "openrouter/openai/gpt-6.1-sol",
                "route_id": "route-challenger",
                "provider_pin": "azure/eu",
            },
        ],
        "conditions": [
            {"label": "high", "effort": "high", "case_ids": list(HIGH_CASE_IDS), "repeats": 2},
            {
                "label": "xhigh",
                "effort": "xhigh",
                "case_ids": list(follow.HELD_OUT_CASE_IDS),
                "repeats": 2,
            },
        ],
        "bounds": {
            "max_context_tokens": 16_000,
            "max_output_tokens": 4096,
            "max_calls_per_attempt": 3,
            "attempt_deadline_seconds": 90,
            "incremental_cap_microusd": 22_000_000,
            "aggregate_cap_microusd": 25_000_000,
        },
        "probes": [
            {"candidate_label": "challenger", "effort": "high", "case_id": "H02", "max_calls": 2},
            {"candidate_label": "challenger", "effort": "high", "case_id": "H05", "max_calls": 2},
            {"candidate_label": "incumbent", "effort": "high", "case_id": "H06", "max_calls": 2},
            {"candidate_label": "incumbent", "effort": "high", "case_id": "H07", "max_calls": 2},
        ],
        "screening": {
            "regression_case_ids": list(follow.HELD_OUT_CASE_IDS),
            "minimum_acceptable_answers": 15,
            "hard_violations_allowed": 0,
            "wrong_identifier_fetches_allowed": 0,
            "latency_screens_seconds": {
                "utility": 10,
                "orchestration": 30,
                "synthesis": 60,
            },
        },
    }


@pytest.fixture(scope="module")
def corpus() -> follow.FixtureSet:
    return follow.load_fixtures(REPO_ROOT / FIXTURES_REL)


def write_manifest(directory: Path, document: dict[str, Any]) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def load_from(
    directory: Path, document: dict[str, Any], corpus: follow.FixtureSet
) -> upgrade.UpgradeSpec:
    return upgrade.load_spec(write_manifest(directory, document), REPO_ROOT, fixtures=corpus)


# ---------------------------------------------------------------------------
# Invalid manifests are refused, and the schema leaves no room for approvals
# ---------------------------------------------------------------------------

INVALID_CASES = (
    ("missing_key", lambda d: d.pop("screening"), "missing required key"),
    ("unknown_key_carries_no_approvals", lambda d: d.update({"approvals": {}}), "unknown key"),
    ("unknown_key_extra_field", lambda d: d.update({"total_attempts": 92}), "unknown key"),
    ("schema_version_2", lambda d: d.update({"schema_version": 2}), "schema_version"),
    ("schema_version_bool", lambda d: d.update({"schema_version": True}), "expected an integer"),
    ("experiment_not_slug", lambda d: d.update({"experiment": "Sol 6.1"}), "safe slug"),
    (
        "experiment_is_historical",
        lambda d: d.update({"experiment": "model-routing-followup"}),
        "historical",
    ),
    ("period_short", lambda d: d.update({"period": "2026-9"}), "YYYY-MM"),
    ("period_invalid_month", lambda d: d.update({"period": "2026-13"}), "YYYY-MM"),
    (
        "fixtures_absolute_path",
        lambda d: d["fixtures"].update({"path": "/etc/passwd"}),
        "repo-relative",
    ),
    (
        "fixtures_traversal",
        lambda d: d["fixtures"].update({"path": "../secrets.json"}),
        "traverse",
    ),
    ("fixtures_sha_not_hex", lambda d: d["fixtures"].update({"sha256": "zz"}), "SHA-256"),
    (
        "fixtures_sha_mismatch",
        lambda d: d["fixtures"].update({"sha256": "a" * 64}),
        "corpus digest",
    ),
    (
        "duplicate_candidate_label",
        lambda d: d["candidates"].append(dict(d["candidates"][0])),
        "duplicate candidate label",
    ),
    (
        "duplicate_candidate_model",
        lambda d: d["candidates"][1].update({"model": d["candidates"][0]["model"]}),
        "duplicate candidate model",
    ),
    (
        "duplicate_candidate_route",
        lambda d: d["candidates"][1].update({"route_id": d["candidates"][0]["route_id"]}),
        "duplicate candidate route id",
    ),
    (
        "candidate_latest_alias",
        lambda d: d["candidates"][0].update({"model": "openrouter/openai/gpt-6-sol/latest"}),
        "/latest",
    ),
    (
        "candidate_not_openrouter",
        lambda d: d["candidates"][0].update({"model": "anthropic/claude-sonnet-5"}),
        "openrouter/",
    ),
    (
        "duplicate_condition_label",
        lambda d: d["conditions"].append(dict(d["conditions"][0])),
        "duplicate condition label",
    ),
    (
        "condition_reserved_label",
        lambda d: d["conditions"][0].update({"label": "probe"}),
        "reserved",
    ),
    (
        "condition_no_cases",
        lambda d: d["conditions"][0].update({"case_ids": []}),
        "at least one case id",
    ),
    (
        "condition_duplicate_case",
        lambda d: d["conditions"][0].update({"case_ids": ["D01", "D01"]}),
        "duplicate case id",
    ),
    (
        "condition_unknown_case",
        lambda d: d["conditions"][0].update({"case_ids": ["X99"]}),
        "unknown case id",
    ),
    ("condition_repeats_zero", lambda d: d["conditions"][0].update({"repeats": 0}), ">= 1"),
    (
        "condition_repeats_bool",
        lambda d: d["conditions"][0].update({"repeats": True}),
        "expected an integer",
    ),
    (
        "condition_effort_not_slug",
        lambda d: d["conditions"][0].update({"effort": "high medium"}),
        "safe slug",
    ),
    (
        "bounds_context_widened",
        lambda d: d["bounds"].update({"max_context_tokens": 32_000}),
        "must equal",
    ),
    (
        "bounds_deadline_lengthened",
        lambda d: d["bounds"].update({"attempt_deadline_seconds": 120}),
        "must equal",
    ),
    (
        "bounds_calls_widened",
        lambda d: d["bounds"].update({"max_calls_per_attempt": 4}),
        "must equal",
    ),
    (
        "bounds_incremental_zero",
        lambda d: d["bounds"].update({"incremental_cap_microusd": 0}),
        ">= 1",
    ),
    (
        "bounds_incremental_over_aggregate",
        lambda d: d["bounds"].update({"incremental_cap_microusd": 26_000_000}),
        "aggregate ceiling",
    ),
    (
        "bounds_aggregate_changed",
        lambda d: d["bounds"].update({"aggregate_cap_microusd": 24_999_999}),
        "must equal",
    ),
    (
        "bounds_incremental_bool",
        lambda d: d["bounds"].update({"incremental_cap_microusd": False}),
        "expected an integer",
    ),
    (
        "probe_unknown_candidate",
        lambda d: d["probes"][0].update({"candidate_label": "ghost"}),
        "unknown candidate label",
    ),
    (
        "probe_duplicate",
        lambda d: d["probes"].append(dict(d["probes"][0])),
        "duplicate probe",
    ),
    (
        "probe_two_tool_case",
        lambda d: d["probes"][0].update({"case_id": "H01"}),
        "at most 1 tool",
    ),
    (
        "probe_two_tool_budget_case",
        lambda d: d["probes"][0].update({"case_id": "D01"}),
        "at most 1 tool",
    ),
    (
        "probe_three_calls",
        lambda d: d["probes"][0].update({"max_calls": 3}),
        "exactly 2 model calls",
    ),
    (
        "screening_regression_subset",
        lambda d: d["screening"].update({"regression_case_ids": ["H01", "H02"]}),
        "held-out set",
    ),
    (
        "screening_hard_violations",
        lambda d: d["screening"].update({"hard_violations_allowed": 1}),
        "must equal",
    ),
    (
        "screening_wrong_fetches",
        lambda d: d["screening"].update({"wrong_identifier_fetches_allowed": 2}),
        "must equal",
    ),
    (
        "screening_loose_latency",
        lambda d: d["screening"]["latency_screens_seconds"].update({"utility": 20}),
        "must equal",
    ),
    (
        "screening_unknown_latency_class",
        lambda d: d["screening"]["latency_screens_seconds"].update({"batch": 10}),
        "unknown key",
    ),
    (
        "screening_minimum_lowered",
        lambda d: d["screening"].update({"minimum_acceptable_answers": 14}),
        "must equal",
    ),
)


@pytest.mark.parametrize(
    ("name", "mutate", "fragment"),
    INVALID_CASES,
    ids=[name for name, _mutate, _fragment in INVALID_CASES],
)
def test_invalid_manifests_are_refused(
    name: str, mutate: Any, fragment: str, tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    document = manifest_document(corpus.sha256)
    mutate(document)
    with pytest.raises(upgrade.SpecError, match=fragment):
        load_from(tmp_path, document, corpus)


def test_load_spec_self_loads_the_declared_corpus_and_freezes_identity(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    path = write_manifest(tmp_path, manifest_document(corpus.sha256))
    spec = upgrade.load_spec(path, REPO_ROOT)
    assert spec.manifest_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert spec.experiment == "model-sol61-upgrade"
    assert spec.period == "2026-09"
    assert spec.schema_version == upgrade.SCHEMA_VERSION == 1
    assert spec.fixtures.path == FIXTURES_REL
    assert spec.fixtures.sha256 == corpus.sha256
    assert spec.candidate_labels == ("incumbent", "challenger")
    assert spec.condition_labels == ("high", "xhigh")
    assert spec.bounds.max_context_tokens == follow.MAX_CONTEXT_TOKENS == 16_000
    assert spec.bounds.max_output_tokens == follow.MAX_OUTPUT_TOKENS == 4096
    assert spec.bounds.max_calls_per_attempt == follow.MAX_CALLS_PER_ATTEMPT == 3
    assert spec.bounds.attempt_deadline_seconds == follow.ATTEMPT_DEADLINE_S == 90.0
    assert spec.bounds.aggregate_cap_microusd == follow.live.CAP_MICROUSD == 25_000_000
    assert spec.bounds.incremental_cap_microusd == 22_000_000
    assert spec.screening.regression_case_ids == follow.HELD_OUT_CASE_IDS
    assert spec.screening.latency_screen_seconds("utility") == 10.0
    with pytest.raises(upgrade.SpecError, match="no screen declared"):
        spec.screening.latency_screen_seconds("batch")
    with pytest.raises(AttributeError):
        spec.experiment = "other"  # type: ignore[misc]


def test_manifest_sha_and_schedule_digest_are_deterministic(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    document = manifest_document(corpus.sha256)
    first = load_from(tmp_path, document, corpus)
    again = tmp_path / "again"
    again.mkdir()
    second = load_from(again, document, corpus)
    assert first == second
    schedule = upgrade.build_schedule(first, corpus)
    assert upgrade.schedule_sha256(schedule) == upgrade.schedule_sha256(
        upgrade.build_schedule(second, corpus)
    )
    payload = upgrade.schedule_payload(schedule)
    assert payload[0] == {
        "attempt_id": schedule[0].attempt_id,
        "case_id": schedule[0].case_id,
        "stage": schedule[0].stage,
        "phase": schedule[0].phase,
        "candidate_label": schedule[0].candidate_label,
        "condition_label": schedule[0].condition_label,
        "effort": schedule[0].effort,
        "repeat": schedule[0].repeat,
        "max_calls": schedule[0].max_calls,
        "max_tool_calls": schedule[0].max_tool_calls,
        "latency_class": schedule[0].latency_class,
        "is_probe": False,
    }


def test_build_schedule_refuses_a_corpus_with_a_different_digest(
    corpus: follow.FixtureSet,
) -> None:
    spec = upgrade.UpgradeSpec(
        schema_version=1,
        experiment="model-sol61-upgrade",
        period="2026-09",
        manifest_sha256="0" * 64,
        fixtures=upgrade.FixtureRef(path=FIXTURES_REL, sha256="b" * 64),
        candidates=(
            upgrade.CandidateSpec(
                "incumbent", "openrouter/openai/gpt-6-sol", "route-a", "azure/eu"
            ),
            upgrade.CandidateSpec(
                "challenger", "openrouter/openai/gpt-6.1-sol", "route-b", "azure/eu"
            ),
        ),
        conditions=(upgrade.ConditionSpec("high", "high", ("D01",), 1),),
        bounds=upgrade.BoundsSpec(16_000, 4096, 3, 90.0, 22_000_000, 25_000_000),
        probes=(),
        screening=upgrade.ScreeningSpec(
            follow.HELD_OUT_CASE_IDS, 15, 0, 0, upgrade.LATENCY_SCREENS_SECONDS
        ),
    )
    with pytest.raises(upgrade.SpecError, match="does not match"):
        upgrade.build_schedule(spec, corpus)


# ---------------------------------------------------------------------------
# Settings are measured settings, not internal budgets
# ---------------------------------------------------------------------------


def test_conditions_carry_effort_verbatim_as_measured_settings(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    assert spec.condition("high").effort == "high"
    assert spec.condition("xhigh").effort == "xhigh"
    schedule = upgrade.build_schedule(spec, corpus)
    by_condition = {condition.label: condition for condition in spec.conditions}
    for attempt in schedule:
        expected = "high" if attempt.is_probe else by_condition[attempt.condition_label].effort
        assert attempt.effort == expected
        assert isinstance(attempt.effort, str)  # a setting, never a number
    # The layer assigns no numeric budget meaning to any setting: per-attempt
    # call ceilings are the executor's pinned constants for every attempt alike.
    assert all(
        attempt.max_calls == spec.bounds.max_calls_per_attempt
        for attempt in schedule
        if not attempt.is_probe
    )


# ---------------------------------------------------------------------------
# Schedule shape: paired groups, phase order, probes last, derived totals
# ---------------------------------------------------------------------------


def test_schedule_matches_the_approved_plan_shape(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    schedule = upgrade.build_schedule(spec, corpus)
    assert spec.total_attempts == 92
    assert spec.dispatch_bound == 272
    assert len(schedule) == 92
    assert len({attempt.attempt_id for attempt in schedule}) == 92
    assert Counter(attempt.phase for attempt in schedule) == {
        "diagnostic": 24,
        "regression": 64,
        "streaming": 4,
    }
    assert all(
        attempt.stage == "diagnostic" for attempt in schedule if attempt.phase == "diagnostic"
    )
    assert all(attempt.stage == "heldout" for attempt in schedule if attempt.phase == "regression")
    phases = [attempt.phase for attempt in schedule]
    assert phases.index("regression") < phases.index("streaming")
    # The regression phase runs every declared high attempt before any xhigh.
    settings = [attempt.condition_label for attempt in schedule if attempt.phase == "regression"]
    assert settings == ["high"] * 32 + ["xhigh"] * 32
    # Every probe is last, is its own streaming stage, and keeps the narrow
    # two-call envelope with a one-tool budget.
    probes = schedule[-len(spec.probes) :]
    assert all(attempt.is_probe for attempt in probes)
    assert all(attempt.phase == attempt.stage == "streaming" for attempt in probes)
    assert all(attempt.repeat == 0 for attempt in probes)
    assert all(attempt.max_calls == 2 for attempt in probes)
    assert all(attempt.condition_label == upgrade.PROBE_CONDITION_LABEL for attempt in probes)
    assert all(attempt.max_tool_calls <= 1 for attempt in probes)
    assert all(not attempt.is_probe for attempt in schedule[: -len(spec.probes)])
    # Attempt ids are dot-separated and include the setting label.
    sample = next(attempt for attempt in schedule if not attempt.is_probe)
    assert sample.attempt_id == (
        f"{sample.case_id}.{sample.candidate_label}.{sample.condition_label}.r{sample.repeat}"
    )


def test_schedule_alternates_candidates_inside_matched_groups(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    schedule = upgrade.build_schedule(spec, corpus)
    groups: dict[tuple[str, int, str], list[upgrade.PlannedAttempt]] = {}
    for attempt in schedule:
        if attempt.is_probe:
            continue
        groups.setdefault((attempt.case_id, attempt.repeat, attempt.condition_label), []).append(
            attempt
        )
    assert len(groups) == 14 * 2 + 8 * 2
    for _key, members in groups.items():
        # The whole group is contiguous in the schedule and covers every
        # candidate exactly once, so matched pairs are adjacent.
        positions = [schedule.index(attempt) for attempt in members]
        assert positions == list(range(min(positions), min(positions) + len(members)))
        assert [attempt.candidate_label for attempt in members] == list(spec.candidate_labels)


def test_derived_totals_come_from_the_manifest_and_probes_count_two_calls(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    document = manifest_document(corpus.sha256)
    spec = load_from(tmp_path, document, corpus)
    # 88 regular attempts (14x2x2 + 8x2x2) plus 4 probes; probes contribute two
    # calls each, never the regular three.
    assert spec.total_attempts == 88 + 4
    assert spec.dispatch_bound == 88 * 3 + 4 * 2
    assert "total_attempts" not in document and "dispatch_bound" not in document
    narrowed = copy.deepcopy(document)
    narrowed["conditions"][0]["repeats"] = 1
    narrowed_dir = tmp_path / "narrowed"
    narrowed_dir.mkdir()
    smaller = load_from(narrowed_dir, narrowed, corpus)
    assert smaller.total_attempts == (14 * 1 + 8 * 2) * 2 + 4 == 64
    assert smaller.dispatch_bound == (14 * 1 + 8 * 2) * 2 * 3 + 4 * 2 == 188


# ---------------------------------------------------------------------------
# Result summary: unknown costs stay None, failures count, no acceptance
# ---------------------------------------------------------------------------


def _row(attempt_id: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "attempt_id": attempt_id,
        "case_id": "D01",
        "stage": "diagnostic",
        "latency_class": "orchestration",
        "candidate_label": "incumbent",
        "condition": "high",
        "repeat": 1,
        "status": "completed",
        "latency_seconds": 5.0,
        "cost_usd": 0.01,
        "account_charge_microusd": 100,
        "calls_with_unknown_charge": 0,
        "tool_steps": [],
        "semantic_verdict": "pending",
        "reviewer": None,
    }
    row.update(overrides)
    return row


def _summary_document(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"artifact_version": "x", "attempts": rows}


def test_unknown_costs_stay_none_and_failures_keep_contributing(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    rows = [
        _row("D01.incumbent.high.r1", latency_seconds=5.0, cost_usd=0.01),
        _row(
            "D01.challenger.high.r1",
            candidate_label="challenger",
            status="failed",
            latency_seconds=7.0,
            cost_usd=0.02,
        ),
        _row(
            "D02.incumbent.high.r1",
            case_id="D02",
            status="stopped",
            cost_usd=None,
            account_charge_microusd=None,
            calls_with_unknown_charge=1,
        ),
        _row(
            "D02.challenger.high.r1",
            candidate_label="challenger",
            case_id="D02",
            latency_seconds=None,
        ),
    ]
    summary = upgrade.result_summary(_summary_document(rows), spec)
    costs = summary["cost_usd"]
    assert costs["total"] is None  # an unknown cost is never scored as zero
    assert costs["attempts_with_unknown_cost"] == 1
    assert costs["cost_per_acceptable"] is None
    charges = summary["account_charge_microusd"]
    assert charges["total"] is None
    assert charges["attempts_with_unknown_charge"] == 1
    assert charges["calls_with_unknown_charge"] == 1
    counts = summary["counts"]
    assert counts["status"] == {"completed": 2, "failed": 1, "stopped": 1}
    assert counts["semantic_verdict"] == {"pending": 4}
    assert counts["human_reviewed"] == 0
    assert counts["pending_or_unreviewed"] == 4
    # Failed attempts keep their latency and costs in the observed statistics.
    assert summary["latency_seconds"]["all"] == {"n": 3, "p95": 7.0, "max": 7.0}
    assert summary["latency_seconds"]["by_stage"]["diagnostic"]["n"] == 3
    deltas = {
        (entry["case_id"], entry["candidates"][0], entry["candidates"][1]): entry["delta_seconds"]
        for entry in summary["paired_deltas_seconds"]
    }
    assert deltas[("D01", "incumbent", "challenger")] == pytest.approx(2.0)
    assert ("D02", "incumbent", "challenger") not in deltas  # no measured latency, no pair


def test_nearest_rank_p95_and_subgroups_follow_the_definition(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    rows = [
        _row(
            f"D01.incumbent.high.r{repeat}",
            repeat=repeat,
            latency_seconds=latency,
            cost_usd=0.01,
        )
        for repeat, latency in ((1, 1.0), (2, 2.0))
    ] + [
        _row(
            f"D01.challenger.high.r{repeat}",
            candidate_label="challenger",
            repeat=repeat,
            latency_seconds=latency,
            cost_usd=0.01,
        )
        for repeat, latency in ((1, 3.0), (2, 4.0), (3, 5.0), (4, 6.0))
    ]
    summary = upgrade.result_summary(_summary_document(rows), spec)
    # Nearest-rank p95 of [1, 2, 3, 4, 5, 6]: rank ceil(0.95 * 6) = 6 -> 6.0.
    assert summary["latency_seconds"]["all"] == {"n": 6, "p95": 6.0, "max": 6.0}
    by_candidate = summary["latency_seconds"]["by_candidate"]
    assert by_candidate["incumbent"] == {"n": 2, "p95": 2.0, "max": 2.0}
    assert by_candidate["challenger"] == {"n": 4, "p95": 6.0, "max": 6.0}
    deltas = summary["paired_deltas_seconds"]
    assert [(entry["candidates"], entry["delta_seconds"]) for entry in deltas] == [
        (["incumbent", "challenger"], 2.0),
        (["incumbent", "challenger"], 2.0),
    ]


def test_cost_per_acceptable_requires_validated_human_verdicts(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    pending = upgrade.result_summary(
        _summary_document(
            [
                _row("D01.incumbent.high.r1", latency_seconds=1.0, cost_usd=0.01),
                _row(
                    "D01.challenger.high.r1",
                    candidate_label="challenger",
                    latency_seconds=1.0,
                    cost_usd=0.03,
                ),
            ]
        ),
        spec,
    )
    assert pending["cost_usd"]["cost_per_acceptable"] is None
    assert pending["counts"]["semantic_verdict"] == {"pending": 2}
    summary = upgrade.result_summary(
        _summary_document(
            [
                _row(
                    "D01.incumbent.high.r1",
                    latency_seconds=1.0,
                    cost_usd=0.01,
                    semantic_verdict="acceptable",
                    reviewer="operator",
                ),
                _row(
                    "D01.challenger.high.r1",
                    candidate_label="challenger",
                    latency_seconds=1.0,
                    cost_usd=0.03,
                    semantic_verdict="acceptable",
                    reviewer="operator",
                ),
            ]
        ),
        spec,
    )
    assert summary["cost_usd"]["cost_per_acceptable"] == pytest.approx(0.02)
    assert summary["counts"]["human_reviewed"] == 2
    # A verdict without a named human reviewer is not validated evidence.
    unattributed = upgrade.result_summary(
        _summary_document(
            [
                _row(
                    "D01.incumbent.high.r1",
                    latency_seconds=1.0,
                    cost_usd=0.01,
                    semantic_verdict="acceptable",
                    reviewer=None,
                ),
                _row(
                    "D01.challenger.high.r1",
                    candidate_label="challenger",
                    latency_seconds=1.0,
                    cost_usd=0.03,
                    semantic_verdict="acceptable",
                    reviewer="operator",
                ),
            ]
        ),
        spec,
    )
    assert unattributed["cost_usd"]["cost_per_acceptable"] is None


def test_summary_and_packet_are_pure_and_never_assign_acceptance(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    rows = [
        _row("D01.incumbent.high.r1", latency_seconds=1.0, cost_usd=0.01),
        _row(
            "D01.challenger.high.r1",
            candidate_label="challenger",
            latency_seconds=2.0,
            cost_usd=0.02,
        ),
    ]
    document = _summary_document(copy.deepcopy(rows))
    frozen = copy.deepcopy(document)
    before = sorted(item.name for item in tmp_path.iterdir())
    summary = upgrade.result_summary(document, spec)
    packet, items, mapping = upgrade.review_packet(document, spec, corpus)
    after = sorted(item.name for item in tmp_path.iterdir())
    assert document == frozen  # neither view mutated the document
    assert before == after  # the layer wrote no files
    assert packet and items and mapping
    assert summary["counts"]["semantic_verdict"] == {"pending": 2}
    assert summary["cost_usd"]["cost_per_acceptable"] is None
    assert "acceptable" not in json.dumps(summary["counts"])
    assert all(item["semantic_verdict"] == "pending" and item["reviewer"] is None for item in items)
    assert all(label != "D01.incumbent.high.r1" for label in mapping)


# ---------------------------------------------------------------------------
# Review packet blinding
# ---------------------------------------------------------------------------


def test_review_packet_blinds_identities_and_keeps_the_mapping_separate(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    observed = [{"name": "register_search", "arguments": {"query": "x"}, "result": {}}]
    rows = [
        _row(
            "H02.challenger.high.r1",
            case_id="H02",
            stage="heldout",
            candidate_label="challenger",
            latency_seconds=3.0,
            cost_usd=0.02,
            tool_steps=observed,
        ),
        _row(
            "H02.incumbent.high.r1",
            case_id="H02",
            stage="heldout",
            latency_seconds=2.0,
            cost_usd=0.01,
            tool_steps=observed,
        ),
    ]
    document = _summary_document(rows)
    packet, items, mapping = upgrade.review_packet(document, spec, corpus)
    assert len(items) == len(mapping) == 2
    # Identifying keys are absent from every item, and the candidate labels and
    # model identities are absent from the packet text and item payloads.
    for item in items:
        assert not {"candidate_label", "condition", "effort", "model", "route_id"} & set(item)
    for secret in (
        "incumbent",
        "challenger",
        "openrouter/openai/gpt-6-sol",
        "route-incumbent",
        "route-challenger",
    ):
        assert secret not in packet
        assert all(secret not in json.dumps(item, sort_keys=True) for item in items)
    # The observed evidence and the pending template are present.
    first = items[0]
    assert first["prompt"] == corpus.by_id()["H02"].prompt
    assert first["rubric"] == corpus.by_id()["H02"].expected["rubric"]
    assert first["tools"] == [dict(tool.function) for tool in corpus.by_id()["H02"].tools]
    assert first["schema"] == corpus.by_id()["H02"].schema
    assert first["observed_tool_steps"][0]["name"] == "register_search"
    assert first["status"] == "completed" and first["final_response"] is None
    assert first["semantic_verdict"] == "pending" and first["reviewer"] is None
    # Labels are opaque and keyed: deterministic per key, different across keys,
    # never equal to the attempt id, and the mapping stays separate.
    assert set(mapping) == {item["label"] for item in items}
    assert set(mapping.values()) == {"H02.challenger.high.r1", "H02.incumbent.high.r1"}
    assert all(label.startswith("R") and len(label) == 13 for label in mapping)
    again_packet, again_items, again_mapping = upgrade.review_packet(
        document, spec, corpus, key=spec.manifest_sha256
    )
    assert [item["label"] for item in again_items] == [item["label"] for item in items]
    assert again_mapping == mapping and again_packet == packet
    _, keyed_items, keyed_mapping = upgrade.review_packet(
        document, spec, corpus, key="independent-review-key"
    )
    assert [item["label"] for item in keyed_items] != [item["label"] for item in items]
    assert set(keyed_mapping.values()) == set(mapping.values())
    # Items are ordered by opaque label, not by schedule order.
    assert [item["label"] for item in items] == sorted(item["label"] for item in items)


def test_review_packet_refuses_rows_outside_the_corpus(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    with pytest.raises(upgrade.SpecError, match="unknown case id"):
        upgrade.review_packet(
            _summary_document([_row("X99.incumbent.high.r1", case_id="X99")]), spec, corpus
        )
    with pytest.raises(upgrade.SpecError, match="expected a JSON array"):
        upgrade.result_summary({"attempts": "nope"}, spec)
    with pytest.raises(upgrade.SpecError, match="attempt_id"):
        upgrade.result_summary({"attempts": [{"case_id": "D01"}]}, spec)


def test_rows_without_a_well_formed_identity_are_counted_but_never_paired(
    tmp_path: Path, corpus: follow.FixtureSet
) -> None:
    spec = load_from(tmp_path, manifest_document(corpus.sha256), corpus)
    summary = upgrade.result_summary(
        _summary_document(
            [
                _row("D01.incumbent.high.r1", latency_seconds=1.0),
                _row(
                    "D01.challenger.high.r1",
                    candidate_label="challenger",
                    latency_seconds=2.0,
                    repeat={"bogus": True},
                ),
            ]
        ),
        spec,
    )
    # The malformed row cannot form a matched pair, but it is still counted and
    # its latency still feeds the observed statistics - nothing is dropped.
    assert summary["paired_deltas_seconds"] == []
    assert summary["latency_seconds"]["all"] == {"n": 2, "p95": 2.0, "max": 2.0}
    assert summary["counts"]["recorded_attempts"] == 2
