"""New UI bridge read after an actual producer exec/death; never in-process restore."""

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from tests.test_history_text_execution_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
    device,
)


def process_packet(s, headers, *, inbox_two_jobs, research):
    return dict(
        socket=str(s.db.host),
        database=s.db.name,
        encryption_key=s.encryption_key,
        journal=str(s.journal),
        journal_id=str(s.journal_id),
        path=str(s.path),
        history_id=str(s.identity.history_id),
        key_check=s.identity.key_check,
        authorization=headers["Authorization"],
        inbox_two_jobs=inbox_two_jobs,
        inbox_research=research,
    )


async def start_driver(packet, tmp_path, mode):
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "TMPDIR": os.environ["TMPDIR"],
        "DAEMON_ENCRYPTION_KEY": packet["encryption_key"],
    }
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.history_text_process_driver",
        mode,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    assert child.stdin is not None
    child.stdin.write(json.dumps(packet).encode() + b"\n")
    await child.stdin.drain()
    child.stdin.close()
    return child


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completed", "inbox_two_jobs", "research"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, False),
        pytest.param(False, True, True, id="research-interrupted-at-first-send"),
    ],
)
async def test_fresh_process_ui_bridge_reads_surviving_sql_without_repeating_work(
    text_host, tmp_path, completed, inbox_two_jobs, research
):
    s = text_host
    _, headers = await device(s, s.account)
    _, second_headers = await device(s, s.account)
    packet = process_packet(s, headers, inbox_two_jobs=inbox_two_jobs, research=research)
    if research:
        packet["question"] = "Which crossing opened first?"
    first = await start_driver(packet, tmp_path, "complete" if completed else "start")
    assert first.stderr is not None
    errors = asyncio.create_task(first.stderr.read())
    try:
        assert first.stdout is not None
        line = await asyncio.wait_for(first.stdout.readline(), 30)
        assert line, (await errors).decode()
        witness = json.loads(line)
        assert witness["calls"] == 1
        packet["task"] = witness["task"]
        if completed:
            await asyncio.wait_for(first.wait(), 10)
    finally:
        if first.returncode is None:
            first.kill()
        await asyncio.wait_for(first.wait(), 10)
        diagnostics = await errors
    assert first.returncode == (0 if completed else -9), diagnostics.decode()
    async with s.db.connection() as conn:
        original = await conn.fetchrow("SELECT * FROM tasks")
        attempt = await conn.fetchrow("SELECT * FROM task_attempts")
        reservation = await conn.fetchrow("SELECT * FROM entitlement_reservations")
        messages = await conn.fetch("SELECT * FROM messages ORDER BY id")
        conversations = await conn.fetch("SELECT * FROM conversations ORDER BY id")
        usage = await conn.fetch(
            "SELECT * FROM entitlement_usage_periods ORDER BY user_id,period_key"
        )
        accounts = await conn.fetch("SELECT * FROM entitlement_accounts ORDER BY user_id")
        events = await conn.fetch("SELECT * FROM task_events ORDER BY task_id,seq")
        recovery = await conn.fetch("SELECT * FROM task_inference_recovery ORDER BY task_id")
        projection = await conn.fetch(
            "SELECT seq,kind,digest FROM task_history_projection ORDER BY seq"
        )
        dispatches = await conn.fetch(
            "SELECT * FROM compute_reservation_metadata ORDER BY reservation_id"
        )
    assert str(original["id"]) == witness["task"]
    assert reservation["status"] == ("settled" if completed else "open")
    if research:
        assert [r["kind"] for r in projection] == ["ACCEPT_PREPARE", "SEND_POSSIBLE"]
        assert [r["reservation_id"] for r in dispatches] == [reservation["id"]]
    history = {name: (s.path / name).read_bytes() for name in ("LOG", "HEAD")}
    packet["authorization"] = second_headers["Authorization"]
    second = await start_driver(packet, tmp_path, "ui-reopen")
    try:
        stdout, stderr = await asyncio.wait_for(second.communicate(), 30)
        assert second.returncode == 0, stderr.decode()
        assert json.loads(stdout) == dict(
            task=packet["task"],
            partial=witness["partial"],
            status="completed" if completed else "running",
            calls=0,
            ui_bridge=True,
        )
        assert s.db.sql("SELECT count(*) FROM tasks") == "1"
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "1"
        assert {name: (s.path / name).read_bytes() for name in history} == history
        async with s.db.connection() as conn:
            assert await conn.fetchrow("SELECT * FROM tasks") == original
            assert await conn.fetchrow("SELECT * FROM task_attempts") == attempt
            assert await conn.fetchrow("SELECT * FROM entitlement_reservations") == reservation
            assert await conn.fetch("SELECT * FROM messages ORDER BY id") == messages
            assert await conn.fetch("SELECT * FROM conversations ORDER BY id") == conversations
            assert await conn.fetch("SELECT * FROM task_events ORDER BY task_id,seq") == events
            assert (
                await conn.fetch("SELECT seq,kind,digest FROM task_history_projection ORDER BY seq")
                == projection
            )
            assert (
                await conn.fetch(
                    "SELECT * FROM compute_reservation_metadata ORDER BY reservation_id"
                )
                == dispatches
            )
            assert (
                await conn.fetch("SELECT * FROM task_inference_recovery ORDER BY task_id")
                == recovery
            )
            assert (
                await conn.fetch(
                    "SELECT * FROM entitlement_usage_periods ORDER BY user_id,period_key"
                )
                == usage
            )
            assert (
                await conn.fetch("SELECT * FROM entitlement_accounts ORDER BY user_id") == accounts
            )
    finally:
        if second.returncode is None:
            second.kill()
        await asyncio.wait_for(second.wait(), 10)


async def source_state(s):
    """Capture durable effects without acquiring write locks or touching the task."""
    async with s.db.connection() as conn:
        return {
            "history": [(s.path / name).read_bytes() for name in ("LOG", "HEAD")],
            "projection": await conn.fetch(
                "SELECT seq,kind,frame FROM task_history_projection ORDER BY seq"
            ),
            "tasks": await conn.fetch("SELECT * FROM tasks ORDER BY id"),
            "messages": await conn.fetch("SELECT * FROM messages ORDER BY id"),
            "attempts": await conn.fetch("SELECT * FROM task_attempts ORDER BY task_id,epoch"),
            "events": await conn.fetch("SELECT * FROM task_events ORDER BY task_id,seq"),
            "sources": await conn.fetch("SELECT * FROM web_snapshots ORDER BY id"),
            "holds": await conn.fetch("SELECT * FROM entitlement_reservations ORDER BY id"),
            "dispatches": await conn.fetch(
                "SELECT * FROM compute_reservation_metadata ORDER BY reservation_id"
            ),
            "periods": await conn.fetch(
                "SELECT * FROM entitlement_usage_periods ORDER BY user_id,period_key"
            ),
            "accounts": await conn.fetch("SELECT * FROM entitlement_accounts ORDER BY user_id"),
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["sources"], indirect=True)
@pytest.mark.parametrize(
    ("cut", "reads", "pins"),
    [
        ("before-page-send", 0, 0),
        ("after-page-before-pin", 1, 0),
        ("after-pin-commit-before-ack", 1, 1),
    ],
)
async def test_dead_source_producer_cannot_resume_on_second_device(
    text_host, tmp_path, owned_search_redis, cut, reads, pins
):
    s = text_host
    _, first_device = await device(s, s.account)
    _, second_device = await device(s, s.account)
    packet = process_packet(s, first_device, inbox_two_jobs=True, research=True)
    owned_search_redis.verify_socket()
    packet.update(
        question="What instrument can visitors borrow from the estuary station?",
        source_cut=cut,
        redis_socket=str(owned_search_redis.socket),
        redis_run_id=owned_search_redis.run_id,
        redis_marker=owned_search_redis.marker,
        redis_nonce=owned_search_redis.nonce,
    )
    original = await start_driver(packet, tmp_path, "start")
    assert original.stdout and original.stderr
    diagnostics = asyncio.create_task(original.stderr.read())
    try:
        line = await asyncio.wait_for(original.stdout.readline(), 60)
        assert line, (await diagnostics).decode()
        witness = json.loads(line)
        assert witness["cut"] == cut
        assert (witness["models"], witness["searches"], witness["reads"]) == (2, 1, reads)
    finally:
        if original.returncode is None:
            original.kill()
        await asyncio.wait_for(original.wait(), 10)
        await diagnostics
    assert original.returncode == -9
    before = await source_state(s)
    (task,) = before["tasks"]
    assert str(task["id"]) == witness["task"] and task["status"] == "running"
    assert len(before["attempts"]) == 1
    assert [f["kind"] for f in before["projection"]] == [
        "ACCEPT_PREPARE",
        "SEND_POSSIBLE",
        "SEND_POSSIBLE",
        "SEND_POSSIBLE",
        "SOURCE_READ_POSSIBLE",
        *(["SOURCE_PIN_PREPARE"] if pins else []),
    ]
    assert len(before["holds"]) == len(before["dispatches"]) == 3
    assert (
        len(before["sources"])
        == sum(e["kind"] == "page_refreshed" for e in before["events"])
        == pins
    )

    packet.update(task=str(task["id"]), authorization=second_device["Authorization"])
    observer = await start_driver(packet, tmp_path, "ui-reopen")
    try:
        stdout, stderr = await asyncio.wait_for(observer.communicate(), 30)
        assert observer.returncode == 0, stderr.decode()
        reply = json.loads(stdout)
        assert reply["task"] == packet["task"] and reply["ui_bridge"] is True
        assert reply["status"] in {"running", "unavailable"} and reply["calls"] == 0
    finally:
        if observer.returncode is None:
            observer.kill()
        await asyncio.wait_for(observer.wait(), 10)
    assert await source_state(s) == before
