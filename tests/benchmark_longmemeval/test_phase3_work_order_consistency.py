from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import cast

from tests.benchmark_longmemeval.taxonomy import build_taxonomy_entries, load_failure_rows


REPORT_PATH = Path(__file__).with_name("PHASE3_WORK_ORDER.md")


JSONDict = dict[str, object]


def _load_report_text() -> str:
    return REPORT_PATH.read_text()


def _load_machine_summary() -> JSONDict:
    text = _load_report_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "PHASE3_WORK_ORDER.md is missing its JSON summary"
    return cast(JSONDict, json.loads(match.group(1)))


def _as_int(value: object) -> int:
    assert isinstance(value, int)
    return value


def _cell_counts() -> Counter[tuple[str, str]]:
    entries = build_taxonomy_entries(failure_rows=load_failure_rows())
    return Counter((str(entry["stage"]), str(entry["category"])) for entry in entries)


def _category_counts() -> Counter[str]:
    entries = build_taxonomy_entries(failure_rows=load_failure_rows())
    return Counter(str(entry["category"]) for entry in entries)


def _items(summary: JSONDict, key: str) -> list[JSONDict]:
    raw_items = summary[key]
    assert isinstance(raw_items, list)
    return [cast(JSONDict, item) for item in cast(list[object], raw_items)]


def _named(items: list[JSONDict], name: str) -> JSONDict:
    for item in items:
        if item["name"] == name:
            return item
    raise AssertionError(f"Missing entry for {name}")


def test_phase3_work_order_mentions_required_guardrails_and_tokens() -> None:
    text = _load_report_text()
    lowered = text.lower()

    assert "subset-veto rule" in lowered
    assert "no-model-swap rule" in lowered
    assert "rank" in lowered
    assert "estimated leverage" in lowered
    assert "dependency" in lowered
    assert "TOP_K_MEMORIES" in text
    assert "MAX_RETURNED_MEMORIES" in text
    assert "abstention prompt hardening" in lowered
    assert "extraction-miss-only sweeps" in lowered


def test_phase3_work_order_consistency() -> None:
    summary = _load_machine_summary()
    cell_counts = _cell_counts()
    category_counts = _category_counts()

    guardrails = cast(JSONDict, summary["guardrails"])
    assert guardrails["subset_veto_rule_preserved"] is True
    assert _as_int(guardrails["subset_veto_min_locked_cases"]) == 5
    assert _as_int(guardrails["dev_subset_primary_cell_floor"]) == 5
    assert guardrails["no_model_swaps"] is True
    assert guardrails["no_residual_portable_historical_advantages"] is True
    assert (
        guardrails["active_retrieval_ceiling_authority"]
        == "tests.longmemeval.evaluate.TOP_K_MEMORIES"
    )
    assert guardrails["inactive_literal_max_returned_memories"] is True

    ordered = _items(summary, "ordered_candidates")
    assert [_as_int(item["rank"]) for item in ordered] == list(range(1, len(ordered) + 1))

    approved = [item for item in ordered if item["status"] == "approved"]
    assert [_as_int(item["rank"]) for item in approved] == [1, 2, 3, 4, 5]
    assert {str(item["name"]) for item in approved} == {
        "top_k_memories_sweep",
        "min_final_score_sweep",
        "initial_vector_candidates_sweep",
        "hybrid_ranking_weight_sweep",
        "temporal_filter_integration",
    }

    for item in ordered:
        target = cast(JSONDict, item["target_failure_cell"])
        stage = str(target["stage"])
        category = str(target["category"])
        count = _as_int(target["count"])

        assert cell_counts[(stage, category)] == count
        assert cast(list[object], target["representative_ids"])
        assert str(item["estimated_leverage"]).strip()
        assert str(item["dependency"]).strip()
        assert str(item["evidence_basis"]).strip()
        assert str(item["promotion_rationale"]).strip()
        assert str(item["blocking_rationale"]).strip()

        evidence_sources = {str(source) for source in cast(list[object], item["evidence_sources"])}
        assert evidence_sources & {"taxonomy", "portable_advantages"}

        status = str(item["status"])
        coverage_gate = str(item["coverage_gate"])
        if count < _as_int(guardrails["subset_veto_min_locked_cases"]):
            assert status.startswith("blocked_")
            assert coverage_gate == "blocked_insufficient_target_cell"
        elif str(item["name"]) == "entity_alias_audit_toggle":
            assert status == "blocked_audit_first"
            assert coverage_gate == "pass"
        else:
            assert status == "approved"
            assert coverage_gate == "pass"

    dedup = _named(ordered, "dedup_threshold_sensitivity")
    assert dedup["status"] == "blocked_insufficient_target_cell"
    dedup_target = cast(JSONDict, dedup["target_failure_cell"])
    assert dedup_target["count"] == 2

    alias = _named(ordered, "entity_alias_audit_toggle")
    assert alias["status"] == "blocked_audit_first"

    contradictions = _items(summary, "contradictions")
    assert {
        "retrieval_ceiling_direction_conflict",
        "ranking_knob_without_historical_delta",
        "dedup_freshness_conflict",
        "model_swap_conflict",
    } <= {str(item["name"]) for item in contradictions}

    history_vetoes = _items(summary, "history_vetoes")
    assert {
        "historical_fast_chunking_4000_2",
        "historical_top_k_5_restore",
        "literal_MAX_RETURNED_MEMORIES_sweep",
        "embedding_route_changes",
        "answer_or_judge_model_swaps",
        "portable_advantages_phase2_replay",
    } <= {str(item["name"]) for item in history_vetoes}
    for item in history_vetoes:
        assert str(item["status"]).startswith("blocked_")
        evidence_sources = {str(source) for source in cast(list[object], item["evidence_sources"])}
        assert "portable_advantages" in evidence_sources or evidence_sources & {
            "judge_drift",
            "contamination_analysis",
        }

    coverage_vetoes = _items(summary, "additional_coverage_vetoes")
    abstention = _named(coverage_vetoes, "abstention_prompt_hardening")
    abstention_reference = cast(JSONDict, abstention["coverage_reference"])
    assert abstention["status"] == "blocked_insufficient_target_cell"
    assert abstention_reference["type"] == "category_total"
    assert abstention_reference["category"] == "abstention"
    assert _as_int(abstention_reference["count"]) == category_counts["abstention"] == 2
    abstention_secondary = cast(JSONDict, abstention_reference["secondary_cell"])
    assert (
        _as_int(abstention_secondary["count"])
        == cell_counts[("generation-error", "temporal-reasoning")]
        == 3
    )

    extraction = _named(coverage_vetoes, "extraction_miss_only_retrieval_or_dedup_sweeps")
    extraction_reference = cast(JSONDict, extraction["coverage_reference"])
    assert extraction["status"] == "blocked_insufficient_target_cell"
    assert extraction_reference["type"] == "target_failure_cell"
    assert (
        _as_int(extraction_reference["count"])
        == cell_counts[("extraction-miss", "single-session-assistant")]
        == 4
    )
