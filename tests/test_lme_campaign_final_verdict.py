"""Synthetic completed-verdict import; no real DB, provider or private evidence."""

from __future__ import annotations

import argparse
import asyncio
import copy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from orchestrator.eval import campaign_final_verdict as final, campaign_guard as guard
from orchestrator.eval import campaign_launch as launch, campaign_retry as retry
from orchestrator.eval import campaign_recovery as ancestor
from orchestrator.eval import campaign_restore as restore, campaign_validation as validation
from orchestrator.eval import fact_harness as harness
from tests.longmemeval import evaluate
from tests.test_lme_campaign_retry import stopped as stopped
from tests.test_lme_campaign_retry import RateLimitError, configure_attach, fake_time, request
from tests.test_lme_campaign_guard import response, send
from tests.test_lme_campaign_second_recovery import recovered as recovered
from tests.test_lme_campaign_second_recovery import pin, write

REAL_BRIDGE = final.source_bridge


def normalized_result():
    return {
        "official_judgment": "incorrect",
        "official_judge_raw_output": "no",
        "official_judge_finish_reason": "stop",
        "official_judge_model": guard.LLM_MODEL.removeprefix("openrouter/"),
        "official_judge_fingerprint": "synthetic-node",
        "official_judge_usage": {"prompt_tokens": 204, "completion_tokens": 2, "total_tokens": 206},
    }


@pytest_asyncio.fixture
async def completed(stopped, monkeypatch):
    fake_time(monkeypatch)
    configure_attach(monkeypatch)
    instance = guard.Campaign(stopped.destination, stopped.plan, len)
    with pytest.raises(RateLimitError):
        with instance.activate():
            async with restore.attached_pool(instance):
                async with guard.campaign_llm_call(
                    "official_judge", request("official_judge")
                ) as receipt:
                    await receipt.prepare_attempt()
                    assert receipt.retry_failure(RateLimitError())
                    await receipt.prepare_attempt()
                    receipt.complete(response("official_judge"))
                instance.finish_question(
                    {
                        "question_id": stopped.plan.question_ids[48],
                        "official_judge_status": "completed",
                        "official_judgment": "correct",
                    }
                )
                instance.begin_question(stopped.plan.question_ids[49])
                send("embedding")
                async with guard.campaign_llm_call("answer", request("answer")) as receipt:
                    await receipt.prepare_attempt()
                    assert receipt.retry_failure(RateLimitError())
                    await receipt.prepare_attempt()
                    receipt.complete(response("answer"))
                async with guard.campaign_llm_call("judge", request("judge")) as receipt:
                    await receipt.prepare_attempt()
                    receipt.complete(response("judge"))
                async with guard.campaign_llm_call(
                    "official_judge", request("official_judge")
                ) as receipt:
                    for ordinal in (1, 2, 3):
                        await receipt.prepare_attempt()
                        assert receipt.retry_failure(RateLimitError()) is (ordinal < 3)
                    raise RateLimitError()
    rows = copy.deepcopy(stopped.rows)
    rows[48].update(
        official_judge_status="completed",
        official_judgment="correct",
        official_judge_usage={"prompt_tokens": 2},
        official_completion_provenance={
            "executor_plan_sha256": instance._plan_hash,
            "inherited_evaluation": retry.inherited_evaluation(instance),
        },
    )
    row = {**stopped.dataset[49], **copy.deepcopy(rows[0])}
    row.update(
        question_id=stopped.dataset[49]["question_id"],
        question=stopped.dataset[49]["question"],
        reference=stopped.dataset[49]["answer"],
        question_type=stopped.dataset[49]["question_type"],
        category=validation.CATEGORY_MAP[stopped.dataset[49]["question_type"]],
        run_id="retry-executor",
        official_judge_status="failed",
        official_judge_usage=None,
    )
    row.pop("official_judgment", None)
    row.pop("answer", None)
    rows.append(row)
    source = stopped.destination
    a1 = source / "A1"
    (a1 / retry.PREFIX_FILES[0]).write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    )
    write(
        a1 / retry.PREFIX_FILES[1],
        {
            "run_id": "retry-executor",
            "phases": {
                "ingest": {"status": "completed", "completed_count": 2126},
                "evaluate": {
                    "status": "failed",
                    "completed_count": 50,
                    "results": {row["question_id"]: row for row in rows},
                },
                "score": {"status": "pending"},
            },
        },
    )
    write(
        a1 / retry.PREFIX_FILES[2],
        {
            "run_id": "retry-executor",
            "git": {"sha": "retry-control", "dirty": False},
            "baseline_qualified": True,
            "disqualifications": [],
            "phases": {},
            "inherited_evaluation": retry.inherited_evaluation(instance),
        },
    )
    previous_path = source.parent / "retry-plan.json"
    write(previous_path, stopped.payload)
    ledger = source / "campaign.json"
    old_hold = sum(
        (Decimal(item["usd"]) for item in instance.state["retry_reservations"]), Decimal(0)
    )
    extra = (
        stopped.plan.input_limits[stopped.plan.question_ids[49]]["official_judge"]
        * guard.INPUT_RATE
        + 10 * guard.OUTPUT_RATE
    )
    overrides = {
        "OLD_CONTROL": "retry-control",
        "OLD_CANDIDATE": "retry-candidate",
        "PLAN_NAME": previous_path.name,
        "DIRECTORY_NAME": source.name,
        "PLAN_SHA": launch.digest_file(previous_path),
        "LEDGER_SHA": launch.digest_file(ledger),
        "ROWS_SHA": launch.digest_file(a1 / retry.PREFIX_FILES[0]),
        "PENDING_QID": stopped.plan.question_ids[49],
        "PENDING_SHA": instance.state["pending"]["request_sha256"],
        "EXTRA_HOLD": extra,
        "OLD_RETRY_HOLD": old_hold,
    }
    for key, value in overrides.items():
        monkeypatch.setattr(final, key, value)
    standalone = source.with_name(source.name + "-official-50-attempt-04")
    standalone.mkdir()
    result_path = standalone / "official-result.json"
    write(result_path, normalized_result())
    monkeypatch.setattr(final, "RESULT_SHA", launch.digest_file(result_path))
    attempt = {
        "parent_ledger_sha256": final.LEDGER_SHA,
        "plan_sha256": final.PLAN_SHA,
        "source_sha": final.OLD_CONTROL,
        "request_sha256": final.PENDING_SHA,
        "ordinal": 4,
        "maximum_new_calls": 1,
        "reserved_usd": str(extra),
        "cumulative_retry_reserved_usd": str(old_hold + extra),
        "source_rows_sha256": final.ROWS_SHA,
        "parent_receipts_sha256": final.PARENT_RECEIPTS_SHA,
        "total_reservations_usd": "15.751905",
        "status": "completed",
        "started_at": "2026-10-30T00:00:00+00:00",
        "completed_at": "2026-10-30T00:00:01+00:00",
        "result_sha256": final.RESULT_SHA,
    }
    attempt_path = standalone / "attempt.json"
    write(attempt_path, attempt)
    monkeypatch.setattr(final, "ATTEMPT_SHA", launch.digest_file(attempt_path))
    lineage = {
        name: pin(path)
        for name, path in (
            ("previous_plan", previous_path),
            ("stopped_ledger", ledger),
            ("attempt_record", attempt_path),
            ("result", result_path),
        )
    }
    _, metadata = final.inspect_lineage(lineage)
    bridge = {
        "old_control": "retry-control",
        "new_control": "final-control",
        "files": {},
        "diff_sha256": "synthetic",
    }
    monkeypatch.setattr(final, "source_bridge", lambda *_: copy.deepcopy(bridge))
    pins = copy.deepcopy(stopped.plan.pins)
    pins.update(
        final_verdict_recovery_lineage=lineage,
        final_verdict_recovery={**metadata, "source_bridge": bridge},
        arm_shas={arm: "final-candidate" if arm == "C0" else "final-control" for arm in guard.ARMS},
    )
    plan = guard.CampaignPlan(stopped.plan.question_ids, stopped.plan.input_limits, pins)
    return SimpleNamespace(
        plan=plan,
        payload=json.loads(guard._json(plan.payload())),
        source=source,
        destination=source.with_name(source.name + "-final-verdict"),
        lineage=lineage,
        rows=rows,
        dataset=stopped.dataset,
        original_state=copy.deepcopy(instance.state),
        attempt=attempt,
        result=normalized_result(),
        standalone=standalone,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("official_judge_finish_reason", "length"),
        ("official_judge_model", "other"),
        ("official_judge_fingerprint", ""),
        ("official_judgment", "correct"),
    ],
)
def test_normalized_result_rejects_invalid_evidence(field, value):
    result = normalized_result()
    result[field] = value
    with pytest.raises(guard.CampaignStopped):
        final.validate_result(
            result, {"input_limits": {final.PENDING_QID: {"official_judge": 995}}}
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("prompt_tokens", True),
        ("prompt_tokens", 0),
        ("prompt_tokens", 996),
        ("completion_tokens", 11),
        ("completion_tokens", -1),
        ("total_tokens", 999),
        ("cost", "NaN"),
        ("cost", True),
        ("cost", "10"),
    ],
)
def test_usage_and_optional_cost_fail_closed(key, value):
    result = normalized_result()
    result["official_judge_usage"][key] = value
    with pytest.raises((guard.CampaignStopped, ValueError)):
        final.validate_result(
            result, {"input_limits": {final.PENDING_QID: {"official_judge": 995}}}
        )


def test_absent_cost_is_not_manufactured_and_unknown_result_rejected():
    result = normalized_result()
    plan = {"input_limits": {final.PENDING_QID: {"official_judge": 995}}}
    final.validate_result(result, plan)
    assert "cost" not in result["official_judge_usage"]
    result["raw_response"] = "synthetic-disallowed"
    with pytest.raises(guard.CampaignStopped, match="Unknown"):
        final.validate_result(result, plan)


@pytest.mark.asyncio
async def test_prepare_is_offline_exact_and_one_fixed_output(completed, monkeypatch):
    previous = Path(completed.lineage["previous_plan"]["path"])
    args = argparse.Namespace(
        **{name: Path(artifact["path"]) for name, artifact in completed.lineage.items()},
        **{name + "_sha256": artifact["sha256"] for name, artifact in completed.lineage.items()},
        output=previous.with_name(previous.stem + "-final-verdict.json"),
        control_sha="final-control",
        candidate_sha="final-candidate",
    )
    forbidden = AsyncMock(side_effect=AssertionError("No service access in final preparation"))
    monkeypatch.setattr(launch.asyncpg, "connect", forbidden)
    monkeypatch.setattr(launch, "rates", forbidden)
    await final.prepare(args)
    assert launch.load_plan(args.output).payload() == completed.plan.payload()
    assert launch.load_plan(previous).payload()["cumulative_reserved_usd"] == "14.7519050"
    with pytest.raises(guard.CampaignStopped, match="fixed successor"):
        await final.prepare(args)
    args.output = previous.with_name("alternate.json")
    with pytest.raises(guard.CampaignStopped, match="fixed successor"):
        await final.prepare(args)
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["previous_plan", "stopped_ledger", "attempt_record", "result"])
async def test_artifact_tampering_is_rejected(completed, name):
    Path(completed.lineage[name]["path"]).write_text("{}")
    with pytest.raises(guard.CampaignStopped):
        final.verify_recovery(completed.payload)


@pytest.mark.asyncio
async def test_unknown_record_and_pending_partial_are_rejected(completed, monkeypatch):
    attempt_path = completed.standalone / "attempt.json"
    changed = {**completed.attempt, "unexpected": True}
    write(attempt_path, changed)
    lineage = copy.deepcopy(completed.lineage)
    lineage["attempt_record"] = pin(attempt_path)
    monkeypatch.setattr(final, "ATTEMPT_SHA", launch.digest_file(attempt_path))
    with pytest.raises(guard.CampaignStopped, match="Unknown completed"):
        final.inspect_lineage(lineage)
    write(attempt_path, completed.attempt)
    monkeypatch.setattr(final, "ATTEMPT_SHA", launch.digest_file(attempt_path))
    write(
        completed.source / "receipt-0199.json",
        {
            "pending": completed.original_state["pending"],
            "response": {},
        },
    )
    with pytest.raises(guard.CampaignStopped, match="partial final receipt"):
        final.inspect_lineage(completed.lineage)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [1, 205])
async def test_exclusive_partial_bootstrap_cannot_resume(completed, monkeypatch, interrupt):
    original = retry._copy
    count = 0

    def interrupted(source, destination, digest):
        nonlocal count
        original(source, destination, digest)
        count += 1
        if count == interrupt:
            raise OSError("synthetic interrupted final bootstrap")

    monkeypatch.setattr(retry, "_copy", interrupted)
    with pytest.raises(OSError):
        with guard.Campaign(completed.destination, completed.plan, len).activate():
            pytest.fail("Partial final bootstrap exposed a live campaign")
    monkeypatch.setattr(retry, "_copy", original)
    before = (completed.source / "campaign.json").read_bytes()
    with pytest.raises((guard.CampaignStopped, FileNotFoundError)):
        with guard.Campaign(completed.destination, completed.plan, len).activate():
            pytest.fail("Partial bootstrap resumed")
    assert before == (completed.source / "campaign.json").read_bytes()


@pytest.mark.asyncio
async def test_imported_fourth_is_separate_terminal_three_unchanged_no_replay(completed):
    instance = guard.Campaign(completed.destination, completed.plan, len)
    old = (completed.source / "campaign.json").read_bytes()
    with pytest.raises(asyncio.CancelledError):
        with instance.activate():
            retry.validate_attempts(instance, settled=True)
            assert instance.state["attempts"][-1]["ordinal"] == 3
            assert instance.state["attempts"][-1]["status"] == "terminal_failed"
            assert len(instance.state["retry_reservations"]) == 5
            assert instance.state["imported_retry_reservations"][0]["ordinal"] == 4
            assert instance.state["restore_attempts"] == 1
            assert not instance.state.get("attach_receipt")
            assert (
                instance.state["inherited_attach_receipt"]
                == completed.original_state["attach_receipt"]
            )
            body = json.loads((completed.destination / "receipt-0199.json").read_bytes())
            assert body["format"] == "normalized_official_result" and "response" not in body
            assert "cost" not in body["normalized_result"]["official_judge_usage"]
            assert len(instance.state["receipts"]) == 200
            for index in range(199):
                name = f"receipt-{index:04d}.json"
                assert (completed.destination / name).read_bytes() == (
                    completed.source / name
                ).read_bytes()
            raise asyncio.CancelledError()
    with pytest.raises(guard.CampaignStopped):
        with guard.Campaign(completed.destination, completed.plan, len).activate():
            pytest.fail("Interrupted finalization replayed")
    assert old == (completed.source / "campaign.json").read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("good_end", [True, False])
async def test_canonical_finalize_no_inference_fresh_end_required_then_restores_four_to_six(
    completed, monkeypatch, tmp_path, good_end
):
    from tests.test_longmemeval_parity_harness import (
        BenchRuntime,
        DATASET,
        KEY_A,
        CONV_Q1_A,
        _ingested_checkpoint,
        _ingest_result,
        _key,
    )

    data = [{**DATASET[0], **row} for row in completed.dataset]
    dataset_path = tmp_path / "synthetic-dev.json"
    write(dataset_path, data)
    BenchRuntime(monkeypatch, git_sha="final-control")
    fingerprints = AsyncMock(
        side_effect=[
            {"sha256": final.STATE_SHA256},
            {
                "sha256": final.STATE_SHA256 if good_end else "changed-finalization-state",
            },
        ]
    )
    monkeypatch.setattr(harness, "fingerprint_benchmark_users", fingerprints)
    fake_time(monkeypatch)
    model = configure_attach(monkeypatch)
    forbidden = AsyncMock(side_effect=AssertionError("Imported A1 must not perform inference"))
    monkeypatch.setattr(harness, "evaluate_single_parity", forbidden)
    monkeypatch.setattr(evaluate, "judge_answer_official", forbidden)
    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", forbidden)
    instance = guard.Campaign(completed.destination, completed.plan, len)

    async def finalize():
        with instance.activate():
            async with restore.attached_pool(instance):
                assert instance.state["restore_attempts"] == 1
                output = completed.destination / "A1"
                runner = harness.LongMemEvalFactRunner(
                    dataset_path=dataset_path,
                    output_path=output / retry.PREFIX_FILES[0],
                    checkpoint_path=output / retry.PREFIX_FILES[1],
                    score_path=output / "longmemeval_score.json",
                    force_retrieval_logging=True,
                )
                checkpoint = _ingested_checkpoint(
                    dataset_path, fingerprint={"sha256": final.STATE_SHA256}
                )
                checkpoint["run_id"] = "finalization-executor"
                checkpoint["phases"]["ingest"]["results"] = {
                    _key(qid, KEY_A): _ingest_result(qid, KEY_A, CONV_Q1_A)
                    for qid in completed.plan.question_ids
                }
                write(runner.checkpoint_path, checkpoint)
                rows = await runner.evaluate()
                score = runner.score()
                manifest = json.loads((output / "run_manifest.json").read_bytes())
                final.validate_prefix(instance, rows, manifest)
                assert rows[49]["run_id"] == "retry-executor"
                assert rows[:49] == completed.rows[:49]
                assert final._base(rows[49]) == final._base(completed.rows[49])
                assert rows[49]["official_judgment"] == "incorrect"
                assert manifest["phases"]["evaluate"]["official_judgments"]["judged"] == 50
                assert score["result_count"] == 50
                instance.finish_arm(qualification={"synthetic": "A1"})

    if good_end:
        await finalize()
        assert instance.state["status"] == "ready_next_arm"
    else:
        with pytest.raises(guard.CampaignStopped, match="fresh complete fingerprint"):
            await finalize()
    assert fingerprints.await_count == 2
    forbidden.assert_not_awaited()
    assert all("DROP" not in sql for sql in model.sql)
    assert json.loads((completed.source / "A1" / "run_manifest.json").read_bytes())["phases"] == {}
    if not good_end:
        return
    for arm in ("A2", "C0"):
        with guard.Campaign(completed.destination, completed.plan, len).activate() as campaign:
            campaign.pending_restore(arm, {"synthetic": arm})
            receipt = {
                "arm": arm,
                "owned_pids": list(range(10)),
                "fingerprint": {"sha256": final.STATE_SHA256},
                "script_sha256": completed.plan.pins["restore_script"]["sha256"],
            }
            campaign.restored(arm, receipt)
            campaign.start_arm(arm, verified_pins=completed.plan.pins, restore=receipt)
            for qid in completed.plan.question_ids:
                campaign.begin_question(qid)
                send("embedding")
                for stage in guard.STAGES[1:]:
                    async with guard.campaign_llm_call(stage, request(stage)) as call:
                        await call.prepare_attempt()
                        call.complete(response(stage))
                campaign.finish_question(
                    {
                        "question_id": qid,
                        "official_judge_status": "completed",
                        "official_judgment": "correct",
                    }
                )
            campaign.finish_arm(qualification={"synthetic": arm})
    state = json.loads((completed.destination / "campaign.json").read_bytes())
    assert state["status"] == "completed" and state["restore_attempts"] == 3
    assert 3 + state["restore_attempts"] == 6 and len(state["receipts"]) == 600
    assert state["attempts"][8]["status"] == "terminal_failed"


def transport_campaign(completed, arm) -> Any:
    """Transport-only synthetic state; no claim of a qualified arm handoff."""
    completed.destination.mkdir()
    plan = copy.deepcopy(completed.payload)
    state = {"plan": plan}
    final.bootstrap(
        completed.destination, state, plan["pins"]["final_verdict_recovery"]["inherited"]
    )
    state["pending"] = {
        "arm": arm,
        "question_id": plan["question_ids"][0],
        "stage": "answer",
        "request_sha256": guard._hash(request("answer")),
    }
    return SimpleNamespace(
        state=state,
        plan=plan,
        directory=completed.destination,
        _check=Mock(),
        _save=Mock(),
        _last_llm_start=0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["A2", "C0"])
async def test_imported_hold_counts_in_later_arm_retry_admission(completed, arm):
    campaign = transport_campaign(completed, arm)
    old = sum((Decimal(item["usd"]) for item in campaign.state["retry_reservations"]), Decimal(0))
    imported = final.EXTRA_HOLD
    bound = (
        campaign.plan["input_limits"][campaign.plan["question_ids"][0]]["answer"] * retry.INPUT_RATE
        + guard.CAPS["answer"] * retry.OUTPUT_RATE
    )
    # The ledger itself must count the separately imported hold, not only new calls.
    campaign.plan["new_reserved_usd"] = str(Decimal(campaign.plan["allocated_usd"]) + old)
    with pytest.raises(guard.CampaignStopped, match="headroom"):
        retry.validate_attempts(campaign)
    # Enough for the next retry without the import, insufficient when it is carried.
    campaign.plan["new_reserved_usd"] = str(
        Decimal(campaign.plan["allocated_usd"]) + old + imported + bound - imported / 2
    )
    await retry.prepare_attempt(campaign, "answer", request("answer"))
    assert retry.failed_attempt(campaign, "answer", request("answer"), RateLimitError())
    before = copy.deepcopy(campaign.state)
    campaign._save.reset_mock()
    with pytest.raises(guard.CampaignStopped, match="headroom"):
        await retry.prepare_attempt(campaign, "answer", request("answer"))
    assert campaign.state == before
    campaign._save.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["A2", "C0"])
async def test_final_import_does_not_extend_later_arm_three_attempt_limit(completed, arm):
    campaign = transport_campaign(completed, arm)
    for ordinal in (1, 2, 3):
        await retry.prepare_attempt(campaign, "answer", request("answer"))
        assert retry.failed_attempt(campaign, "answer", request("answer"), RateLimitError()) is (
            ordinal < 3
        )
    before = copy.deepcopy(campaign.state)
    campaign._save.reset_mock()
    with pytest.raises(guard.CampaignStopped, match="exhausted"):
        await retry.prepare_attempt(campaign, "answer", request("answer"))
    assert campaign.state == before
    assert [item["ordinal"] for item in campaign.state["attempts"][-3:]] == [1, 2, 3]
    assert campaign.state["attempts"][-1]["status"] == "terminal_failed"
    assert campaign.state["imported_retry_reservations"] == [
        final.import_record(campaign.plan["pins"])
    ]
    campaign._save.assert_not_called()


def test_new_bridge_rejects_prompt_retrieval_and_configuration_changes(monkeypatch):
    monkeypatch.setattr(launch, "git_bytes", lambda *_: b"synthetic-source")
    for path in (
        "tests/longmemeval/evaluate.py",
        "orchestrator/memory/retrieval.py",
        "config/policy.json",
    ):
        monkeypatch.setattr(ancestor, "_git", lambda *_, path=path: path.encode())
        with pytest.raises(guard.CampaignStopped, match="other runtime"):
            REAL_BRIDGE("synthetic-new-source")
