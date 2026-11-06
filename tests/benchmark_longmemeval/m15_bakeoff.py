"""M1.5 decomposition latency bake-off driver.

Offline-tested here; the primary executes the live run later. Nothing in this
module contacts a provider, opens a database or reads a dataset outside the
repository at import time or in its tests.

Arms are frozen: the route is pinned by id and the model is read from the
route. Arms B and C run at ``low`` reasoning effort (the owner's decision: it is
their lowest configured setting); the labels A/B/C appear in the output.

The request is the production ``ProductionSubqueryProvider`` request: same
prompt, schema, messages, ``max_tokens`` and input cap, changing only route,
model, reasoning effort and streaming. Streaming is not used: the guarded
stream object (``orchestrator/compute_runtime.py::_MeteredStream``) exposes no
serving provider or cost, and the settlement lives on the account scope's
holds, so a streamed call cannot report both without approximation. Calls are
non-streaming and ``ttft_seconds`` is null.

Generated sub-queries, reply text and question text are never stored, logged or
put in an exception message here.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import inspect
import json
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from orchestrator.compute_runtime import ComputeUnavailable
from orchestrator.memory.multi_query import (
    MAX_INPUT_CHARS,
    PROMPT_V2,
    SUBQUERY_SCHEMA,
    validate_subqueries,
)
from tests.benchmark_longmemeval.dev2_labelling import (
    _append_journal,
    _as_spend,
    _journal_path,
    _NullScope,
    _run_lock_acquired,
    _run_lock_held,
    _write_document_atomic,
)

RECORD_SCHEMA: Final[str] = "m15-bakeoff/1"
JOURNAL_VERSION: Final[int] = 1

MAX_CALLS: Final[int] = 150
MAX_TOKENS: Final[int] = 256
MAX_SUBQUERIES: Final[int] = 3
CONSECUTIVE_FAILURE_LIMIT: Final[int] = 10
DISPATCH_TIMEOUT_S: Final[float] = 120.0

#: The only database this driver may address.
#: Its own database (owner ruling), so this block's spend reconciles on its own ledger.
BENCHMARK_DB_NAME: Final[str] = "daemon_lme_bench_m15"

#: Synthetic benchmark user namespace: deterministic, this driver only.
M15_USER_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6f1f4e0c-9b3a-5d2e-8c71-2a0d4b7e5f19")
M15_USER_NAME: Final[str] = "m15-bakeoff:subquery"

QUESTION_PATH: Final[Path] = Path(__file__).with_name("fixtures") / "dev_subset.json"


@dataclass(frozen=True, slots=True)
class Arm:
    label: str
    route_id: str
    reasoning_effort: str


ARMS: Final[tuple[Arm, ...]] = (
    Arm("A", "luna-azure-eu", "none"),
    Arm("B", "deepseek-flash-coreweave-fp8", "low"),
    Arm("C", "glm-flash-inceptron-fp8", "low"),
)


@dataclass(frozen=True, slots=True)
class Question:
    question_id: str
    message: str
    reference_time: datetime
    digest: str


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _parse_reference_time(value: Any) -> datetime:
    """The question date as UTC, e.g. ``2023/05/30 (Tue) 23:18``."""
    if not isinstance(value, str):
        raise ValueError("question_date missing")
    date_part, _, rest = value.partition(" (")
    time_part = rest.partition(") ")[2]
    try:
        parsed = datetime.strptime(f"{date_part} {time_part}".strip(), "%Y/%m/%d %H:%M")
    except ValueError:
        raise ValueError("question_date unparsable") from None
    return parsed.replace(tzinfo=UTC)


QUESTIONS_REQUIRED: Final[int] = 50


def load_questions(path: Path | str = QUESTION_PATH) -> list[Question]:
    """The frozen question list, in file order, each with its question date."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("dev_subset.json must be a JSON list")
    questions: list[Question] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("dev_subset.json rows must be objects")
        question_id = row.get("question_id")
        message = row.get("question")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("question_id missing")
        if question_id in seen:
            raise ValueError("Duplicate question id")
        if not isinstance(message, str) or not message:
            raise ValueError("question text missing")
        seen.add(question_id)
        reference_time = _parse_reference_time(row.get("question_date"))
        digest = _sha256_json(
            {
                "question_id": question_id,
                "question": message,
                "question_date": row.get("question_date"),
            }
        )
        questions.append(Question(question_id, message, reference_time, digest))
    if len(questions) != QUESTIONS_REQUIRED:
        raise ValueError(f"Expected exactly {QUESTIONS_REQUIRED} questions")
    return questions


def subquery_request(question: Question) -> dict[str, Any]:
    """The exact body ``ProductionSubqueryProvider`` sends, before routing."""
    return {
        "messages": [
            {"role": "system", "content": PROMPT_V2},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message": question.message[:MAX_INPUT_CHARS],
                        "as_of": question.reference_time.date().isoformat(),
                    }
                ),
            },
        ],
        "max_tokens": MAX_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "memory_components_v2",
                "strict": True,
                "schema": SUBQUERY_SCHEMA,
            },
        },
    }


def dispatch_payload(arm: Arm, route: Any, question: Question) -> dict[str, Any]:
    """The production request plus the four bake-off differences."""
    return {
        "_route_id": arm.route_id,
        "model": route.model,
        "_exact_model": True,
        "reasoning_effort": arm.reasoning_effort,
        "stream": False,
        **subquery_request(question),
    }


def call_key(arm: Arm, question: Question) -> str:
    return f"{arm.label}:{question.question_id}"


def dispatch_plan(
    questions: Sequence[Question], arms: Sequence[Arm] = ARMS
) -> list[tuple[Arm, Question]]:
    """One call per question per arm, rotated by question index, in file order."""
    plan: list[tuple[Arm, Question]] = []
    for index, question in enumerate(questions):
        if not arms:
            break
        for offset in range(len(arms)):
            plan.append((arms[(index + offset) % len(arms)], question))
    return plan


def manifest(questions: Sequence[Question], arms: Sequence[Arm] = ARMS) -> dict[str, Any]:
    return {
        "kind": "manifest",
        "version": JOURNAL_VERSION,
        "inputs_sha256": _sha256_json([question.digest for question in questions]),
        "prompt_sha256": _sha256_text(PROMPT_V2),
        "schema_sha256": _sha256_json(SUBQUERY_SCHEMA),
        "arms_sha256": _sha256_json(
            [[arm.label, arm.route_id, arm.reasoning_effort] for arm in arms]
        ),
    }


def _read_bakeoff_journal(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any] | None]:
    """Resumed results, intents and the manifest. Corruption is fatal.

    The dev2 labelling reader keys by question id; here one call is
    ``arm:question``, so the record shape is read here while the append/fsync,
    path, lock and atomic-write helpers stay imported from that module.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, set(), None
    results: dict[str, dict[str, Any]] = {}
    intents: set[str] = set()
    batch: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("Bake-off journal is corrupt") from None
        if not isinstance(record, dict):
            raise ValueError("Bake-off journal is corrupt")
        kind = record.get("kind")
        if kind == "manifest":
            if batch is not None or results or intents:
                raise ValueError("Bake-off journal is corrupt")
            batch = record
            continue
        key = record.get("call_key")
        if not isinstance(key, str) or not key:
            raise ValueError("Bake-off journal is corrupt")
        if kind == "result":
            results[key] = {name: value for name, value in record.items() if name != "kind"}
        elif kind == "intent":
            intents.add(key)
        else:
            raise ValueError("Bake-off journal is corrupt")
    return results, intents, batch


def _stopped_document(max_spend_usd: Decimal, stop_reason: str) -> dict[str, Any]:
    return {
        "schema": RECORD_SCHEMA,
        "arms": [
            {
                "label": arm.label,
                "route_id": arm.route_id,
                "reasoning_effort": arm.reasoning_effort,
            }
            for arm in ARMS
        ],
        "max_spend_usd": str(max_spend_usd),
        "max_calls": MAX_CALLS,
        "calls": 0,
        "total_cost_usd": "0",
        "total_cost_complete": True,
        "unknown_cost_calls": [],
        "stop_reason": stop_reason,
        "summary": {},
        "questions": [],
    }


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    """Linear-interpolated percentile, deterministic for a sorted sample."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _output_tokens(receipt: Mapping[str, Any]) -> int:
    usage = receipt.get("usage")
    # The provider's own field, as ``compute_runtime._usage_counts`` reads it.
    value = usage.get("completion_tokens") if isinstance(usage, Mapping) else None
    return value if type(value) is int and value >= 0 else 0


def _first_choice(receipt: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The content and finish reason, never raised with their text attached."""
    choices = receipt.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, None
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return None, None
    message = choice.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    finish = choice.get("finish_reason")
    return (
        content if isinstance(content, str) else None,
        finish if isinstance(finish, str) else None,
    )


def _evaluate_subqueries(content: str | None, message: str) -> tuple[bool, int]:
    """Parses and validates; returns ``(schema_valid, accepted count)``."""
    if content is None:
        return False, 0
    try:
        data = json.loads(content)
        if not isinstance(data, dict) or set(data) != {"queries"}:
            return False, 0
        accepted = validate_subqueries(data["queries"], message, max_subqueries=MAX_SUBQUERIES)
    except (TypeError, ValueError):
        return False, 0
    return True, len(accepted)


def _failure_category(error: BaseException) -> str | None:
    """A known guard failure's category, or ``None`` when it is uncertain."""
    if isinstance(error, ComputeUnavailable):
        category = getattr(error, "category", None)
        return category if isinstance(category, str) and category else "compute_unavailable"
    if isinstance(error, TimeoutError):
        return "deadline_exceeded"
    return None


def _serving_provider(receipt: Mapping[str, Any]) -> str | None:
    provider = receipt.get("provider")
    return provider if isinstance(provider, str) else None


def _summary(entries: Sequence[Mapping[str, Any]], arms: Sequence[Arm]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for arm in arms:
        rows = [row for row in entries if row.get("arm") == arm.label]
        totals = [
            float(row["total_seconds"]) for row in rows if row.get("total_seconds") is not None
        ]
        ttfts = [float(row["ttft_seconds"]) for row in rows if row.get("ttft_seconds") is not None]
        cost = sum(
            (Decimal(str(row["cost_usd"])) for row in rows if row.get("cost_usd") is not None),
            Decimal(0),
        )
        calls = len(rows)
        completed = [row for row in rows if row.get("error_category") is None]
        summary[arm.label] = {
            "route_id": arm.route_id,
            "reasoning_effort": arm.reasoning_effort,
            "calls": calls,
            "failures": sum(1 for row in rows if row.get("error_category") is not None),
            "ttft_p50_seconds": _percentile(ttfts, 0.50),
            "ttft_p95_seconds": _percentile(ttfts, 0.95),
            "total_p50_seconds": _percentile(totals, 0.50),
            "total_p95_seconds": _percentile(totals, 0.95),
            # A failed call has no reply, so its outcome is unknown, not "invalid" or
            # "not triggered": these rates cover completed calls, and failures are
            # reported on their own.
            "completed_calls": len(completed),
            "schema_valid_rate": (
                sum(1 for row in completed if row.get("schema_valid")) / len(completed)
                if completed
                else 0.0
            ),
            "trigger_rate": (
                sum(1 for row in completed if row.get("triggered")) / len(completed)
                if completed
                else 0.0
            ),
            "truncated_calls": sum(1 for row in rows if row.get("finish_reason") == "length"),
            "total_cost_usd": str(cost),
            "serving_providers": sorted(
                {
                    str(row["serving_provider"])
                    for row in rows
                    if isinstance(row.get("serving_provider"), str)
                }
            ),
        }
    return summary


async def run_bakeoff(
    *,
    questions: Sequence[Question],
    output_path: Path | str,
    max_spend_usd: Decimal,
    call: Callable[[Mapping[str, Any]], Any],
    check_attestation: Callable[[], Any],
    verify_routes: Callable[[Sequence[Arm]], Any],
    arms: Sequence[Arm] = ARMS,
    account_scope: Callable[[str], Any] | None = None,
    acquire_run_lock: Callable[[], Any] | None = None,
    check_run_lock: Callable[[], Any] | None = None,
    read_ledger_spend_usd: Callable[[], Any] | None = None,
    next_call_ceiling_usd: Callable[[Mapping[str, Any]], Any] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Run the bake-off sequentially, with no retry, resuming from its journal.

    The benchmark run lock is taken before the journal is opened and confirmed
    before every send. The output refuses an existing document. A journal
    manifest that no longer matches the inputs stops with ``receipt_mismatch``;
    an intent without a result stops with ``uncertain_dispatch``; a call with a
    known guard failure is recorded and the run continues, while an accounting
    or otherwise uncertain failure stops it. Spend is the whole benchmark
    ledger (settled plus held) and is read before every dispatch.
    """
    from scripts.memory_query_decomposition import receipt_dict, reported_cost

    if not await _run_lock_acquired(acquire_run_lock):
        return _stopped_document(max_spend_usd, "run_lock_busy")

    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"Bake-off output already exists: {output}")

    journal = _journal_path(output)
    try:
        handle = open(journal, "x", encoding="utf-8")
    except FileExistsError:
        handle = open(journal, "a", encoding="utf-8")

    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(f"Bake-off journal is held by another run: {journal}") from None
        if output.exists():
            raise FileExistsError(f"Bake-off output already exists: {output}")
        results, intents, batch = _read_bakeoff_journal(journal)

        stop_reason: str | None = None
        expected_manifest = manifest(questions, arms)
        if batch is None:
            if results or intents:
                stop_reason = "receipt_mismatch"
            else:
                _append_journal(handle, expected_manifest)
        elif batch != expected_manifest:
            stop_reason = "receipt_mismatch"

        routes: Mapping[str, Any] = {}
        if stop_reason is None:
            pending = check_attestation()
            if inspect.isawaitable(pending):
                await pending
            routes = verify_routes(arms)
            if inspect.isawaitable(routes):
                routes = await routes
            if not isinstance(routes, Mapping) or any(arm.label not in routes for arm in arms):
                raise ValueError("Bake-off arm routes were not verified")

        scope = account_scope or (lambda _user_id: _NullScope())
        # Per arm: another arm's successes must not reset a refused arm's count.
        consecutive_failures = {arm.label: 0 for arm in arms}
        plan = dispatch_plan(questions, arms)

        for arm, question in plan:
            if stop_reason is not None:
                break
            key = call_key(arm, question)
            if key in results:
                # A resume replays the plan in order, so each arm's streak carries over.
                if results[key].get("error_category") is None:
                    consecutive_failures[arm.label] = 0
                else:
                    consecutive_failures[arm.label] += 1
                    if consecutive_failures[arm.label] >= CONSECUTIVE_FAILURE_LIMIT:
                        stop_reason = "consecutive_failures"
                continue
            if key in intents:
                # A dispatch with no result may already have been charged.
                stop_reason = "uncertain_dispatch"
                break
            if len(intents) >= MAX_CALLS:
                stop_reason = "max_calls"
                break

            payload = dispatch_payload(arm, routes[arm.label], question)
            if read_ledger_spend_usd is None:
                stop_reason = "spend_unreadable"
                break
            if next_call_ceiling_usd is None:
                stop_reason = "ceiling_unknown"
                break
            try:
                spend_pending = read_ledger_spend_usd()
                if inspect.isawaitable(spend_pending):
                    spend_pending = await spend_pending
                spend = _as_spend(spend_pending)
            except Exception:
                stop_reason = "spend_unreadable"
                break
            try:
                ceiling_pending = next_call_ceiling_usd(dict(payload))
                if inspect.isawaitable(ceiling_pending):
                    ceiling_pending = await ceiling_pending
                ceiling = _as_spend(ceiling_pending)
            except Exception:
                stop_reason = "ceiling_unknown"
                break
            if spend + ceiling > max_spend_usd:
                stop_reason = "spend_cap"
                break
            if not await _run_lock_held(check_run_lock):
                stop_reason = "run_lock_lost"
                break

            _append_journal(
                handle,
                {
                    "kind": "intent",
                    "call_key": key,
                    "arm": arm.label,
                    "question_id": question.question_id,
                },
            )
            intents.add(key)
            started = clock()
            try:
                async with scope(str(uuid.uuid5(M15_USER_NAMESPACE, M15_USER_NAME))):
                    response = call({**payload, "_dispatch_timeout_s": DISPATCH_TIMEOUT_S})
                    if inspect.isawaitable(response):
                        response = await response
            except Exception as error:
                from orchestrator.memory.embedding import raise_if_embedding_accounting_error

                try:
                    raise_if_embedding_accounting_error(error)
                except Exception:
                    stop_reason = "accounting_error"
                    break
                category = _failure_category(error)
                if category is None:
                    stop_reason = "uncertain_dispatch"
                    break
                elapsed = max(clock() - started, 0.0)
                entry = _call_entry(arm, question, elapsed)
                entry["error_category"] = category
                _append_journal(handle, {"kind": "result", "call_key": key, **entry})
                results[key] = dict(entry)
                consecutive_failures[arm.label] += 1
                if consecutive_failures[arm.label] >= CONSECUTIVE_FAILURE_LIMIT:
                    stop_reason = "consecutive_failures"
                    break
                continue
            elapsed = max(clock() - started, 0.0)
            consecutive_failures[arm.label] = 0

            entry = _call_entry(arm, question, elapsed)
            try:
                receipt = receipt_dict(response)
            except Exception:
                entry["error_category"] = "receipt_unknown"
                _append_journal(handle, {"kind": "result", "call_key": key, **entry})
                results[key] = dict(entry)
                stop_reason = "accounting_error"
                break
            entry["serving_provider"] = _serving_provider(receipt)
            entry["output_tokens"] = _output_tokens(receipt)
            content, finish_reason = _first_choice(receipt)
            entry["finish_reason"] = finish_reason
            try:
                cost = reported_cost(receipt)
            except ValueError:
                entry["error_category"] = "cost_unknown"
                _append_journal(handle, {"kind": "result", "call_key": key, **entry})
                results[key] = dict(entry)
                stop_reason = "accounting_error"
                break
            entry["cost_usd"] = str(cost)
            schema_valid, component_count = _evaluate_subqueries(content, question.message)
            entry["schema_valid"] = schema_valid
            entry["component_count"] = component_count
            entry["triggered"] = component_count >= 1
            _append_journal(handle, {"kind": "result", "call_key": key, **entry})
            results[key] = dict(entry)

        entries = [
            results[key]
            for arm, question in plan
            if (key := call_key(arm, question)) in results
            and results[key].get("arm") == arm.label
            and results[key].get("question_id") == question.question_id
        ]
        unknown_cost = [
            call_key(arm, question)
            for arm, question in plan
            if (key := call_key(arm, question)) in results and results[key].get("cost_usd") is None
        ]
        total = sum(
            (Decimal(str(row["cost_usd"])) for row in entries if row.get("cost_usd") is not None),
            Decimal(0),
        )
        document: dict[str, Any] = {
            "schema": RECORD_SCHEMA,
            "inputs_sha256": expected_manifest["inputs_sha256"],
            "arms": [
                {
                    "label": arm.label,
                    "route_id": arm.route_id,
                    "reasoning_effort": arm.reasoning_effort,
                }
                for arm in arms
            ],
            "max_spend_usd": str(max_spend_usd),
            "max_calls": MAX_CALLS,
            "calls": len(intents),
            "total_cost_usd": str(total),
            "total_cost_complete": not unknown_cost
            and stop_reason not in {"uncertain_dispatch", "accounting_error"},
            "unknown_cost_calls": unknown_cost,
            "stop_reason": stop_reason,
            "summary": _summary(entries, arms),
            "questions": entries,
        }
        if stop_reason not in {"uncertain_dispatch", "receipt_mismatch"}:
            _write_document_atomic(output, document)
        return document
    finally:
        handle.close()


def _call_entry(arm: Arm, question: Question, elapsed: float) -> dict[str, Any]:
    """One call's record: timings and metadata, never text or content."""
    return {
        "arm": arm.label,
        "question_id": question.question_id,
        "ttft_seconds": None,
        "total_seconds": elapsed,
        "serving_provider": None,
        "cost_usd": None,
        "output_tokens": 0,
        "finish_reason": None,
        "schema_valid": False,
        "triggered": False,
        "component_count": 0,
        "error_category": None,
    }


def check_bakeoff_database(dsn: str | None) -> str:
    """Refuse before connecting unless ``DATABASE_URL`` is the bake-off's own database."""
    from tests.benchmark_longmemeval.dev2_pilot import db_name_from_dsn

    name = db_name_from_dsn(dsn or "")
    if name != BENCHMARK_DB_NAME:
        raise ValueError(f"Bake-off requires DATABASE_URL to name exactly {BENCHMARK_DB_NAME}")
    return name


def verify_bakeoff_route(arm: Arm) -> Any:
    """Pin one arm's route by id: approved and monitored, model from the route."""
    from orchestrator.entitlements import attestation
    from orchestrator.entitlements.policy import load_inference_policy

    policy = load_inference_policy()
    route = policy.routes.get(arm.route_id)
    if route is None or not route.is_approved(policy.requirements):
        reasons = (
            route.rejection_reasons(policy.requirements)
            if route is not None
            else ("route_missing",)
        )
        raise ValueError(f"Bake-off route unapproved ({arm.label}): {','.join(reasons)}")
    if not attestation.is_monitored(route):
        raise ValueError(f"Bake-off route not monitored ({arm.label})")
    return route


def call_ceiling(route: Any, payload: Mapping[str, Any]) -> Decimal:
    """A conservative USD ceiling for one call: 256 output tokens.

    Priced from the production request bound (``compute_runtime._request_bound``)
    under the route's pinned unit prices, with internal-only keys (such as
    ``_dispatch_timeout_s``) stripped because they never reach the transport.
    """
    from orchestrator.compute_runtime import _request_bound

    if getattr(route, "price_ceiling", None) is None:
        raise ValueError("Bake-off route has no price ceiling")
    params = {key: value for key, value in payload.items() if not key.startswith("_")}
    micro = route.estimate_microusd(_request_bound(params).bound, MAX_TOKENS)
    return Decimal(int(micro)) / Decimal(1_000_000)


async def _run_bakeoff(
    *,
    questions: Sequence[Question],
    output_path: Path | str,
    max_spend_usd: Decimal,
) -> dict[str, Any]:
    """Build the real collaborators. The tests never reach here."""
    import asyncpg

    from orchestrator import compute_runtime
    from orchestrator.config import get_settings
    from orchestrator.entitlements import attestation
    from orchestrator.eval.bench_db import ensure_benchmark_user
    from tests.benchmark_longmemeval.dev2_pilot import (
        RUN_LOCK_HELD_SQL,
        SPEND_SQL,
        acquire_benchmark_run_lock,
        spend_from_row,
    )

    settings = get_settings()
    check_bakeoff_database(settings.database_url)
    user_id = uuid.uuid5(M15_USER_NAMESPACE, M15_USER_NAME)
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=1)
    connection = await asyncpg.connect(settings.database_url)
    try:
        await ensure_benchmark_user(pool, user_id)

        def account_scope(_user_id: str) -> Any:
            return compute_runtime.account_compute(
                pool,
                user_id,
                operation="chat",
                background=True,
                profile="background",
            )

        async def call(payload: Mapping[str, Any]) -> Any:
            return await compute_runtime.guarded_completion(**payload)

        async def read_ledger_spend_usd() -> Decimal:
            row = await connection.fetchrow(SPEND_SQL)
            return Decimal(str(spend_from_row(row).total_usd))

        async def acquire_run_lock() -> bool:
            return await acquire_benchmark_run_lock(connection.fetchval)

        async def check_run_lock() -> bool:
            return int(await connection.fetchval(RUN_LOCK_HELD_SQL) or 0) >= 1

        async def check_attestation() -> None:
            await attestation.check_once(pool)

        readers: dict[str, Any] = {}
        arm_by_route = {arm.route_id: arm for arm in ARMS}

        def next_call_ceiling_usd(payload: Mapping[str, Any]) -> Decimal:
            route_id = str(payload.get("_route_id"))
            route = readers.get(route_id)
            if route is None:
                route = verify_bakeoff_route(arm_by_route[route_id])
                readers[route_id] = route
            return call_ceiling(route, payload)

        def verify_routes(arms: Sequence[Arm]) -> dict[str, Any]:
            routes = {arm.label: verify_bakeoff_route(arm) for arm in arms}
            for arm in arms:
                readers[arm.route_id] = routes[arm.label]
            return routes

        return await run_bakeoff(
            questions=questions,
            output_path=output_path,
            max_spend_usd=max_spend_usd,
            call=call,
            check_attestation=check_attestation,
            verify_routes=verify_routes,
            account_scope=account_scope,
            acquire_run_lock=acquire_run_lock,
            check_run_lock=check_run_lock,
            read_ledger_spend_usd=read_ledger_spend_usd,
            next_call_ceiling_usd=next_call_ceiling_usd,
        )
    finally:
        await connection.close()
        await pool.close()


def bakeoff_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.benchmark_longmemeval.m15_bakeoff",
        description="M1.5 decomposition latency bake-off: three arms, 150 calls, no retry.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Bake-off document. Required unless --dry-run. An existing document refuses the run.",
    )
    parser.add_argument(
        "--max-spend-usd",
        type=Decimal,
        help=(
            "Cumulative ceiling in USD on the whole benchmark database ledger: "
            "settled spend plus open holds plus a conservative ceiling for the "
            "next call. Required unless --dry-run."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=QUESTION_PATH,
        help="Question fixture (default: the committed dev_subset.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the inputs and print the plan. No database, no provider, no files.",
    )
    args = parser.parse_args(argv)

    questions = load_questions(args.input)
    if args.dry_run:
        print(
            f"dry run: {len(questions)} questions x {len(ARMS)} arms = "
            f"{len(questions) * len(ARMS)} calls (MAX_CALLS={MAX_CALLS}); "
            "no database, no provider, no calls, no files"
        )
        for arm in ARMS:
            print(
                f"  arm {arm.label}: route_id={arm.route_id} "
                f"reasoning_effort={arm.reasoning_effort}"
            )
        print(f"  input_sha256={_sha256_json([question.digest for question in questions])}")
        return 0

    if args.output is None or args.max_spend_usd is None:
        parser.error("--output and --max-spend-usd are required unless --dry-run")

    document = asyncio.run(
        _run_bakeoff(
            questions=questions,
            output_path=args.output,
            max_spend_usd=args.max_spend_usd,
        )
    )
    return 0 if document["stop_reason"] is None else 1


def main(argv: Sequence[str] | None = None) -> int:
    return bakeoff_main(list(sys.argv[1:] if argv is None else argv))


__all__ = [
    "ARMS",
    "Arm",
    "BENCHMARK_DB_NAME",
    "CONSECUTIVE_FAILURE_LIMIT",
    "MAX_CALLS",
    "M15_USER_NAMESPACE",
    "Question",
    "RECORD_SCHEMA",
    "bakeoff_main",
    "call_ceiling",
    "call_key",
    "check_bakeoff_database",
    "dispatch_payload",
    "dispatch_plan",
    "load_questions",
    "main",
    "manifest",
    "run_bakeoff",
    "subquery_request",
    "verify_bakeoff_route",
]


if __name__ == "__main__":
    raise SystemExit(main())
