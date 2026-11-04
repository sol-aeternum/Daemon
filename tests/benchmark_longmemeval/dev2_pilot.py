"""Select the dev2 pilot set and materialise one dataset per arm.

Selection is metadata-only and follows ``holdout.py``/``dev2.py``: it reads
``question_id``, ``question_type`` and ``answer_session_ids`` through
``project_metadata`` and nothing else. Volume numbers reuse
``dev2.selected_row_aggregates``/``dev2.estimate_volumes`` so a row outside an
arm contributes nothing beyond its ``question_id``.

The pilot size and the arm sizes are fixed constants; the allocation is
largest-remainder over ``QuestionMetadata.stratum`` and every rank is a
SHA-256 digest of ``[ALGORITHM, seed, stratum, question_id]``, so the result is
reproducible from the IDs and the seed alone. Arm files are written with
``open("x")`` and never overwritten; the manifest carries IDs plus numbers, no
question, session or answer text. Nothing here calls a provider, opens a
database, touches the network or reads the benchmark dataset directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from tests.benchmark_longmemeval.dev2 import (
    QuestionAggregate,
    estimate_volumes,
    selected_row_aggregates,
)
from tests.benchmark_longmemeval.holdout import QuestionMetadata, project_metadata

PILOT_SIZE: Final[int] = 14
ARM_SIZES: Final[dict[int, int]] = {1: 2, 4: 4, 8: 8}
# Distinct from ``holdout.ALGORITHM`` and ``dev2.ALGORITHM``.
ALGORITHM: Final[str] = "dev2-pilot-stratified-largest-remainder-round-robin-sha256-v1"
MANIFEST_NAME: Final[str] = "pilot_manifest.json"


def _validated_ids(ids: Collection[str], label: str) -> list[str]:
    if isinstance(ids, (str, bytes)) or not isinstance(ids, Collection):
        raise ValueError(f"{label} must be a collection of IDs")
    output: list[str] = []
    seen: set[str] = set()
    for value in ids:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be nonempty strings")
        if value in seen:
            raise ValueError(f"{label} must be unique")
        seen.add(value)
        output.append(value)
    return output


def _rows_by_id(rows: Sequence[QuestionMetadata]) -> dict[str, QuestionMetadata]:
    index: dict[str, QuestionMetadata] = {}
    for row in rows:
        if not isinstance(row, QuestionMetadata):
            raise ValueError("Rows must be QuestionMetadata values")
        if row.question_id in index:
            raise ValueError("Duplicate question IDs")
        index[row.question_id] = row
    return index


def _rank(seed: int, stratum: str, question_id: str) -> str:
    payload = json.dumps([ALGORITHM, seed, stratum, question_id], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _largest_remainder(counts: Mapping[str, int], size: int) -> dict[str, int]:
    """Allocate ``size`` seats over ``counts``, ties broken by stratum name."""
    total = sum(counts.values())
    if size > total:
        raise ValueError("size exceeds the number of available rows")
    quotas: dict[str, int] = {}
    remainders: list[tuple[int, str]] = []
    assigned = 0
    for stratum, count in counts.items():
        floor, remainder = divmod(size * count, total)
        quotas[stratum] = floor
        assigned += floor
        remainders.append((-remainder, stratum))
    remainders.sort()
    for _, stratum in remainders[: size - assigned]:
        quotas[stratum] += 1
    return quotas


def select_pilot(
    rows: list[QuestionMetadata],
    dev2_ids: Collection[str],
    *,
    size: int = PILOT_SIZE,
    seed: int = 42,
) -> list[str]:
    """Pick ``size`` dev2 IDs, stratified by ``QuestionMetadata.stratum``.

    One seat allocation is computed with largest remainders over the strata of
    the whole dev2 set, so every stratum is represented in proportion. Within a
    stratum, IDs are ordered by the SHA-256 rank of
    ``[ALGORITHM, seed, stratum, question_id]`` and the top ``quota`` are taken.
    """
    ids = _validated_ids(dev2_ids, "dev2_ids")
    if not ids:
        raise ValueError("dev2_ids must not be empty")
    if size <= 0:
        raise ValueError("size must be positive")
    if size > len(ids):
        raise ValueError("size exceeds the number of dev2 IDs")
    index = _rows_by_id(rows)
    missing = [question_id for question_id in ids if question_id not in index]
    if missing:
        raise ValueError(f"{len(missing)} dev2 IDs are absent from the metadata rows")
    strata = Counter(index[question_id].stratum for question_id in ids)
    quotas = _largest_remainder(strata, size)
    selected: list[str] = []
    for stratum in sorted(strata):
        ranked = sorted(
            (question_id for question_id in ids if index[question_id].stratum == stratum),
            key=lambda question_id: (_rank(seed, stratum, question_id), question_id),
        )
        selected.extend(ranked[: quotas[stratum]])
    return sorted(selected)


def assign_arms(
    rows: list[QuestionMetadata],
    pilot_ids: Collection[str],
    *,
    seed: int = 42,
) -> dict[int, list[str]]:
    """Deal the pilot IDs into the fixed arms.

    Rule: order the pilot IDs by ``(stratum, SHA-256 rank)``, then deal them
    round-robin across the arms in ascending arm order, skipping an arm once it
    is full, until every arm holds exactly ``ARM_SIZES[arm]`` IDs. Consecutive
    same-stratum IDs land in different arms, so each arm gets a mix of strata.
    The pilot must contain exactly ``sum(ARM_SIZES.values())`` IDs, and every ID
    lands in exactly one arm.
    """
    ids = _validated_ids(pilot_ids, "pilot_ids")
    index = _rows_by_id(rows)
    missing = [question_id for question_id in ids if question_id not in index]
    if missing:
        raise ValueError(f"{len(missing)} pilot IDs are absent from the metadata rows")
    capacities = dict(sorted(ARM_SIZES.items()))
    if len(ids) != sum(capacities.values()):
        raise ValueError("pilot size must equal the total arm capacity")
    order = sorted(
        (
            (
                index[question_id].stratum,
                _rank(seed, index[question_id].stratum, question_id),
                question_id,
            )
            for question_id in ids
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    arms = list(capacities)
    result: dict[int, list[str]] = {arm: [] for arm in arms}
    cursor = 0
    for _, _, question_id in order:
        for _ in arms:
            arm = arms[cursor % len(arms)]
            cursor += 1
            if len(result[arm]) < capacities[arm]:
                result[arm].append(question_id)
                break
        else:  # pragma: no cover - guarded by the capacity check above
            raise ValueError("no arm capacity left")
    return result


def _row_id(row: Any) -> str:
    if not isinstance(row, dict):
        raise ValueError("Dataset rows must be objects")
    question_id = row.get("question_id")
    if not isinstance(question_id, str) or not question_id:
        raise ValueError("Question IDs must be nonempty strings")
    return question_id


def write_arm_datasets(
    dataset_rows: list[Any],
    arms: Mapping[int, Collection[str]],
    out_dir: Path,
    *,
    dev2_ids: Collection[str],
    holdout_ids: Collection[str],
) -> dict[int, str]:
    """Write ``arm_c<n>.json`` per arm and return ``{arm: sha256}``.

    Each file is a JSON list of the full original rows for that arm's IDs, in
    dataset order, serialised as ``json.dumps(rows, ensure_ascii=False)`` plus a
    final newline. Files are opened with ``"x"`` and are never overwritten.
    Everything is validated before the first file is created: an ID outside
    ``dev2_ids``, an ID in ``holdout_ids``, a duplicate arm ID or an ID absent
    from the dataset raises and leaves ``out_dir`` untouched.
    """
    allowed = frozenset(_validated_ids(dev2_ids, "dev2_ids"))
    holdout = frozenset(_validated_ids(holdout_ids, "holdout_ids"))
    plan: dict[int, list[str]] = {}
    seen: set[str] = set()
    for arm in sorted(arms):
        ids = _validated_ids(arms[arm], f"arm {arm} IDs")
        for question_id in ids:
            if question_id in holdout:
                raise ValueError("refusing to write a holdout ID into an arm")
            if question_id not in allowed:
                raise ValueError("refusing to write an ID outside the dev2 set")
            if question_id in seen:
                raise ValueError("an ID may appear in only one arm")
            seen.add(question_id)
        plan[arm] = ids
    wanted = set(seen)
    rows_by_id: dict[str, Any] = {}
    for row in dataset_rows:
        question_id = _row_id(row)
        if question_id in wanted:
            if question_id in rows_by_id:
                raise ValueError("Duplicate question IDs in the dataset")
            rows_by_id[question_id] = row
    if wanted - set(rows_by_id):
        raise ValueError("Arm IDs must exist in the dataset")
    out_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[int, str] = {}
    for arm in sorted(plan):
        members = frozenset(plan[arm])
        payload = json.dumps(
            [row for row in dataset_rows if row.get("question_id") in members],
            ensure_ascii=False,
        )
        text = payload + "\n"
        with open(out_dir / f"arm_c{arm}.json", "x", encoding="utf-8", newline="") as handle:
            handle.write(text)
        hashes[arm] = hashlib.sha256(text.encode()).hexdigest()
    return hashes


def pilot_volume(dataset_rows: list[Any], arms: Mapping[int, Collection[str]]) -> dict[str, Any]:
    """Per-arm and total ingest volumes, as numbers only.

    Uses ``dev2.selected_row_aggregates`` so only the arm IDs' rows are read
    beyond their ``question_id``; ``estimate_volumes`` supplies the numbers.
    """
    per_arm: dict[int, list[QuestionAggregate]] = {}
    for arm in sorted(arms):
        per_arm[arm] = selected_row_aggregates(dataset_rows, arms[arm])
    total: list[QuestionAggregate] = [row for arm in per_arm for row in per_arm[arm]]
    return {
        "arms": {arm: estimate_volumes(rows) for arm, rows in per_arm.items()},
        "total": estimate_volumes(total),
    }


def spend_projection(
    volume: Mapping[str, Any],
    *,
    usd_per_session: float,
    usd_per_1k_tokens: float,
) -> dict[str, float]:
    """Token- and session-scaled USD for the total, both labelled estimates."""
    for name, value in (
        ("usd_per_session", usd_per_session),
        ("usd_per_1k_tokens", usd_per_1k_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    total = volume["total"]
    tokens = total["estimated_tokens"]
    sessions = total["estimated_ingest_sessions"]
    return {
        "estimated_token_scaled_usd": float(tokens) / 1000.0 * float(usd_per_1k_tokens),
        "estimated_session_scaled_usd": float(sessions) * float(usd_per_session),
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def prepare(
    *,
    dataset_path: Path,
    dev2_path: Path,
    holdout_path: Path,
    out_dir: Path,
    size: int = PILOT_SIZE,
    seed: int = 42,
) -> dict[str, Any]:
    """Select the pilot, write its arm files and an IDs-plus-numbers manifest."""
    dataset_bytes = dataset_path.read_bytes()
    dev2_bytes = dev2_path.read_bytes()
    holdout_bytes = holdout_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    dev2_ids = _validated_ids(json.loads(dev2_bytes), "dev2_ids")
    holdout_ids = _validated_ids(json.loads(holdout_bytes), "holdout_ids")
    rows = project_metadata(dataset)
    pilot_ids = select_pilot(rows, dev2_ids, size=size, seed=seed)
    arms = assign_arms(rows, pilot_ids, seed=seed)
    hashes = write_arm_datasets(dataset, arms, out_dir, dev2_ids=dev2_ids, holdout_ids=holdout_ids)
    manifest: dict[str, Any] = {
        "algorithm": ALGORITHM,
        "seed": seed,
        "size": size,
        "pilot_ids": pilot_ids,
        "arms": {str(arm): ids for arm, ids in sorted(arms.items())},
        "arm_sha256": {str(arm): digest for arm, digest in sorted(hashes.items())},
        "volumes": pilot_volume(dataset, arms),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "dev2_ids_sha256": hashlib.sha256(dev2_bytes).hexdigest(),
        "holdout_ids_sha256": hashlib.sha256(holdout_bytes).hexdigest(),
    }
    path = out_dir / MANIFEST_NAME
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--dev2", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--size", type=int, default=PILOT_SIZE)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    prepare(
        dataset_path=args.dataset,
        dev2_path=args.dev2,
        holdout_path=args.holdout,
        out_dir=args.out_dir,
        size=args.size,
        seed=args.seed,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
