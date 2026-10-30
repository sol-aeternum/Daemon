"""Bounded synthetic retry/recovery qualification; no credentials or live services."""

from __future__ import annotations

import asyncio
import argparse
import copy
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.eval import campaign_guard as guard, campaign_launch as launch
from orchestrator.eval import campaign_recovery as ancestor, campaign_retry as retry
from orchestrator.eval import campaign_restore as restore, fact_harness as harness
from tests.longmemeval import evaluate
from tests.test_lme_campaign_guard import request as base_request, response, send
from tests.test_lme_campaign_second_recovery import recovered as recovered
from tests.test_lme_campaign_second_recovery import pin, write

REAL_RETRY_BRIDGE = retry.source_bridge


@pytest.mark.parametrize("fail_after", [1, 4, 7, 202])
def test_retry_partial_bootstrap_never_replays(stopped, monkeypatch, fail_after):
    original_digest = launch.digest_file(stopped.source / "campaign.json")
    original_copy = retry._copy
    calls = 0

    def interrupted_copy(source, destination, digest):
        nonlocal calls
        original_copy(source, destination, digest)
        calls += 1
        if calls == fail_after:
            raise OSError("synthetic bootstrap interruption")

    monkeypatch.setattr(retry, "_copy", interrupted_copy)
    with pytest.raises(OSError, match="bootstrap interruption"):
        with guard.Campaign(stopped.destination, stopped.plan, len).activate():
            pytest.fail("Interrupted bootstrap exposed a live campaign")
    monkeypatch.setattr(retry, "_copy", original_copy)
    with pytest.raises((guard.CampaignStopped, FileNotFoundError)):
        with guard.Campaign(stopped.destination, stopped.plan, len).activate():
            pytest.fail("Interrupted retry bootstrap resumed")
    assert launch.digest_file(stopped.source / "campaign.json") == original_digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("question_index", 47),
        ("stage_index", 2),
        ("restore_attempts", 2),
        ("failure_admission", "unknown"),
        ("status", "ready_next_arm"),
    ],
)
def test_retry_semantic_parent_rejection_even_with_matching_hash(
    stopped, monkeypatch, field, value
):
    path = stopped.source / "campaign.json"
    state = json.loads(path.read_text())
    state[field] = value
    write(path, state)
    lineage = copy.deepcopy(stopped.lineage)
    lineage["stopped_ledger"] = pin(path)
    monkeypatch.setattr(retry, "LEDGER_SHA", launch.digest_file(path))
    with pytest.raises(guard.CampaignStopped, match="Stopped official-judge state differs"):
        retry.inspect_lineage(lineage)


class RateLimitError(Exception):
    def __init__(self, message="synthetic rate limit", status=429, headers=None):
        super().__init__(message)
        self.status_code = status
        self.headers = headers


def request(stage="embedding"):
    value = base_request(stage)
    if stage != "embedding":
        value["temperature"] = 0.0  # Exact JSON identity of the canonical LLM builder.
    return value


@pytest.fixture
def stopped(recovered, monkeypatch):
    source = recovered.destination
    previous_path = source.parent / "second-plan.json"
    write(previous_path, recovered.payload)
    campaign = guard.Campaign(source, recovered.plan, len)
    with pytest.raises(RateLimitError):
        with campaign.activate():
            campaign.pending_restore("A1", {"synthetic": True})
            receipt = {
                "arm": "A1",
                "fingerprint": {"sha256": retry.STATE_SHA256},
                "owned_pids": list(range(10)),
                "script_sha256": recovered.plan.pins["restore_script"]["sha256"],
            }
            campaign.restored("A1", receipt)
            campaign.start_arm("A1", verified_pins=recovered.plan.pins, restore=receipt)
            for index, qid in enumerate(recovered.plan.question_ids[:49]):
                campaign.begin_question(qid)
                for stage in guard.STAGES[:3]:
                    send(stage)
                if index == 48:
                    with guard.campaign_call("official_judge", request("official_judge")) as call:
                        error = RateLimitError()
                        call.failure_details(error)
                        raise error
                send("official_judge")
                campaign.finish_question(
                    {
                        "question_id": qid,
                        "official_judge_status": "completed",
                        "official_judgment": "correct",
                    }
                )
    campaign.state["failure_admission"] = "closed"
    write(campaign.state_path, campaign.state)
    rows = copy.deepcopy(recovered.rows[:49])
    for row in rows:
        row.update(
            run_id="original-executor",
            hypothesis="fixture",
            answer_usage={"prompt_tokens": 2},
            judge_usage={"prompt_tokens": 2},
            answer_fingerprint="fixture",
            judge_fingerprint="fixture",
            memory_context_sha256="context",
            system_prompt_sha256="prompt",
            retrieval_mode="embedding:openrouter:azure:openai/text-embedding-3-small:1024",
        )
    rows[48].pop("official_judgment")
    rows[48].update(judgment="correct", official_judge_status="failed", official_judge_usage=None)
    a1 = source / "A1"
    a1.mkdir()
    (a1 / retry.PREFIX_FILES[0]).write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    )
    checkpoint = {
        "run_id": "original-executor",
        "phases": {"evaluate": {"results": {row["question_id"]: row for row in rows}}},
    }
    manifest = {
        "run_id": "original-executor",
        "git": {"sha": "new-control", "dirty": False},
        "baseline_qualified": True,
        "disqualifications": [],
    }
    write(a1 / retry.PREFIX_FILES[1], checkpoint)
    write(a1 / retry.PREFIX_FILES[2], manifest)
    monkeypatch.setattr(retry, "OLD_CONTROL", "new-control")
    monkeypatch.setattr(retry, "OLD_CANDIDATE", "new-candidate")
    monkeypatch.setattr(retry, "PLAN_SHA", launch.digest_file(previous_path))
    monkeypatch.setattr(retry, "LEDGER_SHA", launch.digest_file(campaign.state_path))
    monkeypatch.setattr(retry, "ALLOCATED", Decimal(recovered.payload["allocated_usd"]))
    monkeypatch.setattr(retry, "PENDING_QID", recovered.plan.question_ids[48])
    monkeypatch.setattr(retry, "PENDING_SHA", campaign.state["pending"]["request_sha256"])
    monkeypatch.setattr(
        retry, "PREFIX_HASHES", tuple(launch.digest_file(a1 / name) for name in retry.PREFIX_FILES)
    )
    bridge = {
        "new_control": "retry-control",
        "old_control": "new-control",
        "files": {},
        "diff_sha256": "synthetic",
    }
    monkeypatch.setattr(retry, "source_bridge", lambda *_: copy.deepcopy(bridge))
    lineage = {"previous_plan": pin(previous_path), "stopped_ledger": pin(campaign.state_path)}
    _, metadata = retry.inspect_lineage(lineage)
    pins = copy.deepcopy(recovered.plan.pins)
    pins.update(
        retry_recovery_lineage=lineage,
        retry_policy=copy.deepcopy(retry.POLICY),
        retry_recovery={**metadata, "source_bridge": bridge},
        arm_shas={arm: "retry-candidate" if arm == "C0" else "retry-control" for arm in guard.ARMS},
    )
    plan = guard.CampaignPlan(recovered.plan.question_ids, recovered.plan.input_limits, pins)
    return SimpleNamespace(
        plan=plan,
        payload=json.loads(guard._json(plan.payload())),
        source=source,
        destination=source.with_name(source.name + "-retry"),
        lineage=lineage,
        rows=rows,
        inherited=metadata["inherited"],
        dataset=recovered.dataset,
    )


def fake_time(monkeypatch):
    clock, sleeps = [100.0], []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(guard.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(retry.asyncio, "sleep", sleep)
    return sleeps


def llm(monkeypatch, outcomes):
    import litellm

    mock = AsyncMock(side_effect=outcomes)
    monkeypatch.setattr(litellm, "acompletion", mock)
    config = SimpleNamespace(
        timeout_s=1, base_url=guard.BASE_URL, api_key="fictional-secret", extra_headers={}
    )
    monkeypatch.setattr(
        evaluate, "get_settings", lambda: SimpleNamespace(get_provider_config=lambda _: config)
    )
    return mock


async def call(stage="official_judge"):
    return await evaluate._call_llm_with_provider_config(
        guard.LLM_MODEL, request(stage)["messages"], 0, guard.CAPS[stage], stage
    )


@pytest.mark.parametrize(
    "status,message,headers,ordinal,expected",
    [
        (429, "rate limit", None, 1, 30),
        (429, "rate limit", None, 2, 60),
        (429, "rate limit", {"Retry-After": "90"}, 1, 90),
        (429, "rate limit", {"retry-after": "121"}, 1, None),
        (429, "rate limit", {"retry-after": "bad"}, 1, None),
        (429, "rate limit", {"retry-after": "-1"}, 1, None),
        (429, "rate limit", {"retry-after": ["30"]}, 1, None),
        (429, "billing quota exhausted", None, 1, None),
        (429, "rate limit", None, 3, None),
        (True, "rate limit", None, 1, None),
        (500, "rate limit", None, 1, None),
        (429, "insufficient credits", None, 1, None),
    ],
)
def test_only_explicit_transient_429_and_bounded_header(
    status, message, headers, ordinal, expected
):
    assert retry.retry_delay(RateLimitError(message, status, headers), ordinal) == expected


def test_retry_after_date_and_unknown_exception():
    now = datetime(2026, 10, 30, 0, 0, 0, tzinfo=UTC)
    error = RateLimitError(headers={"Retry-After": "Fri, 30 Oct 2026 00:01:30 GMT"})
    assert retry.retry_delay(error, 1, now=now) == 90
    error.headers = {"Retry-After": "Fri, 30 Oct 2026 00:03:00 GMT"}
    assert retry.retry_delay(error, 1, now=now) is None
    assert retry.retry_delay(TimeoutError("fictional-secret"), 1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes,success,additional",
    [
        ([response("official_judge")], True, 1),
        ([RateLimitError(), response("official_judge")], True, 2),
        ([RateLimitError(), RateLimitError()], False, 2),
        ([TimeoutError("fictional-secret")], False, 1),
        ([RateLimitError("quota exhausted")], False, 1),
        ([{"model": "openai/gpt-4o-2024-08-06", "choices": []}], False, 1),
    ],
)
async def test_recovered_attempts_preserve_pending_reserve_full_bound_and_stop(
    stopped, monkeypatch, outcomes, success, additional
):
    sleeps = fake_time(monkeypatch)
    mock = llm(monkeypatch, outcomes)
    instance = guard.Campaign(stopped.destination, stopped.plan, len)
    # This unit deliberately leaves q50 incomplete, so even a successful logical
    # official call must stop the overall campaign rather than create a handoff.
    with pytest.raises((guard.CampaignStopped, evaluate.BenchmarkProviderError)):
        with instance.activate():
            await call()
    assert mock.await_count == len(outcomes)
    assert len(instance.state["attempts"]) == 1 + mock.await_count
    assert len(instance.state["retry_reservations"]) == additional
    bound = Decimal(500) * guard.INPUT_RATE + 10 * guard.OUTPUT_RATE
    assert (
        sum(Decimal(item["usd"]) for item in instance.state["retry_reservations"])
        == bound * additional
    )
    assert len(instance.state["receipts"]) == (196 if success else 195)
    assert (instance.state["pending"] is None) == success
    assert 30 in sleeps
    if len(outcomes) > 1:
        assert 60 in sleeps
    for path in stopped.destination.glob("failure-*-attempt-*.json"):
        assert "fictional-secret" not in path.read_text()
    before = instance.state_path.read_bytes()
    with pytest.raises(guard.CampaignStopped):
        with guard.Campaign(stopped.destination, stopped.plan, len).activate():
            pytest.fail("Stopped retry campaign replayed")
    assert instance.state_path.read_bytes() == before


@pytest.mark.asyncio
async def test_cancel_during_reserved_wait_retains_hold_without_dispatch(stopped, monkeypatch):
    mock = llm(monkeypatch, [response("official_judge")])
    fake_time(monkeypatch)

    async def cancel(_):
        raise asyncio.CancelledError()

    instance = guard.Campaign(stopped.destination, stopped.plan, len)
    with pytest.raises(asyncio.CancelledError):
        with instance.activate():
            instance._last_llm_start -= 5
            monkeypatch.setattr(retry.asyncio, "sleep", cancel)
            await call()
    mock.assert_not_awaited()
    assert len(instance.state["retry_reservations"]) == 1
    assert instance.state["attempts"][-1]["status"] == "reserved_waiting"
    assert instance.state["pending"]["request_sha256"] == retry.PENDING_SHA


def test_retry_lineage_tamper_and_alternate_destination_blocked(stopped, tmp_path):
    retry.verify_recovery(stopped.payload)
    with pytest.raises(guard.CampaignStopped, match="fixed successor"):
        retry.verify_recovery(stopped.payload, tmp_path / "alternative")
    (stopped.source / "receipt-0195.json").write_text("{}")
    with pytest.raises(guard.CampaignStopped):
        retry.verify_recovery(stopped.payload)


@pytest.mark.asyncio
async def test_retry_preparation_is_exact_offline_and_cannot_reset_output(stopped, monkeypatch):
    previous = Path(stopped.lineage["previous_plan"]["path"])
    args = argparse.Namespace(
        previous_plan=previous,
        stopped_ledger=Path(stopped.lineage["stopped_ledger"]["path"]),
        previous_plan_sha256=stopped.lineage["previous_plan"]["sha256"],
        stopped_ledger_sha256=stopped.lineage["stopped_ledger"]["sha256"],
        output=previous.with_name(previous.stem + "-retry" + previous.suffix),
        control_sha="retry-control",
        candidate_sha="retry-candidate",
    )
    forbidden = AsyncMock(side_effect=AssertionError("No DB or network in retry preparation"))
    monkeypatch.setattr(launch.asyncpg, "connect", forbidden)
    monkeypatch.setattr(launch, "rates", forbidden)
    await retry.prepare(args)
    assert launch.load_plan(args.output).payload() == stopped.plan.payload()
    assert (
        launch.load_plan(previous).payload()["allocated_usd"]
        == stopped.plan.payload()["allocated_usd"]
    )
    with pytest.raises(guard.CampaignStopped, match="fixed successor"):
        await retry.prepare(args)
    args.output = previous.with_name("different-output.json")
    with pytest.raises(guard.CampaignStopped, match="fixed successor"):
        await retry.prepare(args)
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_request_hash_cannot_change(stopped, monkeypatch):
    fake_time(monkeypatch)
    mock = llm(monkeypatch, [response("official_judge")])
    with pytest.raises(guard.CampaignStopped, match="historical pending hash"):
        with guard.Campaign(stopped.destination, stopped.plan, len).activate():
            await evaluate._call_llm_with_provider_config(
                guard.LLM_MODEL, [{"role": "user", "content": "changed"}], 0, 10, "official_judge"
            )
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepared_hash_change_during_retry_wait_cannot_dispatch(stopped, monkeypatch):
    prepared = request("official_judge")
    instance = guard.Campaign(stopped.destination, stopped.plan, len)

    async def change(_):
        prepared["messages"][0]["content"] = "changed while waiting"

    monkeypatch.setattr(retry.asyncio, "sleep", change)
    with pytest.raises(guard.CampaignStopped, match="prepared request changed"):
        with instance.activate():
            async with guard.campaign_llm_call("official_judge", prepared) as receipt:
                await receipt.prepare_attempt()
                pytest.fail("Changed prepared request reached transport")
    assert len(instance.state["retry_reservations"]) == 1
    assert instance.state["attempts"][-1]["status"] == "reserved_waiting"


def test_retry_source_bridge_cannot_change_retrieval_or_configuration(monkeypatch):
    monkeypatch.setattr(launch, "git_bytes", lambda *_: b"synthetic-source")
    for path in ("orchestrator/memory/retrieval.py", "config/inference_policy.production.json"):
        monkeypatch.setattr(ancestor, "_git", lambda *_, path=path: path.encode())
        with pytest.raises(guard.CampaignStopped, match="other runtime"):
            REAL_RETRY_BRIDGE("synthetic-new-source")


@pytest.mark.asyncio
async def test_no_active_campaign_does_not_retry(monkeypatch):
    mock = llm(monkeypatch, [RateLimitError(), response("official_judge")])
    with pytest.raises(evaluate.BenchmarkProviderError):
        await call()
    assert mock.await_count == 1


def configure_attach(monkeypatch, *, failure=None):
    model = SimpleNamespace(open=False, pids=set(), sql=[], seal=0, next_pid=10)

    class Connection:
        def __init__(self, pid):
            self.pid = pid

        async def fetchval(self, _sql):
            return self.pid

    class Acquisition:
        async def __aenter__(self):
            model.next_pid += 1
            model.pids.add(model.next_pid)
            return Connection(model.next_pid)

        async def __aexit__(self, *_):
            pass

    class Pool:
        def acquire(self, **_):
            return Acquisition()

        async def close(self):
            model.pids.clear()

    class Control:
        async def execute(self, sql):
            model.sql.append(sql)
            model.open = sql.endswith("true")
            if failure == "enable_ack" and model.open:
                raise ConnectionError("synthetic lost enable acknowledgment")
            if failure == "cancel" and model.open:
                raise asyncio.CancelledError()
            if failure == "connection_loss" and not model.open:
                raise ConnectionError("synthetic lost control")

        async def close(self):
            pass

    async def identity(_):
        if failure == "connection_loss" and model.sql:
            raise ConnectionError("synthetic lost control")
        return {"system_identifier": "synthetic-server", "oid": 123, "datallowconn": model.open}

    async def sessions(_, pids):
        guard._integer(len(pids))
        assert model.open is False
        if failure == "foreign" or model.pids != pids:
            raise guard.CampaignStopped("Unowned or missing benchmark backend")

    monkeypatch.setattr(restore, "container_identity", lambda: {"synthetic": True})
    monkeypatch.setattr(restore, "verify_server", AsyncMock())
    monkeypatch.setattr(restore, "target_identity", identity)
    monkeypatch.setattr(restore, "verify_sessions", sessions)
    monkeypatch.setattr(
        restore,
        "catalogue",
        AsyncMock(return_value="changed" if failure == "catalogue" else "historical"),
    )
    monkeypatch.setattr(
        restore,
        "fingerprint_benchmark_users",
        AsyncMock(return_value={"sha256": "changed" if failure == "state" else retry.STATE_SHA256}),
    )
    monkeypatch.setattr(restore.asyncpg, "connect", AsyncMock(return_value=Control()))
    monkeypatch.setattr(restore.asyncpg, "create_pool", AsyncMock(return_value=Pool()))
    return model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "foreign", "catalogue", "state", "enable_ack", "connection_loss", "cancel"]
)
async def test_verified_attach_no_restore_and_fail_closed(stopped, monkeypatch, failure):
    model = configure_attach(monkeypatch, failure=failure)
    instance = guard.Campaign(stopped.destination, stopped.plan, len)
    with pytest.raises((guard.CampaignStopped, ConnectionError, asyncio.CancelledError)):
        with instance.activate():
            async with restore.attached_pool(instance) as pool:
                assert failure is None
                assert instance.state["attach_pending"] is None
                assert (
                    instance.state["attach_receipt"]["fingerprint"]["sha256"] == retry.STATE_SHA256
                )
                await pool.close()
    assert instance.state["restore_attempts"] == 1
    assert len(instance.state["restores"]) == 1
    assert all(
        sql.startswith("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS ") for sql in model.sql
    )
    assert not any("DROP" in sql for sql in model.sql)
    if failure == "connection_loss":
        assert instance.state["failure_admission"] == "unknown"
    elif failure not in (None, "foreign"):
        assert instance.state["failure_admission"] == "closed"


@pytest.mark.asyncio
async def test_canonical_q49_official_only_q50_four_new_stages_and_prefix_provenance(
    stopped, tmp_path, monkeypatch
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

    dataset = [{**DATASET[0], **row} for row in stopped.dataset]
    path = tmp_path / "synthetic-dev.json"
    write(path, dataset)
    BenchRuntime(
        monkeypatch, git_sha="retry-control", fingerprints=[{"sha256": retry.STATE_SHA256}]
    )
    fake_time(monkeypatch)
    instance = guard.Campaign(stopped.destination, stopped.plan, len)
    official_calls, parity_calls = [], []

    async def official(question, hypothesis, reference, **kwargs):
        official_calls.append(kwargs["question_id"])
        async with guard.campaign_llm_call("official_judge", request("official_judge")) as receipt:
            await receipt.prepare_attempt()
            receipt.complete(response("official_judge"))
        return {
            "official_judgment": "correct",
            "official_judge_usage": {"prompt_tokens": 2},
            "official_judge_fingerprint": "fixture",
        }

    async def parity(**kwargs):
        parity_calls.append(kwargs["question_id"])
        send("embedding")
        for stage in guard.STAGES[1:]:
            async with guard.campaign_llm_call(stage, request(stage)) as receipt:
                await receipt.prepare_attempt()
                receipt.complete(response(stage))
        return {
            **stopped.rows[0],
            "question_id": kwargs["question_id"],
            "question": kwargs["question_text"],
            "reference": kwargs["reference"],
            "category": kwargs["category"],
            "question_type": dataset[49]["question_type"],
            "official_judgment": "correct",
        }

    monkeypatch.setattr(evaluate, "judge_answer_official", official)
    monkeypatch.setattr(harness, "evaluate_single_parity", parity)
    configure_attach(monkeypatch)
    with instance.activate():
        async with restore.attached_pool(instance):
            output = stopped.destination / "A1"
            runner = harness.LongMemEvalFactRunner(
                dataset_path=path,
                output_path=output / retry.PREFIX_FILES[0],
                checkpoint_path=output / retry.PREFIX_FILES[1],
                score_path=output / "longmemeval_score.json",
                force_retrieval_logging=True,
            )
            checkpoint = _ingested_checkpoint(path, fingerprint={"sha256": retry.STATE_SHA256})
            checkpoint["phases"]["ingest"]["results"] = {
                _key(qid, KEY_A): _ingest_result(qid, KEY_A, CONV_Q1_A)
                for qid in stopped.plan.question_ids
            }
            write(runner.checkpoint_path, checkpoint)
            rows = await runner.evaluate()
            runner.score()
            manifest = json.loads((output / "run_manifest.json").read_bytes())
            retry.validate_prefix(instance, rows, manifest)
            for index, key, value in (
                (0, "hypothesis", "changed"),
                (48, "judgment", "incorrect"),
                (48, "official_judge_status", "failed"),
            ):
                altered = copy.deepcopy(rows)
                altered[index][key] = value
                with pytest.raises(guard.CampaignStopped):
                    retry.validate_prefix(instance, altered, manifest)
            instance.finish_arm(qualification={"synthetic": "A1"})
    assert official_calls == [retry.PENDING_QID]
    assert parity_calls == [stopped.plan.question_ids[49]]
    assert rows[:48] == stopped.rows[:48]
    assert (stopped.destination / "A1" / retry.PREFIX_FILES[0]).read_bytes().splitlines()[:48] == (
        stopped.source / "A1" / retry.PREFIX_FILES[0]
    ).read_bytes().splitlines()[:48]
    assert rows[48]["run_id"] == "original-executor"
    assert rows[49]["run_id"] != "original-executor"
    assert len(instance.state["receipts"]) == 200
    assert instance.state["status"] == "ready_next_arm"
    assert instance.state["restore_attempts"] == 1  # Cumulative remains four after attach.
    # Clean process handoffs retain the attributed A1 restore: only A2 and C0
    # consume the final two attempts, reaching cumulative six, never seven.
    for arm in ("A2", "C0"):
        with guard.Campaign(stopped.destination, stopped.plan, len).activate() as next_arm:
            next_arm.pending_restore(arm, {"synthetic": arm})
            restored = {
                "arm": arm,
                "fingerprint": {"sha256": retry.STATE_SHA256},
                "owned_pids": list(range(10)),
                "script_sha256": stopped.plan.pins["restore_script"]["sha256"],
            }
            next_arm.restored(arm, restored)
            next_arm.start_arm(arm, verified_pins=stopped.plan.pins, restore=restored)
            for qid in stopped.plan.question_ids:
                next_arm.begin_question(qid)
                send("embedding")
                for stage in guard.STAGES[1:]:
                    async with guard.campaign_llm_call(stage, request(stage)) as receipt:
                        await receipt.prepare_attempt()
                        receipt.complete(response(stage))
                next_arm.finish_question(
                    {
                        "question_id": qid,
                        "official_judge_status": "completed",
                        "official_judgment": "correct",
                    }
                )
            next_arm.finish_arm(qualification={"synthetic": arm})
    final_state = json.loads((stopped.destination / "campaign.json").read_bytes())
    assert final_state["status"] == "completed"
    assert final_state["restore_attempts"] == 3
    assert final_state["restore_attempts"] + stopped.payload["prior_restore_attempts"] == 6
    assert len(final_state["receipts"]) == 600


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["budget", "reset", "durability"])
async def test_retry_budget_reset_and_durability_refuse_before_dispatch(stopped, monkeypatch, case):
    fake_time(monkeypatch)
    mock = llm(monkeypatch, [response("official_judge")])
    instance = guard.Campaign(stopped.destination, stopped.plan, len)
    with pytest.raises((guard.CampaignStopped, OSError)):
        with instance.activate():
            if case == "budget":
                # Synthetic full-request quote exceeds the unchanged envelope.
                monkeypatch.setattr(retry, "INPUT_RATE", Decimal("100"))
            elif case == "reset":
                instance.state["attempts"] = []
            else:
                original_save = instance._save

                def fail_save():
                    if instance.state["attempts"][-1]["status"] == "reserved_waiting":
                        raise OSError("synthetic retry reservation durability failure")
                    original_save()

                monkeypatch.setattr(instance, "_save", fail_save)
            await call()
    mock.assert_not_awaited()
    assert instance._stopped
