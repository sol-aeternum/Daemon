"""Discover opt-in modules and greedily balance them using recorded CI durations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = ROOT / "scripts" / "postgres_inventory_timings.json"
PATTERN = re.compile(r"metadata_cluster|compute_metadata_support|--compute-metadata-postgres")


def discover(root: Path) -> list[str]:
    """Keep the original grep discovery contract; timings are never an allowlist."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in (root / "tests").glob("test_*.py")
        if PATTERN.search(path.read_text(encoding="utf-8"))
    )


def partition(files: list[str], durations: dict[str, float], count: int) -> list[list[str]]:
    if count < 1:
        raise ValueError("shard count must be positive")
    fallback = median(durations.values()) if durations else 1.0
    weights = {file: max(durations.get(file, fallback), 0.001) for file in set(files)}
    shards: list[list[str]] = [[] for _ in range(count)]
    loads = [0.0] * count
    for file in sorted(weights, key=lambda file: (-weights[file], file)):
        target = min(range(count), key=lambda index: (loads[index], index))
        shards[target].append(file)
        loads[target] += weights[file]
    return [sorted(shard) for shard in shards]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True, help="one-based shard number")
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.shard <= args.count:
        parser.error("shard must be between 1 and count")
    files = discover(ROOT)
    if not files:
        parser.error("no PostgreSQL opt-in modules discovered")
    durations = json.loads(TIMINGS.read_text(encoding="utf-8"))["seconds_by_file"]
    selected = partition(files, durations, args.count)[args.shard - 1]
    if not selected:
        parser.error("selected shard has no modules")
    print("\n".join(selected))


if __name__ == "__main__":
    main()
