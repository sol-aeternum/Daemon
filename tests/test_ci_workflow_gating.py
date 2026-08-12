from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_frontend_browser_regression_blocking_sequence_is_anchored() -> None:
    workflow = _load_ci_workflow()
    frontend_steps = [name for job_id, name, _ in _iter_job_steps(workflow) if job_id == "frontend"]

    vitest_idx = frontend_steps.index("Vitest")
    chromium_idx = frontend_steps.index("Install Chromium for browser regressions")
    browser_idx = frontend_steps.index("Browser regression inventory")
    build_idx = frontend_steps.index("Build")

    assert vitest_idx < chromium_idx < browser_idx < build_idx
