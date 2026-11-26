"""Explicit desktop-to-phone two-job proof using real auth and owned transport."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from tests.owned_search_redis import OwnedSearchRedis
from tests.test_history_text_execution_postgres import (
    db as db,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)
from tests.test_history_ui_browser_postgres import actual_servers


async def originals(s):
    async with s.db.connection() as conn:
        rows = await conn.fetch(
            "SELECT t.id,t.idempotency_key,t.conversation_id,t.result_message_id,t.status,"
            "t.attempt_count,t.cancel_requested_at,m.content,a.epoch,a.outcome "
            "FROM tasks t JOIN messages m ON m.id=t.result_message_id "
            "JOIN task_attempts a ON a.task_id=t.id WHERE t.user_id=$1 "
            "ORDER BY t.created_at,t.id",
            s.account,
        )
    assert all(r["attempt_count"] == 1 and r["cancel_requested_at"] is None for r in rows)
    assert len(rows) == len(s.calls)
    return [dict(r) for r in rows]


def identity(row):
    return {
        "task": str(row["id"]),
        "conversation": str(row["conversation_id"]),
        "message": str(row["result_message_id"]),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["self-hosted-ui"], indirect=True)
async def test_two_originals_desktop_phone_lost_second_ack(text_host, monkeypatch):
    s = text_host
    s.inbox_pilot = s.inbox_two_jobs = True
    s.synthetic_deltas = ("Synthetic report X.", " First fixture.")
    owner = OwnedSearchRedis(Path(os.environ["OPENCODE_WORK_TEST_DIR"]), "inbox-761-two")
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    with (evidence / f"inbox-two-browser-{uuid4().hex}.jsonl").open("xb") as log:
        redis = child = None
        try:
            owner.start()
            redis = await owner.connect()
            async with s.make(), actual_servers(s, monkeypatch, redis) as (packet, environment):
                child = await asyncio.create_subprocess_exec(
                    "node",
                    "tests/history_inbox_two_jobs_browser.mjs",
                    cwd=Path(__file__).resolve().parents[1],
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=log,
                )
                commands, receipts = child.stdin, child.stdout
                assert commands is not None and receipts is not None

                async def send(value):
                    commands.write(value.encode() + b"\n")
                    await commands.drain()

                async def receive(stage, timeout=150):
                    async with asyncio.timeout(timeout):
                        while True:
                            line = await receipts.readline()
                            assert line, f"Browser stopped before {stage}: see {log.name}"
                            log.write(line)
                            log.flush()
                            received = json.loads(line)
                            if received["stage"] != "diagnostic":
                                break
                    assert received["stage"] == stage
                    return received

                await send(json.dumps(packet))
                first = await receive("first")
                await asyncio.wait_for(s.entered.wait(), 10)
                s.release.set()
                assert await jobs(s) == ["completed"], s.errors
                (x,) = await originals(s)
                assert x["idempotency_key"] == first["key"]
                assert (
                    s.execution.encryption.decrypt(x["content"])
                    == "Synthetic report X. First fixture."
                )
                s.release.clear()
                s.entered.clear()
                s.closed.clear()
                s.synthetic_deltas = ("Synthetic report Y.", " Second fixture.")
                await send(json.dumps(identity(x)))
                second = await receive("second")
                assert second["lost"] == 1
                await asyncio.wait_for(s.entered.wait(), 10)
                actual_x, y = await originals(s)
                assert actual_x == x
                assert y["idempotency_key"] == second["key"] and y["status"] == "running"
                assert y["id"] != x["id"] and y["conversation_id"] != x["conversation_id"]
                await send(json.dumps(identity(y)))
                phone = await receive("phone")
                assert phone["starts"] == 2 and phone["refreshed"]
                async with s.db.connection() as conn:
                    assert (
                        await conn.fetchval(
                            "SELECT count(*) FROM devices WHERE user_id=$1", s.account
                        )
                        == 2
                    )
                    assert await conn.fetchval("SELECT count(*) FROM tasks") == 2
                    assert await conn.fetchval("SELECT count(*) FROM task_attempts") == 2
                s.release.set()
                assert await jobs(s) == ["completed", "completed"], s.errors
                actual_x, actual_y = await originals(s)
                assert actual_x == x and identity(actual_y) == identity(y)
                assert actual_y["epoch"] == y["epoch"] and actual_y["status"] == "completed"
                assert (
                    s.execution.encryption.decrypt(actual_y["content"])
                    == "Synthetic report Y. Second fixture."
                )
                await send("second committed")
                finished = await receive("finished")
                assert finished["starts"] == 2 and finished["lost"] == 1
                assert (finished["x"], finished["y"]) == (str(x["id"]), str(y["id"]))
                await asyncio.wait_for(child.wait(), 20)
                assert child.returncode == 0 and not s.errors
                assert await originals(s) == [x, actual_y]
        finally:
            if child is not None and child.returncode is None:
                child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 15)
                except TimeoutError:
                    child.kill()
                    await child.wait()
                    raise AssertionError("Owned two-job browser did not shut down") from None
            if redis is not None:
                await redis.aclose()
            owner.close()
