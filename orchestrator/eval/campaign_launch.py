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
from typing import Any
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
)
from orchestrator.eval.campaign_validation import (
    DEV_SHA256,
    SNAPSHOT_SHA256,
    comparability,
    digest_file,
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


def verify_common(control: str, candidate: str) -> dict[str, str]:
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
    require(
        changes == ["orchestrator/memory/retrieval.py"],
        "Candidate contains a second execution variable",
    )
    return common


def checkout(plan: CampaignPlan, arm: str) -> None:
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
        fact_harness.memory_formatter_identity() == plan.pins["formatter"], "Formatter pin differs"
    )
    require(
        [row["question_id"] for row in load_dev(DEV)] == list(plan.question_ids),
        "Dev order differs",
    )
    for name in ("snapshot", "ingest_checkpoint", "ingest_manifest", "restore_script"):
        artifact = plan.pins[name]
        require(digest_file(Path(artifact["path"])) == artifact["sha256"], f"Pinned {name} changed")


async def prepare(args: argparse.Namespace) -> None:
    dataset = load_dev(DEV)
    effective = environment()
    require(digest_file(args.snapshot) == SNAPSHOT_SHA256, "Wrong immutable archive")
    common = verify_common(args.control_sha, args.candidate_sha)
    cost = json.loads(args.cost_preflight.read_bytes())
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
    source_manifest = args.ingest.parent / "run_manifest.json"
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
            from orchestrator.eval.campaign_validation import STATE_SHA256

            fingerprint = await fingerprint_benchmark_users(
                connection, [question_user_id(row["question_id"]) for row in dataset]
            )
            require(
                fingerprint["sha256"] == STATE_SHA256, "Preparation protected fingerprint differs"
            )
    finally:
        if connection is not None:
            await connection.close()
        await control.close()
    script_directory = args.output.parent / (args.output.stem + "-restore-script")
    script_directory.mkdir(mode=0o700, exist_ok=False)
    script = await asyncio.to_thread(render_archive, args.snapshot, script_directory)
    pins = {
        "common_files": common,
        "arm_shas": {arm: args.candidate_sha if arm == "C0" else args.control_sha for arm in ARMS},
        "formatter": fact_harness.memory_formatter_identity(),
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
    plan = CampaignPlan(tuple(row["question_id"] for row in dataset), limits, pins)
    require(not args.output.exists(), "Plan output already exists")
    _write(args.output, plan.payload())


def load_plan(path: Path) -> CampaignPlan:
    data = json.loads(path.read_bytes())
    plan = CampaignPlan(tuple(data["question_ids"]), data["input_limits"], data["pins"])
    require(json.loads(json.dumps(plan.payload())) == data, "Plan has changed/unknown fields")
    return plan


async def run_arm(args: argparse.Namespace) -> None:
    plan = load_plan(args.plan)
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
        async with restored_pool(campaign, args.arm, Path(plan.pins["snapshot"]["path"])):
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
            qualified = qualification(rows, dataset, manifest, checkpoint, score)
            require(
                manifest["git"]["sha"] == plan.pins["arm_shas"][args.arm],
                "Manifest execution SHA differs",
            )
            if args.arm != "A0":
                first = json.loads((args.campaign / "A0/run_manifest.json").read_bytes())
                comparability(first, manifest)
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
        _write(args.campaign / "paired-result.json", paired_result(arms))
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
    run = commands.add_parser(
        "arm", help="One explicitly reviewed arm; never automatic resume/retry"
    )
    for name in ("plan", "campaign", "action-review"):
        run.add_argument("--" + name, type=Path, required=True)
    run.add_argument("--arm", choices=ARMS, required=True)
    args = parser.parse_args()
    asyncio.run(prepare(args) if args.command == "prepare" else run_arm(args))


if __name__ == "__main__":
    main()
