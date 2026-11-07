"""M2 exposure gate driver for the dev2 pre-registration (#601).

Offline-tested here; the primary executes the live run later. Nothing in this
module contacts a provider, opens a database or reads benchmark data at import
time or in its tests.

The pre-registration (``tests/benchmark_results/m2_preregistration.md``)
governs. This module deliberately never re-implements the exposure metric: the
``measure`` command goes through ``measure_multi_query_exposure`` with the
production renderer, and the arms are the pre-registered baseline, additive
(quota 3) and evaluation-only round-robin.

Benchmark text never appears in a committed artefact. Documents written here
carry question ids, memory ids, counts and timings; the ``report`` command is
further reduced to question ids, categories and counts.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import inspect
import json
import os
import sys
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from orchestrator.eval.exposure import PinnedSubqueryProvider, measure_multi_query_exposure
from orchestrator.memory import injection
from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.multi_query import (
    DECOMPOSITION_ROUTE_ID,
    MAX_INPUT_CHARS,
    PROMPT_V2,
    SUBQUERY_SCHEMA,
    validate_subqueries,
)
from tests.benchmark_longmemeval.dev2_labelling import (
    _append_journal,
    _as_spend,
    _journal_path,
    _run_lock_acquired,
    _run_lock_held,
    _write_document_atomic,
)
from tests.benchmark_longmemeval.dev2_pilot import check_output_outside_repo, db_name_from_dsn

THRESHOLD_SCHEMA: Final[str] = "m2-threshold/1"
GENERATION_SCHEMA: Final[str] = "m2-subqueries/1"
MEASURE_SCHEMA: Final[str] = "m2-measure/1"
GATES_SCHEMA: Final[str] = "m2-gates/1"
RETENTION_SCHEMA: Final[str] = "m2-retention/1"
REPORT_SCHEMA: Final[str] = "m2-report/1"
PLAUSIBILITY_SCHEMA: Final[str] = "m2-gate4-plausibility/1"
START_MARKER_SCHEMA: Final[str] = "m2-start-marker/1"
LABEL_SCHEMA: Final[str] = "dev2-labelling/2"
#: A loader-compatible earlier document. Gate 1 needs v2's session dates, so
#: ``run_threshold`` refuses a v1 document even though the other gates read it.
LABEL_SCHEMAS: Final[frozenset[str]] = frozenset({"dev2-labelling/1", LABEL_SCHEMA})
JOURNAL_VERSION: Final[int] = 1

FROZEN_QUESTION_COUNT: Final[int] = 100
ABSTENTION_COUNT: Final[int] = 6
#: The pre-registration freezes the spend. It is not a runtime knob.
SPEND_CAP_USD: Final[Decimal] = Decimal("0.50")
BENCHMARK_DB_NAME: Final[str] = "daemon_lme_bench_dev2_final_remap"
REMAP_LINEAGE_SCHEMA: Final[str] = "dev2-remap-lineage/1"
CORPUS_KIND: Final[str] = "historical_clock_remap"

GATE1_FLOOR: Final[Fraction] = Fraction(5)
GATE1_RATE: Final[Fraction] = Fraction(1, 10)
GATE4_FLAG_THRESHOLD: Final[int] = 2

GENERATION_ROUTE_ID: Final[str] = "luna-azure-eu"
GENERATION_REASONING_EFFORT: Final[str] = "none"
GENERATION_MAX_TOKENS: Final[int] = 256
MAX_SUBQUERIES: Final[int] = 3
SUBQUERY_QUOTA: Final[int] = 3
DISPATCH_TIMEOUT_S: Final[float] = 120.0
CONSECUTIVE_FAILURE_LIMIT: Final[int] = 10

#: Block D's closed route and the unapproved production decomposition route it
#: is measured against. Recorded in the output; neither value is a runtime knob.
ROUTE_MAPPING: Final[dict[str, Any]] = {
    "block_d": {
        "route_id": GENERATION_ROUTE_ID,
        "reasoning_effort": GENERATION_REASONING_EFFORT,
        "closed": True,
    },
    "production_decomposition": {
        "route_id": DECOMPOSITION_ROUTE_ID,
        "approved": False,
        "defined_in": "orchestrator/memory/multi_query.py:19",
    },
}

#: The pre-registration's retention diagnosis. Only these four categories count.
RETENTION_CATEGORIES: Final[tuple[str, ...]] = (
    "assistant_turn_content",
    "atomic_decomposition",
    "dedup_or_supersession",
    "other",
)
#: Closed reasons; anything else stays uncommitted and is never copied out.
RETENTION_REASONS: Final[tuple[str, ...]] = (
    "assistant_only_detail",
    "detail_lost_in_decomposition",
    "extracted_then_absent",
    "input_snippet_truncated",
    "failed_chunk",
    "detail_not_in_logged_input",
    "other",
)

M2_USER_NAMESPACE: Final[uuid.UUID] = uuid.UUID("b3a2f6d8-4c71-5a09-9f4e-7d2c1e8b6a35")
M2_USER_NAME: Final[str] = "m2-exposure:subquery"

GATE3_TEST: Final[str] = (
    "tests/test_multi_query_render_subset.py::"
    "test_baseline_rows_survive_the_additive_merge_at_the_same_budget"
)

FROZEN_IDS_PATH: Final[Path] = Path(__file__).with_name("fixtures") / "dev2_ids.json"

FINGERPRINT_LIMIT_NOTE: Final[str] = (
    "FINGERPRINT_TABLES excludes retrieval_log; its row count is checked separately."
)
RETENTION_LIMIT_NOTE: Final[dict[str, str]] = {
    "input_snippet": "first 1000 characters of each chunk; detail past that is not visible",
    "dedup_results": "per-chunk counts, not per-fact outcomes",
    "failed_chunks": "a failed chunk writes no log row",
}


class M2SideEffectError(RuntimeError):
    """The database changed in a way the M2 run forbids."""


@dataclass(frozen=True, slots=True)
class FrozenIds:
    ids: tuple[str, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class LabelEntry:
    question_id: str
    memory_ids: tuple[str, ...]
    answerable_from_store: bool


@dataclass(frozen=True, slots=True)
class LabelSet:
    entries: dict[str, LabelEntry]
    files: tuple[dict[str, str], ...]
    sha256: str
    #: Every record schema the merged documents declared. Empty for a set built
    #: by hand in a test; ``run_threshold`` refuses anything but v2.
    schemas: tuple[str, ...] = ()

    @property
    def answerable_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(qid for qid, entry in self.entries.items() if entry.answerable_from_store)
        )

    @property
    def exposure_answerable_ids(self) -> tuple[str, ...]:
        """The exposure denominator: gate 4 owns the `_abs` questions instead."""
        return tuple(qid for qid in self.answerable_ids if not qid.endswith("_abs"))

    @property
    def exposure_excluded_abs_ids(self) -> tuple[str, ...]:
        """Answerable `_abs` ids kept out of the exposure denominator (gate 4 covers them)."""
        return tuple(qid for qid in self.answerable_ids if qid.endswith("_abs"))

    @property
    def not_answerable_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(qid for qid, entry in self.entries.items() if not entry.answerable_from_store)
        )


@dataclass(frozen=True, slots=True)
class Question:
    question_id: str
    message: str
    reference_time: datetime
    abstention: bool
    answer_session_ids: tuple[str, ...]


Embed = Callable[[Sequence[str], bool], Awaitable[list[EmbeddingVectorResult]]]
MeasureQuestion = Callable[
    [Question, tuple[str, ...], Mapping[str, EmbeddingVectorResult], str, Sequence[str]],
    Awaitable[dict[str, Any]],
]
Fingerprint = Callable[[], Awaitable[dict[str, Any]]]
LogCount = Callable[[], Awaitable[int]]
SpendReader = Callable[[], Awaitable[Any]]
CeilingReader = Callable[[Mapping[str, Any]], Any]
EmbeddingCeiling = Callable[[Sequence[str]], Any]
#: The embedding route a run charges and stores vectors for: (route id, dimensions).
EmbeddingRoute = Callable[[], tuple[str, int]]
RetentionReader = Callable[[uuid.UUID, str, Sequence[str]], Awaitable[list[dict[str, Any]]]]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical(value))


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _prepare_output(output_path: Path | str, repo_root: Path | None) -> Path:
    """An output inside the checkout would dirty the tree; an existing one refuses."""
    output = Path(output_path)
    root = _repository_root() if repo_root is None else repo_root
    check_output_outside_repo(output, root)
    if output.exists():
        raise FileExistsError(f"M2 output already exists: {output}")
    return output


@dataclass(frozen=True, slots=True)
class RemapLineage:
    path: str
    sha256: str
    database: str
    remapped_fingerprint_sha256: str


def load_remap_lineage(path: Path | str) -> RemapLineage:
    """The remap's lineage file, verified to describe exactly this M2 database."""
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("The remap lineage is not JSON") from None
    if not isinstance(data, Mapping):
        raise ValueError("The remap lineage must be an object")
    if data.get("schema") != REMAP_LINEAGE_SCHEMA:
        raise ValueError(f"The remap lineage schema is not {REMAP_LINEAGE_SCHEMA}")
    if data.get("result") != "ok":
        raise ValueError("The remap lineage did not verify")
    remapped = data.get("remapped")
    if not isinstance(remapped, Mapping):
        raise ValueError("The remap lineage has no remapped state")
    if remapped.get("database") != BENCHMARK_DB_NAME:
        raise ValueError(f"The remap lineage names a different database than {BENCHMARK_DB_NAME}")
    fingerprint = remapped.get("fingerprint_sha256")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("The remap lineage has no remapped fingerprint")
    return RemapLineage(str(path), _sha256_bytes(raw), BENCHMARK_DB_NAME, fingerprint)


def check_remap_lineage(lineage: RemapLineage, fingerprint: Mapping[str, Any]) -> None:
    """Refuse unless the database still holds the state the lineage recorded."""
    if fingerprint.get("sha256") != lineage.remapped_fingerprint_sha256:
        raise ValueError("The database fingerprint no longer matches the remap lineage")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return format(Decimal(value.numerator) / Decimal(value.denominator), "f")


def gate1_threshold(answerable_count: int) -> dict[str, Any]:
    """The frozen formula, computed exactly (never rounded) as a Fraction."""
    if type(answerable_count) is not int or answerable_count < 0:
        raise ValueError("answerable_count must be a non-negative integer")
    value = max(GATE1_FLOOR, Fraction(answerable_count, 1) * GATE1_RATE)
    return {
        "formula": "max(5, answerable_count/10)",
        "answerable_count": answerable_count,
        "exact": _fraction_text(value),
        "numerator": value.numerator,
        "denominator": value.denominator,
    }


def load_frozen_ids(path: Path | str = FROZEN_IDS_PATH) -> FrozenIds:
    """The 100 frozen dev2 ids, verified against the fixture's own hash."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("Frozen ids fixture must be an object")
    raw = data.get("question_ids")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Frozen ids fixture has no question_ids")
    ids: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value:
            raise ValueError("Frozen question ids must be nonempty strings")
        ids.append(value)
    if len(ids) != FROZEN_QUESTION_COUNT:
        raise ValueError(f"Frozen id set must hold exactly {FROZEN_QUESTION_COUNT} questions")
    if len(set(ids)) != len(ids):
        raise ValueError("Frozen id set holds duplicates")
    digest = _sha256_text("".join(f"{value}\n" for value in sorted(ids)))
    if digest != data.get("question_ids_sha256"):
        raise ValueError("Frozen id set does not match its pinned SHA-256")
    return FrozenIds(tuple(ids), digest)


def _label_entry(value: Any, frozen: set[str]) -> LabelEntry:
    if not isinstance(value, Mapping):
        raise ValueError("Label question entry must be an object")
    question_id = value.get("question_id")
    if not isinstance(question_id, str) or question_id not in frozen:
        raise ValueError("Label entry is outside the frozen id set")
    if value.get("schema_valid") is not True:
        raise ValueError(f"Label entry is not schema-valid: {question_id}")
    answerable = value.get("answerable_from_store")
    if type(answerable) is not bool:
        raise ValueError(f"Label answerable_from_store is not a boolean: {question_id}")
    raw_ids = value.get("memory_ids")
    if not isinstance(raw_ids, list):
        raise ValueError(f"Label memory_ids must be a list: {question_id}")
    memory_ids: list[str] = []
    for memory_id in raw_ids:
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError(f"Label memory ids must be nonempty strings: {question_id}")
        memory_ids.append(memory_id)
    if len(set(memory_ids)) != len(memory_ids):
        raise ValueError(f"Label memory ids are duplicated: {question_id}")
    if (not memory_ids) != (not answerable):
        raise ValueError(f"Label ids disagree with answerable_from_store: {question_id}")
    return LabelEntry(question_id, tuple(memory_ids), answerable)


def load_labels(paths: Sequence[Path | str], frozen: FrozenIds) -> LabelSet:
    """Merge the final label documents; refuse incomplete, duplicate or invalid sets."""
    if not paths:
        raise ValueError("At least one label document is required")
    frozen_set = set(frozen.ids)
    entries: dict[str, LabelEntry] = {}
    files: list[dict[str, str]] = []
    schemas: list[str] = []
    for path_value in paths:
        path = Path(path_value)
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("Label document must be an object")
        schema = data.get("schema")
        if schema not in LABEL_SCHEMAS:
            raise ValueError(f"Label document schema is not one of {sorted(LABEL_SCHEMAS)}")
        schemas.append(str(schema))
        if data.get("stop_reason") is not None:
            raise ValueError("Label document stopped before completion")
        if data.get("total_cost_complete") is not True:
            raise ValueError("Label document has an incomplete cost total")
        questions = data.get("questions")
        if not isinstance(questions, list) or not questions:
            raise ValueError("Label document has no question entries")
        files.append({"path": str(path), "sha256": _sha256_bytes(raw)})
        for value in questions:
            entry = _label_entry(value, frozen_set)
            if entry.question_id in entries:
                raise ValueError(f"Duplicate label for question: {entry.question_id}")
            entries[entry.question_id] = entry
    missing = sorted(frozen_set - set(entries))
    if missing:
        raise ValueError(f"Label set is incomplete: {len(missing)} frozen ids have no label")
    merged = {
        qid: {
            "memory_ids": sorted(entry.memory_ids),
            "answerable_from_store": entry.answerable_from_store,
        }
        for qid, entry in sorted(entries.items())
    }
    return LabelSet(entries, tuple(files), _sha256_json(merged), tuple(schemas))


def run_threshold(
    *,
    labels: LabelSet,
    frozen: FrozenIds,
    output_path: Path | str,
    repo_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Record gate 1's exact value from the final labels, before any retrieval."""
    if LABEL_SCHEMA not in labels.schemas or any(
        schema != LABEL_SCHEMA for schema in labels.schemas
    ):
        # The amendment makes gate 1's value come from v2's dated labels, so a v1
        # document (which has no session dates) cannot set the threshold.
        raise ValueError(
            f"Gate 1 requires {LABEL_SCHEMA} labels; a v1 document cannot set the threshold"
        )
    output = _prepare_output(output_path, repo_root)
    exposure_answerable = labels.exposure_answerable_ids
    exposure_excluded = labels.exposure_excluded_abs_ids
    answerable = len(exposure_answerable)
    document = {
        "schema": THRESHOLD_SCHEMA,
        "created_at": (clock() if clock is not None else datetime.now(UTC)).isoformat(),
        "frozen_ids_sha256": frozen.sha256,
        "frozen_question_count": len(frozen.ids),
        "label_files": list(labels.files),
        "labels_sha256": labels.sha256,
        "question_count": len(labels.entries),
        "answerable_count": answerable,
        "not_answerable_count": len(labels.not_answerable_ids),
        "answerable_question_ids": list(exposure_answerable),
        "exposure_excluded_abs_count": len(exposure_excluded),
        "exposure_excluded_abs_ids": list(exposure_excluded),
        "retrieval_performed": False,
        "gate1_threshold": gate1_threshold(answerable),
    }
    _write_document_atomic(output, document)
    return document


def _parse_reference_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("question_date missing")
    date_part, _, rest = value.partition(" (")
    time_part = rest.partition(") ")[2]
    try:
        parsed = datetime.strptime(f"{date_part} {time_part}".strip(), "%Y/%m/%d %H:%M")
    except ValueError:
        raise ValueError("question_date unparsable") from None
    return parsed.replace(tzinfo=UTC)


def load_questions(path: Path | str, frozen: FrozenIds) -> list[Question]:
    """The 100 questions with their dates; text is never written to a document."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Question fixture must be a JSON list")
    frozen_set = set(frozen.ids)
    questions: list[Question] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Question rows must be objects")
        question_id = row.get("question_id")
        message = row.get("question")
        if not isinstance(question_id, str) or question_id not in frozen_set:
            raise ValueError("Question is outside the frozen id set")
        if question_id in seen:
            raise ValueError("Duplicate question id")
        if not isinstance(message, str) or not message:
            raise ValueError("Question text missing")
        seen.add(question_id)
        abstention = question_id.endswith("_abs")
        raw_sessions = row.get("answer_session_ids")
        sessions: tuple[str, ...] = ()
        if raw_sessions is not None:
            if not isinstance(raw_sessions, list) or any(
                not isinstance(value, str) or not value for value in raw_sessions
            ):
                raise ValueError("answer_session_ids must be nonempty strings")
            sessions = tuple(raw_sessions)
        questions.append(
            Question(
                question_id=question_id,
                message=message,
                reference_time=_parse_reference_time(row.get("question_date")),
                abstention=abstention,
                answer_session_ids=sessions,
            )
        )
    if set(seen) != frozen_set:
        raise ValueError("Question fixture does not cover the frozen id set exactly")
    if sum(question.abstention for question in questions) != ABSTENTION_COUNT:
        raise ValueError(f"Expected exactly {ABSTENTION_COUNT} abstention questions")
    return questions


def _normalized_message(question: Question) -> str:
    """Production decomposes ``injection._normalize_content(...)``, not raw text.

    ``_derive_query_text`` normalizes the user message before it reaches
    ``ProductionSubqueryProvider.get_subqueries``, so the request body must carry
    the same collapsed text and validation must run against it too.
    """
    return injection._normalize_content(question.message)


def generation_request(question: Question) -> dict[str, Any]:
    """The exact body ``ProductionSubqueryProvider`` sends, before routing."""
    return {
        "messages": [
            {"role": "system", "content": PROMPT_V2},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message": _normalized_message(question)[:MAX_INPUT_CHARS],
                        "as_of": question.reference_time.date().isoformat(),
                    }
                ),
            },
        ],
        "max_tokens": GENERATION_MAX_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "memory_components_v2",
                "strict": True,
                "schema": SUBQUERY_SCHEMA,
            },
        },
    }


def generation_payload(route: Any, question: Question) -> dict[str, Any]:
    return {
        "_route_id": GENERATION_ROUTE_ID,
        "model": route.model,
        "_exact_model": True,
        "reasoning_effort": GENERATION_REASONING_EFFORT,
        "stream": False,
        **generation_request(question),
    }


def _first_choice(receipt: Mapping[str, Any]) -> tuple[str | None, str | None]:
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


def _evaluate_subqueries(content: str | None, message: str) -> tuple[bool, list[str]]:
    if content is None:
        return False, []
    try:
        data = json.loads(content)
        if not isinstance(data, dict) or set(data) != {"queries"}:
            return False, []
        accepted = validate_subqueries(data["queries"], message, max_subqueries=MAX_SUBQUERIES)
    except (TypeError, ValueError):
        return False, []
    return True, accepted


def _failure_category(error: BaseException) -> str | None:
    from orchestrator.compute_runtime import ComputeUnavailable

    if isinstance(error, ComputeUnavailable):
        category = getattr(error, "category", None)
        return category if isinstance(category, str) and category else "compute_unavailable"
    if isinstance(error, TimeoutError):
        return "deadline_exceeded"
    return None


def _receipt_of(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    from scripts.memory_query_decomposition import receipt_dict

    return receipt_dict(value)


def _cost_of(receipt: Mapping[str, Any]) -> Decimal:
    if "cost_usd" in receipt:
        return _as_spend(receipt["cost_usd"])
    from scripts.memory_query_decomposition import reported_cost

    return reported_cost(receipt)


def _question_digest(question: Question) -> str:
    return _sha256_json(
        {
            "question_id": question.question_id,
            "question": question.message,
            "question_date": question.reference_time.isoformat(),
        }
    )


# --- one spend start per M2 run ----------------------------------------------


@dataclass(frozen=True, slots=True)
class StartMarker:
    start_spend_usd: Decimal
    spend_cap_usd: Decimal
    database: str


def _start_marker_payload(start: Decimal) -> dict[str, Any]:
    return {
        "schema": START_MARKER_SCHEMA,
        "start_spend_usd": str(start),
        "spend_cap_usd": str(SPEND_CAP_USD),
        "database": BENCHMARK_DB_NAME,
    }


def _start_marker_path(path: Path | str, repo_root: Path | None) -> Path:
    marker = Path(path)
    root = _repository_root() if repo_root is None else repo_root
    check_output_outside_repo(marker, root)
    return marker


def _read_start_marker(path: Path) -> StartMarker:
    """The one start and cap this M2 run was anchored to; anything else refuses."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"M2 start marker is missing: {path}") from None
    except OSError:
        raise ValueError(f"M2 start marker is unreadable: {path}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("M2 start marker is corrupt") from None
    if not isinstance(data, Mapping) or set(data) != {
        "schema",
        "start_spend_usd",
        "spend_cap_usd",
        "database",
    }:
        raise ValueError("M2 start marker is corrupt")
    if data.get("schema") != START_MARKER_SCHEMA:
        raise ValueError("M2 start marker is corrupt")
    if data.get("database") != BENCHMARK_DB_NAME:
        raise ValueError("M2 start marker names a different benchmark database")
    try:
        start = _as_spend(data.get("start_spend_usd"))
        cap = _as_spend(data.get("spend_cap_usd"))
    except ValueError:
        raise ValueError("M2 start marker is corrupt") from None
    if cap != SPEND_CAP_USD:
        raise ValueError("M2 start marker spend cap is not the frozen cap")
    return StartMarker(start_spend_usd=start, spend_cap_usd=cap, database=BENCHMARK_DB_NAME)


def _optional_start_marker(path: Path) -> StartMarker | None:
    if not path.exists():
        return None
    return _read_start_marker(path)


def _create_start_marker(path: Path, start: Decimal) -> StartMarker:
    """The first start wins: exclusive create, fsync; a later process reads it."""
    payload = _canonical(_start_marker_payload(start))
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return _read_start_marker(path)
    except OSError:
        raise ValueError(f"M2 start marker cannot be created: {path}") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return StartMarker(
        start_spend_usd=start, spend_cap_usd=SPEND_CAP_USD, database=BENCHMARK_DB_NAME
    )


def _generation_static_manifest(
    questions: Sequence[Question], start: Decimal, remap_lineage_sha256: str
) -> dict[str, Any]:
    return {
        "kind": "manifest",
        "version": JOURNAL_VERSION,
        "remap_lineage_sha256": remap_lineage_sha256,
        "inputs_sha256": _sha256_json([_question_digest(question) for question in questions]),
        "prompt_sha256": _sha256_text(PROMPT_V2),
        "schema_sha256": _sha256_json(SUBQUERY_SCHEMA),
        "route_sha256": _sha256_json(
            [GENERATION_ROUTE_ID, GENERATION_REASONING_EFFORT, GENERATION_MAX_TOKENS]
        ),
        "start_spend_usd": str(start),
        "spend_cap_usd": str(SPEND_CAP_USD),
    }


def _read_generation_journal(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any] | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, set(), None
    results: dict[str, dict[str, Any]] = {}
    intents: set[str] = set()
    manifest: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("M2 generation journal is corrupt") from None
        if not isinstance(record, dict):
            raise ValueError("M2 generation journal is corrupt")
        kind = record.get("kind")
        if kind == "manifest":
            if manifest is not None or results or intents:
                raise ValueError("M2 generation journal is corrupt")
            manifest = record
            continue
        key = record.get("question_id")
        if not isinstance(key, str) or not key:
            raise ValueError("M2 generation journal is corrupt")
        if kind == "result":
            results[key] = {name: value for name, value in record.items() if name != "kind"}
        elif kind == "intent":
            intents.add(key)
        else:
            raise ValueError("M2 generation journal is corrupt")
    return results, intents, manifest


def _stopped_generation(stop_reason: str, start: Decimal) -> dict[str, Any]:
    return {
        "schema": GENERATION_SCHEMA,
        "route_id": GENERATION_ROUTE_ID,
        "reasoning_effort": GENERATION_REASONING_EFFORT,
        "route_mapping": ROUTE_MAPPING,
        "start_spend_usd": str(start),
        "spend_cap_usd": str(SPEND_CAP_USD),
        "stop_reason": stop_reason,
        "calls": 0,
        "total_cost_usd": "0",
        "questions": [],
    }


def _generation_entry(question: Question) -> dict[str, Any]:
    return {
        "question_id": question.question_id,
        "subqueries": [],
        "triggered": False,
        "schema_valid": False,
        "latency_seconds": None,
        "cost_usd": None,
        "observed_spend_usd": None,
        "error_category": None,
    }


def pinned_subqueries_sha256(
    entries: Sequence[Mapping[str, Any]],
    *,
    start_spend_usd: Any,
    spend_cap_usd: Any,
) -> str:
    """The stable hash of the pinned sub-query map and its spend binding.

    ``start_spend_usd`` and ``spend_cap_usd`` are part of the hash on purpose:
    the pinned file alone decides the ceiling, so editing either must break it.
    """
    payload = {
        "start_spend_usd": str(_as_spend(start_spend_usd)),
        "spend_cap_usd": str(_as_spend(spend_cap_usd)),
        "questions": {
            str(entry.get("question_id")): {
                "subqueries": list(entry.get("subqueries") or []),
                "triggered": bool(entry.get("triggered")),
            }
            for entry in entries
        },
    }
    return _sha256_json(payload)


async def run_generation(
    *,
    questions: Sequence[Question],
    output_path: Path | str,
    start_marker: Path | str,
    spend_cap_usd: Decimal,
    remap_lineage_sha256: str,
    call: Callable[[Mapping[str, Any]], Any],
    check_attestation: Callable[[], Any],
    verify_route: Callable[[], Any],
    account_scope: Callable[[str], Any],
    acquire_run_lock: Callable[[], Any],
    check_run_lock: Callable[[], Any],
    read_ledger_spend_usd: SpendReader,
    next_call_ceiling_usd: CeilingReader,
    repo_root: Path | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """One ``PROMPT_V2`` pass, resumable, with the frozen spend ceiling.

    ``start_marker`` is the run's one spend start: the first ``generate`` creates
    it, and every later ``generate`` — whatever its output path — reads it
    instead of re-anchoring the ceiling to the ledger.
    """
    if not questions:
        raise ValueError("Generation requires questions")
    if len({question.question_id for question in questions}) != len(questions):
        raise ValueError("Generation questions must be unique")
    if _as_spend(spend_cap_usd) != SPEND_CAP_USD:
        raise ValueError(f"M2 generation spend cap is frozen at {SPEND_CAP_USD}")
    if not _is_sha256(remap_lineage_sha256):
        raise ValueError("M2 generation requires the remap lineage hash")
    if not await _run_lock_acquired(acquire_run_lock):
        return _stopped_generation("run_lock_busy", Decimal(0))

    output = Path(output_path)
    journal = _journal_path(output)
    if output.exists() and not journal.exists():
        # A finished generation document with no journal can only be a deleted
        # journal; the pinned file would otherwise be re-derived from the ledger.
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, Mapping) and existing.get("schema") == GENERATION_SCHEMA:
            raise ValueError("M2 generation document exists without its journal")
    output = _prepare_output(output_path, repo_root)
    marker_path = _start_marker_path(start_marker, repo_root)
    try:
        handle = open(journal, "x", encoding="utf-8")
    except FileExistsError:
        handle = open(journal, "a", encoding="utf-8")

    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(f"M2 generation journal is held: {journal}") from None

        results, intents, manifest = _read_generation_journal(journal)
        marker = _optional_start_marker(marker_path)
        stop_reason: str | None = None
        if manifest is None:
            if results or intents:
                stop_reason = "receipt_mismatch"
                start_spend = marker.start_spend_usd if marker is not None else Decimal(0)
            elif marker is not None:
                # A later output path must not re-anchor the ceiling to the ledger.
                start_spend = marker.start_spend_usd
                manifest = _generation_static_manifest(questions, start_spend, remap_lineage_sha256)
                _append_journal(handle, manifest)
            else:
                try:
                    start_spend = _as_spend(await _maybe_await(read_ledger_spend_usd()))
                except Exception:
                    return _stopped_generation("spend_unreadable", Decimal(0))
                marker = _create_start_marker(marker_path, start_spend)
                start_spend = marker.start_spend_usd
                manifest = _generation_static_manifest(questions, start_spend, remap_lineage_sha256)
                _append_journal(handle, manifest)
        else:
            try:
                start_spend = _as_spend(manifest.get("start_spend_usd"))
            except ValueError:
                stop_reason = "receipt_mismatch"
                start_spend = Decimal(0)
            if stop_reason is None:
                if marker is None:
                    raise ValueError(
                        "M2 generation journal has no start marker; the spend start cannot "
                        "be re-anchored from the ledger"
                    )
                if marker.start_spend_usd != start_spend:
                    raise ValueError("M2 start marker disagrees with the generation journal")
            if stop_reason is None and manifest != _generation_static_manifest(
                questions, start_spend, remap_lineage_sha256
            ):
                stop_reason = "receipt_mismatch"
        ceiling = start_spend + spend_cap_usd

        route: Any = None
        if stop_reason is None:
            pending = check_attestation()
            if inspect.isawaitable(pending):
                await pending
            route = verify_route()
            if inspect.isawaitable(route):
                route = await route
            if route is None:
                raise ValueError("Generation route was not verified")

        high_water = start_spend
        for recorded in results.values():
            high_water = max(high_water, _as_spend(recorded.get("observed_spend_usd") or "0"))
        consecutive_failures = 0
        calls = 0

        for question in questions:
            if stop_reason is not None:
                break
            key = question.question_id
            if key in results:
                recorded = results[key]
                if recorded.get("error_category") is None:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                        stop_reason = "consecutive_failures"
                continue
            if key in intents:
                stop_reason = "uncertain_dispatch"
                break

            payload = generation_payload(route, question)
            try:
                ledger = _as_spend(await _maybe_await(read_ledger_spend_usd()))
            except Exception:
                stop_reason = "spend_unreadable"
                break
            spent = max(ledger, high_water)
            try:
                call_ceiling = _as_spend(await _maybe_await(next_call_ceiling_usd(dict(payload))))
            except Exception:
                stop_reason = "ceiling_unknown"
                break
            if spent + call_ceiling > ceiling:
                stop_reason = "spend_cap"
                break
            if not await _run_lock_held(check_run_lock):
                stop_reason = "run_lock_lost"
                break

            _append_journal(handle, {"kind": "intent", "question_id": key})
            intents.add(key)
            calls += 1
            started = clock()
            try:
                async with account_scope(str(uuid.uuid5(M2_USER_NAMESPACE, M2_USER_NAME))):
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
                entry = _generation_entry(question)
                entry["latency_seconds"] = max(clock() - started, 0.0)
                entry["error_category"] = category
                entry["observed_spend_usd"] = str(spent + call_ceiling)
                _append_journal(handle, {"kind": "result", **entry})
                results[key] = dict(entry)
                high_water = max(high_water, spent + call_ceiling)
                consecutive_failures += 1
                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    stop_reason = "consecutive_failures"
                    break
                continue

            elapsed = max(clock() - started, 0.0)
            consecutive_failures = 0
            entry = _generation_entry(question)
            entry["latency_seconds"] = elapsed
            try:
                receipt = _receipt_of(response)
                cost = _cost_of(receipt)
            except Exception:
                entry["error_category"] = "accounting_error"
                entry["observed_spend_usd"] = str(spent + call_ceiling)
                _append_journal(handle, {"kind": "result", **entry})
                results[key] = dict(entry)
                high_water = max(high_water, spent + call_ceiling)
                stop_reason = "accounting_error"
                break
            entry["cost_usd"] = str(cost)
            entry["observed_spend_usd"] = str(spent + cost)
            content, _finish = _first_choice(receipt)
            schema_valid, subqueries = _evaluate_subqueries(content, _normalized_message(question))
            entry["schema_valid"] = schema_valid
            entry["subqueries"] = subqueries
            entry["triggered"] = bool(subqueries)
            _append_journal(handle, {"kind": "result", **entry})
            results[key] = dict(entry)
            high_water = max(high_water, spent + cost)

        entries = [
            results[question.question_id]
            for question in questions
            if question.question_id in results
            and results[question.question_id].get("question_id") == question.question_id
        ]
        unknown = [entry["question_id"] for entry in entries if entry.get("cost_usd") is None]
        total = sum(
            (Decimal(str(entry["cost_usd"])) for entry in entries if entry.get("cost_usd")),
            Decimal(0),
        )
        document = {
            "schema": GENERATION_SCHEMA,
            "route_id": GENERATION_ROUTE_ID,
            "model": getattr(route, "model", None),
            "reasoning_effort": GENERATION_REASONING_EFFORT,
            "route_mapping": ROUTE_MAPPING,
            "inputs_sha256": manifest["inputs_sha256"] if manifest else None,
            "prompt_sha256": _sha256_text(PROMPT_V2),
            "start_spend_usd": str(start_spend),
            "spend_cap_usd": str(spend_cap_usd),
            "spend_ceiling_usd": str(ceiling),
            "stop_reason": stop_reason,
            "calls": calls,
            "side_effects": {
                "users_inserted": True,
                "entitlement_accounts_inserted": True,
                "note": (
                    "ensure_benchmark_user may insert one users row and its "
                    "entitlement_accounts row for the M2 account user; the spend "
                    "guard's ledger rows are the only other write"
                ),
            },
            "total_cost_usd": str(total),
            "total_cost_complete": not unknown
            and stop_reason not in {"uncertain_dispatch", "accounting_error"},
            "unknown_cost_questions": unknown,
            "subqueries_sha256": pinned_subqueries_sha256(
                entries, start_spend_usd=start_spend, spend_cap_usd=spend_cap_usd
            ),
            "questions": entries,
        }
        if stop_reason not in {"uncertain_dispatch", "receipt_mismatch"}:
            _write_document_atomic(output, document)
        return document
    finally:
        handle.close()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _embedding_texts(question: Question, subqueries: Sequence[str]) -> tuple[str, list[str]]:
    from orchestrator.memory import injection

    query = injection._normalize_content(question.message)
    components = validate_subqueries(list(subqueries), query, max_subqueries=MAX_SUBQUERIES)
    return query, components


def _embedding_batches(texts: Sequence[str], size: int = 3) -> list[list[str]]:
    return [list(texts[index : index + size]) for index in range(0, len(texts), size)]


def _arm_view(metrics: Mapping[str, Any], arm: str, target_ids: Sequence[str]) -> dict[str, Any]:
    values = metrics.get(arm)
    if not isinstance(values, Mapping):
        raise ValueError(f"Exposure result is missing the {arm} arm")
    targets = values.get("targets")
    if not isinstance(targets, Mapping):
        raise ValueError("Exposure result is missing per-target detail")
    rendered_targets = [
        target_id
        for target_id in target_ids
        if isinstance(targets.get(target_id), Mapping) and targets[target_id].get("rendered")
    ]
    rendered_ids = values.get("rendered_ids")
    return {
        "target_count": len(target_ids),
        "rendered_target_ids": sorted(rendered_targets),
        "target_exposed": bool(values.get("target_exposed")),
        "target_any_exposed": bool(values.get("target_any_exposed")),
        "rendered_count": len(rendered_ids) if isinstance(rendered_ids, list) else 0,
        "rendered_ids": sorted(str(value) for value in rendered_ids)
        if isinstance(rendered_ids, list)
        else [],
    }


def load_pinned_subqueries(
    path: Path | str, frozen: FrozenIds, *, journal_path: Path | str | None = None
) -> dict[str, dict[str, Any]]:
    """The pinned map, tied to its spend binding and to its generation journal.

    ``subqueries_sha256`` covers the start and the cap, and the journal manifest
    must agree with the document: deleting the journal, or editing either spend
    field, refuses here rather than at the first dispatch.
    """
    document_path = Path(path)
    data = json.loads(document_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("Pinned sub-query document must be an object")
    if data.get("schema") != GENERATION_SCHEMA:
        raise ValueError(f"Pinned sub-query schema is not {GENERATION_SCHEMA}")
    if data.get("stop_reason") is not None:
        raise ValueError("Pinned sub-query document stopped before completion")
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError("Pinned sub-query document has no questions")
    try:
        start = _as_spend(data.get("start_spend_usd"))
        cap = _as_spend(data.get("spend_cap_usd"))
    except ValueError:
        raise ValueError("Pinned sub-query spend records are unreadable") from None
    if cap != SPEND_CAP_USD:
        raise ValueError(f"Pinned sub-query spend cap is not the frozen {SPEND_CAP_USD}")
    if pinned_subqueries_sha256(questions, start_spend_usd=start, spend_cap_usd=cap) != data.get(
        "subqueries_sha256"
    ):
        raise ValueError("Pinned sub-queries do not match their recorded hash")
    entries: dict[str, dict[str, Any]] = {}
    for value in questions:
        if not isinstance(value, Mapping):
            raise ValueError("Pinned sub-query entry must be an object")
        question_id = value.get("question_id")
        if not isinstance(question_id, str) or question_id not in set(frozen.ids):
            raise ValueError("Pinned sub-query is outside the frozen id set")
        if question_id in entries:
            raise ValueError("Pinned sub-query is duplicated")
        raw = value.get("subqueries")
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ValueError("Pinned sub-query list is invalid")
        if len(raw) > MAX_SUBQUERIES:
            raise ValueError("Pinned sub-query exceeds the at-most-3 limit")
        if bool(value.get("triggered")) != bool(raw):
            raise ValueError("Pinned trigger flag disagrees with its sub-queries")
        if value.get("schema_valid") is not True:
            raise ValueError("Pinned generation has a schema-invalid question")
        if value.get("error_category") is not None:
            raise ValueError("Pinned generation has a failed question; resolve it before measuring")
        entries[question_id] = dict(value)
    if set(entries) != set(frozen.ids):
        raise ValueError("Pinned sub-queries do not cover the frozen id set exactly")

    journal = Path(journal_path) if journal_path is not None else _journal_path(document_path)
    if not journal.exists():
        raise ValueError("Pinned sub-query document has no generation journal")
    recorded, _intents, manifest = _read_generation_journal(journal)
    if manifest is None:
        raise ValueError("Pinned sub-query journal has no manifest")
    if manifest.get("start_spend_usd") != str(start) or manifest.get("spend_cap_usd") != str(cap):
        raise ValueError("Pinned sub-query journal disagrees with the document's spend binding")
    for question_id, entry in entries.items():
        stored = recorded.get(question_id)
        if stored is None:
            raise ValueError("Pinned sub-query journal is missing a question")
        if [str(item) for item in stored.get("subqueries") or []] != list(entry["subqueries"]):
            raise ValueError("Pinned sub-queries disagree with the generation journal")
        if bool(stored.get("triggered")) != bool(entry.get("triggered")):
            raise ValueError("Pinned sub-queries disagree with the generation journal")

    for entry in entries.values():
        entry["_start_spend_usd"] = str(start)
        entry["_spend_cap_usd"] = str(cap)
    return entries


def _spend_of_document(subqueries: Mapping[str, Mapping[str, Any]]) -> tuple[Decimal, Decimal]:
    first = next(iter(subqueries.values()))
    return _as_spend(first["_start_spend_usd"]), _as_spend(first["_spend_cap_usd"])


def retrieval_logging_flags() -> tuple[bool, bool]:
    """The two settings that make production's own guard log retrievals.

    ``_is_retrieval_logging_enabled(False)`` returns True when either setting is
    on, so ``measure`` refuses before any spend rather than after the row count
    check, which can only notice the rows once they exist.
    """
    from orchestrator.config import get_settings

    settings = get_settings()
    return bool(settings.retrieval_logging_enabled), bool(settings.retrieval_logging_debug)


def _text_key(text: str) -> str:
    return _sha256_text(text)


def _vector_record(text: str, vector: EmbeddingVectorResult, observed: Decimal) -> dict[str, Any]:
    """A durable embedding receipt keyed by text hash: no text is journalled."""
    return {
        "kind": "embedding",
        "text_sha256": _text_key(text),
        "embedding": [float(value) for value in vector.embedding],
        "provider": vector.provider,
        "model": vector.model,
        "storage_model": vector.storage_model,
        "observed_spend_usd": str(observed),
    }


def _vector_from_record(record: Mapping[str, Any]) -> EmbeddingVectorResult:
    embedding = record.get("embedding")
    if not isinstance(embedding, list) or any(
        not isinstance(value, int | float) or isinstance(value, bool) for value in embedding
    ):
        raise ValueError("M2 measure journal holds a corrupt embedding vector")
    provider = record.get("provider")
    model = record.get("model")
    storage_model = record.get("storage_model")
    if not all(isinstance(value, str) for value in (provider, model, storage_model)):
        raise ValueError("M2 measure journal holds a corrupt vector receipt")
    return EmbeddingVectorResult(
        embedding=[float(value) for value in embedding],
        provider=str(provider),
        model=str(model),
        storage_model=str(storage_model),
    )


def _read_measure_journal(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any] | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, {}, None
    results: dict[str, dict[str, Any]] = {}
    vectors: dict[str, dict[str, Any]] = {}
    manifest: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("M2 measure journal is corrupt") from None
        if not isinstance(record, dict):
            raise ValueError("M2 measure journal is corrupt")
        kind = record.get("kind")
        if kind == "manifest":
            if manifest is not None or results or vectors:
                raise ValueError("M2 measure journal is corrupt")
            manifest = record
            continue
        if kind == "embedding":
            key = record.get("text_sha256")
            if not isinstance(key, str) or not key or key in vectors:
                raise ValueError("M2 measure journal is corrupt")
            vectors[key] = dict(record)
            continue
        if kind == "result":
            key = record.get("question_id")
            if not isinstance(key, str) or not key or key in results:
                raise ValueError("M2 measure journal is corrupt")
            results[key] = {name: value for name, value in record.items() if name != "kind"}
            continue
        raise ValueError("M2 measure journal is corrupt")
    return results, vectors, manifest


def _measure_manifest(
    questions: Sequence[Question],
    labels: LabelSet,
    subqueries: Mapping[str, Mapping[str, Any]],
    start: Decimal,
    cap: Decimal,
    *,
    embedding_route_id: str,
    embedding_dimensions: int,
    remap_lineage_sha256: str,
) -> dict[str, Any]:
    return {
        "kind": "manifest",
        "version": JOURNAL_VERSION,
        "remap_lineage_sha256": remap_lineage_sha256,
        "inputs_sha256": _sha256_json([_question_digest(question) for question in questions]),
        "labels_sha256": labels.sha256,
        "subqueries_sha256": pinned_subqueries_sha256(
            list(subqueries.values()), start_spend_usd=start, spend_cap_usd=cap
        ),
        "start_spend_usd": str(start),
        "spend_cap_usd": str(cap),
        # The vector cache is keyed by text alone, so a resume needs this binding:
        # vectors from a different route must never be mixed into the same run.
        "embedding_route_id": embedding_route_id,
        "embedding_dimensions": embedding_dimensions,
    }


def _prepare_resumable_output(output: Path, repo_root: Path | None, journal: Path) -> None:
    """A fresh output, or a stopped document that still has its journal."""
    root = _repository_root() if repo_root is None else repo_root
    check_output_outside_repo(output, root)
    if not output.exists():
        return
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise FileExistsError(f"M2 output already exists: {output}") from None
    stopped = (
        isinstance(data, Mapping)
        and data.get("schema") == MEASURE_SCHEMA
        and data.get("stop_reason") is not None
    )
    if not stopped:
        raise FileExistsError(f"M2 output already exists: {output}")
    if not journal.exists():
        raise ValueError("A stopped M2 measure document has no journal to resume from")


def _measure_stop_reason(error: BaseException) -> str:
    category = _failure_category(error)
    if category is not None:
        return category
    if isinstance(error, M2SideEffectError):
        return "side_effect"
    message = str(error)
    for marker, reason in (
        ("spend cap", "spend_cap"),
        ("run lock", "run_lock_lost"),
        ("ceiling is unreadable", "ceiling_unknown"),
    ):
        if marker in message:
            return reason
    return "error"


def _measure_document(
    *,
    questions: Sequence[Question],
    labels: LabelSet,
    subqueries: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
    start: Decimal,
    cap: Decimal,
    ceiling: Decimal,
    high_water: Decimal,
    before_fingerprint: Mapping[str, Any],
    before_log: int | None,
    after_fingerprint: Mapping[str, Any],
    after_log: int | None,
    stop_reason: str | None,
    stopped_by: str | None,
    remap_lineage_sha256: str,
) -> dict[str, Any]:
    entries = [
        dict(results[question.question_id])
        for question in questions
        if question.question_id in results
    ]
    return {
        "schema": MEASURE_SCHEMA,
        "stop_reason": stop_reason,
        "stopped_by": stopped_by,
        "completed_question_count": len(entries),
        "question_count": len(questions),
        "start_spend_usd": str(start),
        "spend_cap_usd": str(cap),
        "spend_ceiling_usd": str(ceiling),
        "spend_high_water_usd": str(high_water),
        "labels_sha256": labels.sha256,
        "subqueries_sha256": pinned_subqueries_sha256(
            list(subqueries.values()), start_spend_usd=start, spend_cap_usd=cap
        ),
        "database": BENCHMARK_DB_NAME,
        "corpus": {
            "kind": CORPUS_KIND,
            "database": BENCHMARK_DB_NAME,
            "remap_lineage_sha256": remap_lineage_sha256,
            "note": "stored times were remapped from ingest wall clock to session time",
        },
        "render": {
            "max_tokens": injection.DEFAULT_MAX_TOKENS,
            "subquery_quota": SUBQUERY_QUOTA,
            "max_subqueries": MAX_SUBQUERIES,
        },
        "side_effects": {
            "touches": False,
            "trust_updates": False,
            "retrieval_log_rows": False,
            "users_created": False,
            "entitlement_accounts_created": False,
            "fingerprint_before": before_fingerprint.get("sha256"),
            "fingerprint_after": after_fingerprint.get("sha256"),
            "retrieval_log_before": before_log,
            "retrieval_log_after": after_log,
            "fingerprint_users": before_fingerprint.get("users"),
            "note": FINGERPRINT_LIMIT_NOTE,
        },
        "questions": entries,
    }


async def run_measure(
    *,
    questions: Sequence[Question],
    labels: LabelSet,
    subqueries: Mapping[str, Mapping[str, Any]],
    output_path: Path | str,
    start_marker: Path | str,
    remap_lineage_sha256: str,
    measure_question: MeasureQuestion,
    embed_texts: Embed,
    fingerprint: Fingerprint,
    retrieval_log_count: LogCount,
    read_ledger_spend_usd: SpendReader,
    embedding_ceiling_usd: EmbeddingCeiling,
    read_embedding_route: EmbeddingRoute,
    acquire_run_lock: Callable[[], Any],
    check_run_lock: Callable[[], Any],
    read_retrieval_logging_flags: Callable[[], tuple[bool, bool]] = retrieval_logging_flags,
    check_users_present: Callable[[], Any] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Measure the three arms, journalled per question, with side effects off.

    The one start marker must exist and agree with the pinned spend binding, so
    the ceiling is the run's first start rather than the current ledger. Every
    embedding receipt and every completed question is journalled durably before
    the next dispatch, so a mid-run failure loses no paid work: the stop writes a
    partial document marked as stopped and re-raises, and a resume re-dispatches
    nothing that is already recorded. The manifest binds the embedding route, so
    a resume after a route change refuses instead of mixing vectors.
    """
    if not _is_sha256(remap_lineage_sha256):
        raise ValueError("M2 measure requires the remap lineage hash")
    logging_enabled, logging_debug = read_retrieval_logging_flags()
    if logging_enabled or logging_debug:
        raise M2SideEffectError(
            "M2 measure refuses to run while retrieval_logging is enabled: "
            "it would write retrieval_log rows"
        )
    output = Path(output_path)
    journal = _journal_path(output)
    _prepare_resumable_output(output, repo_root, journal)
    marker = _read_start_marker(_start_marker_path(start_marker, repo_root))
    if not await _run_lock_acquired(acquire_run_lock):
        return {"schema": MEASURE_SCHEMA, "stop_reason": "run_lock_busy", "questions": []}

    start, cap = _spend_of_document(subqueries)
    if marker.start_spend_usd != start:
        raise ValueError("M2 start marker disagrees with the generation document")
    if marker.spend_cap_usd != cap:
        raise ValueError("M2 start marker disagrees with the generation document spend cap")
    embedding_route_id, embedding_dimensions = read_embedding_route()
    if not isinstance(embedding_route_id, str) or not embedding_route_id:
        raise ValueError("M2 embedding route is unreadable")
    if (
        not isinstance(embedding_dimensions, int)
        or isinstance(embedding_dimensions, bool)
        or embedding_dimensions <= 0
    ):
        raise ValueError("M2 embedding route dimensions are unreadable")
    ceiling = start + cap
    manifest = _measure_manifest(
        questions,
        labels,
        subqueries,
        start,
        cap,
        embedding_route_id=embedding_route_id,
        embedding_dimensions=embedding_dimensions,
        remap_lineage_sha256=remap_lineage_sha256,
    )
    handle = open(journal, "a", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(f"M2 measure journal is held: {journal}") from None

        results, recorded_vectors, stored_manifest = _read_measure_journal(journal)
        if stored_manifest is None:
            if results or recorded_vectors:
                raise ValueError("M2 measure journal has no manifest")
            _append_journal(handle, manifest)
        elif stored_manifest != manifest:
            raise ValueError("M2 measure journal does not match this run")

        cache = {key: _vector_from_record(record) for key, record in recorded_vectors.items()}
        high_water = start
        for record in recorded_vectors.values():
            high_water = max(high_water, _as_spend(record.get("observed_spend_usd") or "0"))
        for stored in results.values():
            high_water = max(high_water, _as_spend(stored.get("spend_high_water_usd") or "0"))

        before_fingerprint: dict[str, Any] = {}
        after_fingerprint: dict[str, Any] = {}
        before_log: int | None = None
        after_log: int | None = None
        stop_reason: str | None = None
        stopped_by: str | None = None
        try:
            if check_users_present is not None:
                await _maybe_await(check_users_present())
            before_fingerprint = await _maybe_await(fingerprint())
            before_log = int(await _maybe_await(retrieval_log_count()))
            _require_fingerprint(before_fingerprint, len(questions))

            for question in questions:
                key = question.question_id
                if key in results:
                    continue
                if not await _run_lock_held(check_run_lock):
                    raise RuntimeError("M2 measure lost the benchmark run lock")
                pinned = subqueries.get(key)
                if pinned is None:
                    raise ValueError("Pinned sub-queries are missing a question")
                label = labels.entries.get(key)
                if label is None:
                    raise ValueError("Labels are missing a question")
                raw = [str(value) for value in pinned.get("subqueries") or []]
                query_text, components = _embedding_texts(question, raw)
                texts = [query_text, *components]
                batches: list[tuple[list[str], bool]] = []
                if _text_key(query_text) not in cache:
                    batches.append(([query_text], True))
                missing_components = [
                    text for text in dict.fromkeys(components) if _text_key(text) not in cache
                ]
                batches.extend((batch, False) for batch in _embedding_batches(missing_components))
                for batch, is_query in batches:
                    try:
                        legacy = _as_spend(await _maybe_await(embedding_ceiling_usd(batch)))
                    except Exception:
                        raise RuntimeError("M2 embedding ceiling is unreadable") from None
                    ledger = _as_spend(await _maybe_await(read_ledger_spend_usd()))
                    spend = max(ledger, high_water)
                    if spend + legacy > ceiling:
                        raise RuntimeError("M2 spend cap reached before an embedding dispatch")
                    vectors = await _maybe_await(embed_texts(batch, is_query))
                    if not isinstance(vectors, list) or len(vectors) != len(batch):
                        raise ValueError("Embedding count differs from the request")
                    # Accepted: a crash here re-spends this batch on resume, under the ledger cap.
                    observed = spend + legacy
                    high_water = max(high_water, observed)
                    for text, vector in zip(batch, vectors, strict=True):
                        record = _vector_record(text, vector, observed)
                        _append_journal(handle, record)
                        cache[_text_key(text)] = vector
                        recorded_vectors[_text_key(text)] = record

                vectors_by_text = {text: cache[_text_key(text)] for text in texts}
                target_ids = list(label.memory_ids)
                additive = await _maybe_await(
                    measure_question(
                        question, tuple(components), vectors_by_text, "additive", target_ids
                    )
                )
                round_robin = await _maybe_await(
                    measure_question(
                        question, tuple(components), vectors_by_text, "round_robin", target_ids
                    )
                )
                baseline = _arm_view(additive, "baseline", target_ids)
                candidate = _arm_view(additive, "candidate", target_ids)
                baseline_round_robin = _arm_view(round_robin, "candidate", target_ids)
                entry = {
                    "question_id": key,
                    "answerable_from_store": label.answerable_from_store,
                    "abstention": question.abstention,
                    "triggered": bool(components),
                    "subquery_count": len(components),
                    "latency_seconds": pinned.get("latency_seconds"),
                    "error_category": pinned.get("error_category"),
                    "target_count": len(target_ids),
                    "spend_high_water_usd": str(high_water),
                    "baseline": baseline,
                    "candidate": candidate,
                    "round_robin": baseline_round_robin,
                    "candidate_only_rendered_ids": sorted(
                        set(candidate["rendered_ids"]) - set(baseline["rendered_ids"])
                    ),
                }
                _append_journal(handle, {"kind": "result", **entry})
                results[key] = entry

            after_fingerprint = await _maybe_await(fingerprint())
            after_log = int(await _maybe_await(retrieval_log_count()))
            _require_fingerprint(after_fingerprint, len(questions))
            if before_fingerprint.get("sha256") != after_fingerprint.get("sha256"):
                raise M2SideEffectError("M2 measure changed the benchmark fingerprint")
            if before_log != after_log:
                raise M2SideEffectError("M2 measure wrote retrieval_log rows")
        except BaseException as error:
            stop_reason = _measure_stop_reason(error)
            stopped_by = type(error).__name__
            _write_document_atomic(
                output,
                _measure_document(
                    questions=questions,
                    labels=labels,
                    subqueries=subqueries,
                    results=results,
                    start=start,
                    cap=cap,
                    ceiling=ceiling,
                    high_water=high_water,
                    before_fingerprint=before_fingerprint,
                    before_log=before_log,
                    after_fingerprint=after_fingerprint,
                    after_log=after_log,
                    stop_reason=stop_reason,
                    stopped_by=stopped_by,
                    remap_lineage_sha256=remap_lineage_sha256,
                ),
            )
            raise

        document = _measure_document(
            questions=questions,
            labels=labels,
            subqueries=subqueries,
            results=results,
            start=start,
            cap=cap,
            ceiling=ceiling,
            high_water=high_water,
            before_fingerprint=before_fingerprint,
            before_log=before_log,
            after_fingerprint=after_fingerprint,
            after_log=after_log,
            stop_reason=None,
            stopped_by=None,
            remap_lineage_sha256=remap_lineage_sha256,
        )
        _write_document_atomic(output, document)
        return document
    finally:
        handle.close()


def _require_fingerprint(value: Mapping[str, Any], users: int) -> None:
    if not isinstance(value, Mapping) or not isinstance(value.get("sha256"), str):
        raise ValueError("Fingerprint is unreadable")
    if int(value.get("users") or 0) != users:
        raise M2SideEffectError("Fingerprint did not cover every M2 question user")


def check_m2_database(dsn: str | None) -> str:
    """Refuse unless ``DATABASE_URL`` names exactly the M2 database."""
    name = db_name_from_dsn(dsn or "")
    if name != BENCHMARK_DB_NAME:
        raise ValueError(f"M2 requires DATABASE_URL to name exactly {BENCHMARK_DB_NAME}")
    return name


def _fraction_value(document: Mapping[str, Any]) -> Fraction:
    value = document.get("gate1_threshold")
    if not isinstance(value, Mapping):
        raise ValueError("Gate 1 threshold is missing")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise ValueError("Gate 1 threshold is not an exact fraction")
    return Fraction(numerator, denominator)


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _plausible_items(value: Any, added: set[str], question_id: str) -> list[str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Gate 4 plausibility is missing: {question_id}")
    ids = value.get("plausible_ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise ValueError(f"Gate 4 plausibility must list plausible_ids: {question_id}")
    if len(set(ids)) != len(ids):
        raise ValueError(f"Gate 4 plausibility ids are duplicated: {question_id}")
    outside = sorted(set(ids) - added)
    if outside:
        raise ValueError(
            f"Gate 4 plausibility names an item the candidate did not add: {question_id}"
        )
    return sorted(ids)


def run_gates(
    *,
    threshold: Mapping[str, Any],
    labels: LabelSet,
    measure: Mapping[str, Any],
    plausibility: Mapping[str, Any],
    output_path: Path | str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Pure evaluation of gates 1, 2, 4 and 5; gate 3 is the existing unit test."""
    output = _prepare_output(output_path, repo_root)
    if threshold.get("schema") != THRESHOLD_SCHEMA:
        raise ValueError("Threshold document has the wrong schema")
    if measure.get("schema") != MEASURE_SCHEMA:
        raise ValueError("Measure document has the wrong schema")
    if threshold.get("labels_sha256") != labels.sha256:
        raise ValueError("Threshold was computed from different labels")
    if measure.get("labels_sha256") != labels.sha256:
        raise ValueError("Measure ran against different labels")
    if measure.get("stop_reason") is not None:
        raise ValueError("Measure document stopped before completion")
    if plausibility.get("schema") != PLAUSIBILITY_SCHEMA:
        raise ValueError("Gate 4 plausibility input has the wrong schema")

    threshold_value = _fraction_value(threshold)
    if int(threshold.get("answerable_count") or -1) != len(labels.exposure_answerable_ids):
        raise ValueError("Threshold answerable count disagrees with the label set")
    raw_questions = measure.get("questions")
    if not isinstance(raw_questions, list) or len(raw_questions) != len(labels.entries):
        raise ValueError("Measure document does not cover the label set exactly")
    measured: dict[str, Mapping[str, Any]] = {}
    for value in raw_questions:
        if not isinstance(value, Mapping):
            raise ValueError("Measure question entry must be an object")
        question_id = value.get("question_id")
        if not isinstance(question_id, str) or question_id in measured:
            raise ValueError("Measure question ids are invalid")
        if question_id not in labels.entries:
            raise ValueError("Measure question is outside the label set")
        measured[question_id] = value
    if set(measured) != set(labels.entries):
        raise ValueError("Measure question set differs from the label set")

    answerable = labels.exposure_answerable_ids
    newly_exposed: list[str] = []
    for question_id in answerable:
        row = measured[question_id]
        baseline = row.get("baseline")
        candidate = row.get("candidate")
        if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
            raise ValueError("Measure arm data is missing")
        if not bool(baseline.get("target_exposed")) and bool(candidate.get("target_exposed")):
            newly_exposed.append(question_id)

    gate1_pass = Fraction(len(newly_exposed)) >= threshold_value
    losses: dict[str, list[str]] = {}
    for question_id in answerable:
        row = measured[question_id]
        baseline = row.get("baseline")
        candidate = row.get("candidate")
        if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
            raise ValueError("Measure arm data is missing")
        exposed_before = set(baseline.get("rendered_target_ids") or [])
        exposed_after = set(candidate.get("rendered_target_ids") or [])
        lost = sorted(exposed_before - exposed_after)
        if lost:
            losses[question_id] = lost
    gate2_pass = not losses

    abstention = [qid for qid in labels.entries if qid.endswith("_abs")]
    if len(abstention) != ABSTENTION_COUNT:
        raise ValueError(f"Expected exactly {ABSTENTION_COUNT} abstention questions")
    raw_plausibility = plausibility.get("questions")
    if not isinstance(raw_plausibility, Mapping):
        raise ValueError("Gate 4 plausibility has no per-question judgments")
    gate4_rows: list[dict[str, Any]] = []
    plausible_total = 0
    for question_id in abstention:
        row = measured[question_id]
        candidate_only = set(str(value) for value in row.get("candidate_only_rendered_ids") or [])
        if question_id not in raw_plausibility:
            raise ValueError(f"Gate 4 plausibility is missing: {question_id}")
        plausible = _plausible_items(raw_plausibility[question_id], candidate_only, question_id)
        plausible_total += len(plausible)
        baseline = row.get("baseline")
        candidate = row.get("candidate")
        if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
            raise ValueError("Measure arm data is missing")
        gate4_rows.append(
            {
                "question_id": question_id,
                "baseline_rendered_count": int(baseline.get("rendered_count") or 0),
                "candidate_rendered_count": int(candidate.get("rendered_count") or 0),
                "candidate_only_items": sorted(candidate_only),
                "candidate_only_count": len(candidate_only),
                "plausible_ids": plausible,
            }
        )
    gate4_flagged = plausible_total >= GATE4_FLAG_THRESHOLD
    # The pre-registration's gate 4 wording allows two readings: count plausible
    # items, or count abstention questions with at least one. The frozen gate is
    # the item count; the question count is reported as an alternative reading
    # for the owner to rule on, and never changes the result.
    alternative_ids = [row["question_id"] for row in gate4_rows if row["plausible_ids"]]

    latencies = [
        float(row["latency_seconds"])
        for row in measured.values()
        if isinstance(row.get("latency_seconds"), (int, float))
        and row.get("error_category") is None
    ]
    triggered = sum(1 for row in measured.values() if bool(row.get("triggered")))
    gate5 = {
        "report_only": True,
        "trigger_rate": triggered / len(measured),
        "completed_calls": len(latencies),
        "latency_p50_seconds": _percentile(latencies, 0.50),
        "latency_p95_seconds": _percentile(latencies, 0.95),
    }

    gate1 = {
        "threshold_exact": _fraction_text(threshold_value),
        "answerable_count": len(answerable),
        "newly_exposed_count": len(newly_exposed),
        "newly_exposed_question_ids": sorted(newly_exposed),
        "passed": gate1_pass,
    }
    gate2 = {
        "passed": gate2_pass,
        "losses": losses,
    }
    gate4 = {
        "flagged_for_owner_review": gate4_flagged,
        "plausible_candidate_only_count": plausible_total,
        "question_count": len(gate4_rows),
        "alternative_reading": {
            "description": "abstention questions with at least one plausible candidate-only item",
            "count": len(alternative_ids),
            "question_ids": alternative_ids,
            "affects_result": False,
        },
        "rows": gate4_rows,
    }
    result = (
        "fail"
        if not (gate1_pass and gate2_pass)
        else ("flag_for_owner_review" if gate4_flagged else "pass")
    )
    document = {
        "schema": GATES_SCHEMA,
        "result": result,
        "route_mapping": ROUTE_MAPPING,
        "gate1": gate1,
        "gate2": gate2,
        "gate3": {"covered": True, "test": GATE3_TEST},
        "gate4": gate4,
        "gate5": gate5,
        "note": "Round-robin is reported only and is not read by this decision.",
    }
    _write_document_atomic(output, document)
    return document


async def run_retention(
    *,
    questions: Sequence[Question],
    labels: LabelSet,
    reader: RetentionReader,
    output_path: Path | str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Read extraction-log evidence for every not-answerable label; no spend."""
    output = _prepare_output(output_path, repo_root)
    from orchestrator.eval.bench_db import question_user_id

    by_id = {question.question_id: question for question in questions}
    rows: list[dict[str, Any]] = []
    for question_id in labels.not_answerable_ids:
        question = by_id.get(question_id)
        if question is None:
            raise ValueError("Retention question is missing from the fixture")
        if not question.answer_session_ids:
            raise ValueError("Retention requires the question's answer session ids")
        evidence = await _maybe_await(
            reader(question_user_id(question_id), question_id, question.answer_session_ids)
        )
        rows.append({"question_id": question_id, "rows": evidence})
    document = {
        "schema": RETENTION_SCHEMA,
        "question_count": len(rows),
        "limits": RETENTION_LIMIT_NOTE,
        "spend_usd": "0",
        "questions": rows,
    }
    _write_document_atomic(output, document)
    return document


REPORT_CATEGORIES: Final[tuple[str, ...]] = (
    "fully_exposed_both",
    "newly_exposed",
    "lost_target",
    "partially_exposed",
    "unexposed",
    "not_answerable",
)


def _report_category(row: Mapping[str, Any]) -> str:
    if not bool(row.get("answerable_from_store")):
        return "not_answerable"
    baseline = row.get("baseline")
    candidate = row.get("candidate")
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        raise ValueError("Report needs both arms")
    before = bool(baseline.get("target_exposed"))
    after = bool(candidate.get("target_exposed"))
    if before and after:
        return "fully_exposed_both"
    if not before and after:
        return "newly_exposed"
    if before and not after:
        return "lost_target"
    if bool(baseline.get("target_any_exposed")) or bool(candidate.get("target_any_exposed")):
        return "partially_exposed"
    return "unexposed"


def _retention_diagnosis(
    categories: Mapping[str, Any], not_answerable_ids: Sequence[str]
) -> dict[str, dict[str, str | None]]:
    """Validate the primary's diagnosis; only closed reasons may be committed."""
    outside = sorted(set(categories) - set(not_answerable_ids))
    if outside:
        raise ValueError("Retention diagnosis names a question that is not not-answerable")
    diagnosis: dict[str, dict[str, str | None]] = {}
    for question_id in not_answerable_ids:
        value = categories.get(question_id)
        if not isinstance(value, Mapping):
            raise ValueError(f"Retention diagnosis is missing: {question_id}")
        category = value.get("category")
        if category not in RETENTION_CATEGORIES:
            raise ValueError(f"Retention diagnosis has an unknown category: {question_id}")
        reason = value.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ValueError(f"Retention diagnosis reason is not text: {question_id}")
        # A free reason carries benchmark-derived text, so it is dropped here
        # unless it is one of the closed values.
        diagnosis[question_id] = {
            "category": str(category),
            "reason": reason if reason in RETENTION_REASONS else None,
        }
    return diagnosis


def run_report(
    *,
    measure: Mapping[str, Any],
    gates: Mapping[str, Any],
    retention_categories: Mapping[str, Any],
    output_path: Path | str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Committed form: question ids, categories and counts only. No benchmark text."""
    output = _prepare_output(output_path, repo_root)
    if measure.get("schema") != MEASURE_SCHEMA or gates.get("schema") != GATES_SCHEMA:
        raise ValueError("Report inputs have the wrong schemas")
    raw_questions = measure.get("questions")
    if not isinstance(raw_questions, list):
        raise ValueError("Report needs the measure question list")
    not_answerable = [
        str(value.get("question_id"))
        for value in raw_questions
        if isinstance(value, Mapping) and value.get("answerable_from_store") is False
    ]
    diagnosis = _retention_diagnosis(retention_categories, not_answerable)
    questions: list[dict[str, str]] = []
    for value in raw_questions:
        if not isinstance(value, Mapping):
            raise ValueError("Report question entry must be an object")
        question_id = value.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("Report question id is invalid")
        category = _report_category(value)
        row: dict[str, str] = {"question_id": question_id, "category": category}
        # Whitelist every copied field: no question text, memory ids or notes.
        found = diagnosis.get(question_id)
        if found is not None:
            row["retention_category"] = str(found["category"])
            if found["reason"] is not None:
                row["retention_reason"] = str(found["reason"])
        questions.append(row)
    counts = Counter(row["category"] for row in questions)
    retention_counts = Counter(
        row["retention_category"] for row in questions if "retention_category" in row
    )
    gate_result = gates.get("result")
    if gate_result not in {"pass", "fail", "flag_for_owner_review"}:
        raise ValueError("Gate result is not a recognised outcome")
    document = {
        "schema": REPORT_SCHEMA,
        "question_count": len(questions),
        "category_counts": {name: counts.get(name, 0) for name in REPORT_CATEGORIES},
        "retention_category_counts": {
            name: retention_counts.get(name, 0) for name in RETENTION_CATEGORIES
        },
        "gate_result": gate_result,
        "gate1_passed": bool((gates.get("gate1") or {}).get("passed")),
        "gate2_passed": bool((gates.get("gate2") or {}).get("passed")),
        "gate4_flagged": bool((gates.get("gate4") or {}).get("flagged_for_owner_review")),
        "questions": questions,
    }
    _write_document_atomic(output, document)
    return document


# --- live wiring -------------------------------------------------------------


def verify_generation_route() -> Any:
    from orchestrator.entitlements import attestation
    from orchestrator.entitlements.policy import load_inference_policy

    policy = load_inference_policy()
    route = policy.routes.get(GENERATION_ROUTE_ID)
    if route is None or not route.is_approved(policy.requirements):
        raise ValueError("M2 generation route is not approved")
    if not attestation.is_monitored(route):
        raise ValueError("M2 generation route is not monitored")
    return route


def generation_ceiling(route: Any, payload: Mapping[str, Any]) -> Decimal:
    from orchestrator.compute_runtime import _request_bound

    if getattr(route, "price_ceiling", None) is None:
        raise ValueError("M2 generation route has no price ceiling")
    params = {key: value for key, value in payload.items() if not key.startswith("_")}
    micro = route.estimate_microusd(_request_bound(params).bound, GENERATION_MAX_TOKENS)
    return Decimal(int(micro)) / Decimal(1_000_000)


def selected_embedding_route() -> Any:
    """The pinned route ``embedding_ceiling_usd`` charges and the manifest binds."""
    from orchestrator.memory import embedding, embedding_adapter

    selector = embedding.get_selected_embedding_route_id()
    if not selector:
        raise ValueError("M2 embeddings require a pinned route")
    return embedding_adapter.approved_route(selector)


def embedding_route_identity() -> tuple[str, int]:
    route = selected_embedding_route()
    return str(route.route_id), int(route.dimensions)


def embedding_ceiling_usd(texts: Sequence[str]) -> Decimal:
    from orchestrator.memory import embedding_adapter

    route = selected_embedding_route()
    tokens = sum(embedding_adapter.input_bound(text) for text in texts)
    return Decimal(int(route.estimate_microusd(tokens))) / Decimal(1_000_000)


def _live_connection() -> Any:
    return None


async def _run_live_generation(
    *,
    questions: Sequence[Question],
    output_path: Path | str,
    start_marker: Path | str,
    remap_lineage: Path | str,
) -> dict[str, Any]:
    import asyncpg

    from orchestrator import compute_runtime
    from orchestrator.config import get_settings
    from orchestrator.entitlements import attestation
    from orchestrator.eval.bench_db import (
        ensure_benchmark_user,
        fingerprint_benchmark_users,
        question_user_id,
    )
    from tests.benchmark_longmemeval.dev2_pilot import (
        RUN_LOCK_HELD_SQL,
        SPEND_SQL,
        acquire_benchmark_run_lock,
        spend_from_row,
    )

    settings = get_settings()
    check_m2_database(settings.database_url)
    lineage = load_remap_lineage(remap_lineage)
    user_id = uuid.uuid5(M2_USER_NAMESPACE, M2_USER_NAME)
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=1)
    connection = await asyncpg.connect(settings.database_url)
    try:
        # No spend before the corpus is proven to be the remapped copy.
        current = await fingerprint_benchmark_users(
            pool, [question_user_id(question.question_id) for question in questions]
        )
        check_remap_lineage(lineage, current)
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

        return await run_generation(
            questions=questions,
            output_path=output_path,
            start_marker=start_marker,
            spend_cap_usd=SPEND_CAP_USD,
            remap_lineage_sha256=lineage.sha256,
            call=call,
            check_attestation=check_attestation,
            verify_route=verify_generation_route,
            account_scope=account_scope,
            acquire_run_lock=acquire_run_lock,
            check_run_lock=check_run_lock,
            read_ledger_spend_usd=read_ledger_spend_usd,
            next_call_ceiling_usd=lambda payload: generation_ceiling(
                verify_generation_route(), payload
            ),
        )
    finally:
        await connection.close()
        await pool.close()


def measure_question_live(
    store: Any,
    labels: LabelSet,
    *,
    question: Question,
    subqueries: tuple[str, ...],
    vectors: Mapping[str, EmbeddingVectorResult],
    merge: str,
    target_ids: Sequence[str],
) -> Awaitable[dict[str, Any]]:
    from orchestrator.memory import injection
    from orchestrator.eval.bench_db import question_user_id

    query = injection._normalize_content(question.message)
    components = validate_subqueries(list(subqueries), query, max_subqueries=MAX_SUBQUERIES)
    label = labels.entries[question.question_id]
    return measure_multi_query_exposure(
        store,
        user_id=question_user_id(question.question_id),
        question=query,
        provider=PinnedSubqueryProvider(tuple(components)),
        query_embeddings=vectors,
        reference_time=question.reference_time,
        allowed_source_conversation_ids=None,
        target_ids=list(label.memory_ids) or list(target_ids),
        gold_conversation_ids=(),
        source_conversations={},
        merge=merge,
    )


async def _embed_texts_live(texts: Sequence[str], is_query: bool) -> list[EmbeddingVectorResult]:
    from orchestrator.memory.embedding import embed_queries_with_metadata, embed_query_with_metadata

    if is_query:
        if len(texts) != 1:
            raise ValueError("Single query embedding requires exactly one text")
        return [await embed_query_with_metadata(texts[0])]
    return await embed_queries_with_metadata(list(texts))


async def _run_live_measure(
    *,
    questions: Sequence[Question],
    labels: LabelSet,
    subqueries: Mapping[str, Mapping[str, Any]],
    output_path: Path | str,
    start_marker: Path | str,
    remap_lineage: Path | str,
) -> dict[str, Any]:
    import asyncpg

    from orchestrator import compute_runtime
    from orchestrator.config import get_settings
    from orchestrator.eval.bench_db import (
        assert_benchmark_database,
        fingerprint_benchmark_users,
        question_user_id,
    )
    from orchestrator.memory.encryption import ContentEncryption
    from orchestrator.memory.store import MemoryStore
    from tests.benchmark_longmemeval.dev2_pilot import (
        RUN_LOCK_HELD_SQL,
        SPEND_SQL,
        acquire_benchmark_run_lock,
        spend_from_row,
    )

    settings = get_settings()
    check_m2_database(settings.database_url)
    lineage = load_remap_lineage(remap_lineage)
    user_id = uuid.uuid5(M2_USER_NAMESPACE, M2_USER_NAME)
    user_ids = [question_user_id(question.question_id) for question in questions]
    required_user_ids = [user_id, *user_ids]
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=1)
    connection = await asyncpg.connect(settings.database_url)
    store = MemoryStore(pool, ContentEncryption(settings.daemon_encryption_key))
    try:
        await assert_benchmark_database(pool, settings.database_url)
        current = await fingerprint_benchmark_users(pool, user_ids)
        check_remap_lineage(lineage, current)
        if settings.memory_subquery_quota != SUBQUERY_QUOTA:
            raise ValueError(f"M2 requires memory_subquery_quota={SUBQUERY_QUOTA}")
        if settings.memory_max_subqueries < MAX_SUBQUERIES:
            raise ValueError("M2 requires memory_max_subqueries to admit 3 sub-queries")
        # Measure writes nothing to users or entitlement_accounts: every required
        # user must already exist from the ingest/generation phases.
        if settings.retrieval_logging_enabled or settings.retrieval_logging_debug:
            raise M2SideEffectError("M2 measure refuses to run while retrieval_logging is enabled")

        async def check_users_present() -> None:
            rows = await connection.fetch(
                "SELECT id FROM users WHERE id = ANY($1::uuid[])", required_user_ids
            )
            present = {str(row["id"]) for row in rows}
            missing = [value for value in required_user_ids if str(value) not in present]
            if missing:
                raise M2SideEffectError(
                    f"M2 measure requires {len(user_ids)} existing benchmark users and the "
                    f"M2 account user; {len(missing)} are missing"
                )

        async def fingerprint() -> dict[str, Any]:
            return await fingerprint_benchmark_users(pool, user_ids)

        async def log_count() -> int:
            value = await connection.fetchval("SELECT count(*) FROM retrieval_log")
            return int(value or 0)

        async def embed(texts: Sequence[str], is_query: bool) -> list[EmbeddingVectorResult]:
            async with compute_runtime.account_compute(
                pool, user_id, operation="chat", background=True, profile="background"
            ):
                return await _embed_texts_live(texts, is_query)

        async def read_ledger_spend_usd() -> Decimal:
            row = await connection.fetchrow(SPEND_SQL)
            return Decimal(str(spend_from_row(row).total_usd))

        async def measure_question(
            question: Question,
            components: tuple[str, ...],
            vectors: Mapping[str, EmbeddingVectorResult],
            merge: str,
            target_ids: Sequence[str],
        ) -> dict[str, Any]:
            return await measure_question_live(
                store,
                labels,
                question=question,
                subqueries=components,
                vectors=vectors,
                merge=merge,
                target_ids=target_ids,
            )

        return await run_measure(
            questions=questions,
            labels=labels,
            subqueries=subqueries,
            output_path=output_path,
            start_marker=start_marker,
            remap_lineage_sha256=lineage.sha256,
            measure_question=measure_question,
            embed_texts=embed,
            fingerprint=fingerprint,
            retrieval_log_count=log_count,
            read_ledger_spend_usd=read_ledger_spend_usd,
            embedding_ceiling_usd=embedding_ceiling_usd,
            read_embedding_route=embedding_route_identity,
            acquire_run_lock=lambda: acquire_benchmark_run_lock(connection.fetchval),
            check_run_lock=lambda: _check_lock(connection.fetchval, RUN_LOCK_HELD_SQL),
            read_retrieval_logging_flags=lambda: (
                bool(settings.retrieval_logging_enabled),
                bool(settings.retrieval_logging_debug),
            ),
            check_users_present=check_users_present,
        )
    finally:
        await connection.close()
        await pool.close()


async def _check_lock(fetchval: Callable[..., Any], sql: str) -> bool:
    return int(await fetchval(sql) or 0) >= 1


def _retention_reader_live(
    *,
    checkpoint: Mapping[str, Any],
    encryption: Any,
) -> RetentionReader:
    """Read-only extraction-log reader; one short connection per question, no spend."""
    import asyncpg

    from orchestrator.config import get_settings
    from tests.benchmark_longmemeval.dev2_pilot import answer_conversation_ids

    settings = get_settings()
    check_m2_database(settings.database_url)

    async def read(
        user_id: uuid.UUID, question_id: str, session_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        mapping = answer_conversation_ids(checkpoint, question_id, list(session_ids))
        conversation_ids = [uuid.UUID(value) for value in mapping.values()]
        connection = await asyncpg.connect(settings.database_url)
        try:
            rows = await connection.fetch(
                "SELECT conversation_id, input_snippet, dedup_results, model_used, created_at "
                "FROM memory_extraction_log WHERE user_id = $1 "
                "AND conversation_id = ANY($2::uuid[]) "
                "ORDER BY created_at ASC, conversation_id ASC",
                user_id,
                conversation_ids,
            )
        finally:
            await connection.close()
        evidence: list[dict[str, Any]] = []
        for row in rows:
            snippet = row["input_snippet"]
            evidence.append(
                {
                    "conversation_id": str(row["conversation_id"]),
                    "input_snippet": encryption.decrypt(snippet)
                    if isinstance(snippet, str)
                    else "",
                    "dedup_results": row["dedup_results"],
                    "model_used": row["model_used"],
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"] is not None
                    else None,
                }
            )
        return evidence

    return read


def _load_checkpoint(path: Path | str) -> Mapping[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("Retention checkpoint must be an object")
    return data


# --- CLI ---------------------------------------------------------------------


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=None, help=argparse.SUPPRESS)


def _add_labels(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--labels", type=Path, action="append", required=True)


def _add_remap_lineage(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--remap-lineage",
        type=Path,
        required=True,
        help=(
            "The dev2_remap verify lineage, outside the repository: the run refuses "
            "before any spend unless it names the remapped copy and matches its fingerprint."
        ),
    )


def _add_start_marker(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--start-marker",
        type=Path,
        required=True,
        help=(
            "One per M2 run, outside the repository: the first start and the cap. "
            "Created by the first generate; every later generate and measure reads it."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.benchmark_longmemeval.m2_exposure",
        description="M2 exposure gate driver: threshold, generate, measure, gates, retention, report.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    threshold = sub.add_parser("threshold", help="Record gate 1's value before retrieval.")
    _add_labels(threshold)
    threshold.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    _add_output(threshold)

    generate = sub.add_parser("generate", help="One PROMPT_V2 pass on the Block D route.")
    generate.add_argument("--questions", type=Path, required=True)
    generate.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    _add_remap_lineage(generate)
    _add_start_marker(generate)
    _add_output(generate)

    measure = sub.add_parser("measure", help="Three arms through the production exposure metric.")
    measure.add_argument("--questions", type=Path, required=True)
    measure.add_argument("--subqueries", type=Path, required=True)
    measure.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    _add_remap_lineage(measure)
    _add_start_marker(measure)
    _add_labels(measure)
    _add_output(measure)

    gates = sub.add_parser("gates", help="Pure gate evaluation; no database.")
    gates.add_argument("--threshold", type=Path, required=True)
    gates.add_argument("--measure", type=Path, required=True)
    gates.add_argument("--plausibility", type=Path, required=True)
    gates.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    _add_labels(gates)
    _add_output(gates)

    retention = sub.add_parser("retention", help="Extraction-log evidence; read-only, no spend.")
    retention.add_argument("--questions", type=Path, required=True)
    retention.add_argument("--checkpoint", type=Path, required=True)
    retention.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    _add_labels(retention)
    _add_output(retention)

    report = sub.add_parser("report", help="Sanitized question ids, categories and counts.")
    report.add_argument("--measure", type=Path, required=True)
    report.add_argument("--gates", type=Path, required=True)
    report.add_argument(
        "--categories",
        type=Path,
        required=True,
        help="Primary-supplied retention diagnosis: question id, category, reason.",
    )
    report.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    _add_output(report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    frozen = load_frozen_ids(args.frozen_ids)
    repo_root = args.repo_root

    if args.command == "threshold":
        labels = load_labels(args.labels, frozen)
        document = run_threshold(
            labels=labels, frozen=frozen, output_path=args.output, repo_root=repo_root
        )
        print(json.dumps(document["gate1_threshold"], sort_keys=True))
        return 0

    if args.command == "generate":
        questions = load_questions(args.questions, frozen)
        document = asyncio.run(
            _run_live_generation(
                questions=questions,
                output_path=args.output,
                start_marker=args.start_marker,
                remap_lineage=args.remap_lineage,
            )
        )
        return 0 if document.get("stop_reason") is None else 1

    if args.command == "measure":
        questions = load_questions(args.questions, frozen)
        labels = load_labels(args.labels, frozen)
        subqueries = load_pinned_subqueries(args.subqueries, frozen)
        document = asyncio.run(
            _run_live_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=args.output,
                start_marker=args.start_marker,
                remap_lineage=args.remap_lineage,
            )
        )
        return 0 if document.get("stop_reason") is None else 1

    if args.command == "gates":
        labels = load_labels(args.labels, frozen)
        threshold = json.loads(Path(args.threshold).read_text(encoding="utf-8"))
        measure = json.loads(Path(args.measure).read_text(encoding="utf-8"))
        plausibility = json.loads(Path(args.plausibility).read_text(encoding="utf-8"))
        document = run_gates(
            threshold=threshold,
            labels=labels,
            measure=measure,
            plausibility=plausibility,
            output_path=args.output,
            repo_root=repo_root,
        )
        print(document["result"])
        return 0

    if args.command == "retention":
        questions = load_questions(args.questions, frozen)
        labels = load_labels(args.labels, frozen)
        checkpoint = _load_checkpoint(args.checkpoint)
        from orchestrator.config import get_settings
        from orchestrator.memory.encryption import ContentEncryption

        encryption = ContentEncryption(get_settings().daemon_encryption_key)
        by_id = {question.question_id: question for question in questions}
        if set(labels.not_answerable_ids) - set(by_id):
            raise ValueError("Retention question fixture is incomplete")
        reader = _retention_reader_live(checkpoint=checkpoint, encryption=encryption)

        document = asyncio.run(
            run_retention(
                questions=questions,
                labels=labels,
                reader=reader,
                output_path=args.output,
                repo_root=repo_root,
            )
        )
        print(f"retention rows: {document['question_count']}")
        return 0

    if args.command == "report":
        measure = json.loads(Path(args.measure).read_text(encoding="utf-8"))
        gates = json.loads(Path(args.gates).read_text(encoding="utf-8"))
        categories = json.loads(Path(args.categories).read_text(encoding="utf-8"))
        if not isinstance(categories, Mapping):
            raise ValueError("Retention diagnosis must be an object of question id to category")
        document = run_report(
            measure=measure,
            gates=gates,
            retention_categories=categories,
            output_path=args.output,
            repo_root=repo_root,
        )
        print(json.dumps(document["category_counts"], sort_keys=True))
        return 0

    parser.error("Unknown command")
    return 2


__all__ = [
    "ABSTENTION_COUNT",
    "BENCHMARK_DB_NAME",
    "CORPUS_KIND",
    "FROZEN_IDS_PATH",
    "GATE3_TEST",
    "GATES_SCHEMA",
    "GENERATION_SCHEMA",
    "GENERATION_ROUTE_ID",
    "LABEL_SCHEMAS",
    "LabelSet",
    "MEASURE_SCHEMA",
    "M2SideEffectError",
    "Question",
    "REMAP_LINEAGE_SCHEMA",
    "RemapLineage",
    "REPORT_CATEGORIES",
    "RETENTION_CATEGORIES",
    "RETENTION_REASONS",
    "RETENTION_SCHEMA",
    "ROUTE_MAPPING",
    "SPEND_CAP_USD",
    "SUBQUERY_QUOTA",
    "THRESHOLD_SCHEMA",
    "check_m2_database",
    "gate1_threshold",
    "build_parser",
    "load_frozen_ids",
    "load_labels",
    "load_pinned_subqueries",
    "load_questions",
    "main",
    "pinned_subqueries_sha256",
    "retrieval_logging_flags",
    "run_gates",
    "run_generation",
    "run_measure",
    "run_report",
    "run_retention",
    "run_threshold",
]


if __name__ == "__main__":
    raise SystemExit(main(list(sys.argv[1:])))
