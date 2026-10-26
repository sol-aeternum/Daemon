"""Positive type-check source coverage, not a debt baseline or evidence deletion.

The three untracked transaction copies are local historical evidence, never
shipped code. Every other repository Python source, including new untracked
product/test/tool source, must remain in the explicit type-check boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOPES = (
    "orchestrator",
    "providers",
    "scripts",
    "tests",
    "tts",
    "backend",
    "config",
    "council",
    "db",
    "frontend/e2e",
    "test_preserve.py",
    "test_subagents.py",
)
LOCAL_EVIDENCE = frozenset(
    {
        "bc14-maintenance-20261023/transaction.py",
        "bc14-maintenance-20261023/preserved-before-branch-amendment-20261023/transaction.py",
        "bc14-maintenance-20261023/preserved-before-observer-amendment-20261023/transaction.py",
    }
)


def covered(path: str, scopes=SCOPES) -> bool:
    return any(path == scope or path.startswith(scope + "/") for scope in scopes)


def inventory(*flags: str) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", *flags, "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        path for path in result.stdout.decode("utf-8").split("\0") if path.endswith((".py", ".pyi"))
    }


def test_type_boundary_covers_all_tracked_and_new_source() -> None:
    tracked = inventory("--cached")
    new = inventory("--others", "--exclude-standard")
    assert not tracked.intersection(LOCAL_EVIDENCE), "Historical evidence must not be shipped"
    assert all(covered(path) for path in tracked), sorted(
        path for path in tracked if not covered(path)
    )
    required_new = new - LOCAL_EVIDENCE
    assert all(covered(path) for path in required_new), sorted(
        path for path in required_new if not covered(path)
    )


def test_type_configuration_keeps_both_selectors_and_debt_policy() -> None:
    config = json.loads((ROOT / "pyrightconfig.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    duplicate = project["tool"]["basedpyright"]
    assert config["include"] == duplicate["include"] == list(SCOPES)
    assert config["venvPath"] == duplicate["venvPath"] == "."
    assert config["venv"] == duplicate["venv"] == ".venv"
    assert config["typeCheckingMode"] == duplicate["typeCheckingMode"] == "standard"
    assert config["baselineFile"] == duplicate["baselineFile"] == ".basedpyright/baseline.json"
    assert (
        set(config)
        == set(duplicate)
        == {
            "include",
            "venvPath",
            "venv",
            "typeCheckingMode",
            "baselineFile",
        }
    )
    assert project["tool"]["setuptools"]["packages"]["find"]["include"] == ["orchestrator*"]


@pytest.mark.parametrize(
    "path",
    [
        "orchestrator/new.py",
        "providers/new.py",
        "scripts/new.py",
        "tests/new.py",
        "tts/new.py",
        "backend/new.py",
        "config/new.py",
        "council/new.py",
        "db/new.py",
        "frontend/e2e/new.py",
        "test_preserve.py",
        "test_subagents.py",
    ],
)
def test_new_code_in_every_source_root_is_checked(path: str) -> None:
    assert covered(path)


def test_missing_root_or_new_unclassified_root_cannot_silently_pass() -> None:
    assert not covered("tests/new.py", tuple(scope for scope in SCOPES if scope != "tests"))
    assert not covered("new_product_package/new.py")
    assert all(not covered(path) for path in LOCAL_EVIDENCE)
