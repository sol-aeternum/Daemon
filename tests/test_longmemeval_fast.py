from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from orchestrator.eval.chunk_harness import (
    BENCHMARK_MEMORY_CATEGORY,
    BENCHMARK_NAME,
    BENCHMARK_SOURCE_TYPE,
    BenchmarkUser,
    DEFAULT_CHUNK_MAX_CHARS,
    DEFAULT_OVERLAP_TURNS,
    LongMemEvalChunkRunner,
    build_question_chunks,
    chunk_session_messages,
)


def write_dataset(path: Path, payload: Sequence[dict[str, Any]]) -> None:
    path.write_text(json.dumps(payload))


def test_chunk_session_messages_keeps_turn_boundaries_and_formats_roles() -> None:
    """With overlap_turns=0, chunking is non-overlapping (original behavior)."""
    messages = [
        {"role": "user", "content": "alpha beta"},
        {"role": "assistant", "content": "gamma delta"},
        {"role": "user", "content": "epsilon zeta eta theta"},
    ]

    chunks = chunk_session_messages(messages, max_chars=50, overlap_turns=0)

    assert chunks == [
        "[User]: alpha beta\n[Assistant]: gamma delta",
        "[User]: epsilon zeta eta theta",
    ]


@pytest.mark.asyncio
async def test_fast_runner_inserts_direct_memories_and_resumes_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = [
        {
            "question_id": "q1",
            "question": "What did the user ask about?",
            "answer": "A laptop",
            "question_type": "single-session-user",
            "haystack_session_ids": ["session-1"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I need a new laptop for school."},
                    {"role": "assistant", "content": "What specs matter most?"},
                    {"role": "user", "content": "Battery life and portability."},
                ]
            ],
        }
    ]
    dataset_path = tmp_path / "dataset.json"
    write_dataset(dataset_path, dataset)

    output_path = tmp_path / "chunk_results.jsonl"
    checkpoint_path = tmp_path / "chunk_checkpoint.json"
    score_path = tmp_path / "chunk_score.json"
    runner = LongMemEvalChunkRunner(
        dataset_path=dataset_path,
        output_path=output_path,
        checkpoint_path=checkpoint_path,
        score_path=score_path,
        chunk_max_chars=85,
    )

    class FakePool:
        def __init__(self) -> None:
            self.execute: AsyncMock = AsyncMock(return_value="DELETE 0")
            self.fetchrow: AsyncMock = AsyncMock(side_effect=self._fetchrow)
            self.closed: bool = False
            self.created_user_id: uuid.UUID | None = None

        async def _fetchrow(self, _query: str, *args: object) -> dict[str, object] | None:
            if "SELECT id FROM users WHERE email = $1" in _query:
                return None
            if "INSERT INTO users" in _query:
                user_id = args[0]
                assert isinstance(user_id, uuid.UUID)
                self.created_user_id = user_id
                return {"id": args[0]}
            return {"id": uuid.uuid4(), "args": args}

        async def close(self) -> None:
            self.closed = True

    class FakeStore:
        def __init__(self, pool: FakePool, encryption: object) -> None:
            self.pool: FakePool = pool
            self.encryption: object = encryption
            self.create_conversation: AsyncMock = AsyncMock(side_effect=self._create_conversation)

        async def _create_conversation(self, **_kwargs: object) -> dict[str, object]:
            return {"id": uuid.uuid4()}

    expected_chunks = [chunk.content for chunk in build_question_chunks(dataset[0], max_chars=85)]

    fake_pool = FakePool()
    fake_store = FakeStore(fake_pool, object())
    embed_documents_mock = AsyncMock(return_value=[[0.1, 0.2] for _ in range(len(expected_chunks))])
    evaluate_single_mock = AsyncMock(
        return_value={
            "question_id": "q1",
            "question": "What did the user ask about?",
            "reference": "A laptop",
            "hypothesis": "A laptop",
            "category": "IE-user",
            "judgment": "correct",
            "memories_used": 2,
        }
    )

    monkeypatch.setattr(
        "orchestrator.eval.chunk_harness.get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://daemon:daemon@localhost/daemon",
            daemon_encryption_key=Fernet.generate_key().decode(),
            embedding_document_model="voyage-4-large",
        ),
    )
    monkeypatch.setattr(
        "orchestrator.eval.chunk_harness.asyncpg.create_pool",
        AsyncMock(return_value=fake_pool),
    )
    monkeypatch.setattr(
        "orchestrator.eval.chunk_harness.MemoryStore",
        lambda pool, encryption: fake_store,
    )
    monkeypatch.setattr(
        "orchestrator.eval.chunk_harness.build_benchmark_user",
        lambda _run_id: BenchmarkUser(
            user_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            email="longmemeval+fast-test@daemon.test",
            name="longmemeval_fast_test",
        ),
    )
    monkeypatch.setattr(
        "orchestrator.eval.chunk_harness.embed_documents",
        embed_documents_mock,
    )
    monkeypatch.setattr(
        "orchestrator.eval.chunk_harness.evaluate_single",
        evaluate_single_mock,
    )

    results = await runner.run()

    assert results[0]["question_id"] == "q1"
    assert results[0]["chunk_count"] == 2
    assert results[0]["session_count"] == 1
    assert embed_documents_mock.await_args_list[0].args[0] == expected_chunks
    assert fake_store.create_conversation.await_count == 1
    assert fake_pool.fetchrow.await_count == 4
    await_call = evaluate_single_mock.await_args
    assert await_call is not None
    assert await_call.kwargs["allowed_source_conversation_ids"]
    assert await_call.kwargs["log_retrieval"] is True
    assert await_call.kwargs["user_id"] == uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    first_insert_args = fake_pool.fetchrow.await_args_list[2].args[1:]
    first_insert_query = fake_pool.fetchrow.await_args_list[2].args[0]
    assert first_insert_args[0] == uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    assert "to_tsvector('english', $13)" in first_insert_query
    assert first_insert_args[4] == BENCHMARK_MEMORY_CATEGORY
    assert first_insert_args[5] == BENCHMARK_SOURCE_TYPE
    assert first_insert_args[7] == 1.0
    assert first_insert_args[8] == "active"
    assert first_insert_args[9] == 0.5
    assert first_insert_args[10] == "l1"
    metadata = json.loads(first_insert_args[11])
    assert metadata["benchmark"] == BENCHMARK_NAME
    assert metadata["benchmark_source_tag"] == BENCHMARK_NAME
    assert metadata["benchmark_substrate"] == "chunk"
    assert metadata["question_id"] == "q1"
    assert first_insert_args[1] != expected_chunks[0]

    assert fake_pool.execute.await_count == 15
    assert output_path.exists()
    assert checkpoint_path.exists()
    checkpoint_payload = json.loads(checkpoint_path.read_text())
    assert checkpoint_payload["results"][0]["question_id"] == "q1"

    embed_documents_mock.reset_mock()
    evaluate_single_mock.reset_mock()
    fake_pool.fetchrow.reset_mock()

    resumed_results = await runner.run()

    assert resumed_results[0]["question_id"] == "q1"
    assert embed_documents_mock.await_count == 0
    assert evaluate_single_mock.await_count == 0
    assert fake_pool.fetchrow.await_count == 2


def test_chunk_session_messages_default_is_4000_chars() -> None:
    """DEFAULT_CHUNK_MAX_CHARS should be 4000."""
    assert DEFAULT_CHUNK_MAX_CHARS == 4000


def test_chunk_session_messages_default_is_2_turn_overlap() -> None:
    """DEFAULT_OVERLAP_TURNS should be 2."""
    assert DEFAULT_OVERLAP_TURNS == 2


def test_chunk_session_messages_2_turn_overlap() -> None:
    """When a chunk fills, the next chunk starts with the last 2 turns of the previous."""
    messages = [
        {"role": "user", "content": "a1 a2"},
        {"role": "assistant", "content": "b1 b2"},
        {"role": "user", "content": "c1 c2"},
        {"role": "assistant", "content": "d1 d2"},
    ]
    chunks = chunk_session_messages(messages, max_chars=50, overlap_turns=2)

    assert len(chunks) == 2
    assert "[User]: a1 a2" in chunks[0]
    assert "[Assistant]: b1 b2" in chunks[0]
    assert "[User]: c1 c2" in chunks[0]
    assert "[Assistant]: b1 b2" in chunks[1]
    assert "[User]: c1 c2" in chunks[1]
    assert "[Assistant]: d1 d2" in chunks[1]


def test_chunk_session_messages_zero_overlap() -> None:
    """overlap_turns=0 means no overlap between chunks."""
    messages = [
        {"role": "user", "content": "alpha beta gamma"},
        {"role": "assistant", "content": "delta epsilon zeta"},
        {"role": "user", "content": "eta theta iota"},
    ]
    chunks = chunk_session_messages(messages, max_chars=45, overlap_turns=0)

    assert len(chunks) == 3
    assert chunks[0] == "[User]: alpha beta gamma"
    assert chunks[1] == "[Assistant]: delta epsilon zeta"
    assert chunks[2] == "[User]: eta theta iota"


def test_chunk_session_messages_1_turn_overlap() -> None:
    """overlap_turns=1 means exactly 1 turn overlaps between adjacent chunks."""
    messages = [
        {"role": "user", "content": "a1 a2"},
        {"role": "assistant", "content": "b1 b2"},
        {"role": "user", "content": "c1 c2"},
        {"role": "assistant", "content": "d1 d2"},
    ]
    chunks = chunk_session_messages(messages, max_chars=50, overlap_turns=1)

    assert len(chunks) == 2
    assert "[User]: a1 a2" in chunks[0]
    assert "[Assistant]: b1 b2" in chunks[0]
    assert "[User]: c1 c2" in chunks[0]
    assert chunks[1].startswith("[User]: c1 c2")
    assert "[Assistant]: d1 d2" in chunks[1]


def test_chunk_session_messages_overlap_when_fewer_turns_than_overlap() -> None:
    messages = [
        {"role": "user", "content": "only one turn"},
        {"role": "assistant", "content": "second turn here"},
    ]
    chunks = chunk_session_messages(messages, max_chars=10, overlap_turns=2)

    assert len(chunks) == 2
    assert chunks[0] == "[User]: only one turn"
    assert chunks[1] == "[Assistant]: second turn here"


def test_chunk_session_messages_overlap_turns_must_be_non_negative() -> None:
    """negative overlap_turns raises ValueError."""
    messages = [{"role": "user", "content": "test"}]
    with pytest.raises(ValueError, match="non-negative"):
        chunk_session_messages(messages, max_chars=100, overlap_turns=-1)


def test_build_question_chunks_respects_overlap_turns() -> None:
    """build_question_chunks passes overlap_turns through to chunk_session_messages."""
    entry = {
        "question_id": "q_test",
        "haystack_sessions": [
            [
                {"role": "user", "content": "turn one"},
                {"role": "assistant", "content": "turn two"},
                {"role": "user", "content": "turn three"},
                {"role": "assistant", "content": "turn four"},
            ]
        ],
        "haystack_session_ids": ["s1"],
    }
    chunks = build_question_chunks(entry, max_chars=40, overlap_turns=2)

    # With 2-turn overlap and max_chars=40, we expect 3 chunks
    # Chunk 1: turns 1+2, Chunk 2: turns 2+3 (overlap), Chunk 3: turns 3+4 (overlap)
    assert len(chunks) == 3
    assert all(isinstance(c.session_id, str) for c in chunks)
    assert [c.chunk_index for c in chunks] == [0, 1, 2]
    assert [c.session_index for c in chunks] == [0, 0, 0]


def test_legacy_fast_runner_3arg_constructor_derives_score_path(tmp_path: Path) -> None:
    """Back-compat: legacy 3-arg LongMemEvalFastRunner(ds, out, ckpt) must work."""
    from orchestrator.eval.longmemeval_fast import (
        LongMemEvalFastRunner,
        SCORE_FILENAME as CHUNK_SCORE_FILENAME,
    )

    output_path = tmp_path / "fast_results.jsonl"
    runner = LongMemEvalFastRunner(
        dataset_path=tmp_path / "dataset.json",
        output_path=output_path,
        checkpoint_path=tmp_path / "fast_checkpoint.json",
    )

    assert isinstance(runner, LongMemEvalFastRunner)
    assert runner.dataset_path == tmp_path / "dataset.json"
    assert runner.output_path == output_path
    assert runner.checkpoint_path == tmp_path / "fast_checkpoint.json"
    assert runner.score_path == output_path.parent / CHUNK_SCORE_FILENAME


def test_legacy_fast_runner_4arg_constructor_honors_explicit_score_path(
    tmp_path: Path,
) -> None:
    """Explicit score_path is honored over the default."""
    from orchestrator.eval.longmemeval_fast import LongMemEvalFastRunner

    explicit = tmp_path / "custom_score.json"
    runner = LongMemEvalFastRunner(
        dataset_path=tmp_path / "dataset.json",
        output_path=tmp_path / "fast_results.jsonl",
        checkpoint_path=tmp_path / "fast_checkpoint.json",
        score_path=explicit,
    )
    assert runner.score_path == explicit


def test_legacy_fast_shim_cli_strips_run_subcommand(monkeypatch, tmp_path: Path) -> None:
    """The documented ``run --dataset ...`` shape is accepted by the shim."""
    from orchestrator.eval import longmemeval_fast, chunk_harness

    captured: dict[str, object] = {}

    def _fake_main(argv: object) -> None:
        captured["argv"] = argv

    monkeypatch.setattr(chunk_harness, "main", _fake_main)

    longmemeval_fast.main(
        [
            "run",
            "--dataset",
            str(tmp_path / "ds.json"),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    forwarded = captured["argv"]
    assert isinstance(forwarded, list)
    assert forwarded[0] == "--dataset"
    assert "run" not in forwarded


def test_legacy_fast_shim_cli_passes_through_without_run(monkeypatch, tmp_path: Path) -> None:
    """Direct-flag invocation (no ``run`` prefix) is forwarded unchanged."""
    from orchestrator.eval import longmemeval_fast, chunk_harness

    captured: dict[str, object] = {}

    def _fake_main(argv: object) -> None:
        captured["argv"] = argv

    monkeypatch.setattr(chunk_harness, "main", _fake_main)

    longmemeval_fast.main(
        [
            "--dataset",
            str(tmp_path / "ds.json"),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    forwarded = captured["argv"]
    assert isinstance(forwarded, list)
    assert forwarded[0] == "--dataset"
