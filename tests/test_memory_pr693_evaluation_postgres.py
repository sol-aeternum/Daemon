"""Real runner/retrieval/logger evaluate and resume; only inference is scripted."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from orchestrator.eval import bench_db, fact_harness
from orchestrator.memory import injection, retrieval
from orchestrator.memory.embedding import EmbeddingConfigurationError
from tests.benchmark_reset_support import benchmark_env_fixture
from tests.longmemeval import evaluate

env = benchmark_env_fixture()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_memory", [False, True])
async def test_logged_evaluation_preserves_protected_fingerprint_and_resumes(
    env, tmp_path, monkeypatch, with_memory
):
    qid = "synthetic-pr693"
    owner = bench_db.question_user_id(qid)
    await bench_db.ensure_benchmark_user(env.pool, owner)
    conversation = await env.memory.create_conversation(owner, title="Synthetic haystack")
    if with_memory:
        await env.memory.insert_memory(owner, "User likes fictional Amber.", "fact", "manual")
    else:
        assert not await env.pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM memory_knowledge_clock WHERE user_id=$1)", owner
        )
    dataset = [
        {
            "question_id": qid,
            "question": "What fictional Amber does the user like?",
            "answer": "Amber",
            "question_type": "single-session-user",
            "question_date": "2026/11/10 (Tue) 02:21",
            "haystack_session_ids": ["synthetic-session"],
            "haystack_sessions": [[{"role": "user", "content": "User likes fictional Amber."}]],
        }
    ]
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset))
    runner = fact_harness.LongMemEvalFactRunner(
        dataset_path=dataset_path,
        output_path=tmp_path / "results.jsonl",
        checkpoint_path=tmp_path / "checkpoint.json",
        score_path=tmp_path / "score.json",
        force_retrieval_logging=True,
    )
    checkpoint = runner.load_checkpoint()
    stream = fact_harness.build_question_streams(dataset)[0]
    baseline = await bench_db.fingerprint_benchmark_users(env.pool, [owner])
    checkpoint["phases"]["ingest"].update(
        status="completed",
        state_fingerprint=baseline,
        results={
            fact_harness.ingest_result_key(qid, stream.sessions[0].corpus_key): {
                "conversation_id": str(conversation["id"]),
                "status": "complete",
            }
        },
    )
    fact_harness.save_runner_checkpoint(runner.checkpoint_path, checkpoint)
    manifests = []

    @asynccontextmanager
    async def phase(self, phase, checkpoint):
        # Use the fixture's real marked pool/store, not a provider-attested app
        # startup or a second pool with a different test schema search_path.
        await bench_db.assert_benchmark_database(env.pool, fact_harness.get_settings().database_url)
        manifest = {"phases": {}, "invocations": [{"phase": phase, "outcome": "running"}]}
        manifests.append(manifest)
        try:
            yield env.pool, env.memory, manifest
        finally:
            fact_harness.save_runner_checkpoint(self.checkpoint_path, checkpoint)

    @asynccontextmanager
    async def compute(*args, **kwargs):
        yield

    monkeypatch.setattr(fact_harness.LongMemEvalFactRunner, "_phase", phase)
    monkeypatch.setattr(fact_harness, "account_compute", compute)
    monkeypatch.setattr(retrieval, "_is_retrieval_logging_enabled", bool)
    monkeypatch.setattr(
        injection,
        "embed_query_with_metadata",
        AsyncMock(side_effect=EmbeddingConfigurationError("synthetic provider disabled")),
    )
    log_done = asyncio.Event()
    original_log = env.memory.log_retrieval

    async def logged(*args, **kwargs):
        result = await original_log(*args, **kwargs)
        log_done.set()  # The real mutation/logger/observer has acknowledged COMMIT.
        return result

    monkeypatch.setattr(env.memory, "log_retrieval", logged)

    async def answer(*args, **kwargs):
        await asyncio.wait_for(log_done.wait(), 10)
        return "Amber", {}

    answer_call = AsyncMock(side_effect=answer)
    monkeypatch.setattr(evaluate, "_answer_with_messages", answer_call)
    monkeypatch.setattr(evaluate, "_judge_with_usage", AsyncMock(return_value=("correct", {})))
    monkeypatch.setattr(
        evaluate,
        "judge_answer_official",
        AsyncMock(
            return_value={
                "official_judgment": "correct",
                "official_judge_usage": {},
            }
        ),
    )
    results = await runner.evaluate()
    assert results[0]["hypothesis"] == "Amber" and "error" not in results[0]
    assert results[0]["memories_used"] == int(with_memory)
    assert await env.pool.fetchval("SELECT count(*) FROM retrieval_log WHERE user_id=$1", owner) > 0
    assert await env.pool.fetchval(
        "SELECT sequence FROM memory_knowledge_clock WHERE user_id=$1", owner
    ) > int(with_memory)
    assert await bench_db.fingerprint_benchmark_users(env.pool, [owner]) == baseline
    for _ in range(2):
        await runner.evaluate()  # Retains the original ingest baseline, no override.
        assert not runner.allow_state_mismatch
        assert manifests[-1]["phases"]["evaluate"]["state_matches_ingest_at_start"] is True
        assert manifests[-1]["phases"]["evaluate"]["state_unchanged_by_evaluate"] is True
    answer_call.assert_awaited_once()
    assert manifests[0]["phases"]["evaluate"]["state_unchanged_by_evaluate"] is True
    # Even an empty-payload reset advances the retained epoch and must invalidate
    # resume rather than allowing diagnostics to mask erased/rebuilt inputs.
    await bench_db.wipe_benchmark_user(env.pool, owner, bench_db.BENCHMARK_RESET_STATEMENTS)
    with pytest.raises(RuntimeError, match="does not match the ingest"):
        await runner.evaluate()
