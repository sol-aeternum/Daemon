"""Bounded, manifest-driven model upgrade evaluations.

Use --dry-run for a read-only whole-schedule preflight. Run diagnostic,
regression, then streaming with the same manifest, policies and private run
directory. --report renders summaries and a model-blinded human review packet
without inference. A manifest describes an experiment; it does not approve
provider routes, allocate funding, supply semantic verdicts or activate routing.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

from orchestrator import compute_runtime, model_routing
from orchestrator.config import Settings, get_settings
from orchestrator.entitlements.errors import EntitlementsError
from orchestrator.entitlements.policy import load_inference_policy, load_policy
from orchestrator.entitlements.service import EntitlementService
from scripts import model_routing_followup as follow
from scripts import model_upgrade_spec as specs
from scripts import model_upgrade_stream as streaming

ROOT = Path(__file__).resolve().parents[1]
PHASES = {"diagnostic": "diagnostic", "regression": "heldout", "streaming": "streaming"}


@dataclass(frozen=True, slots=True)
class UpgradeAttempt(follow.Attempt):
    effort_value: str
    setting_label: str
    phase: str
    is_probe: bool

    @property
    def effort(self) -> str:
        return self.effort_value


def load_plan(
    path: Path,
) -> tuple[Any, dict[str, Any], follow.FixtureSet, tuple[UpgradeAttempt, ...]]:
    before = path.read_bytes()
    spec = specs.load_spec(path, ROOT)
    follow.require(before == path.read_bytes(), "manifest changed while loading")
    raw = json.loads(before)
    fixtures = follow.load_fixtures(ROOT / raw["fixtures"]["path"])
    follow.require(fixtures.sha256 == raw["fixtures"]["sha256"], "fixture digest drift")
    planned = specs.build_schedule(spec, fixtures)
    schedule = tuple(
        UpgradeAttempt(
            attempt_id=a.attempt_id,
            case_id=a.case_id,
            stage=a.stage,
            candidate_label=a.candidate_label,
            condition="explicit",
            repeat=a.repeat,
            latency_class=a.latency_class,
            max_calls=a.max_calls,
            max_tool_calls=a.max_tool_calls,
            effort_value=a.effort,
            setting_label=a.condition_label,
            phase=a.phase,
            is_probe=a.is_probe,
        )
        for a in planned
    )
    return spec, raw, fixtures, schedule


def profile_for(
    raw: dict[str, Any], schedule: tuple[UpgradeAttempt, ...], path: Path
) -> follow.ExperimentProfile:
    return follow.ExperimentProfile(
        experiment=f"model-upgrade/{raw['experiment']}",
        state_version="model-upgrade-state/1",
        results_artifact_version="model-upgrade-results/1",
        generated_by="scripts/model_upgrade.py",
        lock_holder_label="model upgrade runner",
        results_notes="Human adjudication required. Reused regression evidence, not unseen cases. "
        "Production streaming probes are separate protocol evidence. Unknown costs stay null.",
        incremental_cap_microusd=raw["bounds"]["incremental_cap_microusd"],
        total_attempts=len(schedule),
        dispatch_bound=sum(a.max_calls for a in schedule),
        implementation_paths=(
            Path(__file__),
            Path(specs.__file__),
            Path(streaming.__file__),
            path,
            ROOT / "orchestrator/tools/completion.py",
            ROOT / "orchestrator/tools/registry.py",
            ROOT / "orchestrator/tools/executor.py",
            ROOT / "orchestrator/guardrails.py",
            ROOT / "orchestrator/config.py",
        ),
        phase_stages=PHASES,
        phase_labels={p: p for p in PHASES},
        gated_phase="regression",
        gate_stage="diagnostic",
    )


def qualified_routes(raw: dict[str, Any], inference: Any) -> dict[str, Any]:
    routes = {}
    for candidate in raw["candidates"]:
        label = candidate["label"]
        route = inference.route(candidate["route_id"])
        follow.require(
            route is not None and route.is_approved(inference.requirements),
            f"unqualified route for {label}",
        )
        assert route is not None
        follow.require(
            route.provider == "openrouter" and route.model == candidate["model"],
            f"exact model pin mismatch for {label}",
        )
        follow.require(
            tuple(route.transport.provider_only or ()) == (candidate["provider_pin"],)
            and tuple(route.transport.provider_order or ()) == (candidate["provider_pin"],)
            and route.transport.allow_fallbacks is False,
            "provider pin/fallback drift",
        )
        route.transport_payload(inference.requirements)
        follow.require(
            route.price_ceiling is not None
            and route.max_context_tokens >= follow.MAX_CONTEXT_TOKENS
            and route.max_output_tokens >= follow.MAX_OUTPUT_TOKENS,
            "route envelope",
        )
        competitors = [
            r
            for r in inference.routes.values()
            if r.model == route.model
            and r.provider == "openrouter"
            and r.is_approved(inference.requirements)
        ]
        follow.require(len(competitors) == 1, "ambiguous approved route for exact model")
        follow.require(
            model_routing.load_model_routing().model(route.model) is not None,
            "evaluation catalog must declare the exact model",
        )
        follow.require(
            not model_routing.model_parameter_presets(route.model, "routine"),
            "explicit-effort comparison forbids catalog preset injection",
        )
        routes[label] = route
    return routes


def evidence_document(ctx: follow.RunContext) -> dict[str, Any]:
    def extension(a: follow.Attempt, _case: Any, entry: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(a, UpgradeAttempt)
        extra: dict[str, Any] = {
            "phase": a.phase,
            "setting_label": a.setting_label,
            "candidate_model": ctx.routes[a.candidate_label].model,
            "evidence_class": "production_streaming"
            if a.is_probe
            else ("reused_regression" if a.phase == "regression" else "diagnostic"),
        }
        if a.is_probe:
            extra["stream_observation"] = entry.get("stream_observation")
        return extra

    return follow.pending_results(
        ctx.state, ctx.cases, ctx.schedule, profile=ctx.profile, attempt_extension=extension
    )


async def preflight(ctx: follow.RunContext) -> dict[str, Any]:
    resolved = await ctx.service.resolve(ctx.account)
    exposure = await follow.reliability.read_ledger_exposure(ctx.pool, ctx.account, ctx.period)
    follow.require_accounting(exposure.open_holds == 0, "open reservations prevent experiment")
    bound = 0
    for a in ctx.schedule:
        assert isinstance(a, UpgradeAttempt)
        route, case = ctx.routes[a.candidate_label], ctx.cases[a.case_id]
        follow.require(
            model_routing.supports_reasoning_effort(route.model, a.effort),
            f"unsupported effort for {a.attempt_id}",
        )
        params = follow.request_for(case, route, a, [{"role": "user", "content": case.prompt}])
        follow.verify_candidate(case, route, resolved, params)
        follow.require(params.get("reasoning_effort") == a.effort, "outbound effort drift")
        bound += follow.ceiling_bound(route) * a.max_calls
    follow.require_accounting(
        bound <= ctx.profile.incremental_cap_microusd,
        "schedule planning bound exceeds incremental cap",
    )
    follow.require_accounting(
        exposure.total_microusd + bound <= ctx.account_ceiling_microusd,
        "schedule exceeds remaining September allowance",
    )
    follow.ensure_funded_window(ctx.service, ctx.period, 100)
    return {
        "experiment": ctx.profile.experiment,
        "identity": ctx.identity,
        "attempts": len(ctx.schedule),
        "dispatch_bound": ctx.profile.dispatch_bound,
        "planning_bound_microusd": bound,
        "ledger_exposure_microusd": exposure.total_microusd,
        "open_holds": exposure.open_holds,
        "dry_run": True,
    }


def render_report(document: dict[str, Any], spec: Any, fixtures: Any, directory: Path) -> None:
    summary = specs.result_summary(document, spec)
    follow.write_results(directory / "summary.json", summary)
    packet, verdicts, mapping = specs.review_packet(document, spec, fixtures)
    follow.write_results(directory / "review-map.json", mapping)
    follow.write_results(directory / "human-verdicts.json", {"verdicts": verdicts})
    fd = os.open(
        directory / "human-review.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as stream:
        stream.write(packet)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def account_lock(directory: Path, account: uuid.UUID) -> Any:
    import fcntl

    parent = directory.parent
    follow.require(parent.stat().st_mode & 0o077 == 0, "run root must be private (0700)")
    fd = os.open(
        parent / f".upgrade-account-{account}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


async def run(args: argparse.Namespace) -> int:
    spec, raw, fixtures, schedule = load_plan(args.manifest.resolve())
    profile = profile_for(raw, schedule, args.manifest.resolve())
    directory = args.run_dir.resolve()
    follow.require(
        directory.is_dir() and directory.stat().st_mode & 0o077 == 0,
        "existing private durable run directory required (0700)",
    )
    state_path = directory / "state.json"
    if args.report:
        state = json.loads(state_path.read_text())
        follow.require(
            state.get("version") == profile.state_version
            and state["identity"]["manifest_sha256"] == follow.live.digest(args.manifest),
            "report manifest/state mismatch",
        )
        follow.require(
            set(state["attempts"]) <= {a.attempt_id for a in schedule}, "unplanned attempt"
        )

        # Offline report uses the frozen policy, not live admission or credentials.
        def extension(a: follow.Attempt, _case: Any, entry: dict[str, Any]) -> dict[str, Any]:
            assert isinstance(a, UpgradeAttempt)
            extra: dict[str, Any] = {"phase": a.phase, "setting_label": a.setting_label}
            if a.is_probe:
                extra["stream_observation"] = entry.get("stream_observation")
            return extra

        document = follow.pending_results(
            state,
            fixtures.by_id(),
            schedule,
            profile=profile,
            attempt_extension=extension,
        )
        render_report(document, spec, fixtures, directory)
        return 0
    follow.require(args.account is not None and args.phase in PHASES, "account and phase required")
    assert isinstance(args.account, uuid.UUID)
    # New manifests may name a newly authorized funded month. The immutable
    # identity and every dispatch still forbid crossing that chosen boundary.
    follow.require(
        datetime.strptime(raw["period"], "%Y-%m").strftime("%Y-%m") == raw["period"],
        "funded period must be YYYY-MM",
    )
    commercial_path, inference_path = follow.live.policy_paths()
    follow.live.validate_commercial(load_policy(commercial_path))
    routes = qualified_routes(raw, load_inference_policy(inference_path))
    env = Settings.explicit_evaluation_environment()
    follow.require(
        bool(env.get("DATABASE_URL")) and get_settings().database_url == env["DATABASE_URL"],
        "explicit isolated evaluation DATABASE_URL required",
    )
    if not args.dry_run:
        follow.require(bool(get_settings().openrouter_api_key), "OpenRouter credential missing")
    pool = await asyncpg.create_pool(dsn=env["DATABASE_URL"], min_size=1, max_size=2)
    try:
        service = EntitlementService(pool)
        database = await follow.live.validate_database(pool, args.account, service, raw["period"])
        identity = {
            "experiment": profile.experiment,
            "account": str(args.account),
            "period_key": raw["period"],
            "database": database,
            "database_url_sha256": hashlib.sha256(env["DATABASE_URL"].encode()).hexdigest(),
            "manifest_sha256": follow.live.digest(args.manifest),
            "fixtures_sha256": fixtures.sha256,
            "schedule_sha256": follow.schedule_fingerprint(schedule),
            "implementation_sha256": follow.implementation_fingerprint(
                extra_paths=profile.implementation_paths
            ),
            "commercial_sha256": follow.live.digest(commercial_path),
            "inference_sha256": follow.live.digest(inference_path),
        }
        ctx = follow.RunContext(
            pool,
            args.account,
            service,
            raw["period"],
            identity,
            {},
            state_path,
            schedule,
            fixtures.by_id(),
            routes,
            commercial_path,
            inference_path,
            follow.reliability.account_ceiling(await service.resolve(args.account)),
            profile,
        )
        if args.dry_run:
            print(json.dumps(await preflight(ctx), indent=2))
            return 0
        with (
            account_lock(directory, args.account),
            follow.locked_state(
                state_path,
                args.account,
                identity,
                profile=profile,
            ) as state,
        ):
            ctx.state = state
            if not state["attempts"]:
                await preflight(ctx)
            follow.phase_admission(state, schedule, args.phase, profile=profile)
            if args.phase == "streaming":
                follow.require(
                    all(a.attempt_id in state["attempts"] for a in schedule if not a.is_probe),
                    "streaming requires completed benchmark phases",
                )
            exposure = await follow.reliability.read_ledger_exposure(
                pool, args.account, raw["period"]
            )
            follow.freeze_baseline(state, state_path, exposure, raw["period"], profile=profile)
            follow.require_exclusive(state, exposure)
            follow.record_stage(state, args.phase, identity)
            result_path = directory / f"{args.phase}-results.json"
            follow.require(not result_path.exists(), "phase results already exist; no replay")
            follow.write_state(state_path, state)
            try:
                for a in schedule:
                    if a.phase != args.phase or a.attempt_id in state["attempts"]:
                        continue
                    if a.is_probe:
                        await streaming.run_stream_attempt(ctx, a)
                    else:
                        await follow.run_attempt(ctx, a)
                    current = await follow.reliability.read_ledger_exposure(
                        pool, args.account, raw["period"]
                    )
                    follow.require_exclusive(state, current)
            finally:
                follow.write_results(result_path, evidence_document(ctx))
            if len(state["attempts"]) == len(schedule):
                render_report(evidence_document(ctx), spec, fixtures, directory)
    finally:
        await pool.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--account", type=uuid.UUID)
    parser.add_argument("--phase", choices=tuple(PHASES))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (
        follow.FollowupError,
        specs.SpecError,
        EntitlementsError,
        follow.reliability.ReliabilityError,
        OSError,
        ValueError,
        compute_runtime.ComputeUnavailable,
        asyncpg.PostgresError,
    ) as exc:
        print(f"upgrade run rejected: {follow.reliability.sanitize(str(exc))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("upgrade run interrupted; recorded attempts will not be replayed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
