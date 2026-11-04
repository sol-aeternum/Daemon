"""Pre-registered retrieval audit rules, not a production retrieval candidate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from orchestrator.eval import exposure

POOLS = (10, 25, 50, 100)
WIDTHS = (5, 10)
MODES = ("harness", "probes10", "exact")
VARIABLE_PRIORITIES = {"probes": 0, "pool": 1, "width": 2}
PRIORITY_PADDING = 3
PROBES_STEPS = {"harness": 0, "probes10": 1, "exact": 2}
POOL_STEPS = {10: 0, 25: 1, 50: 2, 100: 3}
WIDTH_STEPS = {5: 0, 10: 1}
EXPECTED_PROBES = 10
REQUIRED_CASES = exposure.REQUIRED_CASES
PARITY_GAP = "PARITY_GAP"
RETRIEVAL_CANDIDATE = "RETRIEVAL_CANDIDATE"
QUERY_FORMULATION_CONFIRMED = "QUERY_FORMULATION_CONFIRMED"
COVERAGE_REGRESSION = "COVERAGE_REGRESSION"
BUDGET_LIMITED = "BUDGET_LIMITED"
NONE_OF_THE_ABOVE = "NONE_OF_THE_ABOVE"
SCORER_COMPONENTS = (
    "vector_sim",
    "bm25_score",
    "bm25_normalized",
    "recency_boost",
    "source_boost",
    "access_boost",
    "confidence",
    "trust_score",
    "final_score",
)


@dataclass(frozen=True)
class Configuration:
    pool: int
    mode: str
    k: int

    def __post_init__(self) -> None:
        if self.pool not in POOLS or self.mode not in MODES or self.k not in WIDTHS:
            raise ValueError("Unregistered configuration")

    @property
    def key(self) -> str:
        return f"pool{self.pool}_{self.mode}_k{self.k}"

    def sort_key(self, harness_probes: int) -> tuple[int, tuple[int, ...], tuple[int, ...]]:
        """Owner-approved fixed-length key; collapsed probes10 is baseline."""
        probes_step = (
            0
            if self.mode == "probes10" and harness_probes == EXPECTED_PROBES
            else PROBES_STEPS[self.mode]
        )
        steps = (probes_step, POOL_STEPS[self.pool], WIDTH_STEPS[self.k])
        priorities = sorted(
            VARIABLE_PRIORITIES[name]
            for name, changed in (
                ("probes", probes_step != 0),
                ("pool", self.pool != 10),
                ("width", self.k != 5),
            )
            if changed
        )
        return (
            len(priorities),
            tuple(priorities + [PRIORITY_PADDING] * (3 - len(priorities))),
            steps,
        )


BASELINE = Configuration(10, "harness", 5)


def configurations(harness_probes: int) -> list[Configuration]:
    if harness_probes < 1:
        raise ValueError("Invalid harness probes")
    modes = ("harness", "exact") if harness_probes == EXPECTED_PROBES else MODES
    return [Configuration(pool, mode, k) for pool in POOLS for mode in modes for k in WIDTHS]


def summarize_grid(questions: Sequence[Mapping[str, Any]], config: Configuration) -> dict[str, Any]:
    """Reuse the sweep's aggregation AND coverage-loss causes without changes."""
    adapted = []
    for question in questions:
        adapted.append(
            {
                "question_id": question["question_id"],
                "source_conversations": question["source_conversations"],
                "widths": {
                    str(k): question["configs"][BASELINE.key if k == 5 else config.key]
                    for k in exposure.SWEEP_KS
                },
            }
        )
    summary = exposure.summarize(adapted)[exposure.CANDIDATE_KS[0]]
    summary["config"] = asdict(config)
    summary["empty_retrievals"] = sum(
        question["configs"][config.key]["empty_retrieval"] for question in questions
    )
    summary["distractor_measured_ids"] = sorted(
        question["question_id"] for question in questions if question["distractors_measured"]
    )
    summary["distractor_measured_questions"] = len(summary["distractor_measured_ids"])
    summary["distractors_by_question"] = {
        question["question_id"]: question["configs"][config.key]["distractors"]
        for question in questions
    }
    summary["distractors"] = _measured_distractors(summary, summary["distractor_measured_ids"])
    return summary


def _measured_distractors(
    row: Mapping[str, Any], measured_ids: Sequence[str]
) -> dict[str, int] | None:
    """Validate the measured cohort, preserving None outside it (never zero-fill)."""
    measured = set(measured_ids)
    if (
        len(measured) != len(measured_ids)
        or row["distractor_measured_ids"] != sorted(measured)
        or row["distractor_measured_questions"] != len(measured)
        or set(row["distractors_by_question"]) != set(row["coverage"])
        or not measured <= set(row["coverage"])
    ):
        raise ValueError("Distractor measurement cohort mismatch")
    totals = {"added": 0, "plausible_support": 0, "own_plausible_support": 0}
    for qid, payload in row["distractors_by_question"].items():
        if qid not in measured:
            if payload is not None:
                raise ValueError("Unmeasured distractors must remain None")
            continue
        if not isinstance(payload, Mapping) or any(
            type(payload.get(name)) is not int or payload[name] < 0 for name in totals
        ):
            raise ValueError(f"Missing or invalid measured distractor payload: {qid}")
        for name in totals:
            totals[name] += payload[name]
    return totals if measured else None


def decide_exactness(
    summaries: Mapping[str, Mapping[str, Any]],
    *,
    connection_probes: Mapping[str, int],
    plan_shape_differences: Sequence[str],
    missing_target_ranks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a SET of independent flags plus the registered candidate details.

    Serialization converts only the flags set to a sorted list. Neither parity
    nor abstention warnings veto an otherwise qualifying retrieval candidate.
    """
    if set(connection_probes) != {"harness", "campaign", "sweep"}:
        raise ValueError("Require all three connection probes")
    configs = configurations(connection_probes["harness"])
    if set(summaries) != {config.key for config in configs}:
        raise ValueError("Require the complete collapsed grid")
    baseline = summaries[BASELINE.key]
    if len(baseline["coverage"]) != exposure.QUESTION_COUNT:
        raise ValueError("Require 50 baseline questions")
    baseline_distractors = _measured_distractors(baseline, baseline["distractor_measured_ids"])
    if baseline["distractors"] != baseline_distractors:
        raise ValueError("Baseline distractor aggregate differs from measured payloads")
    flags: set[str] = set()
    if (
        any(value != EXPECTED_PROBES for value in connection_probes.values())
        or plan_shape_differences
    ):
        flags.add(PARITY_GAP)
    candidates = []
    for config in sorted(configs, key=lambda item: item.sort_key(connection_probes["harness"])):
        row = summaries[config.key]
        distractors = _measured_distractors(row, baseline["distractor_measured_ids"])
        if row["distractors"] != distractors:
            raise ValueError("Distractor aggregate differs from measured payloads")
        if not 0 <= row["cases_strict"] <= row["cases_pre_budget"] <= exposure.CASE_COUNT:
            raise ValueError("Require 0 <= R <= P <= 5")
        if row["target_budget_dropped"] < 0:
            raise ValueError("Negative target drops")
        losses = exposure.coverage_losses(baseline["coverage"], row["coverage"])
        if losses and any(
            row.get("coverage_causes", {}).get(qid)
            not in {"prefix_instability", "non_target_budget_drop"}
            for qid in losses
        ):
            raise ValueError("Missing coverage-loss attribution")
        meets = row["cases_strict"] >= REQUIRED_CASES and row["target_budget_dropped"] == 0
        if meets and losses:
            flags.add(COVERAGE_REGRESSION)
        if meets and not losses and config != BASELINE:
            candidates.append(
                {
                    "id": config.key,
                    "config": asdict(config),
                    "token_delta": row["rendered_tokens"] - baseline["rendered_tokens"],
                    "distractor_measured_questions": row["distractor_measured_questions"],
                    "distractor_deltas": {
                        name: distractors[name] - baseline_distractors[name] for name in distractors
                    }
                    if distractors is not None and baseline_distractors is not None
                    else None,
                }
            )
        if row["cases_pre_budget"] >= REQUIRED_CASES and row["target_budget_dropped"] > 0:
            flags.add(BUDGET_LIMITED)
    if candidates:
        flags.add(RETRIEVAL_CANDIDATE)
    if summaries[Configuration(100, "exact", 10).key]["cases_strict"] < REQUIRED_CASES:
        flags.add(QUERY_FORMULATION_CONFIRMED)
    if not flags - {PARITY_GAP}:
        flags.add(NONE_OF_THE_ABOVE)
    return {
        "flags": flags,
        "minimal": candidates[0]["id"] if candidates else None,
        "candidates": candidates,
        "missing_target_ranks": list(missing_target_ranks)
        if QUERY_FORMULATION_CONFIRMED in flags
        else [],
    }


def plan_summary(value: str | list[Any]) -> dict[str, Any]:
    parsed = json.loads(value) if isinstance(value, str) else value
    shape: list[dict[str, Any]] = []

    def visit(node: Mapping[str, Any], path: str) -> None:
        shape.append(
            {
                "path": path,
                "node": node["Node Type"].replace(" ", "_"),
                "index": node.get("Index Name"),
            }
        )
        for pos, child in enumerate(node.get("Plans", [])):
            visit(child, f"{path}.{pos}")

    visit(parsed[0]["Plan"], "0")
    indexes = sorted({row["index"] for row in shape if row["index"]})
    ivfflat = "idx_memories_embedding_ivfflat" in indexes
    return {
        "shape": shape,
        "indexes": indexes,
        "ivfflat_used": ivfflat,
        "exact_non_ivfflat_plan": not ivfflat,
        "shape_sha256": hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest(),
    }


def pool_stability(scored: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """Compare scorer outputs on a fixed union, marking unscored members unknown.

    Missing candidates have no scorer output at that pool: do not impute scores
    or silently compare different populations. Also compare every common pair's
    relative order, and report changed input components (not invented causes).
    """
    pools = sorted(scored, key=int)
    maps = {pool: {str(row["id"]): row for row in scored[pool]} for pool in pools}
    union = sorted({mid for rows in maps.values() for mid in rows})
    comparisons = []
    for left_pos, left in enumerate(pools):
        for right in pools[left_pos + 1 :]:
            common = set(maps[left]) & set(maps[right])
            orders = {
                pool: sorted(common, key=lambda mid: (-float(maps[pool][mid]["final_score"]), mid))
                for pool in (left, right)
            }
            changes = {
                mid: [
                    name
                    for name in SCORER_COMPONENTS
                    if maps[left][mid].get(name) != maps[right][mid].get(name)
                ]
                for mid in sorted(common)
            }
            comparisons.append(
                {
                    "left": left,
                    "right": right,
                    "common_count": len(common),
                    "order_changed": orders[left] != orders[right],
                    "changed_components": {mid: names for mid, names in changes.items() if names},
                }
            )
    complete = all(set(rows) == set(union) for rows in maps.values())
    return {
        "union_ids": union,
        "fixed_union_order_observable": complete,
        "fixed_union_order_changed": any(row["order_changed"] for row in comparisons)
        if complete
        else None,
        "missing_from_pool": {pool: sorted(set(union) - set(rows)) for pool, rows in maps.items()},
        "components": {
            pool: {
                mid: {name: row.get(name) for name in SCORER_COMPONENTS}
                for mid, row in rows.items()
            }
            for pool, rows in maps.items()
        },
        "comparisons": comparisons,
    }
