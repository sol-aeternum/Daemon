"""Pure, unbound v3 allocation and attempt audit; no launch/retry authority.

B2b2 must supply the approved retry budget and an exact, pre-read failure-file
inventory. This module does no filesystem I/O and never changes campaign state.
Failure pins use basenames; failure_files maps those names to their raw bytes.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
import hashlib
import json
from typing import Any, Mapping

from orchestrator.eval.campaign_guard import (
    CAPS,
    EMBEDDING_RATE,
    INPUT_RATE,
    OUTPUT_RATE,
    STAGES,
    _hash,
    _integer,
)
from orchestrator.eval.campaign_layout import layout
from orchestrator.eval.campaign_validation import require


def validate_v3_arms(arms: tuple[str, ...]) -> None:
    require(
        type(arms) is tuple and arms in (("A0", "A1"), ("A0", "A1", "A2", "C0")),
        "V3 requires the pilot or candidate arm sequence",
    )


def _arm_count(arm_count: int) -> None:
    require(type(arm_count) is int and arm_count in (2, 4), "Invalid v3 arm count")


def _bounds(input_limits: Mapping[str, Mapping[str, int]]) -> tuple[Decimal, Decimal]:
    require(bool(input_limits), "Missing v3 input limits")
    embedding = Decimal(0)
    llm = Decimal(0)
    for qid, limits in input_limits.items():
        require(isinstance(qid, str) and bool(qid), "Invalid v3 question identity")
        require(set(limits) == set(STAGES), "Each question needs exactly four stages")
        for stage in STAGES:
            tokens = _integer(limits[stage], positive=True)
            if stage == "embedding":
                require(tokens <= 8000, "Embedding exceeds qualified input cap")
                embedding += (tokens * EMBEDDING_RATE * 1_000_000).to_integral_value(
                    rounding=ROUND_CEILING
                ) / 1_000_000
            else:
                llm += tokens * INPUT_RATE + CAPS[stage] * OUTPUT_RATE
    return embedding, llm


def v3_allocation(input_limits: Mapping[str, Mapping[str, int]], arm_count: int) -> Decimal:
    """Base allocation only: one embedding and three calls per LLM stage."""
    _arm_count(arm_count)
    embedding, llm = _bounds(input_limits)
    return arm_count * (embedding + 3 * llm)


def v3_maximum_retry_reservation(
    input_limits: Mapping[str, Mapping[str, int]], arm_count: int
) -> Decimal:
    """Two additional full-bound attempts per sampled LLM slot, not embeddings."""
    _arm_count(arm_count)
    _, llm = _bounds(input_limits)
    return arm_count * 3 * 2 * llm


def v3_envelope_ok(base: Decimal, retry_budget: Decimal, new_reserved: Decimal) -> bool:
    require(
        all(
            type(value) is Decimal and value.is_finite() and value >= 0
            for value in (base, retry_budget, new_reserved)
        ),
        "Invalid v3 reservation amount",
    )
    return base + retry_budget <= new_reserved


def validate_v3_attempt_ledger(
    state: Mapping[str, Any],
    *,
    retry_budget: Decimal,
    failure_files: Mapping[str, bytes],
    settled: bool = False,
) -> None:
    """Audit a hand-built v3 ledger without reading files or authorizing retries.

    Groups/ordinals belong to (arm, question, stage, sample), never just a request
    hash: equal requests for different samples must have independent allowances.
    A terminal third failure is valid evidence, but cannot settle a handoff.
    """
    require(
        type(retry_budget) is Decimal and retry_budget.is_finite() and retry_budget >= 0,
        "Invalid v3 retry budget",
    )
    require(state.get("layout") == "v3", "V3 ledger layout missing")
    arms = tuple(state["arms"])
    validate_v3_arms(arms)
    plan = state["plan"]
    questions = list(plan["question_ids"])
    require(questions and len(set(questions)) == len(questions), "Invalid v3 question coverage")
    _bounds(plan["input_limits"])
    require(set(questions) == set(plan["input_limits"]), "V3 limits/identities differ")
    require(
        not any(
            key.startswith(("imported", "historical", "final_verdict", "attach_receipt"))
            for key in state
        )
        and state.get("attach_pending") is None,
        "V3 ledger cannot import or attach",
    )
    canonical = layout("v3")
    receipts = state["receipts"]
    # Validate the receipt prefix too, including embedding positions and samples.
    for index, entry in enumerate(receipts):
        ai, qi, si = canonical.inverse(index, question_count=len(questions), arm_count=len(arms))
        require(
            {k: v for k, v in entry.items() if k != "artifact"}
            == canonical.identity(arms[ai], questions[qi], si, entry["request_sha256"])
            and ("sample" not in entry or type(entry["sample"]) is int),
            "V3 receipt identity/index differs",
        )
    groups: dict[tuple[str, str, str, int], list[Mapping[str, Any]]] = {}
    reservations = []
    completed = []
    failures = set()
    total = Decimal(0)
    last_index = -1
    for item in state["attempts"]:
        require(
            set(item)
            <= {
                "identity",
                "receipt_index",
                "ordinal",
                "status",
                "reserved_usd",
                "next_wait_seconds",
                "failure",
                "receipt",
            },
            "V3 attempt has unknown fields",
        )
        identity = item["identity"]
        require(
            set(identity) == {"arm", "question_id", "stage", "sample", "request_sha256"}
            and identity["arm"] in arms
            and identity["question_id"] in questions
            and (identity["stage"], identity["sample"]) in canonical.slots[1:]
            and type(identity["sample"]) is int,
            "V3 attempt identity/sample differs",
        )
        slot = canonical.slots.index((identity["stage"], identity["sample"]))
        require(
            identity
            == canonical.identity(
                identity["arm"], identity["question_id"], slot, identity["request_sha256"]
            ),
            "V3 attempt request identity differs",
        )
        logical = (identity["arm"], identity["question_id"], identity["stage"], identity["sample"])
        group = groups.setdefault(logical, [])
        require(not group or group[0]["identity"] == identity, "V3 retry request hash changed")
        ordinal = item["ordinal"]
        require(
            type(ordinal) is int and ordinal == len(group) + 1 <= 3,
            "V3 retry ordinal/reset differs",
        )
        index = item["receipt_index"]
        expected = canonical.receipt_index(
            arms.index(identity["arm"]),
            questions.index(identity["question_id"]),
            slot,
            question_count=len(questions),
            arm_count=len(arms),
        )
        require(
            type(index) is int and index == expected and last_index <= index <= len(receipts),
            "V3 retry receipt index/order differs",
        )
        last_index = index
        require(type(item["reserved_usd"]) is str, "V3 reservation must be exact decimal text")
        amount = Decimal(item["reserved_usd"])
        bound = (
            plan["input_limits"][identity["question_id"]][identity["stage"]] * INPUT_RATE
            + CAPS[identity["stage"]] * OUTPUT_RATE
        )
        require(
            amount.is_finite() and amount == (Decimal(0) if ordinal == 1 else bound),
            "V3 retry reservation differs from full bound",
        )
        if ordinal > 1:
            require(group[-1]["status"] == "failed_429", "V3 retry follows non-429 outcome")
            total += amount
            reservations.append(
                {"identity_sha256": _hash(identity), "ordinal": ordinal, "usd": str(amount)}
            )
        status = item["status"]
        require(
            status
            in {
                "failed_429",
                "terminal_failed",
                "reserved_waiting",
                "dispatch_pending",
                "completed",
            },
            "Unknown v3 attempt state",
        )
        if status == "completed":
            require(index < len(receipts), "V3 completed receipt missing")
            entry = receipts[index]
            require(
                {k: v for k, v in entry.items() if k != "artifact"} == identity
                and entry["artifact"] == item["receipt"],
                "V3 completed attempt receipt differs",
            )
            completed.append(index)
        else:
            require("receipt" not in item, "V3 unresolved attempt claims receipt")
        failed = status in {"failed_429", "terminal_failed"}
        require(("failure" in item) == failed, "V3 failure evidence missing/unexpected")
        if failed:
            pin = item["failure"]
            name = f"failure-{index:04d}-attempt-{ordinal:02d}.json"
            require(
                set(pin) == {"path", "sha256"} and pin["path"] == name and name in failure_files,
                "V3 failure pin differs",
            )
            raw = failure_files[name]
            require(hashlib.sha256(raw).hexdigest() == pin["sha256"], "V3 failure hash differs")
            failure = json.loads(raw)
            failures.add(name)
            require(
                set(failure) == {"pending", "ordinal", "diagnostics"}
                and failure["pending"] == identity
                and type(failure["ordinal"]) is int
                and failure["ordinal"] == ordinal,
                "V3 failure identity/ordinal differs",
            )
            diagnostics = failure["diagnostics"]
            chain = diagnostics["cause_chain"]
            require(
                set(diagnostics) == {"classification", "cause_chain"} and 1 <= len(chain) <= 4,
                "V3 failure diagnostics differ",
            )
            for cause in chain:
                require(
                    set(cause) == {"exception_type", "http_status_code"}
                    and isinstance(cause["exception_type"], str)
                    and (
                        cause["http_status_code"] is None
                        or type(cause["http_status_code"]) is int
                        and 100 <= cause["http_status_code"] <= 599
                    ),
                    "V3 failure cause chain differs",
                )
            if status == "failed_429":
                wait = item.get("next_wait_seconds")
                require(
                    ordinal < 3
                    and chain[0] == {"exception_type": "RateLimitError", "http_status_code": 429}
                    and diagnostics["classification"] == "provider_error"
                    and type(wait) is int
                    and (30 if ordinal == 1 else 60) <= wait <= 120,
                    "V3 retry lacks verified 429/bounded backoff",
                )
        group.append(item)
    expected_completed = {i for i, entry in enumerate(receipts) if entry["stage"] != "embedding"}
    require(
        len(completed) == len(set(completed)) and set(completed) == expected_completed,
        "V3 ledger omits/duplicates completed slots",
    )
    require(set(failure_files) == failures, "V3 failure files not exactly covered")
    require(
        reservations == state["retry_reservations"] and total <= retry_budget,
        "V3 retry reservations exceed budget",
    )
    if settled:
        require(
            state.get("pending") is None
            and all(g[-1]["status"] == "completed" for g in groups.values()),
            "Unresolved v3 attempt at handoff",
        )
