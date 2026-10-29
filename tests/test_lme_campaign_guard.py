"""Synthetic-only spend/stop failures. No database, credentials or network."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from orchestrator.eval import campaign_guard as guard
from orchestrator.memory import embedding
from tests.longmemeval import evaluate


def plan() -> guard.CampaignPlan:
    ids = tuple(f"synthetic-{i}" for i in range(50))
    return guard.CampaignPlan(
        ids,
        {qid: {"embedding": 20, "answer": 500, "judge": 500, "official_judge": 500} for qid in ids},
        {"source": "synthetic-only", "snapshot": "not-an-execution-approval"},
    )


def campaign(tmp_path: Path, *, directory: str = "campaign") -> guard.Campaign:
    return guard.Campaign(tmp_path / directory, plan(), len)


@contextmanager
def active(tmp_path):
    instance = campaign(tmp_path)
    # Tests deliberately stop before 200 complete questions. Catch only the
    # normal incomplete-campaign signal, never hide an assertion failure.
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            yield instance


def request(stage="embedding"):
    if stage == "embedding":
        return {
            "route_id": "azure-openrouter-small-1024",
            "model": guard.EMBEDDING_MODEL,
            "endpoint": guard.BASE_URL,
            "dimensions": 1024,
            "input": ["fictional query"],
            "provider": {
                "only": ["azure"],
                "order": ["azure"],
                "allow_fallbacks": False,
                "data_collection": "deny",
                "zdr": True,
                "require_parameters": True,
            },
        }
    return {
        "model": guard.LLM_MODEL,
        "messages": [{"role": "user", "content": "fictional"}],
        "temperature": 0,
        "max_tokens": guard.CAPS[stage],
        "seed": 42,
        "num_retries": 0,
        "api_base": guard.BASE_URL,
        "extra_body": {"provider": {"order": ["azure"], "allow_fallbacks": False}},
    }


def response(stage="embedding"):
    if stage == "embedding":
        return {
            "model": "text-embedding-3-small",
            "data": [],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }
    return {
        "model": "openai/gpt-4o-2024-08-06",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "yes"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }


def send(stage):
    with guard.campaign_call(stage, request(stage)) as receipt:
        receipt.complete(response(stage))


def test_inactive_scope_does_not_require_pins_or_receipt():
    assert guard.current_campaign() is None
    guard.reject_legacy_embedding()
    with guard.campaign_call("anything", {"unqualified": True}):
        pass


def test_reservation_pending_receipts_and_partial_evidence(tmp_path):
    with active(tmp_path) as instance:
        state = json.loads(instance.state_path.read_text())
        assert state["plan"]["cumulative_reserved_usd"] == "10.4019050"
        for stage in guard.STAGES:
            with guard.campaign_call(stage, request(stage)) as receipt:
                pending = json.loads(instance.state_path.read_text())["pending"]
                assert pending["stage"] == stage
                assert pending["question_id"] == "synthetic-0"
                receipt.complete(response(stage))
        instance.finish_question(
            {
                "question_id": "synthetic-0",
                "official_judge_status": "completed",
                "official_judgment": "correct",
            }
        )
        assert len(instance.state["receipts"]) == 4
        assert instance.state["question_index"] == 1
    saved = json.loads(instance.state_path.read_text())
    assert saved["status"] == "stopped_reconciliation_required"
    assert len(list(instance.directory.glob("receipt-*.json"))) == 4


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(question_ids=("duplicate",) * 50),
        lambda p: p.update(input_limits={}),
        lambda p: p.update(pins={}),
    ],
)
def test_invalid_plan(tmp_path, mutation):
    values: dict[str, Any] = dict(
        question_ids=plan().question_ids, input_limits=plan().input_limits, pins=plan().pins
    )
    mutation(values)
    with pytest.raises(guard.CampaignStopped):
        guard.Campaign(tmp_path / "bad", guard.CampaignPlan(**values), len)
    assert not (tmp_path / "bad").exists()


def test_whole_envelope_not_historical_usage_is_reserved(tmp_path):
    p = plan()
    limits = {qid: dict(limits) for qid, limits in p.input_limits.items()}
    for entry in limits.values():
        entry["answer"] = 100000
    with pytest.raises(guard.CampaignStopped, match="exceed approved"):
        guard.Campaign(tmp_path / "over", guard.CampaignPlan(p.question_ids, limits, p.pins), len)


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt_tokens", True),
        ("prompt_tokens", -1),
        ("prompt_tokens", 0),
        ("prompt_tokens", "2"),
        ("prompt_tokens", None),
        ("total_tokens", 10),
        ("completion_tokens", 1),
        ("cost", "NaN"),
        ("cost", "Infinity"),
        ("cost", "-1"),
        ("cost", "20"),
        ("cost", True),
    ],
)
def test_invalid_embedding_usage_retains_receipt_and_pending(tmp_path, field, value):
    instance = campaign(tmp_path)
    with pytest.raises((guard.CampaignStopped, ValueError)):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            payload = response()
            payload["usage"][field] = value
            with guard.campaign_call("embedding", request()) as receipt:
                receipt.complete(payload)
    state = json.loads(instance.state_path.read_text())
    assert state["pending"]["stage"] == "embedding"
    assert state["receipts"] == []
    assert (instance.directory / "receipt-0000.json").exists()
    with pytest.raises(guard.CampaignStopped):
        with guard.campaign_call("answer", request("answer")):
            instance.before_send("answer", request("answer"))


@pytest.mark.parametrize("stage", guard.STAGES)
def test_unknown_outcome_no_automatic_reentry(tmp_path, stage):
    instance = campaign(tmp_path)
    with pytest.raises(TimeoutError):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            for earlier in guard.STAGES[: guard.STAGES.index(stage)]:
                send(earlier)
            with guard.campaign_call(stage, request(stage)):
                raise TimeoutError("synthetic unknown outcome")
    state = json.loads(instance.state_path.read_text())
    assert state["pending"]["stage"] == stage
    assert len(state["receipts"]) == guard.STAGES.index(stage)
    with pytest.raises(guard.CampaignStopped, match="clean completed-arm"):
        with campaign(tmp_path).activate():
            pytest.fail("Interrupted campaigns must never replay")
    assert json.loads(instance.state_path.read_text()) == state


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(model="unapproved"),
        lambda r: r.update(input=["a", "b"]),
        lambda r: r.update(input=["x" * 21]),
        lambda r: r.update(dimensions=1),
        lambda r: r["provider"].update(allow_fallbacks=True),
        lambda r: r["provider"].update(zdr=False),
    ],
)
def test_prepared_embedding_rejected_before_send(tmp_path, mutation):
    with pytest.raises(guard.CampaignStopped):
        with campaign(tmp_path).activate() as instance:
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            prepared = request()
            mutation(prepared)
            with guard.campaign_call("embedding", prepared):
                pytest.fail("Unapproved send")
    assert json.loads((tmp_path / "campaign/campaign.json").read_text())["pending"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(num_retries=1),
        lambda r: r.update(seed=43),
        lambda r: r.update(api_base="https://other.invalid"),
        lambda r: r.update(model="unapproved"),
        lambda r: r.update(max_tokens=1024),
        lambda r: r.update(messages=[{"role": "user", "content": "x" * 501}]),
        lambda r: r.update(messages=[{"role": "user", "content": "x", "name": "extra"}]),
    ],
)
def test_prepared_llm_rejected_before_send(tmp_path, mutation):
    with pytest.raises(guard.CampaignStopped):
        with campaign(tmp_path).activate() as instance:
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            send("embedding")
            prepared = request("answer")
            mutation(prepared)
            with guard.campaign_call("answer", prepared):
                pytest.fail("Unapproved send")


def test_second_inflight_call_latches_stop(tmp_path):
    with pytest.raises(guard.CampaignStopped):
        with campaign(tmp_path).activate() as instance:
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            with guard.campaign_call("embedding", request()):
                with guard.campaign_call("embedding", request()):
                    pytest.fail("Second inflight request")


@pytest.mark.parametrize("after", [False, True])
def test_durability_failure_prevents_following_send(tmp_path, monkeypatch, after):
    instance = campaign(tmp_path)
    original = guard._write
    with pytest.raises(OSError):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")

            def fail_write(path, value):
                if after:
                    original(path, value)
                raise OSError("synthetic durability failure")

            monkeypatch.setattr(guard, "_write", fail_write)
            with guard.campaign_call("embedding", request()):
                pytest.fail("No send after a pending write failure")
    assert instance._stopped


def test_directory_fsync_is_not_silently_ignored(tmp_path, monkeypatch):
    def fail(_fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(guard.os, "fsync", fail)
    with pytest.raises(OSError):
        guard._sync_directory(tmp_path)


def test_response_artifact_is_allowlisted(tmp_path):
    with active(tmp_path) as instance:
        send("embedding")
        payload = response("answer")
        payload.update(headers={"Authorization": "fictional-secret"}, api_key="fictional-secret")
        payload["usage"]["not_usage"] = "fictional-secret"
        payload["choices"][0]["api_key"] = "fictional-secret"
        with guard.campaign_call("answer", request("answer")) as receipt:
            receipt.complete(payload)
        text = (instance.directory / "receipt-0001.json").read_text()
        assert "fictional-secret" not in text
        assert "yes" in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport", ["_post_embeddings", "_post_openai_embeddings", "_post_openrouter_embeddings"]
)
async def test_each_legacy_transport_denied_before_http(tmp_path, monkeypatch, transport):
    http = AsyncMock()
    monkeypatch.setattr(embedding.httpx, "AsyncClient", http)
    with pytest.raises(guard.CampaignStopped):
        with campaign(tmp_path).activate() as instance:
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            kwargs = dict(
                api_key="fictional", texts=["fictional"], model="fictional", output_dimension=1024
            )
            if transport != "_post_openai_embeddings":
                kwargs["input_type"] = "query"
            await getattr(embedding, transport)(**kwargs)
    http.assert_not_called()


@pytest.mark.asyncio
async def test_real_llm_hook_records_receipt_and_stops_on_missing_usage(tmp_path, monkeypatch):
    import litellm

    mock = AsyncMock(return_value=response("answer"))
    monkeypatch.setattr(litellm, "acompletion", mock)
    config = SimpleNamespace(
        timeout_s=1,
        base_url=guard.BASE_URL,
        api_key="fictional-secret",
        extra_headers={"Authorization": "fictional-secret"},
    )
    monkeypatch.setattr(
        evaluate, "get_settings", lambda: SimpleNamespace(get_provider_config=lambda _: config)
    )
    instance = campaign(tmp_path)
    with pytest.raises(guard.CampaignStopped):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            send("embedding")
            await evaluate._call_llm_with_provider_config(
                guard.LLM_MODEL, [{"role": "user", "content": "fictional"}], 0, 256, "answer"
            )
            assert mock.await_count == 1
            mock.return_value = {"model": "openai/gpt-4o-2024-08-06", "choices": []}
            await evaluate._call_llm_with_provider_config(
                guard.LLM_MODEL, [{"role": "user", "content": "fictional"}], 0, 256, "judge"
            )
    assert mock.await_count == 2
    saved = json.loads(instance.state_path.read_text())
    assert saved["pending"]["stage"] == "judge"
    assert "fictional-secret" not in instance.state_path.read_text()


def test_response_before_canonical_checkpoint_is_never_replayed(tmp_path):
    instance = campaign(tmp_path)
    with pytest.raises(RuntimeError, match="checkpoint"):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            for stage in guard.STAGES:
                send(stage)
            raise RuntimeError("synthetic checkpoint crash")
    assert len(instance.state["receipts"]) == 4
    with pytest.raises(guard.CampaignStopped):
        with campaign(tmp_path).activate():
            pytest.fail("Reader replay after completed receipt")


def test_arm_must_complete_all_questions(tmp_path):
    with pytest.raises(guard.CampaignStopped, match="Arm incomplete"):
        with campaign(tmp_path).activate() as instance:
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.finish_arm(qualification={"synthetic": True})


def test_complete_arm_boundary_allows_next_pinned_process_but_not_replay(tmp_path):
    saved: dict[str, Any] = {}
    for arm in guard.ARMS:
        with campaign(tmp_path).activate() as instance:
            instance.start_arm(arm, verified_pins=plan().pins, restore={"synthetic_arm": arm})
            for qid in plan().question_ids:
                instance.begin_question(qid)
                for stage in guard.STAGES:
                    send(stage)
                instance.finish_question(
                    {
                        "question_id": qid,
                        "official_judge_status": "completed",
                        "official_judgment": "correct",
                    }
                )
            instance.finish_arm(qualification={"synthetic": True})
        saved = json.loads(instance.state_path.read_text())
        assert saved["status"] == ("completed" if arm == "C0" else "ready_next_arm")
    assert len(saved["receipts"]) == 800  # Four stages includes embeddings, not just LLMs.
    assert len(saved["restores"]) == 4
    with pytest.raises(guard.CampaignStopped):
        with campaign(tmp_path).activate():
            pytest.fail("Completed campaign cannot restart")


def test_corrupt_campaign_is_not_overwritten(tmp_path):
    directory = tmp_path / "campaign"
    directory.mkdir()
    state = directory / "campaign.json"
    state.write_text('{"torn":')
    with pytest.raises(json.JSONDecodeError):
        with campaign(tmp_path).activate():
            pytest.fail("Corruption accepted")
    assert state.read_text() == '{"torn":'


def test_pins_cannot_mutate_during_activation(tmp_path):
    with pytest.raises(guard.CampaignStopped):
        with campaign(tmp_path).activate() as instance:
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            instance.plan["input_limits"]["synthetic-0"]["answer"] = 100000
            with guard.campaign_call("embedding", request()):
                pytest.fail("Mutated allocation used")


def test_duplicate_campaign_writer_refused_by_file_lock(tmp_path):
    def competing():
        with campaign(tmp_path, directory="other").activate():
            pytest.fail("Concurrent owner admitted")

    with active(tmp_path):
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(BlockingIOError):
                executor.submit(competing).result()
        assert not (tmp_path / "other").exists()


@pytest.mark.asyncio
async def test_canonical_catch_saves_partial_then_stops_before_second_question(
    tmp_path, monkeypatch
):
    from orchestrator.eval import fact_harness
    from tests.test_longmemeval_parity_harness import (
        BenchRuntime,
        DATASET,
        KEY_A,
        CONV_Q1_A,
        _ingested_checkpoint,
        _ingest_result,
        _key,
        _runner,
    )

    BenchRuntime(monkeypatch)
    dataset = [{**DATASET[0], "question_id": qid} for qid in plan().question_ids]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset))
    runner = _runner(tmp_path, path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    checkpoint = _ingested_checkpoint(path)
    checkpoint["phases"]["ingest"]["results"] = {
        _key(qid, KEY_A): _ingest_result(qid, KEY_A, CONV_Q1_A) for qid in plan().question_ids
    }
    runner.checkpoint_path.write_text(json.dumps(checkpoint))
    calls = []

    async def failed_official(**kwargs):
        calls.append(kwargs["question_id"])
        for stage in guard.STAGES[:3]:
            send(stage)
        try:
            with guard.campaign_call("official_judge", request("official_judge")):
                raise TimeoutError("synthetic paid request unknown")
        except TimeoutError:
            return {
                "question_id": kwargs["question_id"],
                "hypothesis": "fictional",
                "judgment": "correct",
                "official_judge_status": "failed",
            }

    monkeypatch.setattr(fact_harness, "evaluate_single_parity", failed_official)
    instance = campaign(tmp_path)
    with pytest.raises(guard.CampaignStopped):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.database_pool = await fact_harness.asyncpg.create_pool()
            await runner.evaluate()
    assert calls == ["synthetic-0"]
    rows = [json.loads(line) for line in runner.output_path.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["hypothesis"] == "fictional"
    assert rows[0]["official_judge_status"] == "failed"
    assert json.loads(instance.state_path.read_text())["pending"]["stage"] == "official_judge"
