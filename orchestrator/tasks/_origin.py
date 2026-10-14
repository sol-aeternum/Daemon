"""S0: UNCALLED pure classification of already-authenticated accepted task input.

Nothing here authenticates/decrypts input, observes account/target state, grants
content/control authority, or chooses a task disposition. Upstream operational
uncertainty must stay indeterminate, never be passed as invented plaintext.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeAlias
from uuid import UUID

_INPUT_VERSION = 1
_MAX_GENERATION = 2**63 - 1
_OVERSIZED_INTEGER = object()
OriginRefusalCode: TypeAlias = Literal[
    "task_origin_missing", "task_origin_invalid", "task_origin_foreign"
]


@dataclass(frozen=True, slots=True)
class TaskOriginContext:
    """Explicit trusted comparison input, NOT proof of current/post-wait ownership.

    The caller must independently establish the persisted task owner. There is
    intentionally no replacement origin or current generation parameter.
    """

    task_owner_id: UUID

    def __post_init__(self) -> None:
        if type(self.task_owner_id) is not UUID:
            raise TypeError("task owner comparison requires a UUID")


@dataclass(frozen=True, slots=True)
class OriginalTaskOrigin:
    """Parsed original DATA, not provenance, authentication or a guard scope.

    This does not certify account existence/currency, producer stop, accounting
    settlement, deletion readiness or permission to read/write any target.
    """

    account_id: UUID
    generation: int

    def __post_init__(self) -> None:
        if (
            type(self.account_id) is not UUID
            or type(self.generation) is not int
            or not 0 <= self.generation <= _MAX_GENERATION
        ):
            raise ValueError("invalid original task origin data")


@dataclass(frozen=True, slots=True)
class TaskOriginRefusal:
    """Semantic classification only; no TaskStatus or cleanup/retry decision.

    Missing legacy data remains blocked pending separate disposition, not
    automatically repaired or terminalized. Permission restoration cannot turn
    this result into refreshed origin data or authority to restart stopped work.
    """

    code: OriginRefusalCode

    def __post_init__(self) -> None:
        if self.code not in (
            "task_origin_missing",
            "task_origin_invalid",
            "task_origin_foreign",
        ):
            raise ValueError("unsupported origin refusal code")


@dataclass(frozen=True, slots=True)
class TaskOriginIndeterminate:
    """Passive upstream contract: no authenticated origin observation established.

    Unreadable/unauthenticated ciphertext, unavailable key, DB/schema errors,
    cancellation and uncertain commit are NOT bad-origin evidence. This decoder
    cannot observe/map those failures and never returns this result itself.
    """


TaskOriginClassification: TypeAlias = OriginalTaskOrigin | TaskOriginRefusal
TaskOriginObservation: TypeAlias = TaskOriginClassification | TaskOriginIndeterminate


class _InvalidJSON(ValueError):
    pass


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    # Duplicate members are ambiguous; never select a replacement by key order.
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJSON("duplicate JSON member")
        result[key] = value
    return result


def _non_json_constant(_value: str) -> object:
    raise _InvalidJSON("non-JSON numeric constant")


def _integer(value: str) -> int | object:
    # No allowed generation needs >19 decimal digits. Preserve larger JSON
    # integers as opaque invalid-generation data without Python's digit-limit
    # exception or expensive unbounded int conversion. Unrelated fields stay
    # irrelevant to origin classification; this never manufactures an origin.
    if len(value.removeprefix("-")) > 19:
        return _OVERSIZED_INTEGER
    return int(value)


def _classify_task_origin(
    authenticated_json: str, *, input_version: object, context: TaskOriginContext
) -> TaskOriginClassification:
    """Decode raw authenticated plaintext; do NOT use TaskStore's lossy _open.

    Preconditions are upstream authentication/decryption and an independently
    established task-owner comparison. Wrong Python argument types raise, not
    semantic refusal; do not pass ciphertext, None, decoded objects or exceptions.
    Operational/parser resource exceptions propagate, not bad-origin evidence.

    Order: malformed JSON/nonobject; absent general fields; unsupported/ill-typed
    version; invalid UUID/generation; foreign owner; original data. Null fields
    are present but invalid. Summary-only fields never supply a fallback. No
    account/target checks, attempt-generation substitution or terminal disposition.
    """
    if type(authenticated_json) is not str or type(context) is not TaskOriginContext:
        raise TypeError("authenticated plaintext and task owner context required")
    try:
        value = json.loads(
            authenticated_json,
            object_pairs_hook=_object,
            parse_constant=_non_json_constant,
            parse_int=_integer,
        )
    except (json.JSONDecodeError, _InvalidJSON):
        return TaskOriginRefusal("task_origin_invalid")
    if not isinstance(value, dict):
        return TaskOriginRefusal("task_origin_invalid")
    if "origin_account_id" not in value or "origin_account_generation" not in value:
        return TaskOriginRefusal("task_origin_missing")
    if type(input_version) is not int or input_version != _INPUT_VERSION:
        return TaskOriginRefusal("task_origin_invalid")
    account_id, generation = value["origin_account_id"], value["origin_account_generation"]
    if (
        type(account_id) is not str
        or type(generation) is not int
        or not 0 <= generation <= _MAX_GENERATION
    ):
        return TaskOriginRefusal("task_origin_invalid")
    try:
        original_id = UUID(account_id)
    except ValueError:
        return TaskOriginRefusal("task_origin_invalid")
    if original_id != context.task_owner_id:
        return TaskOriginRefusal("task_origin_foreign")
    return OriginalTaskOrigin(original_id, generation)
