"""Fixed, single-use v3 pilot approval, bindings and artifact qualification.

Claims use the existing cooperative same-UID operator trust model. This pilot
is exposed only through campaign_launch's explicitly selected v3 pilot path.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from orchestrator.eval import campaign_fresh, campaign_v3, campaign_v3_plan
from orchestrator.eval.campaign_guard import (
    LLM_START_INTERVAL,
    CAPS,
    EMBEDDING_RATE,
    INPUT_RATE,
    OUTPUT_RATE,
    Campaign,
    CampaignPlan,
    _hash,
    _json,
    _integer,
    _sync_directory,
)
from orchestrator.eval.campaign_layout import layout
from orchestrator.eval.campaign_retry import POLICY
from orchestrator.eval.campaign_validation import (
    SNAPSHOT_SHA256,
    STATE_SHA256,
    DEV_SHA256,
    RETRIEVAL_MODE,
    expected_corpus,
    require,
)

ARMS = ("A0", "A1")
APPROVAL: dict[str, Any] = {
    "id": "2026-11-02-v3-pilot",
    "mode": "pilot",
    "arms": ["A0", "A1"],
    "layout": "v3",
    "acceptance_rule": campaign_v3.RULE_V3,
    "directory": "/home/sol/.local/share/daemon-lme-bench/runs/remap-historical-2026-10-31/campaign-v3-pilot",
    "new_reserved_usd": "7.7877940",
    "maximum_allocated_usd": "6.6877940",
    "retry_headroom_usd": "1.10",
    "local_maximum_restore_attempts": 2,
    "retry_policy": POLICY,
    "historical_remap_lineage_sha256": "4f77d026dc4336190b5fe26372254c1d653784df76255fdb316a504fde101a91",
}
PRIOR: dict[str, Any] = {
    "plan_path": "/home/sol/.local/share/opencode-work/evidence/memory-tuning-20261028/campaign-plan-fresh-v2approval-20261031.json",
    "plan_sha256": "c0db3058f6ee765652fdab68ba2ed134d2bccff6f25fa2567e16c75f0aa7d206",
    "ledger_path": "/home/sol/.local/share/daemon-lme-bench/runs/remap-historical-2026-10-31/campaign-noted-dates-v2/campaign.json",
    "ledger_sha256": "4c1987a89594922cd3810e52d3a57ff5a0d2775fc087cb009f965e40dc5cb6ae",
    "prior_reserved_usd": "26.7519050",
    "prior_restore_attempts": 11,
}
PIN_KEYS = (
    campaign_fresh.PIN_KEYS - {"fresh_campaign", "candidate_variable", "candidate_formatter"}
) | {"v3_campaign"}
LEGACY_MODE_PINS = frozenset(
    {
        "fresh_campaign",
        "recovery_lineage",
        "retry_recovery_lineage",
        "final_verdict_recovery_lineage",
        "replacement_lineage",
    }
)


def bindings(control: str, remap: str, tokenizer_sha256: str) -> dict[str, Any]:
    from orchestrator.eval import fact_harness

    return {
        "mode": "pilot",
        "dev_sha256": DEV_SHA256,
        "control_sha": control,
        "control_formatter": fact_harness.memory_formatter_identity(),
        "remap_lineage_sha256": remap,
        "caps": dict(CAPS),
        "rates": {
            "input": str(INPUT_RATE),
            "output": str(OUTPUT_RATE),
            "embedding": str(EMBEDDING_RATE),
        },
        "tokenizer_sha256": tokenizer_sha256,
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
        "V3 preflight read-only/bindings/question coverage differs",
    )
    for row in cost["questions"]:
        _integer(row["reader_input_token_bound"], positive=True)


def qualification(
    rows: list[dict[str, Any]],
    dataset: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    score: Mapping[str, Any],
    *,
    corpus: tuple[str, str],
) -> dict[str, Any]:
    """Manifest/checkpoint parity with campaign_validation, plus sampled v3 gates.

    Source line references below name campaign_validation.py on the B2b2 base.
    All unchanged checks read the same fields; only the judgment summaries use
    B2b1's v3 summaries (150 samples, 50 majorities, sample disagreement).
    """
    summaries = campaign_v3.qualification_v3(rows, dataset)
    for row in rows:
        campaign_v3.validate_v3_row(row)
    snapshot_sha256, state_sha256 = corpus
    phases = manifest.get("phases", {})
    evaluated, scored = phases.get("evaluate", {}), phases.get("score", {})
    # Source 95-97: B2b1 replaces official/dual summaries with v3 at both phases/artifact.
    require(
        evaluated.get("v3_summaries") == summaries
        and evaluated.get("official_judgments") == summaries["majority"],
        "Evaluate v3 summary differs",
    )
    dual = {
        "official": summaries["majority"],
        "generic": summaries["generic_strict_majority"],
        "paired": summaries["paired"],
    }
    require(
        scored.get("v3_summaries") == summaries and scored.get("dual_judgments") == dual,
        "Score manifest v3 summary differs",
    )
    require(
        score.get("v3_summaries") == summaries and score.get("dual_judgments") == dual,
        "Score artifact v3 summary differs",
    )
    # Source 98-99: still one result per question, never 150 result rows.
    require(scored.get("error_count") == score.get("error_count") == 0, "Score errors")
    require(scored.get("result_count") == score.get("result_count") == 50, "Score count differs")
    # Source 100-104: the same three checkpoint phases must be completed.
    for phase in ("ingest", "evaluate", "score"):
        require(
            checkpoint.get("phases", {}).get(phase, {}).get("status") == "completed",
            f"{phase} incomplete",
        )
    # Source 105-108: same ingest session denominator and strict <1% threshold.
    ingest = checkpoint["phases"]["ingest"]
    sessions = list(ingest.get("results", {}).values())
    failures = sum(row.get("status") == "extraction_failed" for row in sessions)
    require(bool(sessions) and failures / len(sessions) < 0.01, "Ingest extraction failures")
    # Source 109-115: both checkpoint and manifest rollback evidence.
    require(
        all(
            row.get("restored_pre_session_state") is True
            for row in [*ingest.get("ingest_rollbacks", []), *manifest.get("ingest_rollbacks", [])]
        ),
        "Unproven ingest rollback",
    )
    # Source 116-119: same run qualification and disqualification fields.
    require(
        manifest.get("baseline_qualified") is True and manifest.get("disqualifications") == [],
        "Run unqualified",
    )
    # Source 120: same code-pin changes inventory.
    require(not manifest.get("code_pin_changes"), "Code changed during run")
    # Source 121-124: same source-ingest qualification.
    require(
        manifest.get("ingest_source", {}).get("baseline_qualified") is True,
        "Ingest source unqualified",
    )
    # Source 125: same execution git dirty flag.
    require(manifest.get("git", {}).get("dirty") is False, "Dirty execution code")
    # Source 126: same frozen dataset hash.
    require(manifest.get("dataset", {}).get("sha256") == DEV_SHA256, "Manifest dataset differs")
    # Source 127-129: plan-selected remapped snapshot, not the original corpus literal.
    require(
        manifest.get("ingest_snapshot", {}).get("sha256") == snapshot_sha256, "Snapshot differs"
    )
    # Source 130: same fact substrate.
    require(manifest.get("substrate") == "fact", "Wrong substrate")
    # Source 131-132: same evaluate state assertions.
    require(evaluated.get("state_matches_ingest_at_start") is True, "State mismatch override")
    require(evaluated.get("state_unchanged_by_evaluate") is True, "State mutated during evaluate")
    # Source 133-136: same start/end fingerprints, against the plan's remapped corpus.
    for key in ("state_fingerprint_start", "state_fingerprint_end"):
        require(
            evaluated.get(key, {}).get("sha256") == state_sha256, "Protected fingerprint differs"
        )
    # Source 137: one retrieval/embedding per question, still 50, not per sample.
    require(
        evaluated.get("retrieval_modes") == {RETRIEVAL_MODE: 50}, "Retrieval mode/count differs"
    )
    # Source 138-141: same runtime answer/judge provider fingerprints.
    require(
        all(evaluated.get("provider_fingerprints", {}).get(key) for key in ("answer", "judge")),
        "Provider fingerprints absent",
    )
    # Source 142-159: qualification_v3 above uses 50-row majority accuracy/category
    # sanity and complete 150-sample pairing/disagreement, never paired.total == 50.
    return {"v3": summaries, "extraction_failures": failures}


def validate_mode(pins: Mapping[str, Any]) -> None:
    require(not LEGACY_MODE_PINS.intersection(pins), "V3 campaign cannot mix other mode pins")
    require(set(pins) == PIN_KEYS, "V3 pilot requires exact pins")
    require(
        pins["v3_campaign"] == {"approval": APPROVAL, "prior": PRIOR}
        and pins["historical_remap"]["lineage_sha256"]
        == APPROVAL["historical_remap_lineage_sha256"],
        "V3 pilot approval/prior/remap pins differ",
    )
    shas = pins["arm_shas"]
    require(set(shas) == set(ARMS) and shas["A0"] == shas["A1"], "V3 pilot arm source differs")


def verify_prior() -> None:
    """Read/hash each direct ancestor once; never mutate or resume either file."""
    from orchestrator.eval.campaign_recovery import direct

    values = {}
    for name in ("plan", "ledger"):
        raw = direct(Path(PRIOR[name + "_path"])).read_bytes()
        require(
            hashlib.sha256(raw).hexdigest() == PRIOR[name + "_sha256"],
            f"V3 prior {name} digest changed",
        )
        values[name] = json.loads(raw)
    ledger = values["ledger"]
    require(
        ledger["status"] == "completed"
        and type(ledger["restore_attempts"]) is int
        and ledger["restore_attempts"] == 4,
        "V3 prior ledger must be completed with four restores",
    )
    require(
        ledger.get("plan_sha256") == _hash(values["plan"]) or ledger.get("plan") == values["plan"],
        "V3 prior ledger plan differs from pinned v2 plan",
    )


def payload(plan: CampaignPlan) -> dict[str, Any]:
    validate_mode(plan.pins)
    verify_prior()
    require(
        len(plan.question_ids) == len(set(plan.question_ids)) == 50
        and all(isinstance(qid, str) and qid for qid in plan.question_ids)
        and set(plan.input_limits) == set(plan.question_ids),
        "V3 pilot requires exactly 50 unique dev IDs/allocations",
    )
    allocated = campaign_v3_plan.v3_allocation(plan.input_limits, 2)
    require(
        allocated <= Decimal(APPROVAL["maximum_allocated_usd"]),
        "V3 allocations exceed approved base cap",
    )
    new = Decimal(APPROVAL["new_reserved_usd"])
    prior = Decimal(PRIOR["prior_reserved_usd"])
    result = {
        "schema": 1,
        "arms": list(ARMS),
        "layout": "v3",
        "acceptance_rule": APPROVAL["acceptance_rule"],
        "question_ids": plan.question_ids,
        "input_limits": plan.input_limits,
        "pins": plan.pins,
        "allocated_usd": str(allocated),
        "new_reserved_usd": str(new),
        "prior_reserved_usd": str(prior),
        "cumulative_reserved_usd": str(prior + new),
        "prior_restore_attempts": PRIOR["prior_restore_attempts"],
        "maximum_cumulative_restore_attempts": PRIOR["prior_restore_attempts"]
        + APPROVAL["local_maximum_restore_attempts"],
        "retry_policy": dict(POLICY),
        "llm_start_interval_seconds": LLM_START_INTERVAL,
    }
    require(
        campaign_v3_plan.v3_envelope_ok(allocated, retry_budget(result), new),
        "V3 allocations/retries exceed approved envelope",
    )
    return result


def retry_budget(plan: Mapping[str, Any]) -> Decimal:
    return min(
        Decimal(APPROVAL["retry_headroom_usd"]),
        Decimal(plan["new_reserved_usd"]) - Decimal(plan["allocated_usd"]),
    )


def campaign_for(
    directory: Path, plan: CampaignPlan, count_tokens: Callable[[str], int]
) -> Campaign:
    validate_mode(plan.pins)
    return Campaign(directory, plan, count_tokens, layout=layout("v3"), arms=ARMS)


def destination(directory: Path) -> None:
    require(
        directory.is_absolute()
        and directory == Path(APPROVAL["directory"])
        and not directory.is_symlink()
        and directory.parent.resolve(strict=True) == directory.parent,
        "V3 pilot requires fixed direct nonsymlink approval directory",
    )


def claim_path(directory: Path) -> Path:
    return directory.parent / f".v3-approval-{APPROVAL['id']}.json"


def claim_value(plan: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    return {"approval_id": APPROVAL["id"], "directory": str(directory), "plan_sha256": _hash(plan)}


def verify(plan: Mapping[str, Any], directory: Path | None = None) -> dict[str, Any]:
    from orchestrator.eval.campaign_recovery import direct

    pins = plan["pins"]
    validate_mode(pins)
    verify_prior()
    require(plan["layout"] == "v3" and plan["arms"] == list(ARMS), "V3 pilot layout/arms differ")
    remap = pins["historical_remap"]
    raw = direct(Path(remap["lineage_path"])).read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == APPROVAL["historical_remap_lineage_sha256"],
        "V3 remap lineage changed",
    )
    lineage = json.loads(raw)
    require(
        lineage["status"] == "remapped_snapshot_created"
        and lineage["source"]["snapshot_sha256"] == SNAPSHOT_SHA256
        and lineage["source"]["state_sha256"] == STATE_SHA256
        and lineage["snapshot"]["sha256"] == pins["snapshot"]["sha256"] == remap["snapshot_sha256"]
        and lineage["remapped_state_sha256"] == remap["state_sha256"]
        and Path(lineage["snapshot"]["path"]) == Path(pins["snapshot"]["path"]),
        "V3 remapped corpus pins differ",
    )
    if directory is not None:
        destination(directory)
        claim = claim_path(directory)
        if claim.exists() or claim.is_symlink() or directory.exists():
            direct(claim, directory.parent)
            require(
                json.loads(claim.read_bytes()) == claim_value(plan, directory)
                and directory.is_dir(),
                "V3 approval already claimed without matching campaign directory/plan",
            )
            direct(directory / "campaign.json", directory)
    return {"approval": APPROVAL, "prior": PRIOR}


def claim(plan: Mapping[str, Any], directory: Path) -> None:
    """Under the sibling lock, before mkdir; retain interrupted claims forever."""
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
        restore_attempts=0,
        restore_receipts=[],
        restores=[],
        qualifications=[],
        restore_pending=None,
        attach_pending=None,
    )


def validate_attempt_ledger(
    state: Mapping[str, Any], plan: Mapping[str, Any], directory: Path, *, settled: bool = False
) -> None:
    from orchestrator.eval.campaign_recovery import direct

    require(
        state["layout"] == plan["layout"] == "v3" and state["arms"] == plan["arms"] == list(ARMS),
        "V3 pilot ledger layout/arms differ",
    )
    files = {
        path.name: direct(path, directory).read_bytes() for path in directory.glob("failure-*.json")
    }
    campaign_v3_plan.validate_v3_attempt_ledger(
        state, retry_budget=retry_budget(plan), failure_files=files, settled=settled
    )


def _receipt_manifest(directory: Path, state: Mapping[str, Any]) -> None:
    """Bind the sampled serial identities to actual receipts, never legacy strides."""
    from orchestrator.eval.campaign_recovery import direct

    paths = set()
    for index, entry in enumerate(state["receipts"]):
        name = f"receipt-{index:04d}.json"
        require(entry["artifact"] == name, "V3 receipt path/order differs")
        path = direct(directory / name, directory)
        require(
            json.loads(path.read_bytes()).get("pending")
            == {k: v for k, v in entry.items() if k != "artifact"},
            "V3 receipt identity differs",
        )
        paths.add(path)
    require(set(directory.glob("receipt-*.json")) == paths, "Unexpected v3 receipt artifacts")


def _restores(state: Mapping[str, Any], plan: Mapping[str, Any], count: int) -> None:
    require(
        state.get("restore_pending") is None
        and state.get("attach_pending") is None
        and state["restore_attempts"]
        == len(state["restores"])
        == len(state["restore_receipts"])
        == count
        and state["restores"] == state["restore_receipts"]
        and 0 <= count <= 2
        and plan["prior_restore_attempts"] + count <= plan["maximum_cumulative_restore_attempts"],
        "V3 restore consistency differs",
    )
    for index, receipt in enumerate(state["restores"]):
        require(
            receipt["arm"] == ARMS[index]
            and receipt["fingerprint"]["sha256"] == expected_corpus(plan["pins"])[1]
            and receipt["script_sha256"] == plan["pins"]["restore_script"]["sha256"]
            and len(receipt["owned_pids"]) == len(set(receipt["owned_pids"])) == 10,
            "V3 verified restore receipt differs",
        )


def validate_handoff(
    directory: Path, state: Mapping[str, Any], _approval: Mapping[str, Any]
) -> None:
    plan = state["plan"]
    verify(plan, directory)
    index = state["arm_index"]
    require(
        1 <= index <= 2
        and len(state["receipts"]) == index * 500
        and len(state["qualifications"]) == index,
        "V3 handoff lacks full new execution",
    )
    _restores(state, plan, index)
    _receipt_manifest(directory, state)
    validate_attempt_ledger(state, plan, directory, settled=True)


def before_restore(campaign: Campaign) -> None:
    verify(campaign.plan, campaign.directory)
    state = campaign.state
    index = state["arm_index"]
    require(
        0 <= index < 2
        and len(state["receipts"]) == index * 500
        and len(state["qualifications"]) == index,
        "V3 restore requires completed prior arms",
    )
    _restores(state, campaign.plan, index)
    _receipt_manifest(campaign.directory, state)
    validate_attempt_ledger(state, campaign.plan, campaign.directory, settled=True)


def validate_completion(campaign: Campaign) -> None:
    verify(campaign.plan, campaign.directory)
    state = campaign.state
    index = state["arm_index"]
    require(
        0 <= index < 2
        and state["arm"] == ARMS[index]
        and len(state["receipts"]) == (index + 1) * 500
        and len(state["qualifications"]) == index,
        "V3 completion lacks full new execution",
    )
    _restores(state, campaign.plan, index + 1)
    _receipt_manifest(campaign.directory, state)
    validate_attempt_ledger(state, campaign.plan, campaign.directory, settled=True)
