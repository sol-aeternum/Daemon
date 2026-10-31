"""Single-use historical-remap campaign approval; never a recovery or resume mode.

Filesystem claims share the existing cooperative same-UID operator trust model.
No provider, database, restore, or credential operations belong in this module.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from orchestrator.eval.campaign_guard import (
    ARMS,
    CAPS,
    EMBEDDING_RATE,
    INPUT_RATE,
    LLM_START_INTERVAL,
    OUTPUT_RATE,
    STAGES,
    Campaign,
    CampaignPlan,
    _hash,
    _integer,
    _json,
    _sync_directory,
)
from orchestrator.eval.campaign_retry import POLICY
from orchestrator.eval.campaign_validation import (
    DEV_SHA256,
    SNAPSHOT_SHA256,
    STATE_SHA256,
    digest_file,
    expected_corpus,
    require,
)

APPROVAL: dict[str, Any] = {
    "id": "2026-10-31-noted-dates-historical-v1",
    "directory": "/home/sol/.local/share/daemon-lme-bench/runs/remap-historical-2026-10-31/campaign-noted-dates-v1",
    "new_reserved_usd": "5.50",
    "maximum_allocated_usd": "4.40",
    "retry_headroom_usd": "1.10",
    "local_maximum_restore_attempts": 4,
    "retry_policy": POLICY,
    "candidate_variable": "noted-dates",
    "historical_remap_lineage_sha256": "4f77d026dc4336190b5fe26372254c1d653784df76255fdb316a504fde101a91",
}
PRIOR: dict[str, Any] = {
    "plan_path": "/home/sol/.local/share/opencode-work/evidence/memory-tuning-20261028/campaign-plan-replacement-20261030-recovery-retry-final-verdict.json",
    "plan_sha256": "7a7379f05bcbd2c77811a3f12b92b56a80fc3aa7997c6d47ad530638d715e862",
    "prior_reserved_usd": "15.7519050",
    "prior_restore_attempts": 6,
}
PIN_KEYS = frozenset(
    {
        "common_files",
        "arm_shas",
        "formatter",
        "candidate_variable",
        "candidate_formatter",
        "tokenizer_sha256",
        "tokenizer_envelope",
        "server_identifier",
        "database_oid",
        "catalogue_sha256",
        "original_allow_connections",
        "snapshot",
        "ingest_checkpoint",
        "ingest_manifest",
        "route_evidence",
        "effective_config",
        "restore_script",
        "historical_remap",
        "fresh_campaign",
    }
)


def validate_mode(pins: Mapping[str, Any]) -> None:
    require(
        set(pins) == PIN_KEYS, "Fresh campaign requires exact pins; recovery/import/attach refused"
    )
    require(
        pins["fresh_campaign"] == {"approval": APPROVAL, "prior": PRIOR}
        and pins["candidate_variable"] == "noted-dates"
        and bool(pins["candidate_formatter"])
        and pins["historical_remap"]["lineage_sha256"]
        == APPROVAL["historical_remap_lineage_sha256"],
        "Fresh approval/prior/candidate/remap pins differ",
    )
    shas = pins["arm_shas"]
    require(
        set(shas) == set(ARMS) and shas["A0"] == shas["A1"] == shas["A2"],
        "Fresh control arm source differs",
    )


def payload(plan: CampaignPlan) -> dict[str, Any]:
    validate_mode(plan.pins)
    require(
        len(plan.question_ids) == len(set(plan.question_ids)) == 50
        and all(isinstance(qid, str) and qid for qid in plan.question_ids)
        and set(plan.input_limits) == set(plan.question_ids),
        "Fresh campaign requires exactly 50 unique dev IDs/allocations",
    )
    total = Decimal(0)
    for limits in plan.input_limits.values():
        require(set(limits) == set(STAGES), "Each question needs exactly four stages")
        for stage in STAGES:
            tokens = _integer(limits[stage], positive=True)
            if stage == "embedding":
                require(tokens <= 8000, "Embedding exceeds qualified input cap")
                amount = (tokens * EMBEDDING_RATE * 1_000_000).to_integral_value(
                    rounding=ROUND_CEILING
                ) / 1_000_000
            else:
                amount = tokens * INPUT_RATE + CAPS[stage] * OUTPUT_RATE
            total += 4 * amount
    require(
        total <= Decimal(APPROVAL["maximum_allocated_usd"]),
        "Fresh allocations exceed approved 4.40",
    )
    new = Decimal(APPROVAL["new_reserved_usd"])
    prior = Decimal(PRIOR["prior_reserved_usd"])
    return {
        "schema": 1,
        "arms": ARMS,
        "question_ids": plan.question_ids,
        "input_limits": plan.input_limits,
        "pins": plan.pins,
        "new_reserved_usd": str(new),
        "prior_reserved_usd": str(prior),
        "cumulative_reserved_usd": str(prior + new),
        "allocated_usd": str(total),
        "llm_start_interval_seconds": LLM_START_INTERVAL,
        "prior_restore_attempts": PRIOR["prior_restore_attempts"],
        "maximum_cumulative_restore_attempts": PRIOR["prior_restore_attempts"]
        + APPROVAL["local_maximum_restore_attempts"],
        "retry_policy": dict(POLICY),
    }


def verify_prior() -> None:
    from orchestrator.eval.campaign_recovery import direct

    path = direct(Path(PRIOR["plan_path"]))
    require(digest_file(path) == PRIOR["plan_sha256"], "Fresh prior plan digest changed")


def destination(directory: Path) -> None:
    require(
        directory.is_absolute()
        and directory == Path(APPROVAL["directory"])
        and not directory.is_symlink()
        and directory.parent.resolve(strict=True) == directory.parent,
        "Fresh campaign requires fixed direct nonsymlink approval directory",
    )


def claim_path(directory: Path) -> Path:
    return directory.parent / f".fresh-approval-{APPROVAL['id']}.json"


def claim_value(plan: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    return {"approval_id": APPROVAL["id"], "directory": str(directory), "plan_sha256": _hash(plan)}


def verify(plan: Mapping[str, Any], directory: Path | None = None) -> dict[str, Any]:
    from orchestrator.eval.campaign_recovery import direct

    pins = plan["pins"]
    validate_mode(pins)
    verify_prior()
    remap = pins["historical_remap"]
    path = direct(Path(remap["lineage_path"]))
    require(
        digest_file(path) == APPROVAL["historical_remap_lineage_sha256"],
        "Fresh remap lineage changed",
    )
    lineage = json.loads(path.read_bytes())
    require(
        lineage["status"] == "remapped_snapshot_created"
        and lineage["source"]["snapshot_sha256"] == SNAPSHOT_SHA256
        and lineage["source"]["state_sha256"] == STATE_SHA256
        and lineage["snapshot"]["sha256"] == pins["snapshot"]["sha256"] == remap["snapshot_sha256"]
        and lineage["remapped_state_sha256"] == remap["state_sha256"]
        and Path(lineage["snapshot"]["path"]) == Path(pins["snapshot"]["path"]),
        "Fresh remapped corpus pins differ",
    )
    if directory is not None:
        destination(directory)
        claim = claim_path(directory)
        if claim.exists() or claim.is_symlink() or directory.exists():
            direct(claim, directory.parent)
            require(
                json.loads(claim.read_bytes()) == claim_value(plan, directory)
                and directory.is_dir(),
                "Fresh approval already claimed without matching campaign directory/plan",
            )
            direct(directory / "campaign.json", directory)
    return {"approval": APPROVAL, "prior": PRIOR}


def claim(plan: Mapping[str, Any], directory: Path) -> None:
    """Under the sibling lock, BEFORE mkdir. A partial claim is never removed."""
    destination(directory)
    fd = os.open(claim_path(directory), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(_json(claim_value(plan, directory)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(directory.parent)


def initialize(state: dict[str, Any]) -> None:
    state.update(
        attempts=[],
        retry_reservations=[],
        attach_pending=None,
        restore_pending=None,
        restore_attempts=0,
        restore_receipts=[],
        qualifications=[],
    )


def retry_budget(plan: Mapping[str, Any]) -> Decimal:
    return min(
        Decimal(APPROVAL["retry_headroom_usd"]),
        Decimal(plan["new_reserved_usd"]) - Decimal(plan["allocated_usd"]),
    )


def validate_fresh_attempt_ledger(
    state: Mapping[str, Any], plan: Mapping[str, Any], directory: Path, *, settled: bool = False
) -> None:
    from orchestrator.eval.campaign_recovery import read_pin

    require(
        not any(
            key.startswith(("imported", "historical", "final_verdict", "attach_receipt"))
            for key in state
        )
        and state.get("attach_pending") is None,
        "Fresh campaign cannot import or attach",
    )
    groups: dict[str, list[Mapping[str, Any]]] = {}
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
            "Fresh attempt has historical/imported/unknown fields",
        )
        identity = item["identity"]
        require(
            set(identity) == {"arm", "question_id", "stage", "request_sha256"}
            and identity["arm"] in ARMS
            and identity["question_id"] in plan["question_ids"]
            and identity["stage"] in STAGES[1:]
            and isinstance(identity["request_sha256"], str)
            and len(identity["request_sha256"]) == 64
            and all(c in "0123456789abcdef" for c in identity["request_sha256"]),
            "Fresh retry attempt identity differs",
        )
        key = _hash(identity)
        group = groups.setdefault(key, [])
        ordinal = item["ordinal"]
        require(
            type(ordinal) is int and ordinal == len(group) + 1 <= 3,
            "Fresh retry ordinal/reset differs",
        )
        index = item["receipt_index"]
        expected = (
            ARMS.index(identity["arm"]) * 200
            + list(plan["question_ids"]).index(identity["question_id"]) * 4
            + STAGES.index(identity["stage"])
        )
        require(
            type(index) is int
            and index == expected
            and last_index <= index <= len(state["receipts"]),
            "Fresh retry receipt index/order differs",
        )
        last_index = index
        amount = Decimal(item["reserved_usd"])
        bound = (
            plan["input_limits"][identity["question_id"]][identity["stage"]] * INPUT_RATE
            + CAPS[identity["stage"]] * OUTPUT_RATE
        )
        require(
            amount.is_finite() and amount == (0 if ordinal == 1 else bound),
            "Fresh retry reservation differs from full bound",
        )
        if ordinal > 1:
            require(group[-1]["status"] == "failed_429", "Fresh retry follows non-429 outcome")
            total += amount
            reservations.append({"identity_sha256": key, "ordinal": ordinal, "usd": str(amount)})
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
            "Unknown fresh retry state",
        )
        if status == "completed":
            require(index < len(state["receipts"]), "Fresh completed receipt missing")
            entry = state["receipts"][index]
            require(
                {k: v for k, v in entry.items() if k != "artifact"} == identity
                and entry["artifact"] == item["receipt"],
                "Fresh completed attempt receipt differs",
            )
            completed.append(index)
        else:
            require("receipt" not in item, "Fresh unresolved attempt claims receipt")
        failed = status in {"failed_429", "terminal_failed"}
        require(("failure" in item) == failed, "Fresh failure evidence missing/unexpected")
        if failed:
            path, failure = read_pin(item["failure"], directory)
            failures.add(path)
            require(
                path.name == f"failure-{index:04d}-attempt-{ordinal:02d}.json"
                and set(failure) == {"pending", "ordinal", "diagnostics"}
                and failure["pending"] == identity
                and failure["ordinal"] == ordinal,
                "Fresh failure identity/ordinal differs",
            )
            diagnostics = failure["diagnostics"]
            chain = diagnostics["cause_chain"]
            require(
                set(diagnostics) == {"classification", "cause_chain"} and 1 <= len(chain) <= 4,
                "Fresh failure diagnostics differ",
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
                    "Fresh failure cause chain differs",
                )
            if status == "failed_429":
                wait = item.get("next_wait_seconds")
                require(
                    ordinal < 3
                    and chain[0] == {"exception_type": "RateLimitError", "http_status_code": 429}
                    and diagnostics["classification"] == "provider_error"
                    and type(wait) in (int, float)
                    and (30 if ordinal == 1 else 60) <= wait <= 120,
                    "Fresh retry lacks verified 429/bounded backoff",
                )
        group.append(item)
    expected_completed = {
        i for i, entry in enumerate(state["receipts"]) if entry["stage"] != "embedding"
    }
    require(
        len(completed) == len(set(completed)) and set(completed) == expected_completed,
        "Fresh retry ledger omits/duplicates completed stages",
    )
    require(
        set(directory.glob("failure-*.json")) == failures, "Fresh failure files not exactly covered"
    )
    require(
        reservations == state["retry_reservations"] and total <= retry_budget(plan),
        "Fresh retry reservations exceed headroom",
    )
    logical = [
        (g[0]["identity"]["arm"], g[0]["identity"]["question_id"], g[0]["identity"]["stage"])
        for g in groups.values()
    ]
    require(len(logical) == len(set(logical)), "Fresh retry logical identity/hash changed")
    if settled:
        require(
            all(g[-1]["status"] == "completed" for g in groups.values()),
            "Unresolved fresh attempt at handoff",
        )


def _restores(state: Mapping[str, Any], plan: Mapping[str, Any], count: int) -> None:
    require(
        state.get("restore_pending") is None
        and state.get("attach_pending") is None
        and state["restore_attempts"]
        == len(state["restores"])
        == len(state["restore_receipts"])
        == count
        and state["restores"] == state["restore_receipts"]
        and 0 <= count <= 4,
        "Fresh restore consistency differs",
    )
    for index, receipt in enumerate(state["restores"]):
        require(
            receipt["arm"] == ARMS[index]
            and receipt["fingerprint"]["sha256"] == expected_corpus(plan["pins"])[1]
            and receipt["script_sha256"] == plan["pins"]["restore_script"]["sha256"]
            and len(receipt["owned_pids"]) == len(set(receipt["owned_pids"])) == 10,
            "Fresh verified restore receipt differs",
        )


def validate_handoff(
    directory: Path, state: Mapping[str, Any], _approval: Mapping[str, Any]
) -> None:
    from orchestrator.eval.campaign_recovery import _receipt_manifest

    plan = state["plan"]
    verify(plan, directory)
    index = state["arm_index"]
    require(
        1 <= index <= 4
        and len(state["receipts"]) == index * 200
        and len(state["qualifications"]) == index,
        "Fresh handoff lacks full new execution",
    )
    _restores(state, plan, index)
    _receipt_manifest(directory, state)
    validate_fresh_attempt_ledger(state, plan, directory, settled=True)


def before_restore(campaign: Campaign) -> None:
    from orchestrator.eval.campaign_recovery import _receipt_manifest

    index = campaign.state["arm_index"]
    require(
        len(campaign.state["receipts"]) == index * 200
        and len(campaign.state["qualifications"]) == index,
        "Fresh restore requires completed prior arms",
    )
    _restores(campaign.state, campaign.plan, index)
    _receipt_manifest(campaign.directory, campaign.state)
    validate_fresh_attempt_ledger(campaign.state, campaign.plan, campaign.directory, settled=True)


def validate_completion(campaign: Campaign) -> None:
    from orchestrator.eval.campaign_recovery import _receipt_manifest

    verify(campaign.plan, campaign.directory)
    index = campaign.state["arm_index"]
    require(
        0 <= index < 4
        and campaign.state["arm"] == ARMS[index]
        and len(campaign.state["receipts"]) == (index + 1) * 200
        and len(campaign.state["qualifications"]) == index,
        "Fresh completion lacks full new execution",
    )
    _restores(campaign.state, campaign.plan, index + 1)
    _receipt_manifest(campaign.directory, campaign.state)
    validate_fresh_attempt_ledger(campaign.state, campaign.plan, campaign.directory, settled=True)


def bindings(control: str, candidate: str, remap: str, tokenizer_sha256: str) -> dict[str, Any]:
    from orchestrator.eval import campaign_launch as launch

    return {
        "dev_sha256": DEV_SHA256,
        "control_sha": control,
        "candidate_sha": candidate,
        "control_formatter": launch.fact_harness.memory_formatter_identity(),
        "candidate_formatter": launch.noted_dates_candidate_formatter(control, candidate),
        "remap_lineage_sha256": remap,
        "caps": dict(CAPS),
        "rates": {
            "input": str(INPUT_RATE),
            "output": str(OUTPUT_RATE),
            "embedding": str(EMBEDDING_RATE),
        },
        "tokenizer_sha256": tokenizer_sha256,
    }


def cost_preflight(
    dataset: Sequence[Mapping[str, Any]],
    memories: Mapping[str, Any],
    bound_bindings: Mapping[str, Any],
    count_tokens: Callable[[str], int],
) -> dict[str, Any]:
    """No retrieval: caller supplies already-gathered MemoryContextInputs per ID.

    Measure BOTH complete prompts, including historical runtime datetime, roles,
    system framing and the fixed send-time envelope allowance (not just memories).
    """
    from orchestrator.eval.fact_harness import question_as_of
    from orchestrator.memory import provenance
    from orchestrator.memory.injection import render_memory_context
    from tests.longmemeval.evaluate import build_parity_answer_messages

    require(
        set(memories) == {row["question_id"] for row in dataset},
        "Fresh preflight memories missing/extra",
    )
    questions = []
    original = provenance.RENDER_NOTED_DATES
    try:
        for row in dataset:
            counts = []
            for enabled in (False, True):
                provenance.RENDER_NOTED_DATES = enabled
                context = render_memory_context(memories[row["question_id"]])
                messages = build_parity_answer_messages(
                    row["question"], context, as_of=question_as_of(row)
                )
                counts.append(
                    64
                    + sum(
                        _integer(count_tokens(value))
                        for message in messages
                        for value in message.values()
                    )
                )
            questions.append(
                {"question_id": row["question_id"], "reader_input_token_bound": max(counts)}
            )
    finally:
        provenance.RENDER_NOTED_DATES = original
    return {
        "provider_calls": 0,
        "writes_or_restores": 0,
        "bindings": dict(bound_bindings),
        "questions": questions,
    }


def validate_preflight(
    cost: Mapping[str, Any], expected: Mapping[str, Any], ids: Sequence[str]
) -> None:
    require(
        type(cost.get("provider_calls")) is int
        and cost["provider_calls"] == 0
        and type(cost.get("writes_or_restores")) is int
        and cost["writes_or_restores"] == 0
        and cost.get("bindings") == expected
        and [row["question_id"] for row in cost["questions"]] == list(ids),
        "Fresh preflight read-only/bindings/question coverage differs",
    )
    for row in cost["questions"]:
        _integer(row["reader_input_token_bound"], positive=True)


async def preflight_cli(_args: Any) -> None:
    require(
        False,
        "Fresh cost-preflight blocked: offline retrieval replay is not implemented; inject already-retrieved memories into campaign_fresh.cost_preflight",
    )
