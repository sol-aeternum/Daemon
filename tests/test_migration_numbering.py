"""Migration number prefixes stay unique, so two PRs can't quietly take one number.

``scripts/migrate.py`` tracks applied migrations by filename and applies them in
sorted order. Git sees two new files with one number as no conflict at all.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = re.compile(r"^(\d+)_")

# Already applied in deployed databases. The runner tracks filenames, so renaming
# either would apply it a second time. Never add to this list: renumber instead.
GRANDFATHERED: dict[str, frozenset[str]] = {
    "036": frozenset({"036_memory_content_hash.sql", "036_skill_consolidation_pending_status.sql"}),
}


def duplicate_prefixes(names: Iterable[str]) -> dict[str, set[str]]:
    by_prefix: defaultdict[str, set[str]] = defaultdict(set)
    for name in names:
        match = PREFIX.match(name)
        if match:
            by_prefix[match.group(1)].add(name)
    return {prefix: files for prefix, files in by_prefix.items() if len(files) > 1}


def _sql_names(directory: Path) -> list[str]:
    return [path.name for path in directory.glob("*.sql")]


def test_duplicate_prefixes_finds_a_collision_and_ignores_distinct_numbers() -> None:
    assert duplicate_prefixes(["062_a.sql", "062_b.sql", "063_c.sql"]) == {
        "062": {"062_a.sql", "062_b.sql"}
    }
    assert duplicate_prefixes(["061_a.sql", "062_b.sql"]) == {}


def test_every_migration_has_a_number_prefix() -> None:
    assert [n for n in _sql_names(ROOT / "migrations") if not PREFIX.match(n)] == []


def test_migration_numbers_are_unique_apart_from_the_grandfathered_pair() -> None:
    assert duplicate_prefixes(_sql_names(ROOT / "migrations")) == {
        prefix: set(files) for prefix, files in GRANDFATHERED.items()
    }


def test_rollback_numbers_are_unique() -> None:
    assert duplicate_prefixes(_sql_names(ROOT / "migrations" / "rollback")) == {}
