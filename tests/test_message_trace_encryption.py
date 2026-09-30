"""Tool source text must not become a plaintext shadow of encrypted chat."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet
import pytest

from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore


@pytest.fixture
def trace_store():
    pool = MagicMock()
    pool.fetch = AsyncMock()
    pool.fetchrow = AsyncMock()
    encryption = ContentEncryption(Fernet.generate_key().decode())
    return MemoryStore(pool, encryption), pool, encryption


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["insert", "update"])
async def test_trace_writes_encrypt_bound_parameters_and_return_plain_history(
    trace_store, operation
):
    store, pool, encryption = trace_store
    calls = [{"name": "web_fetch", "arguments": {"url": "https://example.com/private-query"}}]
    results = [{"name": "web_fetch", "result": {"content": "Confidential source — 日本語"}}]

    async def written_row(_query, *args):
        if operation == "insert":
            return {"content": args[3], "tool_calls": args[7], "tool_results": args[8]}
        return {"content": args[1], "tool_calls": args[4], "tool_results": args[5]}

    pool.fetchrow.side_effect = written_row
    if operation == "insert":
        message = await store.insert_message(
            uuid.uuid4(),
            uuid.uuid4(),
            "assistant",
            "Answer",
            tool_calls=calls,
            tool_results=results,
        )
        positions = (8, 9)  # Includes SQL as the first positional argument.
    else:
        message = await store.update_message(
            uuid.uuid4(), content="Answer", tool_calls=calls, tool_results=results
        )
        positions = (5, 6)

    assert message["tool_calls"] == calls
    assert message["tool_results"] == results
    for position, expected in zip(positions, (calls, results), strict=True):
        persisted = pool.fetchrow.call_args.args[position]
        assert "private-query" not in persisted
        assert "Confidential source" not in persisted
        envelope = json.loads(persisted)
        assert envelope["format"] == "daemon.encrypted_tool_trace"
        assert envelope["version"] == 1
        assert json.loads(encryption.decrypt(envelope["ciphertext"])) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reader",
    [
        "get_messages",
        "get_recent_messages",
        "get_summary_message_batch",
        "get_messages_after_cursor",
    ],
)
@pytest.mark.parametrize("legacy", [False, True])
async def test_all_history_read_paths_accept_encrypted_and_legacy_traces(
    trace_store, reader, legacy
):
    store, pool, encryption = trace_store
    trace = [{"name": "web_fetch", "result": {"content": "Saved source"}}]
    value = json.dumps(trace) if legacy else store._encrypt_tool_trace(trace)
    pool.fetch.return_value = [
        {"content": encryption.encrypt("Answer"), "status": "complete", "tool_results": value}
    ]
    kwargs = (
        {"created_at": None, "message_id": None} if reader == "get_messages_after_cursor" else {}
    )
    rows = await getattr(store, reader)(uuid.uuid4(), **kwargs)
    assert rows[0]["tool_results"] == trace


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "envelope",
    [
        {"format": "daemon.encrypted_tool_trace", "version": 2, "ciphertext": "invalid"},
        {"format": "daemon.encrypted_tool_trace", "version": 1},
        {"format": "daemon.encrypted_tool_trace", "version": 1, "ciphertext": "invalid"},
    ],
)
async def test_corrupt_or_unknown_encrypted_trace_fails_closed(trace_store, envelope):
    store, pool, encryption = trace_store
    pool.fetch.return_value = [{"content": encryption.encrypt("Answer"), "tool_results": envelope}]
    with pytest.raises(ValueError):
        await store.get_messages(uuid.uuid4())


@pytest.mark.asyncio
async def test_invalid_decrypted_shape_is_not_treated_as_plaintext(trace_store):
    store, pool, encryption = trace_store
    pool.fetch.return_value = [
        {
            "content": encryption.encrypt("Answer"),
            "tool_results": {
                "format": "daemon.encrypted_tool_trace",
                "version": 1,
                "ciphertext": encryption.encrypt('{"unexpected": "object"}'),
            },
        }
    ]
    with pytest.raises(ValueError, match="payload"):
        await store.get_recent_messages(uuid.uuid4())


@pytest.mark.asyncio
async def test_failed_trace_encryption_never_writes_plaintext(trace_store, monkeypatch):
    store, pool, encryption = trace_store

    def fail(_value):
        raise RuntimeError("Encryption unavailable")

    monkeypatch.setattr(encryption, "encrypt", fail)
    with pytest.raises(RuntimeError, match="Encryption unavailable"):
        await store.update_message(uuid.uuid4(), tool_results=[{"content": "private"}])
    pool.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["error", "cancelled"])
async def test_terminal_extraction_skip_does_not_decrypt_or_include_traces(trace_store, status):
    store, pool, _encryption = trace_store
    pool.fetch.return_value = [
        {
            "content": "unreadable",
            "status": status,
            "tool_results": {
                "format": "daemon.encrypted_tool_trace",
                "version": 1,
                "ciphertext": "invalid",
            },
        }
    ]
    rows = await store.get_messages_after_cursor(uuid.uuid4(), created_at=None, message_id=None)
    assert rows[0]["_extraction_skip"] is True
    assert rows[0]["tool_results"] == []
    assert rows[0]["content"] == ""
