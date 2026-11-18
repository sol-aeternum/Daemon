from __future__ import annotations

import os
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
    ("browser-inventory", "Browser regression inventory"),
    ("postgres-inventory", "PostgreSQL opt-in inventory"),
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


@pytest.mark.parametrize("shard", [1, 2, 3, 4])
def test_postgres_inventory_reports_browser_exclusions_and_pytest_result(
    tmp_path: Path, shard: int
) -> None:
    steps = _load_ci_workflow()["jobs"]["postgres-inventory"]["steps"]
    inventory = next(step for step in steps if step["name"] == "PostgreSQL opt-in inventory")
    upload = next(step for step in steps if step["name"] == "Upload PostgreSQL opt-in results")
    cases = {
        "test_b1_browser_bootstrap_postgres": "test_b1_actual_https_production_browser_empty_history",
        "test_history_ui_browser_postgres": "test_actual_page_saved_partial_second_issued_device_and_terminal",
        "test_resource_history_postgres": "test_native_components_real_browser_sessions_proxy_and_history",
        "test_resource_http_postgres": "test_actual_browser_contexts_and_fresh_composition_restart",
    }
    files = {f"tests/{module}.py" for module in cases}
    selectors = {f"tests/{module}.py::{case}" for module, case in cases.items()}
    (tmp_path / "tests").mkdir()
    for file in files:
        # Exercise the actual workflow's file discovery against the real files.
        shutil.copyfile(REPO_ROOT / file, tmp_path / file)

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("postgres_inventory_shards.py", "postgres_inventory_timings.json"):
        shutil.copyfile(REPO_ROOT / "scripts" / name, scripts / name)

    # Run the exact shell recipe, but never start PostgreSQL, npm, or pytest here.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > pytest-argv\nexit 1\n', encoding="utf-8")
    uv.chmod(0o755)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-c", inventory["run"]],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:{os.defpath}",
            "RUNNER_TEMP": str(tmp_path / "runner"),
            "GITHUB_STEP_SUMMARY": str(summary),
            "POSTGRES_INVENTORY_SHARD": str(shard),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1, result.stderr
    assert "::warning::PostgreSQL inventory pytest exited 1" in result.stdout
    args = (tmp_path / "pytest-argv").read_text(encoding="utf-8").splitlines()
    assert "--compute-metadata-postgres" in args
    assert "--junitxml=pytest-diagnostics/postgres-opt-in.xml" in args
    manifest = (tmp_path / "pytest-diagnostics/postgres-opt-in-files.txt").read_text().splitlines()
    assert len(manifest) == 1 and set(manifest) <= files
    assert set(args) & files == set(manifest), "run exactly the selected mixed modules"
    assert "--durations=50" in args
    assert {
        arg.removeprefix("--deselect=") for arg in args if arg.startswith("--deselect=")
    } == selectors
    report = "pytest-diagnostics/postgres-opt-in-scope.md"
    text = (tmp_path / report).read_text(encoding="utf-8")
    assert text == summary.read_text(encoding="utf-8")
    assert "browser tests not run" in text and "pytest exit code: `1`" in text
    assert "Missing JUnit does not indicate a pass" in text
    assert all(f"- `{selector}`" in text for selector in selectors)
    assert upload["if"] == "always()"
    assert set(upload["with"]["path"].splitlines()) == {
        report,
        "pytest-diagnostics/postgres-opt-in.xml",
        "pytest-diagnostics/postgres-opt-in-files.txt",
        "pytest-diagnostics/postgres-opt-in-output.txt",
    }
    assert upload["with"]["name"] == "postgres-opt-in-results-${{ matrix.shard }}"


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
    browser_idx = frontend_steps.index("Browser HTTP-cache isolation regression")
    build_idx = frontend_steps.index("Build")

    assert vitest_idx < chromium_idx < browser_idx < build_idx
    assert "Browser regression inventory" not in frontend_steps


def test_browser_inventory_job_stays_parallel_and_nonblocking() -> None:
    workflow = _load_ci_workflow()
    job = workflow["jobs"]["browser-inventory"]
    steps = [name for job_id, name, _ in _iter_job_steps(workflow) if job_id == "browser-inventory"]
    by_name = {step["name"]: step for step in job["steps"]}

    # Its own job: never gates or delays the required Frontend gates check.
    assert "needs" not in job and not job.get("continue-on-error", False)
    assert not any(
        "browser-inventory" in str(other.get("needs", "")) for other in workflow["jobs"].values()
    )
    assert job["name"] != workflow["jobs"]["frontend"]["name"]
    assert (
        steps.index("Install frontend dependencies")
        < steps.index("Install Chromium for browser regressions")
        < steps.index("Browser regression inventory")
        < steps.index("Upload browser regression screenshots")
    )
    assert by_name["Browser regression inventory"]["run"] == "npm run test:browser:qualification"
    upload = by_name["Upload browser regression screenshots"]
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "browser-regressions"


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
    assert pytest_step["run"] == "bash scripts/run_backend_pytest.sh"
    assert steps.index(pytest_step) < steps.index(upload)
    assert upload["if"] == "always()"
    assert upload["timeout-minutes"] == 3
    assert upload["uses"] == "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
    assert upload["with"]["path"].splitlines() == [
        "pytest-diagnostics/pytest.log",
        "pytest-diagnostics/pytest-results.xml",
    ]
    assert upload["with"]["if-no-files-found"] == "warn"
    assert upload["with"]["retention-days"] == 7
    assert not upload["with"].get("include-hidden-files", False)
