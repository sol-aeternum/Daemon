"""Freeze a metadata-only LongMemEval holdout; never emit dataset content."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUESTION_TYPES = frozenset(
    {
        "single-session-user",
        "single-session-assistant",
        "single-session-preference",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    }
)
METADATA_FIELDS = ("question_id", "question_type", "answer_session_ids")
ALGORITHM = "type-x-abstention-largest-remainder-sha256-v1"


@dataclass(frozen=True)
class QuestionMetadata:
    question_id: str
    question_type: str
    answer_session_ids: frozenset[str]

    @property
    def stratum(self) -> str:
        # Match the official evaluator's abstention predicate.
        return self.question_type + (":abs" if "_abs" in self.question_id else ":answerable")


def project_metadata(rows: Any) -> list[QuestionMetadata]:
    """Read only the three allowlisted fields, including when validation fails."""
    if not isinstance(rows, list):
        raise ValueError("Dataset must be a list")
    output: list[QuestionMetadata] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or any(key not in row for key in METADATA_FIELDS):
            raise ValueError("Missing required selection metadata")
        qid, qtype, sessions = (row[key] for key in METADATA_FIELDS)
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ValueError("Question IDs must be unique nonempty strings")
        if not isinstance(qtype, str) or qtype not in QUESTION_TYPES:
            raise ValueError("Unknown question type")
        if not isinstance(sessions, list) or any(
            not isinstance(session, str) or not session for session in sessions
        ):
            raise ValueError("Answer-session IDs must be a list of nonempty strings")
        seen.add(qid)
        output.append(QuestionMetadata(qid, qtype, frozenset(sessions)))
    return output


def select_holdout(
    dataset: list[QuestionMetadata],
    dev: list[QuestionMetadata],
    *,
    size: int = 100,
    seed: int = 42,
) -> dict[str, Any]:
    """Proportional integer allocation, then hash-ranked selection per stratum."""
    if size <= 0:
        raise ValueError("Holdout size must be positive")
    # Also validate callers that construct metadata directly.
    if len({row.question_id for row in dataset}) != len(dataset) or len(
        {row.question_id for row in dev}
    ) != len(dev):
        raise ValueError("Duplicate question IDs")
    by_id = {row.question_id: row for row in dataset}
    if any(by_id.get(row.question_id) != row for row in dev):
        raise ValueError("Dev selection metadata does not match the full dataset")
    dev_ids = {row.question_id for row in dev}
    dev_sessions = {session for row in dev for session in row.answer_session_ids}
    candidates = [
        row
        for row in dataset
        if row.question_id not in dev_ids and not row.answer_session_ids & dev_sessions
    ]
    if len(candidates) < size:
        raise ValueError("Insufficient disjoint candidates; do not silently shrink the holdout")
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
    id_bytes = ("\n".join(ids) + "\n").encode()
    return {
        "algorithm": ALGORITHM,
        "seed": seed,
        "size": size,
        "selection_fields": list(METADATA_FIELDS),
        "dataset_count": len(dataset),
        "dev_count": len(dev),
        "excluded_dev_ids": sum(row.question_id in dev_ids for row in dataset),
        "excluded_shared_answer_sessions_only": sum(
            row.question_id not in dev_ids and bool(row.answer_session_ids & dev_sessions)
            for row in dataset
        ),
        "eligible_count": len(candidates),
        "eligible_strata": counts,
        "selected_strata": dict(sorted(Counter(row.stratum for row in selected).items())),
        "question_ids": ids,
        "question_ids_sha256": hashlib.sha256(id_bytes).hexdigest(),
        "question_ids_hash_encoding": "sorted UTF-8 IDs, one per line, final newline",
    }


def freeze(dataset_path: Path, dev_path: Path, output: Path, *, size: int, seed: int) -> None:
    """Create, never overwrite, an IDs-only manifest from local input files."""
    raw_dataset, raw_dev = dataset_path.read_bytes(), dev_path.read_bytes()
    dataset = project_metadata(json.loads(raw_dataset))
    dev = project_metadata(json.loads(raw_dev))
    manifest = select_holdout(dataset, dev, size=size, seed=seed)
    manifest["dataset_sha256"] = hashlib.sha256(raw_dataset).hexdigest()
    manifest["dev_sha256"] = hashlib.sha256(raw_dev).hexdigest()
    with output.open("x") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--dev", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    freeze(args.dataset, args.dev, args.output, size=args.size, seed=args.seed)


if __name__ == "__main__":
    main()
