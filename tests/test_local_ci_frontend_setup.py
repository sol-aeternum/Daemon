"""Exercise frontend-only gate ordering without installing or launching services."""

from __future__ import annotations

import os
import fnmatch
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def run_frontend_runner(
    tmp_path: Path,
    *,
    fail_sync: bool = False,
    fail_ownership: bool = False,
    override: bool = False,
    list_only: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    root = tmp_path / "checkout"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts/local_ci.sh", scripts / "local_ci.sh"
    )
    (root / "frontend").mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "commands"
    environment = tmp_path / "explicit-env" if override else root / ".venv"
    # Only test-owned tool stubs run. The browser stub fails on a pristine checkout
    # unless the real runner scheduled locked Python setup before that gate.
    common = f'printf \'%s\\n\' "$(basename "$0") $*" >> {shlex.quote(str(log))}\n'
    uv = (
        common
        + 'if [ "$*" = "sync --locked" ]; then\n'
        + ("exit 7\n" if fail_sync else f"mkdir -p {shlex.quote(str(environment / 'bin'))}\n")
        + "fi\n"
        + (
            'if [ "$*" = "run pytest -q frontend/e2e/cache_fixture_ownership.py" ]; then exit 8; fi\n'
            if fail_ownership
            else ""
        )
    )
    npx = common + f"test -d {shlex.quote(str(environment / 'bin'))}\n"
    for name, body in {"uv": uv, "npm": common, "npx": npx}.items():
        tool = tools / name
        tool.write_text("#!/bin/sh\n" + body)
        tool.chmod(0o700)
    # Setup preflight only needs to find Python; it must not execute it here.
    python = tools / "python"
    python.write_text("#!/bin/sh\nexit 9\n")
    python.chmod(0o700)
    env = {"PATH": f"{tools}:{os.defpath}"}
    if override:
        env["UV_PROJECT_ENVIRONMENT"] = str(environment)
    result = subprocess.run(
        ["bash", str(scripts / "local_ci.sh"), "frontend", *(["--list"] if list_only else [])],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("override", [False, True])
def test_frontend_only_schedules_locked_python_before_browser(
    tmp_path: Path, override: bool
) -> None:
    result, commands = run_frontend_runner(tmp_path, override=override)
    assert result.returncode == 0, result.stdout + result.stderr
    assert commands.count("uv sync --locked") == 1
    browser = "npm --prefix frontend run test:browser:cache"
    ownership = "uv run pytest -q frontend/e2e/cache_fixture_ownership.py"
    assert commands.count(ownership) == 1
    assert commands.count(browser) == 1
    assert commands.index("uv sync --locked") < commands.index("npm --prefix frontend run test:run")
    assert commands.index("npm --prefix frontend run test:run") < commands.index(ownership)
    assert commands.index(
        "npm ci --prefix frontend --no-audit --no-fund --offline"
    ) < commands.index(ownership)
    assert commands.index("uv sync --locked") < commands.index(ownership) < commands.index(browser)


def test_failed_locked_setup_is_blocking(tmp_path: Path) -> None:
    result, commands = run_frontend_runner(tmp_path, fail_sync=True)
    assert result.returncode == 1
    assert "uv sync --locked" in commands
    assert "frontend/cache-fixture-python (exit=7)" in result.stdout


def test_list_does_not_install_python(tmp_path: Path) -> None:
    result, commands = run_frontend_runner(tmp_path, list_only=True)
    assert result.returncode == 0
    assert commands == []
    assert "uv sync --locked" in result.stdout


def test_ownership_refusal_is_blocking(tmp_path: Path) -> None:
    result, commands = run_frontend_runner(tmp_path, fail_ownership=True)
    assert result.returncode == 1
    assert "uv run pytest -q frontend/e2e/cache_fixture_ownership.py" in commands
    assert "frontend/cache-fixture-ownership (exit=8)" in result.stdout


def test_collision_checks_are_explicit_frontend_gate_not_backend_collection(pytestconfig) -> None:
    root = Path(__file__).resolve().parents[1]
    module = root / "frontend/e2e/cache_fixture_ownership.py"
    assert module.is_file()
    assert not (root / "tests/test_cache_fixture_ownership.py").exists()
    assert not any(
        fnmatch.fnmatch(module.name, pattern) for pattern in pytestconfig.getini("python_files")
    )
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["frontend"]["steps"]
    names = [step["name"] for step in steps]
    check = names.index("Refuse foreign cache fixture endpoints")
    assert names.index("Install frontend dependencies") < check
    assert names.index("Install locked cache fixture backend") < check
    assert names.index("Install locked cache fixture backend") < names.index("Vitest") < check
    assert check < names.index("Browser HTTP-cache isolation regression")
    assert steps[check]["run"] == "uv run pytest -q frontend/e2e/cache_fixture_ownership.py"
    assert steps[check]["working-directory"] == "."
    assert steps[check]["env"]["UV_PROJECT_ENVIRONMENT"] == ".uv-venv"
    assert not steps[check].get("continue-on-error", False)
