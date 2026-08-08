from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from arq import Retry

from orchestrator.memory.store import MemoryStore
from orchestrator.worker.jobs import generate_summary_job


def _messages(count: int) -> list[dict[str, Any]]:
    return [
        {"id": uuid.uuid4(), "role": "user", "content": f"message-{index}"}
        for index in range(count)
    ]


class FakeMemoryStore(MemoryStore):
    def __init__(self, conversation: dict[str, Any]) -> None:
        self.conversation = conversation
        self.messages: list[dict[str, Any]] = []
        self.message_count = int(conversation.get("message_count", 0))
        self.summary_update_result = True
        self.summary_update_error: RuntimeError | None = None
        self.summary_updates: list[dict[str, Any]] = []

    async def get_conversation(self, conversation_id: uuid.UUID) -> dict[str, Any] | None:
        del conversation_id
        return self.conversation

    async def count_messages(self, conversation_id: uuid.UUID) -> int:
        del conversation_id
        return self.message_count

    async def get_summary_message_batch(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        del conversation_id
        return self.messages[:limit]

    async def update_conversation_summary(
        self,
        conversation_id: uuid.UUID,
        *,
        summary: str,
        expected_summary_updated_at: datetime | None,
        summarized_message_count: int,
        summarized_message_ids: list[uuid.UUID],
    ) -> bool:
        del conversation_id
        if self.summary_update_error is not None:
            raise self.summary_update_error
        self.summary_updates.append(
            {
                "summary": summary,
                "expected_summary_updated_at": expected_summary_updated_at,
                "summarized_message_count": summarized_message_count,
                "summarized_message_ids": summarized_message_ids,
            }
        )
        return self.summary_update_result


def _patch_summary_functions(
    monkeypatch: pytest.MonkeyPatch,
    *,
    should_summarize: bool,
    summary: str = "Updated summary",
    observed_messages: list[list[dict[str, Any]]] | None = None,
) -> None:
    async def fake_should_summarize(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return should_summarize

    async def fake_generate_summary(
        messages: list[dict[str, Any]],
        previous_summary: str | None,
        settings: dict[str, Any] | None = None,
    ) -> str:
        del previous_summary, settings
        if observed_messages is not None:
            observed_messages.append(messages)
        return summary

    monkeypatch.setattr(
        "orchestrator.memory.summarization.should_summarize",
        fake_should_summarize,
    )
    monkeypatch.setattr(
        "orchestrator.memory.summarization.generate_summary",
        fake_generate_summary,
    )


@pytest.mark.parametrize(
    ("metadata", "expected_baseline"),
    [
        ({"last_summarized_msg_count": 42}, 42),
        ('{"last_summarized_msg_count": 42}', 42),
        ({"last_summarized_msg_count": "invalid"}, 0),
        ({"last_summarized_msg_count": -1}, 0),
        ({"last_summarized_msg_count": True}, 0),
        ({"last_summarized_msg_count": 1.5}, 0),
        ({"last_summarized_msg_count": 46}, 0),
        ('{"last_summarized_msg_count": Infinity}', 0),
    ],
)
@pytest.mark.asyncio
async def test_generate_summary_job_passes_validated_message_baseline(
    monkeypatch: pytest.MonkeyPatch,
    metadata: object,
    expected_baseline: int,
) -> None:
    conversation_id = uuid.uuid4()
    last_summary_time = datetime.now(timezone.utc)
    store = FakeMemoryStore(
        {
            "id": conversation_id,
            "summary": "Existing summary",
            "summary_updated_at": last_summary_time,
            "metadata": metadata,
            "message_count": 45,
        }
    )
    observed: dict[str, Any] = {}

    async def fake_should_summarize(
        actual_conversation_id: uuid.UUID,
        actual_last_summary_time: datetime | None,
        actual_last_summarized_msg_count: int,
        actual_store: MemoryStore,
        settings: dict[str, Any] | None = None,
    ) -> bool:
        observed.update(
            conversation_id=actual_conversation_id,
            last_summary_time=actual_last_summary_time,
            last_summarized_msg_count=actual_last_summarized_msg_count,
            store=actual_store,
            settings=settings,
        )
        return False

    monkeypatch.setattr(
        "orchestrator.memory.summarization.should_summarize",
        fake_should_summarize,
    )

    result = await generate_summary_job({"store": store}, str(conversation_id))

    assert result == {"status": "skipped", "reason": "thresholds_not_met"}
    assert observed == {
        "conversation_id": conversation_id,
        "last_summary_time": last_summary_time,
        "last_summarized_msg_count": expected_baseline,
        "store": store,
        "settings": {},
    }


@pytest.mark.asyncio
async def test_generate_summary_job_ignores_baseline_without_existing_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid.uuid4()
    store = FakeMemoryStore(
        {
            "id": conversation_id,
            "summary": None,
            "summary_updated_at": None,
            "metadata": {"last_summarized_msg_count": 42},
            "message_count": 45,
        }
    )
    observed_baselines: list[int] = []

    async def fake_should_summarize(
        conversation_id: uuid.UUID,
        last_summary_time: datetime | None,
        last_summarized_msg_count: int,
        store: MemoryStore,
        settings: dict[str, Any] | None = None,
    ) -> bool:
        del conversation_id, last_summary_time, store, settings
        observed_baselines.append(last_summarized_msg_count)
        return False

    monkeypatch.setattr(
        "orchestrator.memory.summarization.should_summarize",
        fake_should_summarize,
    )

    await generate_summary_job({"store": store}, str(conversation_id))

    assert observed_baselines == [0]


@pytest.mark.asyncio
async def test_generate_summary_job_starts_legacy_summary_tracking_from_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid.uuid4()
    store = FakeMemoryStore(
        {
            "id": conversation_id,
            "summary": "Legacy summary",
            "summary_updated_at": datetime.now(timezone.utc),
            "metadata": {},
            "message_count": 45,
        }
    )
    observed_baselines: list[int] = []

    async def fake_should_summarize(
        conversation_id: uuid.UUID,
        last_summary_time: datetime | None,
        last_summarized_msg_count: int,
        store: MemoryStore,
        settings: dict[str, Any] | None = None,
    ) -> bool:
        del conversation_id, last_summary_time, store, settings
        observed_baselines.append(last_summarized_msg_count)
        return False

    monkeypatch.setattr(
        "orchestrator.memory.summarization.should_summarize",
        fake_should_summarize,
    )

    await generate_summary_job({"store": store}, str(conversation_id))

    assert observed_baselines == [0]


@pytest.mark.asyncio
async def test_generate_summary_job_marks_exact_included_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid.uuid4()
    last_summary_time = datetime.now(timezone.utc)
    store = FakeMemoryStore(
        {
            "id": conversation_id,
            "summary": "Existing summary",
            "summary_updated_at": last_summary_time,
            "metadata": {"last_summarized_msg_count": 20},
            "message_count": 27,
        }
    )
    store.messages = _messages(7)
    observed_messages: list[list[dict[str, Any]]] = []
    _patch_summary_functions(
        monkeypatch,
        should_summarize=True,
        observed_messages=observed_messages,
    )

    result = await generate_summary_job({"store": store}, str(conversation_id))

    expected_ids = [message["id"] for message in store.messages]
    assert result == {"status": "success", "summary_length": 15}
    assert observed_messages == [store.messages]
    assert store.summary_updates == [
        {
            "summary": "Updated summary",
            "expected_summary_updated_at": last_summary_time,
            "summarized_message_count": 27,
            "summarized_message_ids": expected_ids,
        }
    ]


@pytest.mark.asyncio
async def test_generate_summary_job_advances_only_through_bounded_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid.uuid4()
    store = FakeMemoryStore(
        {
            "id": conversation_id,
            "summary": None,
            "summary_updated_at": None,
            "metadata": {},
            "message_count": 150,
        }
    )
    store.messages = _messages(150)
    observed_messages: list[list[dict[str, Any]]] = []
    _patch_summary_functions(
        monkeypatch,
        should_summarize=True,
        observed_messages=observed_messages,
    )

    await generate_summary_job({"store": store}, str(conversation_id))

    assert len(observed_messages[0]) == 100
    assert store.summary_updates[0]["summarized_message_count"] == 100
    assert len(store.summary_updates[0]["summarized_message_ids"]) == 100


@pytest.mark.parametrize("empty_summary", ["", "   "])
@pytest.mark.asyncio
async def test_generate_summary_job_retries_empty_summary_without_advancing_baseline(
    monkeypatch: pytest.MonkeyPatch,
    empty_summary: str,
) -> None:
    conversation_id = uuid.uuid4()
    store = FakeMemoryStore(
        {
            "id": conversation_id,
            "summary": "Existing summary",
            "summary_updated_at": datetime.now(timezone.utc),
            "metadata": {"last_summarized_msg_count": 20},
            "message_count": 27,
        }
    )
    store.messages = _messages(7)
    _patch_summary_functions(monkeypatch, should_summarize=True, summary=empty_summary)

    with pytest.raises(Retry):
        await generate_summary_job({"store": store}, str(conversation_id))

    assert store.summary_updates == []


@pytest.mark.parametrize(
    ("update_result", "update_error"),
    [
        (False, None),
        (True, RuntimeError("message inclusion conflict")),
    ],
)
@pytest.mark.asyncio
async def test_generate_summary_job_retries_atomic_update_conflict(
    monkeypatch: pytest.MonkeyPatch,
    update_result: bool,
    update_error: RuntimeError | None,
) -> None:
    conversation_id = uuid.uuid4()
    store = FakeMemoryStore(
        {
            "id": conversation_id,
            "summary": None,
            "summary_updated_at": None,
            "metadata": {},
            "message_count": 20,
        }
    )
    store.messages = _messages(20)
    store.summary_update_result = update_result
    store.summary_update_error = update_error
    _patch_summary_functions(monkeypatch, should_summarize=True)

    with pytest.raises(Retry):
        await generate_summary_job({"store": store}, str(conversation_id))
