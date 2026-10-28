"""Offline integrity checks for the ratified metric and DB-backed triage record.

These check provenance/coverage, not causal improvement or semantic judge validity.
No database, provider, environment key or full holdout dataset is used.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "tests/benchmark_results"
TRIAGE = RESULTS / "failure_triage_phase2_2026-10-28"
DEV = ROOT / "tests/benchmark_longmemeval/fixtures/dev_subset.json"


def load(path: Path):
    return json.loads(path.read_text())


def test_ratification_retains_thresholds_and_qualification_limits() -> None:
    rule = (RESULTS / "ACCEPTANCE_RULE_PROPOSED.md").read_text()
    assert "Ratified by the owner on 2026-10-28" in rule
    assert "2026-10-28-official-v1" in rule
    assert "official category/abstention-aware strict accuracy" in rule
    assert "secondary regression report" in rule
    assert "without exemptions" in rule
    for threshold in (
        "max(3, 2 × *F*)",
        "max(4, 2 × *G*)",
        "No category loses more than 2 questions",
        "do not lose any net",
        "fewer than 1%",
        "state_unchanged_by_evaluate",
    ):
        assert threshold in rule
    assert "do not replace the full" in rule
    assert "block acceptance" in rule


def test_every_non_correct_primary_a_a2_observation_is_covered() -> None:
    payload = load(TRIAGE / "cases.json")
    cases = {row["question_id"]: row for row in payload["cases"]}
    assert len(cases) == len(payload["cases"]) == 16
    assert payload["rule_version"] == "2026-10-28-official-v1"
    assert payload["primary_metric"] == "official_strict"
    observations = 0
    for arm in ("a", "a2"):
        official = [
            json.loads(line)
            for line in (
                RESULTS
                / "measurement_phase1_2026-10-28"
                / f"official_{arm}_1"
                / "longmemeval_results.jsonl"
            )
            .read_text()
            .splitlines()
        ]
        failed = {r["question_id"]: r for r in official if r["official_judgment"] != "correct"}
        assert set(cases) == set(failed)
        for qid, source in failed.items():
            row = cases[qid]["observations"][arm]
            assert row["official_judgment"] == source["official_judgment"]
            assert row["hypothesis"] == source["hypothesis"]
            assert row["memory_context_sha256"] == source["memory_context_sha256"]
            assert row["system_prompt_sha256"] == source["system_prompt_sha256"]
            assert (
                hashlib.sha256(row["memory_context"].encode()).hexdigest()
                == row["memory_context_sha256"]
            )
            observations += 1
    assert observations == payload["coverage"]["arm_observations"] == 32
    assert (
        dict(Counter(row["classification"] for row in cases.values()))
        == payload["classification_counts_unique_questions"]
    )
    assert all(
        row["diagnosis"] and row["falsification_check_and_boundary"] for row in cases.values()
    )


def test_db_provenance_and_case_membership_match_qualified_ingest() -> None:
    provenance = load(TRIAGE / "db_evidence_manifest.json")
    preflight = load(TRIAGE / "db_preflight.json")
    post_gate = load(TRIAGE / "db_post_gate_check.json")
    assert post_gate["after_full_local_gates"] is True and post_gate["unchanged"] is True
    assert post_gate["transaction_read_only"] == "on"
    assert post_gate["fingerprint"] == provenance["fingerprint"]
    baseline = load(RESULTS / "longmemeval_phase_d_2026-10-27/eval_a_qualified/run_manifest.json")
    assert provenance["fingerprint"] == baseline["phases"]["evaluate"]["state_fingerprint_start"]
    assert (
        provenance["fingerprint"]
        == preflight["expected_ingest_fingerprint"]
        == preflight["observed_fingerprint"]
    )
    assert preflight["matches"] is True and preflight["transaction_read_only"] == "on"
    assert provenance["transaction_read_only"] is True
    assert provenance["snapshot_state_matches_qualified_ingest"] is True
    assert provenance["mutations_or_provider_calls"] == 0
    assert hashlib.sha256(DEV.read_bytes()).hexdigest() == provenance["dev_sha256"]
    inventory = {r["question_id"]: r for r in provenance["cases"]}
    for case in load(TRIAGE / "cases.json")["cases"]:
        assert case["db_evidence_sha256"] == inventory[case["question_id"]]["sha256"]
        assert case["db_memories_examined"] == inventory[case["question_id"]]["memories"]
        assert all(s["status"] == "complete" for s in case["support_sessions"])
        assert (
            case["matching_retrieval_logs_available"]
            == inventory[case["question_id"]]["retrieval_logs"]
            == 0
        )
    summary = load(TRIAGE / "embedding_summary.json")
    assert (
        summary["rows_checked"]
        == summary["with_embedding"]
        == sum(c["memories"] for c in provenance["cases"])
    )
    assert summary["local_only_or_unknown_rows"] == 0


def test_bounded_source_excerpts_are_from_that_dev_questions_support() -> None:
    dataset = {r["question_id"]: r for r in load(DEV)}
    for case in load(TRIAGE / "cases.json")["cases"]:
        entry = dataset[case["question_id"]]
        assert case["question"] == entry["question"]
        assert case["reference"] == entry["answer"]
        support = {
            hashlib.sha256(m["content"].encode()).hexdigest(): m["content"]
            for sid, session in zip(
                entry["haystack_session_ids"], entry["haystack_sessions"], strict=True
            )
            if sid in entry["answer_session_ids"]
            for m in session
        }
        for excerpt in case["source_message_excerpts"]:
            assert excerpt["full_content_sha256"] in support
            assert excerpt["excerpt"] in support[excerpt["full_content_sha256"]]
        for memory in case["source_memories"]:
            assert memory["status"] == "active" and memory["valid_to"] is None
            assert memory["local_only"] is False and memory["has_embedding"] is True


def test_classification_caveats_do_not_turn_into_metric_overrides() -> None:
    cases = {r["question_id"]: r for r in load(TRIAGE / "cases.json")["cases"]}
    assert cases["09d032c9"]["classification"] == "rubric-ambiguity"
    assert all(
        r["official_judgment"] == "incorrect" for r in cases["09d032c9"]["observations"].values()
    )
    assert "do not silently rescore" in cases["09d032c9"]["falsification_check_and_boundary"]
    for case in cases.values():
        if case["classification"] == "prompt-selection-gap":
            for row in case["observations"].values():
                assert any(
                    m["content"] not in row["memory_context"] for m in case["source_memories"]
                )
        if case["classification"] == "temporal-grounding-gap":
            assert all(
                m["metadata"] == {} and m["valid_from"].startswith("2026-")
                for m in case["source_memories"]
            )
            assert case["question_date"].startswith("2023/")
