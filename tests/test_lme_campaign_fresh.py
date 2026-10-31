"""Single-use fresh campaigns: synthetic files and mocked transports only."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, asynccontextmanager
import copy
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from orchestrator.eval import campaign_fresh as fresh, campaign_guard as guard
from orchestrator.eval import campaign_launch as launch, campaign_retry as retry
from orchestrator.eval import campaign_restore as restore, campaign_validation as validation
from tests.test_lme_campaign_guard import plan as legacy_plan, send, response
from tests.test_lme_campaign_retry import RateLimitError, fake_time, llm, call, request


def write(path, value):
    path.write_bytes(guard._json(value) + b"\n")


@pytest.fixture
def approved(tmp_path, monkeypatch):
    prior = tmp_path / "prior.json"
    write(prior, {"synthetic": "retained previous reservation"})
    snapshot = tmp_path / "snapshot"
    snapshot.write_bytes(b"synthetic remapped archive")
    lineage = tmp_path / "lineage.json"
    write(
        lineage,
        {
            "status": "remapped_snapshot_created",
            "source": {
                "snapshot_sha256": validation.SNAPSHOT_SHA256,
                "state_sha256": validation.STATE_SHA256,
            },
            "snapshot": {"path": str(snapshot), "sha256": validation.digest_file(snapshot)},
            "remapped_state_sha256": "synthetic-remapped-state",
        },
    )
    approval = copy.deepcopy(fresh.APPROVAL)
    approval.update(
        directory=str(tmp_path / "campaign"),
        historical_remap_lineage_sha256=validation.digest_file(lineage),
    )
    previous = {
        **fresh.PRIOR,
        "plan_path": str(prior),
        "plan_sha256": validation.digest_file(prior),
    }
    monkeypatch.setattr(fresh, "APPROVAL", approval)
    monkeypatch.setattr(fresh, "PRIOR", previous)
    base = legacy_plan()
    pins: dict[str, Any] = dict.fromkeys(fresh.PIN_KEYS, "synthetic")
    pins.update(
        fresh_campaign={"approval": copy.deepcopy(approval), "prior": copy.deepcopy(previous)},
        arm_shas={arm: "candidate" if arm == "C0" else "control" for arm in guard.ARMS},
        formatter={"id": "control"},
        candidate_formatter={"id": "candidate"},
        candidate_variable="noted-dates",
        historical_remap={
            "lineage_path": str(lineage),
            "lineage_sha256": validation.digest_file(lineage),
            "snapshot_sha256": validation.digest_file(snapshot),
            "state_sha256": "synthetic-remapped-state",
        },
        snapshot={"path": str(snapshot), "sha256": validation.digest_file(snapshot)},
        restore_script={"path": str(tmp_path / "restore.sql"), "sha256": "synthetic-script"},
    )
    plan = guard.CampaignPlan(base.question_ids, copy.deepcopy(base.input_limits), pins)
    forbidden = AsyncMock(side_effect=AssertionError("No DB/provider calls in fresh source tests"))
    monkeypatch.setattr(launch.asyncpg, "connect", forbidden)
    monkeypatch.setattr(launch.asyncpg, "create_pool", forbidden)
    return SimpleNamespace(
        plan=plan,
        directory=Path(approval["directory"]),
        lineage=lineage,
        prior=prior,
        payload=json.loads(guard._json(plan.payload())),
        forbidden=forbidden,
    )


def instance(approved):
    return guard.Campaign(approved.directory, approved.plan, len)


def start(campaign, *, question=True):
    arm = guard.ARMS[campaign.state["arm_index"]]
    campaign.pending_restore(arm, {"synthetic": arm})
    receipt = {
        "arm": arm,
        "fingerprint": {"sha256": "synthetic-remapped-state"},
        "owned_pids": list(range(10)),
        "script_sha256": "synthetic-script",
    }
    campaign.restored(arm, receipt)
    campaign.start_arm(arm, verified_pins=campaign.plan["pins"], restore=receipt)
    if question:
        campaign.begin_question(campaign.plan["question_ids"][0])
        send("embedding")


@contextmanager
def partial(approved):
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with c.activate():
            yield c


async def complete_arm(campaign):
    start(campaign, question=False)
    for qid in campaign.plan["question_ids"]:
        campaign.begin_question(qid)
        send("embedding")
        for stage in guard.STAGES[1:]:
            async with guard.campaign_llm_call(stage, request(stage)) as receipt:
                await receipt.prepare_attempt()
                receipt.complete(response(stage))
        campaign.finish_question(
            {
                "question_id": qid,
                "official_judge_status": "completed",
                "official_judgment": "correct",
            }
        )
    campaign.finish_arm(qualification={"synthetic": campaign.state["arm"]})


def test_legacy_payload_bytes_unchanged():
    # Captured from clean c2f752d9 before this implementation, same synthetic pins.
    assert (
        guard._hash(legacy_plan().payload())
        == "f1a0469405c0b01d0ffc2650cfb0b12577d3b692f3d76ebf1eaf00eb330bee06"
    )


def test_fresh_payload_accounting_and_empty_ledger(approved):
    p = approved.payload
    assert (p["new_reserved_usd"], p["prior_reserved_usd"], p["cumulative_reserved_usd"]) == (
        "5.50",
        "15.7519050",
        "21.2519050",
    )
    assert (p["prior_restore_attempts"], p["maximum_cumulative_restore_attempts"]) == (6, 10)
    assert p["retry_policy"] == retry.POLICY
    assert "execution_arms" not in p and "separate_capacity_reserved_usd" not in p
    with partial(approved) as c:
        assert c.state["attempts"] == c.state["retry_reservations"] == []
        assert c.state["attach_pending"] is None
        assert (
            retry.retry_capable(c) and retry.retry_capable(approved.plan) and not retry.enabled(c)
        )
        fresh.validate_fresh_attempt_ledger(c.state, c.plan, c.directory, settled=True)
        path = fresh.claim_path(c.directory)
        assert json.loads(path.read_bytes()) == fresh.claim_value(c.plan, c.directory)
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "pins,expected",
    [
        ({}, False),
        ({"recovery_lineage": {}}, False),
        ({"retry_recovery_lineage": {}}, True),
        ({"fresh_campaign": {}}, True),
    ],
)
def test_retry_capability_does_not_expand_attach(pins, expected):
    assert retry.retry_capable({"pins": pins}) is expected
    assert retry.retry_capable(None) is False


@pytest.mark.asyncio
async def test_four_fresh_arms_clean_handoffs_restore_seven_through_ten(approved, monkeypatch):
    fake_time(monkeypatch)
    c = instance(approved)
    for index, arm in enumerate(guard.ARMS):
        c = instance(approved)
        with c.activate():
            assert c.state["arm_index"] == index
            assert not retry.enabled(c)  # A1 cannot select attached_pool.
            await complete_arm(c)
        assert c.state["restore_attempts"] == index + 1
        assert c.plan["prior_restore_attempts"] + c.state["restore_attempts"] == 7 + index
        assert c.state["status"] == ("completed" if arm == "C0" else "ready_next_arm")
    assert len(c.state["receipts"]) == 800
    assert len(c.state["attempts"]) == 600
    assert c.state["restores"] == c.state["restore_receipts"]
    approved.forbidden.assert_not_awaited()
    before = c.state_path.read_bytes()
    with pytest.raises(guard.CampaignStopped):
        with instance(approved).activate():
            pytest.fail("Completed campaign cannot execute a fifth restore")
    assert c.state_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes,success,count",
    [
        ([RateLimitError(), response("answer")], True, 2),
        ([RateLimitError(), RateLimitError(), RateLimitError()], False, 3),
        ([TimeoutError("fictional-secret")], False, 1),
        ([RateLimitError(headers={"Retry-After": "121"})], False, 1),
    ],
)
async def test_fresh_transport_retry_success_and_terminal_paths(
    approved, monkeypatch, outcomes, success, count
):
    sleeps = fake_time(monkeypatch)
    transport = llm(monkeypatch, outcomes)
    c = instance(approved)
    with pytest.raises((guard.CampaignStopped, launch.evaluate.BenchmarkProviderError)):
        with c.activate():
            start(c)
            await call("answer")
    assert transport.await_count == count
    assert len(c.state["attempts"]) == count
    assert len(c.state["retry_reservations"]) == count - 1
    assert c.state["attempts"][-1]["status"] == ("completed" if success else "terminal_failed")
    assert c.state["status"] == "stopped_reconciliation_required"
    assert 30 in sleeps if count > 1 else True
    assert 60 in sleeps if count > 2 else True
    fresh.validate_fresh_attempt_ledger(c.state, c.plan, c.directory)
    if not success:
        with pytest.raises(guard.CampaignStopped, match="Unresolved"):
            fresh.validate_fresh_attempt_ledger(c.state, c.plan, c.directory, settled=True)
    before = c.state_path.read_bytes()
    with pytest.raises(guard.CampaignStopped):
        with instance(approved).activate():
            pytest.fail("Stopped campaign resumed")
    assert c.state_path.read_bytes() == before
    for path in c.directory.glob("failure-*.json"):
        assert "fictional-secret" not in path.read_text()


@pytest.mark.asyncio
async def test_changed_request_hash_cannot_reset_logical_retry(approved, monkeypatch):
    fake_time(monkeypatch)
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="logical identity/hash changed"):
        with c.activate():
            start(c)
            prepared = request("answer")
            async with guard.campaign_llm_call("answer", prepared) as receipt:
                await receipt.prepare_attempt()
                assert receipt.retry_failure(RateLimitError())
                # Even a forged consistent pending hash cannot restart this logical stage.
                prepared["messages"][0]["content"] = "changed"
                c.state["pending"]["request_sha256"] = guard._hash(prepared)
                await receipt.prepare_attempt()
    assert len(c.state["attempts"]) == 1


def test_claim_crash_before_mkdir_never_bootstraps(approved, monkeypatch):
    original = Path.mkdir

    def fail(path, *args, **kwargs):
        if path == approved.directory:
            raise OSError("synthetic crash after claim")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail)
    with pytest.raises(OSError, match="after claim"):
        with instance(approved).activate():
            pytest.fail("Mkdir interruption exposed campaign")
    path = fresh.claim_path(approved.directory)
    before = path.read_bytes()
    assert not approved.directory.exists()
    with pytest.raises(guard.CampaignStopped, match="already claimed"):
        with instance(approved).activate():
            pytest.fail("Claim alone bootstrapped a new campaign")
    assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["reservation", "restore_pending", "receipt"])
async def test_crashes_retain_state_and_never_resume(approved, monkeypatch, boundary):
    fake_time(monkeypatch)
    c = instance(approved)
    original = c._save
    crashed = False

    def crash():
        nonlocal crashed
        original()
        condition = {
            "reservation": bool(c.state.get("attempts")),
            "restore_pending": bool(c.state.get("restore_pending")),
            "receipt": len(c.state.get("receipts", [])) == 2,
        }[boundary]
        if condition and not crashed:
            crashed = True
            raise OSError("synthetic durable boundary interruption")

    monkeypatch.setattr(c, "_save", crash)
    with pytest.raises(OSError, match="boundary interruption"):
        with c.activate():
            start(c)
            async with guard.campaign_llm_call("answer", request("answer")) as receipt:
                await receipt.prepare_attempt()
                receipt.complete(response("answer"))
    saved = c.state_path.read_bytes()
    with pytest.raises(guard.CampaignStopped):
        with instance(approved).activate():
            pytest.fail("Interrupted state resumed")
    assert c.state_path.read_bytes() == saved
    assert fresh.claim_path(c.directory).exists()
    if boundary == "reservation":
        assert c.state["attempts"][-1]["status"] == "reserved_waiting"
    elif boundary == "restore_pending":
        assert c.state["restore_pending"] and c.state["restore_attempts"] == 1
    else:
        assert (
            len(c.state["receipts"]) == 2
            and c.state["attempts"][-1]["status"] == "dispatch_pending"
        )


@pytest.mark.parametrize(
    "extra",
    [
        "recovery_lineage",
        "retry_recovery_lineage",
        "imported_a0",
        "attach_pending",
        "final_verdict_recovery_lineage",
        "final_verdict_arbitrary",
    ],
)
def test_mixed_fresh_recovery_import_attach_pins_refused(approved, extra):
    pins = {**approved.plan.pins, extra: {}}
    with pytest.raises(guard.CampaignStopped, match="exact pins"):
        guard.CampaignPlan(approved.plan.question_ids, approved.plan.input_limits, pins).payload()


@pytest.mark.parametrize(
    "mutation", ["remap_pin", "remap_file", "prior", "approval", "candidate_variable"]
)
def test_changed_approval_remap_or_prior_refused(approved, mutation):
    if mutation == "remap_pin":
        approved.plan.pins["historical_remap"]["lineage_sha256"] = "changed"
    elif mutation == "remap_file":
        approved.lineage.write_text("{}")
    elif mutation == "prior":
        approved.prior.write_text("{}")
    elif mutation == "approval":
        approved.plan.pins["fresh_campaign"]["approval"]["new_reserved_usd"] = "6"
    else:
        approved.plan.pins.pop("candidate_variable")
    with pytest.raises(guard.CampaignStopped):
        with instance(approved).activate():
            pytest.fail("Changed approval admitted")
    assert not fresh.claim_path(approved.directory).exists()


def test_fixed_directory_changed_plan_and_symlink_parent_refused(approved, tmp_path, monkeypatch):
    with pytest.raises(guard.CampaignStopped, match="fixed direct"):
        with guard.Campaign(tmp_path / "second", approved.plan, len).activate():
            pytest.fail("Second directory reset allowance")
    with partial(approved):
        pass
    modified = copy.deepcopy(approved.payload)
    modified["input_limits"][modified["question_ids"][0]]["answer"] += 1
    with pytest.raises(guard.CampaignStopped, match="already claimed"):
        fresh.verify(modified, approved.directory)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setitem(fresh.APPROVAL, "directory", str(alias / "campaign"))
    with pytest.raises(guard.CampaignStopped, match="nonsymlink"):
        fresh.destination(alias / "campaign")


@pytest.mark.parametrize("case", ["duplicate", "fifth", "missing_receipt"])
def test_restore_repetition_limit_and_start_evidence(approved, case):
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped):
        with c.activate():
            if case == "fifth":
                c.state["arm_index"] = 4
                c.pending_restore("C0", {"synthetic": True})
            elif case == "missing_receipt":
                c.start_arm("A0", verified_pins=c.plan["pins"], restore={"synthetic": True})
            else:
                c.pending_restore("A0", {"synthetic": True})
                c.pending_restore("A0", {"synthetic": True})


@pytest.mark.asyncio
async def test_fresh_attach_refused_before_database_work(approved):
    with partial(approved) as c:
        with pytest.raises(guard.CampaignStopped, match="only approved"):
            async with restore.attached_pool(c):
                pytest.fail("Fresh campaign attached")
        approved.forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_prior_digest_checked_at_handoff_and_before_restore(approved, monkeypatch):
    fake_time(monkeypatch)
    with instance(approved).activate() as c:
        await complete_arm(c)
    approved.prior.write_text("{}")
    with pytest.raises(guard.CampaignStopped, match="prior plan digest"):
        with instance(approved).activate():
            pytest.fail("Changed prior admitted at handoff")


def test_prior_digest_changed_after_activation_blocks_pending_restore(approved):
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="prior plan digest"):
        with c.activate():
            approved.prior.write_text("{}")
            c.pending_restore("A0", {"synthetic": True})
    assert c.state["restore_attempts"] == 0


@pytest.mark.asyncio
async def test_c0_formatter_exception_requires_declared_recomputed_single_flag(
    approved, monkeypatch
):
    monkeypatch.setattr(launch, "noted_dates_candidate_formatter", lambda *_: {"id": "candidate"})
    assert launch.formatter_exception(approved.plan, "C0")
    assert not launch.formatter_exception(approved.plan, "A1")
    approved.plan.pins.pop("candidate_variable")
    assert not launch.formatter_exception(approved.plan, "C0")
    approved.plan.pins["candidate_variable"] = "noted-dates"
    monkeypatch.setattr(launch, "noted_dates_candidate_formatter", lambda *_: {"id": "changed"})
    with pytest.raises(guard.CampaignStopped, match="recomputation"):
        launch.formatter_exception(approved.plan, "C0")


def test_c0_formatter_recomputation_preserves_enabled_candidate_flag(approved, monkeypatch):
    from orchestrator.memory import provenance

    first = b"synthetic source\nRENDER_NOTED_DATES = False\n"
    second = first.replace(launch.NOTED_DATES_OFF, launch.NOTED_DATES_ON)
    monkeypatch.setattr(launch, "git_bytes", lambda sha, _: first if sha == "control" else second)
    approved.plan.pins["candidate_formatter"] = launch.noted_dates_candidate_formatter(
        "control", "candidate"
    )
    monkeypatch.setattr(provenance, "RENDER_NOTED_DATES", True)
    assert launch.formatter_exception(approved.plan, "C0") is True
    assert provenance.RENDER_NOTED_DATES is True
    with pytest.raises(guard.CampaignStopped, match="Control checkout"):
        launch.noted_dates_candidate_formatter("control", "candidate")


def test_reader_preflight_counts_both_complete_historical_prompts(approved, monkeypatch):
    from orchestrator.memory import provenance
    from orchestrator.memory.injection import MemoryContextInputs, render_memory_context
    from orchestrator.eval.fact_harness import question_as_of

    dataset = [
        {
            "question_id": "q",
            "question": "Where today?",
            "question_date": "2023/05/02 (Tue) 10:00",
            "haystack_dates": ["2023/05/01 (Mon) 10:00"],
        }
    ]
    memories = {
        "q": MemoryContextInputs(
            retrieved=[
                {"content": "Fictional place today", "valid_from": datetime(2023, 5, 1, tzinfo=UTC)}
            ]
        )
    }
    monkeypatch.setattr(launch, "noted_dates_candidate_formatter", lambda *_: {"id": "candidate"})
    b = fresh.bindings(
        "control",
        "candidate",
        approved.payload["pins"]["historical_remap"]["lineage_sha256"],
        "tokenizer",
    )
    seen = []

    def count(text):
        seen.append(text)
        return len(text)

    cost = fresh.cost_preflight(dataset, memories, b, count)
    original = provenance.RENDER_NOTED_DATES
    counts = []
    for flag in (False, True):
        provenance.RENDER_NOTED_DATES = flag
        messages = launch.evaluate.build_parity_answer_messages(
            "Where today?", render_memory_context(memories["q"]), as_of=question_as_of(dataset[0])
        )
        counts.append(64 + sum(len(v) for message in messages for v in message.values()))
    provenance.RENDER_NOTED_DATES = original
    assert cost["questions"] == [{"question_id": "q", "reader_input_token_bound": max(counts)}]
    assert any("2023-05-02" in value for value in seen)
    assert any("noted 2023-05-01" in value for value in seen)
    assert "system" in seen and "user" in seen
    fresh.validate_preflight(cost, b, ["q"])
    for key in b:
        altered = copy.deepcopy(cost)
        altered["bindings"][key] = "changed"
        with pytest.raises(guard.CampaignStopped, match="bindings"):
            fresh.validate_preflight(altered, b, ["q"])
    for key in ("provider_calls", "writes_or_restores"):
        altered = copy.deepcopy(cost)
        altered[key] = 1
        with pytest.raises(guard.CampaignStopped):
            fresh.validate_preflight(altered, b, ["q"])


@pytest.mark.asyncio
async def test_preflight_cli_fails_closed_without_replay(approved):
    with pytest.raises(guard.CampaignStopped, match="offline retrieval replay is not implemented"):
        await fresh.preflight_cli(argparse.Namespace(fresh=True))
    approved.forbidden.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "historical,candidate", [(None, "noted-dates"), (Path("fictional"), "retrieval")]
)
async def test_fresh_prepare_requires_exact_explicit_modes(approved, historical, candidate):
    with pytest.raises(guard.CampaignStopped, match="requires --historical-remap"):
        await launch.prepare(
            argparse.Namespace(
                fresh_campaign=True, historical_remap=historical, candidate=candidate
            )
        )
    approved.forbidden.assert_not_awaited()


def test_allocation_boundary_440_and_441(approved):
    limits = copy.deepcopy(approved.plan.input_limits)
    total = Decimal(approved.payload["allocated_usd"])
    step = 4 * guard.INPUT_RATE
    increase = (Decimal("4.40") - total) / step
    assert increase == int(increase)
    limits[approved.plan.question_ids[0]]["answer"] += int(increase)
    p = guard.CampaignPlan(approved.plan.question_ids, limits, approved.plan.pins)
    assert Decimal(p.payload()["allocated_usd"]) == Decimal("4.40")
    limits[approved.plan.question_ids[0]]["answer"] += 1
    with pytest.raises(guard.CampaignStopped, match="allocations exceed"):
        p.payload()
    limits[approved.plan.question_ids[0]]["answer"] += 999
    with pytest.raises(guard.CampaignStopped, match="allocations exceed"):
        p.payload()


def budget_ledger(approved, amount):
    """One synthetic failed first attempt plus a full-bound reserved retry."""
    plan = copy.deepcopy(approved.payload)
    qid = plan["question_ids"][0]
    # amount=1.10 -> exact full request quote at the unchanged answer output cap.
    tokens = (amount - guard.CAPS["answer"] * guard.OUTPUT_RATE) / guard.INPUT_RATE
    assert tokens == int(tokens)
    plan["input_limits"][qid]["answer"] = int(tokens)
    plan["allocated_usd"] = "4.40"
    identity = {"arm": "A0", "question_id": qid, "stage": "answer", "request_sha256": "a" * 64}
    path = approved.directory / "failure-0001-attempt-01.json"
    write(
        path,
        {
            "pending": identity,
            "ordinal": 1,
            "diagnostics": {
                "classification": "provider_error",
                "cause_chain": [{"exception_type": "RateLimitError", "http_status_code": 429}],
            },
        },
    )
    first = {
        "identity": identity,
        "receipt_index": 1,
        "ordinal": 1,
        "status": "failed_429",
        "reserved_usd": "0",
        "next_wait_seconds": 30,
        "failure": {"path": str(path), "sha256": validation.digest_file(path)},
    }
    second = {
        "identity": identity,
        "receipt_index": 1,
        "ordinal": 2,
        "status": "reserved_waiting",
        "reserved_usd": str(amount),
    }
    state = {
        "attempts": [first, second],
        "receipts": [{"stage": "embedding"}],
        "retry_reservations": [
            {"identity_sha256": guard._hash(identity), "ordinal": 2, "usd": str(amount)}
        ],
        "attach_pending": None,
    }
    return plan, state


@pytest.mark.parametrize(
    "amount,accepted", [(Decimal("1.10"), True), (Decimal("1.1000025"), False)]
)
def test_retry_and_total_budget_validator_boundaries(approved, amount, accepted):
    approved.directory.mkdir()
    plan, state = budget_ledger(approved, amount)
    if accepted:
        fresh.validate_fresh_attempt_ledger(state, plan, approved.directory)
        assert Decimal(plan["allocated_usd"]) + amount == Decimal("5.50")
    else:
        with pytest.raises(guard.CampaignStopped, match="exceed headroom"):
            fresh.validate_fresh_attempt_ledger(state, plan, approved.directory)


@pytest.mark.asyncio
async def test_prepare_attempt_budget_refused_before_persisting_reservation(approved, monkeypatch):
    fake_time(monkeypatch)
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="headroom"):
        with c.activate():
            start(c)
            async with guard.campaign_llm_call("answer", request("answer")) as receipt:
                await receipt.prepare_attempt()
                assert receipt.retry_failure(RateLimitError())
                monkeypatch.setattr(fresh, "retry_budget", lambda _: Decimal("0.0000001"))
                await receipt.prepare_attempt()
    assert len(c.state["attempts"]) == 1 and c.state["retry_reservations"] == []


@pytest.mark.asyncio
async def test_fresh_a1_launch_uses_restore_not_attach(approved, monkeypatch):
    """Exercise actual launcher selection without invoking any real path or action."""
    fake_time(monkeypatch)
    with instance(approved).activate() as c:
        await complete_arm(c)
    plan_path = approved.directory.parent / "plan.json"
    write(plan_path, approved.payload)
    review = approved.directory.parent / "review.json"
    write(
        review,
        {
            "supervisor": "astra",
            "status": "approved_for_exact_action",
            "plan_sha256": validation.digest_file(plan_path),
            "source_sha": "control",
            "arm": "A1",
            "review_session": "synthetic",
            "live_state_evidence_sha256": "synthetic",
            "reviewed_at": datetime.now(UTC).isoformat(),
        },
    )
    monkeypatch.setattr(launch, "checkout", lambda *_: None)
    monkeypatch.setattr(launch, "environment", lambda: approved.plan.pins["effective_config"])
    monkeypatch.setattr(
        launch,
        "tokenizer",
        lambda: (
            SimpleNamespace(encode_ordinary=lambda text: list(text)),
            approved.plan.pins["tokenizer_sha256"],
        ),
    )
    monkeypatch.setattr(launch, "rates", lambda: {})
    selected = []

    @asynccontextmanager
    async def restored(campaign, arm, snapshot):
        selected.append((arm, snapshot))
        raise RuntimeError("synthetic stop before restore or runner")
        yield

    monkeypatch.setattr(launch, "restored_pool", restored)
    monkeypatch.setattr(
        restore, "attached_pool", lambda *_: pytest.fail("Fresh A1 took attach path")
    )
    monkeypatch.setattr(launch, "load_dev", lambda *_: [])
    with pytest.raises(RuntimeError, match="before restore"):
        await launch.run_arm(
            argparse.Namespace(
                plan=plan_path, campaign=approved.directory, action_review=review, arm="A1"
            )
        )
    assert selected == [("A1", Path(approved.plan.pins["snapshot"]["path"]))]
    approved.forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_prepare_copies_fixed_approval_and_checks_all_bindings(approved, monkeypatch):
    from orchestrator.eval import bench_db
    from tests.test_lme_campaign_launch import qualified_artifacts

    dataset = qualified_artifacts()[1]
    root = approved.directory.parent
    source_manifest = root / "source_manifest.json"
    write(source_manifest, {"baseline_qualified": True, "disqualifications": []})
    ingest = root / "ingest.json"
    write(ingest, {"dataset_sha256": validation.DEV_SHA256})
    args = argparse.Namespace(
        fresh_campaign=True,
        historical_remap=approved.lineage,
        candidate="noted-dates",
        control_sha="control",
        candidate_sha="candidate",
        snapshot=Path(approved.plan.pins["snapshot"]["path"]),
        ingest=ingest,
        cost_preflight=root / "cost.json",
        output=root / "prepared.json",
    )
    monkeypatch.setattr(launch, "load_dev", lambda _: dataset)
    monkeypatch.setattr(launch, "environment", lambda: {"synthetic": True})
    historical = {**approved.plan.pins["historical_remap"], "source_manifest": str(source_manifest)}
    monkeypatch.setattr(launch, "_historical_remap_pins", lambda _: historical)
    monkeypatch.setattr(launch, "verify_common", lambda *_: {})
    monkeypatch.setattr(
        launch, "tokenizer", lambda: (SimpleNamespace(name="o200k_base"), "synthetic-tokenizer")
    )
    monkeypatch.setattr(launch.fact_harness, "memory_formatter_identity", lambda: {"id": "control"})
    monkeypatch.setattr(launch, "noted_dates_candidate_formatter", lambda *_: {"id": "candidate"})
    expected = fresh.bindings(
        "control", "candidate", historical["lineage_sha256"], "synthetic-tokenizer"
    )
    cost = {
        "provider_calls": 0,
        "writes_or_restores": 0,
        "bindings": expected,
        "questions": [
            {"question_id": row["question_id"], "reader_input_token_bound": 500} for row in dataset
        ],
    }
    for key in expected:
        altered = copy.deepcopy(cost)
        altered["bindings"][key] = "changed"
        write(args.cost_preflight, altered)
        with pytest.raises(guard.CampaignStopped, match="bindings"):
            await launch.prepare(args)
        assert not args.output.exists()
    approved.forbidden.assert_not_awaited()

    class Connection:
        async def close(self):
            pass

        @asynccontextmanager
        async def transaction(self, **_):
            yield

    connect = AsyncMock(return_value=Connection())
    monkeypatch.setattr(launch.asyncpg, "connect", connect)
    monkeypatch.setattr(launch, "rates", lambda: {})
    monkeypatch.setattr(launch, "container_identity", lambda: {})
    monkeypatch.setattr(launch, "verify_server", AsyncMock())
    monkeypatch.setattr(
        launch,
        "target_identity",
        AsyncMock(return_value={"datallowconn": True, "system_identifier": "synthetic", "oid": 1}),
    )
    monkeypatch.setattr(launch, "catalogue", AsyncMock(return_value="synthetic"))
    monkeypatch.setattr(
        bench_db,
        "fingerprint_benchmark_users",
        AsyncMock(return_value={"sha256": validation.STATE_SHA256}),
    )

    def render(snapshot, directory, digest):
        assert snapshot == args.snapshot and digest == validation.digest_file(snapshot)
        path = directory / "restore.sql"
        path.write_text("-- synthetic, never executed\n")
        return path

    monkeypatch.setattr(launch, "render_archive", render)
    write(args.cost_preflight, cost)
    await launch.prepare(args)
    prepared = launch.load_plan(args.output)
    assert prepared.pins["fresh_campaign"] == {"approval": fresh.APPROVAL, "prior": fresh.PRIOR}
    assert set(prepared.pins) == fresh.PIN_KEYS
    assert prepared.payload()["cumulative_reserved_usd"] == "21.2519050"
    assert "under_cumulative_ten_usd" not in cost  # Legacy preflight is not reused.
    assert connect.await_count == 2  # Mocked control + mocked read-only catalogue transaction.


def test_fresh_manifest_records_retry_policy_without_recovery_prefix(approved, monkeypatch):
    from orchestrator.eval import fact_harness
    from tests.test_longmemeval_parity_harness import _runner, _write_dataset

    dataset = _write_dataset(approved.directory.parent)
    runner = _runner(approved.directory.parent, dataset)
    monkeypatch.setattr(type(runner), "_new_manifest", lambda self, _: {"invocations": []})
    with partial(approved) as c:
        manifest = runner._open_manifest("evaluate", {"run_id": "synthetic"})
        assert manifest["campaign_retry_policy"] == {
            "policy": retry.POLICY,
            "plan_sha256": c._plan_hash,
            "protocol": "explicit-opt-in-429; actual attempt histories may differ",
        }
        assert "inherited_evaluation" not in manifest
        assert fact_harness.current_campaign() is c


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "historical",
        "failure_missing",
        "failure_extra",
        "cause",
        "wait",
        "ordinal",
        "receipt",
        "reservation",
    ],
)
async def test_fresh_attempt_ledger_rejects_tampering(approved, monkeypatch, tamper):
    fake_time(monkeypatch)
    transport = llm(monkeypatch, [RateLimitError(), response("answer")])
    with partial(approved) as c:
        start(c)
        await call("answer")
        assert transport.await_count == 2
        state = copy.deepcopy(c.state)
        if tamper == "historical":
            state["attempts"][0]["historical"] = True
        elif tamper == "failure_missing":
            state["attempts"][0].pop("failure")
        elif tamper == "failure_extra":
            write(c.directory / "failure-9999.json", {})
        elif tamper == "cause":
            pin = state["attempts"][0]["failure"]
            path = Path(pin["path"])
            value = json.loads(path.read_bytes())
            value["diagnostics"]["cause_chain"][0]["http_status_code"] = 500
            write(path, value)
            pin["sha256"] = validation.digest_file(path)
        elif tamper == "wait":
            state["attempts"][0]["next_wait_seconds"] = 29
        elif tamper == "ordinal":
            state["attempts"][1]["ordinal"] = 1
        elif tamper == "receipt":
            state["attempts"][1]["receipt_index"] = 0
        else:
            state["retry_reservations"] = []
        with pytest.raises(guard.CampaignStopped):
            fresh.validate_fresh_attempt_ledger(state, c.plan, c.directory)
