from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.eval.bench_db import BenchmarkDatabaseGuardError
from tests.longmemeval import evaluate, rejudge


def synthetic_rows() -> list[dict[str, Any]]:
    return [
        {
            "question_id": qid,
            "question": "What is the fictional cat called?",
            "reference": "Miso",
            "hypothesis": "Miso",
            "category": "IE-user",
            "question_type": "single-session-user",
            "judgment": "correct",
            "memory_context_sha256": "a" * 64,
            "system_prompt_sha256": "b" * 64,
        }
        for qid in ("synthetic-1", "synthetic-2_abs")
    ]


def test_reservation_accounts_for_prior_spend_and_rejects_invalid_values() -> None:
    rows = synthetic_rows()
    reservation = rejudge.cost_reservation(rows, "official", Decimal("0"))
    assert 0 < reservation < Decimal("0.1")
    assert rejudge.cost_reservation(rows, "generic", Decimal("0")) > reservation
    for prior in ("NaN", "Infinity", "-1", "10", "11"):
        with pytest.raises(ValueError):
            rejudge.cost_reservation(rows, "official", Decimal(prior))


@pytest.mark.parametrize("arm", ("a", "a2", "b"))
def test_saved_input_is_exactly_frozen_dev_arm(arm: str) -> None:
    rows, source = rejudge.load_inputs(arm)
    assert len(rows) == len({row["question_id"] for row in rows}) == 50
    assert source["dev_sha256"] == rejudge.DEV_SHA256
    assert all("question_type" in row for row in rows)


def test_dev_pin_cannot_be_overridden_by_cli_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "different.json"
    path.write_text("[]")
    monkeypatch.setattr(rejudge, "DEV_PATH", path)
    with pytest.raises(ValueError, match="Dev dataset pin changed"):
        rejudge.load_inputs("a")


def stub_runtime(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(rejudge, "load_inputs", lambda arm: (rows, {"test": "synthetic"}))
    monkeypatch.setattr(rejudge, "git_state", lambda root: {"sha": "synthetic", "dirty": False})
    monkeypatch.setattr(
        evaluate,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql://b:b@127.0.0.1/daemon_lme_bench"),
    )


@pytest.mark.asyncio
async def test_success_records_raw_metadata_and_never_replays_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = synthetic_rows()
    before = copy.deepcopy(rows)
    stub_runtime(monkeypatch, rows)
    calls: list[str] = []

    async def judge(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["question_id"])
        return {
            "official_judgment": "correct",
            "official_judge_raw_output": "yes",
            "official_judge_finish_reason": "stop",
            "official_judge_fingerprint": "fp_synthetic",
            "official_judge_usage": {"prompt_tokens": 100, "completion_tokens": 1},
        }

    monkeypatch.setattr(evaluate, "judge_answer_official", judge)
    output = tmp_path / "fresh"
    manifest = await rejudge.run("a", "official", output, Decimal("0"))
    assert manifest["status"] == "completed"
    assert manifest["calls_completed"] == 2
    assert manifest["database_access"] is False
    assert manifest["cost"]["actual_billed_cost_usd"] is None
    assert manifest["cost"]["usage_cost_estimate_usd"] == "0.0005200"
    assert rows == before
    records = [
        json.loads(line) for line in (output / evaluate.RESULTS_FILENAME).read_text().splitlines()
    ]
    assert records[0]["official_judge_raw_output"] == "yes"
    assert records[0]["original_judgment"] == "correct"
    with pytest.raises(FileExistsError):
        await rejudge.run("a", "official", output, Decimal("0"))
    assert calls == ["synthetic-1", "synthetic-2_abs"]


@pytest.mark.asyncio
async def test_uncertain_provider_failure_retains_pending_and_whole_run_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_runtime(monkeypatch, synthetic_rows())
    calls = 0

    async def judge(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise evaluate.BenchmarkProviderError("synthetic private provider detail")
        return {
            "official_judgment": "correct",
            "official_judge_raw_output": "yes",
            "official_judge_usage": {"prompt_tokens": 100, "completion_tokens": 1},
        }

    monkeypatch.setattr(evaluate, "judge_answer_official", judge)
    output = tmp_path / "failed"
    with pytest.raises(evaluate.BenchmarkProviderError):
        await rejudge.run("a", "official", output, Decimal("0.5"))
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["status"] == "stopped_reconciliation_required"
    assert manifest["pending_question_id"] == "synthetic-2_abs"
    assert manifest["calls_completed"] == 1
    assert Decimal(manifest["cost"]["cumulative_reserved_usd"]) > Decimal("0.5")
    assert "synthetic private provider detail" not in json.dumps(manifest)
    assert calls == 2


@pytest.mark.asyncio
async def test_production_environment_refused_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_runtime(monkeypatch, synthetic_rows())
    monkeypatch.setattr(
        evaluate,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql://b:b@host/production"),
    )
    with pytest.raises(BenchmarkDatabaseGuardError):
        await rejudge.run("a", "official", tmp_path / "refused", Decimal("0"))
    assert not (tmp_path / "refused").exists()


@pytest.mark.asyncio
async def test_missing_usage_stops_after_durable_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_runtime(monkeypatch, synthetic_rows())
    calls = 0

    async def judge(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "official_judgment": "correct",
            "official_judge_raw_output": "yes",
            "official_judge_usage": {},
        }

    monkeypatch.setattr(evaluate, "judge_answer_official", judge)
    output = tmp_path / "no-usage"
    with pytest.raises(RuntimeError, match="Missing judge usage"):
        await rejudge.run("a", "official", output, Decimal("0"))
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["status"] == "stopped_reconciliation_required"
    assert manifest["cost"]["usage_cost_estimate_usd"] is None
    assert manifest["pending_question_id"] == "synthetic-1"
    assert manifest["calls_completed"] == 1
    assert len((output / evaluate.RESULTS_FILENAME).read_text().splitlines()) == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_dirty_code_refused_before_paid_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_runtime(monkeypatch, synthetic_rows())
    monkeypatch.setattr(rejudge, "git_state", lambda root: {"dirty": True})
    with pytest.raises(ValueError, match="Commit the measurement code"):
        await rejudge.run("a", "official", tmp_path / "dirty", Decimal("0"))
    assert not (tmp_path / "dirty").exists()


def failed_prefix(tmp_path: Path) -> tuple[Path, list[dict[str, Any]]]:
    rows = synthetic_rows()
    path = tmp_path / "original"
    path.mkdir()
    manifest = {
        "run_id": "original-run",
        "mode": "judge_only_saved_dev_hypotheses",
        "arm": "b",
        "metric": "generic",
        "status": "stopped_reconciliation_required",
        "source": {"test": "synthetic"},
        "calls_planned": 2,
        "calls_completed": 1,
        "pending_question_id": "synthetic-2_abs",
        "cost": {"cumulative_reserved_usd": "1.5595300"},
    }
    receipt = {**rows[0], "original_judgment": rows[0]["judgment"], "run_id": "original-run"}
    (path / "run_manifest.json").write_text(json.dumps(manifest))
    (path / evaluate.RESULTS_FILENAME).write_text(json.dumps(receipt) + "\n")
    return path, rows


@pytest.mark.asyncio
async def test_residual_is_fresh_excludes_completed_and_preserves_failed_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed, rows = failed_prefix(tmp_path)
    stub_runtime(monkeypatch, rows)
    before = {p.name: p.read_bytes() for p in failed.iterdir()}
    calls: list[str] = []

    async def judge(
        question: str, hypothesis: str, reference: str, **kwargs: Any
    ) -> tuple[str, dict[str, int]]:
        calls.append(question)
        return "correct", {"prompt_tokens": 100, "completion_tokens": 2}

    monkeypatch.setattr(evaluate, "_judge_with_usage", judge)
    output = tmp_path / "residual"
    result = await rejudge.run(
        "b",
        "generic",
        output,
        Decimal("1.5595300"),
        residual_from=failed,
        replacement_question_id="synthetic-2_abs",
    )
    assert result["calls_planned"] == result["calls_completed"] == len(calls) == 1
    assert result["residual"]["verified_completed_question_ids"] == ["synthetic-1"]
    assert result["residual"]["remaining_question_ids"] == ["synthetic-2_abs"]
    assert result["residual"]["new_replacement_explicitly_authorized"] is True
    assert Decimal(result["cost"]["cumulative_reserved_usd"]) > Decimal("1.5595300")
    receipt = json.loads((output / evaluate.RESULTS_FILENAME).read_text())
    assert receipt["question_id"] == "synthetic-2_abs"
    assert receipt["new_replacement_for_uncertain_request"] is True
    assert receipt["residual_of_run_id"] == "original-run"
    assert {p.name: p.read_bytes() for p in failed.iterdir()} == before
    with pytest.raises(FileExistsError):
        await rejudge.run(
            "b",
            "generic",
            output,
            Decimal("1.5595300"),
            residual_from=failed,
            replacement_question_id="synthetic-2_abs",
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "arm",
        "metric",
        "source",
        "status",
        "pending",
        "count",
        "receipt",
        "duplicate",
        "nested",
        "reservation",
    ],
)
def test_residual_rejects_unreconciled_or_mismatched_evidence(
    tmp_path: Path, mutation: str
) -> None:
    failed, rows = failed_prefix(tmp_path)
    manifest_path = failed / "run_manifest.json"
    result_path = failed / evaluate.RESULTS_FILENAME
    manifest = json.loads(manifest_path.read_text())
    if mutation in {"arm", "metric", "source", "status"}:
        manifest[mutation] = "different"
    elif mutation == "pending":
        manifest["pending_question_id"] = "not-the-next-row"
    elif mutation == "count":
        manifest["calls_completed"] = 0
    elif mutation == "nested":
        manifest["residual"] = {"original_run_id": "some-prior-run"}
    elif mutation == "reservation":
        manifest["cost"]["cumulative_reserved_usd"] = "2"
    elif mutation == "receipt":
        r = json.loads(result_path.read_text())
        r["hypothesis"] = "different"
        result_path.write_text(json.dumps(r) + "\n")
    elif mutation == "duplicate":
        result_path.write_text(result_path.read_text() * 2)
        manifest["calls_completed"] = 2
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        rejudge.reconcile_residual(
            rows,
            {"test": "synthetic"},
            "b",
            "generic",
            failed,
            "synthetic-2_abs",
            Decimal("1.5595300"),
        )


@pytest.mark.asyncio
async def test_residual_requires_explicit_replacement_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed, rows = failed_prefix(tmp_path)
    stub_runtime(monkeypatch, rows)
    for replacement in (None, "not-pending"):
        with pytest.raises(ValueError):
            await rejudge.run(
                "b",
                "generic",
                tmp_path / "refused",
                Decimal("1.5595300"),
                residual_from=failed,
                replacement_question_id=replacement,
            )
    assert not (tmp_path / "refused").exists()


@pytest.mark.asyncio
async def test_safe_failure_metadata_keeps_status_without_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_runtime(monkeypatch, synthetic_rows())

    class RateLimitError(Exception):
        status_code = 429

    async def judge(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            raise RateLimitError("NEVER-RETAIN-THIS-PROVIDER-TEXT")
        except RateLimitError as cause:
            raise evaluate.BenchmarkProviderError("NEVER-RETAIN-THIS-WRAPPER-TEXT") from cause

    monkeypatch.setattr(evaluate, "judge_answer_official", judge)
    output = tmp_path / "structured-error"
    with pytest.raises(evaluate.BenchmarkProviderError):
        await rejudge.run("a", "official", output, Decimal("0"))
    raw = (output / "run_manifest.json").read_text()
    manifest = json.loads(raw)
    assert manifest["failure_diagnostics"] == {
        "provider_exception_type": "RateLimitError",
        "http_status_code": 429,
    }
    assert manifest["pending_question_id"] == "synthetic-1"
    assert "NEVER-RETAIN" not in raw


def test_failure_diagnostics_allowlists_types_and_numeric_status_only() -> None:
    class UntrustedType(Exception):
        status_code = "SECRET-NOT-A-STATUS"

    exc = UntrustedType("SECRET-MESSAGE")
    exc.__cause__ = exc
    assert evaluate.benchmark_failure_diagnostics(exc) == {
        "provider_exception_type": "OtherError",
        "http_status_code": None,
    }
