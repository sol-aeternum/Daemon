"""Checked worker sources against only the approved disposable task database."""

import uuid
import json

import pytest
from arq import Retry

from orchestrator.worker.jobs import _extraction_source_version, _read_extraction_references
from tests.durable_tasks_support import Env, accept_task, durable_env_fixture

env = durable_env_fixture()


@pytest.mark.asyncio
@pytest.mark.parametrize("trace", [[], None, "[]", "fictional-malformed-json"])
async def test_page_and_exact_source_versions_match_legacy_trace_normalization(
    env: Env, trace
) -> None:
    accepted = await accept_task(env, message="Fictional unchanged version")
    await env.pool.execute(
        "UPDATE messages SET tool_calls = $2::jsonb, tool_results = $2::jsonb WHERE id = $1",
        accepted.user_message_id,
        json.dumps(trace),
    )
    exact = await env.memory.get_owned_message(
        accepted.user_message_id, user_id=env.alice, conversation_id=accepted.conversation_id
    )
    page = await env.memory.get_messages_after_cursor(
        accepted.conversation_id, created_at=None, message_id=None, limit=250
    )
    producer = next(row for row in page if row["id"] == accepted.user_message_id)
    assert exact is not None
    assert _extraction_source_version(producer) == _extraction_source_version(exact)


@pytest.mark.asyncio
async def test_owned_exact_message_reread_rejects_other_owner_and_conversation(env: Env) -> None:
    accepted = await accept_task(env, message="Fictional exact source")
    source = await env.memory.get_owned_message(
        accepted.user_message_id, user_id=env.alice, conversation_id=accepted.conversation_id
    )
    assert source is not None and source["content"] == "Fictional exact source"
    assert (
        await env.memory.get_owned_message(
            accepted.user_message_id, user_id=env.bob, conversation_id=accepted.conversation_id
        )
        is None
    )
    assert (
        await env.memory.get_owned_message(
            accepted.user_message_id, user_id=env.alice, conversation_id=uuid.uuid4()
        )
        is None
    )


@pytest.mark.asyncio
async def test_source_reference_restart_and_deletion_without_cursor_advance(env: Env) -> None:
    accepted = await accept_task(env, message="Fictional fragment " + "x" * 80_000)
    source = await env.memory.get_owned_message(
        accepted.user_message_id, user_id=env.alice, conversation_id=accepted.conversation_id
    )
    assert source is not None
    reference = {
        "_extraction_refs": [
            {
                "message_id": str(accepted.user_message_id),
                "source_version": _extraction_source_version(source),
                "fragment_index": 1,
            }
        ]
    }
    raw = await _read_extraction_references(
        env.memory, env.alice, accepted.conversation_id, reference
    )
    assert raw[0]["_extraction_continuation_key"].endswith(":1")
    await env.memory.update_message(
        accepted.user_message_id, content="Fictional changed shorter source"
    )
    raw = await _read_extraction_references(
        env.memory, env.alice, accepted.conversation_id, reference
    )
    assert raw[0]["content"] == "Fictional changed shorter source"
    await env.pool.execute("DELETE FROM messages WHERE id = $1", accepted.user_message_id)
    with pytest.raises(Retry):
        await _read_extraction_references(
            env.memory, env.alice, accepted.conversation_id, reference
        )
    assert await env.memory.get_last_extraction_cursor(accepted.conversation_id) == (None, None)
