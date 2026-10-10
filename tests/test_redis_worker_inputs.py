"""Checked identifier re-reads: fictional inputs, no inference/network."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from arq import Retry

from orchestrator.worker.jobs import _extraction_source_version, _read_extraction_references

OWNER = uuid.UUID("00000000-0000-4000-8000-000000000001")
CONVERSATION = uuid.UUID("00000000-0000-4000-8000-000000000002")
MESSAGE = uuid.UUID("00000000-0000-4000-8000-000000000003")


class SourceStore:
    def __init__(self, source: dict | None) -> None:
        self.reads = AsyncMock(return_value=source)

    async def get_owned_message(
        self, message_id: uuid.UUID, *, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> dict | None:
        return await self.reads(message_id, user_id=user_id, conversation_id=conversation_id)


def message(content: str) -> dict:
    return {
        "id": MESSAGE,
        "conversation_id": CONVERSATION,
        "user_id": OWNER,
        "created_at": datetime(2026, 10, 9, tzinfo=timezone.utc),
        "role": "user",
        "content": content,
    }


def envelope(source: dict, index: int) -> dict:
    return {
        "_extraction_refs": [
            {
                "message_id": str(source["id"]),
                "source_version": _extraction_source_version(source),
                "fragment_index": index,
            }
        ]
    }


@pytest.mark.asyncio
async def test_unchanged_source_resumes_exact_fragment_without_content_payload() -> None:
    original = message("FICTIONAL_CONTENT_SENTINEL" + "x" * 80_000)
    reference = envelope(original, 1)
    assert "FICTIONAL_CONTENT_SENTINEL" not in json.dumps(reference)
    store = SourceStore(original)
    raw = await _read_extraction_references(store, OWNER, CONVERSATION, reference)
    assert raw[0]["_extraction_continuation_key"] == f"{MESSAGE}:1"
    assert "FICTIONAL_CONTENT_SENTINEL" not in raw[0]["content"]
    assert raw[-1]["_extraction_cursor_checkpoint"] is True
    assert raw[0]["_resume_database_extraction"] is True
    store.reads.assert_awaited_once_with(MESSAGE, user_id=OWNER, conversation_id=CONVERSATION)


@pytest.mark.asyncio
async def test_changed_source_restarts_from_beginning_even_if_now_shorter() -> None:
    original = message("x" * 80_000)
    changed = message("replacement fictional text")
    store = SourceStore(changed)
    raw = await _read_extraction_references(store, OWNER, CONVERSATION, envelope(original, 4))
    assert raw[0]["content"] == changed["content"]
    assert raw[0]["_source_version"] == _extraction_source_version(changed)


@pytest.mark.asyncio
async def test_missing_or_mismatched_owned_source_never_advances() -> None:
    store = SourceStore(None)
    with pytest.raises(Retry):
        await _read_extraction_references(store, OWNER, CONVERSATION, envelope(message("text"), 0))


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [-1, True, "1", 100])
async def test_invalid_fragment_rejected(index: object) -> None:
    source = message("text")
    reference = envelope(source, 0)
    reference["_extraction_refs"][0]["fragment_index"] = index
    store = SourceStore(source)
    with pytest.raises(ValueError):
        await _read_extraction_references(store, OWNER, CONVERSATION, reference)


@pytest.mark.asyncio
async def test_references_reject_duplicates_and_content_fields() -> None:
    source = message("fictional source")
    reference = envelope(source, 0)
    reference["_extraction_refs"][0]["content"] = "content must not enter descriptors"
    store = SourceStore(source)
    with pytest.raises(ValueError):
        await _read_extraction_references(store, OWNER, CONVERSATION, reference)
    reference = envelope(source, 0)
    reference["_extraction_refs"] *= 2
    with pytest.raises(ValueError):
        await _read_extraction_references(store, OWNER, CONVERSATION, reference)
