"""Frozen eval-only query fusion and exposure decision; never a runtime candidate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from typing import Any

from orchestrator.eval import decomposition_generation as generation
from orchestrator.eval import exposure
from orchestrator.memory import injection

WIDTHS = (5, 10)
MERGES = ("rrf", "round_robin", "union_rescore")
SOURCES = ("generated", "oracle")
DEPTH = 10
RRF_CONSTANT = 60
MAX_LOGICAL_CALLS = 50
MAX_429_RETRIES = 2
MAX_PARSE_RETRIES = 1
MAX_EMBEDDINGS = 165
COST_CAP_USD = "0.10"
ORACLE_LABEL = "ORACLE — contaminated by knowledge of the cases; ceiling only"
FROZEN_NAME = "memory_query_decomposition_frozen_design_2026-11-03.json"
BASELINE = "original_k5"


def configuration(source: str, merge: str, k: int) -> str:
    if source not in SOURCES or merge not in MERGES or k not in WIDTHS:
        raise ValueError("Unregistered decomposition configuration")
    return f"{source}_{merge}_k{k}"


CONFIGS = tuple(
    configuration(source, merge, k) for source in SOURCES for merge in MERGES for k in WIDTHS
)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def frozen_design() -> bytes:
    prompt = generation.SYSTEM_PROMPT + "\n" + generation.USER_TEMPLATE
    return canonical(
        {
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "schema": generation.SCHEMA,
            "route_id": generation.ROUTE_ID,
            "model": generation.MODEL,
            "profile": "background",
            "parameters": {
                "seed": generation.SEED,
                "response_format": generation.generate_request("question", "2026-11-03")[
                    "response_format"
                ],
                "max_tokens": generation.MAX_TOKENS,
                "stream": False,
                "reasoning": "profile_default_no_override",
                "temperature": "omitted",
                "other_extra_parameters": [],
            },
            "retry_policy": {
                "http_429": MAX_429_RETRIES,
                "parse": MAX_PARSE_RETRIES,
                "identical_request": True,
            },
            "logical_call_cap": MAX_LOGICAL_CALLS,
            "query_cap": generation.MAX_QUERIES,
            "embedding_cap": MAX_EMBEDDINGS,
            "cost_cap_usd": COST_CAP_USD,
            "depth": DEPTH,
            "rrf_constant": RRF_CONSTANT,
            "rrf_ties": ["max_production_final_score_desc", "memory_id_asc"],
            "merges": {
                "rrf": "sum_1_over_60_plus_one_based_rank",
                "round_robin": "query_order_original_first_skip_duplicates",
                "union_rescore": "max_production_final_score_desc_then_memory_id_asc",
            },
            "widths": WIDTHS,
            "decision": {
                "required_cases": exposure.REQUIRED_CASES,
                "case_count": exposure.CASE_COUNT,
                "question_count": exposure.QUESTION_COUNT,
                "baseline": BASELINE,
                "oracle_ceiling_veto": True,
                "unrenderable_pre_budget_target": "invalid_stop_with_case_and_memory_id",
                "flags": [
                    "CEILING_FAILS",
                    "DECOMPOSITION_CANDIDATE",
                    "BUDGET_LIMITED",
                    "MODEL_GAP",
                    "MERGE_LEAD",
                    "COVERAGE_REGRESSION",
                ],
            },
            "decomposition_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "generation_sha256": hashlib.sha256(Path(generation.__file__).read_bytes()).hexdigest(),
        }
    )


def query_set(original: str, generated: Sequence[str]) -> list[str]:
    if len(generated) > generation.MAX_QUERIES:
        raise ValueError("Too many sub-queries")
    queries = list(
        dict.fromkeys(injection._normalize_content(text) for text in [original, *generated])
    )
    if not queries or any(not text for text in queries):
        raise ValueError("Empty normalized query")
    return queries


def fuse(rankings: Sequence[Sequence[Mapping[str, Any]]], merge: str) -> list[dict[str, Any]]:
    """Ranks are 1-based; repeated IDs contribute at most once in each query."""
    if merge not in MERGES or not 1 <= len(rankings) <= 4:
        raise ValueError("Invalid fusion inputs")
    rows: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    rrf: dict[str, Fraction] = {}
    interleaved: list[str] = []
    for ranking in rankings:
        seen: set[str] = set()
        for rank, memory in enumerate(ranking, 1):
            mid = str(memory["id"])
            if mid in seen:
                continue
            seen.add(mid)
            score = float(memory["final_score"])
            if mid not in scores or score > scores[mid]:
                rows[mid], scores[mid] = dict(memory), score
            rrf[mid] = rrf.get(mid, Fraction()) + Fraction(1, RRF_CONSTANT + rank)
    if merge == "round_robin":
        seen = set()
        for pos in range(max((len(ranking) for ranking in rankings), default=0)):
            for ranking in rankings:
                if pos < len(ranking):
                    mid = str(ranking[pos]["id"])
                    if mid not in seen:
                        seen.add(mid)
                        interleaved.append(mid)
        ids = interleaved
    else:
        ids = sorted(
            rows,
            key=lambda mid: (
                (-rrf[mid], -scores[mid], mid) if merge == "rrf" else (-scores[mid], mid)
            ),
        )
    return [rows[mid] for mid in ids]


def substitute(
    inputs: injection.MemoryContextInputs, rows: Sequence[Mapping[str, Any]], k: int
) -> injection.MemoryContextInputs:
    if k not in WIDTHS:
        raise ValueError("Unregistered width")
    return replace(inputs, retrieved=[dict(row) for row in rows[:k]])


def check_case(case_id: str, config: str, row: Mapping[str, Any]) -> None:
    """Owner ruling: a named pre-budget target must render or be budget-dropped."""
    pre = set(row["pre_budget_ids"])
    rendered = set(row["rendered_ids"])
    dropped = set(row["budget_dropped_ids"])
    targets = row["targets"]
    if not targets:
        raise ValueError(f"Empty targets: {case_id} {config}")
    for mid, status in targets.items():
        if mid in pre and mid not in rendered | dropped:
            raise ValueError(f"Unrenderable target: case={case_id} memory={mid} config={config}")
        if status["rendered"] != (mid in rendered) or status["budget_dropped"] != (mid in dropped):
            raise ValueError(f"Inconsistent target: case={case_id} memory={mid} config={config}")
    if (
        row["target_pre_budget"] != (set(targets) <= pre)
        or row["target_exposed"] != (set(targets) <= rendered)
        or row["target_budget_dropped"] != len(set(targets) & dropped)
    ):
        raise ValueError(f"Inconsistent case counts: {case_id} {config}")


def summarize(questions: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    """Reuse exposure's complete coverage and regression attribution definitions."""
    adapted = [
        {
            "question_id": question["question_id"],
            "source_conversations": question["source_conversations"],
            "widths": {
                str(k): question["configs"][BASELINE if k == 5 else key] for k in exposure.SWEEP_KS
            },
        }
        for question in questions
    ]
    row = exposure.summarize(adapted)[10]
    row["distractors"] = {
        name: sum(
            question["configs"][key]["distractors"][name]
            for question in questions
            if question["configs"][key]["distractors"] is not None
        )
        for name in ("added", "plausible_support", "own_plausible_support")
    }
    row["distractor_measured_questions"] = sum(
        question["configs"][key]["distractors"] is not None for question in questions
    )
    return row


def decide_decomposition(questions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute the binding non-exclusive flag set only after per-target validation."""
    if (
        len(questions) != exposure.QUESTION_COUNT
        or len({q["question_id"] for q in questions}) != exposure.QUESTION_COUNT
    ):
        raise ValueError("Require 50 distinct questions")
    cases = [q for q in questions if q["configs"][BASELINE]["targets"]]
    if len(cases) != exposure.CASE_COUNT:
        raise ValueError("Require five target cases")
    for question in questions:
        if set(question["configs"]) != {BASELINE, *CONFIGS}:
            raise ValueError("Require the complete configuration grid")
        expected_targets = set(question["configs"][BASELINE]["targets"])
        for key, row in question["configs"].items():
            if set(row["targets"]) != expected_targets:
                raise ValueError("Target set changed across configurations")
            if expected_targets:
                check_case(question["question_id"], key, row)
    summaries = {key: summarize(questions, key) for key in (BASELINE, *CONFIGS)}
    baseline = summaries[BASELINE]
    ceiling_fails = all(
        summaries[configuration("oracle", "rrf", k)]["cases_strict"] < exposure.REQUIRED_CASES
        for k in WIDTHS
    )
    flags: set[str] = {"CEILING_FAILS"} if ceiling_fails else set()

    def qualifies(key: str) -> bool:
        row = summaries[key]
        return (
            row["cases_strict"] >= exposure.REQUIRED_CASES
            and row["target_budget_dropped"] == 0
            and not row["coverage_losses"]
        )

    candidates = [
        configuration("generated", "rrf", k)
        for k in WIDTHS
        if qualifies(configuration("generated", "rrf", k))
    ]
    if candidates:
        flags.add("DECOMPOSITION_CANDIDATE")
    if any(
        summaries[configuration("generated", "rrf", k)]["cases_pre_budget"]
        >= exposure.REQUIRED_CASES
        and summaries[configuration("generated", "rrf", k)]["target_budget_dropped"] > 0
        for k in WIDTHS
    ):
        flags.add("BUDGET_LIMITED")
    if not ceiling_fails and all(
        summaries[configuration("generated", "rrf", k)]["cases_pre_budget"]
        < exposure.REQUIRED_CASES
        for k in WIDTHS
    ):
        flags.add("MODEL_GAP")
    leads = [
        configuration("generated", merge, k)
        for merge in MERGES[1:]
        for k in WIDTHS
        if qualifies(configuration("generated", merge, k))
    ]
    if not candidates and leads:
        flags.add("MERGE_LEAD")
    if any(
        row["cases_strict"] >= exposure.REQUIRED_CASES
        and row["target_budget_dropped"] == 0
        and row["coverage_losses"]
        for key, row in summaries.items()
        if key.startswith("generated_")
    ):
        flags.add("COVERAGE_REGRESSION")
    if not flags:
        raise ValueError("Decision totality invariant failed")
    selected = candidates[0] if candidates else None
    return {
        "flags": sorted(flags),
        "candidate": selected,
        "vetoed": bool(selected and ceiling_fails),
        "proposal": selected if selected and not ceiling_fails else None,
        "merge_leads": leads if not candidates else [],
        "token_delta": summaries[selected]["rendered_tokens"] - baseline["rendered_tokens"]
        if selected
        else None,
        "distractor_deltas": {
            name: summaries[selected]["distractors"][name] - baseline["distractors"][name]
            for name in baseline["distractors"]
        }
        if selected
        else None,
        "oracle_coverage_losses": {
            key: row["coverage_losses"]
            for key, row in summaries.items()
            if key.startswith("oracle_")
        },
    }
