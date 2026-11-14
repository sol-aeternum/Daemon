"""Owned actual Next/browser qualification; no response fabrication or live inference."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
from uuid import uuid4

import pytest
import uvicorn

from orchestrator.tasks import runner
from tests.history_ui_support import actual_auth_app
from tests.owned_search_redis import OwnedSearchRedis
from tests.test_history_text_execution_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
    jobs,
)


@asynccontextmanager
async def actual_servers(s, monkeypatch, redis):
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    nonce = uuid4().hex
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    backend = f"http://127.0.0.1:{sock.getsockname()[1]}"
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "TMPDIR", "TMP", "TEMP")
        if key in os.environ
    }
    environment.update(
        DAEMON_INTERNAL_API_URL=backend,
        NEXT_PUBLIC_API_URL=backend,
        NEXT_TELEMETRY_DISABLED="1",
        NODE_ENV="development",
    )
    node = None
    server = None
    serving = None
    with (evidence / f"worker-next-{nonce}.log").open("wb") as log:
        try:
            node = await asyncio.create_subprocess_exec(
                "node",
                "tests/history_ui_next.mjs",
                cwd=root,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
            )
            assert node.stdout is not None
            async with asyncio.timeout(60):
                while True:
                    line = await node.stdout.readline()
                    assert line, "Owned Next process exited before readiness"
                    try:
                        ready = json.loads(line)
                    except ValueError:
                        log.write(line)
                        continue
                    if "ui" in ready:
                        break
            wired = await actual_auth_app(s, monkeypatch, redis, ready["ui"])
            config = uvicorn.Config(
                wired.app, log_level="warning", access_log=False, lifespan="off"
            )
            server = uvicorn.Server(config)
            serving = asyncio.create_task(server.serve(sockets=[sock]))
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        serving.result()
                    await asyncio.sleep(0.01)
            yield dict(ui=ready["ui"], backend=backend, bootstrap=wired.bootstrap), environment
        finally:
            if server is not None:
                server.should_exit = True
            if serving is not None:
                await asyncio.wait_for(serving, 15)
            sock.close()
            if node is not None:
                if node.returncode is None:
                    node.terminate()
                try:
                    stdout, _ = await asyncio.wait_for(node.communicate(), 20)
                    log.write(stdout)
                except TimeoutError:
                    node.kill()  # Exact owned subprocess only; no process/name scan.
                    await node.wait()
                    raise


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["self-hosted-ui"], indirect=True)
@pytest.mark.parametrize("outcome", ["complete", "stop"])
async def test_actual_page_saved_partial_second_issued_device_and_terminal(
    text_host, monkeypatch, outcome
):
    s = text_host
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.1)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.1)
    owner = OwnedSearchRedis(
        Path(os.environ["OPENCODE_WORK_TEST_DIR"]), "daily-driver-app-flow-20261113"
    )
    redis = None
    child = None
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    with (evidence / f"worker-browser-{outcome}.log").open("wb") as log:
        try:
            owner.start()
            redis = await owner.connect()
            async with s.make(), actual_servers(s, monkeypatch, redis) as (packet, environment):
                child = await asyncio.create_subprocess_exec(
                    "node",
                    "tests/history_ui_browser.mjs",
                    cwd=Path(__file__).resolve().parents[1],
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=log,
                )
                assert child.stdin is not None and child.stdout is not None
                child.stdin.write(json.dumps({**packet, "outcome": outcome}).encode() + b"\n")
                await child.stdin.drain()
                for stage in ("first-partial", "second-partial"):
                    line = await asyncio.wait_for(child.stdout.readline(), 120)
                    assert line, f"Browser exited before {stage}; inspect retained browser log"
                    receipt = json.loads(line)
                    assert receipt["stage"] == stage
                    log.write(line)
                    assert s.db.sql("SELECT count(*) FROM tasks") == "1"
                    assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
                    assert len(s.calls) == 1 and not s.closed.is_set()
                    async with s.db.connection() as conn:
                        original = await conn.fetchrow(
                            "SELECT t.id,t.idempotency_key,t.conversation_id,t.cancel_requested_at,t.status,m.content "
                            "FROM tasks t JOIN messages m ON m.id=t.result_message_id"
                        )
                    assert original is not None
                    assert original["idempotency_key"] == receipt["key"]
                    assert str(original["conversation_id"]) == receipt["conversation"]
                    assert (
                        original["status"] == "running" and original["cancel_requested_at"] is None
                    )
                    assert s.execution.encryption.decrypt(original["content"]) == "original partial"
                    if stage == "first-partial":
                        assert receipt["partial"] == "original partial"
                        child.stdin.write(b"first-verified\n")
                        await child.stdin.drain()
                    else:
                        assert str(original["id"]) == receipt["task"]
                assert s.db.sql("SELECT count(*) FROM devices") == "2"
                if outcome == "complete":
                    s.release.set()
                    child.stdin.write(b"complete\n")
                    await child.stdin.drain()
                line = await asyncio.wait_for(child.stdout.readline(), 30)
                assert line, "Browser exited before terminal receipt"
                receipt = json.loads(line)
                log.write(line)
                assert receipt["stage"] == "finished"
                await asyncio.wait_for(child.wait(), 15)
                assert child.returncode == 0
                assert await jobs(s) == ["completed" if outcome == "complete" else "cancelled"]
                assert len(s.calls) == 1 and s.closed.is_set()
        finally:
            if child is not None and child.returncode is None:
                child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 15)
                except TimeoutError:
                    child.kill()
                    await child.wait()
                    raise AssertionError(
                        "Owned browser cleanup did not acknowledge completion"
                    ) from None
            if redis is not None:
                await redis.aclose()
            owner.close()
