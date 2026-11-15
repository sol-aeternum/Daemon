"""Offline safety/measurement regressions: no real children or browser calls."""

import copy
import json
import signal
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from frontend.e2e import issue490_ci_probe as probe


def candidate():
    return {
        "schema": 1,
        "workers": 4,
        "retries": [1] * 7,
        "indices": list(range(69)),
        "global_errors": 0,
        "reporter_errors": 0,
        "status": "passed",
        "attempts": [
            {
                "index": i,
                "retry": 0,
                "status": "passed",
                "duration_ms": 1000,
                "worker": i % 4,
                "start_epoch_ms": 1000,
                "window": window() if i == 5 else None,
            }
            for i in range(69)
        ],
    }


def window():
    return {
        "start_epoch_ms": 1000,
        "end_epoch_ms": 2020,
        "start_monotonic_ms": 20,
        "end_monotonic_ms": 1040,
        "fps": 40,
    }


@pytest.fixture
def manifest():
    data = json.loads(probe.MANIFEST.read_text())
    assert len(data) == len(set(map(tuple, data))) == 69
    return data


def test_pass_and_static_manifest_mapping(manifest):
    result = probe.finalize_results(candidate(), manifest, 0)
    assert result["integrity"] and result["inventory_passed"]
    assert result["cases"][5]["identity"] == manifest[5]


def test_configured_retry_is_preserved_and_failure_not_hidden(manifest):
    data = candidate()
    first = data["attempts"][5]
    first["status"] = "failed"
    first["window"]["fps"] = 20
    second = copy.deepcopy(first)
    second.update(retry=1, status="passed")
    second["window"]["fps"] = 40
    data["attempts"].append(second)
    result = probe.finalize_results(data, manifest, 0)
    assert result["integrity"] and result["inventory_passed"]
    assert [a["window"]["fps"] for a in result["cases"][5]["attempts"]] == [20, 40]
    second["status"] = "failed"
    second["window"]["fps"] = 25
    data["status"] = "failed"
    result = probe.finalize_results(data, manifest, 1)
    assert result["integrity"] and not result["inventory_passed"]


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "workers", "retries"])
def test_inventory_and_acceptance_identity_rejects_drift(manifest, change):
    data = candidate()
    if change == "missing":
        data["indices"].pop()
    elif change == "extra":
        data["indices"].append(69)
    elif change == "duplicate":
        data["indices"][-1] = 0
    elif change == "workers":
        data["workers"] = 1
    else:
        data["retries"] = [0]
    with pytest.raises(ValueError):
        probe.finalize_results(data, manifest, 0)


@pytest.mark.parametrize(
    "change",
    ["skip", "missing", "duplicate", "global", "reporter", "exit", "no_window", "threshold"],
)
def test_incomplete_or_invalid_capture_never_passes(manifest, change):
    data = candidate()
    if change == "skip":
        data["attempts"][0]["status"] = "skipped"
    elif change == "missing":
        data["attempts"].pop()
    elif change == "duplicate":
        data["attempts"].append(data["attempts"][0])
    elif change in ("global", "reporter"):
        data[change + "_errors"] = 1
    elif change == "no_window":
        data["attempts"][5]["window"] = None
    elif change == "threshold":
        data["attempts"][5]["window"]["fps"] = 30
    result = probe.finalize_results(data, manifest, -9 if change == "exit" else 0)
    assert not result["integrity"] and not result["inventory_passed"]


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), True, "https://private.invalid/", -1]
)
def test_window_numbers_cannot_carry_text_or_nonfinite_data(value):
    data = window()
    data["fps"] = value
    with pytest.raises(ValueError):
        probe.validate_window(data)


def test_extra_browser_fields_are_rejected_not_copied(manifest):
    for level in ("root", "attempt", "window"):
        data = candidate()
        target = data if level == "root" else data["attempts"][5]
        if level == "window":
            target = target["window"]
        target["headers"] = "synthetic-private-canary"
        with pytest.raises(ValueError):
            probe.finalize_results(data, manifest, 0)


def test_regular_bounded_json_only(tmp_path):
    raw = tmp_path / "candidate.json"
    raw.write_text('{"ok": 1}')
    assert probe.read_json(raw, tmp_path) == {"ok": 1}
    link = tmp_path / "link.json"
    link.symlink_to(raw)
    with pytest.raises(ValueError):
        probe.read_json(link, tmp_path)
    with pytest.raises(ValueError):
        probe.read_json(raw, tmp_path, cap=2)
    with pytest.raises(ValueError):
        probe.read_json(raw, tmp_path / "different")


def test_proc_parsing_and_counter_reset_inputs():
    assert probe.cpu_ticks("cpu 1 2 3 4 5 6 7 8 9 10\n") == list(range(1, 9))
    assert probe.psi_total("some avg10=1.00 avg60=0.1 avg300=0 total=123\n") == 123
    with pytest.raises(ValueError):
        probe.cpu_ticks("cpu -1 2 3 4 5 6 7 8\n")
    with pytest.raises(StopIteration):
        probe.psi_total("unavailable")
    assert probe.parse_limit("cpu.max", "200000 100000") == [200000, 100000]
    assert probe.parse_limit("cpu.max", "max 100000") == [None, 100000]
    assert probe.parse_limit("memory.max", "max") == [None]
    for text in ("1", "1 2 3", "max max", "0 1", "1 0"):
        with pytest.raises(ValueError):
            probe.parse_limit("cpu.max", text)


def test_environment_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("ISSUE490_SYNTHETIC_FORBIDDEN", "canary")
    result = probe.child_environment()
    assert "ISSUE490_SYNTHETIC_FORBIDDEN" not in result
    assert result["CI"] == "1"
    assert result["NEXT_PUBLIC_API_URL"] == "http://127.0.0.1:3181"


def test_opening_only_same_repo_admission_and_workflow_parity():
    assert probe.eligible("pull_request", "opened", probe.BRANCH, True, 1)
    for event, action, branch, own, attempt in [
        ("push", "opened", probe.BRANCH, True, 1),
        ("pull_request", "synchronize", probe.BRANCH, True, 1),
        ("pull_request", "reopened", probe.BRANCH, True, 1),
        ("pull_request", "opened", probe.BRANCH, False, 1),
        ("pull_request", "opened", probe.BRANCH, True, 2),
    ]:
        assert not probe.eligible(event, action, branch, own, attempt)
    workflow = yaml.safe_load((probe.ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["browser-inventory"]
    assert "github.event.action == 'opened'" in job["if"]
    assert "github.run_attempt == '1'" in job["if"]
    assert "head.repo.full_name == github.repository" in job["if"]
    assert job["permissions"] == {"contents": "read"}
    uploads = [step for step in job["steps"] if step.get("name", "").startswith("Upload")]
    assert probe.BRANCH in uploads[0]["if"] and "!=" in uploads[0]["if"]
    assert uploads[1]["with"]["path"].splitlines() == [
        "frontend/test-results/issue490-public/runner.json",
        "frontend/test-results/issue490-public/results.json",
        "frontend/test-results/issue490-public/host.jsonl",
    ]
    assert "complete" in uploads[1]["if"]


def test_bounded_wait_never_replays_or_signals_other_processes():
    class Child:
        def __init__(self, outcomes):
            self.outcomes = iter(outcomes)
            self.signals = []
            self.waits = []

        def wait(self, timeout):
            self.waits.append(timeout)
            value = next(self.outcomes)
            if value is None:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return value

        def send_signal(self, sig):
            self.signals.append(sig)

    child = Child([0])
    assert probe.wait_bounded(child, 900) == {"exit": 0, "timeout": False, "unresolved": False}
    assert not child.signals
    child = Child([None] * 4)
    assert probe.wait_bounded(child, 900) == {"exit": None, "timeout": True, "unresolved": True}
    assert child.signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]
    assert child.waits == [900, 5, 5, 5]


def test_no_completion_sentinel_after_failed_export(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "PUBLIC", tmp_path / "public")
    with pytest.raises(ValueError):
        probe.export({"schema": 1}, [{"unexpected": "canary"}], {})
    assert not (probe.PUBLIC / "complete").exists()
    probe.export({"schema": 1}, [], {"schema": 1})
    assert (probe.PUBLIC / "complete").is_file()
    assert sorted(p.name for p in probe.PUBLIC.iterdir()) == [
        "complete",
        "host.jsonl",
        "results.json",
        "runner.json",
    ]


@pytest.mark.parametrize("unavailable", [False, True])
def test_unsuitable_runner_never_launches_build_or_browser(monkeypatch, unavailable):
    monkeypatch.setattr(probe, "runtime_versions", lambda: {"node": "v24.0.0"})
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="a" * 40))
    monkeypatch.setattr(probe, "runner_hardware", lambda: {"effective_cpu_ceiling": 2})

    def telemetry():
        if unavailable:
            raise OSError("synthetic private context")
        return {}

    monkeypatch.setattr(probe, "sample_host", telemetry)

    def forbidden(*args, **kwargs):
        raise AssertionError("no child must be launched")

    monkeypatch.setattr(probe, "run_child", forbidden)
    exported = []
    monkeypatch.setattr(
        probe, "export", lambda runner, rows, results: exported.append((runner, rows, results))
    )
    assert probe.probe("a" * 40, 1, 1) == 2
    runner, rows, results = exported[0]
    assert runner["browser_invocations"] == 0 and not rows
    assert not results["inventory_passed"]
    assert runner["codes"] == [
        "hardware_or_pressure_unavailable" if unavailable else "insufficient_cpu"
    ]
    assert "synthetic private context" not in json.dumps(exported)
