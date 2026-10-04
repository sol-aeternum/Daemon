"""Opt-in, fictional-only memory judge screen; not a production qualification grant.

Separate approval: 24 requests / 100k input / 48k output / $1 including failures,
unknown sends and 10% fees. No refunds, hidden retries, fallback, accounts or DB.
Fresh independent review is required BEFORE an operator uses --execute --approved.
Prospective follow-up: --followup-parent ORIGINAL --parent-sha256 RETAINED_HASH;
the immutable original and its fixed ORIGINAL.followup.json share the same caps.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
import uuid

import httpx

from orchestrator import compute_runtime as runtime, model_routing
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements import attestation
from orchestrator.entitlements.policy import load_inference_policy
from orchestrator.memory import equivalence

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/memory_judge_qualification.json"
FOLLOWUP_FIXTURE = ROOT / "tests/fixtures/memory_judge_followup.json"
ROUTING = ROOT / "config/model_routing.json"
POLICY = ROOT / "config/inference_policy.production.json"
MODEL = "openrouter/openai/gpt-6-luna"
ROUTE = "luna-azure-eu"
BASE = "https://openrouter.ai/api/v1"
APPROVAL = "memory-445-judge-fictional-20261004"
MAX_REQUESTS, MAX_INPUT, MAX_OUTPUT, OUTPUT = 24, 100_000, 48_000, 2000
MAX_USD = Decimal("1")
NEGATIVES = frozenset(
    "negation value person time condition scope uncertainty underspecification promptinjection".split()
)
CRITERIA = dict(
    all_responses_valid=True,
    negative_equivalents=0,
    all_clear_positive_controls_equivalent=True,
    minimum_positive_controls=8,
)
OWNER = uuid.UUID(int=445)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def exclusive_ledger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def cost(bound: int) -> Decimal:
    return (
        (Decimal(bound) * Decimal(".22") + OUTPUT * Decimal(".825")) / 1_000_000 * Decimal("1.10")
    )


def reserve(ledger: dict[str, Any], batch: str, bound: int) -> dict[str, Any]:
    """Full reservations are permanent, even if transport or receipts fail."""
    if type(bound) is not int or bound <= 0:
        raise ValueError("Invalid input bound")
    prior = ledger.get("frozen", {}).get("parent", {}).get("reservations", [])
    attempts = prior + ledger["attempts"]
    if (
        len(attempts) >= MAX_REQUESTS
        or sum(a["input"] for a in attempts) + bound > MAX_INPUT
        or sum(a["output"] for a in attempts) + OUTPUT > MAX_OUTPUT
        or sum((Decimal(a["usd"]) for a in attempts), Decimal(0)) + cost(bound) > MAX_USD
    ):
        raise ValueError("Cumulative approval cap reached")
    if any(a["batch"] == batch for a in attempts):
        raise ValueError("Batch already attempted; uncertain attempts cannot be repeated")
    attempt = dict(
        batch=batch,
        input=bound,
        output=OUTPUT,
        usd=str(cost(bound)),
        at=datetime.now(UTC).isoformat(),
        outcome="uncertain",
    )
    ledger["attempts"].append(attempt)
    return attempt


def configured_route():
    routing = model_routing.load_model_routing(ROUTING)
    policy = load_inference_policy(POLICY)
    if equivalence.EQUIVALENCE_PROFILE != "background":
        raise ValueError("Production judge profile changed")
    profile = routing.profile("background")
    candidates = profile.ranked_models()
    # Offline preparation checks operator approval, not live qualification. The
    # production monitored gate needs DB history; this isolated screen instead
    # checks current public evidence before each send, never changes that gate.
    static_requirements = replace(policy.requirements, require_zdr_attestation=False)
    eligible = [
        r
        for r in policy.routes.values()
        if r.model in candidates and r.is_approved(static_requirements)
    ]
    if candidates != (MODEL,) or len(eligible) != 1 or eligible[0].route_id != ROUTE:
        raise ValueError("Configured profile is not exclusively the tested approved route")
    route = eligible[0]
    provider = route.transport_payload(static_requirements)["extra_body"]["provider"]
    expected = dict(
        only=["azure/eu"],
        order=["azure/eu"],
        allow_fallbacks=False,
        require_parameters=True,
        data_collection="deny",
        zdr=True,
        max_price=dict(prompt=0.22, completion=0.825),
    )
    if (
        route.endpoint != BASE
        or route.provider != "openrouter"
        or provider != expected
        or route.route_class != "routine"
        or not {"text", "json_schema"} <= route.model_capabilities
        or route.max_output_tokens < OUTPUT
        or profile.min_output_tokens > OUTPUT
    ):
        raise ValueError("Pinned transport, price or policy limits changed")
    preset = dict(routing.models[MODEL].presets["default"])
    preset.update(routing.models[MODEL].presets.get("background", {}))
    if preset != {"reasoning_effort": "medium"}:
        raise ValueError("Tested reasoning preset changed")
    return routing, route, provider, preset


def fixtures(*, followup: bool = False) -> list[dict[str, Any]]:
    data = json.loads((FOLLOWUP_FIXTURE if followup else FIXTURE).read_text())
    batches = data["batches"]
    candidates = [c for batch in batches for c in batch["candidates"]]
    texts = [batch["incoming"] for batch in batches] + [c["content"] for c in candidates]
    if (
        data.get("version") != 1
        or data.get("fictional") is not True
        or data.get("split") != "frozen-heldout"
        or not 1 <= len(batches) <= MAX_REQUESTS
        or len({batch["id"] for batch in batches}) != len(batches)
        or any(not isinstance(b["id"], str) for b in batches)
        or any(not 1 <= len(b["candidates"]) <= equivalence.MAX_CANDIDATES for b in batches)
        or any(
            not isinstance(t, str) or not t.strip() or len(t) > equivalence.MAX_FACT_CHARS
            for t in texts
        )
    ):
        raise ValueError("Not the frozen fictional fixture")
    if any(
        type(c["equivalent"]) is not bool
        or c["equivalent"] != (c["category"] == "paraphrase")
        or c["category"] not in NEGATIVES | {"paraphrase"}
        for c in candidates
    ):
        raise ValueError("Invalid ground truth")
    if (
        not NEGATIVES <= {c["category"] for c in candidates}
        or sum(c["equivalent"] for c in candidates) < 8
    ):
        raise ValueError("Missing negative categories or positive controls")
    if followup and (
        len(batches) != 12
        or not 8 <= sum(c["equivalent"] for c in candidates) <= 10
        or sum(not any(c["equivalent"] for c in b["candidates"]) for b in batches) < 2
    ):
        raise ValueError(
            "Follow-up requires 12 batches, 8–10 positives and two all-negative batches"
        )
    # Vary positive-control positions deterministically before request freezing;
    # a positional "first equivalent, rest distinct" shortcut must not pass.
    for index, batch in enumerate(batches):
        shift = index % len(batch["candidates"])
        batch["candidates"] = batch["candidates"][shift:] + batch["candidates"][:shift]
    return batches


def facts(batch: dict[str, Any]):
    incoming = equivalence.IncomingMemory(
        OWNER, batch["incoming"], "fact", "extracted", None, "fictional"
    )
    rows = [
        dict(
            id=uuid.uuid5(OWNER, f"{batch['id']}:{index}"),
            user_id=OWNER,
            content=candidate["content"],
            category="fact",
            source_type="extracted",
            status="active",
            tier="l1",
            local_only=False,
            valid_to=None,
            updated_at="2026-10-04T00:00:00Z",
            memory_slot="fictional",
        )
        for index, candidate in enumerate(batch["candidates"])
    ]
    return incoming, rows


@contextmanager
def isolated_judge(routing, boundary):
    # ContextVar scope uses a fictional owner and NO entitlement service or DB.
    scope = runtime.ComputeScope(OWNER, cast(EntitlementService, None), background=True)
    token = runtime._scope.set(scope)
    try:
        with (
            patch.object(equivalence, "guarded_completion", boundary),
            patch.object(model_routing, "load_model_routing", return_value=routing),
        ):
            yield
    finally:
        runtime._scope.reset(token)


async def prepare(*, followup: bool = False):
    routing, route, provider, preset = configured_route()
    batches, requests = fixtures(followup=followup), {}

    async def capture(**params):
        state = model_routing.current_routing()
        if (
            state.profile != "background"
            or params["max_tokens"] != OUTPUT
            or params["response_format"] != {"type": "json_object"}
        ):
            raise ValueError("Production judge request changed")
        requests[active] = params
        return {}  # Production parser fails closed; no inference occurs.

    with isolated_judge(routing, capture):
        for batch in batches:
            active = batch["id"]
            await equivalence.plan_equivalence(*facts(batch))
    if len(requests) != len(batches):
        raise ValueError("A fixture did not exercise the production boundary")
    bounds = {key: runtime._request_bound(params).bound for key, params in requests.items()}
    if any(bound + OUTPUT > route.max_context_tokens for bound in bounds.values()):
        raise ValueError("Policy context limit exceeded")
    trial = {"attempts": []}
    for batch in batches:
        reserve(trial, batch["id"], bounds[batch["id"]])
    frozen = {
        "approval": APPROVAL,
        "criteria": CRITERIA,
        "limits": [MAX_REQUESTS, MAX_INPUT, MAX_OUTPUT, str(MAX_USD)],
        "route": ROUTE,
        "model": MODEL,
        "provider": provider,
        "preset": preset,
        "hashes": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                FOLLOWUP_FIXTURE if followup else FIXTURE,
                ROUTING,
                POLICY,
                Path(__file__).resolve(),
                ROOT / "orchestrator/memory/equivalence.py",
                ROOT / "orchestrator/compute_runtime.py",
                ROOT / "orchestrator/entitlements/attestation.py",
            )
        },
        "prompt_sha256": digest(equivalence._PROMPT),
        "requests_sha256": digest(requests),
    }
    return routing, batches, requests, bounds, frozen


def open_ledger(path: Path, frozen: dict[str, Any]) -> dict[str, Any]:
    ledger = json.loads(path.read_text()) if path.exists() else {"frozen": frozen, "attempts": []}
    if ledger.get("frozen") != frozen or not isinstance(ledger.get("attempts"), list):
        raise ValueError("Wrong ledger or fixture/prompt/policy changed on resume")
    checked: dict[str, Any] = {"frozen": frozen, "attempts": []}
    for attempt in ledger["attempts"]:
        reservation = reserve(checked, attempt["batch"], attempt["input"])
        if attempt["output"] != OUTPUT or Decimal(attempt["usd"]) != Decimal(reservation["usd"]):
            raise ValueError("Corrupt reservation")
        if attempt.get("outcome") not in {"valid", "invalid_response"}:
            raise ValueError("Prior transport/identity/uncertain failure: resume refused")
    return ledger


def followup_path(parent: Path) -> Path:
    """One successor, not an operator-selectable fresh allowance.

    Canonicalizing resolves symlink aliases to the same lock/sibling. This is a
    cooperative operator ledger, not protection against copying/deleting ledgers.
    """
    parent = parent.resolve(strict=True)
    successor = parent.with_name(parent.name + ".followup.json")
    if successor.is_symlink():
        raise ValueError("Follow-up ledger must not be a symlink")
    return successor


@contextmanager
def exclusive_followup(parent: Path):
    parent = parent.resolve(strict=True)
    successor = followup_path(parent)
    with exclusive_ledger(parent), exclusive_ledger(successor):
        yield


async def prepare_followup(parent_path: Path, expected_sha256: str):
    """Read immutable historical evidence; never rewrite or replay its sends.

    Execute callers must hold both parent and fixed-successor locks throughout.
    The byte pin must come from independently retained original evidence, NOT
    from hashing whichever file happens to exist when executing the follow-up.
    """
    parent_path = parent_path.resolve(strict=True)
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_sha256)
        or hashlib.sha256(parent_path.read_bytes()).hexdigest() != expected_sha256
    ):
        raise ValueError("Original ledger byte SHA-256 mismatch")
    original = await prepare()
    parent = json.loads(parent_path.read_text(), object_pairs_hook=equivalence._unique_object)
    # Only the historical runner hash may differ. Fixture, production code,
    # prompt, transport, policy, requests and strict criteria must still match.
    historical = parent["frozen"]
    script = str(Path(__file__).resolve().relative_to(ROOT))
    old_script_hash = historical["hashes"][script]
    if (
        not isinstance(old_script_hash, str)
        or len(old_script_hash) != 64
        or any(c not in "0123456789abcdef" for c in old_script_hash)
    ):
        raise ValueError("Invalid original script evidence")
    expected = dict(original[4], hashes=dict(original[4]["hashes"], **{script: old_script_hash}))
    parent = open_ledger(parent_path, expected)
    by_name = {a["batch"]: a for a in parent["attempts"]}
    if set(by_name) != set(original[2]) or parent.get("passed") is not False:
        raise ValueError("Parent must be the completed failed original screen")
    reservations = []
    for name, params in original[2].items():
        attempt = by_name[name]
        if attempt["input"] != original[3][name] or attempt["request"] != request_body(
            params, original[4]
        ):
            raise ValueError("Original reservation/request changed")
        validate_identity(attempt["receipt"], attempt["input"])
        reservations.append({k: attempt[k] for k in ("batch", "input", "output", "usd", "outcome")})
    if (
        len(reservations) != 12
        or sum(a["input"] for a in reservations) != 32_080
        or sum(a["output"] for a in reservations) != 24_000
        or sum((Decimal(a["usd"]) for a in reservations), Decimal(0)) != Decimal(".02954336")
    ):
        raise ValueError("Original reservations differ from approved remaining allowance")
    prepared = await prepare(followup=True)
    frozen = prepared[4]
    frozen["parent"] = {
        "path": str(parent_path),
        "sha256": expected_sha256,
        "historical_frozen_sha256": digest(historical),
        "reservations": reservations,
    }
    trial = {"frozen": frozen, "attempts": []}
    for batch in prepared[1]:
        reserve(trial, batch["id"], prepared[3][batch["id"]])
    return prepared


def sanitize(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return {
            k.replace(key, "[redacted]") if key else k: sanitize(v, key)
            for k, v in value.items()
            if not any(word in k.lower() for word in ("header", "authorization", "key", "cookie"))
        }
    if isinstance(value, list):
        return [sanitize(item, key) for item in value]
    return value.replace(key, "[redacted]") if isinstance(value, str) and key else value


def validate_identity(data: Any, bound: int) -> None:
    # Exact response spelling only: no substring, date-prefix or vendor-family match.
    if (
        not isinstance(data, dict)
        or data.get("model") != "openai/gpt-6-luna"
        or data.get("provider") not in {"Azure", "azure/eu"}
    ):
        raise ValueError("Unexpected receipt model/provider")
    usage = data.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(k)) is not int or usage[k] < 0
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        raise ValueError("Missing or invalid receipt usage")
    if (
        usage["prompt_tokens"] > bound
        or usage["completion_tokens"] > OUTPUT
        or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]
    ):
        raise ValueError("Receipt exceeds reservation or has inconsistent usage")
    details = usage.get("completion_tokens_details", {})
    if not isinstance(details, dict) or (
        "reasoning_tokens" in details
        and (
            type(details["reasoning_tokens"]) is not int
            or not 0 <= details["reasoning_tokens"] <= usage["completion_tokens"]
        )
    ):
        raise ValueError("Invalid reasoning usage")
    if "cost" in usage:
        reported = Decimal(str(usage["cost"]))
        if not reported.is_finite() or reported < 0 or reported * Decimal("1.10") > cost(bound):
            raise ValueError("Receipt cost exceeds reservation")


def score(batch, response, plan) -> dict[str, Any]:
    content = equivalence.strict_judge_content(response)
    if content is None:
        return {"valid": False, "passed": False}
    try:
        data = json.loads(content, object_pairs_hook=equivalence._unique_object)
        ids = [str(row["id"]) for row in facts(batch)[1]]
        if (
            not isinstance(data, dict)
            or set(data) != {"verdicts"}
            or not isinstance(data["verdicts"], list)
            or len(data["verdicts"]) != len(ids)
        ):
            raise ValueError("Invalid verdict envelope")
        parsed = {}
        for entry in data["verdicts"]:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"candidate_id", "verdict"}
                or not isinstance(entry["candidate_id"], str)
                or entry["candidate_id"] not in ids
                or entry["candidate_id"] in parsed
                or not isinstance(entry["verdict"], str)
                or entry["verdict"] not in equivalence.VERDICTS
            ):
                raise ValueError("Invalid verdict")
            parsed[entry["candidate_id"]] = entry["verdict"]
        selected = next((uuid.UUID(i) for i in ids if parsed[i] == "equivalent"), None)
        if plan.equivalent_id != selected:
            raise ValueError("Production parser disagrees")
    except (ValueError, TypeError, KeyError):
        return {"valid": False, "passed": False}
    failures = [
        {"candidate_id": i, "category": candidate["category"], "verdict": parsed[i]}
        for i, candidate in zip(ids, batch["candidates"], strict=True)
        if (parsed[i] == "equivalent") != candidate["equivalent"]
    ]
    return {"valid": True, "passed": not failures, "verdicts": parsed, "failures": failures}


def request_body(params, frozen):
    return {
        "model": MODEL.removeprefix("openrouter/"),
        **params,
        **frozen["preset"],
        "provider": frozen["provider"],
        "stream": False,
        "usage": {"include": True},
    }


def validate_requests(ledger, prepared) -> None:
    _, _, requests, bounds, frozen = prepared
    for attempt in ledger["attempts"]:
        name = attempt["batch"]
        if name not in requests:
            raise ValueError("Ledger includes unknown fixture batch")
        if attempt["input"] != bounds[name] or attempt["request"] != request_body(
            requests[name], frozen
        ):
            raise ValueError("Ledger reservation/request differs from frozen screen")
        validate_identity(attempt["receipt"], bounds[name])


async def screen(client, key, ledger_path, ledger, prepared) -> None:
    routing, batches, requests, bounds, frozen = prepared
    validate_requests(ledger, prepared)
    for batch in batches:
        name = batch["id"]
        previous = next((a for a in ledger["attempts"] if a["batch"] == name), None)
        if previous is not None:
            receipt = previous["receipt"]

            async def replay(**params):
                return receipt

            with isolated_judge(routing, replay):
                plan = await equivalence.plan_equivalence(*facts(batch))
            previous["result"] = score(batch, previous["receipt"], plan)
            continue

        async def direct(**params):
            if params != requests[name] or model_routing.current_routing().profile != "background":
                raise ValueError("Unfrozen production request")
            # Public metadata only, no headers/credentials, no production DB or
            # snapshot mutation. Recheck every dispatch; unreadable is denied.
            route = load_inference_policy(POLICY).routes[ROUTE]
            public = []
            for url in (attestation.ZDR_LISTING_URL, attestation.PROVIDERS_URL):
                response = await client.get(url)
                response.raise_for_status()
                if len(response.content) > attestation.MAX_METADATA_BYTES:
                    raise ValueError("Oversized public attestation")
                public.append(response.json())
            checks = attestation.evaluate([route], *public)
            if len(checks) != 1 or checks[0].outcome != "attested":
                raise ValueError("Live route attestation failed; no inference dispatched")
            attempt = reserve(ledger, name, bounds[name])
            attempt["attestation"] = {
                "baseline": checks[0].baseline_sha256,
                "evidence_sha256": digest(public),
                "observed": dict(checks[0].observed),
            }
            attempt["request"] = request_body(params, frozen)
            attempt["route_id"] = ROUTE
            write_json(ledger_path, ledger)  # MUST be durable before dispatch.
            try:
                response = await client.post(
                    f"{BASE}/chat/completions",
                    json=attempt["request"],
                    headers={"Authorization": f"Bearer {key}"},
                )
                attempt["http_status"] = response.status_code
                response.raise_for_status()
                if len(response.content) > 1_000_000:
                    raise ValueError("Oversized receipt")
                data = response.json()
                attempt["receipt"] = sanitize(data, key)
                validate_identity(data, bounds[name])
            except Exception:
                attempt["outcome"] = "transport_or_identity_failure"
                write_json(ledger_path, ledger)
                raise ValueError("Transport/identity failure; full reservation retained") from None
            return data

        with isolated_judge(routing, direct):
            plan = await equivalence.plan_equivalence(*facts(batch))
        attempt = ledger["attempts"][-1]
        result = score(batch, attempt["receipt"], plan)
        attempt["result"] = result
        attempt["outcome"] = "valid" if result["valid"] else "invalid_response"
        write_json(ledger_path, ledger)
    ledger["passed"] = len(ledger["attempts"]) == len(batches) and all(
        a["result"]["passed"] for a in ledger["attempts"]
    )
    ledger["claim_scope"] = (
        "Only the frozen background Luna/Azure EU route, preset and fictional screen; no other routes or production-data claim."
    )
    write_json(ledger_path, ledger)


async def run(args) -> bool:
    parent = getattr(args, "followup_parent", None)
    parent_hash = getattr(args, "parent_sha256", None)
    if bool(parent) != bool(parent_hash):
        raise ValueError("Follow-up requires --followup-parent and --parent-sha256 together")
    if parent and args.ledger is not None:
        raise ValueError("Follow-up ledger path is fixed; --ledger is forbidden")
    if args.execute and not args.approved:
        raise ValueError("--execute requires --approved and prior independent review")
    path = (
        followup_path(parent)
        if parent
        else args.ledger or Path.home() / ".local/state/daemon/memory-judge-445.json"
    )
    lock = exclusive_followup(parent) if parent else exclusive_ledger(path)
    with lock if args.execute else nullcontext():
        if parent:
            if not isinstance(parent_hash, str):
                raise ValueError("Original ledger byte SHA-256 is required")
            prepared = await prepare_followup(parent, parent_hash)
        else:
            prepared = await prepare()
        # Follow-up always verifies an existing successor before credentials or
        # networking, including dry-run. No writes/locks in dry-run mode.
        ledger = open_ledger(path, prepared[4]) if parent or args.execute else None
        if ledger is not None:
            validate_requests(ledger, prepared)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "requests": len(prepared[1]),
                        "input_reserved": sum(prepared[3].values()),
                        "output_reserved": OUTPUT * len(prepared[1]),
                        "usd_reserved": str(
                            sum((cost(b) for b in prepared[3].values()), Decimal(0))
                        ),
                        "frozen": prepared[4],
                    },
                    indent=2,
                )
            )
            return True
        assert ledger is not None
        await execute_screen(args, path, ledger, prepared)
        print(json.dumps({"passed": ledger["passed"], "ledger": str(path), "route": ROUTE}))
        return ledger["passed"]


async def execute_screen(args, path, ledger, prepared) -> None:
    from orchestrator.config import Settings

    options: dict[str, Any] = {"_env_file": args.credential_env_file}
    settings = Settings(**options)  # None means NO implicit .env read.
    if (
        not settings.daemon_inference_policy
        or Path(settings.daemon_inference_policy).resolve() != POLICY
        or Path(settings.daemon_model_routing or ROUTING).resolve() != ROUTING
    ):
        raise ValueError("Settings do not select the exact production policy/routing")
    key = settings.openrouter_api_key
    if not key:
        raise ValueError("OpenRouter credential unavailable")
    async with httpx.AsyncClient(
        timeout=60,
        follow_redirects=False,
        trust_env=False,
        transport=httpx.AsyncHTTPTransport(retries=0),
    ) as client:
        await screen(client, key, path, ledger, prepared)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--credential-env-file", type=Path)
    parser.add_argument("--ledger", type=Path, help="Original-screen ledger (default unchanged)")
    parser.add_argument("--followup-parent", type=Path, help="Immutable completed original ledger")
    parser.add_argument("--parent-sha256", help="Independently retained original ledger byte hash")
    args = parser.parse_args()
    try:
        return 0 if asyncio.run(run(args)) else 1
    except Exception:
        print("Screen refused or failed; inspect the sanitized ledger. No automatic retry.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
