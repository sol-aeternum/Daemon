from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LOCAL_CI = REPO_ROOT / "scripts" / "local_ci.sh"

ALLOWED_CONTINUE_ON_ERROR_STEPS = {
    ("backend", "Bandit full inventory"),
    ("frontend", "Browser regression inventory"),
}

BLOCKING_GATE_STEPS = {
    ("backend", "Sync backend dependencies"),
    ("backend", "Ruff lint"),
    ("backend", "Ruff format check"),
    ("backend", "Basedpyright error gate"),
    ("backend", "Bandit high-severity gate"),
    ("backend", "Python dependency audit"),
    ("backend", "Pytest"),
    ("frontend", "Install frontend dependencies"),
    ("frontend", "Type check"),
    ("frontend", "ESLint"),
    ("frontend", "Prettier check"),
    ("frontend", "Frontend dependency audit"),
    ("frontend", "Vitest"),
    ("frontend", "Build"),
    ("feature-matrix", "Validate feature matrix"),
    ("pre-commit-security", "Run pre-commit hooks"),
    ("pre-commit-security", "Run commit message hook"),
}

EXPECTED_SECURITY_COMMANDS = {
    ("backend", "Bandit high-severity gate"): (
        "uv run bandit -r orchestrator providers scripts tests -lll"
    ),
    ("backend", "Bandit full inventory"): ("uv run bandit -r orchestrator providers scripts tests"),
    ("backend", "Python dependency audit"): "uv run pip-audit",
    ("frontend", "Frontend dependency audit"): "npm run audit:ci",
}


def _load_ci_workflow() -> dict[str, Any]:
    with CI_WORKFLOW.open("r", encoding="utf-8") as file_obj:
        workflow = yaml.safe_load(file_obj)
    assert isinstance(workflow, dict)
    return workflow


def _iter_job_steps(workflow: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)

    steps: list[tuple[str, str, dict[str, Any]]] = []
    for job_id, job in jobs.items():
        assert isinstance(job_id, str)
        assert isinstance(job, dict)
        job_steps = job.get("steps", [])
        assert isinstance(job_steps, list)
        for step in job_steps:
            assert isinstance(step, dict)
            name = step.get("name")
            if isinstance(name, str):
                steps.append((job_id, name, step))
    return steps


def test_ci_documented_gates_are_present_and_blocking() -> None:
    workflow = _load_ci_workflow()
    all_steps = _iter_job_steps(workflow)
    steps_by_key = {(job_id, name): step for job_id, name, step in all_steps}

    missing = BLOCKING_GATE_STEPS - steps_by_key.keys()
    assert not missing, f"Missing documented workflow steps: {missing}"

    accidentally_nonblocking = [
        (job_id, name)
        for job_id, name in BLOCKING_GATE_STEPS
        if steps_by_key[(job_id, name)].get("continue-on-error") is True
    ]
    assert not accidentally_nonblocking, (
        f"Documented blocking steps marked continue-on-error: {accidentally_nonblocking}"
    )

    nonblocking_steps = {
        (job_id, name) for job_id, name, step in all_steps if step.get("continue-on-error") is True
    }
    assert nonblocking_steps == ALLOWED_CONTINUE_ON_ERROR_STEPS, (
        f"Unexpected continue-on-error policy: {nonblocking_steps}"
    )

    for key, expected_command in EXPECTED_SECURITY_COMMANDS.items():
        assert steps_by_key[key].get("run") == expected_command


def test_local_ci_security_policy_matches_workflow() -> None:
    local_ci = LOCAL_CI.read_text(encoding="utf-8")

    expected_gate_rows = {
        "backend|bandit-high|blocking|uv run bandit -r orchestrator providers scripts tests -lll",
        "backend|bandit|inventory|uv run bandit -r orchestrator providers scripts tests",
        "backend|pip-audit|blocking|uv run pip-audit",
        "frontend|audit-ci|blocking|npm --prefix frontend run audit:ci",
    }
    for row in expected_gate_rows:
        assert row in local_ci


@pytest.mark.parametrize(
    ("inherited_environment", "expected_environment"),
    [(None, ".uv-venv"), ("", ".uv-venv"), ("custom venv", "custom venv")],
    ids=["unset", "empty", "explicit-override"],
)
def test_local_ci_uses_basedpyright_project_environment(
    tmp_path: Path,
    inherited_environment: str | None,
    expected_environment: str,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    runner = scripts / "local_ci.sh"
    shutil.copyfile(LOCAL_CI, runner)

    # Run the real CLI in isolation. Only uv is replaced with an observer;
    # no package-manager command or actual quality gate can run via this PATH.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("bash", "dirname", "date"):
        executable = shutil.which(command)
        assert executable is not None, f"Required runner dependency missing: {command}"
        (bin_dir / command).symlink_to(executable)
    (bin_dir / "python").symlink_to(sys.executable)
    uv = bin_dir / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "%s\\t%s\\n" "$*" "${UV_PROJECT_ENVIRONMENT-}" >> "$GATE_ENV_LOG"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)

    log = tmp_path / "gate-environments.log"
    environment = {"PATH": str(bin_dir), "GATE_ENV_LOG": str(log)}
    if inherited_environment is not None:
        environment["UV_PROJECT_ENVIRONMENT"] = inherited_environment
    result = subprocess.run(
        [str(bin_dir / "bash"), str(runner), "backend"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    assert log.is_file(), "The runner did not launch any uv gate"
    invocations = [line.split("\t", 1) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any("basedpyright" in command.split() for command, _ in invocations), (
        "The runner did not launch the Basedpyright gate"
    )
    observed = [value for _, value in invocations]
    assert all(value == expected_environment for value in observed), observed


def test_frontend_browser_regression_blocking_sequence_is_anchored() -> None:
    workflow = _load_ci_workflow()
    frontend_steps = [name for job_id, name, _ in _iter_job_steps(workflow) if job_id == "frontend"]

    vitest_idx = frontend_steps.index("Vitest")
    chromium_idx = frontend_steps.index("Install Chromium for browser regressions")
    browser_idx = frontend_steps.index("Browser regression inventory")
    build_idx = frontend_steps.index("Build")

    assert vitest_idx < chromium_idx < browser_idx < build_idx
