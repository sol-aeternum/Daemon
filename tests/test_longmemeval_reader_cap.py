from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.longmemeval import evaluate, reader_cap, rejudge


def setup_row(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    messages = evaluate.build_parity_answer_messages("Fictional cat name?", "")
    row = {
        "question_id": reader_cap.QUESTION_ID,
        "question": "Fictional cat name?",
        "reference": "Miso",
        "category": "IE-user",
        "question_type": "single-session-user",
        "memory_context": "",
        "memory_context_sha256": evaluate._sha256_text(""),
        "system_prompt_sha256": evaluate._sha256_text(messages[0]["content"]),
        "answer_usage": {"prompt_tokens": 100},
    }
    monkeypatch.setattr(rejudge, "load_inputs", lambda arm: ([row], {"synthetic": True}))
    monkeypatch.setattr(reader_cap, "git_state", lambda root: {"sha": "synthetic", "dirty": False})
    monkeypatch.setattr(
        evaluate,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql://b:b@127.0.0.1/daemon_lme_bench"),
    )
    return row


def test_real_dev_diagnostic_reservation_stays_within_approved_estimate() -> None:
    rows, _ = rejudge.load_inputs("a")
    row = next(row for row in rows if row["question_id"] == reader_cap.QUESTION_ID)
    reserve = reader_cap.reservation(row, Decimal("0"))
    assert reserve == Decimal("0.0491650")
    with pytest.raises(ValueError):
        reader_cap.reservation(row, Decimal("9.99"))


@pytest.mark.asyncio
async def test_paired_caps_preserve_prompt_and_capture_both_judges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_row(monkeypatch)
    calls: list[dict[str, Any]] = []

    async def fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        key = kwargs["bm_call_key"]
        response = {
            "choices": [
                {
                    "message": {
                        "content": {
                            "answer": "Miso",
                            "judge": "CORRECT\nMatches",
                            "official_judge": "yes",
                        }[key]
                    },
                    "finish_reason": "length"
                    if key == "answer" and kwargs["max_tokens"] == 256
                    else "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 2},
            "system_fingerprint": f"fp_{key}",
        }
        evaluate._capture_benchmark_metadata(response, key=key)
        return response

    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", fake)
    output = tmp_path / "paired"
    manifest = await reader_cap.run(output, Decimal("0"))
    assert manifest["status"] == "completed"
    assert manifest["calls_completed"] == 6
    assert manifest["database_access"] is False
    assert manifest["retrieval_calls"] == 0
    assert [c["bm_call_key"] for c in calls] == ["answer", "judge", "official_judge"] * 2
    assert calls[0]["messages"] == calls[3]["messages"]
    assert (calls[0]["max_tokens"], calls[3]["max_tokens"]) == (256, 1024)
    rows = [
        json.loads(line) for line in (output / evaluate.RESULTS_FILENAME).read_text().splitlines()
    ]
    assert [row["answer_finish_reason"] for row in rows] == ["length", "stop"]
    assert all(row["judgment"] == row["official_judgment"] == "correct" for row in rows)
    assert rows[0]["system_prompt_sha256"] == rows[1]["system_prompt_sha256"]
    with pytest.raises(FileExistsError):
        await reader_cap.run(output, Decimal("0"))
    assert len(calls) == 6


@pytest.mark.asyncio
async def test_changed_prompt_stops_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = setup_row(monkeypatch)
    row["system_prompt_sha256"] = "different"
    with pytest.raises(ValueError, match="Saved A system prompt"):
        await reader_cap.run(tmp_path / "refused", Decimal("0"))
    assert not (tmp_path / "refused").exists()


@pytest.mark.asyncio
async def test_missing_usage_keeps_receipt_and_pending_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_row(monkeypatch)
    calls = 0

    async def fake(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": "Miso"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", fake)
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="Missing usage"):
        await reader_cap.run(output, Decimal("0"))
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["status"] == "stopped_reconciliation_required"
    assert manifest["pending_call"] == "answer:256"
    assert len((output / "calls.jsonl").read_text().splitlines()) == 1
    assert Decimal(manifest["cost"]["whole_run_reservation_usd"]) > 0
    assert calls == 1
