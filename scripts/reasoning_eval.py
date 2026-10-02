"""Bounded, manifest-driven runner for the reasoning-routing evaluation (Package B, B2).

Implements the calibration run and pilot defined in docs/REASONING_EVAL_PROTOCOL.md
on the frozen corpus v1 (tests/fixtures/reasoning_eval/). Execution, accounting and
journaling reuse the shared executor in ``scripts/model_routing_followup.py``: every
call goes through ``guarded_completion`` on one pinned, independently qualified
route, intent is fsynced before dispatch, each call holds its own reservation and
settlement, admission is checked against the manifest's incremental cap and the
account ceiling before every call, and a recorded attempt is never replayed.

This module adds only what the corpus needs: a strict loader for the corpus schema
(multi-turn ``history``, ``split``/``slice``/``stratum``, per-case call limits), a
manifest naming configurations as (candidate, explicit effort) pairs, a whole-run
planning bound, a calibration summary with a pilot projection, and a model-blinded
human review packet. Nothing here scores answers: every verdict is pending.

Usage, with explicitly exported evaluation DATABASE_URL and policy/catalog paths:

    PYTHONPATH=. uv run python scripts/reasoning_eval.py \\
        --manifest tests/fixtures/reasoning_eval/b2_calibration_20261002.json \\
        --run-dir <private-durable-directory> --account <isolated-account-uuid> --dry-run

Remove ``--dry-run`` only for an authorized run. ``--report`` re-renders the summary
and review packet from retained state without inference or credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import secrets
import statistics
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import compute_runtime, model_routing  # noqa: E402
from orchestrator.config import Settings, get_settings  # noqa: E402
from orchestrator.entitlements.errors import EntitlementsError  # noqa: E402
from orchestrator.entitlements.policy import (  # noqa: E402
    RoutePolicy,
    load_inference_policy,
    load_policy,
)
from orchestrator.entitlements.service import EntitlementService  # noqa: E402
from scripts import model_routing_followup as follow  # noqa: E402
from scripts import model_upgrade as upgrade  # noqa: E402

JsonObject = dict[str, Any]

CORPUS_ARTIFACT_VERSION: Final[str] = "reasoning-eval-corpus-v1"
MANIFEST_SCHEMA_VERSION: Final[int] = 1
STATE_VERSION: Final[str] = "reasoning-eval-state/1"
RESULTS_ARTIFACT_VERSION: Final[str] = "reasoning-eval-results/1"
SUMMARY_VERSION: Final[str] = "reasoning-eval-summary/1"

SPLITS: Final[tuple[str, ...]] = ("dev", "val", "test")
SLICES: Final[tuple[str, ...]] = (
    "everyday",
    "coding",
    "planning",
    "synthesis",
    "followup",
    "topic_shift",
    "quoted_injection",
    "revealed_by_tools",
    "missing_info",
    "non_english",
)
STRATA: Final[tuple[str, ...]] = ("easy", "hard", "deceptively_hard", "long_easy")
HISTORY_ROLES: Final[frozenset[str]] = frozenset({"user", "assistant"})

#: Per-case call limits (the protocol's "maximum calls per attempt"): a tool case
#: may use two tool rounds and then answer; any other case answers in one call.
TOOL_CASE_MAX_CALLS: Final[int] = 3
TOOL_CASE_MAX_TOOL_CALLS: Final[int] = 4
PLAIN_CASE_MAX_CALLS: Final[int] = 1

#: The pilot shape from the protocol, used only to project its spend from the
#: calibration measurements. It is not an admission bound.
PILOT_SPLITS: Final[tuple[str, ...]] = ("dev", "val")
PILOT_REPEATS: Final[int] = 3

RESULTS_NOTES: Final[str] = (
    "Human adjudication required: every verdict is pending and no reviewer is recorded. "
    "Single non-streaming calls with no system prompt, on frozen synthetic cases; this is "
    "not the production chat path. Unknown costs stay null, never zero. Raw provider "
    "payloads stay in the private state file."
)


class EvalError(follow.FollowupError):
    """A corpus, manifest or report rule refused the run."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvalError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvalCase(follow.CaseFixture):
    """A corpus v1 case in the shared executor's shape, plus its review strata."""

    split: str = ""
    work_slice: str = ""
    stratum: str = ""


_CASE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "case_id",
        "split",
        "slice",
        "stratum",
        "latency_class",
        "history",
        "prompt",
        "tools",
        "tool_responses",
        "rubric",
    }
)


def _parse_history(value: object, where: str) -> tuple[JsonObject, ...]:
    _require(isinstance(value, list), f"{where}: expected a list")
    assert isinstance(value, list)
    turns: list[JsonObject] = []
    for index, turn in enumerate(value):
        _require(
            isinstance(turn, dict) and set(turn) == {"role", "content"},
            f"{where}[{index}]: expected exactly role and content",
        )
        assert isinstance(turn, dict)
        _require(turn["role"] in HISTORY_ROLES, f"{where}[{index}].role: user or assistant")
        _require(
            isinstance(turn["content"], str) and bool(turn["content"].strip()),
            f"{where}[{index}].content: expected non-empty text",
        )
        turns.append({"role": turn["role"], "content": turn["content"]})
    _require(
        not turns or turns[-1]["role"] == "assistant",
        f"{where}: history must end with an assistant turn before the prompt",
    )
    return tuple(turns)


def _parse_rubric(value: object, where: str) -> JsonObject:
    _require(
        isinstance(value, dict) and set(value) == {"acceptable", "hard_violations", "notes"},
        f"{where}: expected acceptable, hard_violations and notes",
    )
    assert isinstance(value, dict)
    for key in ("acceptable", "hard_violations"):
        items = value[key]
        _require(
            isinstance(items, list)
            and all(isinstance(item, str) and item.strip() for item in items),
            f"{where}.{key}: expected a list of statements",
        )
    _require(bool(value["acceptable"]), f"{where}.acceptable: expected at least one assertion")
    _require(isinstance(value["notes"], str), f"{where}.notes: expected text")
    return {
        "acceptable": list(value["acceptable"]),
        "hard_violations": list(value["hard_violations"]),
        "notes": value["notes"],
    }


def _parse_case(value: object, index: int) -> EvalCase:
    where = f"cases[{index}]"
    _require(isinstance(value, dict), f"{where}: expected an object")
    assert isinstance(value, dict)
    _require(set(value) == _CASE_KEYS, f"{where}: unexpected or missing keys")
    case_id = value["case_id"]
    _require(isinstance(case_id, str) and bool(case_id.strip()), f"{where}.case_id")
    _require(value["split"] in SPLITS, f"{where}.split: expected one of {SPLITS}")
    _require(value["slice"] in SLICES, f"{where}.slice: unknown slice")
    _require(value["stratum"] in STRATA, f"{where}.stratum: unknown stratum")
    _require(
        value["latency_class"] in follow.LATENCY_CLASSES,
        f"{where}.latency_class: expected one of {follow.LATENCY_CLASSES}",
    )
    prompt = value["prompt"]
    _require(isinstance(prompt, str) and bool(prompt.strip()), f"{where}.prompt: expected text")
    raw_tools = value["tools"]
    _require(isinstance(raw_tools, list), f"{where}.tools: expected a list")
    tools = tuple(
        follow._parse_tool(item, f"{where}.tools[{i}]")  # pyright: ignore[reportPrivateUsage]
        for i, item in enumerate(raw_tools)
    )
    names = [tool.name for tool in tools]
    _require(len(set(names)) == len(names), f"{where}.tools: duplicate tool names")
    tool_responses = follow._parse_tool_responses(  # pyright: ignore[reportPrivateUsage]
        value["tool_responses"], f"{where}.tool_responses", tools
    )
    return EvalCase(
        case_id=case_id,
        stage=value["split"],
        latency_class=value["latency_class"],
        prompt=prompt,
        max_calls=TOOL_CASE_MAX_CALLS if tools else PLAIN_CASE_MAX_CALLS,
        max_tool_calls=TOOL_CASE_MAX_TOOL_CALLS if tools else 0,
        tools=tools,
        tool_responses=tool_responses,
        schema=None,
        expected=_parse_rubric(value["rubric"], f"{where}.rubric"),
        sha256=hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        history=_parse_history(value["history"], f"{where}.history"),
        split=value["split"],
        work_slice=value["slice"],
        stratum=value["stratum"],
    )


def load_corpus(path: Path, expected_sha256: str) -> follow.FixtureSet:
    """Load the frozen corpus, refusing any byte that differs from the frozen digest."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    _require(digest == expected_sha256, "corpus digest drift")
    document = json.loads(raw)
    _require(isinstance(document, dict), "corpus: expected an object")
    _require(
        document.get("artifact_version") == CORPUS_ARTIFACT_VERSION,
        "corpus: unexpected artifact version",
    )
    raw_cases = document.get("cases")
    _require(isinstance(raw_cases, list) and bool(raw_cases), "corpus: expected cases")
    assert isinstance(raw_cases, list)
    cases = tuple(_parse_case(item, index) for index, item in enumerate(raw_cases))
    ids = [case.case_id for case in cases]
    _require(len(set(ids)) == len(ids), "corpus: duplicate case ids")
    return follow.FixtureSet(cases=cases, sha256=digest)


# ---------------------------------------------------------------------------
# Manifest and schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Configuration:
    label: str
    candidate: str
    effort: str


@dataclass(frozen=True, slots=True)
class Manifest:
    experiment: str
    period: str
    corpus_path: Path
    corpus_sha256: str
    splits: tuple[str, ...]
    repeats: int
    candidates: tuple[JsonObject, ...]
    configurations: tuple[Configuration, ...]
    incremental_cap_microusd: int
    raw: JsonObject
    sha256: str


_MANIFEST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "experiment",
        "notes",
        "period",
        "corpus",
        "splits",
        "repeats",
        "candidates",
        "configurations",
        "bounds",
    }
)


def load_manifest(path: Path) -> Manifest:
    raw_bytes = path.read_bytes()
    raw = json.loads(raw_bytes)
    _require(isinstance(raw, dict) and set(raw) == _MANIFEST_KEYS, "manifest: unexpected keys")
    _require(raw["schema_version"] == MANIFEST_SCHEMA_VERSION, "manifest: schema version")
    experiment = raw["experiment"]
    _require(
        isinstance(experiment, str)
        and bool(experiment)
        and all(ch.isalnum() or ch in "-." for ch in experiment),
        "manifest.experiment: expected a plain identifier",
    )
    period = raw["period"]
    _require(
        isinstance(period, str)
        and len(period) == 7
        and datetime.strptime(period, "%Y-%m").strftime("%Y-%m") == period,
        "manifest.period: expected YYYY-MM",
    )
    corpus = raw["corpus"]
    _require(
        isinstance(corpus, dict) and set(corpus) == {"path", "sha256"},
        "manifest.corpus: expected path and sha256",
    )
    corpus_path = (ROOT / corpus["path"]).resolve()
    _require(corpus_path.is_relative_to(ROOT), "manifest.corpus.path: must stay in the repo")
    splits = raw["splits"]
    _require(
        isinstance(splits, list)
        and bool(splits)
        and len(set(splits)) == len(splits)
        and all(split in SPLITS for split in splits),
        "manifest.splits: expected distinct corpus splits",
    )
    repeats = raw["repeats"]
    _require(
        isinstance(repeats, int) and not isinstance(repeats, bool) and 1 <= repeats <= 10,
        "manifest.repeats: expected 1..10",
    )
    candidates = raw["candidates"]
    _require(isinstance(candidates, list) and bool(candidates), "manifest.candidates")
    labels: set[str] = set()
    for index, candidate in enumerate(candidates):
        _require(
            isinstance(candidate, dict)
            and set(candidate) == {"label", "model", "route_id", "provider_pin"}
            and all(isinstance(item, str) and item for item in candidate.values()),
            f"manifest.candidates[{index}]: expected label, model, route_id, provider_pin",
        )
        _require(candidate["label"] not in labels, "manifest.candidates: duplicate label")
        labels.add(candidate["label"])
    configurations: list[Configuration] = []
    for index, configuration in enumerate(raw["configurations"]):
        _require(
            isinstance(configuration, dict)
            and set(configuration) == {"label", "candidate", "effort"}
            and all(isinstance(item, str) and item for item in configuration.values()),
            f"manifest.configurations[{index}]: expected label, candidate, effort",
        )
        _require(
            configuration["candidate"] in labels,
            f"manifest.configurations[{index}]: unknown candidate",
        )
        configurations.append(Configuration(**configuration))
    config_labels = [configuration.label for configuration in configurations]
    _require(
        bool(config_labels) and len(set(config_labels)) == len(config_labels),
        "manifest.configurations: expected distinct labels",
    )
    _require(
        len({(c.candidate, c.effort) for c in configurations}) == len(configurations),
        "manifest.configurations: duplicate candidate and effort",
    )
    bounds = raw["bounds"]
    _require(
        isinstance(bounds, dict) and set(bounds) == {"incremental_cap_microusd"},
        "manifest.bounds: expected incremental_cap_microusd",
    )
    cap = bounds["incremental_cap_microusd"]
    _require(
        isinstance(cap, int) and not isinstance(cap, bool) and 0 < cap <= follow.live.CAP_MICROUSD,
        "manifest.bounds.incremental_cap_microusd: expected a positive cap within USD 25",
    )
    return Manifest(
        experiment=experiment,
        period=period,
        corpus_path=corpus_path,
        corpus_sha256=corpus["sha256"],
        splits=tuple(splits),
        repeats=repeats,
        candidates=tuple(candidates),
        configurations=tuple(configurations),
        incremental_cap_microusd=cap,
        raw=raw,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class EvalAttempt(follow.Attempt):
    configuration: str = ""
    effort_value: str = ""

    @property
    def effort(self) -> str:
        return self.effort_value


def build_schedule(manifest: Manifest, fixtures: follow.FixtureSet) -> tuple[EvalAttempt, ...]:
    """Expand the manifest into its recorded schedule.

    Repeat-major, then case in corpus order; the configuration order rotates by one
    per case so no configuration always runs first on a case (for example straight
    after a provider's cold start).
    """
    selected = [case for case in fixtures.cases if case.stage in manifest.splits]
    _require(bool(selected), "schedule: no cases in the selected splits")
    attempts: list[EvalAttempt] = []
    configurations = manifest.configurations
    for repeat in range(1, manifest.repeats + 1):
        for position, case in enumerate(selected):
            offset = position % len(configurations)
            for configuration in configurations[offset:] + configurations[:offset]:
                attempts.append(
                    EvalAttempt(
                        attempt_id=f"{case.case_id}-{configuration.label}-r{repeat}",
                        case_id=case.case_id,
                        stage=case.stage,
                        candidate_label=configuration.candidate,
                        condition="explicit",
                        repeat=repeat,
                        latency_class=case.latency_class,
                        max_calls=case.max_calls,
                        max_tool_calls=case.max_tool_calls,
                        configuration=configuration.label,
                        effort_value=configuration.effort,
                    )
                )
    ids = [attempt.attempt_id for attempt in attempts]
    _require(len(set(ids)) == len(ids), "schedule: duplicate attempt ids")
    return tuple(attempts)


def profile_for(
    manifest: Manifest, schedule: Sequence[EvalAttempt], manifest_path: Path
) -> follow.ExperimentProfile:
    return follow.ExperimentProfile(
        experiment=f"reasoning-eval/{manifest.experiment}",
        state_version=STATE_VERSION,
        results_artifact_version=RESULTS_ARTIFACT_VERSION,
        generated_by="scripts/reasoning_eval.py",
        results_notes=RESULTS_NOTES,
        lock_holder_label="reasoning evaluation runner",
        incremental_cap_microusd=manifest.incremental_cap_microusd,
        total_attempts=len(schedule),
        dispatch_bound=sum(attempt.max_calls for attempt in schedule),
        implementation_paths=(
            Path(__file__),
            Path(upgrade.__file__),
            manifest_path,
            manifest.corpus_path,
        ),
        # One phase per manifest: a calibration and a pilot are separately
        # identified experiments, never two phases of one state.
        phase_stages={"run": "run"},
        phase_labels={"run": "run"},
        gated_phase="",
        gate_stage="",
    )


# ---------------------------------------------------------------------------
# Planning bound
# ---------------------------------------------------------------------------


def first_request(case: follow.CaseFixture, route: RoutePolicy, attempt: EvalAttempt) -> JsonObject:
    params = follow.request_for(case, route, attempt, follow.initial_messages(case))
    _require(params.get("reasoning_effort") == attempt.effort, "outbound effort drift")
    return params


def attempt_bound(case: follow.CaseFixture, route: RoutePolicy, attempt: EvalAttempt) -> int:
    """The most one attempt can be admitted to charge.

    Matches the executor's own admission: the first call's reservation hold plus the
    context-ceiling bound for every further call the case allows.
    """
    params = first_request(case, route, attempt)
    size = compute_runtime._request_bound(params)  # pyright: ignore[reportPrivateUsage]
    first = route.estimate_microusd(size.bound, int(params["max_tokens"]))
    return first + (attempt.max_calls - 1) * follow.ceiling_bound(route)


def planning_bound(
    schedule: Sequence[EvalAttempt],
    cases: Mapping[str, follow.CaseFixture],
    routes: Mapping[str, RoutePolicy],
) -> dict[str, int]:
    """Whole-run worst case per configuration, in microusd."""
    totals: dict[str, int] = {}
    for attempt in schedule:
        bound = attempt_bound(cases[attempt.case_id], routes[attempt.candidate_label], attempt)
        totals[attempt.configuration] = totals.get(attempt.configuration, 0) + bound
    return totals


async def preflight(ctx: follow.RunContext) -> JsonObject:
    """Read-only whole-schedule check: eligibility, efforts, context and both caps."""
    resolved = await ctx.service.resolve(ctx.account)
    exposure = await follow.reliability.read_ledger_exposure(ctx.pool, ctx.account, ctx.period)
    follow.require_accounting(exposure.open_holds == 0, "open reservations prevent experiment")
    for attempt in ctx.schedule:
        assert isinstance(attempt, EvalAttempt)
        route, case = ctx.routes[attempt.candidate_label], ctx.cases[attempt.case_id]
        _require(
            model_routing.supports_reasoning_effort(route.model, attempt.effort),
            f"unsupported effort for {attempt.attempt_id}",
        )
        params = first_request(case, route, attempt)
        held = follow.verify_candidate(case, route, resolved, params)
        planned = attempt_bound(case, route, attempt) - (attempt.max_calls - 1) * (
            follow.ceiling_bound(route)
        )
        _require(
            held == planned, f"planning bound disagrees with the runtime hold for {case.case_id}"
        )
    by_configuration = planning_bound(
        tuple(a for a in ctx.schedule if isinstance(a, EvalAttempt)), ctx.cases, ctx.routes
    )
    bound = sum(by_configuration.values())
    follow.require_accounting(
        bound <= ctx.profile.incremental_cap_microusd,
        "schedule planning bound exceeds the incremental cap",
    )
    follow.require_accounting(
        exposure.total_microusd + bound <= ctx.account_ceiling_microusd,
        "schedule exceeds the remaining account allowance",
    )
    follow.ensure_funded_window(ctx.service, ctx.period, 100)
    return {
        "experiment": ctx.profile.experiment,
        "identity": ctx.identity,
        "attempts": len(ctx.schedule),
        "dispatch_bound": ctx.profile.dispatch_bound,
        "planning_bound_microusd": bound,
        "planning_bound_microusd_by_configuration": dict(sorted(by_configuration.items())),
        "incremental_cap_microusd": ctx.profile.incremental_cap_microusd,
        "account_ceiling_microusd": ctx.account_ceiling_microusd,
        "ledger_exposure_microusd": exposure.total_microusd,
        "open_holds": exposure.open_holds,
        "dry_run": True,
    }


# ---------------------------------------------------------------------------
# Results, summary and review packet
# ---------------------------------------------------------------------------


def _reasoning_details_emitted(entry: Mapping[str, Any]) -> int:
    """Calls whose response carried provider reasoning metadata (#343 evidence).

    The shared executor forwards the full assistant message on tool rounds, so
    emitted metadata is never dropped inside this harness; production continuation
    is a separate question.
    """
    emitted = 0
    for call in entry.get("calls") or ():
        if not isinstance(call, dict):
            continue
        raw = call.get("raw_response")
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and message.get("reasoning_details"):
                emitted += 1
    return emitted


def results_document(
    state: Mapping[str, Any],
    fixtures: follow.FixtureSet,
    schedule: Sequence[EvalAttempt],
    profile: follow.ExperimentProfile,
    manifest: Manifest,
    exposure: follow.reliability.LedgerExposure | None = None,
) -> JsonObject:
    cases = fixtures.by_id()

    def extension(attempt: follow.Attempt, case: Any, entry: JsonObject) -> JsonObject:
        assert isinstance(attempt, EvalAttempt) and isinstance(case, EvalCase)
        return {
            "configuration": attempt.configuration,
            "split": case.split,
            "slice": case.work_slice,
            "stratum": case.stratum,
            "reasoning_details_emitted_calls": _reasoning_details_emitted(entry),
        }

    return follow.pending_results(
        state,
        cases,
        schedule,
        exposure,
        profile=profile,
        attempt_extension=extension,
        document_extension={"manifest_sha256": manifest.sha256},
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_tokens(call: Mapping[str, Any], key: str) -> int | None:
    usage = call.get("usage")
    return _int_or_none(usage.get(key)) if isinstance(usage, dict) else None


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def _token_stats(values: Sequence[int]) -> JsonObject:
    if not values:
        return {"n": 0, "mean": None, "median": None, "max": None}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 1),
        "median": statistics.median(values),
        "max": max(values),
    }


TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"completed", "failed"})


def _call_accounting(call: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    """(provider usage reported, settled at the full hold, ledger charge unknown).

    These are separate risks. A call whose provider omitted usage settles at its full
    reservation hold, so its ledger charge is known but conservative: that frequency is
    the protocol's ``f_unknown``. A missing ledger charge is a different gap (the
    executor keeps the full bound in its own accounting for such a call).
    """
    # Same test as the runtime's own usage validation (compute_runtime._usage_counts):
    # anything else settles at the full reservation hold.
    usage_reported = all(
        (value := _usage_tokens(call, key)) is not None and value >= 0
        for key in ("prompt_tokens", "completion_tokens")
    )
    charge = _int_or_none(call.get("account_charge_microusd"))
    bound = _int_or_none(call.get("reservation_bound_microusd"))
    full_hold = charge is not None and bound is not None and bound > 0 and charge >= bound
    return usage_reported, full_hold, charge is None


def _fraction(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def summarize(
    document: Mapping[str, Any],
    fixtures: follow.FixtureSet,
    schedule: Sequence[EvalAttempt],
) -> JsonObject:
    """Measured tokens, calls, cost and latency per configuration, and a pilot projection.

    Purely observational, and checked against the frozen schedule: every planned
    configuration is reported, including ones with no recorded attempt, with its
    planned, recorded, terminal, stopped and missing counts. Failed and unanswered
    attempts stay in every denominator; an unknown charge is never priced at zero.
    The pilot projection is withheld unless every planned attempt is recorded and
    terminal and every recorded charge is known; ``incomplete_reasons`` says why.
    """
    rows = {
        str(row.get("attempt_id")): row
        for row in document.get("attempts", [])
        if isinstance(row, dict)
    }
    pilot_cases = sum(1 for case in fixtures.cases if case.stage in PILOT_SPLITS)
    pilot_attempts = pilot_cases * PILOT_REPEATS
    planned: dict[str, list[EvalAttempt]] = {}
    for attempt in schedule:
        planned.setdefault(attempt.configuration, []).append(attempt)
    unplanned = sorted(set(rows) - {attempt.attempt_id for attempt in schedule})
    configurations: dict[str, JsonObject] = {}
    projected_mean_total = 0
    projected_max_total = 0
    incomplete: list[str] = []
    if unplanned:
        incomplete.append(f"{len(unplanned)} recorded attempt(s) not in the schedule")
    for label, attempts in planned.items():
        items = [rows[a.attempt_id] for a in attempts if a.attempt_id in rows]
        missing = sorted(a.attempt_id for a in attempts if a.attempt_id not in rows)
        calls = [call for row in items for call in row.get("calls", []) if isinstance(call, dict)]
        accounting = [_call_accounting(call) for call in calls]
        charges = [_int_or_none(row.get("account_charge_microusd")) for row in items]
        known = [charge for charge in charges if charge is not None]
        unknown = len(charges) - len(known)
        terminal = sum(1 for row in items if row.get("status") in TERMINAL_STATUSES)
        stopped = sum(
            1 for row in items if row.get("stop") or row.get("status") not in TERMINAL_STATUSES
        )
        latencies = [
            float(row["latency_seconds"])
            for row in items
            if isinstance(row.get("latency_seconds"), (int, float))
        ]
        statuses: dict[str, int] = {}
        for row in items:
            statuses[str(row.get("status"))] = statuses.get(str(row.get("status")), 0) + 1
        covered = not missing and terminal == len(attempts) and not unknown and bool(items)
        if missing:
            incomplete.append(f"{label}: {len(missing)} planned attempt(s) not recorded")
        if stopped:
            incomplete.append(f"{label}: {stopped} attempt(s) stopped or interrupted")
        if unknown:
            incomplete.append(f"{label}: {unknown} attempt(s) with an unknown ledger charge")
        mean_charge = round(statistics.fmean(known)) if covered else None
        max_charge = max(known) if covered else None
        if mean_charge is not None and max_charge is not None:
            projected_mean_total += mean_charge * pilot_attempts
            projected_max_total += max_charge * pilot_attempts
        configurations[label] = {
            "planned_attempts": len(attempts),
            "recorded_attempts": len(items),
            "terminal_attempts": terminal,
            "stopped_attempts": stopped,
            "missing_attempts": len(missing),
            "missing_attempt_ids": missing,
            "attempts_without_dispatch": sum(1 for row in items if not row.get("calls")),
            "statuses": dict(sorted(statuses.items())),
            "calls": len(calls),
            "calls_per_attempt_mean": round(len(calls) / len(items), 3) if items else None,
            "prompt_tokens": _token_stats(
                [v for c in calls if (v := _usage_tokens(c, "prompt_tokens")) is not None]
            ),
            "completion_tokens": _token_stats(
                [v for c in calls if (v := _usage_tokens(c, "completion_tokens")) is not None]
            ),
            "reasoning_tokens": _token_stats(
                [v for c in calls if (v := _int_or_none(c.get("reasoning_tokens"))) is not None]
            ),
            "reasoning_tokens_unreported_calls": sum(
                1 for c in calls if _int_or_none(c.get("reasoning_tokens")) is None
            ),
            "truncated_attempts": sum(
                1
                for row in items
                for failure in row.get("task_failures", [])
                if isinstance(failure, dict) and failure.get("kind") == "truncated_response"
            ),
            "calls_without_provider_usage": sum(1 for usage, _, _ in accounting if not usage),
            "calls_settled_at_full_hold": sum(1 for _, full, _ in accounting if full),
            "calls_with_unknown_charge": sum(1 for _, _, gap in accounting if gap),
            "f_unknown_usage": _fraction(
                sum(1 for usage, _, _ in accounting if not usage), len(calls)
            ),
            "f_full_hold": _fraction(sum(1 for _, full, _ in accounting if full), len(calls)),
            "f_unknown_charge": _fraction(sum(1 for _, _, gap in accounting if gap), len(calls)),
            "account_charge_microusd_total": sum(known) if items and not unknown else None,
            "account_charge_microusd_mean_per_attempt": mean_charge,
            "account_charge_microusd_max_per_attempt": max_charge,
            "attempts_with_unknown_charge": unknown,
            "latency_seconds_median": statistics.median(latencies) if latencies else None,
            "latency_seconds_p95": _percentile(latencies, 0.95),
            "pilot_projection_microusd": None
            if mean_charge is None or max_charge is None
            else {"at_mean": mean_charge * pilot_attempts, "at_max": max_charge * pilot_attempts},
        }
    complete = not incomplete and bool(planned)
    known_rows = [_int_or_none(row.get("account_charge_microusd")) for row in rows.values()]
    return {
        "summary_version": SUMMARY_VERSION,
        "experiment": document.get("experiment"),
        "manifest_sha256": document.get("manifest_sha256"),
        "planned_attempts": len(schedule),
        "recorded_attempts": len(rows),
        "unplanned_attempt_ids": unplanned,
        "account_charge_microusd_total": sum(v for v in known_rows if v is not None)
        if known_rows and all(v is not None for v in known_rows)
        else None,
        "configurations": configurations,
        "pilot_projection": {
            "cases": pilot_cases,
            "repeats": PILOT_REPEATS,
            "complete": complete,
            "incomplete_reasons": incomplete,
            "at_mean_microusd": projected_mean_total if complete else None,
            "at_max_microusd": projected_max_total if complete else None,
            "method": (
                "Per configuration, the calibration's mean (and maximum) settled charge per "
                "attempt times the pilot's attempts, only when every planned calibration "
                "attempt is recorded, terminal and has a known charge. Full-hold settlements "
                "are included at their charged amount. Calibration covers dev cases only; val "
                "cases are unseen, so the at_max figure is the planning reference."
            ),
        },
        "notes": "Semantic verdicts are pending; nothing here measures answer quality.",
    }


def review_packet(
    document: Mapping[str, Any], fixtures: follow.FixtureSet
) -> tuple[str, list[JsonObject], dict[str, str]]:
    """A model-blinded packet: random opaque labels, grouped by case, no configuration.

    Returns the Markdown packet, the pending verdict template and, separately, the
    label-to-attempt map that must not travel with the packet. Answers can still
    reveal their model through style or self-reference; the packet removes only the
    recorded identities, effort, cost and latency.
    """
    cases = fixtures.by_id()
    rows = [row for row in document.get("attempts", []) if isinstance(row, dict)]
    labels: dict[str, str] = {}
    used: set[str] = set()
    for row in rows:
        while True:
            label = f"R-{secrets.token_hex(4)}"
            if label not in used:
                used.add(label)
                break
        labels[str(row["attempt_id"])] = label
    ordered = sorted(rows, key=lambda row: (str(row["case_id"]), labels[str(row["attempt_id"])]))
    lines = [
        "# Reasoning evaluation human review packet",
        "",
        "Judge each answer against its case rubric only. An answer is **acceptable** when",
        "every acceptable assertion holds and no hard violation occurs. Mark a materially",
        "ambiguous case **undetermined** for every answer to it. Record verdicts in",
        "`human-verdicts.json`; do not open `review-map.json` until all verdicts are in.",
        "",
    ]
    verdicts: list[JsonObject] = []
    current_case: str | None = None
    for row in ordered:
        case = cases[str(row["case_id"])]
        assert isinstance(case, EvalCase)
        label = labels[str(row["attempt_id"])]
        if case.case_id != current_case:
            current_case = case.case_id
            lines += [f"## Case {case.case_id} ({case.work_slice}, {case.stratum})", ""]
            for turn in case.history:
                lines += [f"**{turn['role']} (history):**", "", _quote(turn["content"]), ""]
            lines += ["**Prompt:**", "", _quote(case.prompt), ""]
            lines += ["**Acceptable:**", *(f"- {item}" for item in case.expected["acceptable"])]
            lines += ["", "**Hard violations:**"]
            lines += [f"- {item}" for item in case.expected["hard_violations"]] or ["- (none)"]
            lines += ["", f"**Reviewer notes:** {case.expected['notes']}", ""]
        lines += [f"### Answer {label}", "", f"Status: `{row.get('status')}`"]
        failures = row.get("task_failures") or []
        if failures:
            lines.append(
                "Recorded failures: "
                + ", ".join(str(f.get("kind")) for f in failures if isinstance(f, dict))
            )
        for step in row.get("tool_steps") or []:
            lines.append(
                f"Tool `{step.get('name')}` {json.dumps(step.get('arguments'), sort_keys=True)}"
                f" -> {json.dumps(step.get('result'), sort_keys=True)}"
            )
        response = row.get("final_response")
        lines += ["", _quote(response) if isinstance(response, str) else "> (no final answer)", ""]
        verdicts.append(
            {
                "label": label,
                "case_id": case.case_id,
                "verdict": "pending",
                "allowed_verdicts": ["acceptable", "unacceptable", "undetermined"],
                "failed_assertions": [],
                "hard_violations": [],
                "minor_misses": [],
                "reviewer": None,
            }
        )
    mapping = {label: attempt_id for attempt_id, label in labels.items()}
    return "\n".join(lines) + "\n", verdicts, mapping


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines()) or ">"


def _write_new_text(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def render_report(
    document: JsonObject,
    fixtures: follow.FixtureSet,
    schedule: Sequence[EvalAttempt],
    directory: Path,
) -> None:
    """Write the summary and review artifacts to new private files; never overwrite."""
    follow.write_results(directory / "summary.json", summarize(document, fixtures, schedule))
    packet, verdicts, mapping = review_packet(document, fixtures)
    follow.write_results(directory / "review-map.json", mapping)
    follow.write_results(directory / "human-verdicts.json", {"verdicts": verdicts})
    _write_new_text(directory / "human-review.md", packet)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_plan(
    manifest_path: Path,
) -> tuple[Manifest, follow.FixtureSet, tuple[EvalAttempt, ...], follow.ExperimentProfile]:
    manifest = load_manifest(manifest_path)
    fixtures = load_corpus(manifest.corpus_path, manifest.corpus_sha256)
    schedule = build_schedule(manifest, fixtures)
    return manifest, fixtures, schedule, profile_for(manifest, schedule, manifest_path)


def _private_directory(directory: Path) -> None:
    _require(
        directory.is_dir()
        and not directory.is_symlink()
        and directory.stat().st_mode & 0o077 == 0
        and directory.parent.stat().st_mode & 0o077 == 0,
        "existing private run directory with a private parent required (0700)",
    )


async def run(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest, fixtures, schedule, profile = load_plan(manifest_path)
    directory = args.run_dir.resolve()
    _private_directory(directory)
    state_path = directory / "state.json"
    if args.report:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        _require(
            state.get("version") == profile.state_version
            and state.get("identity", {}).get("manifest_sha256") == manifest.sha256,
            "report manifest/state mismatch",
        )
        _require(
            set(state.get("attempts", {})) <= {a.attempt_id for a in schedule},
            "unplanned recorded attempt",
        )
        render_report(
            results_document(state, fixtures, schedule, profile, manifest),
            fixtures,
            schedule,
            directory,
        )
        return 0
    _require(args.account is not None, "--account is required")
    commercial_path, inference_path = follow.live.policy_paths()
    follow.live.validate_commercial(load_policy(commercial_path))
    routes = upgrade.qualified_routes(manifest.raw, load_inference_policy(inference_path))
    env = Settings.explicit_evaluation_environment()
    _require(
        bool(env.get("DATABASE_URL")) and get_settings().database_url == env["DATABASE_URL"],
        "explicit isolated evaluation DATABASE_URL required",
    )
    _require(bool(env.get(model_routing.MODEL_ROUTING_ENV)), "explicit evaluation catalog required")
    if not args.dry_run:
        _require(bool(get_settings().openrouter_api_key), "OpenRouter credential missing")
    pool = await asyncpg.create_pool(dsn=env["DATABASE_URL"], min_size=1, max_size=2)
    try:
        service = EntitlementService(pool)
        database = await follow.live.validate_database(pool, args.account, service, manifest.period)
        identity: JsonObject = {
            "experiment": profile.experiment,
            "account": str(args.account),
            "period_key": manifest.period,
            "database": database,
            "database_url_sha256": hashlib.sha256(env["DATABASE_URL"].encode()).hexdigest(),
            "manifest_sha256": manifest.sha256,
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
            manifest.period,
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
            upgrade.account_lock(directory, args.account),
            follow.locked_state(state_path, args.account, identity, profile=profile) as state,
        ):
            ctx.state = state
            if not state["attempts"]:
                await preflight(ctx)
            follow.phase_admission(state, schedule, "run", profile=profile)
            exposure = await follow.reliability.read_ledger_exposure(
                pool, args.account, manifest.period
            )
            follow.freeze_baseline(state, state_path, exposure, manifest.period, profile=profile)
            follow.require_exclusive(state, exposure)
            follow.record_stage(state, "run", identity)
            result_path = directory / "results.json"
            _require(
                not result_path.exists(), "results already exist; recorded runs are not replayed"
            )
            follow.write_state(state_path, state)
            try:
                for attempt in schedule:
                    if attempt.attempt_id in state["attempts"]:
                        continue
                    await follow.run_attempt(ctx, attempt)
                    current = await follow.reliability.read_ledger_exposure(
                        pool, args.account, manifest.period
                    )
                    follow.require_exclusive(state, current)
            finally:
                final = await follow.reliability.read_ledger_exposure(
                    pool, args.account, manifest.period
                )
                follow.write_results(
                    result_path,
                    results_document(state, fixtures, schedule, profile, manifest, final),
                )
            if len(state["attempts"]) == len(schedule):
                render_report(
                    results_document(state, fixtures, schedule, profile, manifest, final),
                    fixtures,
                    schedule,
                    directory,
                )
    finally:
        await pool.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--account", type=uuid.UUID)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (
        follow.FollowupError,
        EntitlementsError,
        follow.reliability.ReliabilityError,
        OSError,
        ValueError,
        compute_runtime.ComputeUnavailable,
        asyncpg.PostgresError,
    ) as exc:
        print(f"reasoning eval rejected: {follow.reliability.sanitize(str(exc))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("reasoning eval interrupted; recorded attempts will not be replayed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
