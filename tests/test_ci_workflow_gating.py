from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
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
    [(None, ".venv"), ("", ".venv"), ("custom venv", "custom venv")],
    ids=["unset", "empty", "explicit-override"],
)
def test_local_ci_exports_default_or_override_without_redirecting_type_environment(
    tmp_path: Path,
    inherited_environment: str | None,
    expected_environment: str,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    runner = scripts / "local_ci.sh"
    shutil.copyfile(LOCAL_CI, runner)
    # This fixture checks shell export/aggregation, not real ownership bootstrap.
    # The actual adapter is covered separately; never launch it from fake source.
    (scripts / "run_managed_backend_pytest.py").write_text(
        "import os\n"
        "with open(os.environ['GATE_ENV_LOG'], 'a', encoding='utf-8') as stream:\n"
        "    stream.write('managed-pytest\\t' + os.environ.get('UV_PROJECT_ENVIRONMENT', '') + '\\n')\n",
        encoding="utf-8",
    )

    # Run the real CLI in isolation. uv and the pytest adapter are observers;
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
    assert [command for command, _ in invocations].count("managed-pytest") == 1


def test_all_four_backend_environment_selectors_and_type_gate_strength_are_aligned() -> None:
    pyright = json.loads((REPO_ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    basedpyright = project["tool"]["basedpyright"]
    local_ci = LOCAL_CI.read_text(encoding="utf-8")
    workflow = _load_ci_workflow()
    assert pyright["venv"] == basedpyright["venv"] == ".venv"
    assert 'export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.venv}"' in local_ci
    assert workflow["jobs"]["backend"]["env"]["UV_PROJECT_ENVIRONMENT"] == ".venv"
    assert pyright["venvPath"] == basedpyright["venvPath"] == "."
    assert pyright["typeCheckingMode"] == basedpyright["typeCheckingMode"] == "standard"
    assert pyright["baselineFile"] == basedpyright["baselineFile"] == ".basedpyright/baseline.json"
    assert project["tool"]["ruff"]["include"] == [
        "orchestrator/**/*.py",
        "providers/**/*.py",
        "scripts/**/*.py",
        "tests/**/*.py",
        "tts/**/*.py",
    ]
    steps = {(job, name): step for job, name, step in _iter_job_steps(workflow)}
    assert (
        steps[("backend", "Basedpyright error gate")]["run"] == "uv run basedpyright --level error"
    )
    assert "backend|basedpyright|blocking|uv run basedpyright --level error" in local_ci
    # Name alignment only: no guessed hosted bootstrap or hidden fixture skip.
    assert (
        steps[("backend", "Pytest")]["run"] == "uv run python scripts/run_hosted_backend_pytest.py"
    )


def test_frontend_browser_regression_blocking_sequence_is_anchored() -> None:
    workflow = _load_ci_workflow()
    frontend_steps = [name for job_id, name, _ in _iter_job_steps(workflow) if job_id == "frontend"]

    vitest_idx = frontend_steps.index("Vitest")
    chromium_idx = frontend_steps.index("Install Chromium for browser regressions")
    browser_idx = frontend_steps.index("Browser regression inventory")
    build_idx = frontend_steps.index("Build")

    assert vitest_idx < chromium_idx < browser_idx < build_idx


def test_cross_language_vitest_has_locked_python_before_execution() -> None:
    workflow = _load_ci_workflow()
    frontend_steps = [name for job_id, name, _ in _iter_job_steps(workflow) if job_id == "frontend"]
    assert (
        frontend_steps.index("Set up uv for HTTP-cache isolation fixture")
        < frontend_steps.index("Install locked cache fixture backend")
        < frontend_steps.index("Vitest")
    )
    local_ci = LOCAL_CI.read_text(encoding="utf-8")
    assert local_ci.index(
        "frontend|cache-fixture-python|blocking|uv sync --locked"
    ) < local_ci.index("frontend|test-run|blocking|npm --prefix frontend run test:run")


def test_backend_pytest_diagnostics_have_bounded_execution_and_narrow_upload() -> None:
    workflow = _load_ci_workflow()
    backend = workflow["jobs"]["backend"]
    steps = backend["steps"]
    by_name = {step["name"]: step for step in steps}
    pytest_step = by_name["Pytest"]
    upload = by_name["Upload backend pytest diagnostics"]

    assert backend["timeout-minutes"] == 30
    assert pytest_step["timeout-minutes"] == 25
    assert pytest_step["run"] == "uv run python scripts/run_hosted_backend_pytest.py"
    assert steps.index(pytest_step) < steps.index(upload)
    assert upload["if"] == "always()"
    assert upload["timeout-minutes"] == 3
    assert upload["uses"] == "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
    assert upload["with"]["path"].splitlines() == [
        "pytest-diagnostics/pytest.log",
        "pytest-diagnostics/pytest-results.xml",
        "pytest-diagnostics/hosted-closure.json",
    ]
    assert upload["with"]["if-no-files-found"] == "warn"
    assert upload["with"]["retention-days"] == 7
    assert not upload["with"].get("include-hidden-files", False)
