from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import asyncpg

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_documents
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.evaluate import (
    CATEGORY_MAP,
    evaluate_single,
    load_checkpoint,
    save_checkpoint,
    score_accuracy,
    write_results_jsonl,
)
from tests.longmemeval.ingest import TEST_USER_NAME

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("tests/benchmark_results")
RESULTS_FILENAME = "longmemeval_fast_results.jsonl"
CHECKPOINT_FILENAME = "longmemeval_fast_checkpoint.json"

BENCHMARK_NAME = "longmemeval_fast"
BENCHMARK_SOURCE_TYPE = "import"
BENCHMARK_CATEGORY = "fact"
DEFAULT_CHUNK_MAX_CHARS = 4000
DEFAULT_OVERLAP_TURNS = 2


@dataclass(frozen=True, slots=True)
class SessionChunk:
    session_id: str
    session_index: int
    chunk_index: int
    content: str


@dataclass(frozen=True, slots=True)
class BenchmarkUser:
    user_id: uuid.UUID
    email: str
    name: str


def parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def resolve_output_paths(
    output_dir: Path,
    checkpoint_path: Path | None = None,
) -> tuple[Path, Path]:
    return (
        output_dir / RESULTS_FILENAME,
        checkpoint_path or output_dir / CHECKPOINT_FILENAME,
    )


def load_dataset(dataset_path: Path) -> list[dict[str, Any]]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"LongMemEval dataset not found: {dataset_path}")
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


def normalize_question_id(entry: dict[str, Any], idx: int) -> str:
    return str(entry.get("question_id", f"q{idx}"))


def build_benchmark_user(run_id: str) -> BenchmarkUser:
    return BenchmarkUser(
        user_id=uuid.uuid4(),
        email=f"longmemeval+fast-{run_id}@daemon.test",
        name=f"{TEST_USER_NAME}_fast_{run_id}",
    )


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(value) for value in embedding) + "]"


def _normalize_turn_role(role: object) -> str:
    normalized = str(role or "user").strip().lower()
    if normalized == "assistant":
        return "Assistant"
    return "User"


def _format_turn(message: dict[str, Any]) -> str | None:
    content = " ".join(str(message.get("content", "")).split()).strip()
    if not content:
        return None
    role = _normalize_turn_role(message.get("role"))
    return f"[{role}]: {content}"


def chunk_session_messages(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    overlap_turns: int = DEFAULT_OVERLAP_TURNS,
) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_turns < 0:
        raise ValueError("overlap_turns must be non-negative")

    def _length(turns: list[str]) -> int:
        if not turns:
            return 0
        return sum(len(t) for t in turns) + (len(turns) - 1)

    chunks: list[str] = []
    chunk_turns: list[str] = []
    chunk_len = 0
    pending: str | None = None

    for message in messages:
        if not isinstance(message, dict):
            continue

        formatted_turn = _format_turn(message)
        if formatted_turn is None:
            continue

        turn_len = len(formatted_turn)

        if pending is not None:
            formatted_turn = pending
            pending = None

        if turn_len > max_chars:
            if chunk_turns:
                chunks.append("\n".join(chunk_turns))
                overlap_count = min(overlap_turns, len(chunk_turns))
                chunk_turns = list(chunk_turns[-overlap_count:]) if overlap_count else []
                chunk_len = _length(chunk_turns)
            chunks.append(formatted_turn)
            chunk_turns = []
            chunk_len = 0
            continue

        if chunk_turns:
            sep = 1
            if chunk_len + sep + turn_len > max_chars:
                chunks.append("\n".join(chunk_turns))
                overlap_count = min(overlap_turns, len(chunk_turns))
                chunk_turns = list(chunk_turns[-overlap_count:]) if overlap_count else []
                chunk_len = _length(chunk_turns)
                if turn_len <= max_chars:
                    chunk_turns.append(formatted_turn)
                    chunk_len = _length(chunk_turns)
                else:
                    pending = formatted_turn
                continue

        chunk_turns.append(formatted_turn)
        chunk_len = _length(chunk_turns)

    if chunk_turns:
        chunks.append("\n".join(chunk_turns))

    return chunks


def build_question_chunks(
    entry: dict[str, Any],
    *,
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    overlap_turns: int = DEFAULT_OVERLAP_TURNS,
) -> list[SessionChunk]:
    haystack_sessions = entry.get("haystack_sessions", [])
    haystack_session_ids = entry.get("haystack_session_ids", [])
    if not isinstance(haystack_sessions, list):
        return []

    question_id = str(entry.get("question_id", "question"))
    chunks: list[SessionChunk] = []
    for session_index, messages in enumerate(haystack_sessions):
        if not isinstance(messages, list):
            continue
        session_id = (
            str(haystack_session_ids[session_index])
            if session_index < len(haystack_session_ids)
            else f"{question_id}_session_{session_index}"
        )
        for chunk_index, chunk_text in enumerate(
            chunk_session_messages(messages, max_chars=max_chars, overlap_turns=overlap_turns)
        ):
            chunks.append(
                SessionChunk(
                    session_id=session_id,
                    session_index=session_index,
                    chunk_index=chunk_index,
                    content=chunk_text,
                )
            )
    return chunks


async def cleanup_benchmark_state(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    _ = await pool.execute("DELETE FROM retrieval_log WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM dream_log WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM entities WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM memories WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM memory_extraction_log WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM messages WHERE user_id = $1", user_id)
    _ = await pool.execute("DELETE FROM conversations WHERE user_id = $1", user_id)


async def ensure_benchmark_user(pool: asyncpg.Pool, benchmark_user: BenchmarkUser) -> uuid.UUID:
    row = await pool.fetchrow(
        "SELECT id FROM users WHERE email = $1",
        benchmark_user.email,
    )
    if row is not None:
        return row["id"]

    created_row = await pool.fetchrow(
        """
        INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
        VALUES ($1, $2, $3, $4, '{}'::jsonb, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE
            SET email = EXCLUDED.email,
                name = EXCLUDED.name,
                username = EXCLUDED.username,
                updated_at = NOW()
        RETURNING id
        """,
        benchmark_user.user_id,
        benchmark_user.email,
        benchmark_user.name,
        benchmark_user.name,
    )
    if created_row is None:
        raise RuntimeError("Failed to create isolated LongMemEval fast benchmark user")
    return created_row["id"]


async def delete_benchmark_user(pool: asyncpg.Pool, email: str) -> None:
    _ = await pool.execute("DELETE FROM users WHERE email = $1", email)


async def insert_chunk_memories(
    *,
    pool: asyncpg.Pool,
    encryption: ContentEncryption,
    user_id: uuid.UUID,
    question_id: str,
    chunks: list[SessionChunk],
    conversation_ids_by_session: dict[str, uuid.UUID],
    embedding_model: str,
) -> list[uuid.UUID]:
    if not chunks:
        return []

    embeddings = await embed_documents([chunk.content for chunk in chunks])
    if len(embeddings) != len(chunks):
        raise RuntimeError(
            f"Embedding count mismatch: expected {len(chunks)} got {len(embeddings)}"
        )

    memory_ids: list[uuid.UUID] = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        encrypted_content = encryption.encrypt(chunk.content)
        metadata = {
            "benchmark": BENCHMARK_NAME,
            "benchmark_source_tag": BENCHMARK_NAME,
            "question_id": question_id,
            "session_id": chunk.session_id,
            "session_index": chunk.session_index,
            "chunk_index": chunk.chunk_index,
        }
        row = await pool.fetchrow(
            """
            INSERT INTO memories
                (user_id, content, content_tsv, embedding, embedding_model, category, source_type,
                 source_conversation_id, local_only, confidence, status, memory_slot,
                 trust_score, tier, metadata)
            VALUES ($1, $2, to_tsvector('english', $13), $3::vector, $4, $5, $6, $7, FALSE, $8, $9, NULL, $10, $11, $12::jsonb)
            RETURNING id
            """,
            user_id,
            encrypted_content,
            _vector_literal(embedding),
            embedding_model,
            BENCHMARK_CATEGORY,
            BENCHMARK_SOURCE_TYPE,
            conversation_ids_by_session[chunk.session_id],
            1.0,
            "active",
            0.5,
            "l1",
            json.dumps(metadata),
            chunk.content,
        )
        if row is None:
            raise RuntimeError("Direct memory insert returned no row")
        memory_ids.append(row["id"])
    return memory_ids


async def ingest_question_chunks(
    *,
    store: MemoryStore,
    pool: asyncpg.Pool,
    encryption: ContentEncryption,
    user_id: uuid.UUID,
    question_id: str,
    entry: dict[str, Any],
    chunk_max_chars: int,
    overlap_turns: int = DEFAULT_OVERLAP_TURNS,
) -> tuple[list[uuid.UUID], int]:
    chunks = build_question_chunks(entry, max_chars=chunk_max_chars, overlap_turns=overlap_turns)
    if not chunks:
        return [], 0

    conversation_ids_by_session: dict[str, uuid.UUID] = {}
    for chunk in chunks:
        if chunk.session_id in conversation_ids_by_session:
            continue
        conversation = await store.create_conversation(
            user_id=user_id,
            pipeline="cloud",
            title=f"LongMemEval Fast {question_id}: {chunk.session_id[:32]}",
        )
        conversation_ids_by_session[chunk.session_id] = conversation["id"]

    settings = get_settings()
    await insert_chunk_memories(
        pool=pool,
        encryption=encryption,
        user_id=user_id,
        question_id=question_id,
        chunks=chunks,
        conversation_ids_by_session=conversation_ids_by_session,
        embedding_model=settings.embedding_document_model,
    )
    return list(conversation_ids_by_session.values()), len(chunks)


@dataclass(slots=True)
class LongMemEvalFastRunner:
    dataset_path: Path
    output_path: Path
    checkpoint_path: Path
    limit: int | None = None
    chunk_max_chars: int = DEFAULT_CHUNK_MAX_CHARS
    overlap_turns: int = DEFAULT_OVERLAP_TURNS
    force_retrieval_logging: bool = True

    def load_dataset(self) -> list[dict[str, Any]]:
        dataset = load_dataset(self.dataset_path)
        return dataset if self.limit is None else dataset[: self.limit]

    async def run(self) -> list[dict[str, Any]]:
        dataset = self.load_dataset()
        checkpoint_results = cast(
            dict[str, dict[str, Any]],
            load_checkpoint(
                self.checkpoint_path,
                dataset_path=self.dataset_path,
            ),
        )
        question_order = [normalize_question_id(entry, idx) for idx, entry in enumerate(dataset)]

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
            store = MemoryStore(pool, encryption)
            run_id = uuid.uuid4().hex[:12]
            benchmark_user = build_benchmark_user(run_id)
            benchmark_user_id = await ensure_benchmark_user(pool, benchmark_user)

            logger.info(
                "[fast] Starting LongMemEval fast run for %s questions (%s already checkpointed)",
                len(question_order),
                len(checkpoint_results),
            )

            for idx, entry in enumerate(dataset):
                question_id = question_order[idx]
                if question_id in checkpoint_results:
                    logger.info(
                        "[fast] [%s/%s] %s skip (checkpoint)",
                        idx + 1,
                        len(question_order),
                        question_id,
                    )
                    continue

                question_text = str(entry.get("question", ""))
                reference = str(entry.get("answer", ""))
                category_raw = str(entry.get("question_type", "single-session-user"))
                category = CATEGORY_MAP.get(category_raw, "IE-user")

                logger.info(
                    "[fast] [%s/%s] %s ingest -> retrieve -> answer -> judge",
                    idx + 1,
                    len(question_order),
                    question_id,
                )

                chunk_count = 0
                conversation_ids: list[uuid.UUID] = []
                try:
                    await cleanup_benchmark_state(pool, benchmark_user_id)
                    conversation_ids, chunk_count = await ingest_question_chunks(
                        store=store,
                        pool=pool,
                        encryption=encryption,
                        user_id=benchmark_user_id,
                        question_id=question_id,
                        entry=entry,
                        chunk_max_chars=self.chunk_max_chars,
                        overlap_turns=self.overlap_turns,
                    )
                    result = await evaluate_single(
                        store=store,
                        question_id=question_id,
                        question_text=question_text,
                        reference=reference,
                        category=category,
                        log_retrieval=self.force_retrieval_logging,
                        allowed_source_conversation_ids=conversation_ids,
                        user_id=benchmark_user_id,
                    )
                    result["chunk_count"] = chunk_count
                    result["session_count"] = len(conversation_ids)
                    result["source_type"] = BENCHMARK_SOURCE_TYPE
                    checkpoint_results[question_id] = result
                except Exception as exc:
                    logger.exception("[fast] Question %s failed", question_id)
                    checkpoint_results[question_id] = {
                        "question_id": question_id,
                        "question": question_text,
                        "reference": reference,
                        "hypothesis": "",
                        "category": category,
                        "judgment": "incorrect",
                        "error": str(exc),
                        "chunk_count": chunk_count,
                        "session_count": len(conversation_ids),
                        "source_type": BENCHMARK_SOURCE_TYPE,
                    }
                finally:
                    await cleanup_benchmark_state(pool, benchmark_user_id)

                ordered_results = [
                    checkpoint_results[qid] for qid in question_order if qid in checkpoint_results
                ]
                save_checkpoint(
                    self.checkpoint_path,
                    dataset_path=self.dataset_path,
                    results=ordered_results,
                )
                write_results_jsonl(self.output_path, ordered_results)
            await delete_benchmark_user(pool, benchmark_user.email)
        finally:
            await pool.close()

        results = [checkpoint_results[qid] for qid in question_order if qid in checkpoint_results]
        accuracy = score_accuracy(results)
        logger.info("[fast] Complete: %s results written to %s", len(results), self.output_path)
        logger.info("[fast] Accuracy snapshot: %s", accuracy)
        return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.eval.longmemeval_fast",
        description="Standalone fast LongMemEval harness using direct memory inserts.",
    )
    parser.add_argument("--dataset", type=Path, required=True, help="Path to dataset JSON.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for {RESULTS_FILENAME} and {CHECKPOINT_FILENAME}.",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help=f"Optional checkpoint path (default: <output-dir>/{CHECKPOINT_FILENAME}).",
    )
    parser.add_argument(
        "--limit",
        type=parse_positive_int,
        default=None,
        help="Limit number of questions processed.",
    )
    parser.add_argument(
        "--chunk-max-chars",
        type=parse_positive_int,
        default=DEFAULT_CHUNK_MAX_CHARS,
        help="Maximum characters per inserted session chunk.",
    )
    parser.add_argument(
        "--overlap-turns",
        type=parse_positive_int,
        default=DEFAULT_OVERLAP_TURNS,
        help="Number of turns to overlap between adjacent chunks.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    output_path, checkpoint_path = resolve_output_paths(
        output_dir=args.output_dir,
        checkpoint_path=args.checkpoint,
    )
    runner = LongMemEvalFastRunner(
        dataset_path=args.dataset,
        output_path=output_path,
        checkpoint_path=checkpoint_path,
        limit=args.limit,
        chunk_max_chars=args.chunk_max_chars,
        overlap_turns=args.overlap_turns,
        force_retrieval_logging=True,
    )
    _ = asyncio.run(runner.run())


if __name__ == "__main__":
    main()
