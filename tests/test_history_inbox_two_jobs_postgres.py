"""Sequential isolated inbox: original task identities and fail-closed follow-up."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import asyncpg
from asyncpg.transaction import Transaction
import pytest

from tests.test_history_inbox_ui_postgres import PATH, client, effects
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_text_execution_postgres import (
    db as db,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)

pytestmark = pytest.mark.asyncio


async def start(http, headers, topic, key=None):
    return await http.post(
        PATH,
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json={"text": f"Do a deep dive on {topic}."},
    )


async def status(http, headers, topic=None):
    response = await http.get(PATH, headers=headers, params={"topic": topic} if topic else {})
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    return response.json()


def enable(s):
    s.inbox_pilot = True
    s.inbox_two_jobs = True


async def first_completed(s, http, headers):
    s.synthetic_deltas = ("Synthetic report X.", " First fixture.")
    s.release.set()
    key = str(uuid4())
    assert (await start(http, headers, "X", key)).status_code == 200
    assert await jobs(s) == ["completed"], s.errors
    original = await status(http, headers, "X")
    assert original["state"] == "completed"
    assert original["can_start"] == ["Y"]
    s.entered.clear()
    s.release.clear()
    s.closed.clear()
    s.synthetic_deltas = ("Synthetic report Y.", " Second fixture.")
    return key, original["conversation_id"]


async def test_distinct_originals_selection_and_complete_list(text_host):
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert await status(http, first) == {"state": "empty", "can_start": ["X", "Y"]}
        key_x = str(uuid4())
        s.synthetic_deltas = ("Synthetic report X.", " First fixture.")
        assert (await start(http, first, "X", key_x)).status_code == 200
        await asyncio.wait_for(s.entered.wait(), 10)
        assert (await start(http, second, "Y")).status_code == 409
        assert effects(s) == "1|1|2|1|1"
        assert (await status(http, second))["topic"] == "X"
        s.release.set()
        assert await jobs(s) == ["completed"], s.errors
        x = await status(http, second, "X")
        assert x["can_start"] == ["Y"]
        assert (await status(http, second, "Y"))["state"] == "missing"
        assert (await start(http, first, "X")).status_code == 409
        assert (await start(http, first, "Y", key_x)).status_code == 409
        assert not s.host._acceptance._blocked
        s.entered.clear()
        s.release.clear()
        s.closed.clear()
        s.synthetic_deltas = ("Synthetic report Y.", " Second fixture.")
        key_y = str(uuid4())
        assert (await start(http, first, "Y", key_y)).status_code == 200
        await asyncio.wait_for(s.entered.wait(), 10)
        assert await status(http, second) == {
            "state": "ambiguous",
            "choices": ["X", "Y"],
            "can_start": [],
        }
        assert (await status(http, second, "X"))["conversation_id"] == x["conversation_id"]
        assert await status(http, second, "Y") == {
            "topic": "Y",
            "state": "running",
            "can_start": [],
        }
        assert (await start(http, second, "Y", key_y)).status_code == 409
        assert (await start(http, second, "X")).status_code == 409
        assert effects(s) == "2|2|4|2|2"
        listing = await http.get(
            "/conversations", headers=second, params={"pagination": "cursor", "limit": 1}
        )
        assert listing.status_code == 200
        page = listing.json()
        assert page["total"] == 2 and page["page"]["has_more"]
        next_page = await http.get(
            "/conversations",
            headers=second,
            params={"pagination": "cursor", "limit": 1, "cursor": page["page"]["next_cursor"]},
        )
        assert next_page.status_code == 200
        last = next_page.json()
        assert last["total"] == 2 and not last["page"]["has_more"]
        assert page["conversations"][0]["id"] != last["conversations"][0]["id"]
        s.release.set()
        assert await jobs(s) == ["completed", "completed"], s.errors
        y = await status(http, second, "Y")
        assert y["conversation_id"] != x["conversation_id"]
        async with s.db.connection() as conn:
            rows = await conn.fetch(
                "SELECT id,idempotency_key,conversation_id,result_message_id,attempt_count,status "
                "FROM tasks WHERE user_id=$1 ORDER BY idempotency_key",
                s.account,
            )
        assert len(rows) == 2
        by_key = {row["idempotency_key"]: row for row in rows}
        for key, result, content in (
            (key_x, x, "Synthetic report X. First fixture."),
            (key_y, y, "Synthetic report Y. Second fixture."),
        ):
            row = by_key[key]
            assert (row["attempt_count"], row["status"]) == (1, "completed")
            assert str(row["conversation_id"]) == result["conversation_id"]
            response = await http.get(f"/conversations/{row['conversation_id']}", headers=second)
            assert response.status_code == 200, response.text
            detail = response.json()
            assert detail["latest_task"]["id"] == str(row["id"])
            assert detail["messages"][-1]["id"] == str(row["result_message_id"])
            assert detail["messages"][-1]["content"] == content
        assert effects(s) == "2|2|4|2|2"
        assert {call.task_id for call in s.calls} == {r["id"] for r in rows}


@pytest.mark.parametrize("outcome", ["cancelled", "unavailable"])
async def test_second_cancel_or_unqualified_failure_no_report(text_host, monkeypatch, outcome):
    from orchestrator.tasks import runner

    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.1)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.1)
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make(), client(s) as http:
        await first_completed(s, http, first)
        s.unsolicited = outcome == "unavailable"
        key = str(uuid4())
        response = await start(http, first, "Y", key)
        assert response.status_code in (200, 409, 503)
        await asyncio.wait_for(s.entered.wait(), 10)
        async with s.db.connection() as conn:
            task = await conn.fetchval("SELECT id FROM tasks WHERE idempotency_key=$1", key)
        if outcome == "cancelled":
            result = await http.post(f"/tasks/{task}/cancel", headers=second)
            assert result.status_code == 200
        results = await jobs(s)
        assert sorted(results) == sorted(
            ["completed", "cancelled" if outcome == "cancelled" else "needs_attention"]
        )
        selected = await http.get(PATH, headers=second, params={"topic": "Y"})
        if outcome == "cancelled":
            assert selected.status_code == 200
            assert selected.json() == {"state": "cancelled", "topic": "Y", "can_start": []}
        else:
            assert selected.status_code in (409, 503)
            assert "conversation_id" not in selected.json()
        assert (await start(http, second, "Y", key)).status_code in (409, 503)
        assert effects(s) == "2|2|4|2|2"
        assert len(s.calls) == 2


@pytest.mark.parametrize("after_commit", [False, True])
async def test_second_acceptance_uncertain_is_never_replayed(text_host, monkeypatch, after_commit):
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make(), client(s) as http:
        await first_completed(s, http, first)
        projection = s.host._acceptance._projection
        real_stage = projection.stage
        real_exit = Transaction.__aexit__
        target = None
        injected = False

        async def stage(conn, entry, **kwargs):
            nonlocal target
            result = await real_stage(conn, entry, **kwargs)
            target = conn._con._top_xact
            assert target is not None
            return result

        async def lose_ack(tx, kind, value, trace):
            nonlocal target, injected
            if tx is not target or kind is not None:
                return await real_exit(tx, kind, value, trace)
            target = None
            injected = True
            assert effects(s) == "1|1|2|1|1"
            error = ConnectionError("synthetic second acceptance acknowledgement loss")
            if after_commit:
                await real_exit(tx, kind, value, trace)
            else:
                await real_exit(tx, type(error), error, None)
            raise error

        key = str(uuid4())
        with monkeypatch.context() as fault:
            fault.setattr(projection, "stage", stage)
            fault.setattr(Transaction, "__aexit__", lose_ack)
            assert (await start(http, first, "Y", key)).status_code == 503
        assert injected
        expected = "2|2|4|1|1" if after_commit else "1|1|2|1|1"
        assert effects(s) == expected
        if after_commit:
            assert (await status(http, second, "Y"))["state"] == "queued"
        else:
            assert (await http.get(PATH, headers=second)).status_code in (409, 503)
        assert (await start(http, second, "Y", key)).status_code in (409, 503)
        assert effects(s) == expected and len(s.calls) == 1
        assert len(s.execution._scheduled) == 1


@pytest.mark.parametrize("change", ["generation", "revocation", "clean-release"])
async def test_phase_boundary_reauth_and_no_retained_content_lock(text_host, monkeypatch, change):
    s = text_host
    enable(s)
    device_id, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make(), client(s) as http:
        await first_completed(s, http, first)
        precheck = s.host._inbox_precheck
        entered = asyncio.Event()
        writer = None
        updated = asyncio.Event()

        async def native_writer():
            # Must queue behind the phase-a account SHARE, then acquire after
            # its transaction physically ends and before native A starts.
            async with s.db.connection() as conn, conn.transaction():
                await conn.execute("SET LOCAL lock_timeout='3s'")
                entered.set()
                await conn.fetchrow(
                    "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE",
                    s.account,
                )
                if change == "generation":
                    await conn.execute(
                        "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
                        s.account,
                    )
                if change == "revocation":
                    await conn.execute("UPDATE devices SET revoked_at=now() WHERE id=$1", device_id)
            updated.set()

        original_check = s.host._inbox_originals

        async def check(*args, **kwargs):
            nonlocal writer
            result = await original_check(*args, **kwargs)
            if writer is None:
                writer = asyncio.create_task(native_writer())
                await asyncio.wait_for(entered.wait(), 3)
            return result

        async def between_phases(*args, **kwargs):
            checked = await precheck(*args, **kwargs)
            assert writer is not None
            await asyncio.wait_for(writer, 4)
            # H is still held even though the first SQL transaction settled.
            assert updated.is_set()
            args[0]._check()
            return checked

        with monkeypatch.context() as fault:
            fault.setattr(s.host, "_inbox_originals", check)
            fault.setattr(s.host, "_inbox_precheck", between_phases)
            response = await start(http, first, "Y")
        if change == "clean-release":
            assert response.status_code == 200, response.text
            await asyncio.wait_for(s.entered.wait(), 10)
            s.release.set()
            assert await jobs(s) == ["completed", "completed"], s.errors
        else:
            assert response.status_code == (401 if change == "revocation" else 409)
            assert effects(s) == "1|1|2|1|1" and len(s.calls) == 1
            if change == "revocation":
                assert (await status(http, second, "X"))["state"] == "completed"


async def test_phase_a_cleanup_failure_does_not_admit_second(text_host, monkeypatch):
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    async with s.make(), client(s) as http:
        await first_completed(s, http, first)
        # Lose acknowledgement of the real phase-a W commit only.
        original = Transaction.__aexit__
        target = None
        checked = s.host._inbox_originals
        called = False

        async def mark(reader, operation, conn, *args):
            nonlocal target
            result = await checked(reader, operation, conn, *args)
            target = conn._con._top_xact
            return result

        async def lose(tx, kind, error, trace):
            nonlocal called
            result = await original(tx, kind, error, trace)
            if tx is target and kind is None:
                called = True
                raise ConnectionError("synthetic phase-a acknowledgement loss")
            return result

        with monkeypatch.context() as fault:
            fault.setattr(s.host, "_inbox_originals", mark)
            fault.setattr(Transaction, "__aexit__", lose)
            assert (await start(http, first, "Y")).status_code == 503
        assert called and effects(s) == "1|1|2|1|1" and len(s.calls) == 1


async def test_phase_a_holder_release_failure_does_not_admit_second(text_host, monkeypatch):
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    async with s.make(), client(s) as http:
        await first_completed(s, http, first)
        original_release = asyncpg.Pool.release
        checked = s.host._inbox_originals
        target = None
        called = False

        async def mark(reader, operation, conn, *args):
            nonlocal target
            result = await checked(reader, operation, conn, *args)
            target = conn
            return result

        async def lose(pool, conn, *args, **kwargs):
            nonlocal called
            await original_release(pool, conn, *args, **kwargs)
            if conn is target:
                called = True
                raise ConnectionError("synthetic phase-a release acknowledgement loss")

        with monkeypatch.context() as fault:
            fault.setattr(s.host, "_inbox_originals", mark)
            fault.setattr(asyncpg.Pool, "release", lose)
            assert (await start(http, first, "Y")).status_code == 503
        assert called and effects(s) == "1|1|2|1|1" and len(s.calls) == 1
        assert s.host._acceptance._blocked


async def test_failed_observation_owner_cannot_admit_second(text_host, monkeypatch):
    from orchestrator.services.restore_history.model import HistoryError

    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    async with s.make(), client(s) as http:
        await first_completed(s, http, first)

        async def unavailable(*args, **kwargs):
            raise HistoryError("synthetic_observation_failure")

        with monkeypatch.context() as fault:
            fault.setattr(s.host._reader, "_parity_owned", unavailable)
            assert (await http.get(PATH, headers=first)).status_code == 503
        assert s.host._reader._history._blocked
        assert not s.host._acceptance._blocked
        assert (await start(http, first, "Y")).status_code == 503
        assert len(s.execution._scheduled) == len(s.calls) == 1
        assert effects(s) == "1|1|2|1|1"


async def test_post_receipt_cannot_select_other_original(text_host, monkeypatch):
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    async with s.make(), client(s) as http:
        submit = s.host._submit_authenticated
        prior = None

        async def substitute(*args, **kwargs):
            nonlocal prior
            receipt = await submit(*args, **kwargs)
            if prior is None:
                prior = receipt.task
                return receipt
            return replace(receipt, task=prior)

        monkeypatch.setattr(s.host, "_submit_authenticated", substitute)
        await first_completed(s, http, first)
        response = await start(http, first, "Y")
        assert response.status_code == 503 and "conversation_id" not in response.json()
        await asyncio.wait_for(s.entered.wait(), 10)
        assert (await status(http, first, "Y"))["state"] == "running"
        s.release.set()
        assert await jobs(s) == ["completed", "completed"], s.errors
        assert len(s.calls) == 2 and effects(s) == "2|2|4|2|2"


@pytest.mark.parametrize("damage", ["extra-conversation", "old-generation", "wrong-purpose"])
async def test_whole_envelope_refuses_mixed_or_unverified_history(text_host, damage):
    import json

    from orchestrator.tasks.inputs import canonical_json

    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    async with s.make(), client(s) as http:
        await first_completed(s, http, first)
        async with s.db.connection() as conn:
            if damage == "extra-conversation":
                await conn.execute(
                    "INSERT INTO conversations(id,user_id,title,pipeline) VALUES($1,$2,'Other','cloud')",
                    uuid4(),
                    s.account,
                )
            elif damage == "old-generation":
                await conn.execute(
                    "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
                    s.account,
                )
            else:
                store = s.host._acceptance._tasks
                value = store._open(await conn.fetchval("SELECT input_ciphertext FROM tasks"))
                request = json.loads(value["_request"])
                request["purpose"] = "isolated-inbox-research-v1"
                value["_request"] = canonical_json(request)
                await conn.execute("UPDATE tasks SET input_ciphertext=$1", store._seal(value))
        for topic in (None, "X", "Y"):
            observed = await http.get(PATH, headers=first, params={"topic": topic} if topic else {})
            assert observed.status_code in (409, 503)
            assert "conversation_id" not in observed.json()
        assert (await start(http, first, "Y")).status_code in (409, 503)
        assert len(s.execution._scheduled) == len(s.calls) == 1


async def test_reconstructed_host_reads_first_but_cannot_admit_second(text_host):
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    async with s.make(), client(s) as http:
        _, conversation = await first_completed(s, http, first)
    async with s.make(), client(s) as http:
        observed = await status(http, first)
        assert observed == {
            "state": "completed",
            "topic": "X",
            "conversation_id": conversation,
            "can_start": [],
        }
        assert (await start(http, first, "Y")).status_code == 409
        assert not s.execution._scheduled and len(s.calls) == 1


async def test_completed_sql_is_not_second_start_authority_until_runner_returns(
    text_host, monkeypatch
):
    from orchestrator.services.restore_history import text_execution

    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    done, release_owner = asyncio.Event(), asyncio.Event()
    original_run = text_execution.run_chat_task

    async def hold_return(*args):
        result = await original_run(*args)
        assert result == "completed"
        done.set()
        await release_owner.wait()
        return result

    monkeypatch.setattr(text_execution, "run_chat_task", hold_return)
    async with s.make(), client(s) as http:
        s.release.set()
        assert (await start(http, first, "X")).status_code == 200
        try:
            await asyncio.wait_for(done.wait(), 10)
            observed = await status(http, first, "X")
            assert observed["state"] == "completed" and observed["can_start"] == []
            assert (await start(http, first, "Y")).status_code == 409
            assert effects(s) == "1|1|2|1|1"
        finally:
            release_owner.set()
        assert await jobs(s) == ["completed"]
        assert (await status(http, first, "X"))["can_start"] == ["Y"]


async def test_two_job_foreign_account_refusal_and_no_hidden_start(text_host):
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    async with s.db.connection() as conn:
        foreign = await s.db.account(conn)
    _, other = await device(s, foreign)
    async with s.make(), client(s) as http:
        _, x = await first_completed(s, http, first)
        assert (await start(http, first, "Y")).status_code == 200
        await asyncio.wait_for(s.entered.wait(), 10)
        assert (await status(http, other, "X"))["state"] == "missing"
        assert (await status(http, other, "Y"))["state"] == "missing"
        async with s.db.connection() as conn:
            rows = await conn.fetch("SELECT id,conversation_id FROM tasks ORDER BY id")
        for row in rows:
            for path, ident in (("tasks", row["id"]), ("conversations", row["conversation_id"])):
                known = await http.get(f"/{path}/{ident}", headers=other)
                unknown = await http.get(f"/{path}/{uuid4()}", headers=other)
                assert known.status_code == unknown.status_code == 404
                assert known.json() == unknown.json()
        assert (await status(http, first, "X"))["conversation_id"] == x
        assert len(s.calls) == 2 and effects(s) == "2|2|4|2|2"
        s.release.set()
        assert await jobs(s) == ["completed", "completed"], s.errors


async def test_singleton_composition_does_not_read_or_extend_v2_history(text_host):
    s = text_host
    enable(s)
    _, first = await device(s, s.account)
    async with s.make(), client(s) as http:
        await first_completed(s, http, first)
        assert (await start(http, first, "Z")).status_code == 422
    s.inbox_two_jobs = False
    async with s.make(), client(s) as http:
        assert (await http.get(PATH, headers=first)).status_code == 409
        assert (await start(http, first, "Y")).status_code == 422
        assert (await start(http, first, "X")).status_code == 409
        assert not s.execution._scheduled and len(s.calls) == 1
