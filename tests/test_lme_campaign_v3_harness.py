"""B2a's test-injected sampling, using only fictional data and fake transports."""

from __future__ import annotations

import ast
from contextlib import asynccontextmanager
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import litellm
import pytest

from orchestrator.eval import campaign_guard as guard, campaign_v3, fact_harness
from orchestrator.eval.campaign_layout import V3, layout
from orchestrator.memory.injection import MemoryContextInputs
from tests.longmemeval import evaluate
from tests.test_lme_campaign_contract import (
    fixed_approval as fixed_approval,
    request,
    response,
    synthetic_plan,
)

V3_LAYOUT = layout("v3")
NOW = datetime(2023, 5, 20, tzinfo=timezone.utc)


def instance(tmp_path: Path, *, name: str = "campaign") -> guard.Campaign:
    # Tiny synthetic token counter; this is not a tokenizer/reservation qualification.
    return guard.Campaign(tmp_path / name, synthetic_plan(), lambda _: 1, layout=V3_LAYOUT)


def start(campaign: guard.Campaign) -> None:
    campaign.start_arm("A0", verified_pins=synthetic_plan().pins, restore={"synthetic": True})


def completed_row() -> dict[str, Any]:
    return {
        "question_id": "synthetic-0",
        "category": "IE-user",
        "label_aggregation": "majority_of_3",
        "samples": [
            {
                "sample": sample,
                "hypothesis": "Miso",
                "judgment": "correct",
                "official_judgment": "correct",
                "official_judge_status": "completed",
            }
            for sample in (1, 2, 3)
        ],
        "official_majority": "correct",
        "generic_strict_majority": "correct",
    }


class FakeTransport:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        fail_slot: int | None = None,
        qualified: bool = False,
    ) -> None:
        self.fail_slot = fail_slot
        self.qualified = qualified
        self.calls: list[dict[str, Any]] = []
        self.pendings: list[dict[str, Any]] = []
        self.retrieval = AsyncMock(side_effect=self.retrieve)
        monkeypatch.setattr(evaluate, "build_parity_memory_context", self.retrieval)
        monkeypatch.setattr(litellm, "acompletion", self.complete)
        monkeypatch.setattr(guard, "LLM_START_INTERVAL", 0)
        config = SimpleNamespace(
            timeout_s=1, base_url=guard.BASE_URL, api_key=None, extra_headers=None
        )
        monkeypatch.setattr(
            evaluate, "get_settings", lambda: SimpleNamespace(get_provider_config=lambda _: config)
        )
        evaluate.reset_benchmark_tracking()

    async def retrieve(self, *args: Any, **kwargs: Any) -> tuple[str, MemoryContextInputs]:
        with guard.campaign_call("embedding", request()) as receipt:
            receipt.complete(response())
        return "Fictional memory: the cat is Miso.", MemoryContextInputs(
            retrieved=[{"content": "Fictional cat Miso", "category": "fact"}],
            retrieval_mode="embedding:openrouter:azure:openai/text-embedding-3-small:1024",
        )

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        campaign = guard.current_campaign()
        assert campaign is not None
        pending = copy.deepcopy(campaign.state["pending"])
        self.calls.append(copy.deepcopy(kwargs))
        self.pendings.append(pending)
        slot = campaign.state["stage_index"]
        if slot == self.fail_slot:
            # A retryable HTTP status still stops a non-retry v3 campaign.
            raise SyntheticRateLimit("fictional provider failure")
        stage, sample = V3[slot]
        assert sample is not None
        if stage == "answer":
            text = ("Miso", "Answer: Miso", " Miso ")[sample - 1]
        elif self.qualified:
            correct = int(campaign.state["question"].split("-")[1]) < 25
            text = (
                ("CORRECT" if correct else "INCORRECT")
                if stage == "judge"
                else ("yes" if correct else "no")
            )
        elif stage == "judge":
            text = ("CORRECT", "PARTIAL", "INCORRECT")[sample - 1]
        else:
            text = ("yes", "yes", "no")[sample - 1]
        prompt = 100 + slot
        return {
            "model": "openai/gpt-4o-2024-08-06",
            "system_fingerprint": f"fp-{slot}",
            "choices": [
                {"index": 0, "message": {"content": text}, "finish_reason": f"finish-{slot}"}
            ],
            "usage": {"prompt_tokens": prompt, "completion_tokens": 1, "total_tokens": prompt + 1},
        }


class SyntheticRateLimit(RuntimeError):
    status_code = 429


async def evaluate_question(qid: str = "synthetic-0") -> dict[str, Any]:
    return await evaluate.evaluate_single_parity(
        store=MagicMock(),
        question_id=qid,
        question_text="What is the fictional cat called?",
        reference="Miso",
        category="IE-user",
        allowed_source_conversation_ids=None,
        as_of=NOW,
        historical_clock=True,
        layout=V3_LAYOUT,
    )


@pytest.mark.asyncio
async def test_v3_order_request_identity_rows_and_no_metadata_bleed(tmp_path, monkeypatch):
    fake = FakeTransport(monkeypatch)
    campaign = instance(tmp_path)
    row: dict[str, Any] = {}
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with campaign.activate():
            start(campaign)
            campaign.begin_question("synthetic-0")
            row = await evaluate_question()
            campaign_v3.validate_v3_row(row)
            campaign.finish_question(row)
            assert campaign.state["question_index"] == 1
    assert fake.retrieval.await_count == 1
    receipts = campaign.state["receipts"]
    assert len(receipts) == 10
    assert [(r["stage"], r.get("sample")) for r in receipts] == list(V3)
    assert "sample" not in receipts[0]
    assert fake.pendings == [
        {key: value for key, value in receipt.items() if key != "artifact"}
        for receipt in receipts[1:]
    ]
    for receipt in receipts:
        saved = json.loads((campaign.directory / receipt["artifact"]).read_text())
        assert saved["pending"] == {k: v for k, v in receipt.items() if k != "artifact"}
    for stage in ("answer", "judge", "official_judge"):
        assert len({r["request_sha256"] for r in receipts if r["stage"] == stage}) == 1
    assert all("sample" not in call for call in fake.calls)
    assert all("sample" not in message for call in fake.calls for message in call["messages"])
    assert {"hypothesis", "judgment", "official_judgment"}.isdisjoint(row)
    assert row["official_majority"] == "correct"
    assert row["generic_strict_majority"] == "incorrect"
    assert row["label_aggregation"] == "majority_of_3"
    assert row["memories_used"] == 1 and row["l0_used"] == row["summaries_used"] == 0
    assert row["as_of"] == NOW.isoformat() and row["historical_clock"] is True
    assert row["memory_context_sha256"] == evaluate._sha256_text(row["memory_context"])
    assert row["system_prompt_sha256"] == evaluate._sha256_text(
        evaluate.build_parity_answer_messages(row["question"], row["memory_context"], as_of=NOW)[0][
            "content"
        ]
    )
    assert [sample["judgment"] for sample in row["samples"]] == [
        "correct",
        "partially_correct",
        "incorrect",
    ]
    for sample in row["samples"]:
        assert sample["hypothesis"] == "Miso"
        for offset, stage in enumerate(("answer", "judge", "official_judge"), 1):
            slot = 3 * (sample["sample"] - 1) + offset
            assert sample[f"{stage}_fingerprint"] == f"fp-{slot}"
            assert sample[f"{stage}_finish_reason"] == f"finish-{slot}"
            assert sample[f"{stage}_usage"] == {
                "prompt_tokens": 100 + slot,
                "completion_tokens": 1,
                "total_tokens": 101 + slot,
            }
            assert (
                sample[f"{stage}_raw_output"]
                == json.loads((campaign.directory / f"receipt-{slot:04d}.json").read_text())[
                    "response"
                ]["choices"][0]["message"]["content"]
            )
        assert sample["official_judge_status"] == "completed"


@pytest.mark.asyncio
async def test_fifty_synthetic_rows_qualify_without_finalisation(tmp_path, monkeypatch):
    fake = FakeTransport(monkeypatch, qualified=True)
    campaign = instance(tmp_path)
    rows = []
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with campaign.activate():
            start(campaign)
            for qid in campaign.plan["question_ids"]:
                campaign.begin_question(qid)
                row = await evaluate_question(qid)
                campaign.finish_question(row)
                rows.append(row)
    dataset = [
        {
            "question_id": row["question_id"],
            "question": row["question"],
            "answer": row["reference"],
            "question_type": row["question_type"],
        }
        for row in rows
    ]
    campaign_v3.validate_v3_rows(rows, dataset)
    summary = campaign_v3.qualification_v3(rows, dataset)
    assert summary["samples"]["total"] == summary["samples"]["judged"] == 150
    assert summary["majority"]["total"] == summary["majority"]["judged"] == 50
    assert summary["majority"]["correct"] == 25
    assert summary["samples"]["missing"] == summary["majority"]["missing"] == 0
    assert fake.retrieval.await_count == 50
    # B2a deliberately does not wire this stride into legacy finalisation.
    assert V3_LAYOUT.receipts_per_arm(50) == len(campaign.state["receipts"]) == 500


@pytest.mark.parametrize("slot", range(10))
def test_guard_rejects_wrong_stage_and_provider_sample_at_every_slot(tmp_path, slot):
    campaign = instance(tmp_path)
    campaign.state = {"question": "synthetic-0", "pending": None, "stage_index": slot}
    expected = V3[slot][0]
    wrong = "answer" if expected == "embedding" else "embedding"
    with pytest.raises(guard.CampaignStopped, match="Unexpected/concurrent send"):
        campaign._validate_request(wrong, request(wrong))
    campaign._validate_request(expected, request(expected))
    with pytest.raises(guard.CampaignStopped, match="Unexpected .* request"):
        campaign._validate_request(expected, {**request(expected), "sample": 1})


@pytest.mark.parametrize("index", [-1, 10, 11])
def test_guard_v3_slot_bounds(tmp_path, index):
    campaign = instance(tmp_path)
    campaign.state = {"question": "synthetic-0", "pending": None, "stage_index": index}
    with pytest.raises(guard.CampaignStopped, match="Unexpected/concurrent send"):
        campaign._validate_request("answer", request("answer"))


def test_guard_v3_refuses_historical_pending(tmp_path):
    campaign = instance(tmp_path)
    campaign.state = {
        "question": "synthetic-0",
        "pending": None,
        "stage_index": 0,
        "historical_pending": {"stage": "embedding"},
    }
    with pytest.raises(guard.CampaignStopped, match="Unexpected/concurrent send"):
        campaign._validate_request("embedding", request())


@pytest.mark.parametrize("bad", ["two_samples", "stage_nine", "aggregate", "failed_official"])
def test_guard_refuses_incomplete_question(tmp_path, bad):
    campaign = instance(tmp_path)
    with pytest.raises(guard.CampaignStopped):
        with campaign.activate():
            start(campaign)
            campaign.begin_question("synthetic-0")
            for stage, _ in V3[: 9 if bad == "stage_nine" else 10]:
                with guard.campaign_call(stage, request(stage)) as receipt:
                    receipt.complete(response(stage))
            row = completed_row()
            if bad == "two_samples":
                row["samples"].pop()
            elif bad == "aggregate":
                row["official_majority"] = "incorrect"
            elif bad == "failed_official":
                row["samples"][1]["official_judge_status"] = "failed"
            campaign.finish_question(row)
    assert campaign.state["status"] == "stopped_reconciliation_required"
    assert campaign.state["question_index"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        "legacy",
        "two",
        "order",
        "missing_status",
        "failed",
        "wrong_label",
        "aggregate",
        "error",
        "sample_error",
        "top_level",
    ],
)
def test_v3_completion_never_raises_for_malformed_or_partial_rows(bad):
    row: Any = completed_row()
    if bad is None or isinstance(bad, (dict, list)):
        row = bad
    elif bad == "legacy":
        row = {"judgment": "correct"}
    elif bad == "two":
        row["samples"].pop()
    elif bad == "order":
        row["samples"].reverse()
    elif bad == "missing_status":
        del row["samples"][1]["official_judge_status"]
    elif bad == "failed":
        row["samples"][1]["official_judge_status"] = "failed"
    elif bad == "wrong_label":
        row["samples"][1]["official_judgment"] = None
    elif bad == "aggregate":
        row["official_majority"] = "incorrect"
    elif bad == "error":
        row["error"] = "fictional"
    elif bad == "sample_error":
        row["samples"][1]["error"] = "fictional"
    elif bad == "top_level":
        row["hypothesis"] = None
    assert fact_harness.is_completed_result(row, layout=V3_LAYOUT) is False
    assert fact_harness.is_completed_result(completed_row(), layout=V3_LAYOUT) is True
    assert fact_harness.is_completed_result({"judgment": "correct"}) is True


def runner(tmp_path: Path) -> fact_harness.LongMemEvalFactRunner:
    return fact_harness.LongMemEvalFactRunner(
        dataset_path=tmp_path / "fictional-dataset.json",
        output_path=tmp_path / "results.jsonl",
        checkpoint_path=tmp_path / "checkpoint.json",
        score_path=tmp_path / "score.json",
    )


@pytest.mark.parametrize(
    "order", ["partial_only", "partial_then_complete", "complete_then_partial"]
)
def test_v3_reconciliation_never_adopts_partial_rows(tmp_path, order):
    target = runner(tmp_path)
    complete = {**completed_row(), "run_id": "synthetic-run"}
    partial = copy.deepcopy(complete)
    partial["samples"].pop()
    rows = {
        "partial_only": [partial],
        "partial_then_complete": [partial, complete],
        "complete_then_partial": [complete, partial],
    }[order]
    target.output_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    checkpoint = {"run_id": "synthetic-run", "phases": {"evaluate": {"results": {}}}}
    manifest: dict[str, Any] = {}
    target._reconcile_results(checkpoint, ["synthetic-0"], manifest, layout=V3_LAYOUT)
    saved = checkpoint["phases"]["evaluate"]["results"]
    assert saved == ({} if order == "partial_only" else {"synthetic-0": complete})
    assert manifest["results_reconciliations"][0]["adopted_from_file"] == int(
        order != "partial_only"
    )
    assert target.output_path.read_text() == ("" if not saved else json.dumps(complete) + "\n")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,retained",
    [
        ("after_first", 4),
        ("middle_second", 5),
        ("before_third_official", 9),
        ("official_second", 6),
    ],
)
async def test_interruption_stops_without_error_row_adoption_or_replay(
    tmp_path, monkeypatch, boundary, retained
):
    fake = FakeTransport(monkeypatch, fail_slot=None if boundary == "after_first" else retained)
    if boundary == "after_first":
        original = evaluate.judge_answer_official

        async def interrupted(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("fictional interruption after first sample")

        monkeypatch.setattr(evaluate, "judge_answer_official", interrupted)
    target = runner(tmp_path)
    dataset = [
        {
            "question_id": f"synthetic-{i}",
            "question": "Fictional cat?",
            "answer": "Miso",
            "question_type": "single-session-user",
            "question_date": "2023/05/20 (Sat) 00:00",
        }
        for i in range(50)
    ]
    checkpoint = {
        "run_id": "synthetic-run",
        "phases": {"ingest": {"status": "completed", "results": {}}, "evaluate": {"results": {}}},
    }
    cls = fact_harness.LongMemEvalFactRunner
    monkeypatch.setattr(cls, "_preflight_database_guard", staticmethod(lambda: None))
    monkeypatch.setattr(cls, "load_dataset", lambda self: dataset)
    monkeypatch.setattr(cls, "load_checkpoint", lambda self: checkpoint)
    monkeypatch.setattr(
        fact_harness, "fingerprint_benchmark_users", AsyncMock(return_value={"sha256": "fictional"})
    )

    @asynccontextmanager
    async def phase(self, *args):
        yield None, SimpleNamespace(), {}

    @asynccontextmanager
    async def compute(*args, **kwargs):
        yield None

    monkeypatch.setattr(cls, "_phase", phase)
    monkeypatch.setattr(fact_harness, "account_compute", compute)
    campaign = instance(tmp_path)
    with pytest.raises(RuntimeError):
        with campaign.activate():
            start(campaign)
            await target.evaluate()
    saved = json.loads(campaign.state_path.read_text())
    assert saved["status"] == "stopped_reconciliation_required"
    assert saved["question_index"] == 0 and saved["question"] == "synthetic-0"
    assert (
        len(saved["receipts"]) == len(list(campaign.directory.glob("receipt-*.json"))) == retained
    )
    assert (
        saved["pending"] is None
        if boundary == "after_first"
        else saved["pending"]["sample"] == (3 if retained == 9 else 2)
    )
    assert not target.output_path.exists() or target.output_path.read_text() == ""
    assert checkpoint["phases"]["evaluate"]["results"] == {}
    persisted = json.loads(target.checkpoint_path.read_text())
    assert persisted["phases"]["evaluate"]["results"] == {}
    assert persisted["phases"]["evaluate"].get("completed_count", 0) == 0
    assert fake.retrieval.await_count == 1
    calls = len(fake.calls)
    with pytest.raises(guard.CampaignStopped, match="clean completed-arm"):
        with instance(tmp_path).activate():
            pytest.fail("An interrupted sample must never be replayed")
    assert len(fake.calls) == calls
    assert json.loads(campaign.state_path.read_text()) == saved


@pytest.mark.parametrize("mode", ["legacy", "fresh", "retry", "recovery", "final"])
def test_production_default_resolves_legacy_for_every_plan_mode(tmp_path, fixed_approval, mode):
    plan = synthetic_plan(mode)
    default = guard.Campaign(tmp_path / mode, plan, len)
    injected = guard.Campaign(tmp_path / f"{mode}-injected", plan, len, layout=V3_LAYOUT)
    assert default.layout == layout("legacy")
    assert default.plan == injected.plan
    assert default._plan_hash == injected._plan_hash
    assert "layout" not in default.plan and "campaign_layout" not in default.plan["pins"]


def test_legacy_pending_still_has_exact_four_keys(tmp_path):
    campaign = guard.Campaign(tmp_path / "legacy", synthetic_plan(), len)
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with campaign.activate():
            start(campaign)
            campaign.begin_question("synthetic-0")
            for stage in guard.STAGES:
                with guard.campaign_call(stage, request(stage)) as receipt:
                    assert set(campaign.state["pending"]) == {
                        "arm",
                        "question_id",
                        "stage",
                        "request_sha256",
                    }
                    receipt.complete(response(stage))


def test_new_v3_imports_are_function_local():
    for module in (guard, fact_harness):
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            assert not any(
                isinstance(child, ast.ImportFrom) and "campaign_v3" in (child.module or "")
                for child in ast.walk(node)
            )
    # test_campaign_layout's fresh-interpreter check also guards circular imports.
