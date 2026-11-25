"""Explicit browser qualification; run directly with --compute-metadata-postgres."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from tests.owned_search_redis import OwnedSearchRedis
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_text_execution_postgres import (
    db as db,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)
from tests.test_history_ui_browser_postgres import actual_servers

PARTIAL = "Synthetic research report: X."
REPORT = PARTIAL + " Fixture findings only."


async def original(s):
    async with s.db.connection() as conn:
        rows = await conn.fetch(
            """SELECT t.id,t.user_id,t.idempotency_key,t.conversation_id,t.result_message_id,
                      t.status,t.cancel_requested_at,t.attempt_count,m.status AS result_status,
                      m.content,a.epoch,a.outcome,
                      (SELECT count(*) FROM task_attempts) AS attempts,
                      (SELECT count(*) FROM tasks) AS tasks
               FROM tasks t JOIN messages m ON m.id=t.result_message_id
               JOIN task_attempts a ON a.task_id=t.id"""
        )
    assert len(rows) == 1
    (row,) = rows
    assert (row["tasks"], row["attempts"], row["attempt_count"], len(s.calls)) == (1, 1, 1, 1)
    assert row["user_id"] == s.account and row["cancel_requested_at"] is None
    return dict(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["self-hosted-ui"], indirect=True)
@pytest.mark.parametrize("lost", [False, True], ids=["acknowledged", "lost-response"])
async def test_desktop_to_phone_inbox(text_host, monkeypatch, lost):
    s = text_host
    s.inbox_pilot = True
    s.synthetic_deltas = (PARTIAL, REPORT[len(PARTIAL) :])
    owner = OwnedSearchRedis(Path(os.environ["OPENCODE_WORK_TEST_DIR"]), "inbox-761")
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    with (evidence / f"inbox-761-browser-{int(lost)}-{uuid4().hex}.jsonl").open("xb") as log:
        redis = child = None
        try:
            # Bootstrap and enrollment must create the first two devices before
            # provisioning the separate negative-control account's session.
            owner.start()
            redis = await owner.connect()
            async with s.make(), actual_servers(s, monkeypatch, redis) as (packet, environment):
                async with s.db.connection() as conn:
                    foreign = await s.db.account(conn)
                child = await asyncio.create_subprocess_exec(
                    "node",
                    "tests/history_inbox_ui_browser.mjs",
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

                async def receive(stage, timeout=90):
                    line = await asyncio.wait_for(receipts.readline(), timeout)
                    assert line, f"Browser stopped before {stage}: see {log.name}"
                    log.write(line)
                    log.flush()
                    received = json.loads(line)
                    assert received["stage"] == stage
                    return received

                await send(json.dumps({**packet, "lost": lost}))
                desktop = await receive("desktop", 150)
                assert desktop["lost"] == int(lost)
                async with asyncio.timeout(10):
                    await s.entered.wait()
                    while True:
                        running = await original(s)
                        if s.execution.encryption.decrypt(running["content"]) == PARTIAL:
                            break
                        await asyncio.sleep(0.05)
                assert running["idempotency_key"] == desktop["key"]
                assert (running["status"], running["result_status"]) == ("running", "streaming")
                assert not s.closed.is_set()
                await send("running verified")
                await receive("phone", 120)
                assert await original(s) == running
                async with s.db.connection() as conn:
                    assert (
                        await conn.fetchval(
                            "SELECT count(*) FROM devices WHERE user_id=$1", s.account
                        )
                        == 2
                    )
                _, b_headers = await device(s, foreign)
                # The third browser does not inherit either A cookie/token.
                # Send the real persisted B session to the harness via stdin only.
                await send(json.dumps(b_headers))

                s.release.set()
                assert await jobs(s) == ["completed"]
                finished = await original(s)
                for name in (
                    "id",
                    "idempotency_key",
                    "conversation_id",
                    "result_message_id",
                    "epoch",
                ):
                    assert finished[name] == running[name]
                assert (finished["status"], finished["result_status"], finished["outcome"]) == (
                    "completed",
                    "complete",
                    "completed",
                )
                assert s.execution.encryption.decrypt(finished["content"]) == REPORT
                await send("report committed")

                read = await receive("report", 120)
                assert read["task"] == str(finished["id"])
                assert read["conversation"] == str(finished["conversation_id"])
                assert read["message"] == str(finished["result_message_id"])
                assert await original(s) == finished
                # Real SQL lock contention, not a fabricated backend response.
                # The original read path must refuse during the unresolved wait.
                async with s.db.connection() as conn, conn.transaction():
                    await conn.fetchrow(
                        "SELECT id FROM tasks WHERE id=$1 FOR UPDATE", finished["id"]
                    )
                    await send("original locked")
                    await receive("finished")
                await asyncio.wait_for(child.wait(), 20)
                assert child.returncode == 0
                assert await original(s) == finished
                assert len(s.calls) == 1 and s.closed.is_set() and not s.errors
        finally:
            if child is not None and child.returncode is None:
                child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 15)
                except TimeoutError:
                    child.kill()
                    await child.wait()
                    raise AssertionError(
                        "Owned inbox browser did not acknowledge teardown"
                    ) from None
            if redis is not None:
                await redis.aclose()
            owner.close()
