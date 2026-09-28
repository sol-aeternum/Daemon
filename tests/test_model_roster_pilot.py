"""Tests for scripts/model_roster_pilot.py: offline pilot plan + strict scoring.

Scope guard for this suite: the tool is an offline fixture harness. These tests
pin the fixture matrix, the deterministic attempt plan, strict input rejection,
the incomplete-never-passes rule, hard-violation handling, the 15/16 aggregate
gate, the all-attempts cost denominator and the declared coarse latency report.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from scripts.model_roster_pilot import (
    CANDIDATES,
    EXIT_OK,
    EXIT_REJECTED,
    EXIT_SCREENING_NOT_MET,
    FIXTURES_ARTIFACT_VERSION,
    HARD_VIOLATION_KINDS,
    LATENCY_P95_TARGETS_SECONDS,
    MAX_CALLS_PER_ATTEMPT,
    PLAN_ARTIFACT_VERSION,
    REPORT_ARTIFACT_VERSION,
    RESULTS_ARTIFACT_VERSION,
    WORKLOADS,
    ArtifactError,
    Attempt,
    AttemptResult,
    FixtureSet,
    build_attempts,
    build_plan,
    build_report,
    evaluate_attempt,
    load_fixtures,
    main,
    parse_results,
    percentile_nearest_rank,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "model_roster_pilot.py"
FIXTURES_PATH = REPO_ROOT / "tests" / "fixtures" / "model_roster_pilot.json"
ROUTE_CONFIG_PATH = REPO_ROOT / "config" / "model_routing.json"
POLICY_CONFIG_PATH = REPO_ROOT / "config" / "inference_policy.json"

EXPECTED_CASE_IDS = tuple(
    f"{prefix}{number:02d}" for prefix in ("O", "R", "U") for number in range(1, 9)
)
EXPECTED_ATTEMPTS = 208
EXPECTED_SLICES = 13  # 4 candidates x 3 workloads + mercury utility
ATTEMPTS_PER_SLICE = 16


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _section(value: object, where: str = "value") -> dict[str, object]:
    assert isinstance(value, dict), f"{where}: expected mapping, got {type(value).__name__}"
    return {str(key): item for key, item in value.items()}


def _rows(value: object, where: str = "value") -> list[dict[str, object]]:
    assert isinstance(value, list), f"{where}: expected list, got {type(value).__name__}"
    return [_section(item, f"{where}[]") for item in value]


def _int_at(value: object, where: str = "value") -> int:
    assert isinstance(value, int) and not isinstance(value, bool), f"{where}: {value!r}"
    return value


def _float_at(value: object, where: str = "value") -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool), f"{where}: {value!r}"
    return float(value)


def _strs(value: object, where: str = "value") -> list[str]:
    assert isinstance(value, list), f"{where}: expected list, got {type(value).__name__}"
    return [str(item) for item in value]


def _fixtures() -> FixtureSet:
    return load_fixtures(FIXTURES_PATH)


def _attempts() -> tuple[Attempt, ...]:
    return build_attempts(_fixtures())


def _attempt_by_id(attempt_id: str) -> Attempt:
    for attempt in _attempts():
        if attempt.attempt_id == attempt_id:
            return attempt
    raise AssertionError(f"no planned attempt {attempt_id!r}")


def _passing_record(attempt: Attempt, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "attempt_id": attempt.attempt_id,
        "calls_used": 1,
        "latency_seconds": 4.0,
        "cost_usd": 0.01,
        "semantic_verdict": "pass",
        "adjudicator": "reviewer-a",
        "hard_violation_kinds": [],
    }
    if attempt.requires_schema:
        record["schema_valid"] = True
    record.update(overrides)
    return record


def _results_document(
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
    drop: tuple[str, ...] = (),
    attempts: tuple[Attempt, ...] | None = None,
) -> dict[str, Any]:
    planned = attempts if attempts is not None else _attempts()
    overrides = overrides or {}
    records: list[dict[str, Any]] = []
    for attempt in planned:
        if attempt.attempt_id in drop:
            continue
        records.append(_passing_record(attempt, **overrides.get(attempt.attempt_id, {})))
    return {
        "artifact_version": RESULTS_ARTIFACT_VERSION,
        "fixtures_sha256": hashlib.sha256(FIXTURES_PATH.read_bytes()).hexdigest(),
        "attempts": records,
    }


def _write_results(tmp_path: Path, document: dict[str, Any], name: str = "results.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _score(tmp_path: Path, document: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    path = _write_results(tmp_path, document)
    out = tmp_path / "report.json"
    code = main(
        [
            "score",
            "--fixtures",
            str(FIXTURES_PATH),
            "--results",
            str(path),
            "--format",
            "json",
            "--out",
            str(out),
        ]
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(report, dict)
    return code, report


def _slice(report: dict[str, Any], candidate: str, workload: str) -> dict[str, Any]:
    for block in _rows(report["slices"], "slices"):
        if block["candidate_label"] == candidate and block["workload"] == workload:
            return block
    raise AssertionError(f"no slice for {candidate}/{workload}")


def _candidate(report: dict[str, Any], label: str) -> dict[str, Any]:
    for block in _rows(report["candidates"], "candidates"):
        if block["label"] == label:
            return block
    raise AssertionError(f"no candidate report for {label!r}")


def _outcome(report: dict[str, Any], attempt_id: str) -> dict[str, Any]:
    for block in _rows(report["attempts"], "attempts"):
        if block["attempt_id"] == attempt_id:
            return block
    raise AssertionError(f"no attempt outcome for {attempt_id!r}")


def _rank(report: dict[str, Any], workload: str) -> dict[str, Any]:
    return _section(
        _section(report["cost_rank_by_workload"], "cost_rank_by_workload")[workload],
        f"cost_rank_by_workload.{workload}",
    )


def _rank_entries(report: dict[str, Any], workload: str) -> list[dict[str, Any]]:
    return _rows(_rank(report, workload)["entries"], f"{workload}.entries")


def _mutate_fixtures(tmp_path: Path, mutate: Any) -> Path:
    document = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Fixture matrix
# --------------------------------------------------------------------------


class TestFixtureMatrix:
    def test_twenty_four_cases_across_three_workloads(self) -> None:
        cases = _fixtures().cases
        assert len(cases) == 24
        assert tuple(case.case_id for case in cases) == EXPECTED_CASE_IDS
        for workload in WORKLOADS:
            matching = [case for case in cases if case.workload == workload]
            assert len(matching) == 8, workload

    def test_case_ids_are_unique_and_assertions_are_case_scoped(self) -> None:
        cases = _fixtures().cases
        ids = [case.case_id for case in cases]
        assert len(set(ids)) == len(ids)
        for case in cases:
            assert case.assertion_ids, case.case_id
            assert len(set(case.assertion_ids)) == len(case.assertion_ids)
            for assertion_id in case.assertion_ids:
                assert assertion_id.startswith(case.case_id)
            assert case.human_rubric.strip()
            assert set(case.hard_violation_kinds) <= set(HARD_VIOLATION_KINDS)

    def test_eight_structured_output_cases_carry_a_schema(self) -> None:
        schema_cases = [case for case in _fixtures().cases if case.requires_schema]
        assert [case.case_id for case in schema_cases] == [
            "U01",
            "U02",
            "U03",
            "U04",
            "U05",
            "U06",
            "U07",
            "U08",
        ]
        for case in schema_cases:
            assert case.schema is not None
            assert case.schema["required"]

    def test_tools_are_read_only_benchmark_stubs_within_call_budget(self) -> None:
        for case in _fixtures().cases:
            assert case.max_calls <= MAX_CALLS_PER_ATTEMPT
            by_name = {tool.name: tool for tool in case.tools}
            assert set(case.tool_responses) <= set(by_name)
            for tool in case.tools:
                assert tool.parameter_names, f"{case.case_id}/{tool.name} has no parameters"
            for name, entries in case.tool_responses.items():
                assert entries, f"{case.case_id}/{name}"
                for entry in entries:
                    if "match" in entry:
                        match = _section(entry["match"], f"{case.case_id}/{name}.match")
                        assert match, f"{case.case_id}/{name} has an empty match selector"
                        for key in match:
                            assert key in by_name[name].parameter_names, f"{case.case_id}/{name}"

    def test_prompts_are_unique_and_hashed_stably(self) -> None:
        fixtures = _fixtures()
        prompts = [case.prompt for case in fixtures.cases]
        assert len(set(prompts)) == len(prompts)
        first = [case.prompt_sha256 for case in fixtures.cases]
        second = [case.prompt_sha256 for case in load_fixtures(FIXTURES_PATH).cases]
        assert first == second
        assert all(len(digest) == 64 for digest in first)
        assert len(set(first)) == len(first)
        assert first == [
            hashlib.sha256(case.prompt.encode("utf-8")).hexdigest() for case in fixtures.cases
        ]

    def test_fixture_artifact_declares_its_version(self) -> None:
        document = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
        assert document["artifact_version"] == FIXTURES_ARTIFACT_VERSION
        assert document["status"] == "proposed-benchmark-only"

    def test_rejects_fixture_with_call_budget_above_pilot_limit(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["cases"][0]["max_calls"] = 4

        with pytest.raises(ArtifactError, match="max_calls"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    def test_rejects_fixture_with_write_capable_tool(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            for case in document["cases"]:
                for tool in case["tools"]:
                    tool["read_only"] = False

        with pytest.raises(ArtifactError, match="read-only"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    def test_rejects_fixture_missing_a_required_case(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["cases"] = [case for case in document["cases"] if case["case_id"] != "R05"]

        with pytest.raises(ArtifactError, match="missing R05"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    def test_rejects_fixture_with_unknown_case_id(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["cases"][0]["case_id"] = "O09"

        with pytest.raises(ArtifactError, match="case_id"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    def test_rejects_schema_required_but_schema_null(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["cases"][0]["requires_schema"] = True

        with pytest.raises(ArtifactError, match="schema"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    def test_rejects_tolerance_without_a_declared_expected_value(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["cases"][0]["expected"]["tolerances"] = {"invented_value": 0.5}

        with pytest.raises(ArtifactError, match="tolerances"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("assertions", [{"id": "O01-A1", "kind": "answer_correct"}], "statement"),
            ("failure_examples", [17], "failure_examples"),
            ("expected_values", [], "expected_values"),
            ("tolerances", {"hours_start_utc": "0.1"}, "tolerances"),
        ],
    )
    def test_rejects_malformed_expected_evidence(
        self, tmp_path: Path, field: str, value: Any, match: str
    ) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["cases"][0]["expected"][field] = value

        with pytest.raises(ArtifactError, match=match):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    def test_rejects_schema_required_field_not_in_properties(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["cases"][16]["schema"]["required"] = ["invented"]

        with pytest.raises(ArtifactError, match="undeclared property"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    def test_rejects_tool_required_field_not_in_properties(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["cases"][2]["tools"][0]["parameters"]["required"] = ["invented"]

        with pytest.raises(ArtifactError, match="undeclared property"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))

    def test_rejects_wrong_fixture_artifact_version(self, tmp_path: Path) -> None:
        def mutate(document: dict[str, Any]) -> None:
            document["artifact_version"] = "model-roster-pilot-fixtures/99"

        with pytest.raises(ArtifactError, match="artifact_version"):
            load_fixtures(_mutate_fixtures(tmp_path, mutate))


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


class TestPlan:
    def test_planned_attempt_counts_match_the_proposed_pilot(self) -> None:
        attempts = _attempts()
        assert len(attempts) == EXPECTED_ATTEMPTS
        plan = build_plan(_fixtures(), FIXTURES_PATH)
        counts = _section(plan["counts"], "counts")
        assert counts["planned_attempts"] == EXPECTED_ATTEMPTS
        assert counts["upper_call_bound"] == EXPECTED_ATTEMPTS * MAX_CALLS_PER_ATTEMPT
        assert _section(counts["planned_attempts_by_candidate"]) == {
            "deepseek-flash": 48,
            "glm-flash": 48,
            "luna": 48,
            "mercury": 16,
            "sol": 48,
        }
        assert _section(counts["planned_attempts_by_workload"]) == {
            "orchestration": 64,
            "synthesis": 64,
            "utility": 80,
        }

    def test_mercury_is_planned_for_the_utility_slice_only(self) -> None:
        for attempt in _attempts():
            if attempt.candidate_label == "mercury":
                assert attempt.workload == "utility"
                assert attempt.case_id.startswith("U")
            else:
                assert attempt.case_id[0] in ("O", "R", "U")

    def test_every_slice_has_sixteen_attempts_over_eight_cases(self) -> None:
        slices: dict[tuple[str, str], list[str]] = {}
        for attempt in _attempts():
            slices.setdefault((attempt.candidate_label, attempt.workload), []).append(
                attempt.case_id
            )
        assert len(slices) == EXPECTED_SLICES
        for key, case_ids in slices.items():
            assert len(case_ids) == ATTEMPTS_PER_SLICE, key
            assert len(set(case_ids)) == 8, key
            assert sorted({case_id[1:] for case_id in case_ids}) == [
                f"{number:02d}" for number in range(1, 9)
            ], key

    def test_attempt_ids_are_deterministic_and_unique(self) -> None:
        first = [attempt.attempt_id for attempt in _attempts()]
        second = [attempt.attempt_id for attempt in build_attempts(_fixtures())]
        assert first == second
        assert len(set(first)) == EXPECTED_ATTEMPTS
        assert first[0] == "O01-luna-r1"
        assert all(attempt_id.count("-r") == 1 for attempt_id in first)

    def test_plan_is_byte_identical_across_runs(self) -> None:
        plan_a = build_plan(_fixtures(), FIXTURES_PATH)
        plan_b = build_plan(_fixtures(), FIXTURES_PATH)
        assert json.dumps(plan_a, sort_keys=True) == json.dumps(plan_b, sort_keys=True)

    def test_plan_preserves_adjudication_evidence_and_loaded_hash(self, tmp_path: Path) -> None:
        path = _mutate_fixtures(tmp_path, lambda document: None)
        original = json.loads(path.read_text(encoding="utf-8"))
        fixtures = load_fixtures(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text('{"changed":true}', encoding="utf-8")
        plan = build_plan(fixtures, path)
        assert _section(plan["fixtures"])["sha256"] == digest
        for case, row in zip(original["cases"], _rows(plan["cases"]), strict=True):
            assert row["expected"] == case["expected"]

    def test_calls_used_counts_model_dispatches_not_tool_calls(self) -> None:
        plan = build_plan(_fixtures())
        definition = str(_section(plan["thresholds"])["calls_used_definition"])
        assert "final answer" in definition
        assert "zero tool calls" in definition
        cases = {row["case_id"]: row for row in _rows(plan["cases"])}
        assert "no tool call" in str(cases["O04"]["expected"]).lower()
        assert "At most two search-dispatch calls" in str(cases["O06"]["expected"])
        assert "Three model calls total" in str(cases["O08"]["expected"])

    def test_plan_reports_dispatch_disabled_and_qualification_unresolved(self) -> None:
        plan = build_plan(_fixtures(), FIXTURES_PATH)
        assert plan["artifact_version"] == PLAN_ARTIFACT_VERSION
        dispatch = _section(plan["dispatch"], "dispatch")
        assert dispatch["enabled"] is False
        assert dispatch["executor"] is None
        qualification = _section(plan["qualification"], "qualification")
        assert qualification["resolved"] is False
        assert qualification["candidate_labels_are_logical"] is True
        for key in (
            "model_id",
            "endpoint",
            "provider_tag",
            "region",
            "price_ceiling_usd_per_request",
        ):
            assert qualification[key] is None, key
        authorization = _section(plan["authorization"], "authorization")
        assert authorization["approved"] is False
        assert authorization["spend_cap_usd_approved"] is None
        assert authorization["account_scope_approved"] is False

    def test_every_planned_attempt_carries_unresolved_qualification(self) -> None:
        for row in _rows(build_plan(_fixtures(), FIXTURES_PATH)["attempts"], "attempts"):
            qualification = _section(row["qualification"], "attempt.qualification")
            assert qualification["resolved"] is False
            assert qualification["model_id"] is None
            assert qualification["endpoint"] is None

    def test_plan_declares_the_proposed_unapproved_thresholds(self) -> None:
        thresholds = _section(build_plan(_fixtures(), FIXTURES_PATH)["thresholds"], "thresholds")
        assert thresholds["status"] == "proposed-unapproved"
        assert thresholds["success_threshold_per_slice"] == 15
        assert thresholds["attempts_per_slice"] == ATTEMPTS_PER_SLICE
        assert thresholds["hard_violations_allowed"] == 0
        assert thresholds["max_calls_per_attempt"] == MAX_CALLS_PER_ATTEMPT
        assert _section(thresholds["latency_p95_targets_seconds"]) == {
            "utility": 10.0,
            "orchestration": 30.0,
            "synthesis": 60.0,
        }

    def test_plan_cli_writes_the_artifact(self, tmp_path: Path, capsys: Any) -> None:
        out = tmp_path / "plan.json"
        code = main(
            [
                "plan",
                "--fixtures",
                str(FIXTURES_PATH),
                "--format",
                "json",
                "--out",
                str(out),
            ]
        )
        assert code == EXIT_OK
        captured = capsys.readouterr().out
        plan = json.loads(out.read_text(encoding="utf-8"))
        assert json.loads(captured) == plan
        assert plan["artifact_version"] == PLAN_ARTIFACT_VERSION
        assert _section(plan["counts"])["planned_attempts"] == EXPECTED_ATTEMPTS

    def test_plan_runs_standalone_without_network_or_credentials(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "plan", "--format", "text"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={"PATH": "/usr/bin:/bin"},
            check=False,
        )
        assert result.returncode == EXIT_OK, result.stderr
        assert "planned attempts: 208" in result.stdout
        assert "no dispatch" in result.stdout

    def test_help_documents_the_offline_contract(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--help"])
        assert excinfo.value.code == 0
        text = _help_text(["--help"]) + _help_text(["score", "--help"])
        assert "Never dispatches a model call" in text
        assert "not a public API" in text
        assert "exit codes" in text


def _help_text(argv: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    return result.stdout


class TestRouteConfigUntouched:
    def test_plan_and_score_do_not_mutate_runtime_route_config(self, tmp_path: Path) -> None:
        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        before = (digest(ROUTE_CONFIG_PATH), digest(POLICY_CONFIG_PATH))
        assert main(["plan", "--format", "text"]) == EXIT_OK
        code, _ = _score(tmp_path, _results_document())
        assert code == EXIT_OK
        assert (digest(ROUTE_CONFIG_PATH), digest(POLICY_CONFIG_PATH)) == before


# --------------------------------------------------------------------------
# Source-level dependency guard
# --------------------------------------------------------------------------


class TestOfflineSourceGuards:
    def test_only_standard_library_imports(self) -> None:
        tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert roots == {
            "__future__",
            "argparse",
            "collections",
            "dataclasses",
            "hashlib",
            "json",
            "math",
            "pathlib",
            "re",
            "statistics",
            "sys",
            "typing",
        }

    def test_no_network_client_or_secret_loading_in_source(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "httpx",
            "litellm",
            "openai",
            "requests",
            "urllib",
            "socket",
            "subprocess",
            "dotenv",
            "load_dotenv",
            "os.environ",
            "getenv",
            "OPENROUTER",
            "API_KEY",
        ):
            assert forbidden not in source, forbidden


# --------------------------------------------------------------------------
# Strict results validation
# --------------------------------------------------------------------------


class TestStrictResultsRejection:
    def _reject(self, tmp_path: Path, document: Any, match: str) -> None:
        path = _write_results(tmp_path, document)
        with pytest.raises(ArtifactError, match=match):
            parse_results(path, {attempt.attempt_id: attempt for attempt in _attempts()})

    def test_cli_exit_code_is_two_for_every_rejection(self, tmp_path: Path, capsys: Any) -> None:
        document = _results_document()
        document["attempts"][3]["cost_usd"] = -1
        path = _write_results(tmp_path, document)
        code = main(["score", "--results", str(path), "--fixtures", str(FIXTURES_PATH)])
        assert code == EXIT_REJECTED
        assert "rejected" in capsys.readouterr().err

    def test_rejects_unknown_attempt_id(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["attempt_id"] = "O01-luna-r9"
        self._reject(tmp_path, document, "not a planned attempt id")

    def test_rejects_attempt_id_from_another_workload_candidate_pair(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["attempt_id"] = "O01-mercury-r1"
        self._reject(tmp_path, document, "not a planned attempt id")

    def test_rejects_duplicate_attempt(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"].append(dict(document["attempts"][0]))
        self._reject(tmp_path, document, "duplicate result")

    def test_rejects_negative_cost(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["cost_usd"] = -0.001
        self._reject(tmp_path, document, "non-negative")

    def test_rejects_non_finite_cost(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["cost_usd"] = float("nan")
        self._reject(tmp_path, document, "finite")

    def test_rejects_infinite_latency(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["latency_seconds"] = float("inf")
        self._reject(tmp_path, document, "finite")

    def test_rejects_negative_latency(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["latency_seconds"] = -0.5
        self._reject(tmp_path, document, "non-negative")

    def test_rejects_boolean_cost(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["cost_usd"] = True
        self._reject(tmp_path, document, "finite non-negative number")

    def test_rejects_non_numeric_cost(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["cost_usd"] = "0.01"
        self._reject(tmp_path, document, "finite non-negative number")

    def test_rejects_unknown_attempt_key(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["cost_us"] = 0.01
        self._reject(tmp_path, document, "unknown key")

    def test_rejects_unknown_top_level_key(self, tmp_path: Path) -> None:
        document = _results_document()
        document["totals"] = {}
        self._reject(tmp_path, document, "unknown key")

    def test_rejects_wrong_results_artifact_version(self, tmp_path: Path) -> None:
        document = _results_document()
        document["artifact_version"] = "model-roster-pilot-results/99"
        self._reject(tmp_path, document, "artifact_version")

    def test_rejects_non_boolean_flags(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["hard_violation_kinds"] = "fabricated_source"
        self._reject(tmp_path, document, "expected a JSON array")

    @pytest.mark.parametrize(
        "value", ["fabricated_source", ["invented"], ["fabricated_source", "fabricated_source"]]
    )
    def test_rejects_invalid_hard_kinds(self, tmp_path: Path, value: Any) -> None:
        document = _results_document()
        document["attempts"][0]["hard_violation_kinds"] = value
        self._reject(tmp_path, document, "hard_violation_kinds")

    @pytest.mark.parametrize("verdict", ["pass", "fail"])
    @pytest.mark.parametrize("adjudicator", [None, "  "])
    def test_pass_fail_requires_named_adjudicator(
        self, tmp_path: Path, verdict: str, adjudicator: str | None
    ) -> None:
        document = _results_document()
        document["attempts"][0].update(semantic_verdict=verdict, adjudicator=adjudicator)
        self._reject(tmp_path, document, "adjudicator")

    def test_rejects_results_from_different_fixture_bytes(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        document = _results_document()
        document["fixtures_sha256"] = "0" * 64
        path = _write_results(tmp_path, document)
        assert (
            main(["score", "--fixtures", str(FIXTURES_PATH), "--results", str(path)])
            == EXIT_REJECTED
        )
        assert "fixture hash mismatch" in capsys.readouterr().err

    def test_rejects_unknown_semantic_verdict(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["semantic_verdict"] = "mostly-correct"
        self._reject(tmp_path, document, "semantic_verdict")

    def test_rejects_adjudicator_without_a_declared_verdict(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"][0]["semantic_verdict"] = "pending"
        self._reject(tmp_path, document, "requires a declared semantic verdict")

    def test_rejects_attempt_without_an_attempt_id(self, tmp_path: Path) -> None:
        document = _results_document()
        del document["attempts"][0]["attempt_id"]
        self._reject(tmp_path, document, "missing required key")

    def test_rejects_attempts_that_are_not_a_list(self, tmp_path: Path) -> None:
        document = _results_document()
        document["attempts"] = {"attempt_id": "O01-luna-r1"}
        self._reject(tmp_path, document, "expected a JSON array")

    def test_rejects_unreadable_results_file(self, tmp_path: Path) -> None:
        with pytest.raises(ArtifactError, match="cannot read"):
            parse_results(tmp_path / "absent.json", {})

    def test_rejects_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ArtifactError, match="not valid JSON"):
            parse_results(path, {})


# --------------------------------------------------------------------------
# Attempt-level scoring
# --------------------------------------------------------------------------


class TestAttemptScoring:
    def test_complete_adjudicated_attempt_passes(self) -> None:
        attempt = _attempt_by_id("O03-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {}))
        assert outcome.status == "pass"
        assert outcome.reasons == ()
        assert outcome.hard_violations == ()

    def test_missing_result_is_incomplete_and_never_passes(self) -> None:
        outcome = evaluate_attempt(_attempt_by_id("O03-luna-r1"), None)
        assert outcome.status == "incomplete"
        assert outcome.reasons == ("missing_result",)

    def test_pending_adjudication_is_incomplete(self) -> None:
        attempt = _attempt_by_id("O03-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {"semantic_verdict": "pending"}))
        assert outcome.status == "incomplete"
        assert "semantic_unadjudicated" in outcome.reasons

    def test_absent_semantic_verdict_is_incomplete(self) -> None:
        attempt = _attempt_by_id("O03-luna-r1")
        outcome = evaluate_attempt(
            attempt, _parse(attempt, {"semantic_verdict": None, "adjudicator": None})
        )
        assert outcome.status == "incomplete"
        assert "semantic_unadjudicated" in outcome.reasons

    def test_undeclared_hard_violation_flags_block_a_pass(self) -> None:
        attempt = _attempt_by_id("O03-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {"hard_violation_kinds": None}))
        assert outcome.status == "incomplete"
        assert "hard_kinds_undeclared" in outcome.reasons

    def test_unknown_latency_is_incomplete(self) -> None:
        attempt = _attempt_by_id("O03-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {"latency_seconds": None}))
        assert outcome.status == "incomplete"
        assert "latency_unknown" in outcome.reasons

    def test_unknown_call_count_is_incomplete(self) -> None:
        attempt = _attempt_by_id("O03-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {"calls_used": None}))
        assert outcome.status == "incomplete"
        assert "calls_unknown" in outcome.reasons

    def test_zero_calls_fails(self) -> None:
        attempt = _attempt_by_id("O03-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {"calls_used": 0}))
        assert outcome.status == "fail"
        assert "no_model_call" in outcome.reasons

    def test_clarifying_without_tool_call_still_uses_one_model_dispatch(self) -> None:
        attempt = _attempt_by_id("O04-luna-r1")
        assert attempt.tool_names == ("invoice_lookup",)
        assert evaluate_attempt(attempt, _parse(attempt, {"calls_used": 1})).status == "pass"

    def test_call_budget_overrun_fails_even_with_a_passing_verdict(self) -> None:
        attempt = _attempt_by_id("O03-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {"calls_used": 4}))
        assert outcome.status == "fail"
        assert "calls_exhausted" in outcome.reasons

    def test_schema_case_requires_a_true_schema_flag(self) -> None:
        attempt = _attempt_by_id("U05-luna-r1")
        unverified = evaluate_attempt(attempt, _parse(attempt, {"schema_valid": None}))
        assert unverified.status == "incomplete"
        assert "schema_unverified" in unverified.reasons
        violated = evaluate_attempt(attempt, _parse(attempt, {"schema_valid": False}))
        assert violated.status == "fail"
        assert "schema_violation" in violated.reasons

    def test_semantic_failure_fails(self) -> None:
        attempt = _attempt_by_id("R04-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {"semantic_verdict": "fail"}))
        assert outcome.status == "fail"
        assert "semantic_failed" in outcome.reasons
        assert outcome.hard_violations == ()

    def test_fabrication_is_a_hard_violation(self) -> None:
        attempt = _attempt_by_id("R08-luna-r1")
        outcome = evaluate_attempt(
            attempt, _parse(attempt, {"hard_violation_kinds": ["fabricated_source"]})
        )
        assert outcome.status == "fail"
        assert outcome.hard_violations == ("fabricated_source",)

    def test_unauthorized_action_is_a_hard_violation(self) -> None:
        attempt = _attempt_by_id("O04-luna-r1")
        outcome = evaluate_attempt(
            attempt, _parse(attempt, {"hard_violation_kinds": ["unauthorized_action"]})
        )
        assert outcome.status == "fail"
        assert outcome.hard_violations == ("unauthorized_action",)

    def test_unanticipated_kind_is_reported_and_fails_with_missing_evidence(self) -> None:
        attempt = _attempt_by_id("O01-luna-r1")
        result = _parse(
            attempt,
            {"hard_violation_kinds": ["prompt_injection_followed"], "latency_seconds": None},
        )
        outcome = evaluate_attempt(attempt, result, ("fabricated_source",))
        assert outcome.status == "fail"
        assert outcome.unexpected_hard_violations == ("prompt_injection_followed",)
        assert "latency_unknown" in outcome.reasons

    def test_unknown_cost_does_not_block_a_task_pass(self) -> None:
        attempt = _attempt_by_id("O01-luna-r1")
        outcome = evaluate_attempt(attempt, _parse(attempt, {"cost_usd": None}))
        assert outcome.status == "pass"
        assert outcome.cost_known is False


def _parse(attempt: Attempt, overrides: dict[str, Any]) -> AttemptResult:
    """Build one supplied result through the strict parser, then return it."""
    document = _results_document(attempts=(attempt,))
    record = document["attempts"][0]
    for key, value in overrides.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    if record.get("semantic_verdict") not in ("pass", "fail"):
        # The parser rejects a named adjudicator without a declared verdict.
        record.pop("adjudicator", None)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "results.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        planned = {item.attempt_id: item for item in _attempts()}
        return parse_results(path, planned)[record["attempt_id"]]


# --------------------------------------------------------------------------
# Aggregate scoring
# --------------------------------------------------------------------------


class TestAggregateScoring:
    def test_all_pass_run_meets_the_gate(self, tmp_path: Path) -> None:
        code, report = _score(tmp_path, _results_document())
        assert code == EXIT_OK
        assert report["artifact_version"] == REPORT_ARTIFACT_VERSION
        assert report["screening_outcome"] == "pass"
        totals = _section(report["totals"], "totals")
        assert totals["planned_attempts"] == EXPECTED_ATTEMPTS
        assert totals["results_supplied"] == EXPECTED_ATTEMPTS
        assert totals["passes"] == EXPECTED_ATTEMPTS
        assert totals["failures"] == 0
        assert totals["incomplete"] == 0
        assert totals["hard_violations"] == 0
        assert totals["coverage_complete"] is True
        assert len(_rows(report["slices"], "slices")) == EXPECTED_SLICES
        assert len(_rows(report["candidates"], "candidates")) == len(CANDIDATES)

    def test_fifteen_of_sixteen_successes_still_meets_the_threshold(self, tmp_path: Path) -> None:
        document = _results_document(overrides={"O01-luna-r1": {"semantic_verdict": "fail"}})
        code, report = _score(tmp_path, document)
        block = _slice(report, "luna", "orchestration")
        assert block["passes"] == 15
        assert block["meets_success_threshold"] is True
        assert block["status"] == "pass"
        assert code == EXIT_OK

    def test_fourteen_of_sixteen_successes_misses_the_threshold(self, tmp_path: Path) -> None:
        document = _results_document(
            overrides={
                "O01-luna-r1": {"semantic_verdict": "fail"},
                "O01-luna-r2": {"semantic_verdict": "fail"},
            }
        )
        code, report = _score(tmp_path, document)
        block = _slice(report, "luna", "orchestration")
        assert block["passes"] == 14
        assert block["meets_success_threshold"] is False
        assert block["status"] == "fail"
        assert _candidate(report, "luna")["screened"] is False
        assert report["screening_outcome"] == "fail"
        assert code == EXIT_SCREENING_NOT_MET

    def test_single_hard_violation_zeroes_an_otherwise_passing_slice(self, tmp_path: Path) -> None:
        document = _results_document(
            overrides={"O07-luna-r1": {"hard_violation_kinds": ["prompt_injection_followed"]}}
        )
        code, report = _score(tmp_path, document)
        block = _slice(report, "luna", "orchestration")
        assert block["passes"] == 15
        assert block["meets_success_threshold"] is True
        assert block["hard_violations"] == 1
        assert block["hard_violation_free"] is False
        assert block["status"] == "fail"
        assert _strs(block["hard_violation_attempt_ids"]) == ["O07-luna-r1"]
        assert _candidate(report, "luna")["screened"] is False
        assert code == EXIT_SCREENING_NOT_MET

    def test_missing_attempt_leaves_the_slice_incomplete(self, tmp_path: Path) -> None:
        code, report = _score(tmp_path, _results_document(drop=("R03-sol-r2",)))
        block = _slice(report, "sol", "synthesis")
        assert block["incomplete"] == 1
        assert block["results_supplied"] == 15
        assert block["passes"] == 15
        assert block["coverage_complete"] is False
        assert block["definitive"] is False
        assert block["status"] == "incomplete"
        assert report["screening_outcome"] == "incomplete"
        assert code == EXIT_SCREENING_NOT_MET
        assert _outcome(report, "R03-sol-r2")["reasons"] == ["missing_result"]

    def test_unadjudicated_attempt_is_incomplete_not_a_pass(self, tmp_path: Path) -> None:
        document = _results_document(
            overrides={"U04-luna-r2": {"semantic_verdict": "pending", "adjudicator": None}}
        )
        code, report = _score(tmp_path, document)
        block = _slice(report, "luna", "utility")
        assert block["passes"] == 15
        assert block["incomplete"] == 1
        assert block["results_supplied"] == ATTEMPTS_PER_SLICE
        assert block["results_missing"] == 0
        assert block["status"] == "incomplete"
        assert _candidate(report, "luna")["screened"] is False
        assert code == EXIT_SCREENING_NOT_MET

    def test_known_hard_violation_dominates_missing_results_at_all_levels(
        self, tmp_path: Path
    ) -> None:
        document = _results_document(
            drop=("O01-luna-r2",),
            overrides={"O01-luna-r1": {"hard_violation_kinds": ["prompt_injection_followed"]}},
        )
        code, report = _score(tmp_path, document)
        block = _slice(report, "luna", "orchestration")
        assert code == EXIT_SCREENING_NOT_MET
        assert (
            block["status"]
            == _candidate(report, "luna")["status"]
            == report["screening_outcome"]
            == "fail"
        )
        assert block["results_supplied"] == 15
        assert block["results_missing"] == 1
        assert block["hard_violations_by_kind"] == {"prompt_injection_followed": 1}
        assert block["unexpected_hard_violations_by_kind"] == {"prompt_injection_followed": 1}
        assert _section(report["totals"])["hard_violations_by_kind"] == {
            "prompt_injection_followed": 1
        }
        assert _outcome(report, "O01-luna-r1")["unexpected_hard_violations"] == [
            "prompt_injection_followed"
        ]

    def test_success_threshold_impossible_with_missing_result(self, tmp_path: Path) -> None:
        document = _results_document(
            drop=("O02-luna-r1",),
            overrides={
                "O01-luna-r1": {"semantic_verdict": "fail"},
                "O01-luna-r2": {"semantic_verdict": "fail"},
            },
        )
        _, report = _score(tmp_path, document)
        assert _slice(report, "luna", "orchestration")["status"] == "fail"
        assert report["screening_outcome"] == "fail"

    def test_report_declares_the_scoring_contract(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _results_document())
        contract = _section(report["scoring_contract"], "scoring_contract")
        assert len(_strs(contract["pass_requires"], "pass_requires")) == 5
        assert any("adjudicator" in item for item in _strs(contract["pass_requires"]))
        assert any("schema_valid" in item for item in _strs(contract["pass_requires"]))
        assert any("hard_violation_kinds" in item for item in _strs(contract["pass_requires"]))
        assert _strs(contract["hard_violation_kinds"]) == list(HARD_VIOLATION_KINDS)
        assert "never inferred from a schema" in str(contract["schema_and_semantics_are_separate"])
        assert "incomplete" in str(contract["incomplete_is_not_a_pass"]).lower()

    def test_report_keeps_qualification_unresolved(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _results_document())
        qualification = _section(report["qualification"], "qualification")
        assert qualification["resolved"] is False
        assert qualification["candidate_labels_are_logical"] is True
        labels = {str(block["label"]) for block in _rows(report["candidates"], "candidates")}
        assert labels == {candidate.label for candidate in CANDIDATES}

    def test_report_declares_its_limitations(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _results_document())
        limitations = " ".join(str(item) for item in report["limitations"])
        assert "qualification is unresolved" in limitations
        assert "coarse" in limitations
        assert "held-out" in limitations
        assert "ledger is untouched" in limitations
        assert "ranked per workload" in limitations

    def test_scoring_is_deterministic(self, tmp_path: Path) -> None:
        document = _results_document(overrides={"O05-luna-r1": {"semantic_verdict": "fail"}})
        _, first = _score(tmp_path, document)
        _, second = _score(tmp_path, document)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_report_hashes_exact_results_bytes_and_versions(self, tmp_path: Path) -> None:
        path = _write_results(tmp_path, _results_document())
        loaded = parse_results(path, {item.attempt_id: item for item in _attempts()})
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text('{"changed":true}', encoding="utf-8")
        report = build_report(_fixtures(), _attempts(), loaded)
        inputs = _section(report["inputs"])
        assert _section(inputs["fixtures"]) == {
            "artifact_version": FIXTURES_ARTIFACT_VERSION,
            "sha256": hashlib.sha256(FIXTURES_PATH.read_bytes()).hexdigest(),
        }
        assert _section(inputs["results"]) == {
            "artifact_version": RESULTS_ARTIFACT_VERSION,
            "sha256": digest,
            "fixtures_sha256": _section(inputs["fixtures"])["sha256"],
        }

    def test_direct_report_rejects_drift_even_if_parser_was_not_given_fixture_hash(
        self, tmp_path: Path
    ) -> None:
        document = _results_document()
        document["fixtures_sha256"] = "0" * 64
        results = parse_results(
            _write_results(tmp_path, document),
            {item.attempt_id: item for item in _attempts()},
        )
        with pytest.raises(ArtifactError, match="fixture hash mismatch"):
            build_report(_fixtures(), _attempts(), results)

    def test_build_report_matches_evaluated_attempts(self, tmp_path: Path) -> None:
        attempts = _attempts()
        planned = {attempt.attempt_id: attempt for attempt in attempts}
        path = _write_results(tmp_path, _results_document())
        report = build_report(_fixtures(), attempts, parse_results(path, planned))
        assert len(_rows(report["attempts"], "attempts")) == EXPECTED_ATTEMPTS
        assert report["screening_outcome"] == "pass"


# --------------------------------------------------------------------------
# Cost accounting
# --------------------------------------------------------------------------


class TestCostAccounting:
    def test_denominator_keeps_every_planned_attempt(self, tmp_path: Path) -> None:
        code, report = _score(tmp_path, _results_document(drop=("O01-luna-r1",)))
        cost = _section(report["cost"], "cost")
        assert cost["attempts_in_cost_numerator"] == EXPECTED_ATTEMPTS
        assert cost["attempts_with_known_cost"] == EXPECTED_ATTEMPTS - 1
        assert cost["attempts_unknown_cost"] == 1
        assert cost["cost_total_definitive"] is False
        assert code == EXIT_SCREENING_NOT_MET

    def test_failed_attempt_cost_stays_in_the_denominator(self, tmp_path: Path) -> None:
        _, clean = _score(tmp_path, _results_document())
        _, with_failure = _score(
            tmp_path,
            _results_document(overrides={"O01-luna-r1": {"semantic_verdict": "fail"}}),
        )
        clean_cost = _section(clean["cost"], "cost")
        failed_cost = _section(with_failure["cost"], "cost")
        assert failed_cost["attempts_in_cost_numerator"] == clean_cost["attempts_in_cost_numerator"]
        assert failed_cost["known_cost_total_usd"] == clean_cost["known_cost_total_usd"]
        assert _int_at(failed_cost["attempts_with_known_cost"]) == EXPECTED_ATTEMPTS

    def test_unknown_cost_blocks_the_definitive_total_and_the_rank(self, tmp_path: Path) -> None:
        _, report = _score(
            tmp_path, _results_document(overrides={"O01-luna-r1": {"cost_usd": None}})
        )
        cost = _section(report["cost"], "cost")
        assert cost["attempts_unknown_cost"] == 1
        assert cost["cost_total_definitive"] is False
        assert cost["cost_per_successful_task_usd"] is None
        rank = _rank(report, "orchestration")
        assert rank["status"] == "withheld"
        assert "luna" in str(rank["blocked_reason"])
        assert "unknown or missing attempt cost" in str(rank["blocked_reason"])

    def test_unknown_cost_withholds_only_the_affected_workload(self, tmp_path: Path) -> None:
        _, report = _score(
            tmp_path, _results_document(overrides={"O01-luna-r1": {"cost_usd": None}})
        )
        assert _rank(report, "orchestration")["status"] == "withheld"
        for workload in ("synthesis", "utility"):
            assert _rank(report, workload)["status"] == "ranked", workload
            assert "luna" in [entry["candidate_label"] for entry in _rank_entries(report, workload)]

    def test_rank_is_per_workload_so_mercury_is_never_ranked_on_other_workloads(
        self, tmp_path: Path
    ) -> None:
        _, report = _score(tmp_path, _cost_shaped_document())
        orchestration = _rank(report, "orchestration")
        synthesis = _rank(report, "synthesis")
        utility = _rank(report, "utility")
        assert _strs(orchestration["candidates_planned"]) == [
            "deepseek-flash",
            "glm-flash",
            "luna",
            "sol",
        ]
        assert _strs(synthesis["candidates_planned"]) == [
            "deepseek-flash",
            "glm-flash",
            "luna",
            "sol",
        ]
        assert _strs(utility["candidates_planned"]) == [
            "deepseek-flash",
            "glm-flash",
            "luna",
            "mercury",
            "sol",
        ]
        for workload in ("orchestration", "synthesis"):
            labels = [entry["candidate_label"] for entry in _rank_entries(report, workload)]
            assert "mercury" not in labels
        assert "mercury" in [entry["candidate_label"] for entry in _rank_entries(report, "utility")]

    def test_ranks_each_workload_over_its_own_common_case_set(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _cost_shaped_document())
        for workload, prefix in (("orchestration", "O"), ("synthesis", "R"), ("utility", "U")):
            rank = _rank(report, workload)
            assert _strs(rank["cases"]) == [f"{prefix}{number:02d}" for number in range(1, 9)]
            entries = _rank_entries(report, workload)
            assert entries, workload
            for entry in entries:
                assert _strs(entry["cases"]) == _strs(rank["cases"])

    def test_rank_order_follows_cost_per_successful_attempt(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _cost_shaped_document())
        for workload in WORKLOADS:
            entries = _rank_entries(report, workload)
            values = [_float_at(entry["cost_per_successful_task_usd"]) for entry in entries]
            assert values == sorted(values), workload
            assert [entry["rank"] for entry in entries] == list(range(1, len(entries) + 1))
        utility_order = [entry["candidate_label"] for entry in _rank_entries(report, "utility")]
        assert utility_order == ["mercury", "deepseek-flash", "sol", "luna", "glm-flash"]
        orchestration_order = [
            entry["candidate_label"] for entry in _rank_entries(report, "orchestration")
        ]
        assert orchestration_order == ["deepseek-flash", "sol", "luna", "glm-flash"]

    def test_all_incurred_cost_is_divided_by_successful_attempts(self, tmp_path: Path) -> None:
        """A failed attempt's cost stays in the numerator and never in the divisor."""
        _, clean = _score(tmp_path, _cost_shaped_document())
        overrides = _cost_overrides()
        overrides["O01-luna-r1"] = {"semantic_verdict": "fail", "cost_usd": 1.0}
        _, with_failure = _score(tmp_path, _results_document(overrides=overrides))
        before = _rank_entries(clean, "orchestration")
        after = _rank_entries(with_failure, "orchestration")
        luna_before = next(entry for entry in before if entry["candidate_label"] == "luna")
        luna_after = next(entry for entry in after if entry["candidate_label"] == "luna")
        assert _float_at(luna_before["cost_per_successful_task_usd"]) == pytest.approx(0.02)
        assert luna_after["successful_attempts"] == 15
        # 15 x 0.02 of successes plus 1.00 of failed-attempt cost, over 15 successes.
        assert _float_at(luna_after["cost_per_successful_task_usd"]) == pytest.approx(
            (15 * 0.02 + 1.0) / 15, abs=1e-6
        )
        assert _float_at(luna_after["known_cost_total_usd"]) == pytest.approx(15 * 0.02 + 1.0)

    def test_candidate_all_workload_ratio_is_not_offered_as_a_comparison(
        self, tmp_path: Path
    ) -> None:
        _, report = _score(tmp_path, _cost_shaped_document())
        for label in ("mercury", "luna"):
            cost = _section(_candidate(report, label)["cost"], f"{label}.cost")
            assert cost["cost_per_successful_task_usd"] is None
            assert "different set of workloads" in str(cost["comparability_note"])
        mercury = _candidate(report, "mercury")
        assert _int_at(mercury["planned_attempts"]) == 16
        luna = _candidate(report, "luna")
        assert _int_at(luna["planned_attempts"]) == 48
        assert _strs(mercury["workloads"]) == ["utility"]

    def test_slice_costs_stay_comparable_within_one_workload(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _cost_shaped_document())
        utility = _slice(report, "luna", "utility")["cost"]
        assert _section(utility)["comparability_note"] is None
        assert _section(utility)["cost_per_successful_task_usd"] == pytest.approx(0.02)
        assert _section(utility)["attempts_in_cost_numerator"] == ATTEMPTS_PER_SLICE

    def test_cost_rank_withheld_when_no_candidate_screens_for_a_workload(
        self, tmp_path: Path
    ) -> None:
        overrides: dict[str, dict[str, Any]] = {
            f"O01-{candidate.label}-r{repeat}": {"semantic_verdict": "fail"}
            for candidate in CANDIDATES
            if "orchestration" in candidate.workloads
            for repeat in (1, 2)
        }
        _, report = _score(tmp_path, _results_document(overrides=overrides))
        rank = _rank(report, "orchestration")
        assert rank["status"] == "withheld"
        assert "no candidate met the screening gate" in str(rank["blocked_reason"])
        assert rank["entries"] == []
        assert len(_strs(rank["candidates_excluded"])) == len(_strs(rank["candidates_planned"]))
        assert all("fail" in item for item in _strs(rank["candidates_excluded"]))
        # Withholding is per workload: the untouched slices still rank.
        assert _rank(report, "synthesis")["status"] == "ranked"
        assert _rank(report, "utility")["status"] == "ranked"

    def test_excluded_candidate_is_disclosed_in_the_workload_context(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _results_document(drop=("U08-mercury-r2",)))
        assert _section(report["totals"])["coverage_complete"] is False
        rank = _rank(report, "utility")
        assert rank["status"] == "ranked"
        assert _strs(rank["candidates_excluded"]) == ["mercury (incomplete)"]
        assert "mercury (incomplete)" in str(rank["context"])
        assert "mercury" not in [
            entry["candidate_label"] for entry in _rank_entries(report, "utility")
        ]
        assert _rank(report, "orchestration")["status"] == "ranked"

    def test_pilot_total_is_reported_with_the_all_attempt_denominator(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _cost_shaped_document())
        cost = _section(report["cost"], "cost")
        assert cost["attempts_in_cost_numerator"] == EXPECTED_ATTEMPTS
        assert _int_at(cost["attempts_with_known_cost"]) == EXPECTED_ATTEMPTS
        assert cost["cost_total_definitive"] is True
        assert "divisor is successful attempts only" in str(cost["denominator_rule"])


COST_PER_ATTEMPT = {
    "mercury": 0.005,
    "deepseek-flash": 0.01,
    "sol": 0.015,
    "luna": 0.02,
    "glm-flash": 0.03,
}


def _cost_overrides() -> dict[str, dict[str, Any]]:
    return {
        attempt.attempt_id: {"cost_usd": COST_PER_ATTEMPT[attempt.candidate_label]}
        for attempt in _attempts()
    }


def _cost_shaped_document() -> dict[str, Any]:
    return _results_document(overrides=_cost_overrides())


# --------------------------------------------------------------------------
# Latency reporting
# --------------------------------------------------------------------------


class TestLatencyReporting:
    def test_nearest_rank_percentile(self) -> None:
        values = [float(number) for number in range(1, 17)]
        assert percentile_nearest_rank(values, 0.95) == 16.0
        assert percentile_nearest_rank([5.0], 0.95) == 5.0
        assert percentile_nearest_rank([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
        assert percentile_nearest_rank([], 0.95) is None

    def test_slice_latency_reports_targets_and_coarse_granularity(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _results_document())
        assert _slice(report, "luna", "utility")["latency"]["p95_target_seconds"] == 10.0
        assert _slice(report, "luna", "orchestration")["latency"]["p95_target_seconds"] == 30.0
        assert _slice(report, "luna", "synthesis")["latency"]["p95_target_seconds"] == 60.0
        for workload in WORKLOADS:
            latency = _section(_slice(report, "luna", workload)["latency"], "latency")
            assert latency["p95_method"] == "nearest-rank"
            assert latency["granularity"] == "coarse"
            assert _int_at(latency["observations"]) == ATTEMPTS_PER_SLICE
            assert "not a production SLO" in str(latency["note"])
            assert latency["median_seconds"] == 4.0

    def test_over_target_latency_is_counted_against_the_candidate(self, tmp_path: Path) -> None:
        slow = {
            f"U0{number}-luna-r{repeat}": {"latency_seconds": 25.0}
            for number in range(1, 9)
            for repeat in (1, 2)
        }
        _, report = _score(tmp_path, _results_document(overrides=slow))
        latency = _section(_slice(report, "luna", "utility")["latency"], "latency")
        assert latency["p95_seconds"] == 25.0
        assert latency["observations_over_p95_target"] == ATTEMPTS_PER_SLICE
        assert latency["p95_target_seconds"] == LATENCY_P95_TARGETS_SECONDS["utility"]

    def test_latency_target_status_is_provisional(self, tmp_path: Path) -> None:
        _, report = _score(tmp_path, _results_document())
        thresholds = _section(report["thresholds"], "thresholds")
        assert thresholds["status"] == "proposed-unapproved"
        assert _section(thresholds["latency_p95_targets_seconds"]) == dict(
            LATENCY_P95_TARGETS_SECONDS
        )
        assert (
            _section(_slice(report, "luna", "utility")["latency"])["p95_target_status"]
            == "provisional-unapproved"
        )


# --------------------------------------------------------------------------
# Text rendering
# --------------------------------------------------------------------------


class TestTextRendering:
    def test_text_report_is_rendered_without_json_output(self, tmp_path: Path, capsys: Any) -> None:
        path = _write_results(tmp_path, _results_document())
        code = main(["score", "--results", str(path), "--fixtures", str(FIXTURES_PATH)])
        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert "slices (gate: 15/16 successes" in out
        assert "coarse, not an SLO" in out
        assert "cost rank per workload (cost per successful attempt, same case set)" in out
        assert "utility        1." in out
        assert "mercury" in out

    def test_text_report_states_the_withheld_workload_reason(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        document = _results_document(overrides={"O01-luna-r1": {"cost_usd": None}})
        path = _write_results(tmp_path, document)
        main(["score", "--results", str(path), "--fixtures", str(FIXTURES_PATH)])
        out = capsys.readouterr().out
        assert "orchestration  withheld — unknown or missing attempt cost" in out
        assert "synthesis      1." in out

    def test_text_report_names_incomplete_attempts(self, tmp_path: Path, capsys: Any) -> None:
        path = _write_results(tmp_path, _results_document(drop=("U01-luna-r1",)))
        code = main(["score", "--results", str(path), "--fixtures", str(FIXTURES_PATH)])
        assert code == EXIT_SCREENING_NOT_MET
        out = capsys.readouterr().out
        assert "incomplete attempts (1)" in out
        assert "U01-luna-r1: missing_result" in out

    def test_text_report_names_hard_violations(self, tmp_path: Path, capsys: Any) -> None:
        document = _results_document(
            overrides={"R08-sol-r1": {"hard_violation_kinds": ["unauthorized_action"]}}
        )
        path = _write_results(tmp_path, document)
        code = main(["score", "--results", str(path), "--fixtures", str(FIXTURES_PATH)])
        assert code == EXIT_SCREENING_NOT_MET
        out = capsys.readouterr().out
        assert "hard violations (1)" in out
        assert "R08-sol-r1: unauthorized_action" in out
