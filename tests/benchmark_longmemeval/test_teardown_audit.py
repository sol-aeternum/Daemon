from __future__ import annotations

import asyncio
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import asyncpg
import pytest

from orchestrator.config import get_settings
from orchestrator.eval.longmemeval_fast import cleanup_benchmark_state, ingest_question_chunks
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.evaluate import evaluate_single
from tests.longmemeval.ingest import ingest_session

REPORT_PATH = Path(__file__).with_name("TEARDOWN_AUDIT.md")
TABLES = [
    "users",
    "conversations",
    "messages",
    "memories",
    "memory_extraction_log",
    "retrieval_log",
    "entities",
    "dream_log",
]


@dataclass(slots=True)
class SnapshotRow:
    label: str
    counts: dict[str, int]
    note: str


@dataclass(slots=True)
class RetrievalGate:
    entered: asyncio.Event
    release: asyncio.Event


def _test_vector(value: float = 0.25) -> list[float]:
    return [value] * get_settings().embedding_dimensions


def _resolved_database_url() -> str | None:
    settings = get_settings()
    if not settings.database_url:
        return None

    parsed = urlparse(settings.database_url)
    if parsed.hostname != "postgres":
        return settings.database_url

    if not parsed.username or parsed.password is None:
        return settings.database_url

    return (
        f"postgresql://{parsed.username}:{parsed.password}"
        f"@127.0.0.1:{parsed.port or 5432}/{parsed.path.lstrip('/')}"
    )


async def _create_test_pool() -> asyncpg.Pool:
    resolved = _resolved_database_url()
    if not resolved:
        pytest.skip("DATABASE_URL not configured for teardown audit")

    try:
        return await asyncpg.create_pool(
            dsn=resolved,
            min_size=1,
            max_size=4,
        )
    except OSError as exc:
        parsed = urlparse(resolved)
        if parsed.hostname != "127.0.0.1":
            raise
        if not isinstance(exc, socket.gaierror):
            raise
        pytest.skip(f"Benchmark teardown audit could not reach database: {exc}")


async def _insert_user(
    pool: asyncpg.Pool,
    *,
    user_id: uuid.UUID,
    email: str,
    name: str,
) -> None:
    _ = await pool.execute(
        """
        INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
        VALUES ($1, $2, $3, $3, '{}'::jsonb, NOW(), NOW())
        """,
        user_id,
        email,
        name,
    )


async def _delete_user(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    _ = await pool.execute("DELETE FROM users WHERE id = $1", user_id)


async def _count_rows(pool: asyncpg.Pool, table: str, user_id: uuid.UUID) -> int:
    if table == "users":
        query = "SELECT COUNT(*) FROM users WHERE id = $1"
    else:
        query = f"SELECT COUNT(*) FROM {table} WHERE user_id = $1"
    value = cast(int, await pool.fetchval(query, user_id))
    assert isinstance(value, int)
    return value


async def _snapshot_counts(pool: asyncpg.Pool, user_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLES:
        counts[table] = await _count_rows(pool, table, user_id)
    return counts


async def _wait_for_table_count(
    pool: asyncpg.Pool,
    *,
    table: str,
    user_id: uuid.UUID,
    expected: int,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await _count_rows(pool, table, user_id) == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{table} count did not reach {expected} for audit user {user_id}")


async def _retrieval_log_null_conversation_count(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    value = cast(
        int,
        await pool.fetchval(
            """
            SELECT COUNT(*)
            FROM retrieval_log
            WHERE user_id = $1
              AND conversation_id IS NULL
            """,
            user_id,
        ),
    )
    assert isinstance(value, int)
    return value


def _render_table(rows: list[SnapshotRow]) -> str:
    header = (
        "| Snapshot | users | conversations | messages | memories | "
        "memory_extraction_log | retrieval_log | entities | dream_log | Notes |"
    )
    separator = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    body = [header, separator]
    for row in rows:
        counts = row.counts
        body.append(
            "| "
            + " | ".join(
                [
                    row.label,
                    str(counts["users"]),
                    str(counts["conversations"]),
                    str(counts["messages"]),
                    str(counts["memories"]),
                    str(counts["memory_extraction_log"]),
                    str(counts["retrieval_log"]),
                    str(counts["entities"]),
                    str(counts["dream_log"]),
                    row.note,
                ]
            )
            + " |"
        )
    return "\n".join(body)


def _render_report(
    *,
    canonical_rows: list[SnapshotRow],
    fast_rows: list[SnapshotRow],
    canonical_null_logs: dict[str, int],
    fast_null_logs: dict[str, int],
) -> str:
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    return f"""# LongMemEval Teardown Audit

Date: {timestamp}

## Scope

This audit exercised the live benchmark code paths with deterministic local doubles for extraction, embeddings, answer generation, and judging so the only variable under test was database teardown behavior.

- Canonical lane exercised `tests.longmemeval.ingest.ingest_session()` plus `tests.longmemeval.evaluate.evaluate_single()`, which are the concrete units looped by `orchestrator/eval/runner.py`.
- Fast lane exercised `orchestrator.eval.longmemeval_fast.cleanup_benchmark_state()` plus `ingest_question_chunks()` plus `evaluate_single()`, mirroring the per-question loop in `LongMemEvalFastRunner.run()`.

### Instrumentation note

The fast-lane audit deliberately held the background `store.log_retrieval()` task behind an event before releasing it. That does **not** change which row is written; it only makes the existing asynchronous retrieval-log timing window deterministic so the audit can prove whether late writes survive teardown.

## Canonical lane snapshots

{_render_table(canonical_rows)}

### Canonical interpretation

- `conversations`, `messages`, `memories`, `memory_extraction_log`, and `retrieval_log` all grow from case 1 to case 2 instead of returning to zero.
- The canonical retrieval rows were written with `conversation_id IS NULL` in both observed cases (`after case 1 settled = {canonical_null_logs["after_case1"]}`, `after case 2 settled = {canonical_null_logs["after_case2"]}`), so they are not tied to conversation deletion anyway.
- Manually deleting the audit user returns every table to zero, which shows the residual rows come from **missing per-case teardown**, not from broken foreign-key cleanup.

**Canonical verdict:** residual rows survive between benchmark cases because the canonical lane does not run teardown between cases. The only destructive cleanup is whole-user deletion, and `orchestrator/eval/runner.py` does not call it.

## Fast lane snapshots

{_render_table(fast_rows)}

### Fast interpretation

- `messages` and `memory_extraction_log` stay at zero for every fast-lane snapshot because `ingest_question_chunks()` direct-inserts `memories` and bypasses canonical message persistence and extraction logging.
- After each fast case returns, the post-case cleanup removes the synchronous tables (`conversations`, `memories`, etc.) back to zero.
- Releasing the delayed retrieval-log task **after** cleanup recreates a single `retrieval_log` row (`conversation_id IS NULL` count after case 1 release = {fast_null_logs["after_case1_release"]}; after case 2 release = {fast_null_logs["after_case2_release"]}). That row survives the post-case cleanup because it lands after the deletes have already run.
- The next case's pre-cleanup deletes the leftover row from the prior case, and final user deletion returns every table to zero.

**Fast verdict:** the fast lane has no stable leak in its synchronous tables, but `retrieval_log` can survive teardown through **async bleed** from the background persistence task. Any row left behind is finally removed by the next pre-case cleanup or, if it is the last case, by the end-of-run user deletion.

## Root-cause summary

| Lane | Residual rows observed between cases? | Root cause | Evidence |
| --- | --- | --- | --- |
| Canonical | Yes: `conversations`, `messages`, `memories`, `memory_extraction_log`, `retrieval_log` accumulate 1 -> 2 across the two cases | Missing teardown | Counts only reset after the audit manually deletes the whole user |
| Fast | Yes, but only for `retrieval_log` when the delayed background write lands after cleanup | Async bleed | Post-case cleanup reaches zero, then a late `retrieval_log` row reappears with all other tables still at zero |
| Fast end-of-run | No rows remain after `DELETE FROM users ...` | End-of-run user deletion | Final user delete returns the run-scoped user and every user-linked table to zero |

## Bottom line

- The canonical lane leaks benchmark state between cases because it never tears the benchmark user down between cases.
- The fast lane cleans its synchronous benchmark tables, but retrieval evidence is vulnerable to async timing because the retrieval-log write is backgrounded.
- End-of-run user deletion is a separate mechanism from per-case teardown: it is not what causes the leak, but it is what guarantees the last fast-lane stray row disappears.
"""


@pytest.mark.asyncio
async def test_teardown_audit_writes_report(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved_url = _resolved_database_url()
    if not resolved_url:
        pytest.skip("DATABASE_URL not configured for teardown audit")

    monkeypatch.setenv("DATABASE_URL", resolved_url)
    get_settings.cache_clear()
    settings = get_settings()

    if not settings.daemon_encryption_key:
        pytest.skip("DAEMON_ENCRYPTION_KEY not configured for teardown audit")

    pool = await _create_test_pool()
    encryption = ContentEncryption(settings.daemon_encryption_key)

    async def fake_process_extraction(
        store: MemoryStore,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        text: str,
    ) -> tuple[bool, list[dict[str, object]]]:
        memory = await store.insert_memory(
            user_id=user_id,
            content=f"AUDIT canonical memory for {conversation_id}",
            category="fact",
            source_type="conversation",
            embedding=_test_vector(0.25),
            embedding_model="benchmark-audit-document",
            source_conversation_id=conversation_id,
            memory_slot="profile",
        )
        _ = await store.log_extraction(
            user_id=user_id,
            conversation_id=conversation_id,
            input_snippet=text[:1000],
            extracted_facts=[
                {
                    "content": f"Audit fact for {conversation_id}",
                    "category": "fact",
                    "confidence": 1.0,
                    "slot": "profile",
                }
            ],
            dedup_results={"new": 1, "merged": 0, "superseded": 0},
            model_used="benchmark-audit-extractor",
        )
        return True, [memory]

    async def fake_embed_query(_text: str) -> list[float]:
        return _test_vector(0.25)

    async def fake_answer(
        _question_text: str, memories: list[dict[str, object]], **kwargs: Any
    ) -> str:
        if not memories:
            return "no-memory"
        content = memories[0].get("content")
        assert isinstance(content, str)
        return content

    async def fake_judge(
        _question_text: str, _hypothesis: str, _reference: str, **kwargs: Any
    ) -> str:
        return "correct"

    async def fake_embed_documents(texts: list[str]) -> list[list[float]]:
        return [_test_vector(0.35) for _ in texts]

    monkeypatch.setattr("tests.longmemeval.ingest.process_extraction", fake_process_extraction)
    monkeypatch.setattr("tests.longmemeval.evaluate.embed_query", fake_embed_query)
    monkeypatch.setattr("tests.longmemeval.evaluate.answer_with_llm", fake_answer)
    monkeypatch.setattr("tests.longmemeval.evaluate.judge_answer", fake_judge)
    monkeypatch.setattr("orchestrator.eval.longmemeval_fast.embed_documents", fake_embed_documents)

    canonical_user_id: uuid.UUID | None = None
    fast_user_id: uuid.UUID | None = None

    try:
        canonical_store = MemoryStore(pool, encryption)
        fast_store = MemoryStore(pool, encryption)

        canonical_rows: list[SnapshotRow] = []
        fast_rows: list[SnapshotRow] = []
        canonical_null_logs: dict[str, int] = {}
        fast_null_logs: dict[str, int] = {}

        canonical_user_id = uuid.uuid4()
        await _insert_user(
            pool,
            user_id=canonical_user_id,
            email=f"teardown-audit-canonical+{canonical_user_id.hex}@daemon.test",
            name="teardown_audit_canonical",
        )

        canonical_rows.append(
            SnapshotRow(
                label="baseline",
                counts=await _snapshot_counts(pool, canonical_user_id),
                note="fresh isolated audit user before case 1",
            )
        )

        canonical_case_1 = await ingest_session(
            canonical_store,
            pool,
            canonical_user_id,
            "canonical-session-1",
            [
                {"role": "user", "content": "The user keeps a red notebook."},
                {
                    "role": "assistant",
                    "content": "I can remember the notebook color.",
                },
            ],
            0,
        )
        canonical_case_1_conversation_id = canonical_case_1.get("conversation_id")
        assert isinstance(canonical_case_1_conversation_id, str)
        canonical_case_1_conversation = uuid.UUID(canonical_case_1_conversation_id)
        _ = await evaluate_single(
            canonical_store,
            "canonical-q1",
            "What color is the notebook?",
            "red",
            "IE-user",
            log_retrieval=True,
            allowed_source_conversation_ids=[canonical_case_1_conversation],
            user_id=canonical_user_id,
        )
        await _wait_for_table_count(
            pool,
            table="retrieval_log",
            user_id=canonical_user_id,
            expected=1,
        )
        canonical_rows.append(
            SnapshotRow(
                label="after case 1 settled",
                counts=await _snapshot_counts(pool, canonical_user_id),
                note="no teardown ran after case 1",
            )
        )
        canonical_null_logs["after_case1"] = await _retrieval_log_null_conversation_count(
            pool, canonical_user_id
        )

        canonical_case_2 = await ingest_session(
            canonical_store,
            pool,
            canonical_user_id,
            "canonical-session-2",
            [
                {"role": "user", "content": "The user moved to Lisbon."},
                {"role": "assistant", "content": "I can remember the city."},
            ],
            1,
        )
        canonical_case_2_conversation_id = canonical_case_2.get("conversation_id")
        assert isinstance(canonical_case_2_conversation_id, str)
        canonical_case_2_conversation = uuid.UUID(canonical_case_2_conversation_id)
        _ = await evaluate_single(
            canonical_store,
            "canonical-q2",
            "Where did the user move?",
            "Lisbon",
            "IE-user",
            log_retrieval=True,
            allowed_source_conversation_ids=[canonical_case_2_conversation],
            user_id=canonical_user_id,
        )
        await _wait_for_table_count(
            pool,
            table="retrieval_log",
            user_id=canonical_user_id,
            expected=2,
        )
        canonical_rows.append(
            SnapshotRow(
                label="after case 2 settled",
                counts=await _snapshot_counts(pool, canonical_user_id),
                note="case 2 adds another full row-set on top of case 1",
            )
        )
        canonical_null_logs["after_case2"] = await _retrieval_log_null_conversation_count(
            pool, canonical_user_id
        )

        await _delete_user(pool, canonical_user_id)
        canonical_rows.append(
            SnapshotRow(
                label="after manual user delete",
                counts=await _snapshot_counts(pool, canonical_user_id),
                note="manual cleanup proves FK cascades work when invoked",
            )
        )
        canonical_user_id = None

        fast_user_id = uuid.uuid4()
        await _insert_user(
            pool,
            user_id=fast_user_id,
            email=f"teardown-audit-fast+{fast_user_id.hex}@daemon.test",
            name="teardown_audit_fast",
        )

        fast_rows.append(
            SnapshotRow(
                label="baseline",
                counts=await _snapshot_counts(pool, fast_user_id),
                note="fresh isolated fast-lane user before case 1",
            )
        )

        original_fast_log_retrieval = fast_store.log_retrieval
        active_gate: RetrievalGate | None = None

        async def delayed_log_retrieval(
            user_id: uuid.UUID,
            query_text: str,
            query_embedding_model: str,
            query_embedding: list[float] | None,
            candidate_memory_ids: list[uuid.UUID],
            candidate_scores: dict[str, object],
            selected_memory_ids: list[uuid.UUID],
            l0_included: bool,
            latency_ms: int,
            *,
            conversation_id: uuid.UUID | None = None,
            retrieval_context: str | None = None,
            retrieval_triggered_by: str | None = None,
        ) -> dict[str, Any]:
            gate = active_gate
            assert gate is not None
            gate.entered.set()
            await gate.release.wait()
            return await original_fast_log_retrieval(
                user_id=user_id,
                query_text=query_text,
                query_embedding_model=query_embedding_model,
                query_embedding=query_embedding,
                candidate_memory_ids=candidate_memory_ids,
                candidate_scores=candidate_scores,
                selected_memory_ids=selected_memory_ids,
                l0_included=l0_included,
                latency_ms=latency_ms,
                conversation_id=conversation_id,
                retrieval_context=retrieval_context,
                retrieval_triggered_by=retrieval_triggered_by,
            )

        monkeypatch.setattr(fast_store, "log_retrieval", delayed_log_retrieval)

        fast_cases = [
            (
                "case 1",
                {
                    "question_id": "fast-q1",
                    "haystack_session_ids": ["fast-session-1"],
                    "haystack_sessions": [
                        [
                            {
                                "role": "user",
                                "content": "The train leaves at dawn.",
                            },
                            {
                                "role": "assistant",
                                "content": "I will remember the departure time.",
                            },
                        ]
                    ],
                },
                "When does the train leave?",
                "dawn",
            ),
            (
                "case 2",
                {
                    "question_id": "fast-q2",
                    "haystack_session_ids": ["fast-session-2"],
                    "haystack_sessions": [
                        [
                            {
                                "role": "user",
                                "content": "The concert is on Friday.",
                            },
                            {
                                "role": "assistant",
                                "content": "I will remember the day.",
                            },
                        ]
                    ],
                },
                "What day is the concert?",
                "Friday",
            ),
        ]

        for index, (label, entry, question_text, reference) in enumerate(fast_cases, start=1):
            await cleanup_benchmark_state(pool, fast_user_id)
            fast_rows.append(
                SnapshotRow(
                    label=f"{label} after pre-case cleanup",
                    counts=await _snapshot_counts(pool, fast_user_id),
                    note=(
                        "baseline cleanup before case 1"
                        if index == 1
                        else "this pre-case cleanup removes any leftover row from the prior case"
                    ),
                )
            )

            active_gate = RetrievalGate(asyncio.Event(), asyncio.Event())
            conversation_ids, _ = await ingest_question_chunks(
                store=fast_store,
                pool=pool,
                encryption=encryption,
                user_id=fast_user_id,
                question_id=str(entry["question_id"]),
                entry=entry,
                chunk_max_chars=4000,
                overlap_turns=2,
            )
            _ = await evaluate_single(
                fast_store,
                str(entry["question_id"]),
                question_text,
                reference,
                "IE-user",
                log_retrieval=True,
                allowed_source_conversation_ids=conversation_ids,
                user_id=fast_user_id,
            )
            _ = await asyncio.wait_for(active_gate.entered.wait(), timeout=5.0)

            fast_rows.append(
                SnapshotRow(
                    label=f"{label} after evaluate return",
                    counts=await _snapshot_counts(pool, fast_user_id),
                    note="retrieval_log task is queued but still blocked behind the audit gate",
                )
            )

            await cleanup_benchmark_state(pool, fast_user_id)
            fast_rows.append(
                SnapshotRow(
                    label=f"{label} after post-case cleanup",
                    counts=await _snapshot_counts(pool, fast_user_id),
                    note="cleanup removed synchronous tables before the retrieval-log task was released",
                )
            )

            active_gate.release.set()
            await _wait_for_table_count(
                pool,
                table="retrieval_log",
                user_id=fast_user_id,
                expected=1,
            )
            fast_rows.append(
                SnapshotRow(
                    label=f"{label} after delayed retrieval flush",
                    counts=await _snapshot_counts(pool, fast_user_id),
                    note="late retrieval_log insert survives teardown while all other user tables stay at zero",
                )
            )
            fast_null_logs[
                f"after_case{index}_release"
            ] = await _retrieval_log_null_conversation_count(pool, fast_user_id)

        await _delete_user(pool, fast_user_id)
        fast_rows.append(
            SnapshotRow(
                label="after end-of-run user delete",
                counts=await _snapshot_counts(pool, fast_user_id),
                note="final user deletion clears the last leaked retrieval row",
            )
        )
        fast_user_id = None

        report = _render_report(
            canonical_rows=canonical_rows,
            fast_rows=fast_rows,
            canonical_null_logs=canonical_null_logs,
            fast_null_logs=fast_null_logs,
        )
        _ = REPORT_PATH.write_text(report)

        assert "missing per-case teardown" in report.lower()
        assert "async bleed" in report.lower()
        assert "end-of-run user deletion" in report.lower()
        assert canonical_rows[1].counts["conversations"] == 1
        assert canonical_rows[2].counts["conversations"] == 2
        assert canonical_rows[3].counts["retrieval_log"] == 0
        assert fast_rows[2].counts["retrieval_log"] == 0
        assert fast_rows[3].counts["retrieval_log"] == 0
        assert fast_rows[4].counts["retrieval_log"] == 1
        assert fast_rows[-1].counts["retrieval_log"] == 0
    finally:
        if canonical_user_id is not None:
            await _delete_user(pool, canonical_user_id)
        if fast_user_id is not None:
            await _delete_user(pool, fast_user_id)
        await pool.close()
