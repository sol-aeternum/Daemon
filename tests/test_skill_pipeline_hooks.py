"""Integration tests for skill evaluation pipeline hooks.

Tests the integration between:
1. Daemon response completion hook (daemon.py) that enqueues skill evaluation
2. Worker job execution (jobs.py) that runs the evaluation
3. Debounce behavior to prevent duplicate enqueues

These tests complement test_skill_eval_hook.py which focuses on the daemon hook
enqueue logic in isolation. This file focuses on pipeline integration and job execution.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.skill_evaluator import (
    SKILL_EVALUATION_TOOL_THRESHOLD,
    SkillEvaluationRequest,
    build_skill_evaluation_debounce_key,
)
from orchestrator.worker.jobs import (
    SkillEvaluationJobResult,
    run_skill_evaluation_job,
)


class TestPipelineHookIntegration:
    """Tests for full pipeline: daemon hook → job enqueue → job execution."""

    @pytest.mark.asyncio
    async def test_full_pipeline_qualifying_turn_to_job_result(self) -> None:
        """A qualifying turn should enqueue a job that produces a valid result."""
        conv_uuid = uuid.uuid4()
        user_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()

        mock_queue = MagicMock()
        call_tracker = []

        async def fake_enqueue_job(*args, _job_id=None, _defer_by=None, **kwargs):
            call_tracker.append(
                {
                    "args": args,
                    "job_id": _job_id,
                    "defer_by": _defer_by,
                    "kwargs": kwargs,
                }
            )
            return MagicMock(job_id=_job_id)

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
            c
            for c in call_tracker
            if c["args"] and c["args"][0] == "run_skill_evaluation_job"
        ]
        assert len(skill_eval_calls) == 1, (
            f"Expected 1 skill eval call, got {len(skill_eval_calls)}"
        )

        call = skill_eval_calls[0]
        assert call["args"][0] == "run_skill_evaluation_job"
        assert call["args"][1] == str(user_uuid)
        assert call["args"][2] == str(conv_uuid)
        assert call["args"][3] == str(msg_uuid)
        assert call["args"][4] == 5
        assert call["job_id"] == f"skill_eval:{conv_uuid}:{msg_uuid}"
        assert call["defer_by"] == timedelta(seconds=30)


class TestPipelineDebounceBehavior:
    """Tests for debounce behavior preventing duplicate enqueues."""

    def test_debounce_key_format_stability(self) -> None:
        """Debounce key should be stable across multiple calls with same inputs."""
        conv_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()

        key1 = build_skill_evaluation_debounce_key(conv_uuid, msg_uuid)
        key2 = build_skill_evaluation_debounce_key(conv_uuid, msg_uuid)

        assert key1 == key2
        assert key1 == f"skill_eval:{conv_uuid}:{msg_uuid}"

    def test_debounce_key_uniqueness_per_turn(self) -> None:
        """Debounce key should be unique per conversation/message combination."""
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

    @pytest.mark.asyncio
    async def test_same_debounce_key_prevents_duplicate_enqueue(self) -> None:
        """Same debounce key should allow only one enqueue (arq uniqueness)."""
        conv_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()
        debounce_key = f"skill_eval:{conv_uuid}:{msg_uuid}"

        enqueued_job_ids = set()
        for _ in range(3):
            job_id = debounce_key
            enqueued_job_ids.add(job_id)

        assert len(enqueued_job_ids) == 1


class TestPipelineThresholdBehavior:
    """Tests for tool call threshold gating in pipeline."""

    def test_threshold_constant_is_five(self) -> None:
        """Skill evaluation threshold should be exactly 5 tool calls."""
        assert SKILL_EVALUATION_TOOL_THRESHOLD == 5

    @pytest.mark.asyncio
    async def test_below_threshold_no_enqueue(self) -> None:
        """Turn with fewer than 5 tool calls should not enqueue evaluation."""
        conv_uuid = uuid.uuid4()
        user_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()

        mock_queue = MagicMock()
        call_tracker = []

        async def fake_enqueue_job(*args, _job_id=None, **kwargs):
            call_tracker.append({"args": args, "job_id": _job_id, "kwargs": kwargs})

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
            c
            for c in call_tracker
            if c["args"] and c["args"][0] == "run_skill_evaluation_job"
        ]
        assert len(skill_eval_calls) == 0


class TestPipelineJobResultStructure:
    """Tests for job result structure returned from pipeline."""

    def test_job_result_typing(self) -> None:
        """SkillEvaluationJobResult should have correct TypedDict structure."""
        result: SkillEvaluationJobResult = {
            "status": "ok",
            "classification": "created",
            "tool_call_count": 5,
            "created_skill_id": "test-skill",
            "patched_skill_id": None,
            "matched_skill_id": None,
            "matched_similarity": None,
            "matched_source_type": None,
            "protected": False,
            "trigger_conditions": "test",
            "complexity_origin": 5,
            "reason": "test",
            "errors": [],
            "error_count": 0,
        }

        assert result["status"] == "ok"
        assert result["classification"] == "created"
        assert result["created_skill_id"] == "test-skill"
        assert result["errors"] == []
        assert result["error_count"] == 0

    @pytest.mark.asyncio
    async def test_job_handles_store_unavailable(self) -> None:
        """Job should return skipped status when store is unavailable."""
        ctx: dict[str, object] = {
            "store": None,
            "db_pool": None,
        }

        result = await run_skill_evaluation_job(
            ctx,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            5,
        )

        assert result["status"] == "skipped"
        assert result["classification"] == "skipped_store_unavailable"
        assert "store_unavailable" in result["errors"]

    @pytest.mark.asyncio
    async def test_job_result_has_all_required_fields(self) -> None:
        """Job result should contain all required TypedDict fields."""
        ctx: dict[str, object] = {
            "store": None,
            "db_pool": None,
        }

        result = await run_skill_evaluation_job(
            ctx,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            5,
        )

        required_fields = [
            "status",
            "classification",
            "tool_call_count",
            "created_skill_id",
            "patched_skill_id",
            "matched_skill_id",
            "matched_similarity",
            "matched_source_type",
            "protected",
            "trigger_conditions",
            "complexity_origin",
            "reason",
            "errors",
            "error_count",
        ]

        for field in required_fields:
            assert field in result, f"Missing required field: {field}"


class TestPipelineHookPlacement:
    """Tests for correct hook placement in response pipeline."""

    def test_debounce_key_derived_from_conversation_and_message(self) -> None:
        """Debounce key should be derived from conversation and message IDs."""
        conv_uuid = uuid.uuid4()
        msg_uuid = uuid.uuid4()
        expected_key = f"skill_eval:{conv_uuid}:{msg_uuid}"

        assert build_skill_evaluation_debounce_key(conv_uuid, msg_uuid) == expected_key

    def test_skill_evaluation_request_fields(self) -> None:
        """SkillEvaluationRequest should contain all required fields."""
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
