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
import fcntl
import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from tests.benchmark_longmemeval.dev2_pilot import (
    answer_conversation_ids,
    check_output_outside_repo,
)

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


SPOT_CHECK_REASON_TEXT: Final[dict[str, str]] = {
    "seeded_sample": "Seeded 15% sample",
    "answerable_false": "Every not-answerable label",
}


def _is_answerable_false_label(entry: Mapping[str, Any]) -> bool:
    """Not-answerable labels, including the entries that never reached the model."""
    if entry.get("reason") == "no_memories":
        return True
    return entry.get("schema_valid") is True and entry.get("answerable_from_store") is False


def spot_check_selection(
    labels: Sequence[Mapping[str, Any]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Spot-check selection: the frozen 15% sample, then every not-answerable label.

    ``labels`` is the label document's ``questions`` entries. The seeded draw is
    exactly ``spot_check_sample`` over every question id, in its own order, so
    its behaviour is unchanged. ``answerable_false`` covers every schema-valid
    label with ``answerable_from_store`` false and every ``no_memories`` entry
    (which carries ``answerable_from_store: false``). A question drawn by both
    appears once, with both reasons; the extra questions are sorted by id.
    """
    entries = [entry for entry in labels if isinstance(entry, Mapping)]
    seeded = spot_check_sample([str(entry["question_id"]) for entry in entries])

    reasons_by_question: dict[str, list[str]] = {}
    seeded_order: list[str] = []
    for question_id in seeded:
        reasons_by_question.setdefault(question_id, []).append("seeded_sample")
        seeded_order.append(question_id)

    additional: set[str] = set()
    seen: set[str] = set()
    for entry in entries:
        question_id = str(entry["question_id"])
        if question_id in seen:
            continue
        seen.add(question_id)
        if not _is_answerable_false_label(entry):
            continue
        reasons = reasons_by_question.get(question_id)
        if reasons is None:
            reasons_by_question[question_id] = ["answerable_false"]
            additional.add(question_id)
        else:
            reasons.append("answerable_false")

    ordered = [*seeded_order, *sorted(additional)]
    return [(question_id, tuple(reasons_by_question[question_id])) for question_id in ordered]


def build_spot_check_packet(
    selection: Sequence[tuple[str, Sequence[str]]],
    labels: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    checkpoint: Mapping[str, Any],
    memories_by_question: Mapping[str, Sequence[tuple[str, str]]],
) -> str:
    """Render the owner's spot-check packet. Pure: no connection, no provider call.

    Each selected question appears once, with its reason(s), question text,
    reference answer, label and note, the answer-session memories (labelled ids
    marked ``[LABELLED]``) and the checklist. The header states the rate rule.
    """
    entries = {str(entry["question_id"]): entry for entry in labels if isinstance(entry, Mapping)}
    rows_by_id = {str(row["question_id"]): row for row in rows}
    lines = [
        "# dev2 labelling spot-check packet",
        "",
        "Error-rate rule: the >20% stop is computed over the seeded sample only. "
        "Questions added because they are labelled not-answerable "
        "(`answerable_from_store: false`, including `no_memories`) were picked "
        "*because* of that label, so counting them would bias the rate. They are "
        "still checked and corrected when wrong.",
        "",
        "- `Seeded 15% sample`: the frozen 15% draw from `spot_check_sample`.",
        "- `Every not-answerable label`: every schema-valid "
        "`answerable_from_store: false` label, plus every `no_memories` entry.",
        f"- Selected questions: {len(selection)}",
    ]

    for question_id, reasons in selection:
        entry = entries[question_id]
        row = rows_by_id[question_id]
        lines += ["", f"## {question_id}", ""]
        lines.append(
            "Reason(s): " + ", ".join(SPOT_CHECK_REASON_TEXT[reason] for reason in reasons)
        )
        lines += [
            "",
            f"Question: {row['question']}",
            f"Reference answer: {row['answer']}",
            f"Labelled answerable_from_store: {str(bool(entry.get('answerable_from_store'))).lower()}",
        ]
        note = entry.get("note")
        if isinstance(note, str) and note:
            lines.append(f"Labeller's note: {note}")
        else:
            lines.append("Labeller's note: (none — no stored memories to label)")

        mapping = answer_conversation_ids(
            checkpoint,
            question_id,
            [str(value) for value in row.get("answer_session_ids") or []],
        )
        if mapping:
            lines += ["", "Answer sessions:"]
            lines += [f"- {session} -> {conversation}" for session, conversation in mapping.items()]

        memories = memories_by_question.get(question_id, ())
        lines.append("")
        if memories:
            labelled = {str(memory_id) for memory_id in entry.get("memory_ids") or []}
            lines.append("Answer-session memories:")
            for memory_id, text in memories:
                mark = "[LABELLED] " if memory_id in labelled else ""
                lines.append(f"- {mark}{memory_id}: {text}")
        else:
            lines.append("No answer-session memories were recorded for this question.")

        lines += [
            "",
            "Checklist:",
            "- labelled memory IDs correct?      yes / no — correction:",
            "- any memory missing from the label? yes / no — ids:",
            "- answerable_from_store correct?    yes / no — correction:",
            "- overall: accept / correct",
        ]

    return "\n".join(lines) + "\n"


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


JOURNAL_BATCH_VERSION: Final[int] = 1


def _read_journal(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any] | None]:
    """Resumed results, intents and the batch manifest. Corruption is fatal."""
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
            raise ValueError("Labelling journal is corrupt") from None
        if not isinstance(record, dict):
            raise ValueError("Labelling journal is corrupt")
        kind = record.get("kind")
        if kind == "batch":
            # Exactly one, first: it is what every later receipt is bound to.
            if batch is not None or results or intents:
                raise ValueError("Labelling journal is corrupt")
            batch = record
            continue
        question_id = record.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("Labelling journal is corrupt")
        if kind == "result":
            results[question_id] = {key: value for key, value in record.items() if key != "kind"}
        elif kind == "intent":
            intents.add(question_id)
        else:
            raise ValueError("Labelling journal is corrupt")
    return results, intents, batch


def _batch_manifest(question_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The resume binding: the frozen protocol and the selected rows, as hashes.

    Question and answer text, the user id and the answer-session ids never
    appear in the journal, only one digest per selected question. The first
    journal line is this record, made durable before the first dispatch, so a
    resume compares it exactly and stops instead of carrying stale labels
    across a changed selection, answer or protocol.
    """
    questions: dict[str, str] = {}
    for row in question_rows:
        question_id = str(row["question_id"])
        if question_id in questions:
            raise ValueError("Duplicate question id in the selected rows")
        questions[question_id] = _sha256(
            _canonical(
                {
                    "question": str(row["question"]),
                    "answer": str(row["answer"]),
                    "user_id": str(row["user_id"]),
                    "answer_session_ids": [
                        str(value) for value in row.get("answer_session_ids") or []
                    ],
                }
            )
        )
    return {
        "kind": "batch",
        "version": JOURNAL_BATCH_VERSION,
        "protocol": artifact_hashes(),
        "questions": questions,
    }


def _result_input_sha256(
    row: Mapping[str, Any],
    conversation_ids: Mapping[str, str],
    memories: Sequence[tuple[str, str]],
) -> str:
    """One question's exact inputs, as a digest.

    The frozen request (which carries the prompt, the schema and the template)
    plus the checkpoint's conversation mapping, so a corrected answer, a changed
    user, a changed memory text or a changed mapping all move the digest. With
    no memories there is no request, so the question fields and the mapping are
    hashed instead. Only the digest is stored: memory text never is.
    """
    fields: dict[str, Any] = {
        "question_id": str(row["question_id"]),
        "question": str(row["question"]),
        "answer": str(row["answer"]),
        "user_id": str(row["user_id"]),
        "conversations": dict(conversation_ids),
    }
    if memories:
        try:
            fields["request"] = build_request(
                LabelInput(
                    question_id=str(row["question_id"]),
                    question=str(row["question"]),
                    reference_answer=str(row["answer"]),
                    memories=tuple(memories),
                )
            )
        except ValueError:
            # An unusable input can never match a recorded digest.
            return ""
    return _sha256(_canonical(fields))


def _stopped_document(max_spend_usd: Decimal, stop_reason: str) -> dict[str, Any]:
    """The document shape for a run stopped before its journal was created."""
    return {
        "schema": LABEL_RECORD_SCHEMA,
        "route_id": LABELLER_ROUTE_ID,
        "max_spend_usd": str(max_spend_usd),
        "total_cost_usd": "0",
        "total_cost_complete": True,
        "unknown_cost_questions": [],
        "calls": 0,
        "stop_reason": stop_reason,
        "questions": [],
    }


async def _run_lock_held(check_run_lock: Callable[[], Any] | None) -> bool:
    """``True`` only on a positive answer; a missing or failing check is not held."""
    if check_run_lock is None:
        return False
    try:
        pending = check_run_lock()
        if inspect.isawaitable(pending):
            pending = await pending
    except Exception:
        return False
    return pending is True


async def _run_lock_acquired(acquire_run_lock: Callable[[], Any] | None) -> bool:
    """The benchmark-wide run lock, held before the journal is even opened.

    A missing callable, a raising callable and a refusal all read the same:
    nothing is dispatched, so no path skips the lock.
    """
    if acquire_run_lock is None:
        return False
    try:
        pending = acquire_run_lock()
        if inspect.isawaitable(pending):
            pending = await pending
    except Exception:
        return False
    return pending is True


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

    Priced from the production request bound (``compute_runtime._request_bound``),
    the same figure ``guarded_completion`` reserves: the messages, the
    per-message framing and the response schema all count, so an uncertain
    dispatch cannot settle above this ceiling. Output is charged at
    ``LABELLER_MAX_TOKENS``, under the route's pinned unit prices.
    """
    from orchestrator.compute_runtime import _request_bound

    if getattr(route, "price_ceiling", None) is None:
        raise ValueError("Labeller route has no price ceiling")

    def ceiling(payload: Mapping[str, Any]) -> Decimal:
        params = {
            key: value for key, value in payload.items() if key not in {"_route_id", "_exact_model"}
        }
        micro = route.estimate_microusd(_request_bound(params).bound, LABELLER_MAX_TOKENS)
        return Decimal(int(micro)) / Decimal(1_000_000)

    return ceiling


def ceiling_from_route(
    verify_route: Callable[[], Any],
) -> Callable[[Mapping[str, Any]], Decimal]:
    """The ceiling reader, resolving the verified route only on first use.

    ``_run_label`` builds this before ``label_questions`` has checked
    attestation, so the route must not be resolved eagerly: in a fresh process
    the attestation snapshot is not loaded yet and verification rejects it as
    unknown. The resolved reader is kept for the rest of the run.
    """
    cache: dict[str, Callable[[Mapping[str, Any]], Decimal]] = {}

    def ceiling(payload: Mapping[str, Any]) -> Decimal:
        reader = cache.get("reader")
        if reader is None:
            reader = price_ceiling_reader(verify_route())
            cache["reader"] = reader
        return reader(payload)

    return ceiling


async def label_questions(
    *,
    question_rows: Sequence[Mapping[str, Any]],
    checkpoint: Mapping[str, Any],
    output_path: Path | str,
    max_spend_usd: Decimal,
    call: Callable[[Mapping[str, Any]], Any],
    check_attestation: Callable[[], Any],
    acquire_run_lock: Callable[[], Any] | None = None,
    check_run_lock: Callable[[], Any] | None = None,
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

    ``acquire_run_lock`` is the benchmark-wide exclusive lock, taken before the
    journal is claimed and held for the whole run: a missing, raising or
    refused lock stops with ``run_lock_busy`` and creates nothing, so two
    invocations against different outputs can never both admit a call. A
    recorded result is reused only when the journal's batch manifest and the
    question's input digest still match; otherwise the run stops with
    ``receipt_mismatch`` and sends nothing.
    """
    from scripts.memory_query_decomposition import receipt_dict, reported_cost

    if not await _run_lock_acquired(acquire_run_lock):
        return _stopped_document(max_spend_usd, "run_lock_busy")

    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"Labelling output already exists: {output}")

    journal = _journal_path(output)
    try:
        handle = open(journal, "x", encoding="utf-8")
    except FileExistsError:
        handle = open(journal, "a", encoding="utf-8")

    try:
        # Exclusive for this whole run, created or resumed: two invocations must
        # never both see a question as unsent and dispatch it. State is read only
        # after the lock, because the holder may have just finished.
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(f"Labelling journal is held by another run: {journal}") from None
        if output.exists():
            raise FileExistsError(f"Labelling output already exists: {output}")
        results, intents, batch = _read_journal(journal)

        stop_reason: str | None = None
        expected_batch = _batch_manifest(question_rows)
        if batch is None:
            if results or intents:
                # Written before receipts were bound to their inputs: no manifest
                # to check, so nothing in it can be trusted.
                stop_reason = "receipt_mismatch"
            else:
                # Durable before the first dispatch: a resume compares it exactly.
                _append_journal(handle, expected_batch)
        elif batch != expected_batch:
            stop_reason = "receipt_mismatch"

        if stop_reason is None:
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

        for row in question_rows:
            if stop_reason is not None:
                break
            question_id = str(row["question_id"])
            user_id = str(row["user_id"])
            conversation_ids = answer_conversation_ids(
                checkpoint,
                question_id,
                [str(value) for value in row.get("answer_session_ids") or []],
            )

            recorded = results.get(question_id)
            if recorded is None and question_id in intents:
                # An intent without a result may already have been charged.
                stop_reason = "uncertain_dispatch"
                break

            memories = await load_answer_session_memories(
                memory_reader, decrypt, user_id, list(conversation_ids.values())
            )
            input_sha256 = _result_input_sha256(row, conversation_ids, memories)

            if recorded is not None:
                # Recomputed from the current checkpoint and memories, before the
                # receipt is accepted. No call is made to do this.
                if recorded.get("input_sha256") != input_sha256:
                    stop_reason = "receipt_mismatch"
                    break
                entry = {key: value for key, value in recorded.items() if key != "input_sha256"}
                entries.append(entry)
                calls += int(recorded.get("calls") or 0)
                if isinstance(recorded.get("cost_usd"), str):
                    total += Decimal(recorded["cost_usd"])
                continue
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
                _append_journal(handle, {"kind": "result", "input_sha256": input_sha256, **entry})
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

            # The session lock ends with its connection: confirm it directly before
            # this send. A call already in flight can't be recalled, but none follows.
            if not await _run_lock_held(check_run_lock):
                stop_reason = "run_lock_lost"
                break

            _append_journal(
                handle,
                {"kind": "intent", "question_id": question_id, "input_sha256": input_sha256},
            )
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
                _append_journal(handle, {"kind": "result", "input_sha256": input_sha256, **entry})
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
            _append_journal(handle, {"kind": "result", "input_sha256": input_sha256, **entry})
            entries.append(entry)

        # A call with no known cost still happened: the total is a lower bound, and
        # the ledger stays the authority for spend.
        unknown_cost = [e["question_id"] for e in entries if e.get("cost_usd") is None]
        document = {
            "schema": LABEL_RECORD_SCHEMA,
            "route_id": LABELLER_ROUTE_ID,
            "max_spend_usd": str(max_spend_usd),
            "total_cost_usd": str(total),
            "total_cost_complete": not unknown_cost
            and stop_reason not in {"cost_unknown", "uncertain_dispatch"},
            "unknown_cost_questions": unknown_cost,
            "calls": calls,
            "stop_reason": stop_reason,
            "questions": entries,
        }
        # An uncertain dispatch is not a final state: keep only the journal so a
        # rerun resumes it. A receipt mismatch is not a final state either: the
        # operator must reconcile the inputs, and no document may carry forward
        # labels that the current inputs do not explain. Every other stop is
        # final for this batch.
        if stop_reason not in {"uncertain_dispatch", "receipt_mismatch"}:
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
    from tests.benchmark_longmemeval.dev2_pilot import (
        SPEND_SQL,
        RUN_LOCK_HELD_SQL,
        acquire_benchmark_run_lock,
        spend_from_row,
    )
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

            async def acquire_run_lock() -> bool:
                # Session-scoped on this dedicated connection: held for the whole
                # run, released when the connection closes.
                return await acquire_benchmark_run_lock(connection.fetchval)

            async def check_run_lock() -> bool:
                return int(await connection.fetchval(RUN_LOCK_HELD_SQL) or 0) >= 1

            return await label_questions(
                question_rows=rows,
                checkpoint=checkpoint,
                output_path=output_path,
                max_spend_usd=max_spend_usd,
                call=call,
                check_attestation=lambda: attestation.check_once(pool),
                acquire_run_lock=acquire_run_lock,
                check_run_lock=check_run_lock,
                verify_route=verify_label_route,
                memory_reader=memory_reader,
                decrypt=encryption.decrypt,
                account_scope=account_scope,
                read_ledger_spend_usd=read_ledger_spend_usd,
                # Resolved on first use, after the attestation check above.
                next_call_ceiling_usd=ceiling_from_route(verify_label_route),
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


def _repository_root() -> Path:
    import subprocess

    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(completed.stdout.strip())


def _packet_labels(labels: Any, rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Validate the finished label document before anything is connected."""
    if not isinstance(labels, dict):
        raise ValueError("--labels must be a JSON object")
    if labels.get("stop_reason") is not None:
        raise ValueError(f"Labels document stopped, no packet: {labels['stop_reason']}")
    questions = labels.get("questions")
    if not isinstance(questions, list):
        raise ValueError("--labels must carry a questions list")
    for entry in questions:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("question_id"), str):
            raise ValueError("--labels questions must all carry a question_id")
    row_ids = {str(row["question_id"]) for row in rows}
    unknown = sorted({str(entry["question_id"]) for entry in questions} - row_ids)
    if unknown:
        raise ValueError(f"--labels holds question ids absent from the rows: {unknown}")
    return questions


async def _run_packet(
    *,
    rows: Sequence[Mapping[str, Any]],
    checkpoint: Mapping[str, Any],
    questions: Sequence[Mapping[str, Any]],
    selection: Sequence[tuple[str, Sequence[str]]],
    connector: Callable[[str], Any] | None = None,
    decrypt: Callable[[str], str] | None = None,
) -> str:
    """Read the answer-session memories read-only and render the packet.

    No provider, attestation, spend or lock is involved: this makes no paid call.
    """
    import asyncpg

    from orchestrator.config import get_settings
    from orchestrator.memory.encryption import ContentEncryption

    settings = get_settings()
    database_url = settings.database_url
    if not database_url:
        raise ValueError("database_url is not configured")
    connect = connector or asyncpg.connect
    connection = await connect(database_url)
    memories_by_question: dict[str, tuple[tuple[str, str], ...]] = {}
    try:
        decryption = decrypt or ContentEncryption(settings.daemon_encryption_key).decrypt

        def memory_reader(sql: str, params: Sequence[Any]) -> Any:
            return connection.fetch(sql, *params)

        rows_by_id = {str(row["question_id"]): row for row in rows}
        async with connection.transaction(readonly=True):
            for question_id, _reasons in selection:
                row = rows_by_id[question_id]
                conversation_ids = answer_conversation_ids(
                    checkpoint,
                    question_id,
                    [str(value) for value in row.get("answer_session_ids") or []],
                )
                memories_by_question[question_id] = await load_answer_session_memories(
                    memory_reader,
                    decryption,
                    str(row["user_id"]),
                    list(conversation_ids.values()),
                )
    finally:
        await connection.close()

    return build_spot_check_packet(selection, questions, rows, checkpoint, memories_by_question)


def packet_main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path | None = None,
    connector: Callable[[str], Any] | None = None,
    decrypt: Callable[[str], str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="dev2_labelling.py packet",
        description="Build the owner's spot-check packet from a finished label document.",
    )
    parser.add_argument("--question-rows", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Spot-check packet. Must be outside the repository; an existing packet refuses.",
    )
    args = parser.parse_args(argv)

    rows = json.loads(args.question_rows.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("--question-rows must be a JSON list")
    checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    if not isinstance(checkpoint, dict):
        raise ValueError("--checkpoint must be a JSON object")
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    questions = _packet_labels(labels, rows)

    # The packet quotes benchmark text, so it never lands in the checkout. Every
    # refusal below happens before a connection is opened.
    root = _repository_root() if repo_root is None else repo_root
    check_output_outside_repo(args.output, root)
    if args.output.exists():
        raise FileExistsError(f"Spot-check packet already exists: {args.output}")

    selection = spot_check_selection(questions)
    markdown = asyncio.run(
        _run_packet(
            rows=rows,
            checkpoint=checkpoint,
            questions=questions,
            selection=selection,
            connector=connector,
            decrypt=decrypt,
        )
    )
    # Exclusive: an existing packet is never overwritten, even one created
    # between the check above and this write.
    with open(args.output, "x", encoding="utf-8") as handle:
        handle.write(markdown)
    print(f"wrote {len(selection)} spot-check questions to {args.output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """The real entry point: ``python -m ...dev2_labelling [label|packet] ...``."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "packet":
        return packet_main(arguments[1:])
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
    "build_spot_check_packet",
    "ceiling_from_route",
    "label_main",
    "label_questions",
    "load_answer_session_memories",
    "main",
    "packet_main",
    "parse_label",
    "price_ceiling_reader",
    "request_template",
    "spot_check_sample",
    "spot_check_selection",
    "verify_label_route",
]
