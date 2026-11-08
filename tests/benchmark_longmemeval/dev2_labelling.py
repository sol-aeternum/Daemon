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

Final v3 contract; normal independent review/refreeze precedes paid execution.
Historical v1/v2 requests and packets remain readable, never reused as v3 labels.
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
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from tests.benchmark_longmemeval.dev2_pilot import (
    answer_conversation_ids,
    check_output_outside_repo,
    db_name_from_dsn,
)

LABELLER_MODEL: Final[str] = "openrouter/openai/gpt-6-luna"
LABELLER_ROUTE_ID: Final[str] = "luna-azure-eu"
LABELLER_SEED: Final[int] = 42
LABELLER_REASONING_EFFORT: Final[str] = "medium"
# The response is groups of memory ids plus one short note; retain the pinned
# token limit and reasoning headroom without changing the spend controls.
LABELLER_MAX_TOKENS: Final[int] = 4000

LABEL_SCHEMA_V1: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "memory_ids": {"type": "array", "items": {"type": "string"}},
        "answerable_from_store": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["memory_ids", "answerable_from_store", "note"],
    "additionalProperties": False,
}

LABEL_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "target_groups": {
            "type": "array",
            "items": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        },
        "answerable_from_store": {"type": "boolean"},
        "needs_memory_date": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["target_groups", "answerable_from_store", "needs_memory_date", "note"],
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

#: Labeller v2: v1's wording, plus the date and session context the owner ruled in.
#: The question's date and each memory's source session let a relative expression
#: in a memory ("last month", "recently") resolve against that memory's session
#: date, not against the question's or the wall clock's.
PROMPT_V2: Final[str] = (
    PROMPT_V1
    + " The input gives the question's date, and each memory's source session ID and session date."
    + " Resolve relative expressions in a memory ('last month', 'next week', 'recently') against"
    " that memory's session date, and compare the result with the question's date."
)

PROMPT_TEMPLATE_V2: Final[str] = (
    "Question date:\n{question_date}\n\n"
    "Question:\n{question}\n\n"
    "Reference answer:\n{reference_answer}\n\n"
    'Stored memories (JSON array of {{"id", "session_id", "session_date", "text"}}, '
    "sorted by session date, then session ID, then memory ID):\n{memories}"
)

LABEL_RECORD_SCHEMA_V1: Final[str] = "dev2-labelling/1"
#: v2 documents carry the question date and per-memory session id and date.
LABEL_RECORD_SCHEMA_V2: Final[str] = "dev2-labelling/2"
LABEL_RECORD_SCHEMA: Final[str] = "dev2-labelling/3"
LABEL_RECORD_SCHEMAS: Final[frozenset[str]] = frozenset(
    {LABEL_RECORD_SCHEMA_V1, LABEL_RECORD_SCHEMA_V2, LABEL_RECORD_SCHEMA}
)

PROMPT_V3: Final[str] = (
    "You label an evaluation set of stored memories. You are given a question, its date, "
    "its reference answer, and stored memories with id, text, source session ID, session date "
    "and stored valid_from date. Use only the supplied memory ids.\n"
    "Return target_groups: a list of nonempty lists of interchangeable memory ids. Each "
    "group represents one distinct fact or date needed to state the reference answer. Any "
    "one member satisfies that group; every group is required. Group paraphrases together, "
    "rather than requiring all redundant alternatives. Same-session memories providing the "
    "same needed fact/date via valid_from can also be interchangeable. Do not group "
    "complementary facts together. Include all supplied interchangeable alternatives; do "
    "not repeat an id within a group. A memory supporting multiple required facts may "
    "appear in multiple groups.\n"
    "Resolve relative expressions in a memory ('last month', 'next week', 'recently') "
    "against that memory's session date, not the question date or wall clock. Dates stored "
    "only in valid_from count as answerable evidence, even if absent from memory text. "
    "Set needs_memory_date to true when answering requires a stored date rather than text "
    "alone, and false otherwise. If a temporal cue was dropped at extraction, retain that "
    "observation in the note; do not invent the missing cue or assume session time proves "
    "an event date without support.\n"
    "Set answerable_from_store to false if the supplied text and stored dates do not "
    "support the reference answer, returning empty target_groups. Otherwise return at "
    "least one group and set answerable_from_store to true. The note is one short sentence "
    "explaining the choice.\n"
    'Return only {"target_groups": [[string, ...], ...], "answerable_from_store": boolean, '
    '"needs_memory_date": boolean, "note": string}.'
)
PROMPT_TEMPLATE_V3: Final[str] = PROMPT_TEMPLATE_V2.replace(
    '"session_date", "text"', '"session_date", "text", "valid_from"'
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


@dataclass(frozen=True)
class LabelV3:
    target_groups: tuple[tuple[str, ...], ...]
    answerable_from_store: bool
    needs_memory_date: bool
    note: str


def validate_target_groups(
    raw: Any, *, known_ids: set[str] | None = None
) -> tuple[tuple[str, ...], ...]:
    """Validate the closed group structure without echoing any input values."""
    if not isinstance(raw, list):
        raise ValueError("Label target_groups must be an array")
    groups: list[tuple[str, ...]] = []
    for group in raw:
        if not isinstance(group, list) or not group:
            raise ValueError("Label target_groups must contain nonempty arrays")
        seen: set[str] = set()
        for memory_id in group:
            if not isinstance(memory_id, str) or not memory_id.strip():
                raise ValueError("Label group ids must be nonempty strings")
            if memory_id in seen:
                raise ValueError("Label group ids must be unique within each group")
            if known_ids is not None and memory_id not in known_ids:
                raise ValueError("Label contains a memory id outside the input")
            seen.add(memory_id)
        groups.append(tuple(group))
    return tuple(groups)


#: One v2 memory: ``(memory id, session id, session date, text)``. The session
#: id and date come from the dataset, through the memory's source conversation.
MemoryV2 = tuple[str, str, str, str]


@dataclass(frozen=True)
class LabelInputV2:
    """One v2 labelling input: the question date and dated, attributed memories."""

    question_id: str
    question: str
    question_date: str
    reference_answer: str
    memories: tuple[MemoryV2, ...]

    def __post_init__(self) -> None:
        for name in ("question_id", "question", "question_date", "reference_answer"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Label input {name} must be a nonempty string")
        entries: list[MemoryV2] = []
        for entry in self.memories:
            if not isinstance(entry, tuple) or len(entry) != 4:
                raise ValueError(
                    "Label input memories must be (id, session_id, session_date, text) tuples"
                )
            memory_id, session_id, session_date, text = entry
            if not isinstance(memory_id, str) or not memory_id:
                raise ValueError("Label input memory ids must be nonempty strings")
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("Label input memory session ids must be nonempty strings")
            if not isinstance(session_date, str) or not session_date:
                raise ValueError("Label input memory session dates must be nonempty strings")
            if not isinstance(text, str):
                raise ValueError("Label input memory text must be a string")
            entries.append((memory_id, session_id, session_date, text))
        if len({entry[0] for entry in entries}) != len(entries):
            raise ValueError("Label input memory ids must be unique")
        # Date, then session, then memory id: independent of any ranking, and stable
        # for tied session dates.
        object.__setattr__(
            self,
            "memories",
            tuple(sorted(entries, key=lambda entry: (entry[2], entry[1], entry[0]))),
        )


MemoryV3 = tuple[str, str, str, str, str]


def _stored_date(value: Any) -> str:
    """The stored timestamp, never substituted with a dataset or wall-clock date."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("Memory valid_from is missing or unparsable") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Memory valid_from must be a timezone-aware timestamp")
    return value.isoformat()


@dataclass(frozen=True)
class LabelInputV3:
    question_id: str
    question: str
    question_date: str
    reference_answer: str
    memories: tuple[MemoryV3, ...]

    def __post_init__(self) -> None:
        if any(not isinstance(entry, tuple) or len(entry) != 5 for entry in self.memories):
            raise ValueError("Label input v3 memories require stored valid_from")
        base = LabelInputV2(
            self.question_id,
            self.question,
            self.question_date,
            self.reference_answer,
            tuple((mid, sid, date, text) for mid, sid, date, text, _ in self.memories),
        )
        dates = {entry[0]: _stored_date(entry[4]) for entry in self.memories}
        object.__setattr__(
            self, "memories", tuple((*entry, dates[entry[0]]) for entry in base.memories)
        )


def _memory_block(item: LabelInput) -> str:
    return json.dumps(
        [{"id": memory_id, "text": text} for memory_id, text in item.memories],
        ensure_ascii=False,
        sort_keys=True,
    )


def _memory_block_v2(item: LabelInputV2) -> str:
    return json.dumps(
        [
            {"id": memory_id, "session_id": session_id, "session_date": session_date, "text": text}
            for memory_id, session_id, session_date, text in item.memories
        ],
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
            "json_schema": {"name": "memory_labels", "strict": True, "schema": LABEL_SCHEMA_V1},
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


def request_template_v2() -> dict[str, Any]:
    """The v2 request template: same route, seed, effort and schema as v1."""
    template = request_template()
    template["messages"] = [
        {"role": "system", "content": PROMPT_V2},
        {"role": "user", "content": PROMPT_TEMPLATE_V2},
    ]
    return template


def build_request_v2(item: LabelInputV2) -> dict[str, Any]:
    if not isinstance(item, LabelInputV2):
        raise ValueError("build_request_v2 expects a LabelInputV2")
    request = request_template_v2()
    request["messages"] = [
        {"role": "system", "content": PROMPT_V2},
        {
            "role": "user",
            "content": PROMPT_TEMPLATE_V2.format(
                question_date=item.question_date,
                question=item.question,
                reference_answer=item.reference_answer,
                memories=_memory_block_v2(item),
            ),
        },
    ]
    return request


def request_template_v3() -> dict[str, Any]:
    template = request_template()
    template["messages"] = [
        {"role": "system", "content": PROMPT_V3},
        {"role": "user", "content": PROMPT_TEMPLATE_V3},
    ]
    template["response_format"]["json_schema"]["schema"] = LABEL_SCHEMA
    return template


def build_request_v3(item: LabelInputV3) -> dict[str, Any]:
    if not isinstance(item, LabelInputV3):
        raise ValueError("build_request_v3 expects a LabelInputV3")
    request = request_template_v3()
    memories = [
        {"id": mid, "session_id": sid, "session_date": date, "text": text, "valid_from": stored}
        for mid, sid, date, text, stored in item.memories
    ]
    request["messages"][1]["content"] = PROMPT_TEMPLATE_V3.format(
        question_date=item.question_date,
        question=item.question,
        reference_answer=item.reference_answer,
        memories=json.dumps(memories, ensure_ascii=False, sort_keys=True),
    )
    return request


def parse_label_v3(item: LabelInputV3, text: str) -> LabelV3:
    if not isinstance(item, LabelInputV3):
        raise ValueError("parse_label_v3 expects a LabelInputV3")
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("Label is not valid JSON") from None
    if not isinstance(value, dict) or set(value) != set(LABEL_SCHEMA["required"]):
        raise ValueError("Label v3 fields mismatch")
    groups = validate_target_groups(
        value["target_groups"], known_ids={entry[0] for entry in item.memories}
    )
    if type(value["answerable_from_store"]) is not bool:
        raise ValueError("Label answerable_from_store must be a boolean")
    if type(value["needs_memory_date"]) is not bool:
        raise ValueError("Label needs_memory_date must be a boolean")
    if not isinstance(value["note"], str):
        raise ValueError("Label note must be a string")
    if bool(groups) != value["answerable_from_store"]:
        raise ValueError("Label target_groups emptiness disagrees with answerable_from_store")
    return LabelV3(
        groups, value["answerable_from_store"], value["needs_memory_date"], value["note"]
    )


def parse_label(item: LabelInput | LabelInputV2, text: str) -> Label:
    """Strict validation. Messages name fields, never memory text or values."""
    if not isinstance(item, (LabelInput, LabelInputV2)):
        raise ValueError("parse_label expects a label input")
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
    known = {str(entry[0]) for entry in item.memories}
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


MEMORY_SQL_V2: Final[str] = (
    "SELECT id, source_conversation_id, content FROM memories "
    "WHERE user_id = $1 AND source_conversation_id = ANY($2::uuid[]) "
    "AND status = 'active' ORDER BY id"
)


async def load_answer_session_memories_v2(
    reader: Callable[[str, Sequence[Any]], Any],
    decrypt: Callable[[str], str],
    user_id: str,
    conversation_ids: Sequence[str],
) -> tuple[tuple[str, str, str], ...]:
    """Read the answer-session memories with their source conversation.

    Only ``memories`` is touched, and only its id, ``source_conversation_id``
    and encrypted content: any timestamp column the row happens to carry is
    never read, so a memory's date can only come from the dataset.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not conversation_ids:
        return ()
    pending = reader(MEMORY_SQL_V2, (user_id, list(conversation_ids)))
    rows = await pending if inspect.isawaitable(pending) else pending
    triples: list[tuple[str, str, str]] = []
    for row in _rows(rows):
        entry: Any = row
        memory_id = str(entry[0])
        conversation_id = entry[1]
        if isinstance(conversation_id, uuid.UUID):
            conversation_id = str(conversation_id)  # asyncpg returns uuid columns as UUID
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("Memory has no source conversation")
        content = entry[2]
        if not isinstance(content, str):
            raise ValueError("Memory content must be a string")
        decrypted = decrypt(content)
        if inspect.isawaitable(decrypted):
            decrypted = await decrypted
        if not isinstance(decrypted, str):
            raise ValueError("Decrypted memory content must be a string")
        triples.append((memory_id, conversation_id, decrypted))
    return tuple(sorted(triples, key=lambda entry: entry[0]))


MEMORY_SQL_V3: Final[str] = MEMORY_SQL_V2.replace(
    "source_conversation_id, content", "source_conversation_id, content, valid_from"
)


async def load_answer_session_memories_v3(
    reader: Callable[[str, Sequence[Any]], Any],
    decrypt: Callable[[str], str],
    user_id: str,
    conversation_ids: Sequence[str],
) -> tuple[tuple[str, str, str, str], ...]:
    if not user_id:
        raise ValueError("user_id is required")
    if not conversation_ids:
        return ()
    pending = reader(MEMORY_SQL_V3, (user_id, list(conversation_ids)))
    rows = await pending if inspect.isawaitable(pending) else pending
    entries: list[tuple[str, str, str, str]] = []
    for row in _rows(rows):
        if len(row) != 4 or not isinstance(row[2], str):
            raise ValueError("Memory v3 row must carry id, source, content and valid_from")
        conversation_id = row[1]
        if isinstance(conversation_id, uuid.UUID):
            conversation_id = str(conversation_id)
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("Memory has no source conversation")
        text = decrypt(row[2])
        if inspect.isawaitable(text):
            text = await text
        if not isinstance(text, str):
            raise ValueError("Decrypted memory content must be a string")
        entries.append((str(row[0]), conversation_id, text, _stored_date(row[3])))
    return tuple(sorted(entries))


def _iso_dataset_date(value: Any) -> str:
    """A dataset date as ``YYYY-MM-DD HH:MM``; an unparsable one refuses."""
    from orchestrator.eval.fact_harness import _parse_haystack_date

    parsed = _parse_haystack_date(value)
    if parsed is None:
        raise ValueError("Dataset date missing or unparsable")
    return parsed.strftime("%Y-%m-%d %H:%M")


def validate_question_rows_v2(rows: Sequence[Mapping[str, Any]]) -> None:
    """Refuse a v2 rows file that no question could ever be labelled from.

    Every v2 label needs the question's own date and each memory's session id
    and date, and those come only from the dataset fields the row carries. A
    row missing any of ``question_date``, ``haystack_session_ids`` or
    ``haystack_dates``, or whose two haystack lists disagree in length, is
    refused before a journal or a document exists: a run must never finish as
    a complete-looking document of not-answerable labels. The error carries
    only the question id and the field, never memory text.
    """
    for index, row in enumerate(rows):
        question_id = str(row.get("question_id") or f"row {index}")
        if not str(row.get("question_date") or "").strip():
            raise ValueError(f"{question_id}: question_date is missing or empty")
        session_ids = row.get("haystack_session_ids")
        dates = row.get("haystack_dates")
        if not isinstance(session_ids, (list, tuple)) or not session_ids:
            raise ValueError(f"{question_id}: haystack_session_ids is missing or empty")
        if not isinstance(dates, (list, tuple)) or not dates:
            raise ValueError(f"{question_id}: haystack_dates is missing or empty")
        if len(session_ids) != len(dates):
            raise ValueError(
                f"{question_id}: haystack_session_ids and haystack_dates disagree in length"
            )


def session_dates_from_row(row: Mapping[str, Any]) -> dict[str, str]:
    """``session id -> date`` from the dataset fields carried by the row.

    The dates come from ``haystack_dates`` paired with ``haystack_session_ids``.
    This helper reads no database timestamp; v3 separately reads stored valid_from.
    """
    session_ids = [str(value) for value in row.get("haystack_session_ids") or []]
    raw_dates = list(row.get("haystack_dates") or [])
    if len(session_ids) != len(raw_dates):
        raise ValueError("haystack_session_ids and haystack_dates disagree")
    dates: dict[str, str] = {}
    for session_id, raw in zip(session_ids, raw_dates):
        if session_id in dates:
            raise ValueError("haystack_session_ids holds duplicates")
        dates[session_id] = _iso_dataset_date(raw)
    return dates


def build_label_input_v2(
    row: Mapping[str, Any],
    conversation_ids: Mapping[str, str],
    memories: Sequence[tuple[str, str, str]],
) -> LabelInputV2:
    """Attach the dataset's session id and date to each memory.

    A memory is attributed through its ``source_conversation_id`` and the
    checkpoint's existing session-to-conversation mapping. A memory with no
    attribution, or a session with no date, refuses the question.
    """
    try:
        question_date = _iso_dataset_date(row.get("question_date"))
    except ValueError:
        raise ValueError("Question date missing or unparsable") from None
    dates = session_dates_from_row(row)
    by_conversation: dict[str, str] = {}
    for session_id, conversation_id in conversation_ids.items():
        if str(conversation_id) in by_conversation:
            raise ValueError("Two sessions map to one conversation")
        by_conversation[str(conversation_id)] = str(session_id)
    attributed: list[MemoryV2] = []
    for memory_id, conversation_id, text in memories:
        session_id = by_conversation.get(str(conversation_id))
        if session_id is None:
            raise ValueError("Memory has no session attribution")
        session_date = dates.get(session_id)
        if session_date is None:
            raise ValueError("Memory session has no date")
        attributed.append((str(memory_id), session_id, session_date, text))
    return LabelInputV2(
        question_id=str(row["question_id"]),
        question=str(row["question"]),
        question_date=question_date,
        reference_answer=str(row["answer"]),
        memories=tuple(attributed),
    )


def build_label_input_v3(
    row: Mapping[str, Any],
    conversation_ids: Mapping[str, str],
    memories: Sequence[tuple[str, str, str, str]],
) -> LabelInputV3:
    base = build_label_input_v2(
        row, conversation_ids, [(mid, cid, text) for mid, cid, text, _ in memories]
    )
    dates = {mid: stored for mid, _, _, stored in memories}
    return LabelInputV3(
        base.question_id,
        base.question,
        base.question_date,
        base.reference_answer,
        tuple((*entry, dates[entry[0]]) for entry in base.memories),
    )


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


def seeded_spot_check_changes(
    v2_labels: Sequence[Mapping[str, Any]],
    v3_labels: Sequence[Mapping[str, Any]],
    seeded_ids: Sequence[str],
) -> tuple[str, ...]:
    """Only seeded questions losing a v2 target need rechecking under the owner rule.

    Callers supply the original seeded draw, not a fresh sample of the changed
    labels. Empty output means every seeded union is a superset, so the seeded
    spot-check carries. This does not waive review of not-answerable additions.
    """

    def indexed(labels: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for entry in labels:
            qid = entry.get("question_id")
            if not isinstance(qid, str) or not qid or qid in result:
                raise ValueError("Spot-check comparison needs unique question ids")
            result[qid] = entry
        return result

    before, after = indexed(v2_labels), indexed(v3_labels)
    if not seeded_ids or len(set(seeded_ids)) != len(seeded_ids):
        raise ValueError("Spot-check comparison needs a nonempty unique seeded draw")
    changed: list[str] = []
    for qid in seeded_ids:
        if qid not in before or qid not in after:
            raise ValueError("Spot-check comparison is missing a seeded question")
        old, new = before[qid], after[qid]
        old_no_memories = (
            old.get("reason") == "no_memories" and old.get("answerable_from_store") is False
        )
        new_no_memories = (
            new.get("reason") == "no_memories" and new.get("answerable_from_store") is False
        )
        if (old.get("schema_valid") is not True and not old_no_memories) or (
            new.get("schema_valid") is not True and not new_no_memories
        ):
            raise ValueError("Spot-check comparison requires valid labels")
        old_ids = [] if old_no_memories else old.get("memory_ids")
        if not isinstance(old_ids, list) or any(
            not isinstance(mid, str) or not mid for mid in old_ids
        ):
            raise ValueError("Spot-check comparison requires v2 memory_ids")
        groups = () if new_no_memories else validate_target_groups(new.get("target_groups"))
        if not set(old_ids) <= {mid for group in groups for mid in group}:
            changed.append(qid)
    return tuple(sorted(changed))


def _packet_label_line(entry: Mapping[str, Any]) -> str:
    """State the label as it is: an invalid reply is never shown as a negative label."""
    if entry.get("schema_valid") is True:
        value = str(entry.get("answerable_from_store") is True).lower()
        return f"Labelled answerable_from_store: {value}"
    reason = str(entry.get("reason") or "unknown")
    if reason == "no_memories":
        return "Labelled answerable_from_store: false (no stored memories in the answer sessions)"
    return f"No valid label: the labeller's reply was unusable ({reason})"


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
        "Error-rate rule (owner ruling): the >20% stop is computed over the seeded "
        "sample only. Questions added because they are labelled not-answerable "
        "(`answerable_from_store: false`, including `no_memories`) were picked "
        "*because* of that label, so they get their own correction rate: above 20% "
        "flags for owner review. They are still checked and corrected when wrong.",
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
            _packet_label_line(entry),
        ]
        note = entry.get("note")
        if isinstance(note, str) and note:
            lines.append(f"Labeller's note: {note}")
        else:
            lines.append("Labeller's note: (none)")

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


def build_spot_check_packet_v2(
    selection: Sequence[tuple[str, Sequence[str]]],
    labels: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    checkpoint: Mapping[str, Any],
    memories_by_question: Mapping[str, Sequence[MemoryV2] | Sequence[MemoryV3]],
    *,
    label_schema: str,
    prompt_sha256: str,
) -> str:
    """Render a dated v2/v3 spot-check packet, preserving its record version.

    The input's session framing is preserved: memories are grouped by session in
    date order, each group headed by its session id and date, so the owner can
    judge a relative expression against the memory's own session date.
    """
    v3 = label_schema == LABEL_RECORD_SCHEMA
    version = "v3" if v3 else "v2"
    entries = {str(entry["question_id"]): entry for entry in labels if isinstance(entry, Mapping)}
    rows_by_id = {str(row["question_id"]): row for row in rows}
    lines = [
        f"# dev2 labelling spot-check packet ({version})",
        "",
        f"Labeller version: `{label_schema}`",
        f"Prompt hash ({version}): `{prompt_sha256}`",
        "",
        "Error-rate rule (owner ruling): the >20% stop is computed over the seeded "
        "sample only. Questions added because they are labelled not-answerable "
        "(`answerable_from_store: false`, including `no_memories`) were picked "
        "*because* of that label, so they get their own correction rate: above 20% "
        "flags for owner review. They are still checked and corrected when wrong.",
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
        try:
            question_date = _iso_dataset_date(row.get("question_date"))
        except ValueError:
            question_date = "(missing)"
        lines += [
            "",
            f"Question date: {question_date}",
            f"Question: {row['question']}",
            f"Reference answer: {row['answer']}",
            _packet_label_line(entry),
        ]
        if v3 and entry.get("schema_valid") is True:
            lines.append(f"needs_memory_date: {str(entry['needs_memory_date']).lower()}")
            lines.append("Target groups (any member per group; every group required):")
            lines += [
                f"- Group {index}: " + ", ".join(group)
                for index, group in enumerate(entry["target_groups"], start=1)
            ]
        note = entry.get("note")
        if isinstance(note, str) and note:
            lines.append(f"Labeller's note: {note}")
        else:
            lines.append("Labeller's note: (none)")

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
            labelled = (
                {mid for group in entry.get("target_groups") or [] for mid in group}
                if v3
                else {str(memory_id) for memory_id in entry.get("memory_ids") or []}
            )
            groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
            for memory in memories:
                memory_id, session_id, session_date, text = memory[:4]
                if v3:
                    if len(memory) != 5:
                        raise ValueError("V3 packet memories require stored valid_from")
                    text = f"{text} (valid_from: {memory[4]})"
                groups.setdefault((session_date, session_id), []).append((memory_id, text))
            lines.append("Answer-session memories (grouped by session, sessions in date order):")
            for session_date, session_id in sorted(groups):
                lines += ["", f"### Session {session_id} ({session_date})"]
                for memory_id, text in sorted(groups[(session_date, session_id)]):
                    mark = "[LABELLED] " if memory_id in labelled else ""
                    lines.append(f"- {mark}{memory_id}: {text}")
        else:
            lines.append("No answer-session memories were recorded for this question.")

        lines += [
            "",
            "Checklist:",
            "- target groups/interchangeability correct? yes / no — correction:"
            if v3
            else "- labelled memory IDs correct?      yes / no — correction:",
            "- any group or alternative missing? yes / no — ids:"
            if v3
            else "- any memory missing from the label? yes / no — ids:",
            "- answerable_from_store correct?    yes / no — correction:",
            *(["- needs_memory_date correct? yes / no — correction:"] if v3 else []),
            "- overall: accept / correct",
        ]

    return "\n".join(lines) + "\n"


def artifact_hashes() -> dict[str, str]:
    """Historical v1/v2 pins plus the final v3 contract (no further redesign)."""
    return {
        "prompt": _sha256(PROMPT_V1),
        "prompt_v2": _sha256(PROMPT_V2),
        "label_schema": _sha256(_canonical(LABEL_SCHEMA_V1)),
        "label_record_schema_v2": _sha256(LABEL_RECORD_SCHEMA_V2),
        "request_template": _sha256(_canonical(request_template())),
        "request_template_v2": _sha256(_canonical(request_template_v2())),
        "prompt_v3": _sha256(PROMPT_V3),
        "label_schema_v3": _sha256(_canonical(LABEL_SCHEMA)),
        "label_record_schema_v3": _sha256(LABEL_RECORD_SCHEMA),
        "request_template_v3": _sha256(_canonical(request_template_v3())),
    }


# --- ``label`` command --------------------------------------------------------
#
# The runbook (`dev2_labelling.md`) owns the route precondition, the attestation
# and the cost gate. This section only wires them: the call, the attestation, the
# route check, the memory reader and the decryption are injected, so the tests
# drive the whole flow with fakes and no provider, database or dataset is touched
# by this module's tests.

#: Dated labelling runs on the remapped copy only; preserve the original evidence.
LABEL_DATABASE_NAME_V2: Final[str] = "daemon_lme_bench_dev2_final_remap"


def assert_label_database_v2(dsn: str | None) -> str:
    """Refuse unless the connection names the v2 database exactly."""
    name = db_name_from_dsn(dsn) if dsn else None
    if name != LABEL_DATABASE_NAME_V2:
        raise ValueError(
            f"Labeller v2 runs on {LABEL_DATABASE_NAME_V2}; refusing database {name!r}"
        )
    return name


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
                    # v2: the dataset dates are bound too, so a corrected date
                    # cannot silently reuse a label made without it.
                    "question_date": str(row.get("question_date") or ""),
                    "haystack_session_ids": [
                        str(value) for value in row.get("haystack_session_ids") or []
                    ],
                    "haystack_dates": [str(value) for value in row.get("haystack_dates") or []],
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
    memories: Sequence[tuple[str, str, str, str]],
) -> str:
    """One question's exact inputs, as a digest.

    The frozen request (which carries the prompt, the schema, the template, the
    question date and every memory's session id, date and stored valid_from) plus the checkpoint's
    conversation mapping, so a corrected answer, a changed user, date or memory
    text, or a changed mapping all move the digest. With no memories there is no
    request, so the question fields and the mapping are hashed instead. Only the
    digest is stored: memory text never is.
    """
    fields: dict[str, Any] = {
        "question_id": str(row["question_id"]),
        "question": str(row["question"]),
        "answer": str(row["answer"]),
        "user_id": str(row["user_id"]),
        "question_date": str(row.get("question_date") or ""),
        "haystack_session_ids": [str(value) for value in row.get("haystack_session_ids") or []],
        "haystack_dates": [str(value) for value in row.get("haystack_dates") or []],
        "conversations": dict(conversation_ids),
    }
    if memories:
        try:
            fields["request"] = build_request_v3(
                build_label_input_v3(row, conversation_ids, memories)
            )
        except ValueError:
            # An unusable input can never match a recorded digest.
            return ""
    return _sha256(_canonical(fields))


def _stopped_document(max_spend_usd: Decimal, stop_reason: str) -> dict[str, Any]:
    """The document shape for a run stopped before its journal was created."""
    return {
        "schema": LABEL_RECORD_SCHEMA,
        "artifacts": artifact_hashes(),
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
    ``receipt_mismatch`` and sends nothing. A question whose memories cannot be
    attributed to a dataset session and date stops the run with
    ``unattributable`` before any dispatch, and is never recorded as a
    not-answerable label.
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

            memories = await load_answer_session_memories_v3(
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

            try:
                item = build_label_input_v3(row, conversation_ids, memories)
            except ValueError:
                # No dataset session or date for a memory: no label can be
                # attributed, so the run stops here. This is never an entry: a
                # refused question must not ride along as a not-answerable label.
                stop_reason = "unattributable"
                break
            payload = {
                "_route_id": LABELLER_ROUTE_ID,
                "_exact_model": True,
                **build_request_v3(item),
            }

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
                label = parse_label_v3(item, _response_text(receipt))
            except ValueError:
                label = None
            if label is None:
                entry["schema_valid"] = False
                entry["reason"] = "parse_failure"
            else:
                entry["schema_valid"] = True
                entry["answerable_from_store"] = label.answerable_from_store
                entry["target_groups"] = [list(group) for group in label.target_groups]
                entry["needs_memory_date"] = label.needs_memory_date
                entry["note"] = label.note
            _append_journal(handle, {"kind": "result", "input_sha256": input_sha256, **entry})
            entries.append(entry)

        # A call with no known cost still happened: the total is a lower bound, and
        # the ledger stays the authority for spend.
        unknown_cost = [e["question_id"] for e in entries if e.get("cost_usd") is None]
        document = {
            "schema": LABEL_RECORD_SCHEMA,
            "artifacts": artifact_hashes(),
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
    validate_question_rows_v2(rows)
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
    assert_label_database_v2(settings.database_url)
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
    try:
        # Before any journal, document, lock or call: a rows file the v2 fields
        # cannot attribute is refused whole, so it never becomes a run.
        validate_question_rows_v2(rows)
    except ValueError as error:
        parser.error(str(error))
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


def _read_population(path: Path) -> set[str]:
    """The frozen question IDs this packet must cover: a list or ``question_ids``."""
    value = json.loads(path.read_text(encoding="utf-8"))
    ids = value.get("question_ids") if isinstance(value, Mapping) else value
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
        raise ValueError("--population must hold a non-empty list of question ids")
    if len(set(ids)) != len(ids):
        raise ValueError("--population holds duplicate question ids")
    return set(ids)


def _check_packet_entry(entry: Mapping[str, Any], *, v3: bool = False) -> None:
    """A label is whole or plainly invalid; a truncated one must not read as a judgment."""
    question_id = entry["question_id"]
    if entry.get("schema_valid") is True:
        answerable = entry.get("answerable_from_store")
        if type(answerable) is not bool:
            raise ValueError(f"--labels {question_id}: answerable_from_store must be a boolean")
        if v3:
            groups = validate_target_groups(entry.get("target_groups"))
            if bool(groups) != answerable:
                raise ValueError("Label groups disagree with answerable_from_store")
            if type(entry.get("needs_memory_date")) is not bool:
                raise ValueError("Label needs_memory_date must be a boolean")
            if "memory_ids" in entry:
                raise ValueError("Label v3 must not carry legacy memory_ids")
        elif not isinstance(entry.get("memory_ids"), list):
            raise ValueError(f"--labels {question_id}: memory_ids and note are required")
        if not isinstance(entry.get("note"), str):
            raise ValueError(f"--labels {question_id}: note is required")
    elif entry.get("schema_valid") is False:
        if not isinstance(entry.get("reason"), str):
            raise ValueError(f"--labels {question_id}: an invalid entry must carry its reason")
    else:
        raise ValueError(f"--labels {question_id}: schema_valid must be a boolean")


def _packet_labels(
    labels: Any, rows: Sequence[Mapping[str, Any]], population: set[str]
) -> list[Mapping[str, Any]]:
    """Validate the finished label document before anything is connected."""
    if not isinstance(labels, dict):
        raise ValueError("--labels must be a JSON object")
    schema = labels.get("schema")
    if schema not in LABEL_RECORD_SCHEMAS:
        raise ValueError("--labels has an unknown record schema")
    if schema in {LABEL_RECORD_SCHEMA_V2, LABEL_RECORD_SCHEMA}:
        # The packet's header states the prompt the labels were made with.
        artifacts = labels.get("artifacts")
        key = "prompt_v3" if schema == LABEL_RECORD_SCHEMA else "prompt_v2"
        if not isinstance(artifacts, Mapping) or not isinstance(artifacts.get(key), str):
            raise ValueError("--labels dated document has no prompt hash in `artifacts`")
    if labels.get("stop_reason") is not None:
        raise ValueError(f"Labels document stopped, no packet: {labels['stop_reason']}")
    questions = labels.get("questions")
    if not isinstance(questions, list):
        raise ValueError("--labels must carry a questions list")
    for entry in questions:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("question_id"), str):
            raise ValueError("--labels questions must all carry a question_id")
    label_ids = [str(entry["question_id"]) for entry in questions]
    if len(set(label_ids)) != len(label_ids):
        raise ValueError("--labels holds duplicate question ids")
    row_list = [str(row["question_id"]) for row in rows]
    row_ids = set(row_list)
    if len(row_ids) != len(row_list):
        raise ValueError("--question-rows holds duplicate question ids")
    # Rows and labels can agree on the same incomplete subset, so both answer to the
    # frozen population, not only to each other.
    if row_ids != population:
        raise ValueError("--question-rows does not match the frozen --population")
    for entry in questions:
        _check_packet_entry(entry, v3=schema == LABEL_RECORD_SCHEMA)
    unknown = sorted(set(label_ids) - row_ids)
    if unknown:
        raise ValueError(f"--labels holds question ids absent from the rows: {unknown}")
    # The 15% draw and the not-answerable additions are only meaningful over the whole
    # batch: a partial one would sample the wrong population and hide absent labels.
    missing = sorted(row_ids - set(label_ids))
    if missing:
        raise ValueError(f"--labels is missing question ids from the rows: {missing}")
    return questions


async def _run_packet(
    *,
    labels: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    checkpoint: Mapping[str, Any],
    questions: Sequence[Mapping[str, Any]],
    selection: Sequence[tuple[str, Sequence[str]]],
    connector: Callable[[str], Any] | None = None,
    decrypt: Callable[[str], str] | None = None,
) -> str:
    """Read the answer-session memories read-only and render the packet.

    A v3 document reads actual valid_from dates and displays target groups. A
    v2 document reads the dated v2 input and renders the v2 packet, headed by
    the labeller version and the prompt hash recorded in ``artifacts``. A v1
    document keeps v1's rendering. No provider, attestation, spend or lock is
    involved: this makes no paid call.
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
    label_schema = str(labels["schema"])
    v3 = label_schema == LABEL_RECORD_SCHEMA
    dated = label_schema in {LABEL_RECORD_SCHEMA_V2, LABEL_RECORD_SCHEMA}
    memories_by_question: dict[str, Any] = {}
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
                if v3:
                    loaded_v3 = await load_answer_session_memories_v3(
                        memory_reader,
                        decryption,
                        str(row["user_id"]),
                        list(conversation_ids.values()),
                    )
                    item_v3 = build_label_input_v3(row, conversation_ids, loaded_v3)
                    # Reject a packet naming targets no longer in its input too.
                    if questions:
                        entry = next(e for e in questions if str(e["question_id"]) == question_id)
                        if entry.get("schema_valid") is True:
                            validate_target_groups(
                                entry["target_groups"], known_ids={e[0] for e in item_v3.memories}
                            )
                    memories_by_question[question_id] = item_v3.memories
                elif dated:
                    loaded = await load_answer_session_memories_v2(
                        memory_reader,
                        decryption,
                        str(row["user_id"]),
                        list(conversation_ids.values()),
                    )
                    try:
                        item = build_label_input_v2(row, conversation_ids, loaded)
                    except ValueError:
                        # The labeller refused this question for the same reason:
                        # show it with no dated memories rather than a guessed date.
                        memories_by_question[question_id] = ()
                    else:
                        memories_by_question[question_id] = item.memories
                else:
                    memories_by_question[question_id] = await load_answer_session_memories(
                        memory_reader,
                        decryption,
                        str(row["user_id"]),
                        list(conversation_ids.values()),
                    )
    finally:
        await connection.close()

    if dated:
        artifacts = labels["artifacts"]
        return build_spot_check_packet_v2(
            selection,
            questions,
            rows,
            checkpoint,
            memories_by_question,
            label_schema=label_schema,
            prompt_sha256=str(artifacts["prompt_v3" if v3 else "prompt_v2"]),
        )
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
        "--population",
        type=Path,
        required=True,
        help="Frozen question IDs the batch must cover: a JSON list or a question_ids object.",
    )
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
    questions = _packet_labels(labels, rows, _read_population(args.population))

    # The packet quotes benchmark text, so it never lands in the checkout. Every
    # refusal below happens before a connection is opened.
    root = _repository_root() if repo_root is None else repo_root
    check_output_outside_repo(args.output, root)
    if args.output.exists():
        raise FileExistsError(f"Spot-check packet already exists: {args.output}")

    selection = spot_check_selection(questions)
    markdown = asyncio.run(
        _run_packet(
            labels=labels,
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
    "LABEL_DATABASE_NAME_V2",
    "LABEL_RECORD_SCHEMA",
    "LABEL_RECORD_SCHEMAS",
    "LABEL_RECORD_SCHEMA_V1",
    "LABEL_RECORD_SCHEMA_V2",
    "LABEL_SCHEMA",
    "LABEL_SCHEMA_V1",
    "MEMORY_SQL",
    "MEMORY_SQL_V2",
    "MEMORY_SQL_V3",
    "PROMPT_TEMPLATE_V2",
    "PROMPT_V1",
    "PROMPT_V2",
    "PROMPT_V3",
    "PROMPT_TEMPLATE_V3",
    "Label",
    "LabelInput",
    "LabelInputV2",
    "LabelInputV3",
    "LabelV3",
    "MemoryV2",
    "MemoryV3",
    "artifact_hashes",
    "assert_label_database_v2",
    "build_label_input_v2",
    "build_label_input_v3",
    "build_request",
    "build_request_v2",
    "build_request_v3",
    "build_spot_check_packet",
    "build_spot_check_packet_v2",
    "ceiling_from_route",
    "label_main",
    "label_questions",
    "load_answer_session_memories",
    "load_answer_session_memories_v2",
    "load_answer_session_memories_v3",
    "main",
    "packet_main",
    "parse_label",
    "parse_label_v3",
    "price_ceiling_reader",
    "request_template",
    "request_template_v2",
    "request_template_v3",
    "session_dates_from_row",
    "spot_check_sample",
    "spot_check_selection",
    "seeded_spot_check_changes",
    "validate_target_groups",
    "validate_question_rows_v2",
    "verify_label_route",
]
