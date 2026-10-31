"""Pinned operator entrypoint for the owner-approved Phase3A campaign.

Preparation is read-only. Running an arm additionally requires a source/state-bound
Astra action-review artifact. Never auto-switch source, retry or resume a failed arm.
"""

from __future__ import annotations

import os

# LiteLLM must not search ancestor checkouts for credential-bearing dotenv files.
os.environ["LITELLM_MODE"] = "PRODUCTION"

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping
from unittest.mock import patch

import asyncpg
import httpx

from orchestrator.eval import fact_harness
from orchestrator.eval.campaign_guard import ARMS, Campaign, CampaignPlan, _write
from orchestrator.eval.campaign_restore import (
    CONTROL_DSN,
    DSN,
    catalogue,
    container_identity,
    restored_pool,
    render_archive,
    target_identity,
    verify_server,
    verify_sessions,
)
from orchestrator.eval.campaign_validation import (
    DEV_SHA256,
    RULE_V1,
    SNAPSHOT_SHA256,
    STATE_SHA256,
    comparability,
    digest_file,
    expected_corpus,
    load_dev,
    paired_result,
    qualification,
    require,
)
from orchestrator.eval.run_manifest import git_state
from tests.longmemeval import evaluate
from tests.longmemeval.official_judge import build_official_prompt

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "tests/benchmark_longmemeval/fixtures/dev_subset.json"
COMMON = (
    "orchestrator/eval/campaign_guard.py",
    "orchestrator/eval/campaign_restore.py",
    "orchestrator/eval/campaign_validation.py",
    "orchestrator/eval/campaign_sql.py",
    "orchestrator/eval/campaign_launch.py",
    "orchestrator/eval/campaign_recovery.py",
    "orchestrator/eval/fact_harness.py",
    "orchestrator/memory/embedding.py",
    "orchestrator/memory/embedding_adapter.py",
    "orchestrator/memory/injection.py",
    "tests/longmemeval/evaluate.py",
    "tests/longmemeval/official_judge.py",
    "config/inference_policy.production.json",
    "tests/benchmark_longmemeval/longmemeval_config_pin.json",
)


def environment() -> dict[str, Any]:
    """Check the existing benchmark environment before any destructive attempt.

    Fresh monitored attestation and account admission still happen in the canonical
    phase/adapter. These static checks never substitute for or relax those gates.
    No secret values enter the returned configuration fingerprint.
    """
    from orchestrator.memory import embedding_adapter
    from orchestrator.entitlements.policy import load_inference_policy

    settings = fact_harness.get_settings()
    require(settings.database_url == DSN, "Exact benchmark DSN required")
    require(
        settings.embedding_route_id == "azure-openrouter-small-1024"
        and settings.embedding_dimensions == 1024,
        "Selected embedding route/dimensions differ",
    )
    require(
        bool(settings.daemon_encryption_key) and bool(settings.daemon_auth_pepper),
        "Benchmark encryption/auth material missing",
    )
    provider = settings.get_provider_config("openrouter")
    require(
        provider.base_url == "https://openrouter.ai/api/v1" and bool(provider.api_key),
        "Pinned reader endpoint/credential missing",
    )
    policy = load_inference_policy()
    route = policy.embedding_routes.get("azure-openrouter-small-1024")
    require(route is not None, "Embedding policy route absent")
    if route is None:
        raise RuntimeError("Embedding policy route absent")
    embedding_adapter.validate_adapter_route(route)
    require(
        route.approved
        and route.availability == "verified"
        and route.account_prompt_logging_disabled is True
        and route.free_model_training_opt_out is True
        and route.zdr_baseline is not None
        and route.transport.zdr is True
        and route.transport.data_collection == "deny",
        "Static embedding privacy/approval differs",
    )
    return fact_harness.effective_config(
        {
            "benchmark_mode": True,
            "force_retrieval_logging": True,
            "limit": None,
            "extraction_profile": fact_harness.EXTRACTION_PROFILE,
            "evaluate_operation": fact_harness.EVALUATE_OPERATION,
        }
    )


def tokenizer() -> tuple[Any, str]:
    import tiktoken
    from tiktoken import load

    def no_download(_: str) -> bytes:
        raise RuntimeError("Pinned tokenizer cache unavailable; no download permitted")

    with patch.object(load, "read_file", no_download):
        encoding = tiktoken.get_encoding("o200k_base")
    # Stable digest of actual BPE token bytes/ranks, independent of package cache path.
    digest = hashlib.sha256()
    for token, rank in sorted(encoding._mergeable_ranks.items(), key=lambda item: item[1]):
        digest.update(rank.to_bytes(4, "big") + len(token).to_bytes(4, "big") + token)
    return encoding, digest.hexdigest()


def rates() -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        for model, prompt, completion in (
            ("openai/gpt-4o-2024-08-06", "0.0000025", "0.00001"),
            ("openai/text-embedding-3-small", "0.00000002", "0"),
        ):
            url = f"https://openrouter.ai/api/v1/models/{model}/endpoints"
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()["data"]
            endpoints = [row for row in payload["endpoints"] if row["tag"] == "azure"]
            require(len(endpoints) == 1, "Exact Azure endpoint missing/ambiguous")
            route = endpoints[0]
            from decimal import Decimal

            require(
                route["status"] == 0
                and Decimal(route["pricing"]["prompt"]) == Decimal(prompt)
                and Decimal(route["pricing"]["completion"]) == Decimal(completion),
                "Current route/rates differ from approved quote",
            )
            if "gpt-4o" in model:
                require("seed" in route["supported_parameters"], "Pinned seed unsupported")
            evidence[model] = {
                "url": url,
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "prompt": prompt,
                "completion": completion,
                "tag": "azure",
            }
    return {"checked_at": datetime.now(UTC).isoformat(), "routes": evidence}


def git_bytes(sha: str, path: str) -> bytes:
    require(
        len(sha) == 40 and all(char in "0123456789abcdef" for char in sha), "Nonexact source SHA"
    )
    return subprocess.run(
        ["git", "show", f"{sha}:{path}"], cwd=ROOT, capture_output=True, check=True, timeout=30
    ).stdout


RETRIEVAL_CANDIDATE = "orchestrator/memory/retrieval.py"
PROVENANCE_CANDIDATE = "orchestrator/memory/provenance.py"
NOTED_DATES_OFF = b"RENDER_NOTED_DATES = False\n"
NOTED_DATES_ON = b"RENDER_NOTED_DATES = True\n"


def verify_common(
    control: str, candidate: str, candidate_file: str = RETRIEVAL_CANDIDATE
) -> dict[str, str]:
    common = {}
    for path in COMMON:
        first, second = git_bytes(control, path), git_bytes(candidate, path)
        require(first == second, "Measurement/config code differs between arms")
        common[path] = hashlib.sha256(first).hexdigest()
    changes = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            control,
            candidate,
            "--",
            "orchestrator",
            "providers",
            "config",
            "migrations",
            "tests/longmemeval",
            "tests/benchmark_longmemeval",
            "pyproject.toml",
            "uv.lock",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    ).stdout.splitlines()
    require(changes == [candidate_file], "Candidate contains a second execution variable")
    return common


def noted_dates_candidate_formatter(control: str, candidate: str) -> dict[str, Any]:
    """Formatter identity of a noted-dates candidate whose only change is the flag.

    The candidate's provenance.py must equal control's with exactly the one
    ``RENDER_NOTED_DATES = False`` line set to True; its identity is then computed
    here (control checkout) with that flag on, so C0 can be pinned exactly.
    """
    first = git_bytes(control, PROVENANCE_CANDIDATE)
    second = git_bytes(candidate, PROVENANCE_CANDIDATE)
    require(
        first.count(NOTED_DATES_OFF) == 1
        and second == first.replace(NOTED_DATES_OFF, NOTED_DATES_ON),
        "Noted-dates candidate changes more than the dormant flag",
    )
    from orchestrator.memory import provenance

    require(provenance.RENDER_NOTED_DATES is False, "Control checkout has the flag enabled")
    provenance.RENDER_NOTED_DATES = True
    try:
        return fact_harness.memory_formatter_identity()
    finally:
        provenance.RENDER_NOTED_DATES = False


def acceptance_rule(pins: Mapping[str, Any]) -> str:
    """The ratified rule version a plan binds: v1 unless its fixed approval says otherwise."""
    return pins.get("fresh_campaign", {}).get("approval", {}).get("acceptance_rule", RULE_V1)


def arm_formatter(plan: CampaignPlan, arm: str) -> dict[str, Any]:
    """The formatter pin an arm must match: a declared renderer candidate's for C0."""
    candidate = plan.pins.get("candidate_formatter")
    return candidate if arm == "C0" and candidate is not None else plan.pins["formatter"]


def formatter_exception(plan: CampaignPlan, arm: str) -> bool:
    """Only the verified single-flag noted-dates C0 may differ in formatter."""
    if (
        arm != "C0"
        or plan.pins.get("candidate_variable") != "noted-dates"
        or not plan.pins.get("candidate_formatter")
    ):
        return False
    from orchestrator.memory import provenance

    # Recompute using the control-checkout helper even when called from C0.
    # Keep that helper's existing refusal contract and restore C0's flag exactly.
    original = provenance.RENDER_NOTED_DATES
    provenance.RENDER_NOTED_DATES = False
    try:
        require(
            noted_dates_candidate_formatter(
                plan.pins["arm_shas"]["A0"], plan.pins["arm_shas"]["C0"]
            )
            == plan.pins["candidate_formatter"],
            "Candidate formatter recomputation differs",
        )
    finally:
        provenance.RENDER_NOTED_DATES = original
    return True


def checkout(plan: CampaignPlan, arm: str) -> None:
    if "fresh_campaign" in plan.pins:
        from orchestrator.eval.campaign_fresh import APPROVAL, verify

        verify(json.loads(json.dumps(plan.payload())), Path(APPROVAL["directory"]))
    elif "final_verdict_recovery_lineage" in plan.pins:
        from orchestrator.eval.campaign_final_verdict import verify_recovery

        require(arm in ("A1", "A2", "C0"), "Inherited A0 cannot execute in final recovery")
        verify_recovery(json.loads(json.dumps(plan.payload())))
    elif "retry_recovery_lineage" in plan.pins:
        from orchestrator.eval.campaign_retry import verify_recovery

        require(arm in ("A1", "A2", "C0"), "Inherited A0 cannot execute in retry recovery")
        verify_recovery(json.loads(json.dumps(plan.payload())))
    elif "recovery_lineage" in plan.pins:
        from orchestrator.eval.campaign_recovery import verify_recovery

        require(arm in ("A1", "A2", "C0"), "Inherited A0 cannot execute in recovery")
        verify_recovery(json.loads(json.dumps(plan.payload())))
    else:
        verify_replacement_lineage(plan.pins.get("replacement_lineage"))
    actual = git_state(ROOT)
    require(
        actual["sha"] == plan.pins["arm_shas"][arm] and actual["dirty"] is False,
        "Execution source is not clean pinned SHA",
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    require(not status.stdout.strip(), "Untracked or changed execution checkout")
    # Include untracked runtime files in the normal manifest source hash too.
    require(not (ROOT / ".env").exists(), "Dotenv checkout is not eligible")
    for path, digest in plan.pins["common_files"].items():
        require(digest_file(ROOT / path) == digest, "Common source/config content changed")
    require(
        fact_harness.memory_formatter_identity() == arm_formatter(plan, arm),
        "Formatter pin differs",
    )
    require(
        [row["question_id"] for row in load_dev(DEV)] == list(plan.question_ids),
        "Dev order differs",
    )
    for name in ("snapshot", "ingest_checkpoint", "ingest_manifest", "restore_script"):
        artifact = plan.pins[name]
        require(digest_file(Path(artifact["path"])) == artifact["sha256"], f"Pinned {name} changed")


def verify_replacement_lineage(lineage: Any) -> dict[str, Any]:
    """Keep failed paid/restore attempts immutable; never load them as resumable."""
    require(isinstance(lineage, dict), "Replacement lineage required")
    evidence = {}
    for name in ("previous_plan", "failed_ledger"):
        artifact = lineage[name]
        path = Path(artifact["path"])
        require(digest_file(path) == artifact["sha256"], f"Replacement {name} changed")
        evidence[name] = json.loads(path.read_bytes())
    previous, failed = evidence["previous_plan"], evidence["failed_ledger"]
    require(
        previous["prior_reserved_usd"] == "1.7019050"
        and previous["new_reserved_usd"] == "4.35"
        and previous["cumulative_reserved_usd"] == "6.0519050"
        and previous["arms"] == list(ARMS),
        "Unexpected original reservation/arms",
    )
    require(
        failed["plan"] == previous
        and failed["status"] == "stopped_reconciliation_required"
        and failed["failure_admission"] == "closed"
        and failed["restore_attempts"] == 1
        and failed["restore_pending"] is None
        and len(failed["restore_receipts"]) == 1
        and len(failed["receipts"]) == 6
        and failed["arm"] == "A0"
        and failed["arm_index"] == 0
        and failed["pending"]["arm"] == "A0"
        and failed["pending"]["stage"] == "judge"
        and failed["pending"]["question_id"] == previous["question_ids"][1],
        "Original failure differs; explicit new recovery decision required",
    )
    return previous


async def prepare_replacement(args: argparse.Namespace) -> None:
    """Read-only preparation from pinned history while target admission stays closed.

    The inherited catalogue is dated evidence. The unchanged restore boundary
    remeasures it after opening owned connections and reclosing admission, before
    DROP; this preparation never claims a fresh target catalogue/fingerprint.
    """
    require(not args.output.exists(), "Plan output already exists")
    lineage = {
        name: {
            "path": str(getattr(args, name).resolve()),
            "sha256": getattr(args, name + "_sha256"),
        }
        for name in ("previous_plan", "failed_ledger")
    }
    previous = verify_replacement_lineage(lineage)
    dataset = load_dev(DEV)
    require(
        [row["question_id"] for row in dataset] == previous["question_ids"],
        "Original dev order changed",
    )
    pins = json.loads(json.dumps(previous["pins"]))
    require(environment() == pins["effective_config"], "Original benchmark config changed")
    require(tokenizer()[1] == pins["tokenizer_sha256"], "Original tokenizer changed")
    for name in ("snapshot", "ingest_checkpoint", "ingest_manifest", "restore_script"):
        artifact = pins[name]
        require(
            digest_file(Path(artifact["path"])) == artifact["sha256"], f"Original {name} changed"
        )
    require(pins["snapshot"]["sha256"] == SNAPSHOT_SHA256, "Unqualified archive")
    pins["common_files"] = verify_common(args.control_sha, args.candidate_sha)
    pins["arm_shas"] = {
        arm: args.candidate_sha if arm == "C0" else args.control_sha for arm in ARMS
    }
    pins["route_evidence"] = await asyncio.to_thread(rates)
    pins["replacement_lineage"] = lineage
    require(pins["original_allow_connections"] is True, "Original access state differs")
    container_identity()
    control = await asyncpg.connect(
        CONTROL_DSN,
        timeout=10,
        command_timeout=30,
        server_settings={"default_transaction_read_only": "on"},
    )
    try:
        await verify_server(control)
        identity = await target_identity(control)
        require(
            identity["system_identifier"] == pins["server_identifier"]
            and identity["oid"] == pins["database_oid"]
            and identity["datallowconn"] is False,
            "Replacement target identity/admission differs",
        )
        await verify_sessions(control, set())
    finally:
        await control.close()
    pins["replacement_preparation"] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "target_admission": "closed",
        "catalogue_evidence": "inherited; mandatory live recheck before DROP",
        "prior_restore_attempts": 1,
        "replacement_restore_limit": 4,
    }
    verify_replacement_lineage(lineage)
    plan = CampaignPlan(tuple(previous["question_ids"]), previous["input_limits"], pins)
    _write(args.output, plan.payload())


def verify_replacement_destination(plan: CampaignPlan, directory: Path) -> None:
    """One fixed successor ledger, not a fresh allowance per output directory.

    Like the sibling lock, this is cooperative operator protection, not a defense
    against a same-UID administrator copying or deleting retained evidence.
    """
    if "fresh_campaign" in plan.pins:
        from orchestrator.eval.campaign_fresh import verify

        verify(json.loads(json.dumps(plan.payload())), directory)
        return
    if "final_verdict_recovery_lineage" in plan.pins:
        from orchestrator.eval.campaign_final_verdict import verify_recovery

        verify_recovery(json.loads(json.dumps(plan.payload())), directory)
        return
    if "retry_recovery_lineage" in plan.pins:
        from orchestrator.eval.campaign_retry import verify_recovery

        verify_recovery(json.loads(json.dumps(plan.payload())), directory)
        return
    if "recovery_lineage" in plan.pins:
        from orchestrator.eval.campaign_recovery import verify_recovery

        verify_recovery(json.loads(json.dumps(plan.payload())), directory)
        return
    lineage = plan.pins.get("replacement_lineage")
    verify_replacement_lineage(lineage)
    assert isinstance(lineage, dict)  # Enforced by verify_replacement_lineage above.
    failed_directory = Path(lineage["failed_ledger"]["path"]).resolve(strict=True).parent
    expected = failed_directory.with_name(failed_directory.name + "-replacement")
    require(
        directory.absolute() == expected and not directory.is_symlink(),
        "Replacement must use the fixed successor campaign directory",
    )


def _historical_remap_pins(args: argparse.Namespace) -> dict[str, Any]:
    """Verify a historical-remap lineage against the snapshot and derived ingest checkpoint."""
    lineage_path = args.historical_remap.resolve()
    lineage = json.loads(lineage_path.read_bytes())
    require(lineage.get("status") == "remapped_snapshot_created", "Remap lineage incomplete")
    require(
        lineage["source"]["snapshot_sha256"] == SNAPSHOT_SHA256
        and lineage["source"]["state_sha256"] == STATE_SHA256,
        "Remap is not derived from the qualified corpus",
    )
    require(
        Path(lineage["snapshot"]["path"]).resolve() == args.snapshot.resolve(),
        "Snapshot is not the remap lineage's",
    )
    derived = fact_harness.load_runner_checkpoint(args.ingest, dataset_path=None)
    remap = derived.get("historical_remap") or {}
    require(
        Path(remap.get("lineage_path", "")).resolve() == lineage_path
        and remap.get("lineage_sha256") == digest_file(lineage_path),
        "Ingest checkpoint is not this remap's",
    )
    source_manifest = fact_harness.verify_historical_remap(
        remap, derived["phases"]["ingest"], derived.get("run_id")
    )
    return {
        "lineage_path": str(lineage_path),
        "lineage_sha256": digest_file(lineage_path),
        "snapshot_sha256": lineage["snapshot"]["sha256"],
        "state_sha256": lineage["remapped_state_sha256"],
        "source_manifest": str(source_manifest),
    }


async def prepare(args: argparse.Namespace) -> None:
    fresh = getattr(args, "fresh_campaign", False)
    if fresh:
        from orchestrator.eval import campaign_fresh

        require(
            args.historical_remap is not None and args.candidate == "noted-dates",
            "Fresh campaign requires --historical-remap and --candidate noted-dates",
        )
        campaign_fresh.verify_prior()
    dataset = load_dev(DEV)
    effective = environment()
    historical = _historical_remap_pins(args) if args.historical_remap else None
    snapshot_sha256 = historical["snapshot_sha256"] if historical else SNAPSHOT_SHA256
    require(digest_file(args.snapshot) == snapshot_sha256, "Wrong immutable archive")
    noted_dates = getattr(args, "candidate", "retrieval") == "noted-dates"
    common = verify_common(
        args.control_sha,
        args.candidate_sha,
        PROVENANCE_CANDIDATE if noted_dates else RETRIEVAL_CANDIDATE,
    )
    cost = json.loads(args.cost_preflight.read_bytes())
    if fresh:
        from orchestrator.eval import campaign_fresh

        assert historical is not None
        require(
            historical["lineage_sha256"]
            == campaign_fresh.APPROVAL["historical_remap_lineage_sha256"],
            "Fresh remap lineage is not approved",
        )
        campaign_fresh.validate_preflight(
            cost,
            campaign_fresh.bindings(
                args.control_sha, args.candidate_sha, historical["lineage_sha256"], tokenizer()[1]
            ),
            [row["question_id"] for row in dataset],
        )
    else:
        require(
            cost["provider_calls"] == cost["writes_or_restores"] == 0
            and cost["under_cumulative_ten_usd"] is True,
            "Preflight not read-only/within budget",
        )
    reader = {row["question_id"]: row["reader_input_token_bound"] for row in cost["questions"]}
    limits = {}
    for row in dataset:
        limits[row["question_id"]] = {
            "embedding": len(row["question"].encode()),
            "answer": reader[row["question_id"]],
            "judge": len(
                evaluate.build_generic_judge_prompt(row["question"], "", row["answer"]).encode()
            )
            + 64
            + 256,
            "official_judge": len(
                build_official_prompt(
                    row["question_type"],
                    row["question"],
                    row["answer"],
                    "",
                    question_id=row["question_id"],
                ).encode()
            )
            + 64
            + 256,
        }
    encoding, tokenizer_hash = tokenizer()
    # Evidence for the 64-token fixed envelope allocation: for our two text roles,
    # snapshot ChatML is 3 per message + 3 reply primer (9 <=64). Actual complete
    # content and role tokens are measured at send time, not inferred from bytes.
    require(
        encoding.name == "o200k_base" and 3 * 2 + 3 <= 64, "Tokenizer/envelope contract differs"
    )
    require(
        max(len(evaluate.build_parity_answer_messages(row["question"], "")) for row in dataset)
        == 2,
        "Unexpected reader message shape",
    )
    source_manifest = (
        Path(historical["source_manifest"])
        if historical
        else args.ingest.parent / "run_manifest.json"
    )
    source = json.loads(source_manifest.read_bytes())
    require(
        source["baseline_qualified"] is True and source["disqualifications"] == [],
        "Ingest source not qualified",
    )
    require(
        json.loads(args.ingest.read_bytes())["dataset_sha256"] == DEV_SHA256,
        "Ingest dataset differs",
    )
    current_rates = await asyncio.to_thread(rates)
    container_identity()
    control = await asyncpg.connect(CONTROL_DSN, timeout=10, command_timeout=30)
    connection = None
    try:
        await verify_server(control)
        identity = await target_identity(control)
        require(
            identity["datallowconn"] is True, "Preparation requires original open benchmark state"
        )
        connection = await asyncpg.connect(
            DSN,
            timeout=10,
            command_timeout=30,
            server_settings={"default_transaction_read_only": "on"},
        )
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            schema = await catalogue(connection)
            from orchestrator.eval.bench_db import fingerprint_benchmark_users, question_user_id

            fingerprint = await fingerprint_benchmark_users(
                connection, [question_user_id(row["question_id"]) for row in dataset]
            )
            # Every arm restores the pinned snapshot, so a fresh plan also accepts the
            # target holding its own remapped state (left by a stopped fresh campaign
            # and reopened by recorded reconciliation). Other modes require original.
            accepted = {STATE_SHA256}
            if fresh:
                assert historical is not None
                accepted.add(historical["state_sha256"])
            require(fingerprint["sha256"] in accepted, "Preparation protected fingerprint differs")
    finally:
        if connection is not None:
            await connection.close()
        await control.close()
    script_directory = args.output.parent / (args.output.stem + "-restore-script")
    script_directory.mkdir(mode=0o700, exist_ok=False)
    script = await asyncio.to_thread(
        render_archive, args.snapshot, script_directory, snapshot_sha256
    )
    pins = {
        "common_files": common,
        "arm_shas": {arm: args.candidate_sha if arm == "C0" else args.control_sha for arm in ARMS},
        "formatter": fact_harness.memory_formatter_identity(),
        **(
            {
                "candidate_variable": "noted-dates",
                "candidate_formatter": noted_dates_candidate_formatter(
                    args.control_sha, args.candidate_sha
                ),
            }
            if noted_dates
            else {}
        ),
        "tokenizer_sha256": tokenizer_hash,
        "tokenizer_envelope": {
            "model": "gpt-4o-2024-08-06",
            "per_message": 3,
            "reply_primer": 3,
            "allocated": 64,
            "source": "https://cookbook.openai.com/examples/how_to_count_tokens_with_tiktoken",
        },
        "server_identifier": identity["system_identifier"],
        "database_oid": identity["oid"],
        "catalogue_sha256": schema,
        "original_allow_connections": identity["datallowconn"],
        "snapshot": {"path": str(args.snapshot.resolve()), "sha256": digest_file(args.snapshot)},
        "ingest_checkpoint": {
            "path": str(args.ingest.resolve()),
            "sha256": digest_file(args.ingest),
        },
        "ingest_manifest": {
            "path": str(source_manifest.resolve()),
            "sha256": digest_file(source_manifest),
        },
        "route_evidence": current_rates,
        "effective_config": effective,
        "restore_script": {"path": str(script.resolve()), "sha256": digest_file(script)},
    }
    if historical:
        # Arms restore and evaluate the remapped corpus; the live database is still
        # verified above against the original qualified state it derives from.
        pins["historical_remap"] = {
            key: historical[key]
            for key in ("lineage_path", "lineage_sha256", "snapshot_sha256", "state_sha256")
        }
    if fresh:
        from orchestrator.eval import campaign_fresh

        pins["fresh_campaign"] = json.loads(
            json.dumps({"approval": campaign_fresh.APPROVAL, "prior": campaign_fresh.PRIOR})
        )
    plan = CampaignPlan(tuple(row["question_id"] for row in dataset), limits, pins)
    if fresh:
        from orchestrator.eval import campaign_fresh

        campaign_fresh.verify(json.loads(json.dumps(plan.payload())))
    require(not args.output.exists(), "Plan output already exists")
    _write(args.output, plan.payload())


def load_plan(path: Path) -> CampaignPlan:
    data = json.loads(path.read_bytes())
    plan = CampaignPlan(tuple(data["question_ids"]), data["input_limits"], data["pins"])
    require(json.loads(json.dumps(plan.payload())) == data, "Plan has changed/unknown fields")
    return plan


PHASE_D_A0_RESULTS = (
    ROOT / "tests/benchmark_results/longmemeval_phase_d_2026-10-27/eval_a_qualified"
    "/longmemeval_results.jsonl"
)


async def fresh_cost_preflight(args: argparse.Namespace) -> None:
    """Zero-call, read-only reader bound on the remapped copy (never the restore target).

    Runs on the clean control checkout. Memory text is decrypted in process memory
    only; the output holds sizes, digests and bounds, never plaintext.
    """
    from orchestrator.eval import campaign_fresh
    from orchestrator.eval.bench_db import (
        check_benchmark_dsn,
        fingerprint_benchmark_users,
        question_user_id,
    )
    from orchestrator.memory.encryption import ContentEncryption

    actual = git_state(ROOT)
    require(
        actual["sha"] == args.control_sha and actual["dirty"] is False,
        "Fresh preflight must run on the clean control checkout",
    )
    lineage_path = args.historical_remap.absolute()
    lineage_sha256 = digest_file(lineage_path)
    require(
        lineage_sha256 == campaign_fresh.APPROVAL["historical_remap_lineage_sha256"],
        "Fresh remap lineage is not approved",
    )
    lineage = json.loads(lineage_path.read_bytes())
    require(lineage["status"] == "remapped_snapshot_created", "Remap lineage incomplete")
    database = lineage["copy"]["db"]
    require(database != DSN.rsplit("/", 1)[1], "Preflight must read the copy, not the target")
    dsn = DSN.rsplit("/", 1)[0] + "/" + database
    check_benchmark_dsn(dsn)
    dataset = load_dev(DEV)
    encoding, tokenizer_sha256 = tokenizer()
    bound_bindings = campaign_fresh.bindings(
        args.control_sha, args.candidate_sha, lineage_sha256, tokenizer_sha256
    )
    saved = {
        row["question_id"]: row["memory_context"]
        for row in map(json.loads, PHASE_D_A0_RESULTS.read_text().splitlines())
    }
    key = fact_harness.get_settings().daemon_encryption_key
    require(bool(key), "Benchmark encryption key missing")
    encryption = ContentEncryption(key)
    container_identity()
    users = {row["question_id"]: question_user_id(row["question_id"]) for row in dataset}
    connection = await asyncpg.connect(
        dsn,
        timeout=10,
        server_settings={"default_transaction_read_only": "on", "statement_timeout": "30000"},
    )
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            # The copy has no marker comment (CREATE DATABASE doesn't copy it), and
            # marking it would be a write. Identify it exactly instead: lineage name
            # and OID here, plus the remapped benchmark fingerprint below.
            identity = await connection.fetchrow(
                "SELECT current_database() AS name, d.oid AS oid FROM pg_database d"
                " WHERE d.datname = current_database()"
            )
            require(
                identity is not None
                and identity["name"] == database
                and identity["oid"] == lineage["copy"]["oid"],
                "Connected database is not the lineage's remapped copy",
            )
            name = database
            require(
                await connection.fetchval("SHOW transaction_read_only") == "on",
                "Preflight transaction is not read-only",
            )
            before = await fingerprint_benchmark_users(connection, list(users.values()))
            require(
                before["sha256"] == lineage["remapped_state_sha256"],
                "Remapped copy fingerprint differs",
            )
            rows = await connection.fetch(
                "SELECT user_id, content, category, tier, status, valid_to, valid_from,"
                " created_at, local_only FROM memories WHERE user_id = ANY($1::uuid[])",
                list(users.values()),
            )
            by_user: dict[Any, list[dict[str, Any]]] = {}
            for row in rows:
                require(
                    not (
                        row["tier"] == "l0"
                        and row["status"] == "active"
                        and row["valid_to"] is None
                        and row["local_only"] is False
                    ),
                    "L0 present: the superset bound does not cover a frozen block",
                )
                by_user.setdefault(row["user_id"], []).append(
                    {
                        "content": encryption.decrypt(row["content"]),
                        "category": row["category"],
                        "valid_from": row["valid_from"],
                        "created_at": row["created_at"],
                    }
                )
            after = await fingerprint_benchmark_users(connection, list(users.values()))
            require(after == before, "Remapped copy changed during preflight")
    finally:
        await connection.close()
    result = campaign_fresh.superset_preflight(
        dataset,
        {qid: by_user.get(user, []) for qid, user in users.items()},
        {qid: saved[qid] for qid in users},
        bound_bindings,
        lambda text: len(encoding.encode_ordinary(text)),
    )
    result.update(
        database_name=name,
        transaction_read_only=True,
        fingerprint_before_and_after=before,
        rows_examined=len(rows),
        plaintext_exported=False,
        reader_input_token_bound_total=sum(
            row["reader_input_token_bound"] for row in result["questions"]
        ),
    )
    with args.output.open("x") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "questions"}, indent=2))


async def run_arm(args: argparse.Namespace) -> None:
    plan = load_plan(args.plan)
    verify_replacement_destination(plan, args.campaign)
    checkout(plan, args.arm)
    require(
        environment() == plan.pins["effective_config"],
        "Prepared benchmark environment/config changed",
    )
    encoding, digest = tokenizer()
    require(digest == plan.pins["tokenizer_sha256"], "Tokenizer cache identity changed")
    await asyncio.to_thread(rates)  # Fresh uncredentialed metadata, no inference.
    review = json.loads(args.action_review.read_bytes())
    require(
        review.get("supervisor") == "astra" and review.get("status") == "approved_for_exact_action",
        "Missing action-time supervision",
    )
    require(
        review.get("plan_sha256") == digest_file(args.plan)
        and review.get("source_sha") == plan.pins["arm_shas"][args.arm]
        and review.get("arm") == args.arm
        and review.get("review_session")
        and review.get("live_state_evidence_sha256"),
        "Action review not bound to this plan/source/arm/state",
    )
    age = (datetime.now(UTC) - datetime.fromisoformat(review["reviewed_at"])).total_seconds()
    require(0 <= age <= 900, "Action-state review expired")
    dataset = load_dev(DEV)
    with Campaign(
        args.campaign, plan, lambda text: len(encoding.encode_ordinary(text))
    ).activate() as campaign:
        from orchestrator.eval.campaign_retry import enabled
        from orchestrator.eval.campaign_restore import attached_pool

        pool_context = (
            attached_pool(campaign)
            if enabled(campaign) and args.arm == "A1"
            else restored_pool(campaign, args.arm, Path(plan.pins["snapshot"]["path"]))
        )
        async with pool_context:
            output = args.campaign / args.arm
            runner = fact_harness.LongMemEvalFactRunner(
                dataset_path=DEV,
                output_path=output / "longmemeval_results.jsonl",
                checkpoint_path=output / "longmemeval_checkpoint.json",
                score_path=output / "longmemeval_score.json",
                from_ingest=Path(plan.pins["ingest_checkpoint"]["path"]),
                ingest_snapshot=Path(plan.pins["snapshot"]["path"]),
                force_retrieval_logging=True,
            )
            rows = await runner.evaluate()
            score = runner.score()
            manifest = json.loads((output / "run_manifest.json").read_bytes())
            checkpoint = json.loads(runner.checkpoint_path.read_bytes())
            qualified = qualification(
                rows,
                dataset,
                manifest,
                checkpoint,
                score,
                corpus=expected_corpus(plan.pins),
                rule=acceptance_rule(plan.pins),
            )
            planned = plan.pins.get("historical_remap")
            recorded = (manifest.get("ingest_source") or {}).get("historical_remap")
            require(
                (planned is None and recorded is None)
                or (
                    planned is not None
                    and recorded is not None
                    and recorded.get("lineage_sha256") == planned["lineage_sha256"]
                ),
                "Run clock mode differs from the plan's corpus",
            )
            require(
                manifest["git"]["sha"] == plan.pins["arm_shas"][args.arm],
                "Manifest execution SHA differs",
            )
            require(
                manifest["memory_formatter"] == arm_formatter(plan, args.arm),
                "Manifest formatter differs from the arm's pin",
            )
            if args.arm != "A0":
                first = json.loads((args.campaign / "A0/run_manifest.json").read_bytes())
                # A declared renderer candidate differs in formatter by design; it is
                # pinned exactly above, and every other comparability field still applies.
                comparability(
                    first,
                    manifest,
                    formatter=not formatter_exception(plan, args.arm),
                )
            artifacts = {
                path.name: digest_file(path)
                for path in (
                    runner.output_path,
                    runner.checkpoint_path,
                    runner.score_path,
                    output / "run_manifest.json",
                )
            }
            campaign.finish_arm(qualification={"artifacts": artifacts, **qualified})
    if args.arm == "C0":
        arms = {
            arm: [
                json.loads(line)
                for line in (args.campaign / arm / "longmemeval_results.jsonl")
                .read_text()
                .splitlines()
            ]
            for arm in ARMS
        }
        _write(
            args.campaign / "paired-result.json",
            paired_result(arms, rule=acceptance_rule(plan.pins)),
        )
        # Only a completely finished, qualified campaign can restore original access.
        state = json.loads((args.campaign / "campaign.json").read_bytes())
        require(
            state["status"] == "completed"
            and not state.get("restore_pending")
            and not state["pending"],
            "Incomplete campaign cannot reopen database",
        )
        state["final_reopen"] = "pending"
        _write(args.campaign / "campaign.json", state)
        control = await asyncpg.connect(CONTROL_DSN, timeout=10, command_timeout=30)
        try:
            await verify_server(control)
            identity = await target_identity(control)
            require(
                identity["system_identifier"] == plan.pins["server_identifier"]
                and identity["oid"] == plan.pins["database_oid"],
                "Final target differs",
            )
            require(
                await control.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname='daemon_lme_bench'"
                )
                == 0,
                "Owned pool not closed",
            )
            require(
                plan.pins["original_allow_connections"] is True, "Original connection state differs"
            )
            await control.execute("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS true")
            state["final_reopen"] = "completed"
            _write(args.campaign / "campaign.json", state)
        finally:
            await control.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser(
        "prepare", help="Read-only pin and budget preparation; no restore/inference"
    )
    for name in ("snapshot", "ingest", "cost-preflight", "output"):
        prep.add_argument("--" + name, type=Path, required=True)
    for name in ("control-sha", "candidate-sha"):
        prep.add_argument("--" + name, required=True)
    prep.add_argument(
        "--candidate",
        choices=("retrieval", "noted-dates"),
        default="retrieval",
        help="Candidate variable: retrieval.py (default) or the dormant noted-dates flag",
    )
    prep.add_argument(
        "--historical-remap",
        type=Path,
        default=None,
        help="Remap lineage JSON: arms restore its snapshot with a derived --ingest checkpoint",
    )
    prep.add_argument(
        "--fresh-campaign",
        action="store_true",
        help="Use the single fixed fresh historical noted-dates approval",
    )
    preflight = commands.add_parser(
        "cost-preflight",
        help="Zero-call read-only fresh reader prompt bound on the remapped copy",
    )
    preflight.add_argument("--fresh", action="store_true", required=True)
    preflight.add_argument("--historical-remap", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)
    for name in ("control-sha", "candidate-sha"):
        preflight.add_argument("--" + name, required=True)
    replacement = commands.add_parser(
        "prepare-replacement", help="Read-only recovery preparation; target stays closed"
    )
    for name in ("previous-plan", "failed-ledger", "output"):
        replacement.add_argument("--" + name, type=Path, required=True)
    for name in ("previous-plan-sha256", "failed-ledger-sha256", "control-sha", "candidate-sha"):
        replacement.add_argument("--" + name, required=True)
    second = commands.add_parser(
        "prepare-second-replacement", help="Offline exact stopped-A1 recovery preparation only"
    )
    for name in ("previous-replacement-plan", "stopped-a1-ledger", "output"):
        second.add_argument("--" + name, type=Path, required=True)
    for name in (
        "previous-replacement-plan-sha256",
        "stopped-a1-ledger-sha256",
        "control-sha",
        "candidate-sha",
    ):
        second.add_argument("--" + name, required=True)
    retry = commands.add_parser(
        "prepare-retry-recovery", help="Offline exact stopped-official recovery"
    )
    for name in ("previous-plan", "stopped-ledger", "output"):
        retry.add_argument("--" + name, type=Path, required=True)
    for name in ("previous-plan-sha256", "stopped-ledger-sha256", "control-sha", "candidate-sha"):
        retry.add_argument("--" + name, required=True)
    final = commands.add_parser(
        "prepare-final-verdict-recovery", help="Offline exact completed-verdict import preparation"
    )
    for name in ("previous-plan", "stopped-ledger", "attempt-record", "result", "output"):
        final.add_argument("--" + name, type=Path, required=True)
    for name in (
        "previous-plan-sha256",
        "stopped-ledger-sha256",
        "attempt-record-sha256",
        "result-sha256",
        "control-sha",
        "candidate-sha",
    ):
        final.add_argument("--" + name, required=True)
    run = commands.add_parser(
        "arm", help="One explicitly reviewed arm; never automatic resume/retry"
    )
    for name in ("plan", "campaign", "action-review"):
        run.add_argument("--" + name, type=Path, required=True)
    run.add_argument("--arm", choices=ARMS, required=True)
    args = parser.parse_args()
    from orchestrator.eval.campaign_recovery import prepare_second_replacement
    from orchestrator.eval.campaign_retry import prepare as prepare_retry
    from orchestrator.eval.campaign_final_verdict import prepare as prepare_final

    operation = {
        "prepare": prepare,
        "cost-preflight": fresh_cost_preflight,
        "prepare-replacement": prepare_replacement,
        "prepare-second-replacement": prepare_second_replacement,
        "prepare-retry-recovery": prepare_retry,
        "prepare-final-verdict-recovery": prepare_final,
        "arm": run_arm,
    }
    asyncio.run(operation[args.command](args))


if __name__ == "__main__":
    main()
