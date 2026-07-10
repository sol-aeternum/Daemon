from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

ALLOWED_CONTINUE_ON_ERROR_STEPS = {
    ("backend", "Bandit SAST inventory"),
    ("backend", "Python dependency audit inventory"),
    ("backend", "Pytest"),
    ("frontend", "ESLint"),
    ("frontend", "Prettier check"),
    ("frontend", "Frontend dependency audit inventory"),
    ("frontend", "Vitest"),
}

TEMPORARY_BASELINE_INVENTORY_STEPS = {
    ("backend", "Pytest"),
    ("frontend", "ESLint"),
    ("frontend", "Prettier check"),
    ("frontend", "Vitest"),
}

REQUIRED_BLOCKING_STEPS = {
    ("frontend", "Type check"),
    ("frontend", "Build"),
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


def test_ci_continue_on_error_is_limited_to_inventory_steps() -> None:
    workflow = _load_ci_workflow()
    offenders = [
        (job_id, name)
        for job_id, name, step in _iter_job_steps(workflow)
        if step.get("continue-on-error") is True
        and (job_id, name) not in ALLOWED_CONTINUE_ON_ERROR_STEPS
    ]

    assert offenders == []


def test_ci_currently_green_test_and_build_steps_are_blocking() -> None:
    workflow = _load_ci_workflow()
    steps_by_key = {(job_id, name): step for job_id, name, step in _iter_job_steps(workflow)}

    missing = REQUIRED_BLOCKING_STEPS - steps_by_key.keys()
    assert missing == set()

    non_blocking = [
        key for key in REQUIRED_BLOCKING_STEPS if steps_by_key[key].get("continue-on-error") is True
    ]
    assert non_blocking == []


def test_known_red_baseline_steps_stay_nonblocking_until_precursors_land() -> None:
    workflow = _load_ci_workflow()
    steps_by_key = {(job_id, name): step for job_id, name, step in _iter_job_steps(workflow)}

    missing = TEMPORARY_BASELINE_INVENTORY_STEPS - steps_by_key.keys()
    assert missing == set()

    blocking = [
        key
        for key in TEMPORARY_BASELINE_INVENTORY_STEPS
        if steps_by_key[key].get("continue-on-error") is not True
    ]
    assert blocking == []
