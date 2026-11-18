"""The timing snapshot balances work, but can never remove discovered coverage."""

import json
from pathlib import Path
import subprocess

from scripts.postgres_inventory_shards import ROOT, TIMINGS, discover, partition


def test_discovery_matches_original_grep_and_partitions_every_file_once() -> None:
    original = subprocess.check_output(
        [
            "bash",
            "-c",
            "grep -lE 'metadata_cluster|compute_metadata_support|--compute-metadata-postgres' tests/test_*.py | sort",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    files = discover(ROOT)
    assert files == original
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
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, content in {
        "test_a.py": "metadata_cluster",
        "test_b.py": "compute_metadata_support",
        "test_new.py": "--compute-metadata-postgres",
        "test_ordinary.py": "pass",
        "helper.py": "metadata_cluster",
    }.items():
        (tests / name).write_text(content)
    (tests / "nested").mkdir()
    (tests / "nested/test_nested.py").write_text("metadata_cluster")
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
    assert job["strategy"] == {"fail-fast": False, "matrix": {"shard": [1, 2, 3, 4]}}
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["env"]["POSTGRES_INVENTORY_SHARD"] == "${{ matrix.shard }}"
    assert "tasks-postgres" in job["services"]
    step = next(step for step in job["steps"] if step["name"] == "PostgreSQL opt-in inventory")
    assert step["continue-on-error"] is True
    assert 'work="$RUNNER_TEMP/pg-opt-in-$POSTGRES_INVENTORY_SHARD"' in step["run"]
    assert "set -euo pipefail" in step["run"]
