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


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [False, True])
async def test_fresh_process_ui_bridge_reads_surviving_sql_without_repeating_work(
    text_host, tmp_path, completed
):
    s = text_host
    _, headers = await device(s, s.account)
    packet = dict(
        socket=str(s.db.host),
        database=s.db.name,
        encryption_key=s.encryption_key,
        journal=str(s.journal),
        journal_id=str(s.journal_id),
        path=str(s.path),
        history_id=str(s.identity.history_id),
        key_check=s.identity.key_check,
        authorization=headers["Authorization"],
    )
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "TMPDIR": os.environ["TMPDIR"],
        "DAEMON_ENCRYPTION_KEY": s.encryption_key,
    }

    async def start(mode):
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

    first = await start("complete" if completed else "start")
    try:
        assert first.stdout is not None
        line = await asyncio.wait_for(first.stdout.readline(), 30)
        assert line
        witness = json.loads(line)
        assert witness["calls"] == 1
        packet["task"] = witness["task"]
        if completed:
            await asyncio.wait_for(first.wait(), 10)
    finally:
        if first.returncode is None:
            first.kill()
        await asyncio.wait_for(first.wait(), 10)
    assert first.returncode == (0 if completed else -9)
    second = await start("ui-reopen")
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
    finally:
        if second.returncode is None:
            second.kill()
        await asyncio.wait_for(second.wait(), 10)
