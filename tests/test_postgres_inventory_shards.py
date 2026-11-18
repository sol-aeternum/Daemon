"""The timing snapshot balances work, but can never remove discovered coverage."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.postgres_inventory_shards import PATTERN, ROOT, TIMINGS, discover, partition


def test_discovery_matches_original_grep_and_partitions_every_file_once() -> None:
    assert PATTERN.pattern == "|".join(
        (
            "metadata_" + "cluster",
            "compute_" + "metadata_support",
            "--compute-" + "metadata-postgres",
        )
    )
    original = subprocess.check_output(
        [
            "bash",
            "-c",
            'grep -lE "$1" tests/test_*.py | LC_ALL=C sort',
            "grep-inventory",
            PATTERN.pattern,
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    files = discover(ROOT)
    assert files == original
    assert Path(__file__).relative_to(ROOT).as_posix() not in files
    durations = json.loads(TIMINGS.read_text())["seconds_by_file"]
    shards = partition(files, durations, 4)
    flat = [file for shard in shards for file in shard]
    assert sorted(flat) == files
    assert len(flat) == len(set(flat))
    assert all(shards)
    assert shards == partition(list(reversed(files)), durations, 4)
    # New modules must not make the historical balance regression brittle.
    baseline = partition(list(durations), durations, 4)
    loads = [sum(durations[file] for file in shard) for shard in baseline]
    assert max(loads) - min(loads) < 30, loads


def test_discovery_includes_new_files_and_ignores_removed_timing_entries(tmp_path: Path) -> None:
    first, second, third = PATTERN.pattern.split("|")
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, content in {
        "test_a.py": first,
        "test_b.py": second,
        "test_new.py": third,
        "test_ordinary.py": "pass",
        "helper.py": first,
    }.items():
        (tests / name).write_text(content)
    (tests / "nested").mkdir()
    (tests / "nested/test_nested.py").write_text(first)
    files = discover(tmp_path)
    assert files == ["tests/test_a.py", "tests/test_b.py", "tests/test_new.py"]
    shards = partition(files, {"tests/test_a.py": 10, "tests/test_deleted.py": 20}, 4)
    assert sorted(file for shard in shards for file in shard) == files
    assert sorted(file for shard in partition(files, {}, 4) for file in shard) == files


def test_ci_runs_all_shards_without_cancellation_or_shared_artifacts() -> None:
    import yaml

    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"][
        "postgres-inventory"
    ]
    assert job["strategy"]["fail-fast"] is False
    assert set(job["strategy"]["matrix"]) == {"shard"}
    shards = job["strategy"]["matrix"]["shard"]
    assert shards == list(range(1, len(shards) + 1))
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["env"]["POSTGRES_INVENTORY_SHARD"] == "${{ matrix.shard }}"
    assert job["env"]["POSTGRES_INVENTORY_COUNT"] == "${{ strategy.job-total }}"
    assert "${{ strategy.job-total }}" in job["name"]
    assert "tasks-postgres" in job["services"]
    step = next(step for step in job["steps"] if step["name"] == "PostgreSQL opt-in inventory")
    assert step["continue-on-error"] is True
    assert 'work="$RUNNER_TEMP/pg-opt-in-$POSTGRES_INVENTORY_SHARD"' in step["run"]
    assert "set -euo pipefail" in step["run"]
    assert "uv run python scripts/postgres_inventory_shards.py" in step["run"]
    assert '--count "$POSTGRES_INVENTORY_COUNT"' in step["run"]


@pytest.mark.parametrize(
    "args",
    [
        ["--shard", "1"],
        ["--shard", "0", "--count", "4"],
        ["--shard", "5", "--count", "4"],
        ["--shard", "1", "--count", "0"],
    ],
)
def test_cli_rejects_missing_count_and_invalid_shard_without_output(args: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/postgres_inventory_shards.py"), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
