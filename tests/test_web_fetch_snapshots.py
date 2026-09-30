"""Bounded reader contract, without external HTTP or inference."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from orchestrator.tools.web_fetch import WebFetchTool
from orchestrator.services.fetch.models import FetchPageTooLargeError, FetchExtractionError
from orchestrator.services.web_snapshots import WebSnapshotNotFound, WebSnapshotStore


@pytest.fixture
def reader():
    now = datetime.now(timezone.utc)
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        source_url="https://example.com/Page?ID=A",
        final_url="https://example.com/Page?ID=A",
        title="Page",
        extract_mode="article",
        retrieved_at=now,
        expires_at=now + timedelta(days=30),
        content='界\\"needle' * 2000,
    )
    snapshot.content_chars = len(snapshot.content)
    store = SimpleNamespace(
        settings=SimpleNamespace(
            web_snapshot_max_new_per_turn=2,
            web_snapshot_default_chunk_chars=6000,
            web_snapshot_max_chunk_chars=12000,
        ),
        get=AsyncMock(return_value=snapshot),
        find_latest=AsyncMock(return_value=snapshot),
        create=AsyncMock(return_value=snapshot),
        list=AsyncMock(),
    )
    tool = WebFetchTool(cast(WebSnapshotStore, store), uuid.uuid4(), uuid.uuid4())
    return tool, store, snapshot


@pytest.mark.asyncio
async def test_reader_preserves_exact_unicode_offsets_and_fits_serialized_allowance(reader):
    tool, store, snapshot = reader
    tool.set_result_allowance(lambda text: len(text.encode()) <= 1100)
    first = json.loads(await tool.execute(snapshot_id=str(snapshot.id)))
    assert len(json.dumps(first, ensure_ascii=False).encode()) <= 1100
    assert first["content"] == snapshot.content[: first["end_char"]]
    assert 0 < first["content_length"] < 6000
    second = json.loads(
        await tool.execute(snapshot_id=str(snapshot.id), start_char=first["next_start_char"])
    )
    assert first["content"] + second["content"] == snapshot.content[: second["end_char"]]
    assert first["total_chars"] == len(snapshot.content)
    store.find_latest.assert_not_awaited()


@pytest.mark.asyncio
async def test_reader_reuses_snapshot_and_refresh_bypasses_plaintext_cache(reader):
    tool, store, snapshot = reader
    tool._fetch_service = SimpleNamespace(
        fetch=AsyncMock(
            return_value=SimpleNamespace(
                content=snapshot.content,
                url=snapshot.source_url,
                final_url=snapshot.final_url,
                title="Page",
                source_url=snapshot.source_url,
                extraction_version="web-reader-v1",
            )
        )
    )
    await tool.execute(url=snapshot.source_url)
    tool._fetch_service.fetch.assert_not_awaited()
    await tool.execute(url=snapshot.source_url, force_refresh=True)
    assert tool._fetch_service.fetch.call_args.kwargs["use_cache"] is False
    store.create.assert_awaited_once()
    await tool.execute(url=snapshot.source_url, force_refresh=True)
    result = json.loads(await tool.execute(url=snapshot.source_url, force_refresh=True))
    assert result["error"] == "snapshot_turn_limit"
    assert tool._fetch_service.fetch.await_count == 2


@pytest.mark.asyncio
async def test_expired_missing_or_wrong_owner_snapshot_never_refetches(reader):
    tool, store, snapshot = reader
    store.get.side_effect = WebSnapshotNotFound()
    result = json.loads(await tool.execute(snapshot_id=str(snapshot.id), url=snapshot.source_url))
    assert "error" in result
    assert tool._fetch_service is None


@pytest.mark.asyncio
async def test_find_literal_offsets_and_pagination(reader):
    tool, store, snapshot = reader
    first = json.loads(
        await tool.execute(action="find", snapshot_id=str(snapshot.id), query="needle", limit=2)
    )
    assert len(first["matches"]) == 2
    for match in first["matches"]:
        assert snapshot.content[match["start_char"] : match["end_char"]] == "needle"
    second = json.loads(
        await tool.execute(
            action="find",
            snapshot_id=str(snapshot.id),
            query="needle",
            start_char=first["next_start_char"],
            limit=2,
        )
    )
    assert second["matches"][0]["start_char"] > first["matches"][-1]["start_char"]


@pytest.mark.asyncio
async def test_invalid_arguments_and_tiny_budget_are_bounded(reader):
    tool, store, snapshot = reader
    for args in (
        {"start_char": -1},
        {"max_chars": True},
        {"max_chars": 12001},
        {"url": "https://other.example"},
        {"extract": "metadata"},
    ):
        result = json.loads(await tool.execute(snapshot_id=str(snapshot.id), **args))
        assert "error" in result
    tool.set_result_allowance(lambda text: False)
    assert (
        json.loads(await tool.execute(snapshot_id=str(snapshot.id)))["error"]
        == "context_budget_exhausted"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [FetchPageTooLargeError, FetchExtractionError])
async def test_fetch_denials_preserve_bounded_reason(reader, failure):
    tool, store, snapshot = reader
    store.find_latest.return_value = None
    tool._fetch_service = SimpleNamespace(fetch=AsyncMock(side_effect=failure()))
    result = json.loads(await tool.execute(url=snapshot.source_url))
    assert result == {"error": failure.code}
    store.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_trimming_preserves_next_unreturned_source(reader):
    tool, store, snapshot = reader
    items = [
        SimpleNamespace(
            id=uuid.uuid4(),
            title=str(i) * 200,
            retrieved_at=snapshot.retrieved_at,
            expires_at=snapshot.expires_at,
        )
        for i in range(4)
    ]
    store.list.return_value = SimpleNamespace(items=items, total=4)
    tool.set_result_allowance(lambda text: len(text) < 500)
    result = json.loads(await tool.execute(action="list"))
    assert len(result["sources"]) == 1
    assert result["next_offset"] == 1
    store.list.return_value = SimpleNamespace(items=items[1:], total=4)
    second = json.loads(await tool.execute(action="list", offset=result["next_offset"]))
    assert second["sources"][0]["snapshot_id"] == str(items[1].id)
    tool.set_result_allowance(lambda text: len(text) < 50)
    assert json.loads(await tool.execute(action="list"))["error"] == "context_budget_exhausted"


@pytest.mark.asyncio
async def test_unowned_tool_fails_closed():
    result = json.loads(await WebFetchTool().execute(url="https://example.com"))
    assert result == {"error": "snapshot_storage_unavailable"}
