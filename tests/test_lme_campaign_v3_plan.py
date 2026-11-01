"""B2b1's unbound accounting/audit, with fictional in-memory failure files only."""

from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import json
from typing import Any

import pytest

from orchestrator.eval.campaign_guard import CampaignStopped, _hash
from orchestrator.eval.campaign_layout import layout
from orchestrator.eval.campaign_v3_plan import (
    v3_allocation,
    v3_envelope_ok,
    v3_maximum_retry_reservation,
    validate_v3_arms,
    validate_v3_attempt_ledger,
)

LIMITS = {"fictional": {"embedding": 50, "answer": 500, "judge": 500, "official_judge": 500}}


def test_frozen_literal_base_retry_and_envelope():
    # Per arm: .000001 embedding + 3 * (.00381 + .00381 + .00135).
    assert v3_allocation(LIMITS, 2) == Decimal("0.053822")
    assert v3_allocation(LIMITS, 4) == Decimal("0.107644")
    assert v3_maximum_retry_reservation(LIMITS, 2) == Decimal("0.10764")
    assert v3_maximum_retry_reservation(LIMITS, 4) == Decimal("0.21528")
    assert v3_envelope_ok(Decimal("0.053822"), Decimal("0.01"), Decimal("0.063822"))
    assert not v3_envelope_ok(Decimal("0.053822"), Decimal("0.01"), Decimal("0.063821"))


@pytest.mark.parametrize("tokens,expected", [(1, "0.053822"), (50, "0.053822"), (51, "0.053824")])
def test_embedding_rounding_boundary(tokens, expected):
    limits = copy.deepcopy(LIMITS)
    limits["fictional"]["embedding"] = tokens
    assert v3_allocation(limits, 2) == Decimal(expected)


def test_each_question_embedding_rounds_separately():
    limits = {
        "fictional-one": {**LIMITS["fictional"], "embedding": 51},
        "fictional-two": {**LIMITS["fictional"], "embedding": 51},
    }
    # Each 1.02-microdollar embedding rounds to 2, not a pooled 2.04 -> 3.
    assert v3_allocation(limits, 2) == Decimal("0.107648")


@pytest.mark.parametrize(
    "bad",
    ["empty", "missing_stage", "extra_stage", "zero", "negative", "float", "bool", "embedding_cap"],
)
def test_allocation_rejects_invalid_bounds(bad):
    limits: dict[str, dict[str, Any]] = copy.deepcopy(LIMITS)
    if bad == "empty":
        limits.clear()
    elif bad == "missing_stage":
        del limits["fictional"]["judge"]
    elif bad == "extra_stage":
        limits["fictional"]["extra"] = 500
    elif bad == "embedding_cap":
        limits["fictional"]["embedding"] = 8001
    else:
        limits["fictional"]["answer"] = {"zero": 0, "negative": -1, "float": 500.0, "bool": True}[
            bad
        ]
    with pytest.raises(CampaignStopped):
        v3_allocation(limits, 2)


@pytest.mark.parametrize("count", [0, 1, 3, 5, True, 2.0])
def test_invalid_arm_counts(count):
    with pytest.raises(CampaignStopped):
        v3_allocation(LIMITS, count)
    with pytest.raises(CampaignStopped):
        v3_maximum_retry_reservation(LIMITS, count)


@pytest.mark.parametrize("arms", [("A0",), ("A0", "C0"), ("A1", "A0"), ["A0", "A1"]])
def test_invalid_arm_sequence(arms):
    with pytest.raises(CampaignStopped):
        validate_v3_arms(arms)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), 1.0])
def test_invalid_money(value):
    with pytest.raises(CampaignStopped):
        v3_envelope_ok(Decimal(0), value, Decimal(1))


def ledger(*, exhausted: bool = False) -> tuple[dict[str, Any], dict[str, bytes]]:
    canonical = layout("v3")
    receipts = [
        {**canonical.identity("A0", "fictional", i, "a" * 64), "artifact": f"receipt-{i:04d}.json"}
        for i in range(4 if exhausted else 10)
    ]
    state: dict[str, Any] = {
        "layout": "v3",
        "arms": ["A0", "A1"],
        "plan": {"question_ids": ["fictional"], "input_limits": copy.deepcopy(LIMITS)},
        "receipts": receipts,
        "attempts": [],
        "retry_reservations": [],
        "pending": None,
    }
    files = {}
    for index in range(1, 5 if exhausted else 10):
        identity = canonical.identity("A0", "fictional", index, "a" * 64)
        attempts = 3 if exhausted and index == 4 else 2 if index == 4 else 1
        for ordinal in range(1, attempts + 1):
            failed = index == 4 and (exhausted or ordinal == 1)
            item = {
                "identity": identity.copy(),
                "receipt_index": index,
                "ordinal": ordinal,
                "reserved_usd": "0" if ordinal == 1 else "0.00381",
                "status": ("terminal_failed" if ordinal == 3 else "failed_429")
                if failed
                else "completed",
            }
            if failed:
                name = f"failure-{index:04d}-attempt-{ordinal:02d}.json"
                raw = json.dumps(
                    {
                        "pending": identity,
                        "ordinal": ordinal,
                        "diagnostics": {
                            "classification": "provider_error",
                            "cause_chain": [
                                {"exception_type": "RateLimitError", "http_status_code": 429}
                            ],
                        },
                    }
                ).encode()
                files[name] = raw
                item["failure"] = {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}
                if ordinal < 3:
                    item["next_wait_seconds"] = 30 if ordinal == 1 else 60
            else:
                item["receipt"] = receipts[index]["artifact"]
            state["attempts"].append(item)
            if ordinal > 1:
                state["retry_reservations"].append(
                    {
                        "identity_sha256": _hash(identity),
                        "ordinal": ordinal,
                        "usd": "0.00381",
                    }
                )
    return state, files


def test_sample_two_retry_has_own_allowance_and_is_pure():
    state, files = ledger()
    before = copy.deepcopy(state)
    validate_v3_attempt_ledger(
        state, retry_budget=Decimal("0.00381"), failure_files=files, settled=True
    )
    assert state == before
    answers = [a for a in state["attempts"] if a["identity"]["stage"] == "answer"]
    assert [(a["identity"]["sample"], a["ordinal"], a["reserved_usd"]) for a in answers] == [
        (1, 1, "0"),
        (2, 1, "0"),
        (2, 2, "0.00381"),
        (3, 1, "0"),
    ]


def test_third_failure_is_terminal_and_never_handoff_or_fourth_attempt():
    state, files = ledger(exhausted=True)
    validate_v3_attempt_ledger(state, retry_budget=Decimal("0.00762"), failure_files=files)
    with pytest.raises(CampaignStopped, match="Unresolved"):
        validate_v3_attempt_ledger(
            state, retry_budget=Decimal("0.00762"), failure_files=files, settled=True
        )
    fourth = copy.deepcopy(state["attempts"][-1])
    fourth["ordinal"] = 4
    state["attempts"].append(fourth)
    with pytest.raises(CampaignStopped, match="ordinal"):
        validate_v3_attempt_ledger(state, retry_budget=Decimal(1), failure_files=files)


def test_overlong_receipt_prefix_raises_campaign_stopped():
    state, files = ledger()
    state["receipts"] *= 3  # 30 entries exceed the two-arm, one-question capacity of 20.
    with pytest.raises(CampaignStopped, match="V3 receipt count exceeds layout"):
        validate_v3_attempt_ledger(state, retry_budget=Decimal(1), failure_files=files)


@pytest.mark.parametrize("target", ["receipt", "first_attempt"])
def test_nonhex_request_hash_raises_campaign_stopped(target):
    state, files = ledger()
    if target == "receipt":
        state["receipts"][0]["request_sha256"] = "g" * 64
    else:
        state["attempts"][0]["identity"]["request_sha256"] = "g" * 64
    with pytest.raises(CampaignStopped, match="Invalid v3 request SHA-256"):
        validate_v3_attempt_ledger(state, retry_budget=Decimal(1), failure_files=files)


@pytest.mark.parametrize(
    "bad",
    [
        "missing_sample",
        "duplicate_sample",
        "embedding",
        "index",
        "over_budget",
        "reservation",
        "unresolved",
        "changed_hash",
        "missing_failure",
        "extra_failure",
        "failure_hash",
        "wait",
        "float_wait",
        "non429",
        "duplicate_completion",
        "missing_completion",
        "sample_bool",
    ],
)
def test_ledger_negatives(bad):
    state, files = ledger()
    budget = Decimal("0.00381")
    # sample-2 answer is attempts[3] (failure) and attempts[4] (success).
    failure = state["attempts"][3]
    retry = state["attempts"][4]
    if bad == "missing_sample":
        del failure["identity"]["sample"]
    elif bad == "duplicate_sample":
        failure["identity"]["sample"] = 1
    elif bad == "embedding":
        failure["identity"].update(stage="embedding", sample=None)
    elif bad == "index":
        failure["receipt_index"] = 5
    elif bad == "over_budget":
        budget = Decimal("0.003809")
    elif bad == "reservation":
        retry["reserved_usd"] = "0.00382"
    elif bad == "unresolved":
        identity = layout("v3").identity("A1", "fictional", 1, "a" * 64)
        state["receipts"].append(
            {
                **layout("v3").identity("A1", "fictional", 0, "a" * 64),
                "artifact": "receipt-0010.json",
            }
        )
        state["attempts"].append(
            {
                "identity": identity,
                "receipt_index": 11,
                "ordinal": 1,
                "reserved_usd": "0",
                "status": "dispatch_pending",
            }
        )
    elif bad == "changed_hash":
        retry["identity"]["request_sha256"] = "b" * 64
    elif bad == "missing_failure":
        files.clear()
    elif bad == "extra_failure":
        files["failure-9999.json"] = b"{}"
    elif bad == "failure_hash":
        files[failure["failure"]["path"]] += b" "
    elif bad in {"wait", "float_wait"}:
        failure["next_wait_seconds"] = 121 if bad == "wait" else 30.0
    elif bad == "non429":
        failure["status"] = "terminal_failed"
    elif bad == "duplicate_completion":
        state["attempts"].append(copy.deepcopy(state["attempts"][-1]))
    elif bad == "missing_completion":
        state["attempts"].pop()
    elif bad == "sample_bool":
        state["attempts"][0]["identity"]["sample"] = True
    with pytest.raises(CampaignStopped):
        validate_v3_attempt_ledger(state, retry_budget=budget, failure_files=files, settled=True)
