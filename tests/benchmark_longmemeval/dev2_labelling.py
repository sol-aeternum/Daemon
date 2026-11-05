"""Frozen dev2 labelling protocol: prompt, schema, request template, parsing.

One labeller pass over the dev2 answer-session memories, used to separate
"the store did not contain it" from "exposure failed". Everything here is
pure: no provider call, no database, no dataset read, no network.

The labeller is deliberately blind. ``build_request`` sends only the
question, the reference answer and that question's stored memories, keyed
by memory id. It never sees a ranking, a retrieval result, any candidate or
exposure output, or any other question's data.

Route: ``LABELLER_ROUTE_ID`` (azure/eu, ZDR, data collection denied). The
route precondition, attestation and cost gates live in the operator runbook
(`dev2_labelling.md`); this module only pins the request shape.

Status: frozen before any call. No call has been made.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from tests.benchmark_longmemeval.dev2_pilot import answer_conversation_ids

LABELLER_MODEL: Final[str] = "openrouter/openai/gpt-6-luna"
LABELLER_ROUTE_ID: Final[str] = "luna-azure-eu"
LABELLER_SEED: Final[int] = 42
LABELLER_REASONING_EFFORT: Final[str] = "medium"
# 4000 is ample: the response is a list of memory ids plus one short note, so
# reasoning headroom, not answer length, is the only real cost.
LABELLER_MAX_TOKENS: Final[int] = 4000

LABEL_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "memory_ids": {"type": "array", "items": {"type": "string"}},
        "answerable_from_store": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["memory_ids", "answerable_from_store", "note"],
    "additionalProperties": False,
}

PROMPT_V1: Final[str] = (
    "You label an evaluation set of stored memories. You are given a question, "
    "its reference answer, and the stored memories recorded for that question, "
    "each with an id and a text.\n"
    "List exactly the stored memories that bear on the reference answer: the "
    "memories a reader would need in order to state the answer. Use only the "
    "supplied memory ids, without duplicates.\n"
    "Set answerable_from_store to false when the supplied memories do not "
    "support the reference answer, and then return an empty memory_ids list. "
    "Otherwise return at least one memory id and set answerable_from_store to "
    "true.\n"
    "The note is one short sentence explaining the choice.\n"
    'Return only a JSON object of the form {"memory_ids": [string, ...], '
    '"answerable_from_store": boolean, "note": string}.'
)

PROMPT_TEMPLATE_V1: Final[str] = (
    "Question:\n{question}\n\n"
    "Reference answer:\n{reference_answer}\n\n"
    'Stored memories (JSON array of {{"id", "text"}}):\n{memories}'
)

_LABEL_FIELDS: Final[frozenset[str]] = frozenset({"memory_ids", "answerable_from_store", "note"})


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LabelInput:
    """One labelling input. Memories are ``(id, text)`` sorted by id."""

    question_id: str
    question: str
    reference_answer: str
    memories: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        for name in ("question_id", "question", "reference_answer"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Label input {name} must be a nonempty string")
        pairs: list[tuple[str, str]] = []
        for entry in self.memories:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise ValueError("Label input memories must be (id, text) pairs")
            memory_id, text = entry
            if not isinstance(memory_id, str) or not memory_id:
                raise ValueError("Label input memory ids must be nonempty strings")
            if not isinstance(text, str):
                raise ValueError("Label input memory text must be a string")
            pairs.append((memory_id, text))
        if len({memory_id for memory_id, _ in pairs}) != len(pairs):
            raise ValueError("Label input memory ids must be unique")
        # Order is independent of any ranking: canonical ascending id order.
        object.__setattr__(self, "memories", tuple(sorted(pairs, key=lambda p: p[0])))


@dataclass(frozen=True)
class Label:
    memory_ids: tuple[str, ...]
    answerable_from_store: bool
    note: str


def _memory_block(item: LabelInput) -> str:
    return json.dumps(
        [{"id": memory_id, "text": text} for memory_id, text in item.memories],
        ensure_ascii=False,
        sort_keys=True,
    )


def request_template() -> dict[str, Any]:
    """The frozen request template: ``build_request`` only fills the user text.

    No ``temperature``: the route rejects it.
    """
    return {
        "model": LABELLER_MODEL,
        "messages": [
            {"role": "system", "content": PROMPT_V1},
            {"role": "user", "content": PROMPT_TEMPLATE_V1},
        ],
        "seed": LABELLER_SEED,
        "reasoning_effort": LABELLER_REASONING_EFFORT,
        "max_tokens": LABELLER_MAX_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "memory_labels", "strict": True, "schema": LABEL_SCHEMA},
        },
        "stream": False,
    }


def build_request(item: LabelInput) -> dict[str, Any]:
    if not isinstance(item, LabelInput):
        raise ValueError("build_request expects a LabelInput")
    request = request_template()
    request["messages"] = [
        {"role": "system", "content": PROMPT_V1},
        {
            "role": "user",
            "content": PROMPT_TEMPLATE_V1.format(
                question=item.question,
                reference_answer=item.reference_answer,
                memories=_memory_block(item),
            ),
        },
    ]
    return request


def parse_label(item: LabelInput, text: str) -> Label:
    """Strict validation. Messages name fields, never memory text or values."""
    if not isinstance(item, LabelInput):
        raise ValueError("parse_label expects a LabelInput")
    if not isinstance(text, str):
        raise ValueError("Label text must be a string")
    try:
        value: object = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("Label is not valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError("Label must be a JSON object")
    keys = set(value)
    if keys != set(_LABEL_FIELDS):
        missing = sorted(set(_LABEL_FIELDS) - keys)
        extra = sorted(keys - set(_LABEL_FIELDS))
        raise ValueError(f"Label fields mismatch: missing={missing} extra={extra}")

    raw_ids = value["memory_ids"]
    if not isinstance(raw_ids, list):
        raise ValueError("Label memory_ids must be an array")
    ids: list[str] = []
    for entry in raw_ids:
        if not isinstance(entry, str) or not entry:
            raise ValueError("Label memory_ids must contain nonempty strings")
        ids.append(entry)

    answerable = value["answerable_from_store"]
    # Exact bool: a truthy string or integer is not a valid label.
    if type(answerable) is not bool:
        raise ValueError("Label answerable_from_store must be a boolean")
    note = value["note"]
    if not isinstance(note, str):
        raise ValueError("Label note must be a string")

    if len(set(ids)) != len(ids):
        raise ValueError("Label memory_ids must be unique")
    known = {memory_id for memory_id, _ in item.memories}
    if any(memory_id not in known for memory_id in ids):
        raise ValueError("Label contains a memory id outside the input")
    # Empty ids must mean not answerable, and not answerable must mean empty ids.
    if (not ids) != (not answerable):
        raise ValueError("Label memory_ids emptiness disagrees with answerable_from_store")

    return Label(memory_ids=tuple(ids), answerable_from_store=bool(answerable), note=note)


MEMORY_SQL: Final[str] = (
    "SELECT id, content FROM memories "
    "WHERE user_id = $1 AND source_conversation_id = ANY($2::uuid[]) "
    "AND status = 'active' ORDER BY id"
)


def _rows(value: object) -> Iterable[Any]:
    if isinstance(value, (list, tuple)):
        return value
    raise ValueError("Memory reader returned an unsupported row collection")


async def load_answer_session_memories(
    reader: Callable[[str, Sequence[Any]], Any],
    decrypt: Callable[[str], str],
    user_id: str,
    conversation_ids: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """Read the active memories of the answer sessions and decrypt them.

    ``reader`` is injected (``reader(sql, args)``, sync or async) so this
    module never opens a connection. Only ``memories`` is touched.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not conversation_ids:
        return ()
    pending = reader(MEMORY_SQL, (user_id, list(conversation_ids)))
    rows = await pending if inspect.isawaitable(pending) else pending
    pairs: list[tuple[str, str]] = []
    for row in _rows(rows):
        entry: Any = row
        memory_id = str(entry[0])
        content = entry[1]
        if not isinstance(content, str):
            raise ValueError("Memory content must be a string")
        decrypted = decrypt(content)
        if inspect.isawaitable(decrypted):
            decrypted = await decrypted
        if not isinstance(decrypted, str):
            raise ValueError("Decrypted memory content must be a string")
        pairs.append((memory_id, decrypted))
    return tuple(sorted(pairs, key=lambda p: p[0]))


def spot_check_sample(
    ids: Sequence[str], *, fraction: float = 0.15, seed: int = 42, minimum: int = 2
) -> tuple[str, ...]:
    """Deterministic spot-check draw: SHA-256 order of ``[seed, id]``."""
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    if minimum < 0:
        raise ValueError("minimum must be >= 0")
    unique = sorted({str(memory_id) for memory_id in ids})
    ordered = sorted(
        unique, key=lambda memory_id: (_sha256(_canonical([seed, memory_id])), memory_id)
    )
    count = min(max(math.ceil(fraction * len(ordered)), minimum), len(ordered))
    return tuple(ordered[:count])


def artifact_hashes() -> dict[str, str]:
    """SHA-256 of the prompt, the canonical schema and the canonical template."""
    return {
        "prompt": _sha256(PROMPT_V1),
        "label_schema": _sha256(_canonical(LABEL_SCHEMA)),
        "request_template": _sha256(_canonical(request_template())),
    }


# --- ``label`` command --------------------------------------------------------
#
# The runbook (`dev2_labelling.md`) owns the route precondition, the attestation
# and the cost gate. This section only wires them: the call, the attestation, the
# route check, the memory reader and the decryption are injected, so the tests
# drive the whole flow with fakes and no provider, database or dataset is touched
# by this module's tests.

LABEL_RECORD_SCHEMA: Final[str] = "dev2-labelling/1"


class _NullScope:
    """The stand-in account scope: the tests are not inside a benchmark account."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


def verify_label_route() -> Any:
    """Pin ``LABELLER_ROUTE_ID`` exactly, as ``run_live`` pins its own route."""
    from orchestrator.entitlements.policy import load_inference_policy
    from orchestrator.memory import embedding_adapter

    policy = load_inference_policy()
    route = policy.routes.get(LABELLER_ROUTE_ID)
    if route is None or not route.is_approved(policy.requirements):
        reasons = (
            route.rejection_reasons(policy.requirements)
            if route is not None
            else ("route_missing",)
        )
        raise ValueError(f"Labeller route unapproved: {','.join(reasons)}")
    expected = {
        "only": ["azure/eu"],
        "order": ["azure/eu"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
    }
    actual = dict(route.transport_payload(policy.requirements)["extra_body"]["provider"])
    # transport_payload always adds the route's own price ceiling (policy.py:997);
    # pin it separately so the privacy/pinning fields still compare exactly.
    max_price = actual.pop("max_price", None)
    if (
        route.route_id != LABELLER_ROUTE_ID
        or route.model != LABELLER_MODEL
        or route.provider != "openrouter"
        or route.endpoint != embedding_adapter.ENDPOINT
        or actual != expected
        or route.price_ceiling is None
        or max_price != route.price_ceiling.as_max_price()
    ):
        raise ValueError("Labeller route/pinning/ZDR mismatch")
    return route


def _response_text(receipt: Mapping[str, Any]) -> str:
    """The single completion's content; a refusal or a tool call is an error."""
    choices = receipt.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("Labeller response must carry exactly one choice")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping):
        raise ValueError("Labeller response message missing")
    if message.get("refusal") or message.get("tool_calls"):
        raise ValueError("Labeller response refused or called a tool")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("Labeller response content missing")
    return content


def _serving_provider(receipt: Mapping[str, Any]) -> str | None:
    provider = receipt.get("provider")
    return provider if isinstance(provider, str) else None


def _journal_path(output_path: Path | str) -> Path:
    return Path(f"{Path(output_path)}.receipts.jsonl")


def _append_journal(handle: Any, record: Mapping[str, Any]) -> None:
    """Append one record and make it durable before the next step."""
    handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _read_journal(path: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Resumed results and intents. A corrupt journal is fatal, never ignored."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, set()
    results: dict[str, dict[str, Any]] = {}
    intents: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("Labelling journal is corrupt") from None
        if not isinstance(record, dict):
            raise ValueError("Labelling journal is corrupt")
        kind = record.get("kind")
        question_id = record.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("Labelling journal is corrupt")
        if kind == "result":
            results[question_id] = {key: value for key, value in record.items() if key != "kind"}
        elif kind == "intent":
            intents.add(question_id)
        else:
            raise ValueError("Labelling journal is corrupt")
    return results, intents


def _as_spend(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Ledger spend is not a number") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("Ledger spend is not a usable amount")
    return amount


def _write_document_atomic(output_path: Path | str, document: Mapping[str, Any]) -> None:
    """Temp file, fsync, rename: a crash never leaves a half-written document."""
    target = Path(output_path)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            json.dump(document, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def price_ceiling_reader(route: Any) -> Callable[[Mapping[str, Any]], Decimal]:
    """A conservative USD ceiling for one call on ``route``.

    The input bound is the UTF-8 byte length of the serialized messages: a token
    never costs more than a byte, so the bound cannot be under the real input.
    Output is charged at ``LABELLER_MAX_TOKENS``. Both terms are rounded up.
    """
    price = getattr(route, "price_ceiling", None)
    if price is None:
        raise ValueError("Labeller route has no price ceiling")
    prompt_rate = Decimal(str(price.microusd_per_1m_prompt))
    completion_rate = Decimal(str(price.microusd_per_1m_completion))
    if not prompt_rate.is_finite() or not completion_rate.is_finite():
        raise ValueError("Labeller route price ceiling is not finite")

    def ceiling(payload: Mapping[str, Any]) -> Decimal:
        messages = payload.get("messages")
        if not isinstance(messages, (list, tuple)):
            raise ValueError("Labeller request has no messages")
        bound = len(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        micro = (Decimal(bound) * prompt_rate + Decimal(LABELLER_MAX_TOKENS) * completion_rate) / (
            Decimal(1_000_000)
        )
        return micro.to_integral_value(rounding=ROUND_CEILING) / Decimal(1_000_000)

    return ceiling


async def label_questions(
    *,
    question_rows: Sequence[Mapping[str, Any]],
    checkpoint: Mapping[str, Any],
    output_path: Path | str,
    max_spend_usd: Decimal,
    call: Callable[[Mapping[str, Any]], Any],
    check_attestation: Callable[[], Any],
    verify_route: Callable[[], Any],
    memory_reader: Callable[[str, Sequence[Any]], Any],
    decrypt: Callable[[str], str],
    account_scope: Callable[[str], Any] | None = None,
    read_ledger_spend_usd: Callable[[], Any] | None = None,
    next_call_ceiling_usd: Callable[[Mapping[str, Any]], Any] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Label every question once, sequentially, with no retry.

    ``max_spend_usd`` is a ceiling on the whole benchmark ledger, not on this
    invocation: settled spend plus open holds, read before every dispatch, plus
    a conservative ceiling for the call about to be sent. A missing reader, an
    unreadable ledger, an unknown ceiling or a missing cost all stop the run
    before another call is made. The output is claimed up front: an existing
    document refuses the run, an existing journal resumes it, and no question
    with a recorded result is dispatched again. Memory text and raw responses
    are never written, logged or put in an exception message.
    """
    from scripts.memory_query_decomposition import receipt_dict, reported_cost

    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"Labelling output already exists: {output}")

    journal = _journal_path(output)
    try:
        handle = open(journal, "x", encoding="utf-8")
    except FileExistsError:
        results, intents = _read_journal(journal)
        handle = open(journal, "a", encoding="utf-8")
    else:
        results, intents = {}, set()

    try:
        pending = check_attestation()
        if inspect.isawaitable(pending):
            await pending
        pending_route = verify_route()
        if inspect.isawaitable(pending_route):
            await pending_route

        scope = account_scope or (lambda _user_id: _NullScope())
        total = Decimal(0)
        calls = 0
        entries: list[dict[str, Any]] = []
        stop_reason: str | None = None

        for row in question_rows:
            if stop_reason is not None:
                break
            question_id = str(row["question_id"])
            user_id = str(row["user_id"])

            recorded = results.get(question_id)
            if recorded is not None:
                entries.append(dict(recorded))
                calls += int(recorded.get("calls") or 0)
                if isinstance(recorded.get("cost_usd"), str):
                    total += Decimal(recorded["cost_usd"])
                continue
            if question_id in intents:
                # An intent without a result may already have been charged.
                stop_reason = "uncertain_dispatch"
                break

            conversation_ids = answer_conversation_ids(
                checkpoint,
                question_id,
                [str(value) for value in row.get("answer_session_ids") or []],
            )
            memories = await load_answer_session_memories(
                memory_reader, decrypt, user_id, list(conversation_ids.values())
            )
            if not memories:
                entry = {
                    "question_id": question_id,
                    "schema_valid": False,
                    "answerable_from_store": False,
                    "reason": "no_memories",
                    "calls": 0,
                    "cost_usd": "0",
                    "latency_seconds": 0.0,
                    "serving_provider": None,
                }
                _append_journal(handle, {"kind": "result", **entry})
                entries.append(entry)
                continue

            item = LabelInput(
                question_id=question_id,
                question=str(row["question"]),
                reference_answer=str(row["answer"]),
                memories=memories,
            )
            payload = {"_route_id": LABELLER_ROUTE_ID, "_exact_model": True, **build_request(item)}

            if read_ledger_spend_usd is None:
                stop_reason = "spend_unreadable"
                break
            if next_call_ceiling_usd is None:
                stop_reason = "ceiling_unknown"
                break
            try:
                read_pending = read_ledger_spend_usd()
                if inspect.isawaitable(read_pending):
                    read_pending = await read_pending
                spend = _as_spend(read_pending)
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

            _append_journal(handle, {"kind": "intent", "question_id": question_id})
            started = clock()
            calls += 1
            try:
                async with scope(user_id):
                    response = call(dict(payload))
                    if inspect.isawaitable(response):
                        response = await response
            except Exception:
                # The cause can carry memory text or a raw response, so it is dropped.
                stop_reason = "uncertain_dispatch"
                break
            latency = max(clock() - started, 0.0)
            try:
                receipt = receipt_dict(response)
            except Exception:
                stop_reason = "cost_unknown"
                break

            entry = {
                "question_id": question_id,
                "schema_valid": False,
                "calls": 1,
                "cost_usd": None,
                "latency_seconds": latency,
                "serving_provider": _serving_provider(receipt),
            }
            try:
                cost = reported_cost(receipt)
            except ValueError:
                entry["reason"] = "cost_unknown"
                _append_journal(handle, {"kind": "result", **entry})
                entries.append(entry)
                stop_reason = "cost_unknown"
                break
            total += cost
            entry["cost_usd"] = str(cost)

            try:
                label = parse_label(item, _response_text(receipt))
            except ValueError:
                label = None
            if label is None:
                entry["schema_valid"] = False
                entry["reason"] = "parse_failure"
            else:
                entry["schema_valid"] = True
                entry["answerable_from_store"] = label.answerable_from_store
                entry["memory_ids"] = list(label.memory_ids)
                entry["note"] = label.note
            _append_journal(handle, {"kind": "result", **entry})
            entries.append(entry)

        document = {
            "schema": LABEL_RECORD_SCHEMA,
            "route_id": LABELLER_ROUTE_ID,
            "max_spend_usd": str(max_spend_usd),
            "total_cost_usd": str(total),
            "calls": calls,
            "stop_reason": stop_reason,
            "questions": entries,
        }
        # An uncertain dispatch is not a final state: keep only the journal so a
        # rerun resumes it. Every other stop is final for this batch.
        if stop_reason != "uncertain_dispatch":
            _write_document_atomic(output, document)
        return document
    finally:
        handle.close()


async def _run_label(
    *,
    rows: Sequence[Mapping[str, Any]],
    checkpoint: Mapping[str, Any],
    output_path: Path | str,
    max_spend_usd: Decimal,
) -> dict[str, Any]:
    """Build the real collaborators for ``label``. The tests never reach here."""
    import asyncpg

    from orchestrator import compute_runtime
    from orchestrator.config import get_settings
    from orchestrator.entitlements import attestation
    from orchestrator.memory.encryption import ContentEncryption
    from tests.benchmark_longmemeval.dev2_pilot import SPEND_SQL, spend_from_row
    from uuid import UUID

    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=1)
    try:
        connection = await asyncpg.connect(settings.database_url)
        try:
            encryption = ContentEncryption(settings.daemon_encryption_key)

            def account_scope(user_id: str) -> Any:
                return compute_runtime.account_compute(
                    pool,
                    UUID(user_id),
                    operation="chat",
                    background=True,
                    profile="background",
                )

            async def call(payload: Mapping[str, Any]) -> Any:
                return await compute_runtime.guarded_completion(**payload)

            async def read_ledger_spend_usd() -> Decimal:
                # Settled actuals plus open holds: the driver's own whole-database
                # reader, so the cap cannot silently undercount in-flight calls.
                row = await connection.fetchrow(SPEND_SQL)
                return Decimal(str(spend_from_row(row).total_usd))

            def memory_reader(sql: str, params: Sequence[Any]) -> Any:
                return connection.fetch(sql, *params)

            return await label_questions(
                question_rows=rows,
                checkpoint=checkpoint,
                output_path=output_path,
                max_spend_usd=max_spend_usd,
                call=call,
                check_attestation=lambda: attestation.check_once(pool),
                verify_route=verify_label_route,
                memory_reader=memory_reader,
                decrypt=encryption.decrypt,
                account_scope=account_scope,
                read_ledger_spend_usd=read_ledger_spend_usd,
                next_call_ceiling_usd=price_ceiling_reader(verify_label_route()),
            )
        finally:
            await connection.close()
    finally:
        await pool.close()


def label_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev2_labelling.py label",
        description="Label the dev2 answer-session memories once, with no retry.",
    )
    parser.add_argument("--question-rows", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="Labels document. Required unless --dry-run. An existing document refuses the run.",
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
        "--dry-run",
        action="store_true",
        help="Read and validate the inputs, resolve answer conversations, print the "
        "question count and exit. No database, no provider, no output, no journal.",
    )
    args = parser.parse_args(argv)

    rows = json.loads(args.question_rows.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("--question-rows must be a JSON list")
    checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    if not isinstance(checkpoint, dict):
        raise ValueError("--checkpoint must be a JSON object")

    if args.dry_run:
        # Resolve only; memory text is never read and nothing is created.
        for row in rows:
            answer_conversation_ids(
                checkpoint,
                str(row["question_id"]),
                [str(value) for value in row.get("answer_session_ids") or []],
            )
        print(
            f"dry run: {len(rows)} questions, answer conversations resolved; "
            "no calls will be made and no files are written"
        )
        return 0

    if args.output is None or args.max_spend_usd is None:
        parser.error("--output and --max-spend-usd are required unless --dry-run")

    document = asyncio.run(
        _run_label(
            rows=rows,
            checkpoint=checkpoint,
            output_path=args.output,
            max_spend_usd=args.max_spend_usd,
        )
    )
    return 0 if document["stop_reason"] is None else 1


def main(argv: Sequence[str] | None = None) -> int:
    """The real entry point: ``python -m ...dev2_labelling [label] ...``."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "label":
        arguments = arguments[1:]
    return label_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LABELLER_MAX_TOKENS",
    "LABELLER_MODEL",
    "LABELLER_REASONING_EFFORT",
    "LABELLER_ROUTE_ID",
    "LABELLER_SEED",
    "LABEL_RECORD_SCHEMA",
    "LABEL_SCHEMA",
    "MEMORY_SQL",
    "PROMPT_V1",
    "Label",
    "LabelInput",
    "artifact_hashes",
    "build_request",
    "label_main",
    "label_questions",
    "load_answer_session_memories",
    "main",
    "parse_label",
    "price_ceiling_reader",
    "request_template",
    "spot_check_sample",
    "verify_label_route",
]
