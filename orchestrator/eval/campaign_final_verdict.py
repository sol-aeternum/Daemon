"""One exact completed-attempt-04 import, not another inference or generic resume.

The original stopped ledger and terminal third attempt remain immutable. Imported
normalized evidence is explicitly not a raw provider/HTTP response. All paths use
the existing cooperative same-UID evidence/lock trust model.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from orchestrator.eval import campaign_recovery as ancestor, campaign_retry as retry
from orchestrator.eval.campaign_guard import (
    ARMS,
    CAPS,
    INPUT_RATE,
    LLM_MODEL,
    OUTPUT_RATE,
    Campaign,
    CampaignPlan,
    _hash,
    _integer,
    _json,
    _sync_directory,
    _write,
)
from orchestrator.eval.campaign_validation import STATE_SHA256, digest_file, require
from tests.longmemeval.official_judge import official_label

OLD_CONTROL = "cf7757ec2b672d46e5665b38abaa7a0c26ae727e"
OLD_CANDIDATE = "8a8896389d2a5975b4e26416cb3268eee2f257d8"
PLAN_SHA = "37aa032cd31492c55116cdb1823a50baba5afaa865ecc4e4277596411898c593"
LEDGER_SHA = "5a9a963ee64a1b461c49a5f8b043d7fa885fed99cec576caf09f9f2bc81d96b7"
ATTEMPT_SHA = "fae7895bbe2ecab5eafbc35609ff01d5f43303a7a72066eca41a6a4a5fa193ad"
RESULT_SHA = "25b88af23448e2b6a9e6e1a81379b5d3c5d2113970894aac59224d416fc4eece"
ROWS_SHA = "3957d174873a3ffd035cde09ab634d7eb40e9493810d8a2aae167d28381bae5a"
PARENT_RECEIPTS_SHA = "ead797455e493f38f4ad4b1d9450254751f74c2be6b7b934a4db3fdde3fa4a91"
PENDING_SHA = "499510f0a7e9d7653f932d67b67bfe00de8f5e21930f76f614af0ce44e73bf2c"
PENDING_QID = "7a8d0b71"
PLAN_NAME = "campaign-plan-replacement-20261030-recovery-retry.json"
DIRECTORY_NAME = "campaign-final-20261029-replacement-recovery-retry"
EXTRA_HOLD = Decimal("0.0025875")
OLD_RETRY_HOLD = Decimal("0.0226600")
INGEST_COUNT = 2126
RESULT_KEYS = {
    "official_judgment",
    "official_judge_raw_output",
    "official_judge_finish_reason",
    "official_judge_model",
    "official_judge_fingerprint",
    "official_judge_usage",
}
ATTEMPT_KEYS = {
    "parent_ledger_sha256",
    "plan_sha256",
    "source_sha",
    "request_sha256",
    "ordinal",
    "maximum_new_calls",
    "reserved_usd",
    "cumulative_retry_reserved_usd",
    "source_rows_sha256",
    "parent_receipts_sha256",
    "total_reservations_usd",
    "status",
    "started_at",
    "completed_at",
    "result_sha256",
}
BRIDGE_FILES = (
    "orchestrator/eval/campaign_guard.py",
    "orchestrator/eval/campaign_launch.py",
    "orchestrator/eval/campaign_retry.py",
    "orchestrator/eval/campaign_restore.py",
    "orchestrator/eval/fact_harness.py",
    "orchestrator/eval/campaign_final_verdict.py",
)


def enabled(pins: Mapping[str, Any]) -> bool:
    return "final_verdict_recovery_lineage" in pins


def validate_mode(pins: Mapping[str, Any]) -> None:
    require(
        set(pins.get("final_verdict_recovery_lineage", {}))
        == {"previous_plan", "stopped_ledger", "attempt_record", "result"}
        and set(pins.get("final_verdict_recovery", {}))
        == {"evidence_sha256", "inherited", "source_bridge"},
        "Exact final-verdict lineage/metadata required",
    )
    retry.validate_mode(pins)


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
    require(set(paths) <= set(BRIDGE_FILES), "Final-verdict bridge changes other runtime behavior")
    files = {}
    for path in BRIDGE_FILES:
        exists = bool(ancestor._git("ls-tree", "--name-only", OLD_CONTROL, "--", path))
        require(
            exists or path.endswith("/campaign_final_verdict.py"), "Missing final bridge source"
        )
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


def validate_result(result: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    require(set(result) == RESULT_KEYS, "Unknown normalized official-result fields")
    raw = result["official_judge_raw_output"]
    require(
        isinstance(raw, str)
        and bool(raw.strip())
        and result["official_judgment"] == ("correct" if official_label(raw) else "incorrect")
        and result["official_judge_finish_reason"] == "stop"
        and result["official_judge_model"] in {LLM_MODEL, LLM_MODEL.removeprefix("openrouter/")}
        and isinstance(result["official_judge_fingerprint"], str)
        and bool(result["official_judge_fingerprint"]),
        "Normalized official verdict/model/finish evidence differs",
    )
    usage = result["official_judge_usage"]
    require(
        isinstance(usage, dict)
        and {"prompt_tokens", "completion_tokens", "total_tokens"} <= set(usage)
        and set(usage) <= {"prompt_tokens", "completion_tokens", "total_tokens", "cost"},
        "Invalid normalized official usage fields",
    )
    prompt = _integer(usage["prompt_tokens"], positive=True)
    completion = _integer(usage["completion_tokens"])
    require(
        _integer(usage["total_tokens"], positive=True) == prompt + completion
        and completion <= CAPS["official_judge"]
        and prompt <= plan["input_limits"][PENDING_QID]["official_judge"],
        "Normalized official usage exceeds allocation/arithmetic",
    )
    if "cost" in usage:
        require(type(usage["cost"]) in (str, int, float), "Invalid normalized cost type")
        cost = Decimal(str(usage["cost"]))
        require(
            cost.is_finite() and 0 <= cost <= prompt * INPUT_RATE + completion * OUTPUT_RATE,
            "Invalid normalized cost bound",
        )


def inspect_lineage(lineage: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from orchestrator.eval import campaign_launch as launch

    expected = {
        "previous_plan": PLAN_SHA,
        "stopped_ledger": LEDGER_SHA,
        "attempt_record": ATTEMPT_SHA,
        "result": RESULT_SHA,
    }
    require(set(lineage) == set(expected), "Unknown final-verdict ancestor")
    require(
        all(lineage[key]["sha256"] == digest for key, digest in expected.items()),
        "Unapproved final-verdict artifact hashes",
    )
    plan_path, previous = ancestor.read_pin(lineage["previous_plan"])
    require(
        plan_path.name == PLAN_NAME and not enabled(previous["pins"]),
        "Unexpected/recursive final-verdict plan",
    )
    require(
        json.loads(_json(launch.load_plan(plan_path).payload())) == previous,
        "Final-verdict parent payload differs",
    )
    ledger_path, stopped = ancestor.read_pin(lineage["stopped_ledger"])
    source = ancestor.direct(ledger_path.parent, plan_path.parent)
    require(
        source.name == DIRECTORY_NAME and ledger_path.name == "campaign.json",
        "Wrong final-verdict parent directory",
    )
    retry.verify_recovery(previous, source)  # Verify old bridges on the old payload only.
    retry.validate_attribution(stopped, previous)
    retry.validate_attempt_ledger(stopped, previous, source)
    require(
        previous["pins"]["arm_shas"]
        == {arm: OLD_CANDIDATE if arm == "C0" else OLD_CONTROL for arm in ARMS}
        and Decimal(previous["allocated_usd"]) == retry.ALLOCATED
        and previous["prior_reserved_usd"] == "10.4019050"
        and previous["new_reserved_usd"] == "4.35"
        and previous["cumulative_reserved_usd"] == "14.7519050"
        and previous["prior_restore_attempts"] == 3
        and previous["maximum_cumulative_restore_attempts"] == 6
        and stopped["plan"] == previous
        and stopped["plan_sha256"] == _hash(previous)
        and stopped["status"] == "stopped_reconciliation_required"
        and stopped["failure_admission"] == "closed"
        and stopped["attach_pending"] is None
        and stopped["arm"] == "A1"
        and stopped["arm_index"] == 1
        and stopped["question_index"] == 49
        and stopped["question"] == PENDING_QID
        and previous["question_ids"][49] == PENDING_QID
        and stopped["stage_index"] == 3
        and stopped["restore_attempts"] == 1
        and stopped["restore_pending"] is None
        and len(stopped["receipts"]) == 199
        and stopped["qualifications"] == [stopped["imported_a0"]["qualification"]]
        and stopped["pending"]
        == {
            "arm": "A1",
            "question_id": PENDING_QID,
            "stage": "official_judge",
            "request_sha256": PENDING_SHA,
        },
        "Stopped final official-judge state differs",
    )
    unresolved = [item for item in stopped["attempts"] if item["status"] == "terminal_failed"]
    require(
        len(unresolved) == 1
        and unresolved[0]["identity"] == stopped["pending"]
        and unresolved[0]["ordinal"] == 3
        and unresolved[0]["receipt_index"] == 199
        and all(
            item["status"] in {"completed", "failed_429", "terminal_failed"}
            for item in stopped["attempts"]
        )
        and len(stopped["retry_reservations"]) == 5
        and sum((Decimal(item["usd"]) for item in stopped["retry_reservations"]), Decimal(0))
        == OLD_RETRY_HOLD,
        "Historical terminal attempt/reservations differ",
    )
    require(
        [(item["receipt_index"], item["ordinal"], item["status"]) for item in stopped["attempts"]]
        == [
            (195, 1, "failed_429"),
            (195, 2, "failed_429"),
            (195, 3, "completed"),
            (197, 1, "failed_429"),
            (197, 2, "completed"),
            (198, 1, "completed"),
            (199, 1, "failed_429"),
            (199, 2, "failed_429"),
            (199, 3, "terminal_failed"),
        ],
        "Historical logical attempt sequence differs",
    )
    evidence = {str(plan_path): PLAN_SHA, str(ledger_path): LEDGER_SHA}
    evidence.update(ancestor._receipt_manifest(source, stopped, arm_offset=1))
    require(
        not (source / "receipt-0199.json").exists()
        and not (source / "receipt-0199.json").is_symlink(),
        "Unexpected partial final receipt",
    )
    for item in stopped["attempts"]:
        if "failure" in item:
            path, _ = ancestor.read_pin(item["failure"])
            evidence[str(path)] = item["failure"]["sha256"]
    for path in source.glob("failure-*.json"):
        ancestor.direct(path, source)
        require(str(path) in evidence, "Unbound parent failure artifact")
    artifacts = {}
    for name in retry.PREFIX_FILES:
        path = ancestor.direct(source / "A1" / name, source / "A1")
        artifacts[name] = digest_file(path)
        evidence[str(path)] = artifacts[name]
    require(artifacts[retry.PREFIX_FILES[0]] == ROWS_SHA, "Final A1 rows changed")
    for folder, bound in (
        ("A0", stopped["imported_a0"]["qualification"]["artifacts"]),
        ("inherited-A1", previous["pins"]["retry_recovery"]["inherited"]["prefix_artifacts"]),
    ):
        for name, digest in bound.items():
            path = ancestor.direct(source / folder / name, source / folder)
            require(digest_file(path) == digest, "Final parent inherited artifact changed")
            evidence[str(path)] = digest
    rows = retry._rows(source / "A1" / retry.PREFIX_FILES[0])
    checkpoint = json.loads((source / "A1" / retry.PREFIX_FILES[1]).read_bytes())
    manifest = json.loads((source / "A1" / retry.PREFIX_FILES[2]).read_bytes())
    require(
        len(rows) == 50
        and [row["question_id"] for row in rows] == previous["question_ids"]
        and checkpoint["phases"]["evaluate"]["results"] == {row["question_id"]: row for row in rows}
        and checkpoint["phases"]["ingest"]["status"] == "completed"
        and checkpoint["phases"]["ingest"]["completed_count"] == INGEST_COUNT
        and checkpoint["phases"]["evaluate"]["status"] == "failed"
        and checkpoint["phases"]["evaluate"]["completed_count"] == 50
        and checkpoint["phases"]["score"]["status"] == "pending"
        and manifest["phases"] == {}
        and manifest["git"]["sha"] == OLD_CONTROL
        and manifest["git"]["dirty"] is False
        and manifest["baseline_qualified"] is True
        and manifest["disqualifications"] == []
        and manifest["inherited_evaluation"]
        == {
            "source_ledger": previous["pins"]["retry_recovery"]["inherited"]["source_ledger"],
            "source_sha": retry.OLD_CONTROL,
            "run_id": previous["pins"]["retry_recovery"]["inherited"]["run_id"],
            "artifacts": previous["pins"]["retry_recovery"]["inherited"]["prefix_artifacts"],
            "retry_policy": retry.POLICY,
            "protocol": "explicit-official-only-recovery-v1",
        }
        and rows[49]["run_id"] == checkpoint["run_id"] == manifest["run_id"],
        "Failed parent evaluation artifacts/provenance differ",
    )
    original = retry._rows(source / "inherited-A1" / retry.PREFIX_FILES[0])
    require(
        rows[:48] == original[:48] and _base(rows[48]) == _base(original[48]),
        "Parent inherited q49 base/prefix changed",
    )
    dataset = launch.load_dev(launch.DEV)
    for index, row in enumerate(rows):
        require(
            row["question"] == dataset[index]["question"]
            and row["reference"] == dataset[index]["answer"]
            and row["question_type"] == dataset[index]["question_type"]
            and not row.get("error")
            and row["judgment"] in {"correct", "incorrect", "partially_correct"},
            "Final prefix row identity/base differs",
        )
        if index < 49:
            require(
                row["official_judge_status"] == "completed"
                and row["official_judgment"] in {"correct", "incorrect"},
                "Final inherited official coverage incomplete",
            )
    require(
        rows[49]["official_judge_status"] == "failed"
        and "official_judgment" not in rows[49]
        and rows[49].get("official_judge_usage") is None
        and rows[49]["hypothesis"]
        and rows[49]["answer_usage"]
        and rows[49]["judge_usage"]
        and rows[49]["answer_fingerprint"]
        and rows[49]["judge_fingerprint"]
        and rows[49]["memory_context_sha256"]
        and rows[49]["system_prompt_sha256"],
        "Final q50 base/failed official evidence differs",
    )
    standalone = source.with_name(source.name + "-official-50-attempt-04")
    ancestor.direct(standalone, source.parent)
    attempt_path, attempt = ancestor.read_pin(lineage["attempt_record"], standalone)
    result_path, result = ancestor.read_pin(lineage["result"], standalone)
    require(
        attempt_path.name == "attempt.json" and result_path.name == "official-result.json",
        "Wrong standalone completed-verdict paths",
    )
    validate_result(result, previous)
    require(set(attempt) == ATTEMPT_KEYS, "Unknown completed attempt-record fields")
    require(
        attempt["parent_ledger_sha256"] == LEDGER_SHA
        and attempt["plan_sha256"] == PLAN_SHA
        and attempt["source_sha"] == OLD_CONTROL
        and attempt["request_sha256"] == PENDING_SHA
        and type(attempt["ordinal"]) is int
        and attempt["ordinal"] == 4
        and type(attempt["maximum_new_calls"]) is int
        and attempt["maximum_new_calls"] == 1
        and attempt["status"] == "completed"
        and attempt["result_sha256"] == RESULT_SHA
        and attempt["source_rows_sha256"] == ROWS_SHA
        and attempt["parent_receipts_sha256"] == PARENT_RECEIPTS_SHA
        and Decimal(attempt["reserved_usd"]) == EXTRA_HOLD
        and EXTRA_HOLD
        == previous["input_limits"][PENDING_QID]["official_judge"] * INPUT_RATE
        + CAPS["official_judge"] * OUTPUT_RATE
        and Decimal(attempt["cumulative_retry_reserved_usd"]) == OLD_RETRY_HOLD + EXTRA_HOLD
        and Decimal(attempt["total_reservations_usd"]) == Decimal("15.751905"),
        "Completed attempt binding/reservation differs",
    )
    started, completed = (
        datetime.fromisoformat(attempt[key]) for key in ("started_at", "completed_at")
    )
    require(
        started.tzinfo is not None and completed.tzinfo is not None and completed >= started,
        "Invalid completed attempt timestamps",
    )
    evidence.update({str(attempt_path): ATTEMPT_SHA, str(result_path): RESULT_SHA})
    inherited = {
        "source_directory": str(source),
        "lineage": dict(lineage),
        "artifacts": artifacts,
        "attempts": stopped["attempts"],
        "retry_reservations": stopped["retry_reservations"],
        "receipts": stopped["receipts"],
        "pending": stopped["pending"],
        "attach_receipt": stopped["attach_receipt"],
        "result": result,
        "failure_artifacts": {
            Path(path).name: digest
            for path, digest in evidence.items()
            if Path(path).parent == source and Path(path).name.startswith("failure-")
        },
        "receipt_artifacts": {
            Path(path).name: digest
            for path, digest in evidence.items()
            if Path(path).parent == source and Path(path).name.startswith("receipt-")
        },
    }
    return previous, {"evidence_sha256": evidence, "inherited": inherited}


def _base(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("official_")}


def verify_recovery(payload: Mapping[str, Any], directory: Path | None = None) -> dict[str, Any]:
    from orchestrator.eval.campaign_launch import verify_common

    pins = payload["pins"]
    validate_mode(pins)
    previous, metadata = inspect_lineage(pins["final_verdict_recovery_lineage"])
    for key in ("evidence_sha256", "inherited"):
        require(
            pins["final_verdict_recovery"][key] == metadata[key], "Final-verdict evidence changed"
        )
    for key, value in previous["pins"].items():
        if key not in {"common_files", "arm_shas"}:
            require(pins.get(key) == value, "Final-verdict inherited pins changed")
    require(
        payload["question_ids"] == previous["question_ids"]
        and payload["input_limits"] == previous["input_limits"],
        "Final-verdict allocations changed",
    )
    bridge = source_bridge(pins["arm_shas"]["A0"])
    require(
        bridge == pins["final_verdict_recovery"]["source_bridge"]
        and pins["arm_shas"]
        == {arm: pins["arm_shas"]["C0"] if arm == "C0" else bridge["new_control"] for arm in ARMS}
        and verify_common(bridge["new_control"], pins["arm_shas"]["C0"]) == pins["common_files"],
        "Final-verdict comparison/source bridge changed",
    )
    inherited = metadata["inherited"]
    if directory is not None:
        source = Path(inherited["source_directory"])
        require(
            directory.is_absolute()
            and directory == source.with_name(source.name + "-final-verdict")
            and not directory.is_symlink(),
            "Final verdict requires fixed successor directory",
        )
        ancestor.direct(directory.parent)
        if directory.exists():
            verify_imported(directory, pins)
    return inherited


async def prepare(args: argparse.Namespace) -> None:
    from orchestrator.eval.campaign_launch import verify_common

    expected = args.previous_plan.with_name(args.previous_plan.stem + "-final-verdict.json")
    require(
        args.output == expected and not args.output.exists() and not args.output.is_symlink(),
        "Final-verdict plan requires unused fixed successor output",
    )
    lineage = {
        name: {"path": str(getattr(args, name)), "sha256": getattr(args, name + "_sha256")}
        for name in ("previous_plan", "stopped_ledger", "attempt_record", "result")
    }
    previous, metadata = inspect_lineage(lineage)
    pins = json.loads(_json(previous["pins"]))
    pins.update(
        final_verdict_recovery_lineage=lineage,
        final_verdict_recovery={**metadata, "source_bridge": source_bridge(args.control_sha)},
        common_files=verify_common(args.control_sha, args.candidate_sha),
        arm_shas={arm: args.candidate_sha if arm == "C0" else args.control_sha for arm in ARMS},
    )
    payload = json.loads(
        _json(
            CampaignPlan(tuple(previous["question_ids"]), previous["input_limits"], pins).payload()
        )
    )
    verify_recovery(payload)
    _write(args.output, payload)


def import_record(pins: Mapping[str, Any]) -> dict[str, Any]:
    inherited = pins["final_verdict_recovery"]["inherited"]
    return {
        "identity": inherited["pending"],
        "ordinal": 4,
        "reserved_usd": str(EXTRA_HOLD),
        "format": "normalized_official_result",
        "lineage": pins["final_verdict_recovery_lineage"],
        "receipt_index": 199,
    }


def normalized_receipt(pins: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": "normalized_official_result",
        "pending": pins["final_verdict_recovery"]["inherited"]["pending"],
        "normalized_result": pins["final_verdict_recovery"]["inherited"]["result"],
        "provenance": import_record(pins),
    }


def _imported_attempts(inherited: Mapping[str, Any], directory: Path) -> list[dict[str, Any]]:
    attempts = json.loads(_json(inherited["attempts"]))
    for item in attempts:
        if "failure" in item and not item.get("historical"):
            item["failure"]["path"] = str(directory / Path(item["failure"]["path"]).name)
    return attempts


def validate_import_resolution(
    state: Mapping[str, Any], plan: Mapping[str, Any], directory: Path
) -> dict[str, Any]:
    pins = plan["pins"]
    record = import_record(pins)
    inherited = pins["final_verdict_recovery"]["inherited"]
    prefix = _imported_attempts(inherited, directory)
    require(
        state["final_verdict_import"] == record
        and state["imported_retry_reservations"] == [record]
        and state["attempts"][: len(prefix)] == prefix
        and state["retry_reservations"][:5] == inherited["retry_reservations"]
        and state["receipts"][:199] == inherited["receipts"]
        and state["receipts"][199] == {**record["identity"], "artifact": "receipt-0199.json"},
        "Final-verdict import/historical attempt accounting changed",
    )
    path = ancestor.direct(directory / "receipt-0199.json", directory)
    require(
        path.read_bytes() == _json(normalized_receipt(pins)) + b"\n",
        "Normalized imported receipt changed",
    )
    return record


def verify_imported(directory: Path, pins: Mapping[str, Any]) -> None:
    inherited = pins["final_verdict_recovery"]["inherited"]
    retry.verify_imported(directory, pins["retry_recovery"]["inherited"])
    for name, digest in inherited["receipt_artifacts"].items():
        require(
            digest_file(ancestor.direct(directory / name, directory)) == digest,
            "Imported final parent receipt changed",
        )
    for folder, artifacts in (
        ("inherited-final-A1", inherited["artifacts"]),
        (
            "imported-final-verdict",
            {"attempt.json": ATTEMPT_SHA, "official-result.json": RESULT_SHA},
        ),
    ):
        parent = ancestor.direct(directory / folder, directory)
        require(
            {path.name for path in parent.iterdir()} == set(artifacts),
            "Imported final artifact coverage differs",
        )
        for name, digest in artifacts.items():
            require(
                digest_file(ancestor.direct(parent / name, parent)) == digest,
                "Imported completed-verdict evidence changed",
            )
    for name, digest in inherited["failure_artifacts"].items():
        require(
            digest_file(ancestor.direct(directory / name, directory)) == digest,
            "Imported failure evidence changed",
        )
    require(
        (directory / "receipt-0199.json").read_bytes() == _json(normalized_receipt(pins)) + b"\n",
        "Imported normalized receipt changed",
    )


def bootstrap(directory: Path, state: dict[str, Any], inherited: Mapping[str, Any]) -> None:
    pins = state["plan"]["pins"]
    source = Path(inherited["source_directory"])
    stopped = json.loads((source / "campaign.json").read_bytes())
    for name in ("A0", "inherited-A1", "inherited-final-A1", "imported-final-verdict", "A1"):
        (directory / name).mkdir(mode=0o700, exist_ok=False)
        _sync_directory(directory)
    for name, digest in stopped["imported_a0"]["qualification"]["artifacts"].items():
        retry._copy(source / "A0" / name, directory / "A0" / name, digest)
    for name, digest in pins["retry_recovery"]["inherited"]["prefix_artifacts"].items():
        retry._copy(source / "inherited-A1" / name, directory / "inherited-A1" / name, digest)
    for name, digest in inherited["artifacts"].items():
        retry._copy(source / "A1" / name, directory / "inherited-final-A1" / name, digest)
    for key, name in (("attempt_record", "attempt.json"), ("result", "official-result.json")):
        artifact = inherited["lineage"][key]
        retry._copy(
            Path(artifact["path"]), directory / "imported-final-verdict" / name, artifact["sha256"]
        )
    for name, digest in {
        **inherited["receipt_artifacts"],
        **inherited["failure_artifacts"],
    }.items():
        retry._copy(source / name, directory / name, digest)
    _write(directory / "receipt-0199.json", normalized_receipt(pins))
    for key in (
        "restores",
        "restore_receipts",
        "restore_attempts",
        "imported_restore",
        "imported_prefix",
        "imported_a0",
        "qualifications",
        "historical_pending",
    ):
        state[key] = stopped[key]
    state.update(
        arm="A1",
        arm_index=1,
        question_index=50,
        question=None,
        stage_index=0,
        pending=None,
        restore_pending=None,
        attach_pending=None,
        inherited_attach_receipt=stopped["attach_receipt"],
        attempts=_imported_attempts(inherited, directory),
        retry_reservations=stopped["retry_reservations"],
        imported_retry_reservations=[import_record(pins)],
        final_verdict_import=import_record(pins),
        receipts=[*stopped["receipts"], {**inherited["pending"], "artifact": "receipt-0199.json"}],
    )
    verify_imported(directory, pins)


def receipt_manifest(directory: Path, state: Mapping[str, Any]) -> None:
    ancestor._receipt_manifest(directory, state, arm_offset=1)
    for index, entry in enumerate(state["receipts"]):
        data = json.loads((directory / entry["artifact"]).read_bytes())
        if index == 199:
            require(
                data == normalized_receipt(state["plan"]["pins"]),
                "Normalized final receipt differs",
            )
        else:
            require(set(data) == {"pending", "response"}, "Unknown raw receipt format")


def before_attach(campaign: Campaign) -> None:
    require(
        campaign.state["question_index"] == 50
        and campaign.state["question"] is None
        and campaign.state["stage_index"] == 0
        and campaign.state["inherited_attach_receipt"]
        == campaign.plan["pins"]["final_verdict_recovery"]["inherited"]["attach_receipt"],
        "Finalization attach state differs",
    )
    retry.validate_attempts(campaign, settled=True)


def _attached(state: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    retry.validate_attribution(state, plan)
    require(
        state["attach_receipt"].get("final_verdict_recovery_lineage")
        == plan["pins"]["final_verdict_recovery_lineage"],
        "Finalization requires a fresh source-bound attach",
    )


def before_restore(campaign: Campaign) -> None:
    _attached(campaign.state, campaign.plan)
    retry.validate_attempts(campaign, settled=True)
    receipt_manifest(campaign.directory, campaign.state)


def validate_handoff(
    directory: Path, state: Mapping[str, Any], inherited: Mapping[str, Any]
) -> None:
    index = state["arm_index"]
    require(
        1 < index < 4
        and state["restore_attempts"] == index - 1
        and len(state["receipts"]) == (index - 1) * 200
        and len(state["qualifications"]) == index
        and state["question_index"] == 50
        and state["stage_index"] == 0
        and state["inherited_attach_receipt"] == inherited["attach_receipt"],
        "Final-verdict handoff state differs",
    )
    verify_imported(directory, state["plan"]["pins"])
    _attached(state, state["plan"])
    retry.validate_attempt_ledger(state, state["plan"], directory, settled=True)
    receipt_manifest(directory, state)


def provenance(campaign: Campaign) -> dict[str, Any]:
    inherited = campaign.plan["pins"]["final_verdict_recovery"]["inherited"]
    return {
        "protocol": "completed-final-verdict-import-v1",
        "source_sha": OLD_CONTROL,
        "lineage": inherited["lineage"],
        "artifacts": inherited["artifacts"],
        "historical_attach_receipt": inherited["attach_receipt"],
        "fingerprint_window": "fresh finalization only; old failed evaluation has no end fingerprint",
    }


def seed_runner(campaign: Campaign, checkpoint: dict[str, Any], manifest: dict[str, Any]) -> None:
    before_attach(campaign)
    _attached(campaign.state, campaign.plan)
    require(not checkpoint["phases"]["evaluate"]["results"], "Finalization runner must be fresh")
    rows = retry._rows(campaign.directory / "inherited-final-A1" / retry.PREFIX_FILES[0])
    rows[49] = {key: value for key, value in rows[49].items() if not key.startswith("official_")}
    rows[49].update(campaign.plan["pins"]["final_verdict_recovery"]["inherited"]["result"])
    rows[49].update(
        official_judge_status="completed",
        official_completion_provenance=import_record(campaign.plan["pins"]),
    )
    checkpoint["phases"]["evaluate"]["results"] = {row["question_id"]: row for row in rows}
    checkpoint["inherited_evaluation"] = provenance(campaign)
    manifest["inherited_evaluation"] = provenance(campaign)


def write_recovery_results(campaign: Campaign, path: Path, rows: list[dict[str, Any]]) -> None:
    from orchestrator.eval.durable_io import atomic_write_text

    lines = (
        (campaign.directory / "inherited-final-A1" / retry.PREFIX_FILES[0])
        .read_bytes()
        .splitlines(keepends=True)
    )
    require(
        len(lines) == 50
        and all(line.endswith(b"\n") for line in lines)
        and rows[:49] == [json.loads(line) for line in lines[:49]],
        "Final inherited row bytes/payload differ",
    )
    atomic_write_text(path, b"".join(lines[:49]).decode() + json.dumps(rows[49]) + "\n")


def validate_prefix(
    campaign: Campaign, rows: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> None:
    original_path = campaign.directory / "inherited-final-A1" / retry.PREFIX_FILES[0]
    original = retry._rows(original_path)
    expected = _base(original[49])
    expected.update(campaign.plan["pins"]["final_verdict_recovery"]["inherited"]["result"])
    expected.update(
        official_judge_status="completed",
        official_completion_provenance=import_record(campaign.plan["pins"]),
    )
    require(
        len(rows) == 50
        and rows[:49] == original[:49]
        and rows[49] == expected
        and manifest["inherited_evaluation"] == provenance(campaign)
        and manifest["git"]["sha"] == campaign.plan["pins"]["arm_shas"]["A1"],
        "Final inherited rows/provenance changed",
    )
    output = campaign.directory / "A1" / retry.PREFIX_FILES[0]
    require(
        output.read_bytes().splitlines(keepends=True)[:49]
        == original_path.read_bytes().splitlines(keepends=True)[:49],
        "Final first-49 JSONL bytes changed",
    )


def validate_completion(campaign: Campaign) -> None:
    before_restore(campaign)
    verify_imported(campaign.directory, campaign.plan["pins"])
    if campaign.state["arm"] == "A1":
        output = campaign.directory / "A1"
        manifest = json.loads((output / "run_manifest.json").read_bytes())
        validate_prefix(campaign, retry._rows(output / retry.PREFIX_FILES[0]), manifest)
        phase = manifest["phases"]["evaluate"]
        require(
            phase["state_matches_ingest_at_start"] is True
            and phase["state_unchanged_by_evaluate"] is True
            and phase["state_fingerprint_start"]["sha256"] == STATE_SHA256
            and phase["state_fingerprint_end"]["sha256"] == STATE_SHA256
            and phase["official_judgments"]["judged"] == 50,
            "Finalization lacks fresh complete fingerprint/official summary",
        )
