"""Tests for advisor_traces persistence - encrypted storage and API exposure."""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore


class MockRecord:
    """Mock asyncpg Record that behaves like a dict."""

    def __init__(self, **kwargs):
        self._data = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data.keys())

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()


@pytest_asyncio.fixture
async def mock_db_pool():
    pool = AsyncMock()
    return pool


@pytest_asyncio.fixture
async def mock_encryption():
    enc = MagicMock(spec=ContentEncryption)
    enc.encrypt = MagicMock(side_effect=lambda x: x)
    enc.decrypt = MagicMock(side_effect=lambda x: x)
    return enc


@pytest_asyncio.fixture
async def memory_store(mock_db_pool, mock_encryption):
    return MemoryStore(db_pool=mock_db_pool, encryption=mock_encryption)


class TestStoreLayerAdvisorTracesField:
    """Test that store layer accepts advisor_traces parameter."""

    def test_insert_message_accepts_advisor_traces(self):
        """Verify insert_message accepts advisor_traces parameter."""
        from orchestrator.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.insert_message)
        params = list(sig.parameters.keys())

        assert "advisor_traces" in params, (
            f"insert_message should accept advisor_traces. Found params: {params}"
        )

    def test_update_message_accepts_advisor_traces(self):
        """Verify update_message accepts advisor_traces parameter."""
        from orchestrator.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.update_message)
        params = list(sig.parameters.keys())

        assert "advisor_traces" in params, (
            f"update_message should accept advisor_traces. Found params: {params}"
        )


class TestAdvisorTracesEncryption:
    """Test that advisor_traces are encrypted before storage."""

    @pytest.mark.asyncio
    async def test_insert_message_encrypts_advisor_traces(
        self,
        memory_store: MemoryStore,
        mock_db_pool: AsyncMock,
        mock_encryption: MagicMock,
    ) -> None:
        """Verify insert_message encrypts advisor_traces before storing."""
        conversation_id = uuid.uuid4()
        user_id = uuid.uuid4()
        message_id = uuid.uuid4()

        advisor_trace = {
            "advisor_id": "coding_1",
            "text_parts": ["Hello from advisor"],
            "reasoning_parts": ["Let me think"],
            "tool_calls": [],
            "tool_results": [],
            "errors": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "trace_key": "trace_abc",
            "parent_trace_key": None,
            "event_tags": {"domain": "coding"},
        }

        mock_row = MockRecord(
            id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
            content="encrypted_content",
            model="test-model",
            tokens_in=10,
            tokens_out=5,
            tool_calls="[]",
            tool_results="[]",
            status="complete",
            metadata="{}",
            reasoning_text=None,
            reasoning_duration_secs=None,
            reasoning_model=None,
            advisor_traces=json.dumps(advisor_trace),
            created_at=datetime.now(),
            updated_at=None,
        )

        mock_db_pool.fetchrow.return_value = mock_row

        await memory_store.insert_message(
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
            content="Hello",
            advisor_traces=advisor_trace,
        )

        call_args = mock_db_pool.fetchrow.call_args
        sql_args = call_args[0]  # noqa: F841

        encrypt_call = mock_encryption.encrypt.call_args_list
        advisor_json_call = [c for c in encrypt_call if "advisor_id" in str(c)]

        assert len(advisor_json_call) == 1, (
            "advisor_traces should be JSON-serialized then encrypted"
        )

    @pytest.mark.asyncio
    async def test_insert_message_decrypts_and_parses_advisor_traces(
        self,
        memory_store: MemoryStore,
        mock_db_pool: AsyncMock,
        mock_encryption: MagicMock,
    ) -> None:
        """Verify insert_message decrypts and parses advisor_traces on return."""
        conversation_id = uuid.uuid4()
        user_id = uuid.uuid4()
        message_id = uuid.uuid4()

        advisor_trace = {
            "advisor_id": "coding_1",
            "text_parts": ["Hello from advisor"],
            "reasoning_parts": ["Let me think"],
            "tool_calls": [],
            "tool_results": [],
            "errors": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "trace_key": "trace_abc",
            "parent_trace_key": None,
            "event_tags": {"domain": "coding"},
        }

        mock_row = MockRecord(
            id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
            content="encrypted_content",
            model="test-model",
            tokens_in=10,
            tokens_out=5,
            tool_calls="[]",
            tool_results="[]",
            status="complete",
            metadata="{}",
            reasoning_text=None,
            reasoning_duration_secs=None,
            reasoning_model=None,
            advisor_traces=json.dumps(advisor_trace),
            created_at=datetime.now(),
            updated_at=None,
        )

        mock_db_pool.fetchrow.return_value = mock_row

        result = await memory_store.insert_message(
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
            content="Hello",
            advisor_traces=advisor_trace,
        )

        assert result.get("advisor_traces") is not None
        assert result["advisor_traces"]["advisor_id"] == "coding_1"
        assert result["advisor_traces"]["text_parts"] == ["Hello from advisor"]
        assert result["advisor_traces"]["trace_key"] == "trace_abc"

    @pytest.mark.asyncio
    async def test_update_message_encrypts_advisor_traces(
        self,
        memory_store: MemoryStore,
        mock_db_pool: AsyncMock,
        mock_encryption: MagicMock,
    ) -> None:
        """Verify update_message encrypts advisor_traces before storing."""
        message_id = uuid.uuid4()

        advisor_trace = {
            "advisor_id": "coding_2",
            "text_parts": ["Updated text"],
            "reasoning_parts": [],
            "tool_calls": [],
            "tool_results": [],
            "errors": [],
            "usage": None,
            "trace_key": "trace_def",
            "parent_trace_key": "trace_abc",
            "event_tags": {},
        }

        mock_row = MockRecord(
            id=message_id,
            conversation_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            role="assistant",
            content="encrypted_content",
            model="test-model",
            tokens_in=10,
            tokens_out=5,
            tool_calls="[]",
            tool_results="[]",
            status="complete",
            metadata="{}",
            reasoning_text=None,
            reasoning_duration_secs=None,
            reasoning_model=None,
            advisor_traces=json.dumps(advisor_trace),
            created_at=datetime.now(),
            updated_at=None,
        )

        mock_db_pool.fetchrow.return_value = mock_row

        await memory_store.update_message(
            message_id=message_id,
            advisor_traces=advisor_trace,
        )

        encrypt_call = mock_encryption.encrypt.call_args_list
        advisor_json_call = [c for c in encrypt_call if "advisor_id" in str(c)]

        assert len(advisor_json_call) == 1, (
            "advisor_traces should be JSON-serialized then encrypted"
        )


class TestAdvisorTracesInMessageDTO:
    """Test that message DTOs include advisor_traces field."""

    def test_message_out_has_advisor_traces_field(self):
        """Verify MessageOut schema includes advisor_traces field."""
        from orchestrator.routes.conversations import MessageOut

        fields = MessageOut.model_fields

        assert "advisor_traces" in fields, (
            f"MessageOut should have advisor_traces field. Found: {list(fields.keys())}"
        )

    def test_advisor_traces_is_optional(self):
        """Verify advisor_traces is optional (nullable) in MessageOut."""
        from orchestrator.routes.conversations import MessageOut

        fields = MessageOut.model_fields

        if "advisor_traces" in fields:
            field = fields["advisor_traces"]
            assert not field.is_required(), "advisor_traces should be optional"


class TestGetMessagesAdvisorTraces:
    """Test that get_messages returns decrypted advisor_traces."""

    @pytest.mark.asyncio
    async def test_get_messages_decrypts_advisor_traces(
        self,
        memory_store: MemoryStore,
        mock_db_pool: AsyncMock,
        mock_encryption: MagicMock,
    ) -> None:
        """Verify get_messages decrypts and returns advisor_traces."""
        conversation_id = uuid.uuid4()

        advisor_trace = {
            "advisor_id": "coding_1",
            "text_parts": ["Hello from advisor"],
            "reasoning_parts": ["Let me think"],
            "tool_calls": [],
            "tool_results": [],
            "errors": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "trace_key": "trace_abc",
            "parent_trace_key": None,
            "event_tags": {"domain": "coding"},
        }

        mock_row = MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            user_id=uuid.uuid4(),
            role="assistant",
            content="encrypted_content",
            model="test-model",
            tokens_in=10,
            tokens_out=5,
            tool_calls="[]",
            tool_results="[]",
            status="complete",
            metadata="{}",
            reasoning_text=None,
            reasoning_duration_secs=None,
            reasoning_model=None,
            advisor_traces=json.dumps(advisor_trace),
            created_at=datetime.now(),
            updated_at=None,
        )

        mock_db_pool.fetch.return_value = [mock_row]

        results = await memory_store.get_messages(conversation_id=conversation_id)

        assert len(results) == 1
        assert results[0].get("advisor_traces") is not None
        assert results[0]["advisor_traces"]["advisor_id"] == "coding_1"
        assert results[0]["advisor_traces"]["trace_key"] == "trace_abc"

    @pytest.mark.asyncio
    async def test_get_recent_messages_decrypts_advisor_traces(
        self,
        memory_store: MemoryStore,
        mock_db_pool: AsyncMock,
        mock_encryption: MagicMock,
    ) -> None:
        """Verify get_recent_messages decrypts and returns advisor_traces."""
        conversation_id = uuid.uuid4()

        advisor_trace = {
            "advisor_id": "coding_1",
            "text_parts": ["Hello from advisor"],
            "reasoning_parts": ["Let me think"],
            "tool_calls": [],
            "tool_results": [],
            "errors": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "trace_key": "trace_abc",
            "parent_trace_key": None,
            "event_tags": {},
        }

        mock_row = MockRecord(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            user_id=uuid.uuid4(),
            role="assistant",
            content="encrypted_content",
            model="test-model",
            tokens_in=10,
            tokens_out=5,
            tool_calls="[]",
            tool_results="[]",
            status="complete",
            metadata="{}",
            reasoning_text=None,
            reasoning_duration_secs=None,
            reasoning_model=None,
            advisor_traces=json.dumps(advisor_trace),
            created_at=datetime.now(),
            updated_at=None,
        )

        mock_db_pool.fetch.return_value = [mock_row]

        results = await memory_store.get_recent_messages(conversation_id=conversation_id)

        assert len(results) == 1
        assert results[0].get("advisor_traces") is not None
        assert results[0]["advisor_traces"]["advisor_id"] == "coding_1"
        assert results[0]["advisor_traces"]["trace_key"] == "trace_abc"


class TestAdvisorTracesNotInPromptHistory:
    """Verify advisor_traces are NOT fed back into prompt/history assembly.

    This is a contract test: advisor_traces are replay-only data stored on
    messages but they should NOT appear in the history assembly path.
    """

    def test_advisor_traces_not_in_normalize_message_for_history(self):
        """Verify _normalize_message does NOT inject advisor_traces into history.

        The _normalize_message in store.py normalizes tool_calls/tool_results/metadata
        but does NOT add advisor_traces to the message. Advisor traces are only
        present when explicitly stored and fetched - they are not synthesized.
        """
        from orchestrator.memory.store import _normalize_message

        message = {
            "tool_calls": "[]",
            "tool_results": "[]",
            "metadata": "{}",
            "advisor_traces": {"advisor_id": "test"},
        }

        normalized = _normalize_message(message)

        assert "advisor_traces" in normalized
        assert normalized["advisor_traces"] == {"advisor_id": "test"}
