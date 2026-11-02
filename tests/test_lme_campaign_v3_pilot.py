"""Fixed v3 pilot: synthetic ancestors, receipts and transports, never real data."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from orchestrator.eval import campaign_guard as guard, campaign_launch as launch
from orchestrator.eval import campaign_restore as restore, campaign_retry as retry
from orchestrator.eval import campaign_v3_pilot as pilot, campaign_v3_plan as audit
from orchestrator.eval import campaign_validation as validation
from orchestrator.eval.campaign_layout import layout
from tests.test_lme_campaign_guard import plan as legacy_plan, response, send
from tests.test_lme_campaign_retry import RateLimitError, call, fake_time, llm, request
from tests.test_lme_campaign_v3_harness import completed_row


def write(path, value):
    path.write_bytes(guard._json(value) + b"\n")


def test_frozen_approval_and_prior_literals():
    assert pilot.APPROVAL == {
        "id": "2026-11-02-v3-pilot",
        "mode": "pilot",
        "arms": ["A0", "A1"],
        "layout": "v3",
        "acceptance_rule": "2026-11-01-paired-sign-v3",
        "directory": "/home/sol/.local/share/daemon-lme-bench/runs/remap-historical-2026-10-31/campaign-v3-pilot",
        "new_reserved_usd": "7.7877940",
        "maximum_allocated_usd": "6.6877940",
        "retry_headroom_usd": "1.10",
        "local_maximum_restore_attempts": 2,
        "retry_policy": {
            "maximum_attempts": 3,
            "backoff_seconds": [30, 60],
            "maximum_wait_seconds": 120,
            "minimum_start_interval_seconds": 5,
            "retryable_status": 429,
        },
        "historical_remap_lineage_sha256": "4f77d026dc4336190b5fe26372254c1d653784df76255fdb316a504fde101a91",
    }
    assert pilot.PRIOR == {
        "plan_path": "/home/sol/.local/share/opencode-work/evidence/memory-tuning-20261028/campaign-plan-fresh-v2approval-20261031.json",
        "plan_sha256": "c0db3058f6ee765652fdab68ba2ed134d2bccff6f25fa2567e16c75f0aa7d206",
        "ledger_path": "/home/sol/.local/share/daemon-lme-bench/runs/remap-historical-2026-10-31/campaign-noted-dates-v2/campaign.json",
        "ledger_sha256": "4c1987a89594922cd3810e52d3a57ff5a0d2775fc087cb009f965e40dc5cb6ae",
        "prior_reserved_usd": "26.7519050",
        "prior_restore_attempts": 11,
    }


@pytest.fixture
def approved(tmp_path, monkeypatch):
    prior = tmp_path / "prior-plan.json"
    write(prior, {"synthetic": "v2 plan"})
    ledger = tmp_path / "prior-ledger.json"
    write(
        ledger,
        {
            "status": "completed",
            "restore_attempts": 4,
            "plan_sha256": guard._hash({"synthetic": "v2 plan"}),
        },
    )
    snapshot = tmp_path / "snapshot"
    snapshot.write_bytes(b"synthetic remapped snapshot")
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
    approval = copy.deepcopy(pilot.APPROVAL)
    approval.update(
        directory=str(tmp_path / "campaign"),
        historical_remap_lineage_sha256=validation.digest_file(lineage),
    )
    previous = {
        **pilot.PRIOR,
        "plan_path": str(prior),
        "plan_sha256": validation.digest_file(prior),
        "ledger_path": str(ledger),
        "ledger_sha256": validation.digest_file(ledger),
    }
    monkeypatch.setattr(pilot, "APPROVAL", approval)
    monkeypatch.setattr(pilot, "PRIOR", previous)
    pins: dict[str, Any] = dict.fromkeys(pilot.PIN_KEYS, "synthetic")
    pins.update(
        v3_campaign={"approval": copy.deepcopy(approval), "prior": copy.deepcopy(previous)},
        arm_shas={"A0": "control", "A1": "control"},
        formatter={"id": "control"},
        historical_remap={
            "lineage_path": str(lineage),
            "lineage_sha256": validation.digest_file(lineage),
            "snapshot_sha256": validation.digest_file(snapshot),
            "state_sha256": "synthetic-remapped-state",
        },
        snapshot={"path": str(snapshot), "sha256": validation.digest_file(snapshot)},
        restore_script={"path": str(tmp_path / "restore.sql"), "sha256": "synthetic-script"},
    )
    base = legacy_plan()
    plan = guard.CampaignPlan(base.question_ids, copy.deepcopy(base.input_limits), pins)
    forbidden = AsyncMock(side_effect=AssertionError("No DB/provider work"))
    monkeypatch.setattr(launch.asyncpg, "connect", forbidden)
    monkeypatch.setattr(launch.asyncpg, "create_pool", forbidden)
    return SimpleNamespace(
        plan=plan,
        directory=Path(approval["directory"]),
        prior=prior,
        ledger=ledger,
        lineage=lineage,
        forbidden=forbidden,
        payload=json.loads(guard._json(plan.payload())),
    )


def instance(approved):
    return pilot.campaign_for(approved.directory, approved.plan, len)


@contextmanager
def partial(approved):
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with instance(approved).activate() as c:
            yield c


def start(c):
    arm = c.arms[c.state["arm_index"]]
    c.pending_restore(arm, {"synthetic": arm})
    receipt = {
        "arm": arm,
        "fingerprint": {"sha256": "synthetic-remapped-state"},
        "script_sha256": "synthetic-script",
        "owned_pids": list(range(10)),
    }
    c.restored(arm, receipt)
    c.start_arm(arm, verified_pins=c.plan["pins"], restore=receipt)


async def complete_arm(c):
    start(c)
    for qid in c.plan["question_ids"]:
        c.begin_question(qid)
        send("embedding")
        for stage, _ in c.layout.slots[1:]:
            await call(stage)
        row = completed_row()
        row["question_id"] = qid
        c.finish_question(row)
    c.finish_arm(qualification={"synthetic": c.state["arm"]})


def test_payload_round_trip_and_accounting(approved, tmp_path):
    p = approved.payload
    assert p["allocated_usd"] == "2.6911000"
    assert (p["new_reserved_usd"], p["prior_reserved_usd"], p["cumulative_reserved_usd"]) == (
        "7.7877940",
        "26.7519050",
        "34.5396990",
    )
    assert (p["prior_restore_attempts"], p["maximum_cumulative_restore_attempts"]) == (11, 13)
    assert p["layout"] == "v3" and p["arms"] == ["A0", "A1"]
    assert (
        p["acceptance_rule"]
        == launch.acceptance_rule(approved.plan.pins)
        == "2026-11-01-paired-sign-v3"
    )
    assert pilot.retry_budget(p) == Decimal("1.10")
    assert p["retry_policy"] == retry.POLICY and p["llm_start_interval_seconds"] == 5.0
    path = tmp_path / "plan.json"
    write(path, p)
    assert json.loads(guard._json(launch.load_plan(path).payload())) == p
    write(path, {**p, "extra": True})
    with pytest.raises(guard.CampaignStopped):
        launch.load_plan(path)


@pytest.mark.parametrize("other", sorted(pilot.LEGACY_MODE_PINS))
def test_mixed_modes_never_fall_through(approved, other):
    approved.plan.pins[other] = {}
    for dispatch in (
        approved.plan.payload,
        lambda: retry.retry_capable(approved.plan),
        lambda: launch.acceptance_rule(approved.plan.pins),
        lambda: launch.checkout(approved.plan, "A0"),
    ):
        with pytest.raises(guard.CampaignStopped, match="cannot mix other mode pins"):
            dispatch()


@pytest.mark.parametrize("bad", ["missing", "extra", "arms", "unequal", "approval"])
def test_exact_pin_refusals(approved, bad):
    pins = approved.plan.pins
    if bad == "missing":
        pins.pop("formatter")
    elif bad == "extra":
        pins["unexpected"] = True
    elif bad == "arms":
        pins["arm_shas"]["A2"] = "control"
    elif bad == "unequal":
        pins["arm_shas"]["A1"] = "other"
    else:
        pins["v3_campaign"]["approval"]["new_reserved_usd"] = "8"
    with pytest.raises(guard.CampaignStopped):
        approved.plan.payload()


def test_envelope_and_allocation_refusals(approved, monkeypatch):
    assert not audit.v3_envelope_ok(Decimal("6.7"), Decimal("1.1"), Decimal("7.7"))
    approved.plan.input_limits[approved.plan.question_ids[0]]["answer"] = 1_000_000
    with pytest.raises(guard.CampaignStopped, match="base cap"):
        approved.plan.payload()
    approved.plan.input_limits[approved.plan.question_ids[0]]["answer"] = 500
    monkeypatch.setitem(pilot.APPROVAL, "new_reserved_usd", "2")
    approved.plan.pins["v3_campaign"]["approval"]["new_reserved_usd"] = "2"
    with pytest.raises(guard.CampaignStopped, match="reservation amount|envelope"):
        approved.plan.payload()


def test_initialization_factory_claim_and_attach_capability(approved):
    with partial(approved) as c:
        assert c.layout == layout("v3") and c.arms == ("A0", "A1")
        assert c.state["layout"] == "v3" and c.state["arms"] == ["A0", "A1"]
        for key in (
            "attempts",
            "retry_reservations",
            "restores",
            "restore_receipts",
            "qualifications",
        ):
            assert c.state[key] == []
        assert c.state["restore_attempts"] == 0
        assert c.state["restore_pending"] is c.state["attach_pending"] is None
        assert (
            retry.retry_capable(c)
            and retry.retry_capable(approved.plan)
            and retry.retry_capable(c.plan)
        )
        assert not retry.enabled(c)
        pilot.validate_attempt_ledger(c.state, c.plan, c.directory, settled=True)
        path = pilot.claim_path(c.directory)
        assert json.loads(path.read_bytes()) == pilot.claim_value(c.plan, c.directory)
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("bad", ["default", "legacy", "candidate_arms"])
def test_v3_pin_requires_v3_pilot_construction(approved, bad):
    kwargs: dict[str, Any] = {} if bad == "default" else {"layout": layout("legacy")}
    if bad == "candidate_arms":
        kwargs = {"layout": layout("v3"), "arms": guard.ARMS}
    c = guard.Campaign(approved.directory, approved.plan, len, **kwargs)
    with pytest.raises(guard.CampaignStopped, match="explicit V3 layout and pilot arms"):
        with c.activate():
            pytest.fail("Wrong construction admitted")
    assert not approved.directory.exists()


@pytest.mark.asyncio
async def test_two_arms_one_sample_retry_and_settled_handoff(approved, monkeypatch):
    fake_time(monkeypatch)
    retried = False

    async def transport(*args, **kwargs):
        nonlocal retried
        active = guard.current_campaign()
        assert active is not None
        pending = active.state["pending"]
        if (pending["arm"], pending["question_id"], pending["stage"], pending["sample"]) == (
            "A1",
            "synthetic-7",
            "answer",
            2,
        ) and not retried:
            retried = True
            raise RateLimitError()
        return response(pending["stage"])

    mock = llm(monkeypatch, [])
    mock.side_effect = transport
    c = instance(approved)
    for index in range(2):
        c = instance(approved)
        with c.activate():
            await complete_arm(c)
        assert c.state["status"] == ("ready_next_arm" if index == 0 else "completed")
        assert len(c.state["receipts"]) == 500 * (index + 1)
        assert c.state["restore_attempts"] == index + 1
        pilot.validate_attempt_ledger(c.state, c.plan, c.directory, settled=True)
        if index == 0:
            for bad in ("unresolved", "count", "fingerprint", "layout", "arms"):
                state = copy.deepcopy(c.state)
                if bad == "unresolved":
                    state["pending"] = {"unresolved": True}
                elif bad == "count":
                    state["restore_attempts"] = 2
                elif bad == "fingerprint":
                    state["restores"][0]["fingerprint"]["sha256"] = "wrong"
                elif bad == "layout":
                    state["layout"] = "legacy"
                else:
                    state["arms"] = list(guard.ARMS)
                with pytest.raises(guard.CampaignStopped):
                    pilot.validate_handoff(c.directory, state, {})
    assert retried and mock.await_count == 901
    assert len(c.state["attempts"]) == 901 and len(c.state["retry_reservations"]) == 1
    attempts = [
        a
        for a in c.state["attempts"]
        if a["identity"]["question_id"] == "synthetic-7"
        and a["identity"]["stage"] == "answer"
        and a["identity"]["arm"] == "A1"
    ]
    assert [(a["identity"]["sample"], a["ordinal"]) for a in attempts] == [
        (1, 1),
        (2, 1),
        (2, 2),
        (3, 1),
    ]
    assert c.state["retry_reservations"][0]["usd"] == "0.0038100"
    before = c.state_path.read_bytes()
    with pytest.raises(guard.CampaignStopped):
        with instance(approved).activate():
            pytest.fail("Third activation admitted")
    assert c.state_path.read_bytes() == before
    approved.forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_hash_same_sample_refused(approved, monkeypatch):
    fake_time(monkeypatch)
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="logical identity/hash changed"):
        with c.activate():
            start(c)
            c.begin_question(c.plan["question_ids"][0])
            send("embedding")
            prepared = request("answer")
            async with guard.campaign_llm_call("answer", prepared) as receipt:
                await receipt.prepare_attempt()
                assert receipt.retry_failure(RateLimitError())
                prepared["messages"][0]["content"] = "changed"
                c.state["pending"]["request_sha256"] = guard._hash(prepared)
                await receipt.prepare_attempt()
    assert len(c.state["attempts"]) == 1


@pytest.mark.asyncio
async def test_retry_exhaustion_never_settles(approved, monkeypatch):
    fake_time(monkeypatch)
    transport = llm(monkeypatch, [RateLimitError(), RateLimitError(), RateLimitError()])
    c = instance(approved)
    with pytest.raises(launch.evaluate.BenchmarkProviderError):
        with c.activate():
            start(c)
            c.begin_question(c.plan["question_ids"][0])
            send("embedding")
            await call("answer")
    assert transport.await_count == 3
    assert c.state["attempts"][-1]["status"] == "terminal_failed"
    assert len(c.state["retry_reservations"]) == 2
    pilot.validate_attempt_ledger(c.state, c.plan, c.directory)
    with pytest.raises(guard.CampaignStopped, match="Unresolved"):
        pilot.validate_attempt_ledger(c.state, c.plan, c.directory, settled=True)


@pytest.mark.asyncio
async def test_retry_reservations_over_budget_refused(approved, monkeypatch):
    fake_time(monkeypatch)
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="headroom"):
        with c.activate():
            start(c)
            c.begin_question(c.plan["question_ids"][0])
            send("embedding")
            async with guard.campaign_llm_call("answer", request("answer")) as receipt:
                await receipt.prepare_attempt()
                assert receipt.retry_failure(RateLimitError())
                # The exact planner budget, not a second per-sample envelope.
                monkeypatch.setattr(pilot, "retry_budget", lambda _: Decimal("0.000001"))
                await receipt.prepare_attempt()
    assert c.state["retry_reservations"] == []


@pytest.mark.parametrize(
    "bad",
    ["count", "fingerprint", "script", "duplicates", "nine", "list_mismatch", "pending", "attach"],
)
def test_restore_validator_checks_full_receipts(approved, bad):
    with partial(approved) as c:
        start(c)
        if bad == "count":
            c.state["restore_attempts"] = 2
        elif bad == "list_mismatch":
            c.state["restore_receipts"] = []
        elif bad in ("pending", "attach"):
            c.state["restore_pending" if bad == "pending" else "attach_pending"] = {}
        else:
            receipt = c.state["restores"][0]
            if bad == "fingerprint":
                receipt["fingerprint"]["sha256"] = "wrong"
            elif bad == "script":
                receipt["script_sha256"] = "wrong"
            else:
                receipt["owned_pids"] = [0] * 10 if bad == "duplicates" else list(range(9))
        with pytest.raises(guard.CampaignStopped):
            pilot._restores(c.state, c.plan, 1)


def test_start_requires_matching_new_restore(approved):
    with pytest.raises(guard.CampaignStopped, match="new verified restore"):
        with instance(approved).activate() as c:
            c.start_arm("A0", verified_pins=c.plan["pins"], restore={"synthetic": True})


def test_failed_before_restore_prevents_mutation(approved, monkeypatch):
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="synthetic validation"):
        with c.activate():
            monkeypatch.setattr(
                pilot,
                "before_restore",
                Mock(side_effect=guard.CampaignStopped("synthetic validation")),
            )
            c.pending_restore("A0", {})
    assert c.state["restore_attempts"] == 0 and c.state["restore_pending"] is None
    assert c.state["status"] == "stopped_reconciliation_required"


def test_unfinished_context_exit_stops(approved):
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with c.activate():
            start(c)
    assert c.state["status"] == "stopped_reconciliation_required"


def test_third_restore_refused(approved):
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="completed prior arms"):
        with c.activate():
            c.state["arm_index"] = 2
            c.pending_restore("A2", {})
    assert c.state["restore_attempts"] == 0


@pytest.mark.asyncio
async def test_attached_pool_refused_without_database_work(approved):
    with partial(approved) as c:
        with pytest.raises(guard.CampaignStopped, match="only approved"):
            async with restore.attached_pool(c):
                pytest.fail("V3 attached to a legacy restored pool")
        approved.forbidden.assert_not_awaited()


@pytest.mark.parametrize(
    "bad", ["plan_digest", "ledger_digest", "status", "restores", "plan_binding", "symlink"]
)
def test_prior_verification_is_bound_and_direct(approved, monkeypatch, tmp_path, bad):
    if bad in ("plan_digest", "ledger_digest"):
        (approved.prior if bad == "plan_digest" else approved.ledger).write_text("{}")
    elif bad == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(approved.prior)
        monkeypatch.setitem(pilot.PRIOR, "plan_path", str(alias))
    else:
        ledger = json.loads(approved.ledger.read_bytes())
        ledger[
            {"status": "status", "restores": "restore_attempts", "plan_binding": "plan_sha256"}[bad]
        ] = "wrong"
        write(approved.ledger, ledger)
        monkeypatch.setitem(pilot.PRIOR, "ledger_sha256", validation.digest_file(approved.ledger))
    with pytest.raises(guard.CampaignStopped):
        pilot.verify_prior()


def test_prior_files_read_once_per_verification(approved, monkeypatch):
    original = Path.read_bytes
    reads = []

    def read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    pilot.verify_prior()
    assert reads == [approved.prior, approved.ledger]


def test_prior_change_blocks_before_restore(approved):
    c = instance(approved)
    with pytest.raises(guard.CampaignStopped, match="prior ledger digest"):
        with c.activate():
            approved.ledger.write_text("{}")
            c.pending_restore("A0", {})
    assert c.state["restore_attempts"] == 0


def test_checkout_v3_dispatch_and_unchanged_tail(approved, monkeypatch):
    verify = Mock(wraps=pilot.verify)
    monkeypatch.setattr(pilot, "verify", verify)
    forbidden = Mock(side_effect=AssertionError("No replacement dispatch"))
    monkeypatch.setattr(launch, "verify_replacement_lineage", forbidden)
    # Stop at the first unchanged tail check, before any real checkout/data reads.
    state = Mock(return_value={"sha": "wrong", "dirty": False})
    monkeypatch.setattr(launch, "git_state", state)
    with pytest.raises(guard.CampaignStopped, match="clean pinned SHA"):
        launch.checkout(approved.plan, "A0")
    verify.assert_called_once_with(approved.payload, approved.directory)
    forbidden.assert_not_called()
    state.assert_called_once()
    assert launch.arm_formatter(approved.plan, "A1") == approved.plan.pins["formatter"]
    # B2b3 now dispatches the destination to the same v3 verifier.
    verify.reset_mock()
    launch.verify_replacement_destination(approved.plan, approved.directory)
    verify.assert_called_once_with(approved.payload, approved.directory)
    forbidden.assert_not_called()


def test_claim_directory_and_partial_claim_refused(approved, tmp_path):
    with pytest.raises(guard.CampaignStopped, match="fixed direct"):
        pilot.destination(tmp_path / "other")
    pilot.claim(approved.payload, approved.directory)
    before = pilot.claim_path(approved.directory).read_bytes()
    with pytest.raises(guard.CampaignStopped, match="already claimed"):
        with instance(approved).activate():
            pytest.fail("Partial claim resumed")
    assert pilot.claim_path(approved.directory).read_bytes() == before
