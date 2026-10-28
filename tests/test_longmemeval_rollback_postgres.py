"""Real-PostgreSQL regression for interrupted LongMemEval ingest sessions.

Deleting an interrupted session's own rows is an exact rollback only when its
extraction changed nothing else. When it already superseded an earlier
session's memory (``supersede_memory`` closes the old row via ``valid_to``), the
harness must not baseline the altered state: it wipes that question's user and
rebuilds the question from its first session, leaving every other user alone.

Requires ``TASKS_TEST_DATABASE_URL`` (CI provides it); skips otherwise.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from orchestrator.eval.bench_db import CROSS_SESSION_TABLES, fingerprint_benchmark_state
from orchestrator.eval.fact_harness import LongMemEvalFactRunner, QuestionStream
from tests.durable_tasks_support import Env, durable_env_fixture

env = durable_env_fixture()


def _runner(tmp_path: Path) -> LongMemEvalFactRunner:
    return LongMemEvalFactRunner(
        dataset_path=tmp_path / "dataset.json",
        output_path=tmp_path / "longmemeval_results.jsonl",
        checkpoint_path=tmp_path / "longmemeval_checkpoint.json",
        score_path=tmp_path / "longmemeval_score.json",
    )


async def _checkpointed_session(env: Env) -> tuple[dict[str, Any], uuid.UUID]:
    conversation = await env.memory.create_conversation(user_id=env.alice, title="session A")
    memory = await env.memory.insert_memory(
        user_id=env.alice,
        content="User lives in Adelaide",
        category="fact",
        source_type="extracted",
        source_conversation_id=conversation["id"],
    )
    results = {
        "q1:key-a": {
            "question_id": "q1",
            "corpus_key": "key-a",
            "conversation_id": str(conversation["id"]),
        },
    }
    return results, memory["id"]


def _stream(env: Env) -> QuestionStream:
    return QuestionStream(question_id="q1", user_id=env.alice, sessions=(), reordered=False)


async def _pending(env: Env) -> dict[str, Any]:
    return {
        "q1": {
            "corpus_key": "key-b",
            "state_fingerprint": await fingerprint_benchmark_state(
                env.pool, env.alice, tables=CROSS_SESSION_TABLES
            ),
        }
    }


@pytest.mark.asyncio
async def test_rollback_of_a_self_contained_session_restores_the_pre_session_state(
    env: Env, tmp_path: Path
) -> None:
    results, _memory_id = await _checkpointed_session(env)
    pending = await _pending(env)
    orphan = await env.memory.create_conversation(user_id=env.alice, title="session B")
    await env.memory.insert_memory(
        user_id=env.alice,
        content="User adopted a cat named Miso",
        category="fact",
        source_type="extracted",
        source_conversation_id=orphan["id"],
    )
    manifest: dict[str, Any] = {}

    await _runner(tmp_path)._reconcile_interrupted_session(
        env.pool, _stream(env), pending, results, manifest
    )

    assert manifest["ingest_rollbacks"][0]["restored_pre_session_state"] is True
    assert manifest["ingest_rollbacks"][0]["memories"] == 1
    assert "q1" not in pending


@pytest.mark.asyncio
async def test_rollback_after_superseding_an_earlier_memory_rebuilds_only_that_user(
    env: Env, tmp_path: Path
) -> None:
    results, memory_id = await _checkpointed_session(env)
    other = await env.memory.create_conversation(user_id=env.bob, title="another question")
    await env.memory.insert_memory(
        user_id=env.bob,
        content="User plays the cello",
        category="fact",
        source_type="extracted",
        source_conversation_id=other["id"],
    )
    bob_before = await fingerprint_benchmark_state(env.pool, env.bob)
    pending = await _pending(env)
    orphan = await env.memory.create_conversation(user_id=env.alice, title="session B")
    await env.memory.supersede_memory(
        old_memory_id=memory_id,
        new_content="User moved to Melbourne",
        new_category="fact",
        new_source_type="extracted",
        user_id=env.alice,
        source_conversation_id=orphan["id"],
    )
    manifest: dict[str, Any] = {}

    await _runner(tmp_path)._reconcile_interrupted_session(
        env.pool, _stream(env), pending, results, manifest
    )

    assert manifest["ingest_rollbacks"][0]["restored_pre_session_state"] is False
    assert manifest["question_rebuilds"][0]["sessions_discarded"] == 1
    assert pending == {}
    assert results == {}
    alice_after = await fingerprint_benchmark_state(env.pool, env.alice)
    assert all(digest["rows"] == 0 for digest in alice_after["tables"].values())
    assert await env.pool.fetchval("SELECT count(*) FROM users WHERE id = $1", env.alice) == 1
    assert await fingerprint_benchmark_state(env.pool, env.bob) == bob_before
