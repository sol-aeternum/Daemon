"""One explicitly approved second replacement; never a general resume mechanism.

All filesystem checks assume cooperating same-UID operators, like the campaign
lock. Partial bootstraps remain retained and permanently non-resumable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from orchestrator.eval.campaign_guard import ARMS, STAGES, _hash, _sync_directory, _write
from orchestrator.eval.campaign_validation import digest_file, qualification, require

OLD_CONTROL = "c59ea23d9c75f99ea93582703c867434147d1f4e"
OLD_CANDIDATE = "071006584f4a0ee57671abcb73a4b6df288242f9"
A0_FILES = (
    "longmemeval_results.jsonl",
    "longmemeval_checkpoint.json",
    "longmemeval_score.json",
    "run_manifest.json",
)
BRIDGE_FILES = (
    "orchestrator/eval/campaign_guard.py",
    "orchestrator/eval/campaign_launch.py",
    "orchestrator/eval/campaign_recovery.py",
    "tests/longmemeval/evaluate.py",
)
RUNTIME_PATHS = (
    "orchestrator",
    "providers",
    "config",
    "migrations",
    "tests/longmemeval",
    "tests/benchmark_longmemeval",
    "pyproject.toml",
    "uv.lock",
    ":(exclude)tests/benchmark_longmemeval/CAMPAIGN_GUARD.md",
)


def direct(path: Path, parent: Path | None = None) -> Path:
    require(path.is_absolute() and ".." not in path.parts, "Recovery path must be absolute/direct")
    require(path.resolve(strict=True) == path and not path.is_symlink(), "Recovery symlink refused")
    require(parent is None or path.parent == parent, "Recovery evidence has wrong parent")
    return path


def read_pin(pin: Mapping[str, Any], parent: Path | None = None) -> tuple[Path, dict[str, Any]]:
    require(set(pin) == {"path", "sha256"}, "Invalid recovery artifact pin")
    path = direct(Path(pin["path"]), parent)
    require(path.is_file() and digest_file(path) == pin["sha256"], "Recovery ancestor changed")
    return path, json.loads(path.read_bytes())


def validate_mode(pins: Mapping[str, Any]) -> None:
    lineage = pins.get("recovery_lineage")
    require(
        isinstance(lineage, dict)
        and set(lineage) == {"previous_replacement_plan", "stopped_a1_ledger"}
        and isinstance(pins.get("recovery"), dict)
        and set(pins["recovery"]) == {"evidence_sha256", "inherited_a0", "source_bridge"},
        "Second recovery requires exact lineage and evidence, not a scope flag",
    )


def _git(*args: str) -> bytes:
    from orchestrator.eval.campaign_launch import ROOT

    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=True, timeout=30
    ).stdout


def source_bridge(old: str, new: str) -> dict[str, Any]:
    from orchestrator.eval.campaign_launch import git_bytes

    # git_bytes validates exact SHAs before they may reach revision arguments.
    git_bytes(old, BRIDGE_FILES[0])
    git_bytes(new, BRIDGE_FILES[0])
    require(old == OLD_CONTROL, "Unapproved recovery source ancestor")
    changed = _git("diff", "--no-ext-diff", "--name-only", old, new, "--", *RUNTIME_PATHS)
    paths = changed.decode().splitlines()
    require(set(paths) <= set(BRIDGE_FILES), "Recovery bridge changes other runtime behavior")
    files = {}
    for path in BRIDGE_FILES:
        exists = bool(_git("ls-tree", "--name-only", old, "--", path))
        require(exists or path.endswith("/campaign_recovery.py"), "Missing old bridge source")
        files[path] = {
            "before": hashlib.sha256(git_bytes(old, path)).hexdigest() if exists else None,
            "after": hashlib.sha256(git_bytes(new, path)).hexdigest(),
        }
    diff = _git("diff", "--no-ext-diff", "--binary", old, new, "--", *RUNTIME_PATHS)
    return {
        "old_control": old,
        "new_control": new,
        "files": files,
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
    }


def _receipt_manifest(
    directory: Path, state: Mapping[str, Any], *, arm_offset: int = 0
) -> dict[str, str]:
    """Bind every completed receipt and any captured, still-pending response."""
    result = {}
    ids = state["plan"]["question_ids"]
    for number, entry in enumerate(state["receipts"]):
        name = f"receipt-{number:04d}.json"
        require(entry.get("artifact") == name, "Receipt path/order differs")
        path = direct(directory / name, directory)
        data = json.loads(path.read_bytes())
        pending = {key: value for key, value in entry.items() if key != "artifact"}
        arm = ARMS[number // 200 + arm_offset]
        within = number % 200
        require(
            pending.get("arm") == arm
            and pending.get("question_id") == ids[within // 4]
            and pending.get("stage") == STAGES[within % 4]
            and isinstance(pending.get("request_sha256"), str)
            and len(pending["request_sha256"]) == 64
            and data.get("pending") == pending,
            "Receipt identity differs from frozen serial execution",
        )
        result[str(path)] = digest_file(path)
    pending_path = directory / f"receipt-{len(state['receipts']):04d}.json"
    if pending_path.exists() or pending_path.is_symlink():
        direct(pending_path, directory)
        require(
            state["pending"] is not None
            and json.loads(pending_path.read_bytes()).get("pending") == state["pending"],
            "Partial pending receipt identity differs",
        )
        result[str(pending_path)] = digest_file(pending_path)
    # No unbound extra receipts may silently become ancestor evidence.
    require(
        {str(path) for path in directory.glob("receipt-*.json")} == set(result),
        "Unexpected ancestor receipt artifacts",
    )
    return result


def inspect_lineage(lineage: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from orchestrator.eval import campaign_launch as launch
    from orchestrator.eval.campaign_validation import STATE_SHA256

    require(
        set(lineage) == {"previous_replacement_plan", "stopped_a1_ledger"},
        "Invalid second recovery lineage",
    )
    plan_path, previous = read_pin(lineage["previous_replacement_plan"])
    root = plan_path.parent
    ledger_path, stopped = read_pin(lineage["stopped_a1_ledger"])
    require(ledger_path.name == "campaign.json", "Stopped ledger is not canonical")
    require("recovery_lineage" not in previous["pins"], "Third recovery is not approved")
    # Canonical old payload still uses the original four-arm accounting.
    require(
        json.loads(json.dumps(launch.load_plan(plan_path).payload())) == previous,
        "Replacement plan differs",
    )
    original_lineage = previous["pins"]["replacement_lineage"]
    launch.verify_replacement_lineage(original_lineage)
    original_plan_path, original = read_pin(original_lineage["previous_plan"], root)
    original_ledger_path, original_failed = read_pin(original_lineage["failed_ledger"])
    original_directory = direct(original_ledger_path.parent, root)
    require(original_ledger_path.name == "campaign.json", "Original ledger is not canonical")
    directory = direct(ledger_path.parent, root)
    require(
        directory == original_directory.with_name(original_directory.name + "-replacement"),
        "Stopped replacement has wrong parent/destination",
    )
    require(
        previous["pins"]["arm_shas"]
        == {arm: OLD_CANDIDATE if arm == "C0" else OLD_CONTROL for arm in ARMS},
        "Unapproved stopped replacement source",
    )
    ids = previous["question_ids"]
    require(
        stopped["plan"] == previous
        and stopped["plan_sha256"] == _hash(previous)
        and stopped["status"] == "stopped_reconciliation_required"
        and stopped["failure_admission"] == "closed"
        and stopped["arm"] == "A1"
        and stopped["arm_index"] == 1
        and stopped["question_index"] == 11
        and stopped["question"] == ids[11]
        and stopped["stage_index"] == 1
        and stopped["restore_attempts"] == 2
        and stopped["restore_pending"] is None
        and len(stopped["restore_receipts"]) == len(stopped["restores"]) == 2
        and len(stopped["receipts"]) == 245
        and stopped["pending"]["arm"] == "A1"
        and stopped["pending"]["question_id"] == ids[11]
        and stopped["pending"]["stage"] == "answer"
        and set(stopped["pending"]) == {"arm", "question_id", "stage", "request_sha256"}
        and isinstance(stopped["pending"]["request_sha256"], str)
        and len(stopped["pending"]["request_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in stopped["pending"]["request_sha256"])
        and len(stopped["qualifications"]) == 1,
        "Stopped A1 state differs; new owner decision required",
    )
    for index, receipt in enumerate(stopped["restore_receipts"]):
        require(
            receipt == stopped["restores"][index]
            and receipt["arm"] == ARMS[index]
            and receipt["fingerprint"]["sha256"] == STATE_SHA256
            and receipt["script_sha256"] == previous["pins"]["restore_script"]["sha256"]
            and len(receipt["owned_pids"]) == 10
            and len(set(receipt["owned_pids"])) == 10,
            "Replacement restore is not verified",
        )
    evidence = {
        str(path): digest_file(path)
        for path in (plan_path, ledger_path, original_plan_path, original_ledger_path)
    }
    evidence.update(_receipt_manifest(original_directory, original_failed))
    evidence.update(_receipt_manifest(directory, stopped))
    a0 = direct(directory / "A0", directory)
    artifacts = stopped["qualifications"][0]["artifacts"]
    require(set(artifacts) == set(A0_FILES), "Inherited A0 needs exactly four artifacts")
    for name in A0_FILES:
        path = direct(a0 / name, a0)
        require(digest_file(path) == artifacts[name], "Qualified A0 artifact changed")
        evidence[str(path)] = artifacts[name]
    rows = [json.loads(line) for line in (a0 / A0_FILES[0]).read_text().splitlines()]
    checkpoint, score, manifest = [json.loads((a0 / name).read_bytes()) for name in A0_FILES[1:]]
    dataset = launch.load_dev(launch.DEV)
    require([row["question_id"] for row in dataset] == ids, "Inherited dev order differs")
    qualified = {
        "artifacts": artifacts,
        **qualification(rows, dataset, manifest, checkpoint, score),
    }
    require(qualified == stopped["qualifications"][0], "Inherited A0 qualification differs")
    require(manifest["git"]["sha"] == OLD_CONTROL, "Inherited A0 source differs")
    for name in ("snapshot", "ingest_checkpoint", "ingest_manifest", "restore_script"):
        pin = previous["pins"][name]
        path = direct(Path(pin["path"]))
        require(digest_file(path) == pin["sha256"], "Inherited immutable artifact changed")
        require(original["pins"][name] == pin, "Original immutable artifact differs")
    inherited = {
        "source_directory": str(a0),
        "source_ledger": dict(lineage["stopped_a1_ledger"]),
        "qualification": qualified,
    }
    return previous, {"evidence_sha256": evidence, "inherited_a0": inherited}


def verify_recovery(payload: Mapping[str, Any], directory: Path | None = None) -> dict[str, Any]:
    pins = payload["pins"]
    validate_mode(pins)
    previous, actual = inspect_lineage(pins["recovery_lineage"])
    metadata = pins["recovery"]
    require(
        actual["evidence_sha256"] == metadata["evidence_sha256"]
        and actual["inherited_a0"] == metadata["inherited_a0"],
        "Recovery evidence manifest changed",
    )
    require(
        payload["question_ids"] == previous["question_ids"]
        and payload["input_limits"] == previous["input_limits"],
        "Recovery allocations/order differ",
    )
    for key, value in previous["pins"].items():
        if key not in {"common_files", "arm_shas", "route_evidence"}:
            require(pins.get(key) == value, "Recovery inherited pins differ")
    bridge = source_bridge(OLD_CONTROL, pins["arm_shas"]["A0"])
    require(bridge == metadata["source_bridge"], "Recovery source bridge changed")
    from orchestrator.eval.campaign_launch import verify_common

    require(
        pins["arm_shas"]
        == {arm: pins["arm_shas"]["C0"] if arm == "C0" else bridge["new_control"] for arm in ARMS}
        and verify_common(bridge["new_control"], pins["arm_shas"]["C0"]) == pins["common_files"],
        "Recovery common source differs",
    )
    if directory is not None:
        previous_directory = Path(pins["recovery_lineage"]["stopped_a1_ledger"]["path"]).parent
        require(
            directory.is_absolute()
            and directory == previous_directory.with_name(previous_directory.name + "-recovery")
            and not directory.is_symlink(),
            "Recovery must use fixed successor directory",
        )
        direct(directory.parent)
        if directory.exists():
            verify_imported(directory, actual["inherited_a0"])
    return actual["inherited_a0"]


def verify_imported(directory: Path, inherited: Mapping[str, Any]) -> None:
    a0 = direct(directory / "A0", directory)
    require({p.name for p in a0.iterdir()} == set(A0_FILES), "Imported A0 coverage differs")
    for name, digest in inherited["qualification"]["artifacts"].items():
        require(digest_file(direct(a0 / name, a0)) == digest, "Imported A0 changed")


def bootstrap(directory: Path, state: dict[str, Any], inherited: Mapping[str, Any]) -> None:
    a0 = directory / "A0"
    a0.mkdir(mode=0o700, exist_ok=False)
    _sync_directory(directory)
    for name, digest in inherited["qualification"]["artifacts"].items():
        source = direct(Path(inherited["source_directory"]) / name)
        require(digest_file(source) == digest, "Bootstrap source changed")
        with source.open("rb") as src, (a0 / name).open("xb") as dst:
            os.chmod(dst.name, 0o600)
            for block in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        require(digest_file(source) == digest_file(a0 / name) == digest, "Bootstrap copy differs")
        _sync_directory(a0)
    verify_imported(directory, inherited)
    state.update(
        arm_index=1,
        restore_attempts=0,
        restore_pending=None,
        restore_receipts=[],
        qualifications=[inherited["qualification"]],
        imported_a0=dict(inherited),
    )


def validate_handoff(
    directory: Path, state: Mapping[str, Any], inherited: Mapping[str, Any]
) -> None:
    index = state["arm_index"]
    require(
        1 < index < len(ARMS)
        and state.get("imported_a0") == inherited
        and state.get("restore_attempts") == index - 1
        and len(state["restores"]) == len(state.get("restore_receipts", [])) == index - 1
        and len(state.get("qualifications", [])) == index
        and state["qualifications"][0] == inherited["qualification"]
        and len(state["receipts"]) == (index - 1) * 200
        and state["question_index"] == 50
        and state["stage_index"] == 0,
        "Recovery handoff state/restore offset differs",
    )
    verify_imported(directory, inherited)
    _receipt_manifest(directory, state, arm_offset=1)


async def prepare_second_replacement(args: argparse.Namespace) -> None:
    """Offline preparation only. Live state/action review remains mandatory at launch."""
    from orchestrator.eval import campaign_launch as launch
    from orchestrator.eval.campaign_guard import CampaignPlan

    require(not args.output.exists() and not args.output.is_symlink(), "Plan output already exists")
    expected = args.previous_replacement_plan.with_name(
        args.previous_replacement_plan.stem + "-recovery" + args.previous_replacement_plan.suffix
    )
    require(args.output == expected, "Recovery plan must use fixed successor output")
    lineage = {
        name: {"path": str(getattr(args, name)), "sha256": getattr(args, name + "_sha256")}
        for name in ("previous_replacement_plan", "stopped_a1_ledger")
    }
    previous, metadata = inspect_lineage(lineage)
    bridge = source_bridge(OLD_CONTROL, args.control_sha)
    pins = json.loads(json.dumps(previous["pins"]))
    pins.update(
        recovery_lineage=lineage,
        recovery={**metadata, "source_bridge": bridge},
        common_files=launch.verify_common(args.control_sha, args.candidate_sha),
        arm_shas={arm: args.candidate_sha if arm == "C0" else args.control_sha for arm in ARMS},
    )
    plan = CampaignPlan(tuple(previous["question_ids"]), previous["input_limits"], pins)
    payload = json.loads(json.dumps(plan.payload()))
    verify_recovery(payload)
    _write(args.output, payload)


def failure_diagnostics(exc: BaseException) -> dict[str, Any]:
    """Only static classes, integer status and exact known-phrase classification.

    Never call str/repr or access response/request/header properties. Causes are
    bounded and cycle-safe. Unknown class names are not retained.
    """
    allowed = {
        "RateLimitError",
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "NotFoundError",
        "APITimeoutError",
        "Timeout",
        "TimeoutError",
        "APIConnectionError",
        "ServiceUnavailableError",
        "InternalServerError",
        "APIError",
        "APIStatusError",
        "OpenAIError",
        "BenchmarkProviderError",
    }
    chain = []
    seen: set[int] = set()
    current: BaseException | None = exc
    classification = "provider_error"
    for _ in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        name = type(current).__name__
        kind = name if name in allowed else "UnknownError"
        fields = BaseException.__getattribute__(current, "__dict__")
        status = fields.get("status_code")
        status = status if type(status) is int and 100 <= status <= 599 else None
        # LiteLLM's known conversion failure is not proof of an empty HTTP body.
        # Inspect only actual string args/message, never arbitrary object formatting.
        values = (*BaseException.__getattribute__(current, "args"), fields.get("message"))
        if name in allowed and any(
            type(value) is str and "Provider returned an empty response" in value
            for value in values
        ):
            classification = "provider_empty_response"
        chain.append({"exception_type": kind, "http_status_code": status})
        cause = BaseException.__getattribute__(current, "__cause__")
        if cause is None:
            cause = BaseException.__getattribute__(current, "__context__")
        current = cause if isinstance(cause, BaseException) else None
    return {"classification": classification, "cause_chain": chain}
