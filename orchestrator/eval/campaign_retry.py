"""Exact stopped-official-judge recovery and bounded, explicitly opted-in 429 retries.

Not general resume, provider fallback, or billing authority. Retained reservations
never refund. Filesystem evidence uses the campaign's cooperative operator trust.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

import httpx

from orchestrator.eval import campaign_recovery as ancestor
from orchestrator.eval.campaign_guard import (
    ARMS,
    CAPS,
    INPUT_RATE,
    OUTPUT_RATE,
    Campaign,
    CampaignPlan,
    _hash,
    _json,
    _sync_directory,
    _write,
)
from orchestrator.eval.campaign_validation import STATE_SHA256, digest_file, require

OLD_CONTROL = "6cd58595cfc414e7bc1490621d33e8e1fc89cf18"
OLD_CANDIDATE = "546f582f5cfeaa0ce0331ad3f4739797c4834f46"
PLAN_SHA = "053526244a14f4218e17992611bc48a76a1330d6263ca797aecb0614dd5ff22a"
LEDGER_SHA = "189225def280d5b0338fbfe7e88fd0451eb330be1069c300058429973224e793"
PENDING_SHA = "c9e1940e9cc94175195c58861b126f53d996ea2791e703bc9447e0197e27f823"
PENDING_QID = "25e5aa4f"
PREFIX_FILES = ("longmemeval_results.jsonl", "longmemeval_checkpoint.json", "run_manifest.json")
PREFIX_HASHES = (
    "b6d8b35ad9700ed7dc976a5ad82520e7bb5b1ff701e4115c2bc7e8b5601a0fc5",
    "d12eaf7c4daa56a192c554d0f5cc0138e32e1e5f6a52ddfd9dfc0f2be7163c9f",
    "14a9ec65e57e090f2b6b44800d365cc4b9f49ffe3bd8d8a443c6969910512f0b",
)
ALLOCATED = Decimal("3.251061")
POLICY = {
    "maximum_attempts": 3,
    "backoff_seconds": [30, 60],
    "maximum_wait_seconds": 120,
    "minimum_start_interval_seconds": 5,
    "retryable_status": 429,
}
BRIDGE_FILES = (
    *ancestor.BRIDGE_FILES,
    "orchestrator/eval/campaign_retry.py",
    "orchestrator/eval/campaign_restore.py",
    "orchestrator/eval/fact_harness.py",
)


def enabled(campaign: Campaign | None) -> bool:
    return campaign is not None and "retry_recovery_lineage" in campaign.plan["pins"]


def retry_capable(plan_or_campaign: Campaign | CampaignPlan | Mapping[str, Any] | None) -> bool:
    """Transport capability only; enabled() deliberately remains attach-only."""
    if plan_or_campaign is None:
        return False
    if isinstance(plan_or_campaign, Mapping):
        pins = plan_or_campaign["pins"]
    elif isinstance(plan_or_campaign, CampaignPlan):
        pins = plan_or_campaign.pins
    else:
        pins = plan_or_campaign.plan["pins"]
    return "retry_recovery_lineage" in pins or "fresh_campaign" in pins


def validate_mode(pins: Mapping[str, Any]) -> None:
    require(
        set(pins.get("retry_recovery_lineage", {})) == {"previous_plan", "stopped_ledger"}
        and pins.get("retry_policy") == POLICY
        and set(pins.get("retry_recovery", {}))
        == {"evidence_sha256", "source_bridge", "inherited"},
        "Exact retry recovery lineage/policy required",
    )
    ancestor.validate_mode(pins)


def source_bridge(new: str) -> dict[str, Any]:
    from orchestrator.eval.campaign_launch import git_bytes

    git_bytes(OLD_CONTROL, BRIDGE_FILES[0])
    git_bytes(new, BRIDGE_FILES[0])
    paths = (
        ancestor._git(
            "diff", "--no-ext-diff", "--name-only", OLD_CONTROL, new, "--", *ancestor.RUNTIME_PATHS
        )
        .decode()
        .splitlines()
    )
    require(set(paths) <= set(BRIDGE_FILES), "Retry bridge changes other runtime behavior")
    files = {}
    for path in BRIDGE_FILES:
        exists = bool(ancestor._git("ls-tree", "--name-only", OLD_CONTROL, "--", path))
        require(exists or path.endswith("/campaign_retry.py"), "Missing retry bridge source")
        files[path] = {
            "before": hashlib.sha256(git_bytes(OLD_CONTROL, path)).hexdigest() if exists else None,
            "after": hashlib.sha256(git_bytes(new, path)).hexdigest(),
        }
    diff = ancestor._git(
        "diff", "--no-ext-diff", "--binary", OLD_CONTROL, new, "--", *ancestor.RUNTIME_PATHS
    )
    return {
        "old_control": OLD_CONTROL,
        "new_control": new,
        "files": files,
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
    }


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def inspect_lineage(lineage: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from orchestrator.eval import campaign_launch as launch

    require(set(lineage) == {"previous_plan", "stopped_ledger"}, "Unknown retry ancestor")
    require(
        lineage["previous_plan"]["sha256"] == PLAN_SHA
        and lineage["stopped_ledger"]["sha256"] == LEDGER_SHA,
        "Unapproved retry ancestors",
    )
    plan_path, previous = ancestor.read_pin(lineage["previous_plan"])
    ledger_path, stopped = ancestor.read_pin(lineage["stopped_ledger"])
    require("retry_recovery_lineage" not in previous["pins"], "Recursive retry recovery refused")
    require(
        json.loads(_json(launch.load_plan(plan_path).payload())) == previous,
        "Retry ancestor plan differs",
    )
    source = ancestor.direct(ledger_path.parent, plan_path.parent)
    require(ledger_path.name == "campaign.json", "Retry ledger is not canonical")
    inherited_a0 = ancestor.verify_recovery(previous, source)  # Old strict four-file bridge intact.
    ids = previous["question_ids"]
    require(
        previous["pins"]["arm_shas"]
        == {arm: OLD_CANDIDATE if arm == "C0" else OLD_CONTROL for arm in ARMS}
        and Decimal(previous["allocated_usd"]) == ALLOCATED
        and previous["prior_reserved_usd"] == "10.4019050"
        and previous["new_reserved_usd"] == "4.35"
        and previous["cumulative_reserved_usd"] == "14.7519050"
        and stopped["plan"] == previous
        and stopped["plan_sha256"] == _hash(previous)
        and stopped["status"] == "stopped_reconciliation_required"
        and stopped["failure_admission"] == "closed"
        and stopped["arm_index"] == 1
        and stopped["arm"] == "A1"
        and stopped["question_index"] == 48
        and stopped["question"] == ids[48] == PENDING_QID
        and stopped["stage_index"] == 3
        and len(stopped["receipts"]) == 195
        and stopped["restore_attempts"] == 1
        and stopped["restore_pending"] is None
        and len(stopped["restores"]) == len(stopped["restore_receipts"]) == 1
        and stopped["qualifications"] == [inherited_a0["qualification"]]
        and stopped["imported_a0"] == inherited_a0
        and stopped["pending"]
        == {
            "arm": "A1",
            "question_id": PENDING_QID,
            "stage": "official_judge",
            "request_sha256": PENDING_SHA,
        },
        "Stopped official-judge state differs from approved recovery",
    )
    receipt = stopped["restores"][0]
    require(
        receipt == stopped["restore_receipts"][0]
        and receipt["arm"] == "A1"
        and receipt["fingerprint"]["sha256"] == STATE_SHA256
        and receipt["script_sha256"] == previous["pins"]["restore_script"]["sha256"]
        and len(receipt["owned_pids"]) == len(set(receipt["owned_pids"])) == 10,
        "Historical A1 restore differs",
    )
    evidence = {str(plan_path): digest_file(plan_path), str(ledger_path): digest_file(ledger_path)}
    evidence.update(ancestor._receipt_manifest(source, stopped, arm_offset=1))
    partial = source / "receipt-0195.json"
    require(not partial.exists() and not partial.is_symlink(), "Unexpected pending partial receipt")
    failure_path = ancestor.direct(source / "failure-0195.json", source)
    failure = json.loads(failure_path.read_bytes())
    require(
        set(failure) == {"pending", "diagnostics"} and failure["pending"] == stopped["pending"],
        "Failure identity differs",
    )
    diagnostics = failure["diagnostics"]
    require(
        set(diagnostics) == {"classification", "cause_chain"}
        and diagnostics["classification"] == "provider_error"
        and 1 <= len(diagnostics["cause_chain"]) <= 4
        and diagnostics["cause_chain"][0]
        == {"exception_type": "RateLimitError", "http_status_code": 429},
        "Historical failure is not an explicit rate limit",
    )
    for item in diagnostics["cause_chain"]:
        require(
            set(item) == {"exception_type", "http_status_code"}
            and item["exception_type"]
            in {"RateLimitError", "APIError", "UnknownError", "APIStatusError"}
            and (
                item["http_status_code"] is None
                or type(item["http_status_code"]) is int
                and 100 <= item["http_status_code"] <= 599
            ),
            "Malformed historical diagnostics",
        )
    evidence[str(failure_path)] = digest_file(failure_path)
    prefix = ancestor.direct(source / "A1", source)
    artifacts = {}
    for name, digest in zip(PREFIX_FILES, PREFIX_HASHES, strict=True):
        path = ancestor.direct(prefix / name, prefix)
        require(digest_file(path) == digest, "Approved A1 prefix changed")
        artifacts[name] = digest
        evidence[str(path)] = digest
    rows = _rows(prefix / PREFIX_FILES[0])
    checkpoint = json.loads((prefix / PREFIX_FILES[1]).read_bytes())
    manifest = json.loads((prefix / PREFIX_FILES[2]).read_bytes())
    require(
        len(rows) == 49
        and [row["question_id"] for row in rows] == ids[:49]
        and checkpoint["phases"]["evaluate"]["results"] == {row["question_id"]: row for row in rows}
        and manifest["git"]["sha"] == OLD_CONTROL
        and manifest["git"]["dirty"] is False
        and manifest["run_id"] == checkpoint["run_id"]
        and all(row["run_id"] == checkpoint["run_id"] and not row.get("error") for row in rows)
        and manifest["baseline_qualified"] is True
        and manifest["disqualifications"] == [],
        "Inherited canonical prefix/provenance differs",
    )
    dataset = launch.load_dev(launch.DEV)
    for index, row in enumerate(rows):
        expected = dataset[index]
        require(
            row["question"] == expected["question"]
            and row["reference"] == expected["answer"]
            and row["question_type"] == expected["question_type"],
            "Inherited prefix question differs",
        )
        if index < 48:
            require(
                row["official_judge_status"] == "completed"
                and row["official_judgment"] in {"correct", "incorrect"},
                "Incomplete inherited official prefix",
            )
    last = rows[48]
    require(
        last["judgment"] == "correct"
        and last["official_judge_status"] == "failed"
        and last.get("official_judge_usage") is None
        and "official_judgment" not in last
        and bool(last["hypothesis"])
        and last["answer_usage"]
        and last["judge_usage"]
        and last["answer_fingerprint"]
        and last["judge_fingerprint"]
        and last["memory_context_sha256"]
        and last["system_prompt_sha256"],
        "Recovered q49 base/official evidence differs",
    )
    for name in ancestor.A0_FILES:
        path = ancestor.direct(source / "A0" / name, source / "A0")
        evidence[str(path)] = inherited_a0["qualification"]["artifacts"][name]
    inherited = {
        "source_directory": str(source),
        "source_ledger": dict(lineage["stopped_ledger"]),
        "a0": inherited_a0,
        "prefix_artifacts": artifacts,
        "run_id": checkpoint["run_id"],
        "pending": stopped["pending"],
        "restore": receipt,
    }
    return previous, {"evidence_sha256": evidence, "inherited": inherited}


def verify_recovery(payload: Mapping[str, Any], directory: Path | None = None) -> dict[str, Any]:
    from orchestrator.eval.campaign_launch import verify_common

    pins = payload["pins"]
    validate_mode(pins)
    previous, metadata = inspect_lineage(pins["retry_recovery_lineage"])
    for key in ("evidence_sha256", "inherited"):
        require(pins["retry_recovery"][key] == metadata[key], "Retry evidence manifest changed")
    require(
        payload["question_ids"] == previous["question_ids"]
        and payload["input_limits"] == previous["input_limits"]
        and Decimal(payload["allocated_usd"]) == ALLOCATED,
        "Retry allocation changed",
    )
    for key, value in previous["pins"].items():
        if key not in {"common_files", "arm_shas"}:
            require(pins.get(key) == value, "Retry inherited pins changed")
    bridge = source_bridge(pins["arm_shas"]["A0"])
    require(bridge == pins["retry_recovery"]["source_bridge"], "Retry source bridge changed")
    require(
        pins["arm_shas"]
        == {arm: pins["arm_shas"]["C0"] if arm == "C0" else bridge["new_control"] for arm in ARMS}
        and verify_common(bridge["new_control"], pins["arm_shas"]["C0"]) == pins["common_files"],
        "Retry comparison source changed",
    )
    inherited = metadata["inherited"]
    if directory is not None:
        source = Path(inherited["source_directory"])
        require(
            directory.is_absolute()
            and directory == source.with_name(source.name + "-retry")
            and not directory.is_symlink(),
            "Retry requires fixed successor directory",
        )
        ancestor.direct(directory.parent)
        if directory.exists():
            verify_imported(directory, inherited)
    return inherited


async def prepare(args: argparse.Namespace) -> None:
    from orchestrator.eval import campaign_launch as launch

    expected = args.previous_plan.with_name(
        args.previous_plan.stem + "-retry" + args.previous_plan.suffix
    )
    require(
        args.output == expected and not args.output.exists() and not args.output.is_symlink(),
        "Retry plan requires unused fixed successor output",
    )
    lineage = {
        name: {"path": str(getattr(args, name)), "sha256": getattr(args, name + "_sha256")}
        for name in ("previous_plan", "stopped_ledger")
    }
    previous, metadata = inspect_lineage(lineage)
    pins = json.loads(_json(previous["pins"]))
    pins.update(
        retry_recovery_lineage=lineage,
        retry_policy=POLICY,
        retry_recovery={**metadata, "source_bridge": source_bridge(args.control_sha)},
        arm_shas={arm: args.candidate_sha if arm == "C0" else args.control_sha for arm in ARMS},
        common_files=launch.verify_common(args.control_sha, args.candidate_sha),
    )
    payload = json.loads(
        _json(
            CampaignPlan(tuple(previous["question_ids"]), previous["input_limits"], pins).payload()
        )
    )
    verify_recovery(payload)
    _write(args.output, payload)


def _copy(source: Path, destination: Path, digest: str) -> None:
    ancestor.direct(source)
    require(digest_file(source) == digest, "Retry bootstrap source changed")
    with source.open("rb") as src, destination.open("xb") as dst:
        os.chmod(dst.name, 0o600)
        for block in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(block)
        dst.flush()
        os.fsync(dst.fileno())
    require(
        digest_file(source) == digest_file(destination) == digest, "Retry bootstrap copy changed"
    )
    _sync_directory(destination.parent)


def verify_imported(directory: Path, inherited: Mapping[str, Any]) -> None:
    ancestor.verify_imported(directory, inherited["a0"])
    prefix = ancestor.direct(directory / "inherited-A1", directory)
    require(
        {p.name for p in prefix.iterdir()} == set(PREFIX_FILES),
        "Retry imported prefix coverage differs",
    )
    for name, digest in inherited["prefix_artifacts"].items():
        require(
            digest_file(ancestor.direct(prefix / name, prefix)) == digest,
            "Retry imported prefix changed",
        )
    source = Path(inherited["source_directory"])
    for index in range(195):
        name = f"receipt-{index:04d}.json"
        require(
            digest_file(ancestor.direct(directory / name, directory)) == digest_file(source / name),
            "Retry imported receipt changed",
        )


def bootstrap(directory: Path, state: dict[str, Any], inherited: Mapping[str, Any]) -> None:
    source = Path(inherited["source_directory"])
    for name in ("A0", "inherited-A1", "A1"):
        (directory / name).mkdir(mode=0o700, exist_ok=False)
        _sync_directory(directory)
    for name, digest in inherited["a0"]["qualification"]["artifacts"].items():
        _copy(source / "A0" / name, directory / "A0" / name, digest)
    for name, digest in inherited["prefix_artifacts"].items():
        _copy(source / "A1" / name, directory / "inherited-A1" / name, digest)
    stopped = json.loads((source / "campaign.json").read_bytes())
    for index in range(195):
        name = f"receipt-{index:04d}.json"
        _copy(source / name, directory / name, digest_file(source / name))
    state.update(
        arm_index=1,
        arm="A1",
        question_index=48,
        question=state["plan"]["question_ids"][48],
        stage_index=3,
        pending=None,
        historical_pending=dict(inherited["pending"]),
        receipts=stopped["receipts"],
        restore_attempts=1,
        restores=[inherited["restore"]],
        restore_receipts=[inherited["restore"]],
        restore_pending=None,
        imported_restore={
            "source_ledger": inherited["source_ledger"],
            "receipt": inherited["restore"],
        },
        imported_prefix=dict(inherited),
        imported_a0=inherited["a0"],
        qualifications=[inherited["a0"]["qualification"]],
        attach_pending=None,
        retry_reservations=[],
        attempts=[
            {
                "identity": inherited["pending"],
                "receipt_index": 195,
                "ordinal": 1,
                "status": "failed_429",
                "reserved_usd": "0",
                "historical": True,
                "next_wait_seconds": 30,
                "failure": {
                    "path": str(source / "failure-0195.json"),
                    "sha256": digest_file(source / "failure-0195.json"),
                },
            }
        ],
    )
    verify_imported(directory, inherited)


def validate_attempts(campaign: Campaign, *, settled: bool = False) -> None:
    if "fresh_campaign" in campaign.plan["pins"]:
        from orchestrator.eval.campaign_fresh import validate_fresh_attempt_ledger

        validate_fresh_attempt_ledger(
            campaign.state, campaign.plan, campaign.directory, settled=settled
        )
        return
    validate_attempt_ledger(campaign.state, campaign.plan, campaign.directory, settled=settled)


def validate_attempt_ledger(
    state: Mapping[str, Any], plan: Mapping[str, Any], directory: Path, *, settled: bool = False
) -> None:
    total = Decimal(0)
    reservations = []
    groups: dict[str, list[Mapping[str, Any]]] = {}
    completed_indices = []
    failure_paths = set()
    inherited = plan["pins"]["retry_recovery"]["inherited"]
    resolution = None
    if "final_verdict_recovery_lineage" in plan["pins"]:
        from orchestrator.eval.campaign_final_verdict import validate_import_resolution

        resolution = validate_import_resolution(state, plan, directory)
        total += Decimal(resolution["reserved_usd"])
        completed_indices.append(199)
    require(
        bool(state["attempts"])
        and state["attempts"][0]["identity"] == inherited["pending"]
        and state["attempts"][0].get("historical") is True
        and state["attempts"][0]["status"] == "failed_429"
        and state["attempts"][0]["receipt_index"] == 195,
        "Historical failed attempt cannot be reset",
    )
    for item in state["attempts"]:
        identity = item["identity"]
        key = _hash(identity)
        group = groups.setdefault(key, [])
        require(item["ordinal"] == len(group) + 1 <= 3, "Retry attempt ordinal/reset differs")
        require(
            identity["stage"] in {"answer", "judge", "official_judge"}
            and identity["arm"] in {"A1", "A2", "C0"}
            and identity["question_id"] in plan["question_ids"],
            "Retry attempt identity differs",
        )
        amount = Decimal(item["reserved_usd"])
        bound = (
            plan["input_limits"][identity["question_id"]][identity["stage"]] * INPUT_RATE
            + CAPS[identity["stage"]] * OUTPUT_RATE
        )
        require(
            amount == (0 if item["ordinal"] == 1 else bound),
            "Retry reservation differs from full bound",
        )
        if item["ordinal"] > 1:
            total += amount
            reservations.append(
                {"identity_sha256": key, "ordinal": item["ordinal"], "usd": str(amount)}
            )
            require(group[-1]["status"] == "failed_429", "Retry follows non-rate-limit outcome")
        require(
            item["status"]
            in {
                "failed_429",
                "terminal_failed",
                "reserved_waiting",
                "dispatch_pending",
                "completed",
            },
            "Unknown retry attempt state",
        )
        require(
            type(item["receipt_index"]) is int and item["receipt_index"] >= 195,
            "Retry receipt index differs",
        )
        if group:
            require(
                item["receipt_index"] == group[0]["receipt_index"], "Retry receipt index changed"
            )
        if item["status"] == "completed":
            completed_indices.append(item["receipt_index"])
            entry = state["receipts"][item["receipt_index"]]
            require(
                {key: value for key, value in entry.items() if key != "artifact"} == identity
                and entry["artifact"] == item["receipt"],
                "Completed attempt receipt differs",
            )
        if item["status"] == "failed_429":
            wait = item.get("next_wait_seconds")
            minimum = 30 if item["ordinal"] == 1 else 60
            require(
                type(wait) in (int, float) and minimum <= wait <= 120,
                "Retry wait is missing or below approved backoff",
            )
        if "failure" in item:
            parent = Path(inherited["source_directory"]) if item.get("historical") else directory
            path, failure = ancestor.read_pin(item["failure"], parent)
            require(failure["pending"] == identity, "Retry failure identity differs")
            if not item.get("historical"):
                failure_paths.add(path)
                require(
                    path.name
                    == f"failure-{item['receipt_index']:04d}-attempt-{item['ordinal']:02d}.json"
                    and failure["ordinal"] == item["ordinal"],
                    "Retry failure ordinal differs",
                )
            else:
                require(
                    path.name == "failure-0195.json" and item["ordinal"] == 1,
                    "Historical retry failure provenance differs",
                )
            if item["status"] == "failed_429":
                require(
                    failure["diagnostics"]["cause_chain"][0]
                    == {"exception_type": "RateLimitError", "http_status_code": 429},
                    "Retry failure is not a rate limit",
                )
        group.append(item)
    expected_completed = {
        index
        for index, entry in enumerate(state["receipts"])
        if index >= 195 and entry["stage"] != "embedding"
    }
    require(
        len(completed_indices) == len(set(completed_indices))
        and set(completed_indices) == expected_completed,
        "Retry attempt ledger omits or duplicates completed transport stages",
    )
    require(
        set(directory.glob("failure-*-attempt-*.json")) == failure_paths,
        "Retry attempt failure evidence omitted from ledger",
    )
    require(
        reservations == state["retry_reservations"]
        and total.is_finite()
        and total <= Decimal(plan["new_reserved_usd"]) - Decimal(plan["allocated_usd"]),
        "Retry reservations exceed unchanged envelope headroom",
    )
    if settled:
        require(
            all(
                group[-1]["status"] == "completed"
                or (
                    resolution is not None
                    and group[-1]["identity"] == resolution["identity"]
                    and group[-1]["ordinal"] == 3
                    and group[-1]["status"] == "terminal_failed"
                )
                for group in groups.values()
            ),
            "Unresolved retry attempt at handoff",
        )
    # Changing a request hash must never start a second allowance for one logical stage.
    logical = [
        (g[0]["identity"]["arm"], g[0]["identity"]["question_id"], g[0]["identity"]["stage"])
        for g in groups.values()
    ]
    require(len(logical) == len(set(logical)), "Retry logical identity/hash changed")


def _identity(campaign: Campaign, stage: str, request: Mapping[str, Any]) -> dict[str, Any]:
    campaign._check()
    pending = campaign.state["pending"]
    require(
        pending and pending["stage"] == stage and pending["request_sha256"] == _hash(request),
        "Retry prepared request changed",
    )
    return dict(pending)


async def prepare_attempt(campaign: Campaign, stage: str, request: Mapping[str, Any]) -> None:
    require(retry_capable(campaign), "Retry transport is not explicitly enabled")
    identity = _identity(campaign, stage, request)
    state = campaign.state
    validate_attempts(campaign)
    matching = [item for item in state["attempts"] if item["identity"] == identity]
    ordinal = len(matching) + 1
    require(
        ordinal <= 3 and (not matching or matching[-1]["status"] == "failed_429"),
        "Retry attempt repeated or exhausted",
    )
    amount = (
        Decimal(0)
        if ordinal == 1
        else (
            campaign.plan["input_limits"][identity["question_id"]][stage] * INPUT_RATE
            + CAPS[stage] * OUTPUT_RATE
        )
    )
    already = sum((Decimal(item["usd"]) for item in state["retry_reservations"]), Decimal(0))
    if "final_verdict_recovery_lineage" in campaign.plan["pins"]:
        from orchestrator.eval.campaign_final_verdict import validate_import_resolution

        imported = validate_import_resolution(state, campaign.plan, campaign.directory)
        already += Decimal(imported["reserved_usd"])
    budget = Decimal(campaign.plan["new_reserved_usd"]) - Decimal(campaign.plan["allocated_usd"])
    if "fresh_campaign" in campaign.plan["pins"]:
        from orchestrator.eval.campaign_fresh import retry_budget

        budget = retry_budget(campaign.plan)
        logical = (identity["arm"], identity["question_id"], identity["stage"])
        require(
            all(
                (
                    item["identity"]["arm"],
                    item["identity"]["question_id"],
                    item["identity"]["stage"],
                )
                != logical
                or item["identity"] == identity
                for item in state["attempts"]
            ),
            "Fresh retry logical identity/hash changed",
        )
    require(
        already + amount <= budget,
        "Retry reservations exceed unchanged envelope headroom",
    )
    item = {
        "identity": identity,
        "receipt_index": len(state["receipts"]),
        "ordinal": ordinal,
        "status": "reserved_waiting",
        "reserved_usd": str(amount),
    }
    state["attempts"].append(item)
    if ordinal > 1:
        state["retry_reservations"].append(
            {"identity_sha256": _hash(identity), "ordinal": ordinal, "usd": str(amount)}
        )
    validate_attempts(campaign)
    campaign._save()  # Reservation survives sleep, failure, cancellation and success.
    delay = matching[-1]["next_wait_seconds"] if matching else 0
    require(type(delay) in (int, float) and 0 <= delay <= 120, "Invalid persisted retry wait")
    if delay:
        await asyncio.sleep(delay)
    while True:
        campaign._check()
        require(
            _identity(campaign, stage, request) == identity, "Retry identity changed while waiting"
        )
        remaining = 5 - (time.monotonic() - campaign._last_llm_start)
        if remaining <= 0:
            break
        await asyncio.sleep(remaining)
    item["status"] = "dispatch_pending"
    campaign._save()  # Before every actual transport call, including attempt one.
    campaign._last_llm_start = time.monotonic()


def retry_delay(exc: BaseException, ordinal: int, *, now: datetime | None = None) -> float | None:
    fields = BaseException.__getattribute__(exc, "__dict__")
    if (
        type(exc).__name__ != "RateLimitError"
        or type(fields.get("status_code")) is not int
        or fields["status_code"] != 429
    ):
        return None
    if ordinal >= 3:
        return None
    seen = set()
    current: BaseException | None = exc
    for _ in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        data = BaseException.__getattribute__(current, "__dict__")
        args = BaseException.__getattribute__(current, "args")
        if len(args) > 4:
            return None
        values = (*args, data.get("message"), data.get("code"))
        if any(type(value) is str and len(value) > 8192 for value in values):
            return None
        markers = (
            "quota",
            "exhaust",
            "billing",
            "insufficient",
            "credit",
            "spending limit",
            "daily limit",
            "monthly limit",
        )
        if any(
            type(value) is str and any(marker in value.lower() for marker in markers)
            for value in values
        ):
            return None
        current = BaseException.__getattribute__(current, "__cause__")
    headers = fields.get("headers")
    if headers is None and fields.get("response") is not None:
        response = fields["response"]
        if type(response) is not httpx.Response:
            return None
        headers = response.__dict__.get("headers")
    # Read one allowlisted header only; never serialize headers or a response object.
    if headers is None:
        header = None
    elif type(headers) is dict:
        matches = [
            value
            for key, value in headers.items()
            if type(key) is str and key.lower() == "retry-after"
        ]
        if len(matches) > 1:
            return None
        header = matches[0] if matches else None
    elif isinstance(headers, httpx.Headers):
        header = headers.get("retry-after")
    else:
        return None
    wait = float((30, 60)[ordinal - 1])
    if header is not None:
        if type(header) is not str or not header or header != header.strip():
            return None
        if header.isascii() and header.isdigit():
            try:
                seconds = float(int(header))
            except (ValueError, OverflowError):
                return None
        else:
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    return None
                seconds = max(0.0, (date - (now or datetime.now(UTC))).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return None
        wait = max(wait, seconds)
    return wait if wait <= 120 else None


def failed_attempt(
    campaign: Campaign, stage: str, request: Mapping[str, Any], exc: BaseException
) -> bool:
    require(retry_capable(campaign), "Retry transport is not explicitly enabled")
    identity = _identity(campaign, stage, request)
    item = campaign.state["attempts"][-1]
    require(
        item["identity"] == identity and item["status"] == "dispatch_pending",
        "Failure has no dispatched attempt",
    )
    delay = retry_delay(exc, item["ordinal"])
    path = (
        campaign.directory
        / f"failure-{item['receipt_index']:04d}-attempt-{item['ordinal']:02d}.json"
    )
    require(not path.exists() and not path.is_symlink(), "Attempt failure artifact already exists")
    _write(
        path,
        {
            "pending": identity,
            "ordinal": item["ordinal"],
            "diagnostics": ancestor.failure_diagnostics(exc),
        },
    )
    item.update(
        status="failed_429" if delay is not None else "terminal_failed",
        failure={"path": str(path), "sha256": digest_file(path)},
    )
    if delay is not None:
        item["next_wait_seconds"] = delay
    campaign._save()
    return delay is not None


def complete_attempt(campaign: Campaign, stage: str, request: Mapping[str, Any]) -> None:
    require(retry_capable(campaign), "Retry transport is not explicitly enabled")
    item = campaign.state["attempts"][-1]
    require(
        item["status"] == "dispatch_pending"
        and item["identity"]["stage"] == stage
        and item["identity"]["request_sha256"] == _hash(request)
        and item["receipt_index"] == len(campaign.state["receipts"]) - 1,
        "Success has no dispatched retry attempt",
    )
    item.update(status="completed", receipt=campaign.state["receipts"][-1]["artifact"])
    if "fresh_campaign" in campaign.plan["pins"]:
        validate_attempts(campaign)
    campaign._save()


def validate_attribution(
    state: Mapping[str, Any], plan: Mapping[str, Any], *, attached: bool = True
) -> None:
    inherited = plan["pins"]["retry_recovery"]["inherited"]
    attributed = {"source_ledger": inherited["source_ledger"], "receipt": inherited["restore"]}
    require(
        state["imported_prefix"] == inherited
        and state["imported_restore"] == attributed
        and state["restores"][0] == inherited["restore"]
        and state["restores"] == state["restore_receipts"]
        and len(state["restores"]) == state["restore_attempts"]
        and 1 <= state["restore_attempts"] <= 3,
        "Retry restore attribution changed",
    )
    for index, receipt in enumerate(state["restores"]):
        require(
            receipt["arm"] == ARMS[index + 1]
            and receipt["fingerprint"]["sha256"] == STATE_SHA256
            and receipt["script_sha256"] == plan["pins"]["restore_script"]["sha256"]
            and len(receipt["owned_pids"]) == len(set(receipt["owned_pids"])) == 10,
            "Retry verified restore receipt differs",
        )
    if attached:
        receipt = state.get("attach_receipt", {})
        require(
            state.get("attach_pending") is None
            and receipt.get("inherited_restore") == attributed
            and receipt["fingerprint"]["sha256"] == STATE_SHA256
            and len(receipt["owned_pids"]) == len(set(receipt["owned_pids"])) == 10
            and receipt["identity"]["oid"] == plan["pins"]["database_oid"]
            and receipt["identity"]["system_identifier"] == plan["pins"]["server_identifier"],
            "Retry verified attach attribution differs",
        )


def before_restore(campaign: Campaign) -> None:
    validate_attribution(campaign.state, campaign.plan)
    validate_attempts(campaign, settled=True)


def validate_handoff(
    directory: Path, state: Mapping[str, Any], inherited: Mapping[str, Any]
) -> None:
    index = state["arm_index"]
    require(
        1 < index < 4
        and state["imported_prefix"] == inherited
        and state["restore_attempts"] == index - 1
        and len(state["restores"]) == len(state["restore_receipts"]) == index - 1
        and len(state["receipts"]) == (index - 1) * 200
        and len(state["qualifications"]) == index
        and state["qualifications"][0] == inherited["a0"]["qualification"]
        and state.get("attach_receipt")
        and state.get("attach_pending") is None,
        "Retry handoff state/restore attribution differs",
    )
    verify_imported(directory, inherited)
    ancestor._receipt_manifest(directory, state, arm_offset=1)
    validate_attribution(state, state["plan"])
    validate_attempt_ledger(state, state["plan"], directory, settled=True)


def inherited_evaluation(campaign: Campaign) -> dict[str, Any]:
    inherited = campaign.plan["pins"]["retry_recovery"]["inherited"]
    return {
        "source_ledger": inherited["source_ledger"],
        "source_sha": OLD_CONTROL,
        "run_id": inherited["run_id"],
        "artifacts": inherited["prefix_artifacts"],
        "retry_policy": POLICY,
        "protocol": "explicit-official-only-recovery-v1",
    }


def seed_runner(campaign: Campaign, checkpoint: dict[str, Any], manifest: dict[str, Any]) -> None:
    require(
        campaign.state["arm"] == "A1"
        and campaign.state["question_index"] == 48
        and campaign.state.get("attach_receipt")
        and not checkpoint["phases"]["evaluate"]["results"],
        "Retry runner requires verified attach and fresh evaluation",
    )
    verify_imported(campaign.directory, campaign.state["imported_prefix"])
    rows = _rows(campaign.directory / "inherited-A1" / PREFIX_FILES[0])
    checkpoint["phases"]["evaluate"]["results"] = {row["question_id"]: row for row in rows}
    checkpoint["inherited_evaluation"] = inherited_evaluation(campaign)
    manifest["inherited_evaluation"] = inherited_evaluation(campaign)


def write_recovery_results(campaign: Campaign, path: Path, rows: list[dict[str, Any]]) -> None:
    """Retain exact original first-48 JSONL bytes, not merely equivalent mappings."""
    from orchestrator.eval.durable_io import atomic_write_text

    source = campaign.directory / "inherited-A1" / PREFIX_FILES[0]
    lines = source.read_bytes().splitlines(keepends=True)
    require(
        len(lines) == 49 and all(line.endswith(b"\n") for line in lines),
        "Inherited prefix lines are not complete",
    )
    require(
        rows[:48] == [json.loads(line) for line in lines[:48]],
        "Inherited prefix changed before write",
    )
    suffix = "".join(json.dumps(row) + "\n" for row in rows[48:]).encode()
    atomic_write_text(path, (b"".join(lines[:48]) + suffix).decode())


async def recover_official(campaign: Campaign, previous: Mapping[str, Any]) -> dict[str, Any]:
    from tests.longmemeval.evaluate import judge_answer_official

    require(
        campaign.state["question"] == previous["question_id"] == PENDING_QID
        and campaign.state["stage_index"] == 3,
        "Wrong official-only recovery question",
    )
    result = json.loads(_json(previous))
    official = await judge_answer_official(
        previous["question"],
        previous["hypothesis"],
        previous["reference"],
        question_id=previous["question_id"],
        question_type=previous["question_type"],
        benchmark_mode=True,
    )
    require(
        set(official)
        <= {
            "official_judgment",
            "official_judge_raw_output",
            "official_judge_finish_reason",
            "official_judge_model",
            "official_judge_fingerprint",
            "official_judge_usage",
        }
        and official.get("official_judgment") in {"correct", "incorrect"},
        "Invalid recovered official verdict",
    )
    result.pop("official_judge_failure_diagnostics", None)
    result.update(
        official,
        official_judge_status="completed",
        official_completion_provenance={
            "executor_plan_sha256": campaign._plan_hash,
            "inherited_evaluation": inherited_evaluation(campaign),
        },
    )
    return result


def validate_prefix(
    campaign: Campaign, rows: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> None:
    original = _rows(campaign.directory / "inherited-A1" / PREFIX_FILES[0])
    require(len(rows) == 50 and rows[:48] == original[:48], "Completed inherited prefix changed")
    source_bytes = (
        (campaign.directory / "inherited-A1" / PREFIX_FILES[0])
        .read_bytes()
        .splitlines(keepends=True)
    )
    result_bytes = (
        (campaign.directory / "A1" / PREFIX_FILES[0]).read_bytes().splitlines(keepends=True)
    )
    require(result_bytes[:48] == source_bytes[:48], "Inherited prefix JSONL bytes changed")

    def base(row: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if not key.startswith("official_")}

    require(
        base(rows[48]) == base(original[48])
        and rows[48]["official_judge_status"] == "completed"
        and rows[48]["official_judgment"] in {"correct", "incorrect"}
        and rows[48].get("official_completion_provenance")
        == {
            "executor_plan_sha256": campaign._plan_hash,
            "inherited_evaluation": inherited_evaluation(campaign),
        }
        and manifest.get("inherited_evaluation") == inherited_evaluation(campaign)
        and rows[49]["run_id"] == manifest["run_id"],
        "Mixed-source recovery provenance differs",
    )


def validate_completion(campaign: Campaign) -> None:
    from orchestrator.eval import campaign_guard

    inherited = campaign.plan["pins"]["retry_recovery"]["inherited"]
    verify_imported(campaign.directory, inherited)
    validate_attempts(campaign, settled=True)
    validate_attribution(campaign.state, campaign.plan)
    require(
        campaign.state.get("attach_receipt")
        and campaign.state.get("attach_pending") is None
        and campaign.state["imported_restore"]
        == {"source_ledger": inherited["source_ledger"], "receipt": inherited["restore"]},
        "Retry lacks verified attach/restore attribution",
    )
    ancestor._receipt_manifest(campaign.directory, campaign.state, arm_offset=1)
    if campaign.state["arm"] == "A1":
        output = campaign.directory / "A1"
        validate_prefix(
            campaign,
            _rows(output / PREFIX_FILES[0]),
            json.loads((output / "run_manifest.json").read_bytes()),
        )
        require(len(campaign.state["receipts"]) == 200, "Recovery lacks five new completed stages")
    require(
        campaign.plan["llm_start_interval_seconds"] == campaign_guard.LLM_START_INTERVAL,
        "Retry pacing pin differs",
    )
