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

import hashlib
import inspect
import json
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

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


__all__ = [
    "LABELLER_MAX_TOKENS",
    "LABELLER_MODEL",
    "LABELLER_REASONING_EFFORT",
    "LABELLER_ROUTE_ID",
    "LABELLER_SEED",
    "LABEL_SCHEMA",
    "MEMORY_SQL",
    "PROMPT_V1",
    "Label",
    "LabelInput",
    "artifact_hashes",
    "build_request",
    "load_answer_session_memories",
    "parse_label",
    "request_template",
    "spot_check_sample",
]
