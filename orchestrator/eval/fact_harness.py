from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import asyncpg

from orchestrator.config import get_settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.evaluate import (
    CATEGORY_MAP,
    CHECKPOINT_FILENAME,
    RESULTS_FILENAME,
    evaluate_single,
    print_results,
    score_accuracy,
    write_results_jsonl,
)
from tests.longmemeval.ingest import (
    CorpusPlan,
    build_corpus_plan,
    create_test_user,
    ingest_session,
    normalize_question_id,
)

logger = logging.getLogger(__name__)

PhaseName = Literal["ingest", "evaluate", "score"]

SCORE_FILENAME = "longmemeval_score.json"
CHECKPOINT_VERSION = 2
DEFAULT_OUTPUT_DIR = Path("tests/benchmark_results")
BENCHMARK_NAME = "longmemeval_fact"
BENCHMARK_SUBSTRATE = "fact"

HARNESS_BANNER = (
    "[fact-harness] LongMemEval FACT-substrate runner — LLM-extracted facts via "
    "production store.insert_memory. Use for wave-gate evaluation."
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2)


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
            f"{checkpoint_path} uses version {checkpoint_version!r}, but the corpus-first "
            f"LongMemEval harness requires version {CHECKPOINT_VERSION}. Old per-question "
            "session checkpoints are not compatible with the shared-corpus flow. Delete the "
            "checkpoint and rerun ingestion/evaluation."
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
    return {
        "substrate": BENCHMARK_SUBSTRATE,
        "benchmark_name": BENCHMARK_NAME,
        "generated_at": utc_now_iso(),
        "result_count": len(results),
        "accuracy": accuracy,
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
    """Reset the fact-substrate canonical benchmark state for the canonical test user.

    When a checkpoint path is supplied, the checkpoint is deleted along with the
    database rows so the next ingest cannot resume past missing benchmark state.

    When ``cleanup_redis`` is True, ARQ extraction Redis keys matching
    ``REDIS_EXTRACT_PATTERNS`` are also deleted after the DB cleanup succeeds.
    Redis failures are reported via ``redis_keys_deleted`` and do not flip
    ``success`` to False.
    """
    tables_cleared: dict[str, int] = {}
    total = 0
    checkpoint_reset: dict[str, Any] = {}
    redis_keys_deleted = 0
    try:
        from tests.longmemeval.ingest import TEST_USER_ID

        checkpoint_reset = _reset_checkpoint_file(checkpoint_path)

        for table, statement in CANONICAL_RESET_STATEMENTS:
            result = await pool.execute(statement, TEST_USER_ID)
            deleted = int(str(result).split()[-1]) if isinstance(result, str) else 0
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


@dataclass(slots=True)
class LongMemEvalFactRunner:
    dataset_path: Path
    output_path: Path
    checkpoint_path: Path
    score_path: Path
    limit: int | None = None
    force_retrieval_logging: bool = True

    def load_dataset(self) -> list[dict[str, Any]]:
        dataset = load_dataset(self.dataset_path)
        return dataset if self.limit is None else dataset[: self.limit]

    def build_corpus_plan(self) -> CorpusPlan:
        return build_corpus_plan(self.load_dataset())

    def load_checkpoint(self) -> dict[str, Any]:
        return load_runner_checkpoint(self.checkpoint_path, dataset_path=self.dataset_path)

    async def ingest(self) -> list[dict[str, Any]]:
        dataset = self.load_dataset()
        corpus_plan = build_corpus_plan(dataset)
        checkpoint = self.load_checkpoint()
        ingest_phase = mark_phase_started(checkpoint, "ingest")
        save_runner_checkpoint(self.checkpoint_path, checkpoint)

        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL not configured")
        if not settings.daemon_encryption_key:
            raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")

        pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=10,
        )

        try:
            encryption = ContentEncryption(settings.daemon_encryption_key)
            store = MemoryStore(db_pool=pool, encryption=encryption)
            test_user_id = await create_test_user(pool)

            existing_results = build_corpus_results_lookup(checkpoint)
            total_sessions = corpus_plan.unique_normalized_contents
            processed = len(existing_results)

            logger.info(
                "[ingest] Starting LongMemEval ingestion for %s dataset entries (%s haystack refs, %s unique session ids, %s unique normalized sessions, %s already checkpointed)",
                len(dataset),
                corpus_plan.total_haystack_refs,
                corpus_plan.unique_session_ids,
                total_sessions,
                processed,
            )

            for session_index, corpus_session in enumerate(corpus_plan.corpus_sessions):
                if corpus_session.corpus_key in existing_results:
                    logger.info(
                        "[ingest] [%s/%s] %s skip (checkpoint)",
                        min(processed + 1, total_sessions),
                        total_sessions,
                        corpus_session.canonical_session_id,
                    )
                    continue

                logger.info(
                    "[ingest] [%s/%s] %s ingesting (%s raw ids)",
                    processed + 1,
                    total_sessions,
                    corpus_session.canonical_session_id,
                    len(corpus_session.raw_session_ids),
                )
                try:
                    result: dict[str, Any] = await ingest_session(
                        store=store,
                        pool=pool,
                        user_id=test_user_id,
                        session_id=corpus_session.canonical_session_id,
                        messages=corpus_session.messages,
                        session_index=session_index,
                    )
                except Exception as exc:
                    logger.exception(
                        "[ingest] Session %s failed",
                        corpus_session.canonical_session_id,
                    )
                    result = {
                        "session_id": corpus_session.canonical_session_id,
                        "status": "error",
                        "error": str(exc),
                    }

                result["corpus_key"] = corpus_session.corpus_key
                result["raw_session_ids"] = list(corpus_session.raw_session_ids)
                existing_results[corpus_session.corpus_key] = result
                processed = len(existing_results)
                ingest_phase["completed_count"] = processed
                ingest_phase["updated_at"] = utc_now_iso()
                save_runner_checkpoint(self.checkpoint_path, checkpoint)
        finally:
            await pool.close()

        mark_phase_completed(
            checkpoint,
            "ingest",
            completed_count=len(checkpoint["phases"]["ingest"]["results"]),
        )
        save_runner_checkpoint(self.checkpoint_path, checkpoint)
        logger.info(
            "[ingest] Complete: %s sessions recorded in checkpoint %s",
            checkpoint["phases"]["ingest"]["completed_count"],
            self.checkpoint_path,
        )
        return ordered_results(checkpoint["phases"]["ingest"]["results"])

    async def evaluate(self) -> list[dict[str, Any]]:
        dataset = self.load_dataset()
        corpus_plan = build_corpus_plan(dataset)
        checkpoint = self.load_checkpoint()
        evaluate_phase = mark_phase_started(checkpoint, "evaluate")
        save_runner_checkpoint(self.checkpoint_path, checkpoint)

        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL not set")
        if not settings.daemon_encryption_key:
            raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")

        pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=10,
        )

        try:
            encryption = ContentEncryption(settings.daemon_encryption_key)
            store = MemoryStore(pool, encryption)
            question_order = build_question_order(dataset)
            completed_results: dict[str, dict[str, Any]] = evaluate_phase["results"]
            ingest_results = build_corpus_results_lookup(checkpoint)

            if self.force_retrieval_logging:
                logger.info("[evaluate] LongMemEval benchmark forcing retrieval logging ON")

            logger.info(
                "[evaluate] Starting LongMemEval evaluation for %s questions (%s already checkpointed)",
                len(question_order),
                len(completed_results),
            )

            for idx, entry in enumerate(dataset):
                question_id = question_order[idx]
                if question_id in completed_results:
                    logger.info(
                        "[evaluate] [%s/%s] %s skip (checkpoint)",
                        idx + 1,
                        len(question_order),
                        question_id,
                    )
                    continue

                question_text = entry.get("question", "")
                reference = entry.get("answer", "")
                category_raw = entry.get("question_type", "single-session-user")
                category = CATEGORY_MAP.get(category_raw, "IE-user")
                logger.info(
                    "[evaluate] [%s/%s] %s evaluating",
                    idx + 1,
                    len(question_order),
                    question_id,
                )

                try:
                    corpus_keys = resolve_question_corpus_refs(corpus_plan, question_id)
                    conversation_ids = resolve_question_conversation_ids(
                        ingest_results,
                        corpus_keys,
                    )
                    result = await evaluate_single(
                        store=store,
                        question_id=question_id,
                        question_text=question_text,
                        reference=reference,
                        category=category,
                        log_retrieval=self.force_retrieval_logging,
                        allowed_source_conversation_ids=[
                            uuid.UUID(value) for value in conversation_ids
                        ],
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
                        "error": str(exc),
                    }

                completed_results[question_id] = result
                evaluate_phase["completed_count"] = len(completed_results)
                evaluate_phase["updated_at"] = utc_now_iso()
                ordered = [
                    completed_results[qid] for qid in question_order if qid in completed_results
                ]
                write_results_jsonl(self.output_path, ordered)
                save_runner_checkpoint(self.checkpoint_path, checkpoint)
        finally:
            await pool.close()

        ordered_results_list = [
            checkpoint["phases"]["evaluate"]["results"][qid]
            for qid in question_order
            if qid in checkpoint["phases"]["evaluate"]["results"]
        ]
        write_results_jsonl(self.output_path, ordered_results_list)
        mark_phase_completed(
            checkpoint,
            "evaluate",
            completed_count=len(ordered_results_list),
        )
        save_runner_checkpoint(self.checkpoint_path, checkpoint)
        logger.info(
            "[evaluate] Complete: %s questions written to %s",
            len(ordered_results_list),
            self.output_path,
        )
        return ordered_results_list

    def score(self) -> dict[str, Any]:
        checkpoint = load_runner_checkpoint(self.checkpoint_path, dataset_path=None)
        score_phase = mark_phase_started(checkpoint, "score")

        evaluate_results = checkpoint["phases"]["evaluate"].get("results", {})
        if evaluate_results:
            results = ordered_results(evaluate_results)
        elif self.output_path.exists():
            results = [
                json.loads(line)
                for line in self.output_path.read_text().splitlines()
                if line.strip()
            ]
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
