from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import inspect
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import asyncpg

from orchestrator import model_routing, routing_log
from orchestrator.auth_pepper import initialize_development_pepper, validate_pepper_config
from orchestrator.compute_runtime import account_compute
from orchestrator.config import get_settings
from orchestrator.eval.bench_db import (
    BENCHMARK_USER_NAMESPACE,
    CROSS_SESSION_TABLES,
    assert_benchmark_database,
    check_benchmark_dsn,
    ensure_benchmark_user,
    fingerprint_benchmark_state,
    fingerprint_benchmark_users,
    list_benchmark_conversation_ids,
    question_user_id,
    rollback_orphan_conversations,
    summarize_compute_spend,
    summarize_ingest_routes,
    wipe_benchmark_user,
)
from orchestrator.eval.durable_io import (
    atomic_write_json,
    file_sha256,
    read_jsonl_records,
    append_jsonl_record,
)
from orchestrator.eval.interrupts import StopController
from orchestrator.eval.run_manifest import (
    MANIFEST_FILENAME,
    check_code_pin,
    git_state,
    ManifestPinError,
    load_manifest,
    optional_file_sha256,
    save_manifest,
    source_sha256,
)
from orchestrator.entitlements import attestation
from orchestrator.entitlements.policy import load_inference_policy
from orchestrator.memory import injection
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.injection import (
    MemoryContextInputs,
    compose_system_prompt,
    render_memory_context,
)
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.evaluate import (
    ANSWER_MAX_TOKENS,
    BENCHMARK_ANSWER_MODEL,
    BENCHMARK_JUDGE_MODEL,
    BENCHMARK_ANSWER_ENDPOINT_SLUG,
    BENCHMARK_SEED,
    CATEGORY_MAP,
    CHECKPOINT_FILENAME,
    JUDGE_MAX_TOKENS,
    RESULTS_FILENAME,
    evaluate_single_parity,
    print_results,
    score_accuracy,
)
from tests.longmemeval.ingest import (
    CorpusPlan,
    TEST_USER_ID,
    build_corpus_key,
    build_corpus_plan,
    ingest_session_parity,
    normalize_question_id,
)
from tests.longmemeval.official_judge import (
    OFFICIAL_MAX_TOKENS,
    OFFICIAL_SOURCE_COMMIT,
    score_dual_judgments,
)

logger = logging.getLogger(__name__)

PhaseName = Literal["ingest", "evaluate", "score"]

SCORE_FILENAME = "longmemeval_score.json"
# v4: one benchmark user per question, sessions in haystack-date order, results
# keyed by question and session. v3 (one shared user) and older must not mix.
CHECKPOINT_VERSION = 4
DEFAULT_OUTPUT_DIR = Path("tests/benchmark_results")
BENCHMARK_NAME = "longmemeval_fact"
BENCHMARK_SUBSTRATE = "fact"
BENCHMARK_CONFIG_PIN_PATH = Path("tests/benchmark_longmemeval/longmemeval_config_pin.json")

HARNESS_BANNER = (
    "[fact-harness] LongMemEval FACT-substrate runner — LLM-extracted facts via "
    "production store.insert_memory. Use for wave-gate evaluation."
)

# Differences between a benchmark question and a production chat turn that the
# harness cannot remove. Copied into every run manifest; keep in sync with
# tests/benchmark_results/parity_baseline_*.md.
PARITY_EXCEPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "query_source",
        "difference": "The question text stands in for the latest user message; production "
        "derives the query from the conversation's recent messages.",
        "impact": "Same normalisation and retrieval call; no prior turns in the query.",
    },
    {
        "id": "benchmark_users",
        "difference": "Each question's haystack is ingested into its own benchmark user, in "
        "haystack-date order; a session shared by several questions is ingested once per "
        "question.",
        "impact": "Each user's memory holds one history, as in production and LongMemEval; "
        "retrieval reads all of that user's memories, unscoped.",
    },
    {
        "id": "read_only_evaluate",
        "difference": "Evaluate disables trust recording and memory access touches "
        "(record_side_effects=False, touch_results=False).",
        "impact": "Production updates access_count/last_accessed_at, which feed later ranking; "
        "the harness keeps every question on the ingest state.",
    },
    {
        "id": "retrieval_log_label",
        "difference": "Retrieval logging is forced on with retrieval_triggered_by='longmemeval'.",
        "impact": "Diagnostics only; retrieval_log is not a ranking input.",
    },
    {
        "id": "system_prompt_extras",
        "difference": "The answer prompt is compose_system_prompt(memory_context) with no "
        "preferences, skills index, answer controls or tools.",
        "impact": "The memory block and base Daemon prompt match production; the answer model "
        "cannot call memory tools to fetch more.",
    },
    {
        "id": "answer_judge_models",
        "difference": "Answers and judgments use the pinned benchmark route "
        f"({BENCHMARK_ANSWER_MODEL}, temperature 0, seed {BENCHMARK_SEED}), not the "
        "production chat route.",
        "impact": "Measures memory surface with a fixed reader; not end-to-end product quality.",
    },
    {
        "id": "no_post_extraction_jobs",
        "difference": "Ingest runs extraction and dedup only: no entity resolution, "
        "conversation summaries, dreaming or consolidation jobs.",
        "impact": "No post-extraction jobs are scheduled; extraction may still store summary-category "
        "memories, which the shared production renderer includes. This is not a summary-free arm.",
    },
    {
        "id": "message_timestamps",
        "difference": "Haystack messages are stored at ingest time, not at the dataset's "
        "session dates, and retrieval runs with query_reference_time=None.",
        "impact": "Recency and temporal windows see the ingest clock; temporal questions are "
        "disadvantaged relative to real usage.",
    },
)


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def resolve_output_paths(
    output_dir: Path,
    checkpoint_path: Path | None = None,
    score_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    output_path = output_dir / RESULTS_FILENAME
    effective_checkpoint = checkpoint_path or output_dir / CHECKPOINT_FILENAME
    effective_score = score_path or output_dir / SCORE_FILENAME
    return output_path, effective_checkpoint, effective_score


def load_dataset(dataset_path: Path) -> list[dict[str, Any]]:
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"LongMemEval dataset not found: {dataset_path}. "
            "Pass --dataset with a valid JSON file path."
        )
    if not dataset_path.is_file():
        raise ValueError(f"LongMemEval dataset path is not a file: {dataset_path}")

    try:
        with dataset_path.open() as handle:
            dataset = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LongMemEval dataset is not valid JSON: {dataset_path}") from exc

    if not isinstance(dataset, list):
        raise ValueError(f"LongMemEval dataset must be a JSON list: {dataset_path}")

    return dataset


def read_json(path: Path) -> Any:
    with path.open() as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    """Atomic replace: an interrupted write never leaves a torn checkpoint."""
    atomic_write_json(path, payload)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


PROMPT_HASH_SENTINEL_MEMORY: dict[str, object] = {
    "content": "__LONGMEMEVAL_MEMORY__",
    "category": "fact",
}


async def build_assembled_system_prompt(
    memories: list[dict[str, Any]] | None = None,
) -> str:
    """The benchmark answer system prompt, built by the production renderer."""
    inputs = MemoryContextInputs(retrieved=list(memories or [PROMPT_HASH_SENTINEL_MEMORY]))
    return compose_system_prompt(render_memory_context(inputs))


def _resolve_without_loop(awaitable: Any) -> Any:
    """Drive a coroutine that never suspends, without needing (or nesting) an event loop."""
    try:
        awaitable.send(None)
    except StopIteration as stop:
        return stop.value
    awaitable.close()
    raise RuntimeError("The answer prompt builder must not suspend")


def _run_prompt_builder_for_hash() -> str:
    result = build_assembled_system_prompt([dict(PROMPT_HASH_SENTINEL_MEMORY)])
    if inspect.isawaitable(result):
        return str(_resolve_without_loop(result))
    return str(result)


def memory_formatter_identity() -> dict[str, Any]:
    """Identity of the active memory formatter, as recorded in run manifests.

    ``output_sha256`` hashes the assembled answer prompt for the pin's sentinel
    memory (the same value as the pin's ``answer.prompt_sha256``);
    ``source_sha256`` hashes the renderer source and its formatting helpers.
    """
    return {
        "module": injection.__name__,
        "function": render_memory_context.__qualname__,
        "source_sha256": source_sha256(
            injection.render_memory_context,
            injection._format_l0_block,
            injection._truncate_to_chars,
            injection._normalize_content,
            injection.estimate_tokens,
            injection.compose_system_prompt,
        ),
        "output_sha256": _sha256_text(_run_prompt_builder_for_hash()),
    }


async def prepare_service_runtime(settings: Any, pool: Any) -> dict[str, int]:
    """Process startup the worker performs before handling jobs; returns attestation counts.

    * Routing-log vocabulary, as the service launcher initialises it.
    * Auth pepper: validated up front (``MemoryStore`` needs it for keyed hashes)
      and the development DB-shared pepper loaded, as worker startup does.
    * ZDR route attestation. Without it every monitored route fails closed
      (``zdr_attestation_unknown``). One synchronous check runs first so admission
      is settled before the first model call instead of racing the loop
      ``attestation.start`` spawns; that loop then refreshes and re-checks exactly
      as in production.
    """
    try:
        routing_log.initialize_vocabulary()
    except Exception:
        logger.warning("Routing log vocabulary unavailable; labels fail closed")
    validate_pepper_config(settings)
    await initialize_development_pepper(settings, pool)
    counts = await attestation.check_once(pool)
    await attestation.start(pool)
    return counts


def approved_profile_routes(profile: str) -> list[str]:
    """Route ids currently admitted for a routing profile's shortlist."""
    policy = load_inference_policy()
    models = {candidate.model for candidate in model_routing.profile_candidates(profile) or ()}
    return sorted(
        route.route_id
        for route in policy.routes.values()
        if route.model in models and route.is_approved(policy.requirements)
    )


def build_longmemeval_pinned_config(settings: Any) -> dict[str, Any]:
    payload = json.loads(BENCHMARK_CONFIG_PIN_PATH.read_text())
    payload["shared"]["answer"]["prompt_sha256"] = _sha256_text(_run_prompt_builder_for_hash())
    payload["shared"]["query_embedding"]["embedding_query_model"] = settings.embedding_query_model
    payload["shared"]["query_embedding"]["embedding_dimensions"] = settings.embedding_dimensions
    return payload


def _collect_config_drift_warnings(
    current: dict[str, Any],
    pinned: dict[str, Any],
    prefix: str = "",
) -> list[str]:
    warnings: list[str] = []
    for key, current_value in current.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in pinned:
            warnings.append(f"{path} missing from pinned config")
            continue
        pinned_value = pinned[key]
        if isinstance(current_value, dict) and isinstance(pinned_value, dict):
            warnings.extend(_collect_config_drift_warnings(current_value, pinned_value, path))
        elif current_value != pinned_value:
            warnings.append(f"{path} drift: live={current_value!r} pinned={pinned_value!r}")
    return warnings


def _default_phase_state() -> dict[str, Any]:
    return {
        "status": "pending",
        "started_at": None,
        "updated_at": None,
        "completed_count": 0,
    }


def load_runner_checkpoint(
    checkpoint_path: Path,
    *,
    dataset_path: Path | None,
) -> dict[str, Any]:
    if not checkpoint_path.exists():
        return {
            "version": CHECKPOINT_VERSION,
            "dataset_path": str(dataset_path) if dataset_path is not None else None,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "phases": {
                "ingest": {**_default_phase_state(), "results": {}},
                "evaluate": {**_default_phase_state(), "results": {}},
                "score": {**_default_phase_state(), "accuracy": {}},
            },
        }

    payload = read_json(checkpoint_path)
    if not isinstance(payload, dict):
        raise ValueError(f"Checkpoint must be a JSON object: {checkpoint_path}")

    checkpoint_version = payload.get("version")
    if checkpoint_version != CHECKPOINT_VERSION:
        raise ValueError(
            "Checkpoint version mismatch: "
            f"{checkpoint_path} uses version {checkpoint_version!r}, but the production-parity "
            f"LongMemEval harness requires version {CHECKPOINT_VERSION}. Older checkpoints hold "
            "results from the legacy single-message answer prompt (or per-question sessions) "
            "and must not be resumed or mixed. Start a fresh output directory."
        )

    checkpoint_dataset = payload.get("dataset_path")
    if dataset_path is not None and checkpoint_dataset not in (None, str(dataset_path)):
        raise ValueError(
            "Checkpoint dataset mismatch: "
            f"{checkpoint_path} was created for {checkpoint_dataset}, not {dataset_path}"
        )

    payload.setdefault("created_at", utc_now_iso())
    payload["updated_at"] = utc_now_iso()
    payload["dataset_path"] = str(dataset_path) if dataset_path is not None else checkpoint_dataset

    phases = payload.setdefault("phases", {})
    ingest_phase = phases.setdefault("ingest", _default_phase_state())
    ingest_phase.setdefault("results", {})
    evaluate_phase = phases.setdefault("evaluate", _default_phase_state())
    evaluate_phase.setdefault("results", {})
    score_phase = phases.setdefault("score", _default_phase_state())
    score_phase.setdefault("accuracy", {})

    return payload


def save_runner_checkpoint(checkpoint_path: Path, checkpoint: dict[str, Any]) -> None:
    checkpoint["updated_at"] = utc_now_iso()
    write_json(checkpoint_path, checkpoint)


def mark_phase_started(checkpoint: dict[str, Any], phase: PhaseName) -> dict[str, Any]:
    phase_state = checkpoint["phases"][phase]
    if phase_state.get("started_at") is None:
        phase_state["started_at"] = utc_now_iso()
    phase_state["status"] = "running"
    phase_state["updated_at"] = utc_now_iso()
    return phase_state


def mark_phase_completed(
    checkpoint: dict[str, Any],
    phase: PhaseName,
    *,
    completed_count: int,
) -> None:
    phase_state = checkpoint["phases"][phase]
    phase_state["status"] = "completed"
    phase_state["completed_count"] = completed_count
    phase_state["updated_at"] = utc_now_iso()


def ordered_results(results_by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [results_by_key[key] for key in results_by_key]


def build_score_payload(results: list[dict[str, Any]]) -> dict[str, Any]:
    accuracy = score_accuracy(results)
    judgments = ("correct", "partially_correct", "incorrect")
    category_counts: dict[str, dict[str, int]] = {}
    for result in results:
        category = str(result.get("category", "IE-user"))
        counts = category_counts.setdefault(
            category, {"total": 0, "errors": 0, **{name: 0 for name in judgments}}
        )
        counts["total"] += 1
        judgment = str(result.get("judgment", "incorrect"))
        counts[judgment if judgment in judgments else "incorrect"] += 1
        counts["errors"] += int("error" in result)
    correct = sum(counts["correct"] for counts in category_counts.values())
    errored = [str(r.get("question_id")) for r in results if "error" in r]
    return {
        "substrate": BENCHMARK_SUBSTRATE,
        "benchmark_name": BENCHMARK_NAME,
        "generated_at": utc_now_iso(),
        "result_count": len(results),
        "accuracy": accuracy,
        "strict_accuracy": correct / len(results) if results else 0.0,
        "category_counts": dict(sorted(category_counts.items())),
        "error_count": len(errored),
        "errored_question_ids": errored,
        "dual_judgments": score_dual_judgments(results),
    }


def build_question_order(dataset: list[dict[str, Any]]) -> list[str]:
    return [normalize_question_id(entry, idx) for idx, entry in enumerate(dataset)]


def build_corpus_results_lookup(
    checkpoint: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return checkpoint["phases"]["ingest"]["results"]


def resolve_question_corpus_refs(
    corpus_plan: CorpusPlan,
    question_id: str,
) -> tuple[str, ...]:
    return corpus_plan.question_corpus_refs.get(question_id, ())


def resolve_question_conversation_ids(
    ingest_results: dict[str, dict[str, Any]],
    corpus_keys: tuple[str, ...],
) -> list[str]:
    conversation_ids: list[str] = []
    missing_corpus_keys: list[str] = []
    for corpus_key in corpus_keys:
        result = ingest_results.get(corpus_key)
        if result is None:
            missing_corpus_keys.append(corpus_key)
            continue
        conversation_id = result.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id:
            conversation_ids.append(conversation_id)
            continue
        missing_corpus_keys.append(corpus_key)

    if missing_corpus_keys:
        raise RuntimeError(
            "Missing ingested corpus sessions for benchmark question scope: "
            + ", ".join(missing_corpus_keys)
        )

    return conversation_ids


@dataclass(frozen=True, slots=True)
class QuestionSession:
    corpus_key: str
    session_id: str
    messages: list[dict[str, Any]]
    haystack_date: str | None


@dataclass(frozen=True, slots=True)
class QuestionStream:
    """One question's haystack: its own benchmark user and sessions in ingest order."""

    question_id: str
    user_id: uuid.UUID
    sessions: tuple[QuestionSession, ...]
    reordered: bool  # the dataset listed these sessions out of date order


class IngestSessionFailed(RuntimeError):
    """A session didn't complete; it stays pending so resume rolls it back and retries."""


def _parse_haystack_date(value: Any) -> datetime | None:
    """LongMemEval dates look like ``2023/05/20 (Sat) 02:21``."""
    if not isinstance(value, str):
        return None
    date_part, _, rest = value.partition(" (")
    time_part = rest.partition(") ")[2]
    try:
        return datetime.strptime(f"{date_part} {time_part}".strip(), "%Y/%m/%d %H:%M")
    except ValueError:
        return None


def build_question_streams(dataset: list[dict[str, Any]]) -> tuple[QuestionStream, ...]:
    """Per-question ingest plans, sessions stable-sorted by haystack date.

    A question whose dates don't all parse keeps the dataset order. A session
    repeated within one question is ingested once, at its earliest position.
    """
    streams: list[QuestionStream] = []
    for idx, entry in enumerate(dataset):
        question_id = normalize_question_id(entry, idx)
        raw_sessions = entry.get("haystack_sessions") or []
        raw_ids = entry.get("haystack_session_ids") or []
        raw_dates = entry.get("haystack_dates") or []
        sessions = [
            QuestionSession(
                corpus_key=build_corpus_key(messages),
                session_id=str(raw_ids[pos]) if pos < len(raw_ids) else f"{question_id}_{pos}",
                messages=messages,
                haystack_date=raw_dates[pos] if pos < len(raw_dates) else None,
            )
            for pos, messages in enumerate(raw_sessions)
            if isinstance(messages, list)
        ]
        dates = [_parse_haystack_date(session.haystack_date) for session in sessions]
        order = list(range(len(sessions)))
        if sessions and all(date is not None for date in dates):
            order.sort(key=lambda pos: (dates[pos], pos))
        ordered = [sessions[pos] for pos in order]
        seen: set[str] = set()
        unique = []
        for session in ordered:
            if session.corpus_key not in seen:
                seen.add(session.corpus_key)
                unique.append(session)
        streams.append(
            QuestionStream(
                question_id=question_id,
                user_id=question_user_id(question_id),
                sessions=tuple(unique),
                reordered=order != sorted(order),
            )
        )
    return tuple(streams)


def ingest_result_key(question_id: str, corpus_key: str) -> str:
    return f"{question_id}:{corpus_key}"


def _benchmark_users_record(streams: tuple[QuestionStream, ...]) -> dict[str, Any]:
    return {
        "per": "question",
        "namespace": str(BENCHMARK_USER_NAMESPACE),
        "count": len(streams),
        "sessions": sum(len(stream.sessions) for stream in streams),
        "session_order": "haystack_date",
        "reordered_questions": [stream.question_id for stream in streams if stream.reordered],
    }


@dataclass(slots=True)
class ResetSummary:
    success: bool
    tables_cleared: dict[str, int]
    total_rows_deleted: int
    checkpoint_reset: dict[str, Any] = field(default_factory=dict)
    redis_keys_deleted: int = 0
    redis_error: str | None = None
    error: str | None = None


CANONICAL_RESET_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("retrieval_log", "DELETE FROM retrieval_log WHERE user_id = $1"),
    ("dream_log", "DELETE FROM dream_log WHERE user_id = $1"),
    ("entities", "DELETE FROM entities WHERE user_id = $1"),
    ("memories", "DELETE FROM memories WHERE user_id = $1"),
    ("memory_extraction_log", "DELETE FROM memory_extraction_log WHERE user_id = $1"),
    ("messages", "DELETE FROM messages WHERE user_id = $1"),
    ("conversations", "DELETE FROM conversations WHERE user_id = $1"),
)
CANONICAL_RESET_TABLES: tuple[str, ...] = tuple(
    table for table, _statement in CANONICAL_RESET_STATEMENTS
)


def _reset_checkpoint_file(checkpoint_path: Path | str | None) -> dict[str, Any]:
    """Remove a benchmark checkpoint so the next ingest cannot skip reset DB state."""
    if checkpoint_path is None:
        return {
            "checkpoint_path": None,
            "checkpoint_existed": False,
            "checkpoint_removed": False,
        }

    path = Path(checkpoint_path)
    existed = path.exists()
    if existed:
        path.unlink()
    return {
        "checkpoint_path": str(path),
        "checkpoint_existed": existed,
        "checkpoint_removed": existed and not path.exists(),
    }


CANONICAL_TEST_USER_EMAIL = "longmemeval@daemon.test"

REDIS_EXTRACT_PATTERNS: tuple[str, ...] = (
    "extract:*",
    "arq:job:extract:*",
    "arq:result:extract:*",
    "arq:retry:extract:*",
)


def _scan_delete_redis_keys(client: Any, pattern: str) -> int:
    """Delete every Redis key matching ``pattern`` (cursor-scanned)."""
    count = 0
    cursor = 0
    while True:
        scan_result = client.scan(cursor=cursor, match=pattern, count=500)
        if isinstance(scan_result, tuple) and len(scan_result) == 2:
            cursor, keys = scan_result
            if keys:
                count += int(client.delete(*keys))
        else:
            break
        if cursor == 0:
            break
    return count


def _cleanup_redis_keys() -> dict[str, Any]:
    """Best-effort cleanup of ARQ extraction Redis keys.

    Returns a dict with ``keys_deleted`` (int) and ``error`` (str | None).
    Never raises — failures are reported in the dict so the caller can log
    or include them in a summary without breaking the overall reset.
    """
    try:
        import redis  # type: ignore[import-not-found]
    except Exception as exc:
        return {"keys_deleted": 0, "error": f"redis_unavailable: {exc}"}
    try:
        from orchestrator.config import get_settings

        settings = get_settings()
        redis_url = getattr(settings, "redis_url", None) or "redis://localhost:6379/0"
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        total = sum(_scan_delete_redis_keys(client, p) for p in REDIS_EXTRACT_PATTERNS)
        return {"keys_deleted": total, "error": None}
    except Exception as exc:
        return {"keys_deleted": 0, "error": str(exc)}


async def reset_canonical_benchmark(
    pool: asyncpg.Pool,
    checkpoint_path: Path | str | None = None,
    *,
    cleanup_redis: bool = False,
) -> ResetSummary:
    """Reset the fact-substrate canonical benchmark state for every benchmark user.

    That is the legacy shared test user plus every per-question user.

    Raises ``BenchmarkDatabaseGuardError`` unless the pool is connected to a
    marked benchmark database.

    When a checkpoint path is supplied, the checkpoint is deleted along with the
    database rows so the next ingest cannot resume past missing benchmark state.

    When ``cleanup_redis`` is True, ARQ extraction Redis keys matching
    ``REDIS_EXTRACT_PATTERNS`` are also deleted after the DB cleanup succeeds.
    Redis failures are reported via ``redis_keys_deleted`` and do not flip
    ``success`` to False.
    """
    # Destructive: refuse anything but a marked benchmark database, before any delete.
    await assert_benchmark_database(pool, get_settings().database_url)
    tables_cleared: dict[str, int] = {}
    total = 0
    checkpoint_reset: dict[str, Any] = {}
    redis_keys_deleted = 0
    try:
        checkpoint_reset = _reset_checkpoint_file(checkpoint_path)
        question_users = await pool.fetch(
            "SELECT id FROM users WHERE email LIKE 'longmemeval+%@daemon.test'"
        )
        user_ids = [TEST_USER_ID, *(row["id"] for row in question_users or [])]

        for table, statement in CANONICAL_RESET_STATEMENTS:
            deleted = 0
            for user_id in user_ids:
                result = await pool.execute(statement, user_id)
                deleted += int(str(result).split()[-1]) if isinstance(result, str) else 0
            tables_cleared[table] = deleted
            total += deleted
    except Exception as exc:
        return ResetSummary(
            success=False,
            tables_cleared=tables_cleared,
            total_rows_deleted=total,
            checkpoint_reset=checkpoint_reset,
            error=str(exc),
        )

    redis_error_value: str | None = None
    if cleanup_redis:
        redis_result = _cleanup_redis_keys()
        redis_keys_deleted = int(redis_result.get("keys_deleted", 0))
        redis_error_value = redis_result.get("error")

    return ResetSummary(
        success=True,
        tables_cleared=tables_cleared,
        total_rows_deleted=total,
        checkpoint_reset=checkpoint_reset,
        redis_keys_deleted=redis_keys_deleted,
        redis_error=redis_error_value,
    )


def is_completed_result(result: object) -> bool:
    """Base/generic observation is complete without a top-level harness error.

    A failed/missing additive official metric never permits automatic replay of
    the saved reader/generic calls; its coverage is reported separately.
    """
    return (
        isinstance(result, dict)
        and "error" not in result
        and isinstance(result.get("judgment"), str)
    )


EXTRACTION_PROFILE = "background"
EVALUATE_OPERATION = "chat"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _policy_source(loader_name: str) -> dict[str, Any]:
    try:
        if loader_name == "inference_policy":
            from orchestrator.entitlements.policy import load_inference_policy

            loaded: Any = load_inference_policy()
        else:
            from orchestrator.model_routing import load_model_routing

            loaded = load_model_routing()
    except Exception as exc:
        return {"path": None, "sha256": None, "error": type(exc).__name__}
    path_value = getattr(loaded, "source_path", None)
    path = Path(path_value) if path_value else None
    if path is None:
        settings = get_settings()
        configured = (
            settings.daemon_inference_policy
            if loader_name == "inference_policy"
            else settings.daemon_model_routing
        )
        default = (
            "config/inference_policy.json"
            if loader_name == "inference_policy"
            else "config/model_routing.json"
        )
        path = Path(configured or default)
    return {"path": str(path), "sha256": optional_file_sha256(path)}


def _embedding_route_description() -> dict[str, Any]:
    from orchestrator.memory.embedding import (
        describe_embedding_configuration,
        get_selected_embedding_route_id,
    )

    route_id = get_selected_embedding_route_id() or None
    configuration, reasons = describe_embedding_configuration()
    return {
        "route_id": route_id,
        "mode": "qualified_route" if route_id else "lexical_fallback",
        "configuration": configuration,
        "reason_codes": list(reasons),
    }


# Settings fields never hashed into the effective config: credentials, peppers
# and DSNs (the database is pinned by fingerprints instead).
_CONFIG_EXCLUDED_MARKERS: tuple[str, ...] = (
    "key",
    "secret",
    "token",
    "password",
    "pepper",
    "database_url",
    "dsn",
)


def _short_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def effective_config(harness: dict[str, Any]) -> dict[str, Any]:
    """Everything outside the code that decides what a phase computes.

    Non-secret settings are recorded as per-field hashes, so a refusal can name
    what changed (``EMBEDDING_ROUTE_ID``, dedup thresholds, …) without
    publishing values; policy/routing/pin files by content hash.
    """
    settings = get_settings()
    dumped = settings.model_dump(mode="json") if hasattr(settings, "model_dump") else vars(settings)
    fields = {
        name: _short_hash(value)
        for name, value in sorted(dumped.items())
        if not any(marker in name.lower() for marker in _CONFIG_EXCLUDED_MARKERS)
    }
    files = {
        "inference_policy": _policy_source("inference_policy").get("sha256"),
        "model_routing": _policy_source("model_routing").get("sha256"),
        "config_pin": optional_file_sha256(BENCHMARK_CONFIG_PIN_PATH),
    }
    body = {"settings": fields, "files": files, "harness": harness}
    return {"sha256": _short_hash(body), **body}


def config_changes(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    for section in ("settings", "files", "harness"):
        before = recorded.get(section) or {}
        after = current.get(section) or {}
        for name in sorted(set(before) | set(after)):
            if before.get(name) != after.get(name):
                changes.append(f"{section}.{name}")
    return changes


def _seconds_between(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def _source_qualification(manifest_path: Path, source_run_id: Any) -> dict[str, Any]:
    """Qualification of a --from-ingest source run; ``None`` when it can't be verified."""
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError):
        manifest = None
    if manifest is None:
        return {"baseline_qualified": None, "disqualifications": ["source_manifest_missing"]}
    if source_run_id is None or manifest.get("run_id") != source_run_id:
        return {"baseline_qualified": None, "disqualifications": ["source_manifest_other_run"]}
    return {
        "baseline_qualified": manifest.get("baseline_qualified") is True,
        "disqualifications": list(manifest.get("disqualifications") or []),
        "git": manifest.get("git"),
    }


def _tally(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _sum_usage(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        usage = row.get(key)
        if not isinstance(usage, dict):
            continue
        for name, value in usage.items():
            if isinstance(value, int):
                totals[name] = totals.get(name, 0) + value
    return totals


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    from orchestrator.eval.durable_io import atomic_write_text

    atomic_write_text(path, "".join(json.dumps(row) + "\n" for row in rows))


@dataclass(slots=True)
class LongMemEvalFactRunner:
    dataset_path: Path
    output_path: Path
    checkpoint_path: Path
    score_path: Path
    limit: int | None = None
    force_retrieval_logging: bool = True
    benchmark_mode: bool = True
    from_ingest: Path | None = None
    ingest_snapshot: Path | None = None
    retry_failed_sessions: bool = False
    allow_code_change: bool = False
    allow_state_mismatch: bool = False
    # Question streams ingested side by side. Never changes results: no user ever
    # has two sessions in flight. Not part of the effective config.
    concurrency: int = 1
    stop: StopController | None = None
    manifest_path: Path | None = None

    # ----------------------------------------------------------------- setup
    def load_dataset(self) -> list[dict[str, Any]]:
        dataset = load_dataset(self.dataset_path)
        return dataset if self.limit is None else dataset[: self.limit]

    def build_corpus_plan(self) -> CorpusPlan:
        return build_corpus_plan(self.load_dataset())

    def resolved_manifest_path(self) -> Path:
        return self.manifest_path or self.output_path.parent / MANIFEST_FILENAME

    def load_checkpoint(self) -> dict[str, Any]:
        checkpoint = load_runner_checkpoint(self.checkpoint_path, dataset_path=self.dataset_path)
        dataset_sha256 = file_sha256(self.dataset_path) if self.dataset_path.is_file() else None
        recorded_sha256 = checkpoint.get("dataset_sha256")
        if recorded_sha256 and dataset_sha256 and recorded_sha256 != dataset_sha256:
            raise ValueError(
                f"Checkpoint dataset content mismatch: {self.checkpoint_path} was created for a "
                "dataset with a different sha256"
            )
        checkpoint["dataset_sha256"] = dataset_sha256 or recorded_sha256
        # Binds the manifest and every result row to this checkpoint; rows or a
        # manifest from another run (or a reset) are refused, never adopted.
        checkpoint.setdefault("run_id", uuid.uuid4().hex)
        settings = get_settings()
        pinned_authority = json.loads(BENCHMARK_CONFIG_PIN_PATH.read_text())
        live_authority = build_longmemeval_pinned_config(settings=settings)
        drift_warnings = _collect_config_drift_warnings(live_authority, pinned_authority)
        checkpoint["benchmark_effective_config"] = {
            "lane": "canonical",
            "pin_path": str(BENCHMARK_CONFIG_PIN_PATH),
            "runtime": {
                "dataset_path": str(self.dataset_path),
                "limit": self.limit,
                "output_path": str(self.output_path),
                "checkpoint_path": str(self.checkpoint_path),
                "score_path": str(self.score_path),
                "force_retrieval_logging": self.force_retrieval_logging,
                "benchmark_mode": self.benchmark_mode,
            },
            "pinned_authority": pinned_authority,
        }
        checkpoint["benchmark_config_drift_warnings"] = drift_warnings
        if drift_warnings:
            logger.warning(
                "LongMemEval benchmark config drift detected: %s",
                "; ".join(drift_warnings),
            )
        return checkpoint

    def _seed_from_ingest(self, checkpoint: dict[str, Any]) -> None:
        """Copy a completed ingest's corpus map into a fresh evaluate-only run."""
        if self.from_ingest is None:
            return
        source = load_runner_checkpoint(self.from_ingest, dataset_path=None)
        source_ingest = source["phases"]["ingest"]
        if source_ingest.get("status") != "completed":
            raise ValueError(
                f"--from-ingest checkpoint has no completed ingest: {self.from_ingest}"
            )
        if source.get("dataset_sha256") != checkpoint.get("dataset_sha256"):
            raise ValueError("--from-ingest checkpoint was built from a different dataset")
        ingest_copy = json.loads(json.dumps(source_ingest))
        current = checkpoint["phases"]["ingest"]
        if current.get("results") and current.get("results") != ingest_copy.get("results"):
            raise ValueError(
                f"{self.checkpoint_path} already holds a different ingest than {self.from_ingest}"
            )
        checkpoint["phases"]["ingest"] = ingest_copy
        source_manifest = self.from_ingest.parent / MANIFEST_FILENAME
        checkpoint["ingest_source"] = {
            "checkpoint_path": str(self.from_ingest),
            "checkpoint_sha256": file_sha256(self.from_ingest),
            "manifest_path": str(source_manifest) if source_manifest.is_file() else None,
            "manifest_sha256": optional_file_sha256(source_manifest),
            "state_fingerprint": ingest_copy.get("state_fingerprint"),
            **_source_qualification(source_manifest, source.get("run_id")),
        }

    @staticmethod
    def _preflight_database_guard() -> None:
        """Refuse a non-benchmark DSN before any file or database is written."""
        check_benchmark_dsn(get_settings().database_url)

    def _check_stop(self) -> None:
        if self.stop is not None:
            self.stop.raise_if_requested()

    # -------------------------------------------------------------- manifest
    def _new_manifest(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        answer_model = BENCHMARK_ANSWER_MODEL if self.benchmark_mode else "openrouter/openai/gpt-4o"
        judge_model = BENCHMARK_JUDGE_MODEL if self.benchmark_mode else "openrouter/openai/gpt-4o"
        return {
            "created_at": utc_now_iso(),
            "run_id": checkpoint["run_id"],
            "baseline_qualified": True,
            "disqualifications": [],
            "effective_config": effective_config(self._harness_config()),
            "benchmark_name": BENCHMARK_NAME,
            "substrate": BENCHMARK_SUBSTRATE,
            "git": git_state(_REPO_ROOT),
            "config_pin": {
                "path": str(BENCHMARK_CONFIG_PIN_PATH),
                "sha256": optional_file_sha256(BENCHMARK_CONFIG_PIN_PATH),
            },
            "memory_formatter": memory_formatter_identity(),
            "dataset": {
                "path": str(self.dataset_path),
                "sha256": checkpoint.get("dataset_sha256"),
                "limit": self.limit,
            },
            "routes": {
                "answer": {
                    "model": answer_model,
                    "benchmark_mode": self.benchmark_mode,
                    "endpoint_slug": BENCHMARK_ANSWER_ENDPOINT_SLUG
                    if self.benchmark_mode
                    else None,
                    "temperature": 0.0 if self.benchmark_mode else 0.7,
                    "seed": BENCHMARK_SEED if self.benchmark_mode else None,
                    "max_tokens": ANSWER_MAX_TOKENS,
                },
                "judge": {
                    "model": judge_model,
                    "benchmark_mode": self.benchmark_mode,
                    "temperature": 0.0,
                    "seed": BENCHMARK_SEED if self.benchmark_mode else None,
                    "max_tokens": JUDGE_MAX_TOKENS,
                },
                "official_judge": {
                    "model": judge_model,
                    "benchmark_mode": self.benchmark_mode,
                    "temperature": 0.0,
                    "seed": BENCHMARK_SEED if self.benchmark_mode else None,
                    "max_tokens": OFFICIAL_MAX_TOKENS,
                    "endpoint_slug": BENCHMARK_ANSWER_ENDPOINT_SLUG
                    if self.benchmark_mode
                    else None,
                    "prompt_source_commit": OFFICIAL_SOURCE_COMMIT,
                    "upstream_deviations": [
                        "Azure via OpenRouter instead of direct OpenAI",
                        "seed 42",
                    ]
                    if self.benchmark_mode
                    else ["Unpinned harness route"],
                },
                "extraction": {
                    "profile": EXTRACTION_PROFILE,
                    "pinned_model": None,
                    "chunking": "orchestrator.worker.jobs._chunk_messages_for_extraction",
                    "observed_models": None,
                },
                "inference_policy": _policy_source("inference_policy"),
                "model_routing": _policy_source("model_routing"),
            },
            "embedding": _embedding_route_description(),
            "ingest_source": checkpoint.get("ingest_source"),
            "ingest_snapshot": None,
            "parity_exceptions": [dict(item) for item in PARITY_EXCEPTIONS],
            "invocations": [],
            "phases": {},
        }

    def _harness_config(self) -> dict[str, Any]:
        return {
            "benchmark_mode": self.benchmark_mode,
            "force_retrieval_logging": self.force_retrieval_logging,
            "limit": self.limit,
            "extraction_profile": EXTRACTION_PROFILE,
            "evaluate_operation": EVALUATE_OPERATION,
        }

    @staticmethod
    def _disqualify(manifest: dict[str, Any], reason: str) -> None:
        reasons = manifest.setdefault("disqualifications", [])
        if reason not in reasons:
            reasons.append(reason)
        manifest["baseline_qualified"] = False

    def _open_manifest(self, phase: PhaseName, checkpoint: dict[str, Any]) -> dict[str, Any]:
        path = self.resolved_manifest_path()
        manifest = load_manifest(path)
        now = utc_now_iso()
        if manifest is None:
            manifest = self._new_manifest(checkpoint)
        else:
            if manifest.get("run_id") != checkpoint.get("run_id"):
                raise ManifestPinError(
                    f"{path} belongs to a different run than {self.checkpoint_path} "
                    "(for example after a reset). Use a fresh --output-dir."
                )
            changed = config_changes(
                manifest.get("effective_config") or {},
                effective_config(self._harness_config()),
            )
            if changed:
                raise ManifestPinError(
                    "Refusing to resume with a different configuration: "
                    f"{', '.join(changed)} changed since this run started. "
                    "Start a fresh --output-dir for a different configuration."
                )
            changes = check_code_pin(
                manifest, git_state(_REPO_ROOT), allow_change=self.allow_code_change
            )
            if changes:
                manifest.setdefault("code_pin_changes", []).append({"at": now, "changes": changes})
                self._disqualify(manifest, "code_changed_mid_run")
            formatter = memory_formatter_identity()
            if formatter != manifest.get("memory_formatter"):
                manifest.setdefault("formatter_changes", []).append(
                    {"at": now, "formatter": formatter}
                )
            if checkpoint.get("ingest_source") and not manifest.get("ingest_source"):
                manifest["ingest_source"] = checkpoint["ingest_source"]
        if self.ingest_snapshot is not None:
            manifest["ingest_snapshot"] = {
                "path": str(self.ingest_snapshot),
                "sha256": optional_file_sha256(self.ingest_snapshot),
                "recorded_at": now,
            }
        if self.allow_state_mismatch:
            self._disqualify(manifest, "allow_state_mismatch")
        ingest_source = checkpoint.get("ingest_source")
        if ingest_source is not None and ingest_source.get("baseline_qualified") is not True:
            # An unqualified or unverifiable corpus stays unqualified downstream.
            self._disqualify(manifest, "source_ingest_not_qualified")
        invocations = manifest.setdefault("invocations", [])
        for previous in invocations:
            if previous.get("outcome") == "running":
                # Killed without an exit record (SIGKILL, power loss): close it
                # at its last recorded progress.
                last = previous.get("last_progress_at") or previous.get("started_at")
                previous["outcome"] = "killed"
                previous["ended_at"] = last
                previous["active_seconds"] = round(
                    _seconds_between(previous.get("started_at"), last), 1
                )
        # The phase is already marked started by now, so only an earlier
        # invocation of it says this one resumes.
        resumed = any(previous.get("phase") == phase for previous in invocations)
        invocations.append(
            {
                "phase": phase,
                "started_at": now,
                "resumed": resumed,
                "outcome": "running",
                "concurrency": self.concurrency,
            }
        )
        save_manifest(path, manifest)
        return manifest

    @staticmethod
    def _phase_active_seconds(manifest: dict[str, Any], phase: str) -> float:
        """Active time across every invocation of a phase, including the open one."""
        total = 0.0
        for invocation in manifest.get("invocations", []):
            if invocation.get("phase") != phase:
                continue
            if invocation.get("outcome") == "running":
                total += _seconds_between(invocation.get("started_at"), utc_now_iso())
            else:
                total += float(invocation.get("active_seconds") or 0.0)
        return round(total, 1)

    def _heartbeat(self, manifest: dict[str, Any]) -> None:
        manifest["invocations"][-1]["last_progress_at"] = utc_now_iso()
        save_manifest(self.resolved_manifest_path(), manifest)

    def _close_manifest(
        self, manifest: dict[str, Any], outcome: str, active_seconds: float
    ) -> None:
        invocation = manifest["invocations"][-1]
        invocation["ended_at"] = utc_now_iso()
        invocation["outcome"] = outcome
        invocation["active_seconds"] = round(active_seconds, 1)
        save_manifest(self.resolved_manifest_path(), manifest)

    # ----------------------------------------------------------------- phase
    @asynccontextmanager
    async def _phase(
        self, phase: PhaseName, checkpoint: dict[str, Any]
    ) -> AsyncIterator[tuple[Any, MemoryStore, dict[str, Any]]]:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL not configured")
        if not settings.daemon_encryption_key:
            raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")
        check_benchmark_dsn(settings.database_url)
        manifest = self._open_manifest(phase, checkpoint)
        phase_state = checkpoint["phases"][phase]
        started = time.monotonic()
        outcome = "failed"
        pool: Any = None
        attestation_started = False
        try:
            pool = await asyncpg.create_pool(
                dsn=settings.database_url, min_size=2, max_size=max(10, 4 * self.concurrency + 2)
            )
            await assert_benchmark_database(pool, settings.database_url)
            attestation_started = True
            attestation_counts = await prepare_service_runtime(settings, pool)
            manifest.setdefault("route_attestations", []).append(
                {"at": utc_now_iso(), "phase": phase, **attestation_counts}
            )
            # Route approval rests on the attestations just recorded, so the
            # description taken when the manifest was created reads "unapproved".
            manifest["embedding"] = _embedding_route_description()
            encryption = ContentEncryption(settings.daemon_encryption_key)
            store = MemoryStore(db_pool=pool, encryption=encryption)
            yield pool, store, manifest
            outcome = "completed"
        except asyncio.CancelledError:
            outcome = "abandoned"
            raise
        except Exception as exc:
            signal_name = getattr(exc, "signal_name", None)
            outcome = (
                f"interrupted:{signal_name}" if signal_name else f"failed:{type(exc).__name__}"
            )
            raise
        finally:
            elapsed = time.monotonic() - started
            phase_state["active_seconds"] = round(
                float(phase_state.get("active_seconds") or 0.0) + elapsed, 1
            )
            if outcome != "completed":
                phase_state["status"] = "failed" if outcome.startswith("failed") else "interrupted"
                phase_state["updated_at"] = utc_now_iso()
            try:
                save_runner_checkpoint(self.checkpoint_path, checkpoint)
                self._close_manifest(manifest, outcome, elapsed)
            finally:
                if attestation_started:
                    await attestation.stop()
                if pool is not None:
                    await pool.close()

    # ---------------------------------------------------------------- ingest
    async def _reconcile_interrupted_session(
        self,
        pool: Any,
        stream: QuestionStream,
        pending_sessions: dict[str, dict[str, Any]],
        ingest_results: dict[str, dict[str, Any]],
        manifest: dict[str, Any],
    ) -> None:
        """Undo a question's interrupted session exactly.

        Before each session the checkpoint records it in ``pending_sessions``
        with a fingerprint of that question's user. Deleting the interrupted
        session's own rows must restore that fingerprint. If extraction had
        already merged into or superseded an earlier session's memory, it
        cannot; the question is then rebuilt from its first session instead
        (see ``_rebuild_question``). No other stream ever writes to this user.
        """
        referenced = {
            str(result["conversation_id"])
            for result in ingest_results.values()
            if result.get("question_id") == stream.question_id and result.get("conversation_id")
        }
        present = await list_benchmark_conversation_ids(pool, stream.user_id)
        orphans = present - referenced
        pending = pending_sessions.get(stream.question_id)
        if pending is None:
            if orphans:
                raise RuntimeError(
                    f"Benchmark user for {stream.question_id} holds {len(orphans)} "
                    "conversation(s) this checkpoint does not reference and no interrupted "
                    "session is recorded. It was not built by this checkpoint: restore the "
                    "matching snapshot or reset."
                )
            return
        if len(orphans) > 1:
            raise RuntimeError(
                f"Benchmark user for {stream.question_id} holds {len(orphans)} unreferenced "
                "conversations; at most one (the interrupted session) is expected. Restore "
                "the matching snapshot."
            )
        counts = await rollback_orphan_conversations(pool, stream.user_id, orphans)
        after = await fingerprint_benchmark_state(pool, stream.user_id, tables=CROSS_SESSION_TABLES)
        exact = after["sha256"] == pending["state_fingerprint"]["sha256"]
        after_failure = "failure" in pending
        manifest.setdefault("ingest_rollbacks", []).append(
            {
                "at": utc_now_iso(),
                "question_id": stream.question_id,
                "corpus_key": pending.get("corpus_key"),
                "after_failure": after_failure,
                "orphan_conversations": len(orphans),
                **counts,
                "restored_pre_session_state": exact,
            }
        )
        save_manifest(self.resolved_manifest_path(), manifest)
        if not exact:
            await self._rebuild_question(
                pool, stream, pending_sessions, ingest_results, manifest, pending
            )
            return
        logger.warning(
            "[ingest] Rolled back interrupted session %s of %s exactly: %s",
            pending.get("corpus_key"),
            stream.question_id,
            counts,
        )
        pending_sessions.pop(stream.question_id, None)

    async def _rebuild_question(
        self,
        pool: Any,
        stream: QuestionStream,
        pending_sessions: dict[str, dict[str, Any]],
        ingest_results: dict[str, dict[str, Any]],
        manifest: dict[str, Any],
        pending: dict[str, Any],
    ) -> None:
        """Wipe one question's user and re-ingest the question from its first session.

        The interrupted session's partial facts changed earlier memories of this
        user, so its rollback was inexact. The user's memory is built only from
        its own question's sessions, so an empty user is exactly its state before
        the first session: rebuilding keeps the run a baseline, and costs only
        this question's sessions. The database is wiped before the checkpoint
        forgets the question, so a crash in between just rebuilds again.
        """
        deleted = await wipe_benchmark_user(pool, stream.user_id, CANONICAL_RESET_STATEMENTS)
        remaining = await fingerprint_benchmark_state(pool, stream.user_id)
        leftover = {
            table: digest["rows"] for table, digest in remaining["tables"].items() if digest["rows"]
        }
        if leftover:
            raise RuntimeError(
                f"Rebuilding {stream.question_id} left rows behind ({leftover}); refusing to "
                "continue. Restore the matching snapshot or start a fresh ingest."
            )
        discarded = [
            key
            for key, result in ingest_results.items()
            if result.get("question_id") == stream.question_id
        ]
        for key in discarded:
            ingest_results.pop(key)
        pending_sessions.pop(stream.question_id, None)
        manifest.setdefault("question_rebuilds", []).append(
            {
                "at": utc_now_iso(),
                "question_id": stream.question_id,
                "trigger_corpus_key": pending.get("corpus_key"),
                "after_failure": "failure" in pending,
                "sessions_discarded": len(discarded),
                "deleted": deleted,
            }
        )
        save_manifest(self.resolved_manifest_path(), manifest)
        logger.warning(
            "[ingest] Rollback of %s was inexact; rebuilding %s from its first session "
            "(%s checkpointed sessions discarded)",
            pending.get("corpus_key"),
            stream.question_id,
            len(discarded),
        )

    async def _retry_failed_sessions(
        self,
        pool: Any,
        checkpoint: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        results: dict[str, dict[str, Any]] = checkpoint["phases"]["ingest"]["results"]
        failed = [key for key, result in results.items() if result.get("status") != "complete"]
        if not failed:
            return
        # A failed session may have written part of its facts (and merged into
        # earlier ones) before failing; deleting it is not an exact rollback, so
        # the run stops being a baseline.
        counts = {"conversations": 0, "memories": 0, "memory_extraction_log": 0}
        for key in failed:
            result = results.pop(key)
            if result.get("conversation_id") and result.get("user_id"):
                removed = await rollback_orphan_conversations(
                    pool, uuid.UUID(result["user_id"]), {str(result["conversation_id"])}
                )
                for name, value in removed.items():
                    counts[name] = counts.get(name, 0) + value
        manifest.setdefault("ingest_retries", []).append(
            {"at": utc_now_iso(), "sessions": len(failed), **counts}
        )
        self._disqualify(manifest, "retried_failed_sessions")
        save_runner_checkpoint(self.checkpoint_path, checkpoint)
        save_manifest(self.resolved_manifest_path(), manifest)

    async def _ingest_stream(
        self,
        pool: Any,
        store: MemoryStore,
        stream: QuestionStream,
        checkpoint: dict[str, Any],
        manifest: dict[str, Any],
        halted: Callable[[], bool],
    ) -> None:
        """Ingest one question's remaining sessions in order, one at a time.

        A session that doesn't complete is treated like an interruption: it is
        not recorded as a result, its pending entry (and pre-session
        fingerprint) stays, and the stream raises so the run stops after the
        other streams finish their in-flight session. Resume rolls it back
        exactly and re-ingests it, or rebuilds the question if the failed
        session's partial facts had already changed other memories.
        """
        ingest_phase = checkpoint["phases"]["ingest"]
        results: dict[str, dict[str, Any]] = ingest_phase["results"]
        pending: dict[str, dict[str, Any]] = ingest_phase["pending_sessions"]

        def extraction_scope() -> Any:
            return account_compute(
                pool,
                stream.user_id,
                operation="agent",
                auto_route=True,
                background=True,
                profile=EXTRACTION_PROFILE,
            )

        for position, session in enumerate(stream.sessions):
            key = ingest_result_key(stream.question_id, session.corpus_key)
            if key in results:
                continue
            if halted():
                return  # another stream stopped the run; this one stays resumable
            self._check_stop()
            logger.info(
                "[ingest] %s [%s/%s] %s ingesting (%s/%s sessions overall)",
                stream.question_id,
                position + 1,
                len(stream.sessions),
                session.session_id,
                len(results) + 1,
                ingest_phase.get("total_sessions"),
            )
            pending[stream.question_id] = {
                "corpus_key": session.corpus_key,
                "started_at": utc_now_iso(),
                "state_fingerprint": await fingerprint_benchmark_state(
                    pool, stream.user_id, tables=CROSS_SESSION_TABLES
                ),
            }
            save_runner_checkpoint(self.checkpoint_path, checkpoint)
            created: list[uuid.UUID] = []

            async def remember(conversation_id: uuid.UUID) -> None:
                created.append(conversation_id)

            try:
                result: dict[str, Any] = await ingest_session_parity(
                    store=store,
                    user_id=stream.user_id,
                    session_id=session.session_id,
                    messages=session.messages,
                    session_index=position,
                    scope_factory=extraction_scope,
                    on_conversation_created=remember,
                )
            except Exception as exc:
                logger.exception(
                    "[ingest] Session %s of %s failed", session.session_id, stream.question_id
                )
                result = {
                    "session_id": session.session_id,
                    "status": "error",
                    "error": type(exc).__name__,
                }
                if created:
                    result["conversation_id"] = str(created[0])

            if result.get("status") != "complete":
                # Keep only types and counts: errors can carry conversation text.
                failure = {
                    "at": utc_now_iso(),
                    "session_id": session.session_id,
                    "status": result.get("status"),
                    "error": result.get("error"),
                    "chunks_failed": result.get("chunks_failed"),
                    "chunk_errors": result.get("chunk_errors"),
                }
                pending[stream.question_id]["failure"] = failure
                manifest.setdefault("ingest_failures", []).append(
                    {"question_id": stream.question_id, "corpus_key": session.corpus_key, **failure}
                )
                save_runner_checkpoint(self.checkpoint_path, checkpoint)
                save_manifest(self.resolved_manifest_path(), manifest)
                raise IngestSessionFailed(
                    f"Session {session.session_id} of {stream.question_id} did not complete "
                    f"({result.get('status')}); stopping. Resume to roll it back and retry it."
                )

            result["question_id"] = stream.question_id
            result["user_id"] = str(stream.user_id)
            result["corpus_key"] = session.corpus_key
            result["haystack_date"] = session.haystack_date
            result["ingested_at"] = utc_now_iso()
            results[key] = result
            pending.pop(stream.question_id, None)
            ingest_phase["completed_count"] = len(results)
            ingest_phase["updated_at"] = utc_now_iso()
            save_runner_checkpoint(self.checkpoint_path, checkpoint)
            self._heartbeat(manifest)

    async def ingest(self) -> list[dict[str, Any]]:
        if self.from_ingest is not None:
            raise ValueError("--from-ingest runs are evaluate-only; run ingest without it")
        self._preflight_database_guard()
        dataset = self.load_dataset()
        streams = build_question_streams(dataset)
        user_ids = [stream.user_id for stream in streams]
        checkpoint = self.load_checkpoint()
        ingest_phase = mark_phase_started(checkpoint, "ingest")
        ingest_phase["total_sessions"] = sum(len(stream.sessions) for stream in streams)
        pending_sessions = ingest_phase.setdefault("pending_sessions", {})
        save_runner_checkpoint(self.checkpoint_path, checkpoint)

        async with self._phase("ingest", checkpoint) as (pool, store, manifest):
            manifest["benchmark_users"] = _benchmark_users_record(streams)
            for stream in streams:
                await ensure_benchmark_user(pool, stream.user_id)
            results: dict[str, dict[str, Any]] = ingest_phase["results"]
            # Settle interrupted sessions before anything else touches the DB.
            for stream in streams:
                await self._reconcile_interrupted_session(
                    pool, stream, pending_sessions, results, manifest
                )
            save_runner_checkpoint(self.checkpoint_path, checkpoint)
            if self.retry_failed_sessions:
                await self._retry_failed_sessions(pool, checkpoint, manifest)

            logger.info(
                "[ingest] Starting LongMemEval ingestion: %s questions, %s sessions "
                "(%s already checkpointed), concurrency %s",
                len(streams),
                ingest_phase["total_sessions"],
                len(results),
                self.concurrency,
            )

            # The production chunker lives in orchestrator.worker, whose package
            # import constructs arq's Worker; arq installs its own SIGINT/SIGTERM
            # handlers on the running loop. Import it now and take them back.
            importlib.import_module("orchestrator.worker.jobs")
            if self.stop is not None:
                self.stop.reassert_handlers()

            approved = approved_profile_routes(EXTRACTION_PROFILE)
            manifest["routes"]["extraction"]["approved_route_ids"] = approved
            if not approved:
                raise RuntimeError(
                    f"No approved inference route for the {EXTRACTION_PROFILE!r} profile "
                    "(check route attestation in the run manifest); refusing to ingest"
                )

            todo = deque(
                stream
                for stream in streams
                if any(
                    ingest_result_key(stream.question_id, session.corpus_key) not in results
                    for session in stream.sessions
                )
            )
            errors: list[Exception] = []

            async def worker() -> None:
                while todo and not errors:
                    stream = todo.popleft()
                    try:
                        await self._ingest_stream(
                            pool,
                            store,
                            stream,
                            checkpoint,
                            manifest,
                            lambda: bool(errors),
                        )
                    except Exception as exc:
                        # Siblings finish their in-flight session, then stop.
                        errors.append(exc)

            await asyncio.gather(
                *(worker() for _ in range(max(1, min(self.concurrency, len(todo)))))
            )
            if errors:
                raise errors[0]

            mark_phase_completed(checkpoint, "ingest", completed_count=len(results))
            fingerprint = await fingerprint_benchmark_users(pool, user_ids)
            ingest_phase["state_fingerprint"] = fingerprint
            routes = await summarize_ingest_routes(pool, user_ids)
            spend = await summarize_compute_spend(pool, user_ids, ingest_phase.get("started_at"))
            manifest["routes"]["extraction"]["observed_models"] = routes["extraction_models"]
            manifest["phases"]["ingest"] = {
                "completed_at": utc_now_iso(),
                "active_seconds": self._phase_active_seconds(manifest, "ingest"),
                "questions": len(streams),
                "sessions": len(results),
                "status_counts": _tally([str(r.get("status")) for r in results.values()]),
                "state_fingerprint": fingerprint,
                "memory_embeddings": routes["memory_embeddings"],
                "compute_spend": spend,
            }
        logger.info(
            "[ingest] Complete: %s sessions recorded in checkpoint %s",
            checkpoint["phases"]["ingest"]["completed_count"],
            self.checkpoint_path,
        )
        return ordered_results(checkpoint["phases"]["ingest"]["results"])

    # -------------------------------------------------------------- evaluate
    def _reconcile_results(
        self,
        checkpoint: dict[str, Any],
        question_order: list[str],
        manifest: dict[str, Any],
    ) -> None:
        """Make the results file and checkpoint agree before resuming.

        A question appended to the results file but killed before the checkpoint
        save is adopted, never re-run; duplicate lines collapse to one.
        """
        results: dict[str, dict[str, Any]] = checkpoint["phases"]["evaluate"]["results"]
        file_rows = read_jsonl_records(self.output_path)
        foreign = [row for row in file_rows if row.get("run_id") != checkpoint["run_id"]]
        if foreign:
            raise RuntimeError(
                f"{self.output_path} holds {len(foreign)} result row(s) from another run (or a "
                "legacy harness). They are never adopted: move the file away or use a fresh "
                "--output-dir."
            )
        from_file: dict[str, dict[str, Any]] = {}
        duplicates = 0
        for row in file_rows:
            question_id = row.get("question_id")
            if not isinstance(question_id, str):
                continue
            if question_id in from_file:
                duplicates += 1
                if is_completed_result(from_file[question_id]):
                    continue
            from_file[question_id] = row
        adopted = 0
        for question_id, row in from_file.items():
            current = results.get(question_id)
            if is_completed_result(current):
                continue
            if is_completed_result(row) or current is None:
                results[question_id] = row
                adopted += int(is_completed_result(row))
        ordered = [results[qid] for qid in question_order if qid in results]
        atomic_write_jsonl(self.output_path, ordered)
        if file_rows or results:
            manifest.setdefault("results_reconciliations", []).append(
                {
                    "at": utc_now_iso(),
                    "file_rows": len(file_rows),
                    "duplicates_dropped": duplicates,
                    "adopted_from_file": adopted,
                    "completed": sum(1 for row in ordered if is_completed_result(row)),
                    "errored": sum(1 for row in ordered if not is_completed_result(row)),
                }
            )

    async def evaluate(self) -> list[dict[str, Any]]:
        self._preflight_database_guard()
        dataset = self.load_dataset()
        streams = {stream.question_id: stream for stream in build_question_streams(dataset)}
        user_ids = [stream.user_id for stream in streams.values()]
        checkpoint = self.load_checkpoint()
        self._seed_from_ingest(checkpoint)
        if checkpoint["phases"]["ingest"].get("status") != "completed":
            raise RuntimeError(
                "Evaluate needs a completed ingest: finish `ingest` first or pass --from-ingest"
            )
        evaluate_phase = mark_phase_started(checkpoint, "evaluate")
        save_runner_checkpoint(self.checkpoint_path, checkpoint)
        question_order = build_question_order(dataset)

        async with self._phase("evaluate", checkpoint) as (pool, store, manifest):
            manifest["benchmark_users"] = _benchmark_users_record(tuple(streams.values()))
            fingerprint_start = await fingerprint_benchmark_users(pool, user_ids)
            expected = checkpoint["phases"]["ingest"].get("state_fingerprint")
            state_matches = (
                expected is None or expected.get("sha256") == fingerprint_start["sha256"]
            )
            if not state_matches:
                if not self.allow_state_mismatch:
                    raise RuntimeError(
                        "Benchmark database state does not match the ingest this run evaluates. "
                        "Restore the ingest snapshot (RUNBOOK_LAPTOP.md) before evaluating."
                    )
                logger.warning("[evaluate] Proceeding on a database that differs from the ingest")
            self._reconcile_results(checkpoint, question_order, manifest)
            save_runner_checkpoint(self.checkpoint_path, checkpoint)

            completed_results: dict[str, dict[str, Any]] = evaluate_phase["results"]
            ingest_results: dict[str, dict[str, Any]] = checkpoint["phases"]["ingest"]["results"]
            if self.force_retrieval_logging:
                logger.info("[evaluate] LongMemEval benchmark forcing retrieval logging ON")
            logger.info(
                "[evaluate] Starting LongMemEval evaluation for %s questions (%s already complete)",
                len(question_order),
                sum(1 for row in completed_results.values() if is_completed_result(row)),
            )

            for idx, entry in enumerate(dataset):
                question_id = question_order[idx]
                previous = completed_results.get(question_id)
                if is_completed_result(previous):
                    continue
                self._check_stop()

                question_text = entry.get("question", "")
                reference = entry.get("answer", "")
                category_raw = entry.get("question_type", "single-session-user")
                category = CATEGORY_MAP.get(category_raw, "IE-user")
                attempt = int((previous or {}).get("attempt") or 0) + 1
                logger.info(
                    "[evaluate] [%s/%s] %s evaluating (attempt %s)",
                    idx + 1,
                    len(question_order),
                    question_id,
                    attempt,
                )
                stream = streams[question_id]
                try:
                    missing = [
                        session.corpus_key
                        for session in stream.sessions
                        if not (
                            ingest_results.get(ingest_result_key(question_id, session.corpus_key))
                            or {}
                        ).get("conversation_id")
                    ]
                    if missing:
                        raise RuntimeError(
                            f"Missing ingested sessions for {question_id}: {', '.join(missing)}"
                        )

                    def chat_scope(user_id: uuid.UUID = stream.user_id) -> Any:
                        return account_compute(pool, user_id, operation=EVALUATE_OPERATION)

                    # The question's own user holds only its haystack, so
                    # retrieval reads all of that user's memories, as production does.
                    result = await evaluate_single_parity(
                        store=store,
                        question_id=question_id,
                        question_text=question_text,
                        reference=reference,
                        category=category,
                        allowed_source_conversation_ids=None,
                        log_retrieval=self.force_retrieval_logging,
                        user_id=stream.user_id,
                        benchmark_mode=self.benchmark_mode,
                        scope_factory=chat_scope,
                    )
                except Exception as exc:
                    logger.exception("[evaluate] Question %s failed", question_id)
                    result = {
                        "question_id": question_id,
                        "question": question_text,
                        "reference": reference,
                        "hypothesis": "",
                        "category": category,
                        "judgment": "incorrect",
                        "error": type(exc).__name__,
                    }
                result["attempt"] = attempt
                result["evaluated_at"] = utc_now_iso()
                result["run_id"] = checkpoint["run_id"]
                append_jsonl_record(self.output_path, result)
                completed_results[question_id] = result
                evaluate_phase["completed_count"] = sum(
                    1 for row in completed_results.values() if is_completed_result(row)
                )
                evaluate_phase["updated_at"] = utc_now_iso()
                save_runner_checkpoint(self.checkpoint_path, checkpoint)
                self._heartbeat(manifest)

            ordered_results_list = [
                completed_results[qid] for qid in question_order if qid in completed_results
            ]
            question_ids = [row["question_id"] for row in ordered_results_list]
            if len(question_ids) != len(set(question_ids)):
                raise RuntimeError("Duplicate question results after evaluation")
            atomic_write_jsonl(self.output_path, ordered_results_list)
            fingerprint_end = await fingerprint_benchmark_users(pool, user_ids)
            errored = [row for row in ordered_results_list if not is_completed_result(row)]
            mark_phase_completed(
                checkpoint,
                "evaluate",
                completed_count=len(ordered_results_list) - len(errored),
            )
            evaluate_phase["error_count"] = len(errored)
            spend = await summarize_compute_spend(pool, user_ids, evaluate_phase.get("started_at"))
            manifest["phases"]["evaluate"] = {
                "completed_at": utc_now_iso(),
                "active_seconds": self._phase_active_seconds(manifest, "evaluate"),
                "questions": len(ordered_results_list),
                "errored": len(errored),
                "state_matches_ingest_at_start": state_matches,
                "state_unchanged_by_evaluate": fingerprint_start["sha256"]
                == fingerprint_end["sha256"],
                "state_fingerprint_start": fingerprint_start,
                "state_fingerprint_end": fingerprint_end,
                "retrieval_modes": _tally(
                    [str(row.get("retrieval_mode")) for row in ordered_results_list]
                ),
                "answer_usage": _sum_usage(ordered_results_list, "answer_usage"),
                "judge_usage": _sum_usage(ordered_results_list, "judge_usage"),
                "official_judge_usage": _sum_usage(ordered_results_list, "official_judge_usage"),
                "official_judgments": score_dual_judgments(ordered_results_list)["official"],
                "official_judge_failed": sum(
                    row.get("official_judge_status") == "failed" for row in ordered_results_list
                ),
                "official_judge_fingerprints": _tally(
                    [str(row.get("official_judge_fingerprint")) for row in ordered_results_list]
                ),
                "answer_finish_reasons": _tally(
                    [str(row.get("answer_finish_reason")) for row in ordered_results_list]
                ),
                "provider_fingerprints": {
                    call: _tally(
                        [str(row.get(f"{call}_fingerprint")) for row in ordered_results_list]
                    )
                    for call in ("answer", "judge")
                },
                "compute_spend": spend,
            }
            if fingerprint_start["sha256"] != fingerprint_end["sha256"]:
                logger.error("[evaluate] Benchmark state changed during evaluate: run contaminated")
        logger.info(
            "[evaluate] Complete: %s questions written to %s",
            len(ordered_results_list),
            self.output_path,
        )
        return ordered_results_list

    # ----------------------------------------------------------------- score
    def score(self) -> dict[str, Any]:
        checkpoint = load_runner_checkpoint(self.checkpoint_path, dataset_path=None)
        score_phase = mark_phase_started(checkpoint, "score")

        evaluate_results = checkpoint["phases"]["evaluate"].get("results", {})
        if evaluate_results:
            results = ordered_results(evaluate_results)
        elif self.output_path.exists():
            results = read_jsonl_records(self.output_path, repair=False)
            if any(row.get("run_id") != checkpoint.get("run_id") for row in results):
                raise RuntimeError(
                    f"{self.output_path} holds results from another run; refusing to score them"
                )
        else:
            raise FileNotFoundError(
                "No evaluation results available to score. Run the evaluate phase first "
                f"or provide results at {self.output_path}."
            )

        payload = build_score_payload(results)
        write_json(self.score_path, payload)
        score_phase["accuracy"] = payload["accuracy"]
        score_phase["result_count"] = payload["result_count"]
        mark_phase_completed(
            checkpoint,
            "score",
            completed_count=payload["result_count"],
        )
        save_runner_checkpoint(self.checkpoint_path, checkpoint)

        manifest_path = self.resolved_manifest_path()
        manifest = load_manifest(manifest_path)
        if manifest is not None:
            manifest.setdefault("phases", {})["score"] = {
                "completed_at": utc_now_iso(),
                "score_path": str(self.score_path),
                "score_sha256": file_sha256(self.score_path),
                "strict_accuracy": payload["strict_accuracy"],
                "baseline_qualified": manifest.get("baseline_qualified", False),
                "result_count": payload["result_count"],
                "error_count": payload["error_count"],
            }
            save_manifest(manifest_path, manifest)

        print_results(results, payload["accuracy"])
        logger.info("[score] Summary written to %s", self.score_path)
        return payload

    async def run(self) -> dict[str, Any]:
        logger.info(HARNESS_BANNER)
        ingest_results = await self.ingest()
        evaluation_results = await self.evaluate()
        score_payload = self.score()
        return {
            "ingest": ingest_results,
            "evaluate": evaluation_results,
            "score": score_payload,
        }
