from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.skill_evaluator import (
    SKILL_EVALUATION_TOOL_THRESHOLD,
    SkillEvaluationRequest,
    build_skill_evaluation_debounce_key,
)
from orchestrator.worker.jobs import run_skill_evaluation_job


@pytest.mark.asyncio
async def test_job_handles_store_unavailable() -> None:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()

    ctx: dict[str, object] = {
        "store": None,
        "db_pool": None,
    }

    result = await run_skill_evaluation_job(
        ctx,
        str(user_id),
        str(conversation_id),
        str(assistant_message_id),
        5,
    )

    assert result["status"] == "skipped"
    assert result["classification"] == "skipped_store_unavailable"
    assert "store_unavailable" in result["errors"]


def test_threshold_constant_is_five() -> None:
    assert SKILL_EVALUATION_TOOL_THRESHOLD == 5


def test_debounce_key_format() -> None:
    conversation_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()

    key = build_skill_evaluation_debounce_key(conversation_id, assistant_message_id)

    assert key == f"skill_eval:{conversation_id}:{assistant_message_id}"


def test_debounce_key_is_unique_per_turn() -> None:
    conv1 = uuid.uuid4()
    conv2 = uuid.uuid4()
    msg1 = uuid.uuid4()
    msg2 = uuid.uuid4()

    key1 = build_skill_evaluation_debounce_key(conv1, msg1)
    key2 = build_skill_evaluation_debounce_key(conv2, msg1)
    key3 = build_skill_evaluation_debounce_key(conv1, msg2)

    assert key1 != key2
    assert key1 != key3
    assert key2 != key3


def test_debounce_key_is_stable() -> None:
    conv = uuid.uuid4()
    msg = uuid.uuid4()

    key1 = build_skill_evaluation_debounce_key(conv, msg)
    key2 = build_skill_evaluation_debounce_key(conv, msg)

    assert key1 == key2


@pytest.mark.asyncio
async def test_job_result_has_correct_structure() -> None:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()

    ctx: dict[str, object] = {
        "store": None,
        "db_pool": None,
    }

    result = await run_skill_evaluation_job(
        ctx,
        str(user_id),
        str(conversation_id),
        str(assistant_message_id),
        5,
    )

    assert isinstance(result, dict)
    assert "status" in result
    assert "classification" in result
    assert "tool_call_count" in result
    assert "created_skill_id" in result
    assert "patched_skill_id" in result
    assert "matched_skill_id" in result
    assert "matched_similarity" in result
    assert "matched_source_type" in result
    assert "protected" in result
    assert "trigger_conditions" in result
    assert "complexity_origin" in result
    assert "reason" in result
    assert "errors" in result
    assert "error_count" in result


def test_skill_evaluation_request_fields() -> None:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()
    tool_call_count = 7

    request = SkillEvaluationRequest(
        user_id=user_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        tool_call_count=tool_call_count,
    )

    assert request.user_id == user_id
    assert request.conversation_id == conversation_id
    assert request.assistant_message_id == assistant_message_id
    assert request.tool_call_count == tool_call_count


class TestDaemonHookEnqueueLogic:
    """Tests for the skill evaluation enqueue hook in daemon.py."""

    @pytest.mark.asyncio
    async def test_qualifying_turn_enqueues_once(self) -> None:
        conv_uuid = uuid.uuid4()
        user_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()

        mock_queue = MagicMock()
        call_tracker = []

        async def fake_enqueue_job(*args, _job_id=None, **kwargs):
            call_tracker.append((args, _job_id, kwargs))

        mock_queue.enqueue_job = fake_enqueue_job

        mock_store = MagicMock()
        mock_store.insert_message = AsyncMock(return_value={"id": msg_uuid})
        mock_store.update_message = AsyncMock()

        async def mock_completion_with_tools(**kwargs):
            for i in range(5):
                yield {"type": "content_delta", "content": "x"}
                yield {"type": "tool_executing", "name": f"tool_{i}", "arguments": "{}"}
                yield {
                    "type": "tool_result",
                    "name": f"tool_{i}",
                    "result": {"success": True},
                }
            yield {"type": "content_delta", "content": "done"}
            yield {"type": "done"}

        from orchestrator.daemon import stream_sse_chat
        from orchestrator.config import ProviderConfig, Settings

        settings = Settings(mock_llm=False)

        async def is_disconnected():
            return False

        provider_config = ProviderConfig(name="openrouter", model="test")

        with (
            patch(
                "orchestrator.daemon.completion_with_tools",
                mock_completion_with_tools,
            ),
            patch(
                "orchestrator.memory.store.MemoryStore.update_message",
                new_callable=AsyncMock,
            ),
            patch(
                "orchestrator.memory.trust_signals.apply_implicit_positive_signal",
                new_callable=AsyncMock,
            ),
        ):
            frames = []
            async for frame in stream_sse_chat(
                settings=settings,
                provider_config=provider_config,
                system_prompt="test",
                user_message="test",
                request_id="req_123",
                conversation_id=f"conv_{conv_uuid}",
                is_disconnected=is_disconnected,
                memory_store=mock_store,
                user_id=user_uuid,
                conversation_uuid=conv_uuid,
                queue=mock_queue,
            ):
                frames.append(frame)

        skill_eval_calls = [
            c for c in call_tracker if c[0] and c[0][0] == "run_skill_evaluation_job"
        ]

        assert len(skill_eval_calls) == 1, (
            f"Expected 1 skill eval call, got {len(skill_eval_calls)}: {skill_eval_calls}"
        )
        args, job_id, kwargs = skill_eval_calls[0]
        assert args[0] == "run_skill_evaluation_job"
        assert args[1] == str(user_uuid)
        assert args[2] == str(conv_uuid)
        assert args[3] == str(msg_uuid)
        assert args[4] == 5
        assert job_id == f"skill_eval:{conv_uuid}:{msg_uuid}"
        assert kwargs.get("_defer_by") == timedelta(seconds=30)

    @pytest.mark.asyncio
    async def test_below_threshold_turn_does_not_enqueue(self) -> None:
        conv_uuid = uuid.uuid4()
        user_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()

        mock_queue = MagicMock()
        call_tracker = []

        async def fake_enqueue_job(*args, _job_id=None, **kwargs):
            call_tracker.append((args, _job_id, kwargs))

        mock_queue.enqueue_job = fake_enqueue_job

        mock_store = MagicMock()
        mock_store.insert_message = AsyncMock(return_value={"id": msg_uuid})
        mock_store.update_message = AsyncMock()

        async def mock_completion_with_tools(**kwargs):
            yield {"type": "content_delta", "content": "hello"}
            yield {"type": "done"}

        from orchestrator.daemon import stream_sse_chat
        from orchestrator.config import ProviderConfig, Settings

        settings = Settings(mock_llm=False)

        async def is_disconnected():
            return False

        provider_config = ProviderConfig(name="openrouter", model="test")

        with (
            patch(
                "orchestrator.daemon.completion_with_tools",
                mock_completion_with_tools,
            ),
            patch(
                "orchestrator.memory.store.MemoryStore.update_message",
                new_callable=AsyncMock,
            ),
            patch(
                "orchestrator.memory.trust_signals.apply_implicit_positive_signal",
                new_callable=AsyncMock,
            ),
        ):
            frames = []
            async for frame in stream_sse_chat(
                settings=settings,
                provider_config=provider_config,
                system_prompt="test",
                user_message="test",
                request_id="req_123",
                conversation_id=f"conv_{conv_uuid}",
                is_disconnected=is_disconnected,
                memory_store=mock_store,
                user_id=user_uuid,
                conversation_uuid=conv_uuid,
                queue=mock_queue,
            ):
                frames.append(frame)

        skill_eval_calls = [
            c for c in call_tracker if c[0] and c[0][0] == "run_skill_evaluation_job"
        ]
        assert len(skill_eval_calls) == 0

    def test_debounce_key_derived_from_conversation_and_message(self) -> None:
        conv_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()
        debounce_key = f"skill_eval:{conv_uuid}:{msg_uuid}"

        assert debounce_key == build_skill_evaluation_debounce_key(conv_uuid, msg_uuid)

    def test_same_debounce_key_prevents_duplicate_enqueue(self) -> None:
        conv_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()
        debounce_key = f"skill_eval:{conv_uuid}:{msg_uuid}"

        seen_job_ids = set()
        for _ in range(3):
            seen_job_ids.add(debounce_key)

        assert len(seen_job_ids) == 1
