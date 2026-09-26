from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from orchestrator.config import ProviderConfig, Settings
from orchestrator.daemon import stream_sse_chat


class FakeMemoryStore:
    def __init__(self) -> None:
        self.message_id = uuid.uuid4()
        self.insert_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.row: dict[str, Any] | None = None

    async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
        self.insert_calls.append(kwargs)
        self.row = {"id": self.message_id, **kwargs}
        return {"id": self.message_id}

    async def update_message(self, *, message_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append({"message_id": message_id, **kwargs})
        if self.row is not None:
            self.row.update({key: value for key, value in kwargs.items() if value is not None})
        return self.row or {"id": message_id}


class FakeDedupQueue:
    def __init__(self) -> None:
        self.attempts: list[dict[str, Any]] = []
        self.accepted_job_ids: set[str] = set()

    async def enqueue_job(
        self,
        *args: Any,
        _job_id: str | None = None,
        _defer_by: timedelta | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace | None:
        self.attempts.append(
            {
                "args": args,
                "job_id": _job_id,
                "defer_by": _defer_by,
                "kwargs": kwargs,
            }
        )
        if _job_id is not None and _job_id in self.accepted_job_ids:
            return None
        if _job_id is not None:
            self.accepted_job_ids.add(_job_id)
        return SimpleNamespace(job_id=_job_id)


async def _not_disconnected() -> bool:
    return False


async def _collect_stream(
    store: FakeMemoryStore,
    completion_events: AsyncIterator[dict[str, Any]],
    *,
    queue: FakeDedupQueue | None = None,
    conversation_uuid: uuid.UUID | None = None,
    is_disconnected=None,
) -> list[str]:
    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion_events:
            yield event

    frames: list[str] = []
    effective_conversation_uuid = conversation_uuid or uuid.uuid4()
    with patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools):
        async for frame in stream_sse_chat(
            settings=Settings(mock_llm=False),
            provider_config=ProviderConfig(name="openrouter", model="test-model"),
            system_prompt="system",
            user_message="hello",
            request_id="req_123",
            conversation_id=f"conv_{effective_conversation_uuid.hex}",
            is_disconnected=is_disconnected or _not_disconnected,
            memory_store=store,
            user_id=uuid.uuid4(),
            conversation_uuid=effective_conversation_uuid,
            queue=queue,
        ):
            frames.append(frame)
    return frames


@pytest.mark.asyncio
async def test_real_stream_abort_leaves_streaming_row_with_partial_content() -> None:
    async def aborting_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "partial"}
        raise RuntimeError("simulated stream failure")

    store = FakeMemoryStore()

    frames = await _collect_stream(store, aborting_completion())

    assert len(store.insert_calls) == 1
    assert store.insert_calls[0]["role"] == "assistant"
    assert store.insert_calls[0]["content"] == ""
    assert store.insert_calls[0]["status"] == "streaming"
    assert store.row is not None
    assert store.row["content"] == "partial"
    assert store.row["status"] == "error"
    assert any("event: error" in frame for frame in frames)
    assert any(call.get("status") == "error" for call in store.update_calls)
    assert store.row["status"] == "error"


@pytest.mark.asyncio
async def test_real_completed_stream_updates_single_row_to_complete() -> None:
    async def successful_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "hel"}
        yield {"type": "content_delta", "content": "lo"}
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()

    frames = await _collect_stream(store, successful_completion())

    assert len(store.insert_calls) == 1
    assert store.row is not None
    assert store.row["content"] == "hello"
    assert store.row["status"] == "complete"
    assert sum(1 for call in store.update_calls if call.get("status") == "complete") == 1
    assert any("event: final" in frame for frame in frames)
    assert any("event: done" in frame for frame in frames)


@pytest.mark.asyncio
async def test_streaming_generator_close_forced_status_cancelled() -> None:
    async def streaming_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "partial"}
        event = asyncio.Event()
        await event.wait()

    store = FakeMemoryStore()
    frames: list[str] = []
    conversation_uuid = uuid.uuid4()

    async def collect_stream() -> None:
        async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            async for event in streaming_completion():
                yield event

        with patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools):
            async for frame in stream_sse_chat(
                settings=Settings(mock_llm=False),
                provider_config=ProviderConfig(name="openrouter", model="test-model"),
                system_prompt="system",
                user_message="hello",
                request_id="req_123",
                conversation_id=f"conv_{conversation_uuid.hex}",
                is_disconnected=_not_disconnected,
                memory_store=store,
                user_id=uuid.uuid4(),
                conversation_uuid=conversation_uuid,
            ):
                frames.append(frame)

    stream_task = asyncio.create_task(collect_stream())
    while not store.insert_calls:
        await asyncio.sleep(0)
    while not any("event: token" in frame for frame in frames):
        await asyncio.sleep(0)

    stream_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stream_task

    assert store.row is not None
    assert store.row["status"] == "cancelled"
    assert store.row["content"] == "partial"
    assert any("event: token" in frame for frame in frames)


@pytest.mark.asyncio
async def test_disconnected_stream_updates_status_cancelled_and_skips_extraction() -> None:
    async def partial_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "partial"}
        yield {"type": "content_delta", "content": "more"}

    disconnected: dict[str, int] = {"count": 0}

    async def after_first_disconnect() -> bool:
        disconnected["count"] += 1
        return disconnected["count"] >= 2

    queue = FakeDedupQueue()
    store = FakeMemoryStore()

    frames = await _collect_stream(
        store,
        partial_completion(),
        queue=queue,
        is_disconnected=after_first_disconnect,
    )

    assert store.row is not None
    assert store.row["status"] == "cancelled"
    assert store.row["content"] == "partial"
    extraction_attempts = [
        attempt for attempt in queue.attempts if attempt["args"][0] == "extract_memories"
    ]
    assert extraction_attempts == []
    assert any("event: done" in frame for frame in frames)


@pytest.mark.asyncio
async def test_real_completed_stream_keeps_advisor_traces_on_preinserted_row() -> None:
    async def completion_with_advisor_trace() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "answer"}
        yield {
            "type": "advisor_start",
            "advisor_id": "advisor_1",
            "trace_key": "req_123:advisor_1",
            "event_scope": "advisor",
        }
        yield {
            "type": "advisor_text_delta",
            "advisor_id": "advisor_1",
            "content": "nested advice",
            "event_scope": "advisor",
        }
        yield {
            "type": "advisor_end",
            "advisor_id": "advisor_1",
            "status": "completed",
            "tokens_in": 3,
            "tokens_out": 2,
            "event_scope": "advisor",
        }
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()

    await _collect_stream(store, completion_with_advisor_trace())

    assert len(store.insert_calls) == 1
    final_updates = [call for call in store.update_calls if call.get("status") == "complete"]
    assert len(final_updates) == 1
    advisor_traces = final_updates[0]["advisor_traces"]
    assert advisor_traces["advisor_1"]["text"] == "nested advice"
    assert advisor_traces["advisor_1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_extraction_enqueue_uses_stable_conversation_debounce_key() -> None:
    async def successful_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "answer"}
        yield {"type": "done", "finish_reason": "stop"}

    conversation_uuid = uuid.uuid4()
    queue = FakeDedupQueue()

    for _ in range(5):
        await _collect_stream(
            FakeMemoryStore(),
            successful_completion(),
            queue=queue,
            conversation_uuid=conversation_uuid,
        )

    extraction_attempts = [
        attempt
        for attempt in queue.attempts
        if attempt["args"]
        and attempt["args"][0] == "extract_memories"
        and attempt.get("job_id") == f"extract:{conversation_uuid}"
    ]

    assert len(extraction_attempts) == 5
    assert {attempt["job_id"] for attempt in extraction_attempts} == {
        f"extract:{conversation_uuid}"
    }
    assert all(attempt["defer_by"] == timedelta(seconds=30) for attempt in extraction_attempts)
    # The first duplicate enqueue schedules a follow-up extraction so turns
    # that arrive during an in-flight run are not lost; subsequent duplicates
    # collapse into the same deterministic follow-up _job_id and arq drops them.
    assert queue.accepted_job_ids == {
        f"extract:{conversation_uuid}",
        f"extract:{conversation_uuid}:followup",
    }
    followup_attempts = [
        attempt
        for attempt in queue.attempts
        if attempt.get("job_id") == f"extract:{conversation_uuid}:followup"
    ]
    assert len(followup_attempts) == 4
    assert all(attempt["defer_by"] == timedelta(seconds=60) for attempt in followup_attempts)


def test_extract_memories_worker_registration_does_not_retain_result_key() -> None:
    from orchestrator.worker.worker import worker

    extract_function = worker.functions["extract_memories"]

    assert extract_function.keep_result_s == 0


# --- Explicit disconnect / cancellation must never look like a success (#316) ---
#
# Ported from the #316 repair. Assertions target this branch's lifecycle rather
# than the older "leave the row streaming" convention: every interrupted exit
# path is terminalized by ``terminalize_incomplete_assistant`` with a
# non-complete status, so these tests assert the row is never promoted to
# ``complete`` and that no success-only work runs.


class RecordingTrustSignals:
    """Stand-in for orchestrator.memory.trust_signals to observe call sites."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def apply_implicit_positive_signal(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class DisconnectProbe:
    """Return False for the first ``disconnect_after`` probes, then True."""

    def __init__(self, disconnect_after: int) -> None:
        self.disconnect_after = disconnect_after
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


def _build_stream(
    store: FakeMemoryStore,
    *,
    is_disconnected: Any,
    queue: FakeDedupQueue | None = None,
    conversation_uuid: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    mock_llm: bool = False,
) -> AsyncGenerator[str, None]:
    effective_conversation_uuid = conversation_uuid or uuid.uuid4()
    # stream_sse_chat is an async generator function annotated as AsyncIterator;
    # callers that need aclose (client abort simulation) rely on the generator.
    return cast(
        AsyncGenerator[str, None],
        stream_sse_chat(
            settings=Settings(mock_llm=mock_llm),
            provider_config=ProviderConfig(name="openrouter", model="test-model"),
            system_prompt="system",
            user_message="hello",
            request_id="req_316",
            conversation_id=f"conv_{effective_conversation_uuid.hex}",
            is_disconnected=is_disconnected,
            memory_store=store,
            user_id=user_id or uuid.uuid4(),
            conversation_uuid=effective_conversation_uuid,
            queue=queue,
        ),
    )


def _parse_frames(frames: list[str]) -> list[tuple[str, dict[str, Any]]]:
    parsed: list[tuple[str, dict[str, Any]]] = []
    for frame in frames:
        event_type = ""
        data: list[str] = []
        for line in frame.splitlines():
            if line.startswith("event: "):
                event_type = line[len("event: ") :]
            elif line.startswith("data: "):
                data.append(line[len("data: ") :])
        if event_type and data:
            parsed.append((event_type, json.loads("".join(data))))
    return parsed


def _terminal_data(frames: list[str]) -> dict[str, Any]:
    done_frames = [payload for event_type, payload in _parse_frames(frames) if event_type == "done"]
    assert len(done_frames) == 1, f"expected exactly one done event, got {frames}"
    return done_frames[0]["data"]


def _complete_updates(store: FakeMemoryStore) -> list[dict[str, Any]]:
    return [call for call in store.update_calls if call.get("status") == "complete"]


@pytest.mark.asyncio
async def test_disconnect_before_content_leaves_row_uncompleted_and_reports_cancelled() -> None:
    async def completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "should never be streamed"}
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()
    queue = FakeDedupQueue()
    trust = RecordingTrustSignals()
    probe = DisconnectProbe(disconnect_after=0)

    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion():
            yield event

    frames: list[str] = []
    with (
        patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools),
        patch("orchestrator.daemon._lazy_import_trust_signals", lambda: trust),
    ):
        async for frame in _build_stream(store, is_disconnected=probe, queue=queue):
            frames.append(frame)

    assert len(store.insert_calls) == 1
    assert store.insert_calls[0]["status"] == "streaming"
    assert store.row is not None
    assert store.row["content"] == ""
    assert store.row["status"] == "cancelled"
    assert _complete_updates(store) == []
    event_types = [event_type for event_type, _ in _parse_frames(frames)]
    assert "final" not in event_types
    assert "error" not in event_types
    assert _terminal_data(frames) == {
        "status": "cancelled",
        "reason": "Client disconnected during streaming",
    }
    # No fabricated fallback answer is streamed on cancellation.
    assert not any("I encountered issues while executing tools" in frame for frame in frames)
    assert queue.attempts == []
    assert trust.calls == []


@pytest.mark.asyncio
async def test_disconnect_after_partial_content_keeps_partial_row_available() -> None:
    async def completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "par"}
        yield {"type": "content_delta", "content": "tial"}
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()
    queue = FakeDedupQueue()
    trust = RecordingTrustSignals()
    # Probe 1/2 see a live client, probe 3 (the "done" event) sees the disconnect.
    probe = DisconnectProbe(disconnect_after=2)

    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion():
            yield event

    frames: list[str] = []
    with (
        patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools),
        patch("orchestrator.daemon._lazy_import_trust_signals", lambda: trust),
    ):
        async for frame in _build_stream(store, is_disconnected=probe, queue=queue):
            frames.append(frame)

    # Everything the client actually received stays on the row...
    assert store.row is not None
    assert store.row["content"] == "partial"
    # ...but the interrupted turn is never promoted to complete.
    assert store.row["status"] == "cancelled"
    assert _complete_updates(store) == []
    event_types = [event_type for event_type, _ in _parse_frames(frames)]
    assert "final" not in event_types
    token_texts = [
        payload["data"]["text"]
        for event_type, payload in _parse_frames(frames)
        if event_type == "token"
    ]
    assert "".join(token_texts) == "partial"
    assert _terminal_data(frames)["status"] == "cancelled"
    assert queue.attempts == []
    assert trust.calls == []


@pytest.mark.asyncio
async def test_disconnect_with_interrupted_tool_call_skips_skill_evaluation() -> None:
    async def completion() -> AsyncIterator[dict[str, Any]]:
        for index in range(6):
            yield {"type": "content_delta", "content": "x"}
            yield {"type": "tool_executing", "name": f"tool_{index}", "arguments": "{}"}
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()
    queue = FakeDedupQueue()
    # Accept all six tool calls, exceeding the skill-evaluation threshold, then
    # disconnect before completion. This guards both success-only job paths.
    probe = DisconnectProbe(disconnect_after=12)

    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion():
            yield event

    frames: list[str] = []
    with patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools):
        async for frame in _build_stream(store, is_disconnected=probe, queue=queue):
            frames.append(frame)

    assert store.row is not None
    assert store.row["status"] == "cancelled"
    assert _complete_updates(store) == []
    assert probe.calls == 13
    assert sum(event_type == "tool_call" for event_type, _ in _parse_frames(frames)) == 6
    assert [attempt["args"][0] for attempt in queue.attempts] == []
    assert "final" not in [event_type for event_type, _ in _parse_frames(frames)]


@pytest.mark.asyncio
async def test_mock_mode_disconnect_does_not_persist_complete() -> None:
    store = FakeMemoryStore()
    queue = FakeDedupQueue()
    # Mock mode probes once per emitted character; accept two, then disconnect.
    probe = DisconnectProbe(disconnect_after=2)

    frames: list[str] = []
    async for frame in _build_stream(store, is_disconnected=probe, queue=queue, mock_llm=True):
        frames.append(frame)

    # The streamed characters are never assembled into a completed answer, so the
    # pre-inserted row is never promoted to a completion.
    assert store.row is not None
    assert store.row["status"] == "cancelled"
    assert _complete_updates(store) == []
    assert "final" not in [event_type for event_type, _ in _parse_frames(frames)]
    assert _terminal_data(frames)["status"] == "cancelled"
    assert queue.attempts == []


@pytest.mark.asyncio
async def test_asyncio_cancellation_never_persists_complete() -> None:
    release = asyncio.Event()
    reached_blocking_await = asyncio.Event()

    async def completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "half"}
        reached_blocking_await.set()
        await release.wait()
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()
    queue = FakeDedupQueue()
    frames: list[str] = []

    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion():
            yield event

    async def consume() -> None:
        async for frame in _build_stream(store, is_disconnected=_not_disconnected, queue=queue):
            frames.append(frame)

    with patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools):
        task = asyncio.create_task(consume())
        await asyncio.wait_for(reached_blocking_await.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert store.row is not None
    assert store.row["content"] == "half"
    assert store.row["status"] == "cancelled"
    assert _complete_updates(store) == []
    assert queue.attempts == []
    assert "final" not in [event_type for event_type, _ in _parse_frames(frames)]
    assert "done" not in [event_type for event_type, _ in _parse_frames(frames)]


@pytest.mark.asyncio
async def test_early_generator_close_never_persists_complete() -> None:
    async def completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "par"}
        yield {"type": "content_delta", "content": "tial"}
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()
    queue = FakeDedupQueue()
    frames: list[str] = []

    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion():
            yield event

    with patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools):
        stream = _build_stream(store, is_disconnected=_not_disconnected, queue=queue)
        async for frame in stream:
            frames.append(frame)
            if "event: token" in frame:
                break
        await stream.aclose()

    assert store.row is not None
    # The finalizer preserves the received fragment even before periodic flush.
    assert store.row["content"] == "par"
    assert store.row["status"] == "cancelled"
    assert _complete_updates(store) == []
    assert queue.attempts == []
    assert "final" not in [event_type for event_type, _ in _parse_frames(frames)]
    assert "done" not in [event_type for event_type, _ in _parse_frames(frames)]


@pytest.mark.asyncio
async def test_successful_stream_still_finalises_and_applies_success_work() -> None:
    async def completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "hello"}
        yield {"type": "done", "finish_reason": "stop"}

    store = FakeMemoryStore()
    queue = FakeDedupQueue()
    trust = RecordingTrustSignals()

    async def fake_completion_with_tools(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in completion():
            yield event

    frames: list[str] = []
    with (
        patch("orchestrator.daemon.completion_with_tools", fake_completion_with_tools),
        patch("orchestrator.daemon._lazy_import_trust_signals", lambda: trust),
    ):
        async for frame in _build_stream(store, is_disconnected=_not_disconnected, queue=queue):
            frames.append(frame)

    assert store.row is not None
    assert store.row["content"] == "hello"
    assert store.row["status"] == "complete"
    assert len(_complete_updates(store)) == 1
    event_types = [event_type for event_type, _ in _parse_frames(frames)]
    assert "final" in event_types
    assert _terminal_data(frames) == {"status": "completed"}
    assert [attempt["args"][0] for attempt in queue.attempts] == ["extract_memories"]
    assert len(trust.calls) == 1


@pytest.mark.asyncio
async def test_capacity_refusal_propagates_with_code_and_terminalizes_row() -> None:
    from orchestrator.compute_runtime import ComputeUnavailable
    from orchestrator.entitlements.errors import TrialExhausted

    async def exhausted_completion() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "content": "partial"}
        raise TrialExhausted("trial allowance exhausted: requested 10 of ceiling 0")

    store = FakeMemoryStore()
    with pytest.raises(ComputeUnavailable) as raised:
        await _collect_stream(store, exhausted_completion())

    # The caller maps the sanitized code onto its protocol; no ledger detail leaks.
    assert raised.value.code == "trial_exhausted"
    assert "ceiling" not in raised.value.message
    assert store.row is not None
    assert store.row["status"] == "error"
