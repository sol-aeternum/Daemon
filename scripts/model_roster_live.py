#!/usr/bin/env python3
"""Sequential, evaluation-only adapter for the synthetic roster fixtures.

No route is enabled here. An operator supplies an isolated migrated database,
an evaluation commercial policy and an approved inference policy. A durable
``in_progress`` entry is written *before* each model dispatch; interrupted
attempts are never retried. The state directory contains raw model responses:
keep it private and do not publish it as a human scoring result.

``--extension`` is an explicit opt-in selector for a separately identified
plan. It scopes the candidate catalog, the pin set, the attempt ceiling, the
state identity and the offline import, so an extension run uses its own state
and pins and can neither continue nor reset the default run's records. The
model's route pin still comes from the operator's external inference policy;
nothing here approves an endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import sys
import time
import uuid
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg

from orchestrator import compute_runtime, model_routing
from orchestrator.config import Settings, get_settings
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.errors import EntitlementsError
from orchestrator.entitlements.ledger import next_period_key
from orchestrator.entitlements.plans import Capability
from orchestrator.entitlements.policy import load_inference_policy, load_policy
from scripts import model_roster_pilot as pilot

CAP_MICROUSD = 25_000_000
DB_PREFIX = "daemon_roster_pilot_"
DB_MARKER = "daemon-model-roster-evaluation-only"
ACCOUNT_MARKER = "Synthetic roster pilot"
USERNAME_MARKER = "roster_pilot_20260927"
STATE_VERSION = "model-roster-live-state/1"


class LiveError(Exception):
    """Preflight, attribution or persistent-state failure; never downgrade."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LiveError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_period_window(service: EntitlementService, period: str) -> None:
    require(service.current_period_key() == period, "original period ended")
    boundary = datetime.fromisoformat(next_period_key(period) + "-01").replace(tzinfo=timezone.utc)
    # No call begins when its timeout could normally run into the next funded month.
    require(
        datetime.now(timezone.utc) + timedelta(seconds=get_settings().request_timeout_s + 10)
        < boundary,
        "too close to original period boundary",
    )


def write_state(path: Path, state: dict[str, Any]) -> None:
    """Replace and fsync a private checkpoint before any billable call."""
    raw = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def locked_state(path: Path, identity: dict[str, Any]):
    require(path.parent.is_dir() and not path.is_symlink(), "state directory/file invalid")
    require(path.parent.stat().st_mode & 0o077 == 0, "state directory must be private (0700)")
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LiveError("another roster runner holds the state lock") from exc
        require(not path.with_name(path.name + ".tmp").exists(), "incomplete state checkpoint")
        if path.exists():
            require(path.is_file() and not path.is_symlink(), "state path invalid")
            state = json.loads(path.read_text(encoding="utf-8"))
            require(state.get("version") == STATE_VERSION, "state version changed")
            require(state.get("identity") == identity, "run identity drift")
            require(isinstance(state.get("attempts"), dict), "state attempts invalid")
        else:
            state = {"version": STATE_VERSION, "identity": identity, "attempts": {}}
            write_state(path, state)
        yield state
    finally:
        os.close(fd)


def policy_paths() -> tuple[Path, Path]:
    exported = Settings.explicit_evaluation_environment()
    commercial = exported.get("DAEMON_COMMERCIAL_CONFIG")
    inference = exported.get("DAEMON_INFERENCE_POLICY")
    if not commercial or not inference:
        raise LiveError("explicit evaluation policy paths required")
    paths = (Path(commercial).resolve(), Path(inference).resolve())
    require(all(path.is_file() for path in paths), "policy file missing")
    require(
        paths[0] != Path("config/commercial.json").resolve(), "default commercial policy forbidden"
    )
    return paths


def validate_commercial(policy: Any) -> None:
    trial = policy.trial
    require(
        not trial.enabled
        and not trial.granted_on_account_creation
        and trial.budget_microusd == 0
        and trial.extended_agents == 0,
        "evaluation trial must be entirely disabled",
    )
    pro = policy.plan("pro")  # Plan is a StrEnum in the entitlement contract.
    require(
        0 < pro.limits.monthly_budget_microusd <= CAP_MICROUSD
        and all(
            0 <= policy.plan(name).limits.monthly_budget_microusd <= CAP_MICROUSD
            for name in ("free", "pro", "power")
        )
        and pro.limits.max_concurrent_operations == 1,
        "evaluation plan budgets must be <= USD25, pro funded and concurrency one",
    )
    require(policy.period == "calendar_month_utc", "evaluation period must be calendar month UTC")


def pinned_routes(
    pins: dict[str, str],
    inference: Any,
    fixtures: pilot.FixtureSet,
    candidates: Sequence[pilot.Candidate] | None = None,
) -> dict[str, Any]:
    """Resolve pins against the selected plan's candidates, or reject them.

    The catalog is an explicit argument: a default run accepts only default
    candidate labels and an extension run accepts only its own arm, so an
    extension can never be dispatched through a default pin file or vice versa.
    No route is enabled, approximated or substituted here.
    """
    catalog = pilot.resolve_catalog(None) if candidates is None else tuple(candidates)
    labels = {attempt.candidate_label for attempt in pilot.build_attempts(fixtures, catalog)}
    require(
        bool(pins) and set(pins) <= labels, "pins must name a nonempty planned candidate subset"
    )
    routes = {}
    for label, route_id in pins.items():
        if not isinstance(route_id, str) or not route_id:
            raise LiveError(f"missing route ID for {label}")
        route = inference.route(route_id)
        if route is None:
            raise LiveError(f"unqualified route for {label}")
        require(
            route.is_approved(inference.requirements),
            f"unqualified route for {label}",
        )
        require(
            route.provider == "openrouter" and route.model.startswith("openrouter/"),
            "non-OpenRouter route",
        )
        expected_model = pilot.EXTENSION_MODEL_IDS.get(label)
        require(
            expected_model is None or route.model == expected_model,
            "extension model pin mismatch",
        )
        require(
            route.route_class in {"routine", "premium"} and route.price_ceiling is not None,
            "unfunded route class",
        )
        # guarded_completion selects by model, not route ID. More than one
        # eligible route for a model would allow an unintended billed endpoint.
        competitors = [
            r
            for r in inference.routes.values()
            if r.model == route.model
            and r.provider == "openrouter"
            and r.route_class in {"routine", "premium"}
            and r.is_approved(inference.requirements)
        ]
        require(
            len(competitors) == 1 and competitors[0].route_id == route_id,
            "ambiguous model route pin",
        )
        route.transport_payload(inference.requirements)
        routes[label] = route
    return routes


def request_for(
    case: pilot.CaseFixture, route: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": route.model,
        "messages": messages,
        "max_tokens": min(4096, route.max_output_tokens),
    }
    if tools:
        params["tools"] = tools
        params["tool_choice"] = "auto"
    if case.requires_schema:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "pilot_" + case.case_id, "strict": True, "schema": case.schema},
        }
    return params


def case_tools(case: pilot.CaseFixture) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in case.tools
    ]


def verify_candidate(
    case: pilot.CaseFixture, route: Any, resolved: Any, params: dict[str, Any]
) -> int:
    bound = compute_runtime._request_bound(params)
    candidates = compute_runtime._priced_candidates(
        resolved, bound, params, route.model, check_budget=False
    )
    require(
        len(candidates) == 1 and candidates[0][2].route_id == route.route_id,
        f"pinned route ineligible for {case.case_id}",
    )
    return candidates[0][0]


async def validate_database(
    pool: Any, account: uuid.UUID, service: EntitlementService, expected_period: str
) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT current_database() AS name, "
            "shobj_description((SELECT oid FROM pg_database WHERE datname = current_database()), "
            "'pg_database') AS marker"
        )
        require(
            row["name"].startswith(DB_PREFIX) and row["marker"] == DB_MARKER,
            "evaluation DB marker/name required",
        )
        user = await conn.fetchrow("SELECT id, name, username FROM users WHERE id = $1", account)
        require(
            user is not None
            and user["id"] == account
            and user["name"] == ACCOUNT_MARKER
            and user["username"] == USERNAME_MARKER,
            "dedicated evaluation account required",
        )
        require(
            await conn.fetchval(
                "SELECT count(*) FROM users WHERE username = $1 AND id <> $2",
                USERNAME_MARKER,
                account,
            )
            == 0,
            "duplicate evaluation identity",
        )
        # Migration seed users are permitted, but no other user may have funded
        # entitlements. This check also catches trial-funded free accounts.
        require(
            await conn.fetchval(
                "SELECT count(*) FROM entitlement_accounts WHERE user_id <> $1 "
                "AND (plan <> 'free' OR trial_budget_microusd > 0 "
                "OR trial_reserved_microusd > 0 OR byok_enabled)",
                account,
            )
            == 0,
            "another funded account exists",
        )
        record = await service.store.get_account(conn, account)
        if record is None:
            raise LiveError("active admin pro entitlement account required")
        require(
            record.plan.value == "pro"
            and record.plan_source == "admin"
            and record.status.value == "active",
            "active admin pro entitlement account required",
        )
        require(
            not record.byok_enabled
            and record.trial_budget_microusd == 0
            and record.trial_consumed_microusd == 0
            and record.trial_reserved_microusd == 0
            and record.trial_extended_agents == 0
            and record.trial_state.value == "exhausted",
            "trial/BYOK funds forbidden",
        )
    resolved = await service.resolve(account)
    require(resolved.period_key == expected_period == service.current_period_key(), "period drift")
    require(
        resolved.plan.value == "pro"
        and resolved.recurring_budget_microusd
        == service.policy.plan("pro").limits.monthly_budget_microusd
        and not resolved.trial_funds_premium,
        "account budget/trial drift",
    )
    return row["name"]


def as_dict(response: Any) -> dict[str, Any]:
    raw = response if isinstance(response, dict) else response.model_dump(mode="json")
    require(isinstance(raw, dict), "non-object completion response")
    return raw


def provider_echo_matches(reported: Any, pinned: str) -> bool:
    """Match a provider's display name without claiming it attests a subroute.

    OpenRouter echoes names such as Inceptron, not tags such as inceptron/fp8.
    The exact subroute remains constrained by the outbound only/order transport.
    Explicit but different subroute tags and unrelated provider names fail closed.
    """
    if reported is None or reported == pinned:
        return True
    if not isinstance(reported, str) or "/" in reported:
        return False

    def normalize(text: str) -> str:
        return "".join(char for char in text.casefold() if char.isalnum())

    provider = pinned.split("/", 1)[0]
    # Catalog display name for the google-vertex family is "Google". The
    # outbound exact tag still pins geography; a display echo cannot attest it.
    expected_display = "Google" if provider == "google-vertex" else provider
    return bool(reported) and normalize(reported) in {
        normalize(provider),
        normalize(expected_display),
    }


def schema_matches(value: Any, schema: dict[str, Any]) -> bool:
    types = schema.get("type")
    allowed = types if isinstance(types, list) else [types]
    matches = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "null": lambda: value is None,
        "boolean": lambda: type(value) is bool,
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float) and math.isfinite(value),
    }
    if not any(name in matches and matches[name]() for name in allowed):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, str) and len(value) > schema.get("maxLength", float("inf")):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return False
        if not schema.get("minimum", -float("inf")) <= value <= schema.get("maximum", float("inf")):
            return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])) <= set(value):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(
            key not in properties or schema_matches(item, properties[key])
            for key, item in value.items()
        )
    if isinstance(value, list) and "items" in schema:
        return all(schema_matches(item, schema["items"]) for item in value)
    return True


def simulate(
    case: pilot.CaseFixture, name: str, args: dict[str, Any], positions: dict[str, int]
) -> dict[str, Any]:
    spec = next((item for item in case.tools if item.name == name), None)
    if spec is None:
        raise LiveError("undeclared tool requested")
    require(set(args) <= set(spec.parameter_names), "undeclared tool argument")
    require(schema_matches(args, spec.parameters), "invalid tool arguments")
    responses = case.tool_responses.get(name, [])
    matched = next(
        (
            entry
            for entry in responses
            if isinstance(match := entry.get("match"), dict)
            and all(args.get(k) == v for k, v in match.items())
        ),
        None,
    )
    if matched is None:
        positional = [entry for entry in responses if "match" not in entry]
        if positional:
            index = positions.get(name, 0)
            matched = positional[min(index, len(positional) - 1)]
            positions[name] = index + 1
    # `match` is harness selection metadata, not a field returned by the tool.
    return (
        {key: value for key, value in matched.items() if key != "match"}
        if matched is not None
        else {"error": "no fixture response for arguments"}
    )


def pending_results(
    state: dict[str, Any],
    fixtures: pilot.FixtureSet,
    extension: str | None = None,
) -> dict[str, Any]:
    """Import into the offline scorer after human adjudication; no fabricated verdict.

    The extension discriminator is written only for an extension run, so the
    default import keeps its original shape and the offline scorer can refuse a
    mismatched plan instead of pooling two arms.
    """
    selected = pilot.resolve_extension(extension)
    attempts = []
    for attempt_id, entry in state["attempts"].items():
        calls = entry["calls"]
        cost = [call.get("provider_cost_usd") for call in calls]
        charges = [call.get("account_charge_microusd") for call in calls]
        known_charge = sum(charge for charge in charges if charge is not None)
        unknown_charges = sum(charge is None for charge in charges)
        attempts.append(
            {
                "attempt_id": attempt_id,
                "calls_used": len(calls),
                "latency_seconds": entry.get("latency_seconds"),
                "cost_usd": sum(cost) if calls and all(x is not None for x in cost) else None,
                "schema_valid": entry.get("schema_valid"),
                "semantic_verdict": "pending",
                "notes": (
                    f"Live state: {entry['status']}; provider invoice cost unknown where null. "
                    f"Account ledger known charge: {known_charge} microusd; "
                    f"calls with unknown ledger charge: {unknown_charges}. "
                    "Full call-level evidence is in the private state file. Human review required."
                ),
            }
        )
    document: dict[str, Any] = {
        "artifact_version": pilot.RESULTS_ARTIFACT_VERSION,
        "fixtures_sha256": fixtures.sha256,
        "attempts": attempts,
    }
    if selected is not None:
        document["extension"] = selected
    return document


def attempt_budget(extension: str | None, requested: int | None) -> int:
    """Bounded attempt budget for one batch of the selected plan.

    With no explicit value the budget is the selected plan's own size — 208 for
    the default five-candidate plan, 48 for a single-arm extension — and an
    explicit value may only lower it within that ceiling. A batch can never
    dispatch more attempts than the selected plan contains, and this bound is a
    plan size, not a budget approval.
    """
    ceiling = pilot.planned_attempt_ceiling(extension)
    if requested is None:
        return ceiling
    require(1 <= requested <= ceiling, f"max attempts must be in 1..{ceiling}")
    return requested


def batch_candidates(pins: dict[str, str], only: list[str] | None) -> set[str]:
    """Select an explicit execution batch without changing immutable route pins."""
    selected = set(pins) if only is None else set(only)
    require(bool(selected) and selected <= set(pins), "batch candidates must be pinned")
    return selected


async def run_attempt(
    attempt: pilot.Attempt,
    case: pilot.CaseFixture,
    route: Any,
    account: uuid.UUID,
    pool: Any,
    service: EntitlementService,
    state: dict[str, Any],
    state_path: Path,
    identity: dict[str, Any],
    commercial_path: Path,
    inference_path: Path,
) -> None:
    entry: dict[str, Any] = {
        "status": "in_progress",
        "calls": [],
        "tool_steps": [],
        "schema_valid": None,
    }
    state["attempts"][attempt.attempt_id] = entry
    write_state(state_path, state)
    messages: list[dict[str, Any]] = [{"role": "user", "content": case.prompt}]
    tools = case_tools(case)
    positions: dict[str, int] = {}
    started = time.monotonic()
    scope = None
    try:
        async with compute_runtime.account_compute(
            pool,
            account,
            operation="chat",
            profile="routine",
            expected_period=identity["period_key"],
        ) as scope:
            for index in range(case.max_calls):
                ensure_period_window(service, identity["period_key"])
                require(
                    digest(commercial_path) == identity["commercial_sha256"]
                    and digest(inference_path) == identity["inference_sha256"],
                    "policy changed during run",
                )
                await validate_database(pool, account, service, identity["period_key"])
                params = request_for(case, route, messages, tools)
                resolved = await scope.service.resolve(account)
                reservation_bound = verify_candidate(case, route, resolved, params)
                bound = compute_runtime._request_bound(params)
                candidates = compute_runtime._priced_candidates(
                    resolved, bound, params, route.model
                )
                require(
                    len(candidates) == 1 and candidates[0][2].route_id == route.route_id,
                    "pinned route exceeds remaining allocation",
                )
                ensure_period_window(service, identity["period_key"])
                # Persist intent before dispatch. A crash between checkpoint and call
                # may lose one attempt but can never duplicate a paid call.
                call: dict[str, Any] = {
                    "number": index + 1,
                    "status": "dispatched_unknown",
                    "route_id": route.route_id,
                    "requested_model": route.model,
                    "provider_pin": list(route.transport.provider_only or ()),
                    "reservation_bound_microusd": reservation_bound,
                    "provider_cost_usd": None,
                    "account_charge_microusd": None,
                }
                entry["calls"].append(call)
                write_state(state_path, state)
                call_start = time.monotonic()
                settled_before = sum(scope.settled.values())
                try:
                    response = as_dict(await compute_runtime.guarded_completion(**params))
                finally:
                    call["elapsed_seconds"] = time.monotonic() - call_start
                    charge = sum(scope.settled.values()) - settled_before
                    call["account_charge_microusd"] = (
                        charge if charge >= 0 and len(scope.settled) > index else None
                    )
                    write_state(state_path, state)
                routed = model_routing.current_routing()
                require(
                    routed.selected_route_id == route.route_id
                    and routed.selected_model == route.model,
                    "runtime route drift",
                )
                call["raw_response"] = response
                call["actual_model_reported"] = response.get("model")
                require(
                    response.get("model") == route.model.removeprefix("openrouter/")
                    or response.get("model") == route.model,
                    "served model drift",
                )
                call["provider_reported"] = response.get(
                    "provider"
                )  # null means unobserved, never inferred
                require(
                    provider_echo_matches(
                        response.get("provider"), route.transport.provider_only[0]
                    ),
                    "served provider drift",
                )
                usage = response.get("usage")
                call["usage"] = usage
                if isinstance(usage, dict) and type(usage.get("cost")) in (int, float):
                    cost = float(usage["cost"])
                    if 0 <= cost < float("inf"):
                        call["provider_cost_usd"] = cost
                call["status"] = "completed"
                write_state(state_path, state)
                choices = response.get("choices")
                if not isinstance(choices, list) or len(choices) != 1:
                    raise LiveError("invalid response choices")
                choice = choices[0]
                require(
                    isinstance(choice, dict) and isinstance(choice.get("message"), dict),
                    "invalid response message",
                )
                message = choice["message"]
                require(
                    choice.get("finish_reason") not in {"length", "content_filter"},
                    "truncated/filtered response",
                )
                requested_tools = message.get("tool_calls") or []
                require(isinstance(requested_tools, list), "invalid tool calls")
                if requested_tools:
                    require(index + 1 < case.max_calls, "tool call without final-answer budget")
                    messages.append(message)
                    for tool_call in requested_tools:
                        require(
                            isinstance(tool_call, dict)
                            and isinstance(tool_call.get("function"), dict),
                            "invalid tool call",
                        )
                        function = tool_call["function"]
                        args = json.loads(function["arguments"])
                        result = simulate(case, function["name"], args, positions)
                        entry["tool_steps"].append(
                            {"name": function["name"], "arguments": args, "result": result}
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "content": json.dumps(result),
                            }
                        )
                    write_state(state_path, state)
                    continue
                content = message.get("content")
                require(isinstance(content, str) and bool(content.strip()), "empty final answer")
                entry["final_response"] = content
                if case.requires_schema:
                    try:
                        entry["schema_valid"] = schema_matches(
                            json.loads(content), case.schema or {}
                        )
                    except ValueError:
                        entry["schema_valid"] = False
                entry["status"] = "completed"
                break
            else:
                raise LiveError("call budget exhausted")
    except BaseException as exc:
        entry["status"] = (
            "uncertain"
            if any(c["status"] == "dispatched_unknown" for c in entry["calls"])
            else "failed"
        )
        entry["error"] = f"{type(exc).__name__}: {exc}"
        cause = exc.__context__
        if cause is not None:
            entry["underlying_error_type"] = type(cause).__name__
            entry["underlying_status_code"] = getattr(cause, "status_code", None)
            message = str(cause)
            key = get_settings().openrouter_api_key
            if key:
                message = message.replace(key, "[REDACTED]")
            entry["underlying_error"] = message[:2000]
        raise
    finally:
        if scope is not None and entry["calls"]:
            known = sum(c["account_charge_microusd"] or 0 for c in entry["calls"])
            delta = sum(scope.settled.values()) - known
            last = entry["calls"][-1]
            if delta > 0 and last["account_charge_microusd"] is None:
                last["account_charge_microusd"] = delta
        entry["latency_seconds"] = time.monotonic() - started
        write_state(state_path, state)


async def run(args: argparse.Namespace) -> None:
    # The extension selector is resolved before any preflight work, and every
    # catalog, plan, identity and import below follows it. Every existing
    # safeguard — exact route pin, unique approved model route, funded-period
    # window, isolated account and database, private state, no replay of an
    # interrupted attempt — applies unchanged to an extension run.
    extension = pilot.resolve_extension(args.extension)
    catalog = pilot.resolve_catalog(extension)
    commercial_path, inference_path = policy_paths()
    policy = load_policy(commercial_path)
    validate_commercial(policy)
    inference = load_inference_policy(inference_path)
    fixtures = pilot.load_fixtures(args.fixtures)
    pins = json.loads(args.pins.read_text(encoding="utf-8"))
    require(isinstance(pins, dict), "pins must be an object mapping labels to route IDs")
    routes = pinned_routes(pins, inference, fixtures, catalog)
    selected = batch_candidates(pins, args.only_candidate)
    if any(route.route_class == "premium" for route in routes.values()):
        require(
            Capability.PREMIUM_ROUTING in policy.plan("pro").capabilities,
            "premium route needs evaluation pro-plan capability (no trial funding)",
        )
    require(
        "DATABASE_URL" in Settings.explicit_evaluation_environment()
        and get_settings().database_url
        == Settings.explicit_evaluation_environment()["DATABASE_URL"],
        "explicit evaluation DATABASE_URL required",
    )
    require(bool(get_settings().openrouter_api_key), "OpenRouter credential missing")
    pool = await asyncpg.create_pool(
        dsn=Settings.explicit_evaluation_environment()["DATABASE_URL"], min_size=1, max_size=2
    )
    try:
        service = EntitlementService(pool)
        db_name = await validate_database(pool, args.account, service, args.period)
        resolved = await service.resolve(args.account)
        for case in fixtures.cases:
            for candidate in pilot.candidates_for(case.workload, catalog):
                if candidate.label not in routes:
                    continue
                route = routes[candidate.label]
                verify_candidate(
                    case,
                    route,
                    resolved,
                    request_for(
                        case, route, [{"role": "user", "content": case.prompt}], case_tools(case)
                    ),
                )
        identity = {
            "fixtures_sha256": fixtures.sha256,
            "account": str(args.account),
            "database": db_name,
            "database_url_sha256": hashlib.sha256(
                Settings.explicit_evaluation_environment()["DATABASE_URL"].encode()
            ).hexdigest(),
            "period_key": args.period,
            "pins": pins,
            "unrun_candidate_labels": sorted(
                candidate.label for candidate in catalog if candidate.label not in pins
            ),
            "commercial_sha256": digest(commercial_path),
            "inference_sha256": digest(inference_path),
        }
        if extension is not None:
            # Only an extension run records the discriminator, so the default
            # identity is unchanged and existing live state keeps resuming, while
            # an extension can never adopt or continue a default run's state.
            identity["extension"] = extension
        with locked_state(args.state, identity) as state:
            if args.results:
                require(not args.results.exists(), "results file already exists")
                require(args.results.parent.is_dir(), "results directory missing")
            completed = 0
            state.setdefault("execution_batches", []).append(
                {
                    "candidates": sorted(selected),
                    "max_attempts": args.max_attempts,
                    "extension": extension,
                }
            )
            write_state(args.state, state)
            try:
                for attempt in pilot.build_attempts(fixtures, catalog):
                    if attempt.candidate_label not in selected:
                        continue
                    if attempt.attempt_id in state["attempts"]:
                        continue
                    if completed >= args.max_attempts:
                        break
                    ensure_period_window(service, identity["period_key"])
                    await run_attempt(
                        attempt,
                        fixtures.by_id()[attempt.case_id],
                        routes[attempt.candidate_label],
                        args.account,
                        pool,
                        service,
                        state,
                        args.state,
                        identity,
                        commercial_path,
                        inference_path,
                    )
                    completed += 1
            finally:
                if args.results:
                    write_state(args.results, pending_results(state, fixtures, extension))
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=pilot.DEFAULT_FIXTURES_PATH)
    parser.add_argument(
        "--pins",
        type=Path,
        required=True,
        help="JSON mapping selected candidate labels to qualified route IDs; omitted arms stay unrun",
    )
    parser.add_argument("--account", type=uuid.UUID, required=True)
    parser.add_argument("--period", required=True, help="original funded UTC month, YYYY-MM")
    parser.add_argument(
        "--state",
        type=Path,
        required=True,
        help="private persistent JSON state; parent directory must exist",
    )
    parser.add_argument(
        "--results", type=Path, help="new offline-scorer import with pending human verdicts"
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help=(
            "bounded attempts for this batch; defaults to the selected plan's own size (208 for "
            "the default plan) and may only be lowered"
        ),
    )
    parser.add_argument(
        "--only-candidate",
        action="append",
        help="Run only this already-pinned candidate in this batch; repeat for several",
    )
    parser.add_argument(
        "--extension",
        default=None,
        help=(
            "Opt-in extension selector for a separately identified plan with its own state and "
            f"pins; one of {', '.join(sorted(pilot.EXTENSION_CATALOGS))}. Omit it for the "
            "default plan. It must match the offline plan/score selector."
        ),
    )
    args = parser.parse_args()
    try:
        args.extension = pilot.resolve_extension(args.extension)
        args.max_attempts = attempt_budget(args.extension, args.max_attempts)
        require(
            len(args.period) == 7
            and datetime.strptime(args.period, "%Y-%m").strftime("%Y-%m") == args.period,
            "period must be YYYY-MM",
        )
        asyncio.run(run(args))
    except (
        LiveError,
        OSError,
        ValueError,
        pilot.ArtifactError,
        EntitlementsError,
        compute_runtime.ComputeUnavailable,
        asyncpg.PostgresError,
    ) as exc:
        print(f"roster live rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
