"""Select the fresh dev2 evaluation set and estimate its bounded cost.

Selection is metadata-only and IDs-only, following ``holdout.py``: it reads
``question_id``, ``question_type`` and ``answer_session_ids`` and nothing else.
The estimate reads per-question session counts (session IDs, turn counts and
character counts) for the selected dev2 rows only; it never reads holdout rows,
question text or answer text, and it emits numbers only.

No prices are embedded. ``estimate`` takes every unit cost as an explicit input
and reports arithmetic over those inputs, so each cost, duration and restore
count is an estimate, not a measured value. Nothing here ingests, downloads,
calls a provider or touches a database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from tests.benchmark_longmemeval.holdout import (
    METADATA_FIELDS,
    QuestionMetadata,
    project_metadata,
)

# Selection is restricted to the types a memory-system change can plausibly move.
ELIGIBLE_TYPES: Final[frozenset[str]] = frozenset(
    {"multi-session", "temporal-reasoning", "knowledge-update"}
)
# Distinct from ``holdout.ALGORITHM`` so the two rank streams cannot collide.
ALGORITHM: Final[str] = "dev2-eligible-type-x-abstention-largest-remainder-sha256-v1"
CHARS_PER_TOKEN: Final[int] = 4
SPOT_CHECK_PERCENT: Final[int] = 15
IDS_HASH_ENCODING: Final[str] = "sorted UTF-8 IDs, one per line, final newline"

MANIFEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "algorithm",
        "seed",
        "size",
        "selection_fields",
        "dataset_count",
        "dev_count",
        "holdout_count",
        "excluded_dev_ids",
        "excluded_holdout_ids",
        "excluded_shared_answer_sessions_only",
        "excluded_holdout_shared_answer_sessions_only",
        "excluded_ineligible_types",
        "eligible_count",
        "eligible_strata",
        "selected_strata",
        "question_ids",
        "question_ids_sha256",
        "question_ids_hash_encoding",
        "holdout_ids_sha256",
        "holdout_ids_hash_encoding",
        "estimate",
        "dataset_sha256",
        "dev_sha256",
        "holdout_sha256",
        "unit_costs_sha256",
    }
)

ESTIMATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "chars_per_token_approximation",
        "estimated_questions",
        "estimated_ingest_sessions",
        "estimated_ingest_turns",
        "estimated_ingest_chars",
        "estimated_tokens",
        "estimated_extraction_usd",
        "estimated_embedding_usd",
        "estimated_extraction_seconds",
        "estimated_embedding_seconds",
        "estimated_db_restores",
        "estimated_db_restore_seconds",
        "estimated_wall_time_seconds",
        "labelling_spot_check_fraction",
        "estimated_labelling_spot_check_questions",
        "estimated_labelling_seconds",
        "estimated_labelling_minutes",
        "session_count_conflicts",
    }
)

UNIT_COST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "extraction_usd_per_1k_tokens",
        "embedding_usd_per_1k_tokens",
        "extraction_seconds_per_1k_tokens",
        "embedding_seconds_per_1k_tokens",
        "turns_per_db_restore",
        "db_restore_seconds",
        "labelling_seconds_per_question",
    }
)


@dataclass(frozen=True)
class SessionCounts:
    """Counts for one ingest session, as supplied by the caller."""

    session_id: str
    turns: int
    chars: int


@dataclass(frozen=True)
class QuestionAggregate:
    """Per-question aggregate counts supplied to the estimator.

    ``sessions`` carries each ingest session's ID with its turn and character
    counts; ``turns`` and ``chars`` are the per-question totals those supply.
    The estimator de-duplicates by session ID before summing, because the same
    session appears in several question haystacks.
    """

    question_id: str
    sessions: tuple[SessionCounts, ...]

    @property
    def turns(self) -> int:
        return sum(session.turns for session in self.sessions)

    @property
    def chars(self) -> int:
        return sum(session.chars for session in self.sessions)


def _validated_ids(ids: Any, label: str) -> frozenset[str]:
    if isinstance(ids, (str, bytes)) or not isinstance(ids, Iterable):
        raise ValueError(f"{label} must be a collection of IDs")
    output: set[str] = set()
    for value in ids:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be nonempty strings")
        if value in output:
            raise ValueError(f"{label} must be unique")
        output.add(value)
    return frozenset(output)


def _ids_sha256(ids: Iterable[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode()).hexdigest()


def select_dev2(
    dataset: list[QuestionMetadata],
    dev: list[QuestionMetadata],
    holdout_ids: Collection[str],
    *,
    size: int = 100,
    seed: int = 42,
) -> dict[str, Any]:
    """Proportional integer allocation over eligible strata, then hash rank.

    Exclusion precedence per dataset row: dev ID, holdout ID, an answer session
    shared with a dev question, an answer session shared with a holdout question,
    an ineligible question type, otherwise eligible. The exclusion counts plus
    ``eligible_count`` always sum to ``dataset_count``.
    """
    if size <= 0:
        raise ValueError("dev2 size must be positive")
    holdout = _validated_ids(holdout_ids, "Holdout IDs")
    # Also validate callers that construct metadata directly.
    if len({row.question_id for row in dataset}) != len(dataset) or len(
        {row.question_id for row in dev}
    ) != len(dev):
        raise ValueError("Duplicate question IDs")
    by_id = {row.question_id: row for row in dataset}
    if any(by_id.get(row.question_id) != row for row in dev):
        raise ValueError("Dev selection metadata does not match the full dataset")
    if not holdout <= by_id.keys():
        raise ValueError("Holdout IDs do not match the full dataset")
    dev_ids = {row.question_id for row in dev}
    if dev_ids & holdout:
        raise ValueError("Dev and holdout IDs overlap")
    dev_sessions = {session for row in dev for session in row.answer_session_ids}
    holdout_sessions = {
        session for question_id in holdout for session in by_id[question_id].answer_session_ids
    }

    excluded: Counter[str] = Counter()
    candidates: list[QuestionMetadata] = []
    for row in dataset:
        if row.question_id in dev_ids:
            excluded["excluded_dev_ids"] += 1
        elif row.question_id in holdout:
            excluded["excluded_holdout_ids"] += 1
        elif row.answer_session_ids & dev_sessions:
            excluded["excluded_shared_answer_sessions_only"] += 1
        elif row.answer_session_ids & holdout_sessions:
            excluded["excluded_holdout_shared_answer_sessions_only"] += 1
        elif row.question_type not in ELIGIBLE_TYPES:
            excluded["excluded_ineligible_types"] += 1
        else:
            candidates.append(row)
    if len(candidates) < size:
        raise ValueError("Insufficient eligible candidates; do not silently shrink the dev2 set")

    groups: dict[str, list[QuestionMetadata]] = defaultdict(list)
    for row in candidates:
        groups[row.stratum].append(row)
    counts = {key: len(rows) for key, rows in sorted(groups.items())}
    quotas = {key: size * count // len(candidates) for key, count in counts.items()}
    remainder_order = sorted(counts, key=lambda key: (-(size * counts[key] % len(candidates)), key))
    for key in remainder_order[: size - sum(quotas.values())]:
        quotas[key] += 1

    def rank(row: QuestionMetadata) -> tuple[str, str]:
        payload = json.dumps([ALGORITHM, seed, row.stratum, row.question_id], separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest(), row.question_id

    selected = [
        row for key in sorted(groups) for row in sorted(groups[key], key=rank)[: quotas[key]]
    ]
    ids = sorted(row.question_id for row in selected)
    return {
        "algorithm": ALGORITHM,
        "seed": seed,
        "size": size,
        "selection_fields": list(METADATA_FIELDS),
        "dataset_count": len(dataset),
        "dev_count": len(dev),
        "holdout_count": len(holdout),
        "excluded_dev_ids": excluded["excluded_dev_ids"],
        "excluded_holdout_ids": excluded["excluded_holdout_ids"],
        "excluded_shared_answer_sessions_only": excluded["excluded_shared_answer_sessions_only"],
        "excluded_holdout_shared_answer_sessions_only": excluded[
            "excluded_holdout_shared_answer_sessions_only"
        ],
        "excluded_ineligible_types": excluded["excluded_ineligible_types"],
        "eligible_count": len(candidates),
        "eligible_strata": counts,
        "selected_strata": dict(sorted(Counter(row.stratum for row in selected).items())),
        "question_ids": ids,
        "question_ids_sha256": _ids_sha256(ids),
        "question_ids_hash_encoding": IDS_HASH_ENCODING,
        "holdout_ids_sha256": _ids_sha256(holdout),
        "holdout_ids_hash_encoding": IDS_HASH_ENCODING,
    }


def selected_row_aggregates(rows: Any, selected_ids: Collection[str]) -> list[QuestionAggregate]:
    """Per-question session counts for the selected rows only.

    Every other row contributes nothing beyond its question ID read by the ID
    scan: its sessions, messages and lengths are never touched. This is what
    keeps the estimate away from holdout rows.
    """
    if not isinstance(rows, list):
        raise ValueError("Dataset must be a list")
    selected = frozenset(selected_ids)
    if not selected:
        return []
    output: list[QuestionAggregate] = []
    found: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Dataset rows must be objects")
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("Question IDs must be unique nonempty strings")
        if question_id not in selected:
            continue
        if question_id in found:
            raise ValueError("Duplicate question IDs")
        sessions = row.get("haystack_sessions", [])
        session_ids = row.get("haystack_session_ids", [])
        if not isinstance(sessions, list) or not isinstance(session_ids, list):
            raise ValueError("Haystack sessions and session IDs must be lists")
        counts: list[SessionCounts] = []
        for index, messages in enumerate(sessions):
            if not isinstance(messages, list):
                raise ValueError("Session messages must be a list")
            session_id = (
                str(session_ids[index])
                if index < len(session_ids)
                else f"{question_id}_session_{index}"
            )
            turns = len(messages)
            chars = 0
            for message in messages:
                if isinstance(message, dict):
                    content = message.get("content", "")
                    chars += len(content) if isinstance(content, str) else len(str(content))
            counts.append(SessionCounts(session_id, turns, chars))
        output.append(QuestionAggregate(question_id, tuple(counts)))
        found.add(question_id)
    if selected - found:
        raise ValueError("Selected question IDs must exist in the dataset")
    return output


@dataclass(frozen=True)
class _Totals:
    questions: int
    sessions: int
    turns: int
    chars: int
    tokens: float
    spot_checked: int
    session_count_conflicts: int


def _totals(rows: Sequence[QuestionAggregate]) -> _Totals:
    """De-duplicate sessions by ID, then return the ingest totals.

    A session repeated across question haystacks is counted once. If the same
    session ID ever arrives with different counts, the larger per-field counts
    are kept as a conservative bound and the conflict is counted in
    ``session_count_conflicts`` so it can never pass unnoticed.
    """
    unique: dict[str, SessionCounts] = {}
    seen_questions: set[str] = set()
    conflicts = 0
    for row in rows:
        if not isinstance(row, QuestionAggregate):
            raise ValueError("Estimate rows must be QuestionAggregate values")
        if row.question_id in seen_questions:
            raise ValueError("Duplicate question IDs in the estimate")
        seen_questions.add(row.question_id)
        for session in row.sessions:
            if not isinstance(session.session_id, str) or not session.session_id:
                raise ValueError("Session IDs must be nonempty strings")
            for name, value in (("turns", session.turns), ("chars", session.chars)):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"Session {name} must be nonnegative integers")
            existing = unique.get(session.session_id)
            if existing is None:
                unique[session.session_id] = session
            elif existing != session:
                conflicts += 1
                unique[session.session_id] = SessionCounts(
                    session.session_id,
                    max(existing.turns, session.turns),
                    max(existing.chars, session.chars),
                )
    questions = len(seen_questions)
    turns = sum(session.turns for session in unique.values())
    chars = sum(session.chars for session in unique.values())
    return _Totals(
        questions=questions,
        sessions=len(unique),
        turns=turns,
        chars=chars,
        tokens=chars / CHARS_PER_TOKEN,
        # ceil(15% of questions) in integer arithmetic, with no float rounding.
        spot_checked=(questions * SPOT_CHECK_PERCENT + 99) // 100,
        session_count_conflicts=conflicts,
    )


def estimate_volumes(rows: Sequence[QuestionAggregate]) -> dict[str, float | int]:
    """Unit-free estimate: ingest volumes only, no costs."""
    totals = _totals(rows)
    return {
        "chars_per_token_approximation": float(CHARS_PER_TOKEN),
        "estimated_questions": totals.questions,
        "estimated_ingest_sessions": totals.sessions,
        "estimated_ingest_turns": totals.turns,
        "estimated_ingest_chars": totals.chars,
        "estimated_tokens": totals.tokens,
        "labelling_spot_check_fraction": SPOT_CHECK_PERCENT / 100,
        "estimated_labelling_spot_check_questions": totals.spot_checked,
        "session_count_conflicts": totals.session_count_conflicts,
    }


def _unit_cost(costs: Mapping[str, Any], name: str) -> float:
    value = costs[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Unit cost {name} must be a number")
    if value < 0:
        raise ValueError(f"Unit cost {name} must be nonnegative")
    return float(value)


def estimate(
    rows: Sequence[QuestionAggregate], unit_costs: Mapping[str, Any]
) -> dict[str, float | int]:
    """Pure numeric estimate over caller-supplied unit costs.

    Every returned value is an estimate: ``estimated_tokens`` is
    ``chars / 4`` (an approximation, not a tokenizer count), and the cost,
    duration, restore and labelling values are arithmetic over ``unit_costs``.
    No price, rate or capacity is embedded here, and no text enters or leaves.
    """
    if not isinstance(unit_costs, Mapping):
        raise ValueError("Unit costs must be a mapping")
    if set(unit_costs) != UNIT_COST_FIELDS:
        raise ValueError("Unit costs must supply exactly the declared fields")
    costs = {name: _unit_cost(unit_costs, name) for name in UNIT_COST_FIELDS}
    per_restore = costs["turns_per_db_restore"]
    if per_restore <= 0:
        raise ValueError("Unit cost turns_per_db_restore must be positive")

    totals = _totals(rows)
    tokens = totals.tokens
    extraction_usd = tokens / 1000 * costs["extraction_usd_per_1k_tokens"]
    embedding_usd = tokens / 1000 * costs["embedding_usd_per_1k_tokens"]
    extraction_seconds = tokens / 1000 * costs["extraction_seconds_per_1k_tokens"]
    embedding_seconds = tokens / 1000 * costs["embedding_seconds_per_1k_tokens"]
    restores = math.ceil(totals.turns / per_restore)
    restore_seconds = restores * costs["db_restore_seconds"]
    labelling_seconds = totals.spot_checked * costs["labelling_seconds_per_question"]
    return {
        "chars_per_token_approximation": float(CHARS_PER_TOKEN),
        "estimated_questions": totals.questions,
        "estimated_ingest_sessions": totals.sessions,
        "estimated_ingest_turns": totals.turns,
        "estimated_ingest_chars": totals.chars,
        "estimated_tokens": tokens,
        "estimated_extraction_usd": round(extraction_usd, 6),
        "estimated_embedding_usd": round(embedding_usd, 6),
        "estimated_extraction_seconds": round(extraction_seconds, 6),
        "estimated_embedding_seconds": round(embedding_seconds, 6),
        "estimated_db_restores": restores,
        "estimated_db_restore_seconds": round(restore_seconds, 6),
        "estimated_wall_time_seconds": round(
            extraction_seconds + embedding_seconds + restore_seconds, 6
        ),
        "labelling_spot_check_fraction": SPOT_CHECK_PERCENT / 100,
        "estimated_labelling_spot_check_questions": totals.spot_checked,
        "estimated_labelling_seconds": round(labelling_seconds, 6),
        "estimated_labelling_minutes": round(labelling_seconds / 60, 6),
        "session_count_conflicts": totals.session_count_conflicts,
    }


def assert_manifest_allowlisted(manifest: Mapping[str, Any]) -> None:
    """Fail if a manifest carries a field outside the allowlist or non-numeric estimates."""
    if not isinstance(manifest, Mapping):
        raise ValueError("Manifest must be a mapping")
    unexpected = sorted(set(manifest) - MANIFEST_FIELDS)
    if unexpected:
        raise ValueError("Manifest field outside allowlist: " + ", ".join(unexpected))
    block = manifest.get("estimate")
    if block is None:
        return
    if not isinstance(block, Mapping):
        raise ValueError("Estimate must be a mapping")
    unexpected_estimate = sorted(set(block) - ESTIMATE_FIELDS)
    if unexpected_estimate:
        raise ValueError("Estimate field outside allowlist: " + ", ".join(unexpected_estimate))
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) for value in block.values()
    ):
        raise ValueError("Estimate values must be numbers")


def _holdout_ids(manifest: Any) -> frozenset[str]:
    if not isinstance(manifest, dict):
        raise ValueError("Holdout manifest must be an object")
    return _validated_ids(manifest.get("question_ids"), "Holdout IDs")


def freeze(
    dataset_path: Path,
    dev_path: Path,
    holdout_path: Path,
    output: Path,
    *,
    size: int,
    seed: int,
    unit_costs_path: Path | None = None,
) -> None:
    """Create, never overwrite, an IDs-plus-estimate manifest from local input files."""
    raw_dataset, raw_dev, raw_holdout = (
        dataset_path.read_bytes(),
        dev_path.read_bytes(),
        holdout_path.read_bytes(),
    )
    rows = json.loads(raw_dataset)
    holdout_ids = _holdout_ids(json.loads(raw_holdout))
    manifest = select_dev2(
        project_metadata(rows),
        project_metadata(json.loads(raw_dev)),
        holdout_ids,
        size=size,
        seed=seed,
    )
    manifest["dataset_sha256"] = hashlib.sha256(raw_dataset).hexdigest()
    manifest["dev_sha256"] = hashlib.sha256(raw_dev).hexdigest()
    manifest["holdout_sha256"] = hashlib.sha256(raw_holdout).hexdigest()
    aggregates = selected_row_aggregates(rows, manifest["question_ids"])
    if holdout_ids.intersection(row.question_id for row in aggregates):
        raise ValueError("The estimate must never include holdout rows")
    if unit_costs_path is None:
        manifest["estimate"] = estimate_volumes(aggregates)
    else:
        raw_costs = unit_costs_path.read_bytes()
        manifest["unit_costs_sha256"] = hashlib.sha256(raw_costs).hexdigest()
        manifest["estimate"] = estimate(aggregates, json.loads(raw_costs))
    assert_manifest_allowlisted(manifest)
    with output.open("x") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--dev", required=True, type=Path)
    parser.add_argument("--holdout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--unit-costs",
        type=Path,
        default=None,
        help=(
            "Optional JSON object with explicit unit costs. Omitted: emit the "
            "unit-free volume estimate only, because no price is assumed here."
        ),
    )
    args = parser.parse_args()
    freeze(
        args.dataset,
        args.dev,
        args.holdout,
        args.output,
        size=args.size,
        seed=args.seed,
        unit_costs_path=args.unit_costs,
    )


if __name__ == "__main__":
    main()
