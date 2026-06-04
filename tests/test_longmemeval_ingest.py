"""Unit tests for LongMemEval ingestion adapter."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.longmemeval.ingest import (
    TEST_USER_EMAIL,
    TEST_USER_ID,
    TEST_USER_NAME,
    build_corpus_plan,
    build_corpus_key,
    ensure_dataset,
    ingest_session,
    normalize_session_messages,
    poll_extraction_complete,
)


class MockRecord:
    def __init__(self, **kwargs):
        self._data = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return self._data.keys()


@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    return pool


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.create_conversation = AsyncMock(return_value={"id": uuid.uuid4()})
    store.insert_message = AsyncMock(return_value={"id": uuid.uuid4()})
    return store


@pytest.fixture
def sample_session_messages():
    return [
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing well, thank you!"},
        {"role": "user", "content": "I'm looking to buy a new laptop."},
        {"role": "assistant", "content": "What specifications are you looking for?"},
    ]


class TestIngestSession:
    @pytest.mark.asyncio
    async def test_ingest_session_creates_conversation(
        self, mock_store, mock_pool, sample_session_messages
    ):
        conversation_id = uuid.uuid4()
        mock_store.create_conversation.return_value = {"id": conversation_id}

        with patch("tests.longmemeval.ingest.process_extraction", new_callable=AsyncMock):
            with patch(
                "tests.longmemeval.ingest.poll_extraction_complete",
                new_callable=AsyncMock,
                return_value=True,
            ):
                result = await ingest_session(
                    store=mock_store,
                    pool=mock_pool,
                    user_id=TEST_USER_ID,
                    session_id="test_session_1",
                    messages=sample_session_messages,
                    session_index=0,
                )

        assert result["session_id"] == "test_session_1"
        assert result["conversation_id"] == str(conversation_id)
        assert result["message_count"] == len(sample_session_messages)
        mock_store.create_conversation.assert_called_once()

    @pytest.mark.asyncio
    async def test_ingest_session_inserts_all_messages(
        self, mock_store, mock_pool, sample_session_messages
    ):
        mock_store.create_conversation.return_value = {"id": uuid.uuid4()}

        with patch("tests.longmemeval.ingest.process_extraction", new_callable=AsyncMock):
            with patch(
                "tests.longmemeval.ingest.poll_extraction_complete",
                new_callable=AsyncMock,
                return_value=True,
            ):
                await ingest_session(
                    store=mock_store,
                    pool=mock_pool,
                    user_id=TEST_USER_ID,
                    session_id="test_session_2",
                    messages=sample_session_messages,
                    session_index=0,
                )

        assert mock_store.insert_message.call_count == len(sample_session_messages)

    @pytest.mark.asyncio
    async def test_ingest_session_handles_empty_content(self, mock_store, mock_pool):
        messages_with_empty = [
            {"role": "user", "content": "Valid message"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "Another valid"},
        ]
        mock_store.create_conversation.return_value = {"id": uuid.uuid4()}

        with patch("tests.longmemeval.ingest.process_extraction", new_callable=AsyncMock):
            with patch(
                "tests.longmemeval.ingest.poll_extraction_complete",
                new_callable=AsyncMock,
                return_value=True,
            ):
                await ingest_session(
                    store=mock_store,
                    pool=mock_pool,
                    user_id=TEST_USER_ID,
                    session_id="test_session_3",
                    messages=messages_with_empty,
                    session_index=0,
                )

        assert mock_store.insert_message.call_count == 2

    @pytest.mark.asyncio
    async def test_ingest_session_calls_extraction(
        self, mock_store, mock_pool, sample_session_messages
    ):
        mock_store.create_conversation.return_value = {"id": uuid.uuid4()}

        with patch(
            "tests.longmemeval.ingest.process_extraction",
            new_callable=AsyncMock,
        ) as mock_extract:
            with patch(
                "tests.longmemeval.ingest.poll_extraction_complete",
                new_callable=AsyncMock,
                return_value=True,
            ):
                await ingest_session(
                    store=mock_store,
                    pool=mock_pool,
                    user_id=TEST_USER_ID,
                    session_id="test_session_4",
                    messages=sample_session_messages,
                    session_index=0,
                )

            mock_extract.assert_called_once()
            call_kwargs = mock_extract.call_args.kwargs
            assert call_kwargs["user_id"] == TEST_USER_ID
            assert "text" in call_kwargs


class TestPollExtractionComplete:
    @pytest.mark.asyncio
    async def test_poll_returns_true_when_entry_found(self):
        from contextlib import asynccontextmanager

        conversation_id = uuid.uuid4()

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value=MockRecord(
                id=uuid.uuid4(),
                extracted_facts=[{"content": "test fact", "category": "fact"}],
            )
        )

        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.acquire = mock_acquire

        result = await poll_extraction_complete(
            pool=mock_pool,
            conversation_id=conversation_id,
            max_wait_seconds=5,
            poll_interval=0.1,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_poll_returns_false_on_timeout(self):
        from contextlib import asynccontextmanager

        conversation_id = uuid.uuid4()

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.acquire = mock_acquire

        result = await poll_extraction_complete(
            pool=mock_pool,
            conversation_id=conversation_id,
            max_wait_seconds=1,
            poll_interval=0.1,
        )

        assert result is False


class TestEnsureDataset:
    @pytest.mark.asyncio
    async def test_loads_existing_dataset(self):
        import json
        from pathlib import Path
        import tempfile

        test_data = [{"question_id": "test_1"}, {"question_id": "test_2"}]

        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "test_dataset.json"
            with open(test_path, "w") as f:
                json.dump(test_data, f)

            with patch(
                "tests.longmemeval.ingest.DATASET_PATH",
                test_path,
            ):
                result = await ensure_dataset()

            assert result == test_data

    @pytest.mark.asyncio
    async def test_raises_when_dataset_missing_and_no_download(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "nonexistent.json"

            with patch(
                "tests.longmemeval.ingest.DATASET_PATH",
                test_path,
            ):
                with patch(
                    "tests.longmemeval.ingest.DATASET_URL",
                    "http://localhost:9999/nonexistent.json",
                ):
                    with pytest.raises(Exception):
                        await ensure_dataset()


class TestConstants:
    def test_test_user_constants(self):
        assert TEST_USER_EMAIL == "longmemeval@daemon.test"
        assert TEST_USER_NAME == "longmemeval_test_user"
        assert TEST_USER_ID == uuid.UUID("12345678-1234-5678-1234-567812345678")


class TestCorpusPlanning:
    def test_normalize_session_messages_collapses_whitespace(self):
        normalized_a = normalize_session_messages([{"role": "User", "content": "hello   world\n"}])
        normalized_b = normalize_session_messages([{"role": "user", "content": "hello world"}])

        assert normalized_a == normalized_b

    def test_build_corpus_key_deduplicates_equal_session_content(self):
        first = [{"role": "user", "content": "same session"}]
        second = [{"role": "user", "content": "same   session"}]

        assert build_corpus_key(first) == build_corpus_key(second)

    def test_build_corpus_plan_reuses_duplicate_sessions_across_questions(self):
        shared_session = [{"role": "user", "content": "shared fact"}]
        unique_session = [{"role": "assistant", "content": "other fact"}]
        dataset = [
            {
                "question_id": "q1",
                "haystack_session_ids": ["session-a", "session-b"],
                "haystack_sessions": [shared_session, unique_session],
            },
            {
                "question_id": "q2",
                "haystack_session_ids": ["session-c"],
                "haystack_sessions": [[{"role": "user", "content": "shared   fact"}]],
            },
        ]

        plan = build_corpus_plan(dataset)

        assert plan.total_haystack_refs == 3
        assert plan.unique_session_ids == 3
        assert plan.unique_normalized_contents == 2
        assert len(plan.corpus_sessions) == 2

        shared_key = build_corpus_key(shared_session)
        assert plan.question_corpus_refs["q1"] == (
            shared_key,
            build_corpus_key(unique_session),
        )
        assert plan.question_corpus_refs["q2"] == (shared_key,)

        shared_entry = next(
            session for session in plan.corpus_sessions if session.corpus_key == shared_key
        )
        assert shared_entry.canonical_session_id == "session-a"
        assert shared_entry.raw_session_ids == ("session-a", "session-c")
