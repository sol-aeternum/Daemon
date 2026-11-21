"""Retired submissions and saved transcripts on disposable PostgreSQL.

Hosted Backend gates supply the separate TASKS_TEST_DATABASE_URL
fixture service. No application database is used.
"""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator import main
from orchestrator.tasks import runner
from tests.durable_tasks_support import accept_task, durable_env_fixture, expire_lease
from tests.test_task_api_postgres import Api, api as api

env = durable_env_fixture(initialized=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["/council --default Consider a plan", "/council config: rounds=2"]
)
async def test_stale_clients_create_no_work(api: Api, monkeypatch, text: str) -> None:
    capture = AsyncMock(side_effect=AssertionError("retired command reached admission"))
    monkeypatch.setattr(main, "capture_generation", capture)
    result = await api.client.post(
        "/chat", json={"message": text}, headers={"Idempotency-Key": "retired-request"}
    )
    assert result.status_code == 410
    assert result.json()["detail"]["message"] == "Council has been retired."
    assert "X-Daemon-Task-Id" not in result.headers
    capture.assert_not_awaited()
    assert not api.redis.enqueued
    for statement in (
        "SELECT count(*) FROM tasks",
        "SELECT count(*) FROM messages",
        "SELECT count(*) FROM entitlement_reservations",
        "SELECT count(*) FROM council_sessions",
    ):
        assert await api.env.pool.fetchval(statement) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_input,interrupted",
    [
        pytest.param({"message": "/council config: rounds=1"}, False, id="queued-command"),
        pytest.param({"profile": "council"}, True, id="interrupted-workload"),
    ],
)
async def test_previously_accepted_task_cannot_dispatch_or_retry(
    api: Api, monkeypatch, task_input: dict, interrupted: bool
) -> None:
    # Simulate the old isolated text acceptance path, or an old workload label.
    accepted = await accept_task(api.env, **task_input)
    if interrupted:
        store = api.env.tasks
        old = await store.claim(accepted.task_id, worker_id="old-worker", lease_s=30)
        assert old is not None
        await store.begin_execution(accepted.task_id, old.epoch, uuid4(), binding=old)
        await expire_lease(api.env, accepted.task_id)
    provider = AsyncMock(side_effect=AssertionError("retired work reached account compute"))
    monkeypatch.setattr(runner, "account_compute", provider)
    ctx = api.ctx()
    assert await runner.run_chat_task(ctx, str(accepted.task_id)) == "failed"
    assert await runner.run_chat_task(ctx, str(accepted.task_id)) == "task_already_terminal"
    provider.assert_not_called()
    row = await api.env.pool.fetchrow(
        "SELECT status, terminal_code, attempt_count FROM tasks WHERE id=$1", accepted.task_id
    )
    assert row and tuple(row) == ("failed", "council_retired", int(interrupted))
    assert await api.env.pool.fetchval("SELECT count(*) FROM entitlement_reservations") == 0
    assert await api.env.pool.fetchval("SELECT count(*) FROM task_operations") == 0


@pytest.mark.asyncio
async def test_owner_reopens_legacy_council_rows_without_a_rewrite(api: Api) -> None:
    store, pool, owner = api.env.memory, api.env.pool, api.env.alice
    conv = await store.create_conversation(owner, title="Fictional Council transcript")
    conversation_id = conv["id"]
    fixtures = [
        (
            "complete",
            "Prefer the documented option.\n\n### Skeptic\nCheck the assumptions.",
            [
                {
                    "type": "council_output",
                    "section": "consensus",
                    "content": "Prefer the documented option.",
                },
                {
                    "type": "council_output",
                    "section": "contested",
                    "content": "### Skeptic\nCheck the assumptions.",
                },
                {"type": "council_done", "total_tokens": 21},
            ],
        ),
        (
            "error",
            "This partial result was saved.",
            [
                {
                    "type": "council_output",
                    "section": "consensus",
                    "content": "This partial result was saved.",
                },
                {"type": "council_error", "error": "The synthetic run was interrupted."},
            ],
        ),
    ]
    expected = []
    for status, text, events in fixtures:
        row = await store.insert_message(
            conversation_id, owner, "assistant", text, model="council", status=status
        )
        metadata = {"request_id": "req_fictional", "council_events": events}
        traces = [
            {"name": "council_events", "result": {"events": events}, "request_id": "req_fictional"}
        ]
        # Use the old on-disk shape instead of the current trace/metadata encoders.
        await pool.execute(
            "UPDATE messages SET tool_results=$2::jsonb, metadata=$3::jsonb WHERE id=$1",
            row["id"],
            json.dumps(traces),
            json.dumps(metadata),
        )
        expected.append((str(row["id"]), text, status, metadata, traces))

    sql = "SELECT id, content, status, metadata, tool_results FROM messages ORDER BY created_at, id"
    before = await pool.fetch(sql)
    assert len(before) == 2 and all(
        row["content"] != item[1] for row, item in zip(before, expected)
    )
    for query in ("", "?pagination=cursor"):
        result = await api.client.get(f"/conversations/{conversation_id}{query}")
        assert result.status_code == 200
        rows = result.json()["messages"]
        assert [
            (row["id"], row["content"], row["status"], row["metadata"], row["tool_results"])
            for row in rows
        ] == expected
        assert all(row["model"] == "council" for row in rows)

    api.as_user(api.env.bob)
    for query in ("", "?pagination=cursor"):
        result = await api.client.get(f"/conversations/{conversation_id}{query}")
        assert result.status_code == 404
        assert "partial result" not in result.text
    assert await pool.fetch(sql) == before
