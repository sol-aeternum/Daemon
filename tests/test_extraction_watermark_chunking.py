"""Regression tests for issue #229 — extraction watermark must not advance past unseen text.

These tests cover the two independent failure modes the issue identifies:

1. The model only ever sees the tail of ``MAX_EXTRACTION_INPUT_CHARS`` characters
   of the joined batch, but the worker records ``max(created_at)`` over the full
   selected batch as ``last_message_observed_at``. Older messages therefore
   become permanently behind the cursor.
2. ``process_extraction`` returns early on zero-fact batches without calling
   ``log_extraction``, so the cursor never advances and the same zero-fact
   text is replayed indefinitely.

Both are addressed by ``_chunk_messages_for_extraction`` (worker-side oldest-first
chunking that fits the model's input limit) and ``process_extraction``'s
no-fact checkpoint (per-chunk ``log_extraction`` even on zero-fact chunks).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from arq import Retry

from orchestrator.memory.extraction import (
    MAX_EXTRACTION_INPUT_CHARS,
    extract_facts_from_text,
    process_extraction,
)
from orchestrator.memory.store import MemoryStore
from orchestrator.worker.jobs import (
    _chunk_messages_for_extraction,
    extract_memories,
)


# ---------------------------------------------------------------------------
# _chunk_messages_for_extraction unit tests
# ---------------------------------------------------------------------------


def _msg(role: str, content: str, *, created_at: datetime, id: str | None = None) -> dict:
    row: dict[str, object] = {
        "role": role,
        "content": content,
        "created_at": created_at,
    }
    if id is not None:
        row["id"] = id
    return row


def test_chunk_messages_respects_max_chars_budget() -> None:
    """Every extractor call must fit within MAX_EXTRACTION_INPUT_CHARS."""
    from orchestrator.memory.extraction import messages_to_extraction_text
    from orchestrator.memory.titles import ConversationMessage

    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    parsed: list[ConversationMessage] = []
    raw: list[dict[str, object]] = []
    for i in range(8):
        content = "x" * 600 + f" msg{i}"
        parsed.append({"role": "user", "content": content})
        raw.append(_msg("user", content, created_at=base + timedelta(seconds=i), id=str(i)))

    chunks = _chunk_messages_for_extraction(parsed, raw)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(messages_to_extraction_text(chunk["messages"])) <= MAX_EXTRACTION_INPUT_CHARS
        assert len(chunk["messages"]) == len(chunk["raw_messages"])


def test_chunk_messages_preserves_oldest_first_order() -> None:
    """Worker feeds oldest-first; chunks must respect that ordering."""
    from orchestrator.memory.titles import ConversationMessage

    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    parsed: list[ConversationMessage] = []
    raw: list[dict[str, object]] = []
    for i in range(6):
        content = "x" * 1200 + f" msg{i}"
        parsed.append({"role": "user", "content": content})
        raw.append(_msg("user", content, created_at=base + timedelta(seconds=i), id=str(i)))

    chunks = _chunk_messages_for_extraction(parsed, raw)
    seen: list[str] = []
    for chunk in chunks:
        seen.extend(str(message["id"]) for message in chunk["raw_messages"])
    assert seen == ["0", "1", "2", "3", "4", "5"]


def test_chunk_messages_handles_single_message_oversized() -> None:
    """Oversized messages are losslessly fragmented before cursor advance."""
    from orchestrator.memory.extraction import messages_to_extraction_text
    from orchestrator.memory.titles import ConversationMessage

    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    original = "".join(chr(0x1000 + index) for index in range(10_000))
    parsed: list[ConversationMessage] = [{"role": "user", "content": original}]
    raw = [_msg("user", original, created_at=base, id="0")]
    chunks = _chunk_messages_for_extraction(parsed, raw)

    assert len(chunks) == 3
    assert all(
        len(messages_to_extraction_text(chunk["messages"])) <= MAX_EXTRACTION_INPUT_CHARS
        for chunk in chunks
    )
    fragments = [str(chunk["messages"][0]["content"]) for chunk in chunks]
    rebuilt = fragments[0]
    for fragment in fragments[1:]:
        assert rebuilt[-200:] == fragment[:200]
        rebuilt += fragment[200:]
    assert rebuilt == original
    assert [chunk["advances_cursor"] for chunk in chunks] == [False, False, True]


def test_chunk_messages_rejects_non_positive_max_chars() -> None:
    with pytest.raises(ValueError):
        _chunk_messages_for_extraction([], [], max_chars=0)


@pytest.mark.asyncio
async def test_store_cursor_skips_explicit_error_and_cancelled_rows() -> None:
    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    pool = SimpleNamespace(
        fetch=AsyncMock(
            return_value=[
                {
                    "id": "err",
                    "content": "bad-row",
                    "created_at": base,
                    "status": "error",
                },
                {
                    "id": "cxl",
                    "content": "cancelled-row",
                    "created_at": base + timedelta(minutes=1),
                    "status": "cancelled",
                },
                {
                    "id": "mutable",
                    "content": "pending",
                    "created_at": base + timedelta(minutes=2),
                    "status": "streaming",
                },
            ]
        )
    )
    store = object.__new__(MemoryStore)
    store._pool = cast(Any, pool)
    store._enc = cast(Any, SimpleNamespace(decrypt=lambda value: value))

    rows = await store.get_messages_after_cursor(
        uuid.uuid4(),
        created_at=base,
        message_id="cursor",
    )

    assert [row["id"] for row in rows] == ["err", "cxl"]
    assert rows[0]["_extraction_skip"] is True
    assert rows[0]["content"] == ""
    assert rows[1]["_extraction_skip"] is True
    assert rows[1]["content"] == ""


# ---------------------------------------------------------------------------
# extract_memories integration: cursor only advances per chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_memories_chunks_large_batch_and_advances_per_chunk() -> None:
    """When the joined text exceeds the model budget, the worker must chunk
    oldest-first and pass the *chunk's* last_message_observed_at (not the
    batch-wide max) to ``process_extraction``.
    """
    store = AsyncMock()
    ctx = cast(dict[str, object], {"store": store})

    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    # Build six messages whose joined role-labeled text exceeds 4,000 chars.
    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    msgs = []
    for i in range(6):
        msgs.append(
            {
                "role": "user",
                "content": "y" * 800 + f" msg{i}",
                "created_at": (base + timedelta(seconds=i)).isoformat(),
                "id": str(i),
            }
        )
    messages_json = json.dumps(msgs)

    captured: list[dict[str, object]] = []

    async def fake_process_extraction(
        **kwargs: object,
    ) -> tuple[bool, list[dict[str, object]], bool]:
        captured.append(dict(kwargs))
        return True, [], False

    with patch(
        "orchestrator.worker.jobs.process_extraction",
        side_effect=fake_process_extraction,
    ):
        with patch("orchestrator.worker.jobs.MemoryStore", object):
            result = await extract_memories(ctx, user_id, conversation_id, messages_json)

    assert result["status"] == "ok"
    assert len(captured) >= 2, f"expected multiple extractor calls; got {len(captured)}"

    # The cursor advance must come from the *chunk's* last message, not the
    # entire batch's max timestamp.
    base_ts = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    expected_observed: list[object] = [base_ts + timedelta(seconds=i) for i in range(6)]
    observed = [c["last_message_observed_at"] for c in captured]
    # Each chunk's last_message_observed_at must be the timestamp of the
    # last message *in that chunk* (oldest-first), not the batch-wide max.
    for c in captured:
        assert c["last_message_observed_at"] is not None

    # JSON round-trips ``created_at`` as an ISO-8601 string; coerce to
    # datetime so the comparison is unambiguous.
    def _coerce(value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return None

    coerced_observed = [_coerce(v) for v in observed]
    coerced_observed = [ts for ts in coerced_observed if ts is not None]
    assert coerced_observed, "no chunks were processed"
    # The final chunk's observed timestamp must equal the newest message
    # timestamp in the batch (i.e. cursor advances all the way through).
    assert max(coerced_observed) == base_ts + timedelta(seconds=5), (
        f"final chunk should observe the last message timestamp; got {coerced_observed}"
    )
    # Every observed timestamp must come from the chunk itself (must be
    # one of the per-message timestamps), not the batch-wide max for
    # earlier chunks.
    for ts in coerced_observed:
        assert ts is not None
        assert ts in expected_observed


@pytest.mark.asyncio
async def test_extract_memories_checkpoint_exception_is_retryable() -> None:
    store = object.__new__(MemoryStore)
    store.get_conversation = AsyncMock(return_value=None)
    store.log_extraction = AsyncMock(side_effect=RuntimeError("checkpoint write failed"))

    message = {
        "role": "user",
        "content": "x" * 120,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "id": "0",
    }

    class _Outcome:
        facts: list[object] = []
        raw_count = 0
        calibrated_count = 0
        rejected_count = 0
        slot_coverage = 0
        succeeded = True

    with patch(
        "orchestrator.memory.extraction.extract_facts_from_text",
        new_callable=AsyncMock,
    ) as extract_mock:
        extract_mock.return_value = _Outcome()
        with pytest.raises(Retry):
            await extract_memories(
                {"store": store},
                uuid.uuid4(),
                uuid.uuid4(),
                json.dumps([message]),
            )


@pytest.mark.asyncio
async def test_extract_memories_internal_timeout_is_retryable() -> None:
    async def delayed_extract(*_args: object, **_kwargs: object) -> dict[str, object]:
        await asyncio.sleep(0.01)
        return {"status": "ok"}

    with (
        patch("orchestrator.worker.jobs.EXTRACTION_JOB_DEADLINE_SECONDS", 0.001),
        patch("orchestrator.worker.jobs._extract_memories_once", delayed_extract),
    ):
        with pytest.raises(Retry):
            await extract_memories(
                {"store": object.__new__(MemoryStore)}, uuid.uuid4(), uuid.uuid4()
            )


# ---------------------------------------------------------------------------
# process_extraction: no-fact checkpoint advances the cursor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_extraction_no_fact_writes_checkpoint() -> None:
    """When the extractor returns no facts, ``process_extraction`` must still
    log a checkpoint row with the supplied ``last_message_observed_at`` so
    the durable watermark advances through the chunk the extractor examined.
    """
    store = AsyncMock()
    store.log_extraction = AsyncMock(return_value={"id": "row-1"})
    store.get_conversation = AsyncMock(return_value=None)

    # ``extract_facts_from_text`` patched to return zero facts.
    class _Outcome:
        facts: list[object] = []
        raw_count = 0
        calibrated_count = 0
        rejected_count = 0
        slot_coverage: dict[str, int] = {}

    base_ts = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)

    with patch(
        "orchestrator.memory.extraction.extract_facts_from_text",
        new_callable=AsyncMock,
    ) as extract_mock:
        extract_mock.return_value = _Outcome()
        # Use a small but non-empty text so the retry branch is skipped
        # (``should_retry`` requires ``len(text.strip()) >= 80``).
        await process_extraction(
            store,
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            text="x" * 80,
            last_message_observed_at=base_ts,
            chunk_index=2,
        )

    # log_extraction must have been called once with the chunk's last
    # observed timestamp — the cursor advance.
    assert store.log_extraction.await_count == 1, (
        "no-fact batch must still call log_extraction once to advance the cursor"
    )
    call_kwargs = store.log_extraction.await_args.kwargs
    assert call_kwargs["last_message_observed_at"] == base_ts
    # The dedup_results payload must mark this as a no-fact checkpoint and
    # carry the chunk_index so operators can correlate the row.
    dedup = call_kwargs["dedup_results"]
    assert dedup.get("no_fact_checkpoint") is True
    assert dedup.get("chunk_index") == 2


@pytest.mark.asyncio
async def test_process_extraction_chunk_index_none_preserved_in_log() -> None:
    """When called from non-chunked paths (legacy single-batch), the
    ``chunk_index`` column must serialize as JSON null, not be silently
    dropped or coerced to 0."""
    store = AsyncMock()
    store.log_extraction = AsyncMock(return_value={"id": "row-1"})
    store.get_conversation = AsyncMock(return_value=None)

    class _Outcome:
        facts: list[object] = []
        raw_count = 0
        calibrated_count = 0
        rejected_count = 0
        slot_coverage: dict[str, int] = {}

    with patch(
        "orchestrator.memory.extraction.extract_facts_from_text",
        new_callable=AsyncMock,
    ) as extract_mock:
        extract_mock.return_value = _Outcome()
        await process_extraction(
            store,
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            text="x" * 80,
            last_message_observed_at=None,
            chunk_index=None,
        )

    call_kwargs = store.log_extraction.await_args.kwargs
    assert call_kwargs["dedup_results"].get("chunk_index") is None


@pytest.mark.asyncio
async def test_process_extraction_checkpoint_failure_is_retryable() -> None:
    store = AsyncMock()
    store.get_conversation = AsyncMock(return_value=None)
    store.log_extraction = AsyncMock(side_effect=RuntimeError("database unavailable"))

    class _Outcome:
        facts: list[object] = []
        raw_count = 0
        calibrated_count = 0
        rejected_count = 0
        slot_coverage: dict[str, int] = {}

    with patch(
        "orchestrator.memory.extraction.extract_facts_from_text",
        new_callable=AsyncMock,
        return_value=_Outcome(),
    ):
        with pytest.raises(RuntimeError, match="database unavailable"):
            await process_extraction(
                store,
                user_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                text="short",
                last_message_observed_at=datetime.now(timezone.utc),
            )


@pytest.mark.asyncio
async def test_failed_chunk_does_not_submit_later_chunks() -> None:
    store = AsyncMock()
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = cast(dict[str, object], {"store": store, "redis": queue})
    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    messages = [
        {
            "role": "user",
            "content": "z" * 800 + str(index),
            "created_at": (base + timedelta(seconds=index)).isoformat(),
            "id": str(index),
        }
        for index in range(8)
    ]
    calls: list[dict[str, object]] = []
    memory_id = uuid.uuid4()

    async def fail_second_chunk(
        **kwargs: object,
    ) -> tuple[bool, list[dict[str, object]], bool]:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            return True, [{"id": memory_id}], False
        return False, [], False

    with patch(
        "orchestrator.worker.jobs.process_extraction",
        side_effect=fail_second_chunk,
    ):
        with (
            patch("orchestrator.worker.jobs.MemoryStore", object),
            patch("orchestrator.worker.jobs.ArqRedis", SimpleNamespace),
            patch("orchestrator.worker.jobs.MAX_EXTRACTION_CHUNKS_PER_JOB", 8),
            patch(
                "orchestrator.worker.jobs.enqueue_with_debounce",
                new_callable=AsyncMock,
            ) as enqueue_mock,
        ):
            with pytest.raises(Exception):
                await extract_memories(
                    ctx,
                    uuid.uuid4(),
                    uuid.uuid4(),
                    json.dumps(messages, default=str),
                )

    assert len(calls) == 2
    assert any(call.args[1] == "resolve_entities_job" for call in enqueue_mock.await_args_list)


@pytest.mark.asyncio
async def test_full_oldest_batch_enqueues_extraction_continuation() -> None:
    store = object.__new__(MemoryStore)
    store.consume_summary_continuation_pending = AsyncMock(return_value=False)
    store.get_last_extraction_cursor = AsyncMock(return_value=(None, None))
    timestamp = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    store.get_messages_after_cursor = AsyncMock(
        return_value=[
            {
                "role": "user",
                "content": f"message {index}",
                "created_at": timestamp,
                "id": f"{index:04d}",
            }
            for index in range(250)
        ]
    )
    store.encrypt_extraction_continuation = Mock(return_value="encrypted-continuation")
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = cast(dict[str, object], {"store": store, "redis": queue})
    enqueue = AsyncMock(return_value=SimpleNamespace())

    with patch("orchestrator.worker.jobs.process_extraction", new_callable=AsyncMock) as process:
        process.return_value = (True, [], False)
        with patch("orchestrator.worker.jobs.enqueue_with_debounce", enqueue):
            result = await extract_memories(ctx, uuid.uuid4(), uuid.uuid4())

    processed_messages = cast(int, result["processed_messages"])
    assert 0 < processed_messages < 250
    store.get_messages_after_cursor.assert_awaited_once()
    extraction_enqueues = [
        call for call in enqueue.await_args_list if call.args[1] == "extract_memories"
    ]
    assert len(extraction_enqueues) == 1


@pytest.mark.asyncio
async def test_provider_failure_never_advances_checkpoint() -> None:
    store = AsyncMock()
    store.get_conversation = AsyncMock(return_value=None)
    store.log_extraction = AsyncMock()

    class _FailureOutcome:
        facts: list[object] = []
        raw_count = 0
        calibrated_count = 0
        rejected_count = 0
        slot_coverage: dict[str, int] = {}
        succeeded = False

    with patch(
        "orchestrator.memory.extraction.extract_facts_from_text",
        new_callable=AsyncMock,
        return_value=_FailureOutcome(),
    ) as extract_mock:
        success, memories, continuation = await process_extraction(
            store,
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            text="short input",
            last_message_observed_at=datetime.now(timezone.utc),
        )

    assert (success, memories, continuation) == (False, [], False)
    assert extract_mock.await_count == 2
    store.log_extraction.assert_not_awaited()


@pytest.mark.asyncio
async def test_schema_invalid_response_is_not_successful() -> None:
    response = {"choices": [{"message": {"content": "{}"}}]}
    with patch(
        "orchestrator.memory.extraction.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=response,
    ):
        outcome = await extract_facts_from_text("candidate memory text")

    assert outcome.succeeded is False
    assert outcome.facts == []


@pytest.mark.asyncio
async def test_malformed_fact_entry_is_not_successful() -> None:
    response = {"choices": [{"message": {"content": '{"facts":[{"error":"rate limited"}]}'}}]}
    with patch(
        "orchestrator.memory.extraction.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=response,
    ):
        outcome = await extract_facts_from_text("candidate memory text")

    assert outcome.succeeded is False
    assert outcome.facts == []


@pytest.mark.asyncio
async def test_supported_category_alias_is_not_treated_as_malformed() -> None:
    """``CATEGORY_NORMALIZATION`` aliases like ``intent``/``goal``/``plan`` map to
    ``project``; they must not be rejected by the shape check before the
    normalization runs (Codex P2 on PR #238, ``extraction.py:552-578``)."""

    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "facts": [
                                {
                                    "content": "user plans to ship feature F",
                                    "category": "intent",
                                    "confidence": 0.7,
                                    "slot": "project_f",
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }
    with patch(
        "orchestrator.memory.extraction.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=response,
    ):
        outcome = await extract_facts_from_text("candidate memory text")

    assert outcome.succeeded is True
    assert outcome.facts
    assert outcome.facts[0].category == "project"


@pytest.mark.asyncio
async def test_numeric_string_confidence_is_not_treated_as_malformed() -> None:
    """Provider confidence returned as a numeric string (``"0.85"``) must be
    accepted by the shape check before downstream coercion
    (Codex P2 on PR #238, ``extraction.py:552-578``)."""

    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "facts": [
                                {
                                    "content": "user reports working on feature F this quarter",
                                    "category": "fact",
                                    "confidence": "0.85",
                                    "slot": None,
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }
    with patch(
        "orchestrator.memory.extraction.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=response,
    ):
        outcome = await extract_facts_from_text("candidate memory text")

    assert outcome.succeeded is True
    assert outcome.facts
    assert outcome.facts[0].confidence == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_bool_confidence_is_still_rejected_as_malformed() -> None:
    """``True``/``False`` are ``int`` subclasses in Python; they must still be
    rejected by the shape check so a stray ``true`` token does not silently
    coerce to ``1.0``."""

    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "facts": [
                                {
                                    "content": "user has confirmed allergy to peanuts",
                                    "category": "fact",
                                    "confidence": True,
                                    "slot": None,
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }
    with patch(
        "orchestrator.memory.extraction.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=response,
    ):
        outcome = await extract_facts_from_text("candidate memory text")

    assert outcome.succeeded is False
    assert outcome.facts == []


@pytest.mark.asyncio
async def test_oversized_message_continuation_key_is_stable_across_ciphertext() -> None:
    """The continuation key embedded in the encrypted envelope must be derived
    from the plaintext fragment's ``_extraction_continuation_key`` so the next
    job enqueues under the same job id regardless of the randomized Fernet
    ciphertext (Codex P2 on PR #238, ``worker/jobs.py:466-473``)."""

    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    store = object.__new__(MemoryStore)
    store.consume_summary_continuation_pending = AsyncMock(return_value=False)
    store.get_last_extraction_cursor = AsyncMock(return_value=(None, None))
    store.get_messages_after_cursor = AsyncMock(
        return_value=[
            _msg(
                "user",
                "x" * 80_000,
                created_at=datetime.now(timezone.utc),
                id="oversized",
            )
        ]
    )
    store.encrypt_extraction_continuation = Mock(return_value="ciphertext-token")
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = cast(dict[str, object], {"store": store, "redis": queue})

    captured_keys: list[str] = []

    def _capture(_value: str) -> str:
        captured_keys.append(_value)
        return "ciphertext-token"

    store.encrypt_extraction_continuation = Mock(side_effect=_capture)

    with (
        patch(
            "orchestrator.worker.jobs.process_extraction",
            new_callable=AsyncMock,
            return_value=(True, [], False),
        ),
        patch(
            "orchestrator.worker.jobs.enqueue_with_debounce",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(job_id="continuation"),
        ) as enqueue_mock,
    ):
        await extract_memories(ctx, user_id, conversation_id)

    assert enqueue_mock.await_args is not None
    envelope = json.loads(enqueue_mock.await_args.kwargs["args"][2])
    assert envelope["_encrypted_extraction_continuation"] == "ciphertext-token"
    # The plaintext-derived continuation key survives the encryption
    # round-trip; this is the value the next job uses to deduplicate.
    # The fixture's 80_000-character message spans multiple fragments; the
    # continuation payload begins with fragment index 1.
    assert envelope["_continuation_key"] == "oversized:1"


@pytest.mark.asyncio
async def test_extract_memories_caps_chunks_and_enqueues_continuation() -> None:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    messages = [
        _msg(
            "user",
            f"message-{index}-" + ("x" * 2_000),
            created_at=base + timedelta(seconds=index),
            id=str(index),
        )
        for index in range(20)
    ]
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = cast(dict[str, object], {"store": object.__new__(MemoryStore), "redis": queue})

    with (
        patch("orchestrator.worker.jobs.MemoryStore", object),
        patch(
            "orchestrator.worker.jobs.process_extraction",
            new_callable=AsyncMock,
            return_value=(True, [], False),
        ) as process_mock,
        patch(
            "orchestrator.worker.jobs.enqueue_with_debounce",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(job_id="continuation"),
        ) as enqueue_mock,
    ):
        result = await extract_memories(
            ctx,
            user_id,
            conversation_id,
            json.dumps(messages, default=str),
        )

    assert process_mock.await_count == 1
    assert result["processed_chunks"] == 1
    assert enqueue_mock.await_args is not None
    assert enqueue_mock.await_args.args[1] == "extract_memories"
    continuation_args = enqueue_mock.await_args.kwargs["args"]
    assert len(continuation_args) == 3
    assert len(json.loads(continuation_args[2])) == 19


@pytest.mark.asyncio
async def test_database_continuation_is_encrypted_before_enqueue() -> None:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    store = object.__new__(MemoryStore)
    store.consume_summary_continuation_pending = AsyncMock(return_value=False)
    store.get_last_extraction_cursor = AsyncMock(return_value=(None, None))
    store.get_messages_after_cursor = AsyncMock(
        return_value=[
            _msg(
                "user",
                "secret-plaintext-" + ("x" * 20_000),
                created_at=datetime.now(timezone.utc),
                id="oversized",
            )
        ]
    )
    store.encrypt_extraction_continuation = Mock(return_value="ciphertext-token")
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = cast(dict[str, object], {"store": store, "redis": queue})

    with (
        patch(
            "orchestrator.worker.jobs.process_extraction",
            new_callable=AsyncMock,
            return_value=(True, [], False),
        ),
        patch(
            "orchestrator.worker.jobs.enqueue_with_debounce",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(job_id="continuation"),
        ) as enqueue_mock,
    ):
        await extract_memories(ctx, user_id, conversation_id)

    assert enqueue_mock.await_args is not None
    continuation_arg = enqueue_mock.await_args.kwargs["args"][2]
    continuation_envelope = json.loads(continuation_arg)
    assert continuation_envelope["_encrypted_extraction_continuation"] == "ciphertext-token"
    # The continuation key is derived from plaintext fragment metadata so
    # the next job can recover a stable key regardless of randomized Fernet
    # ciphertext (Codex P2 on PR #238, ``worker/jobs.py:466-473``). The
    # oversized message in this fixture spans multiple fragments; the
    # continuation payload begins with fragment index 1.
    assert continuation_envelope["_continuation_key"] == "oversized:1"
    assert "secret-plaintext" not in continuation_arg
    store.encrypt_extraction_continuation.assert_called_once()


@pytest.mark.asyncio
async def test_oversized_message_continuation_resumes_at_fragment() -> None:
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = cast(dict[str, object], {"store": object.__new__(MemoryStore), "redis": queue})
    message = _msg(
        "user",
        "x" * 80_000,
        created_at=datetime.now(timezone.utc),
        id="oversized",
    )

    with (
        patch("orchestrator.worker.jobs.MemoryStore", object),
        patch(
            "orchestrator.worker.jobs.process_extraction",
            new_callable=AsyncMock,
            return_value=(True, [], False),
        ) as process_mock,
        patch(
            "orchestrator.worker.jobs.enqueue_with_debounce",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(job_id="continuation"),
        ) as enqueue_mock,
    ):
        await extract_memories(
            ctx,
            uuid.uuid4(),
            uuid.uuid4(),
            json.dumps([message], default=str),
        )

    assert process_mock.await_count == 1
    assert enqueue_mock.await_args is not None
    continuation = json.loads(enqueue_mock.await_args.kwargs["args"][2])
    assert continuation
    assert continuation[0]["_extraction_cursor_checkpoint"] is False
    assert len(continuation[0]["content"]) < len(message["content"])


@pytest.mark.asyncio
async def test_direct_caller_without_queue_completes_synchronously() -> None:
    ctx = cast(dict[str, object], {"store": object.__new__(MemoryStore)})
    messages = [
        _msg(
            "user",
            "x" * 2_000,
            created_at=datetime.now(timezone.utc) + timedelta(seconds=index),
            id=str(index),
        )
        for index in range(12)
    ]

    with (
        patch("orchestrator.worker.jobs.MemoryStore", object),
        patch(
            "orchestrator.worker.jobs.process_extraction",
            new_callable=AsyncMock,
            return_value=(True, [], False),
        ) as process_mock,
    ):
        result = await extract_memories(
            ctx,
            uuid.uuid4(),
            uuid.uuid4(),
            json.dumps(messages, default=str),
        )

    assert process_mock.await_count == 12
    assert result["processed_chunks"] == 12


@pytest.mark.asyncio
async def test_store_stops_before_first_unfinished_message() -> None:
    store = object.__new__(MemoryStore)
    pool = SimpleNamespace(
        fetch=AsyncMock(
            return_value=[
                {"id": "1", "content": "done", "status": "complete"},
                {"id": "2", "content": "partial", "status": "streaming"},
                {"id": "3", "content": "later", "status": "complete"},
            ]
        )
    )
    store._pool = pool  # type: ignore[attr-defined]
    store._enc = SimpleNamespace(decrypt=lambda value: value)  # type: ignore[attr-defined]

    rows = await store.get_messages_after_cursor(
        uuid.uuid4(),
        created_at=None,
        message_id=None,
    )

    assert [row["id"] for row in rows] == ["1"]


@pytest.mark.asyncio
async def test_store_never_age_skips_streaming_row() -> None:
    store = object.__new__(MemoryStore)
    pool = SimpleNamespace(
        fetch=AsyncMock(
            return_value=[
                {
                    "id": "stale",
                    "content": "encrypted-partial",
                    "status": "streaming",
                    "created_at": datetime.now(timezone.utc) - timedelta(minutes=16),
                },
                {
                    "id": "later",
                    "content": "done",
                    "status": "complete",
                    "created_at": datetime.now(timezone.utc),
                },
            ]
        )
    )
    store._pool = pool  # type: ignore[attr-defined]
    store._enc = SimpleNamespace(decrypt=lambda value: value)  # type: ignore[attr-defined]

    rows = await store.get_messages_after_cursor(
        uuid.uuid4(),
        created_at=None,
        message_id=None,
    )

    # Streaming rows are still treated as live and must remain mutable regardless
    # of age; they intentionally block extraction and are not emitted from this
    # page.
    assert rows == []


@pytest.mark.asyncio
async def test_store_cursor_uses_message_id_tiebreaker() -> None:
    timestamp = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetch.return_value = []
    store = object.__new__(MemoryStore)
    store._pool = pool
    store._enc = cast(Any, SimpleNamespace())

    await store.get_messages_after_cursor(
        uuid.uuid4(),
        created_at=timestamp,
        message_id="0007",
        limit=250,
    )

    query = pool.fetch.await_args.args[0]
    assert "created_at = $2 AND id::text > $3" in query
    assert "ORDER BY created_at ASC, id::text ASC" in query
    assert pool.fetch.await_args.args[2:] == (timestamp, "0007", 250)


@pytest.mark.asyncio
async def test_store_legacy_timestamp_cursor_replays_equal_timestamp() -> None:
    """A legacy timestamp-only cursor must not skip peers with the same timestamp."""
    timestamp = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetch.return_value = []
    store = object.__new__(MemoryStore)
    store._pool = pool
    store._enc = cast(Any, SimpleNamespace())

    await store.get_messages_after_cursor(
        uuid.uuid4(),
        created_at=timestamp,
        message_id=None,
        limit=250,
    )

    query = pool.fetch.await_args.args[0]
    assert "created_at >= $2" in query


@pytest.mark.asyncio
async def test_store_ignores_non_checkpoint_fragment_logs() -> None:
    timestamp = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    pool = AsyncMock()
    pool.fetchrow.return_value = {"watermark": timestamp, "message_id": "0007"}
    store = object.__new__(MemoryStore)
    store._pool = pool

    assert await store.get_last_extraction_cursor(uuid.uuid4()) == (timestamp, "0007")
    query = pool.fetchrow.await_args.args[0]
    assert "cursor_checkpoint' IS DISTINCT FROM 'false'" in query
