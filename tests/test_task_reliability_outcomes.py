"""Bounded outcome normalization; no HTTP, database, or inference calls."""

import copy
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.tasks.fence import _outcome_of
from orchestrator.tasks.runner import AttemptSink, AttemptState
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import Claim, TaskStore


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": 201, "headers": {}, "body": "created"}, "succeeded"),
        ({"status": 400, "headers": {}, "body": "invalid"}, "unknown"),
        ({"status": 503, "headers": {}, "body": "upstream error"}, "unknown"),
        ({"status": 500, "success": True}, "unknown"),
        ({"success": False, "error": "timeout"}, "unknown"),
        ({"performed": False, "status": 500}, "failed"),
        ({"success": True}, "succeeded"),
        ({}, "unknown"),
        ("", "unknown"),
        ("legacy response", "unknown"),
        (None, "unknown"),
    ],
)
def test_normalizes_real_http_and_missing_evidence(result, expected):
    assert _outcome_of(result) == expected
    if isinstance(result, dict):
        assert _outcome_of(json.dumps(result)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"error": "notification timed out", "success": False}, "unknown"),
        ({"error": "not sent", "performed": False}, "failed"),
        ({"success": True}, "succeeded"),
        (json.dumps({"error": "timeout"}), "unknown"),
        ({}, "unknown"),
    ],
)
async def test_durable_sink_saves_the_live_outcome_without_mutating_engine_rows(result, expected):
    store = MagicMock(spec=TaskStore)
    store.complete = AsyncMock(return_value=TaskStatus.COMPLETED)
    claim = Claim(
        task_id=uuid.uuid4(),
        epoch=1,
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        user_message_id=uuid.uuid4(),
        result_message_id=uuid.uuid4(),
        attempt_count=1,
        max_attempts=3,
        task_input={},
    )
    sink = AttemptSink(store, AttemptState(claim))
    # Live/replay classify the result itself, not an untrusted outcome in it.
    rows = [{"name": "notification_send", "result": result, "id": "original"}]
    original = copy.deepcopy(rows)
    await sink.update_message(
        claim.result_message_id,
        content="answer",
        status="complete",
        tool_results=rows,
        tool_calls=[{"name": "notification_send", "arguments": {}}],
    )
    fields = store.complete.call_args.kwargs["message_fields"]
    assert fields["tool_results"] == [{**original[0], "outcome": expected}]
    assert fields["tool_calls"] == [{"name": "notification_send", "arguments": {}}]
    assert rows == original
    assert fields["tool_results"] is not rows
    assert fields["tool_results"][0] is not rows[0]
