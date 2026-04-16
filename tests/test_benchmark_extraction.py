from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from tests import benchmark_extraction


def test_build_benchmark_transcript_is_user_only() -> None:
    transcript = benchmark_extraction.build_benchmark_transcript(
        ["first turn", "second turn"]
    )

    assert transcript == [
        {"role": "user", "content": "first turn"},
        {"role": "user", "content": "second turn"},
    ]


def test_ensure_benchmark_user_sync_wrapper_uses_async_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_user_id = str(uuid.uuid4())
    captured_user_ids: list[str] = []

    async def fake_ensure(user_id: str) -> None:
        captured_user_ids.append(user_id)

    def fake_asyncio_run(coro: Any) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(
        benchmark_extraction, "_ensure_benchmark_user_async", fake_ensure
    )
    monkeypatch.setattr(benchmark_extraction.asyncio, "run", fake_asyncio_run)

    benchmark_extraction._ensure_benchmark_user(expected_user_id)

    assert captured_user_ids == [expected_user_id]


@pytest.mark.asyncio
async def test_replay_benchmark_conversation_uses_cumulative_user_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid.uuid4()
    expected_conversation_id = str(conversation_id)
    user_id = str(uuid.uuid4())
    expected_user_id = user_id
    captured_extractions: list[list[dict[str, str]]] = []
    inserted_messages: list[dict[str, Any]] = []
    updated_conversations: list[dict[str, Any]] = []

    class FakePool:
        async def close(self) -> None:
            return None

    class FakeStore:
        def __init__(self, db_pool: Any, encryption: Any) -> None:
            self.db_pool = db_pool
            self.encryption = encryption

        async def create_conversation(
            self, user_uuid: uuid.UUID, title: str | None = None
        ) -> dict[str, Any]:
            assert user_uuid == uuid.UUID(user_id)
            assert title == "Benchmark"
            return {"id": conversation_id}

        async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
            inserted_messages.append(kwargs)
            return kwargs

        async def update_conversation(
            self, conversation_uuid: uuid.UUID, **kwargs: Any
        ) -> dict[str, Any]:
            updated_conversations.append(
                {"conversation_id": conversation_uuid, **kwargs}
            )
            return {"id": conversation_uuid}

    async def fake_create_pool(*, dsn: str, min_size: int, max_size: int) -> FakePool:
        assert dsn == "postgresql://bench"
        assert min_size == 1
        assert max_size == 4
        return FakePool()

    async def fake_invoke_benchmark_extraction(
        *,
        store: Any,
        user_id: str,
        conversation_id: str,
        messages_json: list[dict[str, str]],
    ) -> dict[str, object]:
        assert isinstance(store, FakeStore)
        assert user_id == expected_user_id
        assert conversation_id == expected_conversation_id
        captured_extractions.append(messages_json)
        return {"status": "ok", "processed_messages": len(messages_json)}

    class FakeAsyncpgModule:
        @staticmethod
        async def create_pool(*, dsn: str, min_size: int, max_size: int) -> FakePool:
            return await fake_create_pool(dsn=dsn, min_size=min_size, max_size=max_size)

    monkeypatch.setattr(benchmark_extraction, "asyncpg", FakeAsyncpgModule())

    async def fake_ensure_benchmark_user_async(user_id: str) -> None:
        assert user_id == expected_user_id

    monkeypatch.setattr(
        benchmark_extraction,
        "_ensure_benchmark_user_async",
        fake_ensure_benchmark_user_async,
    )
    monkeypatch.setattr(
        benchmark_extraction,
        "invoke_benchmark_extraction",
        fake_invoke_benchmark_extraction,
    )
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", "")

    import orchestrator.memory.store as store_module

    monkeypatch.setattr(store_module, "MemoryStore", FakeStore)

    replayed_conversation_id = await benchmark_extraction.replay_benchmark_conversation(
        messages=["first turn", "second turn", "third turn"],
        user_id=user_id,
        db_url="postgresql://bench",
    )

    assert replayed_conversation_id == str(conversation_id)
    assert [message["role"] for message in inserted_messages] == [
        "user",
        "user",
        "user",
    ]
    assert [message["content"] for message in inserted_messages] == [
        "first turn",
        "second turn",
        "third turn",
    ]
    assert captured_extractions == [
        [{"role": "user", "content": "first turn"}],
        [
            {"role": "user", "content": "first turn"},
            {"role": "user", "content": "second turn"},
        ],
        [
            {"role": "user", "content": "first turn"},
            {"role": "user", "content": "second turn"},
            {"role": "user", "content": "third turn"},
        ],
    ]
    assert len(updated_conversations) == 3
    assert all(
        update["metadata_patch"]["benchmark_transcript_mode"] == "user_only_replay"
        for update in updated_conversations
    )
