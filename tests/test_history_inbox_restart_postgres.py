"""One create-new launch lineage; real Next, browser, PostgreSQL and Redis."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import httpx

from orchestrator.auth_runtime_state import SETUP_TOKEN_HASH_KEY, set_runtime_value
from orchestrator.auth_tokens import hash_token
from tests.history_restart_launcher import RestartFixture
from tests.history_inbox_two_jobs_browser_postgres import identity
from tests.test_history_text_execution_postgres import (
    db as db,
    projection_db as projection_db,
    text_host as text_host,
    device,
)


@pytest.fixture
def lineage(request):
    if not request.config.getoption("--compute-metadata-postgres"):
        pytest.skip("requires owned disposable PostgreSQL and Redis")
    owner = RestartFixture()
    try:
        owner.cluster.start()
        owner.redis.start()
        yield owner
    finally:
        owner.close()


@pytest.fixture
def metadata_cluster(lineage):
    return lineage.cluster


@asynccontextmanager
async def node(script, environment, log):
    process = await asyncio.create_subprocess_exec(
        "node",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=log,
        env=environment,
    )
    try:
        yield process
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.communicate(), 20)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise


async def stage(process, name):
    async with asyncio.timeout(120):
        while line := await process.stdout.readline():
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if name == "ui" and "ui" in result or result.get("stage") == name:
                return result
            assert result.get("stage") == "diagnostic"
    raise AssertionError(f"fixture exited before {name}")


async def originals(s):
    async with s.db.connection() as conn:
        return await conn.fetch(
            "SELECT t.id,t.conversation_id,t.user_message_id,t.result_message_id,t.idempotency_key,t.status,"
            "a.id attempt_id,a.epoch,a.outcome,a.compute_scope_id,m.content,r.id reservation_id,r.status financial_status,"
            "r.actual_microusd,r.period_key FROM tasks t JOIN task_attempts a ON a.task_id=t.id "
            "JOIN messages m ON m.id=t.result_message_id JOIN entitlement_reservations r "
            "ON r.user_id=t.user_id AND r.scope_id=a.compute_scope_id ORDER BY t.created_at,t.id"
        )


async def original_rows(s, task):
    async with s.db.connection() as conn:
        return (
            await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task),
            await conn.fetch("SELECT * FROM task_attempts WHERE task_id=$1 ORDER BY epoch", task),
            await conn.fetch("SELECT * FROM task_events WHERE task_id=$1 ORDER BY seq", task),
            await conn.fetch("SELECT * FROM task_inference_recovery WHERE task_id=$1", task),
            await conn.fetch(
                "SELECT * FROM messages WHERE conversation_id=(SELECT conversation_id FROM tasks WHERE id=$1) ORDER BY id",
                task,
            ),
            await conn.fetch(
                "SELECT r.* FROM entitlement_reservations r JOIN task_attempts a ON a.compute_scope_id=r.scope_id WHERE a.task_id=$1",
                task,
            ),
        )


async def check_ledger(s, count):
    async with s.db.connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM entitlement_reservations WHERE user_id=$1", s.account
        )
        assert len(rows) == count and all(r["status"] == "settled" for r in rows)
        periods = await conn.fetch(
            "SELECT * FROM entitlement_usage_periods WHERE user_id=$1", s.account
        )
        account = await conn.fetchrow(
            "SELECT * FROM entitlement_accounts WHERE user_id=$1", s.account
        )
        assert {p["period_key"] for p in periods} == {r["period_key"] for r in rows}
        for p in periods:
            assert p["reserved_microusd"] == 0
            assert p["spent_microusd"] == sum(
                r["actual_microusd"]
                for r in rows
                if r["charge_kind"] == "plan" and r["period_key"] == p["period_key"]
            )
        assert account["trial_reserved_microusd"] == 0
        assert account["trial_consumed_microusd"] == sum(
            r["actual_microusd"] for r in rows if r["charge_kind"] == "trial"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["managed-restart"], indirect=True)
async def test_clean_process_replacement_explicit_phone_y(text_host, lineage):
    s = text_host
    bootstrap = "synthetic-disposable-ui-setup"
    async with s.db.connection() as conn:
        await set_runtime_value(conn, SETUP_TOKEN_HASH_KEY, hash_token(bootstrap))
    env = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "LANG", "TMPDIR", "TMP", "TEMP")
        if k in os.environ
    }
    env.update(
        DAEMON_INTERNAL_API_URL=lineage.backend,
        NEXT_PUBLIC_API_URL=lineage.backend,
        NEXT_TELEMETRY_DISABLED="1",
        NODE_ENV="development",
    )
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    with (evidence / f"managed-inbox-{uuid4().hex}.log").open("xb") as log:
        try:
            async with node("tests/history_ui_next.mjs", env, log) as next_server:
                ui = (await stage(next_server, "ui"))["ui"]
                await lineage.original(s, ui, log)
                producer_pid = lineage.child.pid
                async with node("tests/history_inbox_two_jobs_browser.mjs", env, log) as browser:

                    async def send(value):
                        assert browser.stdin is not None
                        browser.stdin.write(value.encode() + b"\n")
                        await browser.stdin.drain()

                    await send(
                        json.dumps(
                            dict(
                                ui=ui,
                                backend=lineage.backend,
                                bootstrap=bootstrap,
                                managedRestart=True,
                            )
                        )
                    )
                    first = await stage(browser, "first")
                    await lineage.send("entered")
                    x_dispatch = await lineage.read()
                    await lineage.send("retire")
                    assert "refused" in await lineage.read()  # No cancellation of active X.
                    await lineage.send("release")
                    assert await lineage.read() == {"result": ["completed"], "dispatches": 1}
                    (x,) = await originals(s)
                    assert x_dispatch == {"task": str(x["id"])}
                    assert (
                        x["idempotency_key"] == first["key"] and x["financial_status"] == "settled"
                    )
                    before = await original_rows(s, x["id"])
                    prefix = (s.path / "LOG").read_bytes()
                    await check_ledger(s, 1)
                    await lineage.replace()
                    successor_pid = lineage.child.pid
                    assert producer_pid != successor_pid
                    with pytest.raises(RuntimeError, match="no replacement"):
                        await lineage.replace()
                    await send(json.dumps(identity(x)))
                    assert await stage(browser, "recovered") == {
                        "stage": "recovered",
                        "starts": 1,
                        "x": str(x["id"]),
                    }
                    assert await originals(s) == [x]
                    assert await original_rows(s, x["id"]) == before
                    handoff = (s.path / "LOG").read_bytes()
                    assert handoff.startswith(prefix) and len(handoff) > len(prefix)
                    await check_ledger(s, 1)
                    await send("explicit Y")
                    second = await stage(browser, "second")
                    assert second["lost"] == 1
                    await lineage.send("entered")
                    y_dispatch = await lineage.read()
                    original_x, y = await originals(s)
                    assert y_dispatch == {"task": str(y["id"])}
                    assert original_x == x and y["status"] == "running"
                    assert y["idempotency_key"] == second["key"]
                    for key in (
                        "id",
                        "conversation_id",
                        "user_message_id",
                        "result_message_id",
                        "idempotency_key",
                        "compute_scope_id",
                        "attempt_id",
                        "reservation_id",
                    ):
                        assert x[key] != y[key]
                    await send(json.dumps(identity(y)))
                    assert (await stage(browser, "phone"))["refreshed"]
                    await lineage.send("release")
                    assert await lineage.read() == {"result": ["completed"], "dispatches": 1}
                    await send("second committed")
                    finished = await stage(browser, "finished")
                    assert (finished["x"], finished["y"], finished["starts"]) == (
                        str(x["id"]),
                        str(y["id"]),
                        2,
                    )
                    assert await asyncio.wait_for(browser.wait(), 20) == 0
                    assert await original_rows(s, x["id"]) == before
                    assert (s.path / "LOG").read_bytes().startswith(handoff)
                    await check_ledger(s, 2)
                    x2, y2 = await originals(s)
                    assert (
                        x2 == x
                        and y2["financial_status"] == "settled"
                        and y2["outcome"] == "completed"
                    )
                    assert s.db.sql("SELECT count(*) FROM tasks") == "2"
                    assert s.db.sql("SELECT count(*) FROM task_attempts") == "2"
                    assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "2"
                    assert s.db.sql("SELECT count(*) FROM devices") == "2"
                    assert (
                        s.db.sql(
                            "SELECT count(*) FROM task_history_projection WHERE kind='SEND_POSSIBLE'"
                        )
                        == "2"
                    )
                    assert (
                        s.db.sql(
                            "SELECT count(*) FROM task_history_projection WHERE kind='INBOX_ORIGINAL_RETIRED'"
                        )
                        == "1"
                    )
                    await lineage.send("close")
                    assert await asyncio.wait_for(lineage.child.wait(), 20) == 0
                    evidence_rows = [
                        {
                            key: str(row[key])
                            for key in (
                                "id",
                                "attempt_id",
                                "conversation_id",
                                "result_message_id",
                                "reservation_id",
                                "period_key",
                                "actual_microusd",
                                "financial_status",
                            )
                        }
                        for row in (x2, y2)
                    ]
                    log.write(
                        json.dumps(
                            {
                                "qualified": "managed-clean-restart",
                                "pids": [producer_pid, successor_pid],
                                "dispatches": [x_dispatch["task"], y_dispatch["task"]],
                                "jobs": evidence_rows,
                            }
                        ).encode()
                        + b"\n"
                    )
        finally:
            await lineage.close_child()


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["managed-restart"], indirect=True)
@pytest.mark.parametrize(
    "cut",
    [
        "kill",
        "completed-kill",
        "launcher-eof",
        "stop",
        "transport",
        "terminal",
        "post-terminal-owner",
        "settlement",
        "handoff-rollback",
        "handoff-commit",
        "retirement-ack",
        "pool-close",
        "nonzero-exit",
    ],
)
async def test_failed_retirement_has_no_successor(text_host, lineage, cut):
    s = text_host
    _, headers = await device(s, s.account)
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    with (evidence / f"managed-inbox-cut-{cut}-{uuid4().hex}.log").open("xb") as log:
        try:
            await lineage.original(s, "http://127.0.0.1:1", log, fault=cut)
            async with httpx.AsyncClient(base_url=lineage.backend, headers=headers) as http:
                response = await http.post(
                    "/tasks/inbox-pilot",
                    headers={"Idempotency-Key": "original-x"},
                    json={"text": "Do a deep dive on X."},
                )
                assert response.status_code == 200
                await lineage.send("entered")
                task = (await lineage.read())["task"]
                if cut == "kill":
                    lineage.child.kill()
                    assert await lineage.child.wait() != 0
                elif cut == "launcher-eof":
                    lineage.channel.close()
                    assert await lineage.read() == {"stage": "fenced-shutdown"}
                    # No positive retirement ACK exists. A stuck local/remote
                    # read need not finish when the launcher disappears.
                    if lineage.child.returncode is None:
                        lineage.child.kill()
                    assert await lineage.child.wait() != 0
                else:
                    if cut == "stop":
                        assert (await http.post(f"/tasks/{task}/cancel")).status_code == 200
                    await lineage.send("joined" if cut == "stop" else "release")
                    outcome = await lineage.read()
                    assert outcome["dispatches"] == 1
                    if cut in (
                        "handoff-rollback",
                        "handoff-commit",
                        "retirement-ack",
                        "nonzero-exit",
                        "transport",
                        "post-terminal-owner",
                        "completed-kill",
                        "pool-close",
                    ):
                        assert outcome["result"] == ["completed"]
                    if cut == "stop":
                        assert outcome["result"] == ["cancelled"]
                    refused = await http.post(
                        "/tasks/inbox-pilot",
                        headers={"Idempotency-Key": "independent-y"},
                        json={"text": "Do a deep dive on Y."},
                    )
                    assert refused.status_code in (409, 503)
                    if cut == "completed-kill":
                        lineage.child.kill()
                        assert await lineage.child.wait() != 0
                with pytest.raises(RuntimeError, match="original"):
                    await lineage.replace()
                with pytest.raises(RuntimeError, match="no replacement"):
                    await lineage.replace()
                assert s.db.sql("SELECT count(*) FROM tasks") == "1"
                assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
                assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "1"
                assert (
                    s.db.sql(
                        "SELECT count(*) FROM task_history_projection WHERE kind='SEND_POSSIBLE'"
                    )
                    == "1"
                )
                if cut == "kill":
                    assert s.db.sql("SELECT status FROM entitlement_reservations") == "open"
                if cut in ("terminal", "settlement", "transport"):
                    assert s.db.sql("SELECT status FROM entitlement_reservations") == "settled"
                if cut in ("terminal", "transport"):
                    assert s.db.sql("SELECT status FROM tasks") == "completed"
                sql_fact = cut in ("handoff-commit", "retirement-ack", "nonzero-exit", "pool-close")
                assert s.db.sql(
                    "SELECT count(*) FROM task_history_projection WHERE kind='INBOX_ORIGINAL_RETIRED'"
                ) == str(int(sql_fact))
                assert (b"INBOX_ORIGINAL_RETIRED" in (s.path / "LOG").read_bytes()) == (
                    sql_fact or cut == "handoff-rollback"
                )
                if cut == "handoff-rollback":
                    from orchestrator.services.restore_history.model import HistoryError

                    with pytest.raises(HistoryError, match="reader_reconciliation_required"):
                        async with s.make():
                            pytest.fail("observer opened over an unprojected retirement")
                if cut in ("completed-kill", "terminal", "transport", "retirement-ack"):
                    from orchestrator.services.restore_history.acceptance_host import (
                        create_isolated_ui_app,
                    )

                    async with (
                        s.make(),
                        httpx.AsyncClient(
                            base_url="http://observation",
                            headers=headers,
                            transport=httpx.ASGITransport(app=create_isolated_ui_app(s.host)),
                        ) as observer,
                    ):
                        observed = await observer.get("/tasks/inbox-pilot", params={"topic": "X"})
                        assert observed.status_code == 200
                        assert observed.json()["state"] == "completed"
                        assert observed.json()["can_start"] == []
                        assert (
                            await observer.post(
                                "/tasks/inbox-pilot",
                                headers={"Idempotency-Key": "read-only"},
                                json={"text": "Do a deep dive on Y."},
                            )
                        ).status_code == 503
                        assert not s.execution._jobs and not s.execution._started
        finally:
            await lineage.close_child()


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["managed-restart"], indirect=True)
@pytest.mark.parametrize(
    "case", ["generation", "revoked", "stale-head", "y-commit", "poisoned-reader", "concurrent"]
)
async def test_successor_still_requires_whole_history_and_new_acceptance(text_host, lineage, case):
    s = text_host
    _, first = await device(s, s.account)
    second_device, second = await device(s, s.account)
    async with s.db.connection() as conn:
        foreign_account = await s.db.account(conn)
    _, foreign = await device(s, foreign_account)
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    with (evidence / f"managed-inbox-successor-{case}-{uuid4().hex}.log").open("xb") as log:
        try:
            await lineage.original(s, "http://127.0.0.1:1", log, fault=case)
            async with httpx.AsyncClient(base_url=lineage.backend) as http:

                async def submit(topic, key, headers):
                    return await http.post(
                        "/tasks/inbox-pilot",
                        headers={**headers, "Idempotency-Key": key},
                        json={"text": f"Do a deep dive on {topic}."},
                    )

                assert (await submit("X", "first", first)).status_code == 200
                await lineage.send("entered")
                task = (await lineage.read())["task"]
                await lineage.send("release")
                assert await lineage.read() == {"result": ["completed"], "dispatches": 1}
                prior_head = (s.path / "HEAD").read_bytes()
                await lineage.replace()
                known = await http.get(f"/tasks/{task}", headers=foreign)
                unknown = await http.get(f"/tasks/{uuid4()}", headers=foreign)
                assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
                assert known.status_code != 200
                assert (await submit("Y", "foreign", foreign)).status_code == 409
                assert (await submit("Y", "first", second)).status_code == 409
                if case == "generation":
                    async with s.db.connection() as conn:
                        await conn.execute(
                            "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
                            s.account,
                        )
                if case == "stale-head":
                    (s.path / "HEAD").write_bytes(prior_head)
                if case == "revoked":
                    async with s.db.connection() as conn:
                        await conn.execute(
                            "UPDATE devices SET revoked_at=now() WHERE id=$1", second_device
                        )
                if case == "concurrent":
                    replies = await asyncio.gather(
                        submit("Y", "second", first), submit("Y", "third", second)
                    )
                    assert sorted(r.status_code for r in replies) == [200, 409]
                    await lineage.send("entered")
                    assert (await lineage.read())["task"] != task
                else:
                    refusal = (401,) if case == "revoked" else (409, 503)
                    assert (await submit("Y", "second", second)).status_code in refusal
                    assert (await submit("Y", "second", second)).status_code in refusal
                await lineage.send("release")
                if case == "concurrent":
                    assert await lineage.read() == {"result": ["completed"], "dispatches": 1}
                    await check_ledger(s, 2)
                else:
                    assert await lineage.read() == {"result": [], "dispatches": 0}
                    await check_ledger(s, 1)
                    assert s.db.sql("SELECT count(*) FROM tasks") == (
                        "2" if case in ("y-commit", "poisoned-reader") else "1"
                    )
                    assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
                await lineage.send("close")
                assert await asyncio.wait_for(lineage.child.wait(), 20) == 0
        finally:
            await lineage.close_child()
