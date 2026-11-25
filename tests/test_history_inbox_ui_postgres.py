"""Disposable PostgreSQL negatives for the explicitly composed inbox pilot."""

import asyncio
from dataclasses import replace
import json
from uuid import uuid4

from asyncpg.transaction import Transaction
from httpx import ASGITransport, AsyncClient
import pytest

from orchestrator.services.restore_history.acceptance_host import create_isolated_ui_app
from orchestrator.tasks.inputs import canonical_json
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_text_execution_postgres import (
    db as db,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)

PATH = "/tasks/inbox-pilot"
TEXT = "Do a deep dive on X."

pytestmark = pytest.mark.asyncio


def client(s):
    return AsyncClient(
        transport=ASGITransport(create_isolated_ui_app(s.host)), base_url="http://fixture.test"
    )


async def start(http, headers, key=None, **extra):
    return await http.post(
        PATH,
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json={"text": TEXT, **extra},
    )


def effects(s):
    return s.db.sql(
        """SELECT (SELECT count(*) FROM tasks),(SELECT count(*) FROM conversations),
                  (SELECT count(*) FROM messages),(SELECT count(*) FROM task_attempts),
                  (SELECT count(*) FROM entitlement_reservations)"""
    )


async def test_default_off_and_caller_cannot_mark_a_normal_turn(text_host):
    s = text_host
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await http.get(PATH, headers=headers)).status_code != 200
        assert (await start(http, headers)).status_code != 200
        assert effects(s) == "0|0|0|0|0"
        given = f"ibox1:{uuid4()}:{uuid4()}"
        s.release.set()
        sent = await http.post(
            "/chat",
            headers={**headers, "Idempotency-Key": given},
            json={"message": TEXT, "purpose": "isolated-inbox-research-v1"},
        )
        assert sent.status_code == 200
        assert await jobs(s) == ["completed"]
        async with s.db.connection() as conn:
            wire = await conn.fetchval("SELECT input_ciphertext FROM tasks")
        canonical = json.loads(s.host._acceptance._tasks._open(wire)["_request"])
        assert "purpose" not in canonical
        assert len(s.calls) == 1
        retained = effects(s)

    s.inbox_pilot = True
    async with s.make(), client(s) as http:
        status = await http.get(PATH, headers=headers)
        assert status.status_code in (409, 503)
        assert "conversation_id" not in status.json()
        assert (await start(http, headers)).status_code in (409, 503)
        assert not s.execution._scheduled and len(s.calls) == 1
        assert effects(s) == retained


async def test_nonempty_native_account_refuses_before_accepting(text_host):
    s = text_host
    s.inbox_pilot = True
    _, headers = await device(s, s.account)
    async with s.db.connection() as conn:
        await conn.execute(
            "INSERT INTO conversations(id,user_id,title,pipeline) VALUES($1,$2,'Existing','cloud')",
            uuid4(),
            s.account,
        )
    async with s.make(), client(s) as http:
        assert (await http.get(PATH, headers=headers)).status_code in (409, 503)
        assert (await start(http, headers)).status_code in (409, 503)
        assert effects(s) == "0|1|0|0|0"
        assert not s.execution._scheduled and not s.calls


async def test_competing_keys_and_replay_cannot_replace_the_original(text_host):
    s = text_host
    s.inbox_pilot = True
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await http.get(PATH)).status_code == 401
        assert (await start(http, headers, purpose="isolated-inbox-research-v1")).status_code == 422
        standard = await http.post(
            "/chat",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"message": TEXT},
        )
        assert standard.status_code in (404, 405, 503)
        assert effects(s) == "0|0|0|0|0"
        assert (await http.get(PATH, headers=headers)).json() == {"state": "empty"}

        keys = (str(uuid4()), str(uuid4()))
        results = await asyncio.gather(*(start(http, headers, key) for key in keys))
        assert sorted(result.status_code for result in results) == [200, 409]
        key = keys[0 if results[0].status_code == 200 else 1]
        async with asyncio.timeout(10):
            await s.entered.wait()
        assert (await start(http, headers, key)).status_code == 409
        assert not s.host._acceptance._blocked and not s.host._acceptance._history._blocked
        async with s.db.connection() as conn:
            row = await conn.fetchrow("SELECT id,idempotency_key,input_ciphertext FROM tasks")
        assert row["idempotency_key"] == key
        assert s.execution._scheduled == {row["id"]}
        canonical = json.loads(s.host._acceptance._tasks._open(row["input_ciphertext"])["_request"])
        assert canonical["purpose"] == "isolated-inbox-research-v1"
        assert effects(s) == "1|1|2|1|1"
        assert len(s.calls) == 1
        assert (await http.get(PATH, headers=headers)).json() == {"state": "running"}
        s.release.set()
        assert await jobs(s) == ["completed"]


@pytest.mark.parametrize("after_commit", [False, True], ids=["rollback", "lost-db-ack"])
async def test_uncertain_native_acceptance_never_dispatches(text_host, monkeypatch, after_commit):
    s = text_host
    s.inbox_pilot = True
    _, headers = await device(s, s.account)
    _, second = await device(s, s.account)
    key = str(uuid4())
    async with s.make(), client(s) as http:
        projection = s.host._acceptance._projection
        real_stage = projection.stage
        real_exit = Transaction.__aexit__
        target = None
        fault_called = False

        async def stage(conn, entry, **kwargs):
            nonlocal target
            result = await real_stage(conn, entry, **kwargs)
            target = conn._con._top_xact
            assert target is not None
            return result

        async def exit_fault(tx, kind, value, traceback):
            nonlocal target, fault_called
            if tx is not target or kind is not None:
                return await real_exit(tx, kind, value, traceback)
            target = None
            fault_called = True
            assert effects(s) == "0|0|0|0|0"
            error = ConnectionError("synthetic acceptance outcome loss")
            if after_commit:
                await real_exit(tx, kind, value, traceback)
            else:
                await real_exit(tx, type(error), error, None)
            raise error

        with monkeypatch.context() as fault:
            fault.setattr(projection, "stage", stage)
            fault.setattr(Transaction, "__aexit__", exit_fault)
            assert (await start(http, headers, key)).status_code == 503
        assert fault_called
        expected = "1|1|2|0|0" if after_commit else "0|0|0|0|0"
        assert effects(s) == expected
        observed = await http.get(PATH, headers=second)
        if after_commit:
            assert observed.status_code == 200
            assert observed.json() == {"state": "queued"}
        else:
            assert observed.status_code in (409, 503)
        assert (await start(http, second, key)).status_code in (409, 503)
        assert effects(s) == expected
        assert not s.execution._scheduled and not s.calls


async def test_generation_rotated_between_capture_and_acceptance_refuses(text_host, monkeypatch):
    s = text_host
    s.inbox_pilot = True
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        previous = s.host._acceptance._accept_owned
        changed = False

        async def rotated(*args, **kwargs):
            nonlocal changed
            async with s.db.connection() as conn:
                result = await conn.execute(
                    "UPDATE entitlement_accounts SET content_generation=content_generation+1 "
                    "WHERE user_id=$1",
                    s.account,
                )
            assert result == "UPDATE 1"
            changed = True
            return await previous(*args, **kwargs)

        monkeypatch.setattr(s.host._acceptance, "_accept_owned", rotated)
        assert (await start(http, headers)).status_code in (409, 503)
        assert changed
        assert effects(s) == "0|0|0|0|0"
        assert s.db.sql("SELECT count(*) FROM task_history_acceptance") == "0"
        assert not s.execution._scheduled and not s.calls


async def test_start_does_not_acknowledge_a_mismatched_readback_receipt(text_host, monkeypatch):
    s = text_host
    s.inbox_pilot = True
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        submit = s.host._submit_authenticated

        async def mismatched(*args, **kwargs):
            receipt = await submit(*args, **kwargs)
            return replace(receipt, task=replace(receipt.task, result_message_id=uuid4()))

        monkeypatch.setattr(s.host, "_submit_authenticated", mismatched)
        assert (await start(http, headers)).status_code == 503
        async with asyncio.timeout(10):
            await s.entered.wait()
        assert (await http.get(PATH, headers=headers)).json() == {"state": "running"}
        assert effects(s) == "1|1|2|1|1"
        assert len(s.calls) == 1
        s.release.set()
        assert await jobs(s) == ["completed"]


@pytest.mark.parametrize("damage", ["empty-result", "native-purpose", "new-generation"])
async def test_completed_snapshot_refuses_unverified_report(text_host, damage):
    s = text_host
    s.inbox_pilot = True
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        s.release.set()
        assert (await start(http, headers)).status_code == 200
        assert await jobs(s) == ["completed"]
        before = await http.get(PATH, headers=headers)
        assert before.status_code == 200
        assert "no-store" in before.headers["cache-control"]
        assert set(before.json()) == {"state", "conversation_id"}
        conversation = before.json()["conversation_id"]
        assert before.json()["state"] == "completed"
        async with s.db.connection() as conn:
            if damage == "empty-result":
                assert (
                    await conn.execute(
                        "UPDATE messages SET content=$1 WHERE id=(SELECT result_message_id FROM tasks)",
                        s.execution.encryption.encrypt(""),
                    )
                    == "UPDATE 1"
                )
            elif damage == "native-purpose":
                store = s.host._acceptance._tasks
                stored = store._open(await conn.fetchval("SELECT input_ciphertext FROM tasks"))
                canonical = json.loads(stored["_request"])
                assert canonical.pop("purpose") == "isolated-inbox-research-v1"
                stored["_request"] = canonical_json(canonical)
                assert (
                    await conn.execute("UPDATE tasks SET input_ciphertext=$1", store._seal(stored))
                    == "UPDATE 1"
                )
            else:
                assert (
                    await conn.execute(
                        "UPDATE entitlement_accounts SET content_generation=content_generation+1 "
                        "WHERE user_id=$1",
                        s.account,
                    )
                    == "UPDATE 1"
                )
        refused = await http.get(PATH, headers=headers)
        assert refused.status_code in (409, 503)
        assert "conversation_id" not in refused.json()
        assert (await http.get(f"/conversations/{conversation}", headers=headers)).status_code in (
            409,
            503,
        )
        assert (await start(http, headers)).status_code in (409, 503)
        assert effects(s) == "1|1|2|1|1"
        assert len(s.calls) == 1
