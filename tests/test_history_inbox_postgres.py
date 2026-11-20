"""#761 proof on disposable PostgreSQL, actual bearer sessions and protected H files."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from asyncpg.transaction import Transaction
from httpx import ASGITransport, AsyncClient, ReadError
import pytest
import pytest_asyncio

from orchestrator.auth_tokens import hash_token
from orchestrator.services.restore_history.coordination import HistoryOperation
from orchestrator.services.restore_history.model import AcceptPrepare
from tests.history_inbox_support import DDL, create_fixture_inbox_app
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_observation_boundary_postgres import device
from tests.test_terminal_history_acceptance_host_postgres import (
    entries,
    terminal_host as terminal_host,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def inbox(terminal_host, monkeypatch):
    s = terminal_host
    s.db.sql(DDL)
    async with s.db.connection() as conn:
        s.foreign = await s.db.account(conn)
        for owner in (s.account, s.foreign):
            await conn.execute(
                """INSERT INTO public.fixture_inboxes (id,account_id,generation)
                   SELECT $1,user_id,content_generation FROM public.entitlement_accounts
                   WHERE user_id=$2""",
                uuid4(),
                owner,
            )
    da1, s.a1 = await device(s, s.account)
    da2, s.a2 = await device(s, s.account)
    db1, s.b = await device(s, s.foreign)
    assert len({da1, da2, db1}) == 3
    async with s.db.connection() as conn:
        for headers, owner, dev in (
            (s.a1, s.account, da1),
            (s.a2, s.account, da2),
            (s.b, s.foreign, db1),
        ):
            assert tuple(
                await conn.fetchrow(
                    "SELECT user_id,device_id FROM public.sessions WHERE access_token_hash=$1",
                    hash_token(headers["Authorization"].removeprefix("Bearer ")),
                )
            ) == (owner, dev)
    assert s.account != s.foreign
    s.claim = AsyncMock(side_effect=AssertionError("acceptance fixture attempted to claim"))
    s.schedule = Mock(side_effect=AssertionError("acceptance fixture attempted to schedule"))
    monkeypatch.setattr("orchestrator.tasks.store.TaskStore.claim", s.claim)
    monkeypatch.setattr(
        "orchestrator.services.restore_history.text_execution.HistoryTextExecution.schedule",
        s.schedule,
    )
    async with s.make() as host:
        s.v = host
        s.app = create_fixture_inbox_app(host.host)
        async with AsyncClient(
            transport=ASGITransport(s.app), base_url="http://fixture.test"
        ) as http:
            s.http = http
            yield s
    s.claim.assert_not_called()
    s.schedule.assert_not_called()
    assert tuple(
        s.db.sql(
            """SELECT (SELECT count(*) FROM public.task_attempts),
                  (SELECT count(*) FROM public.task_operations),
                  (SELECT count(*) FROM public.tasks WHERE attempt_count<>0 OR wake_seq<>0)"""
        ).split("|")
    ) == ("0", "0", "0")


async def start(s, headers, key, text):
    r = await s.http.post(
        "/inbox/starts", headers={**headers, "Idempotency-Key": str(key)}, json={"text": text}
    )
    assert r.headers["cache-control"] == "no-store"
    return r


async def retrieve(s, headers, key):
    return await s.http.get(f"/inbox/starts/by-key/{key}", headers=headers)


async def status(s, headers, text, ref=None):
    return await s.http.post(
        "/inbox/status", headers=headers, json={"text": text, "reply_to_item": ref}
    )


def bundle_counts(s):
    return s.db.sql(
        """SELECT (SELECT count(*) FROM public.fixture_inbox_links),
                  (SELECT count(*) FROM public.conversations),
                  (SELECT count(*) FROM public.tasks),
                  (SELECT count(*) FROM public.messages),
                  (SELECT count(*) FROM public.task_events),
                  (SELECT count(*) FROM public.task_history_projection)"""
    )


async def test_two_jobs_and_cross_account_scope(inbox):
    s = inbox
    key, other_key = uuid4(), uuid4()
    first = await start(s, s.a1, key, "Heat pumps")
    second = await start(s, s.a2, other_key, "Window insulation")
    foreign = await start(s, s.b, key, "Roof insulation")
    assert first.status_code == second.status_code == foreign.status_code == 201
    assert bundle_counts(s) == "3|3|3|6|3|3"
    assert len(entries(s)) == 3
    items_a = first.json()["items"] + second.json()["items"]
    items_b = foreign.json()["items"]
    for creds in (s.a1, s.a2):
        response = await s.http.get("/inbox", headers=creds)
        assert response.status_code == 200
        assert response.json() == {"items": items_a}
        assert (await retrieve(s, creds, key)).json() == first.json()
    assert (await s.http.get("/inbox", headers=s.b)).json() == {"items": items_b}
    assert (await retrieve(s, s.b, key)).json() == foreign.json()

    vague = await status(s, s.a2, "How’s that going?")
    assert vague.json() == {
        "kind": "clarify",
        "text": "Which job?",
        "choices": [
            {"ref": items_a[1]["id"], "title": "Heat pumps"},
            {"ref": items_a[3]["id"], "title": "Window insulation"},
        ],
    }
    assert (await status(s, s.a1, "  WINDOW  INSULATION  ")).json()["ref"] == items_a[3]["id"]
    assert (await status(s, s.a2, "Window insulation", items_a[0]["id"])).json() == {
        "kind": "queued",
        "ref": items_a[1]["id"],
        "text": "Saved. I can't confirm it has started.",
    }
    bad_ref = await status(s, s.b, "Roof insulation", items_a[0]["id"])
    missing_ref = await status(s, s.b, "Roof insulation", f"request:{uuid4()}")
    assert bad_ref.status_code == missing_ref.status_code == 404
    assert (
        bad_ref.json()
        == missing_ref.json()
        == {"detail": {"code": "no_match", "retry_safe": False}}
    )
    assert (await status(s, s.b, "Heat pumps")).json() == bad_ref.json()
    assert (await retrieve(s, s.b, other_key)).json() == (await retrieve(s, s.b, uuid4())).json()
    assert (await start(s, s.a1, uuid4(), "Third A job")).status_code == 409
    assert (await start(s, s.b, uuid4(), "Second B job")).status_code == 201
    assert bundle_counts(s) == "4|4|4|8|4|4"


async def test_same_key_race_and_changed_input(inbox):
    s, key = inbox, uuid4()
    results = await asyncio.gather(start(s, s.a1, key, "Same job"), start(s, s.a2, key, "Same job"))
    accepted, recovery = sorted(results, key=lambda r: r.status_code)
    assert accepted.status_code == 201
    assert recovery.status_code == 409
    assert recovery.json()["detail"] == {
        "code": "acceptance_recovery_required",
        "retry_safe": False,
    }
    assert (await retrieve(s, s.a1, key)).json() == accepted.json()
    conflict = await start(s, s.a2, key, "Changed input")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert (await retrieve(s, s.a2, key)).json() == accepted.json()
    assert bundle_counts(s) == "1|1|1|2|1|1"
    assert len(entries(s)) == 1


@pytest.mark.parametrize(
    "point", ["before_prepare", "durable_prepare_rollback", "lost_database_ack"]
)
async def test_database_acceptance_faults(inbox, monkeypatch, point):
    s, key = inbox, uuid4()
    history = s.v.acceptance._history
    # A genuine transaction is still used; arm exceptions at existing operation/
    # driver boundaries. Independent SQL must not see any of A's staged rows.
    real_append = HistoryOperation.append
    real_exit = Transaction.__aexit__
    armed = False

    async def append(operation, command):
        nonlocal armed
        if operation._coordinator is not history or type(command.data) is not AcceptPrepare:
            return await real_append(operation, command)
        assert bundle_counts(s) == "0|0|0|0|0|0"
        if point == "before_prepare":
            raise ConnectionError("synthetic failure before durable prepare")
        result = await real_append(operation, command)
        armed = True
        return result

    async def exit_transaction(tx, kind, value, traceback):
        nonlocal armed
        if not armed or kind is not None:
            return await real_exit(tx, kind, value, traceback)
        armed = False
        if point == "durable_prepare_rollback":
            error = ConnectionError("synthetic rollback before database commit")
            await real_exit(tx, type(error), error, None)
            raise error
        await real_exit(tx, kind, value, traceback)
        raise ConnectionError("synthetic lost database commit acknowledgement")

    with monkeypatch.context() as fault:
        fault.setattr(HistoryOperation, "append", append)
        fault.setattr(Transaction, "__aexit__", exit_transaction)
        response = await start(s, s.a1, key, "Faulted sample")
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "acceptance_unconfirmed", "retry_safe": False}
    observed = await retrieve(s, s.a2, key)
    if point == "lost_database_ack":
        assert bundle_counts(s) == "1|1|1|2|1|1"
        assert observed.status_code == 200
        assert observed.json()["items"][0]["text"] == "Faulted sample"
        assert observed.json() == (await retrieve(s, s.a1, key)).json()
    else:
        assert bundle_counts(s) == "0|0|0|0|0|0"
        if point == "before_prepare":
            assert observed.status_code == 404
        else:
            assert observed.status_code == 503
            assert (await s.http.get("/inbox", headers=s.b)).status_code == 503
    assert len(entries(s)) == (0 if point == "before_prepare" else 1)
    assert s.v.acceptance._blocked
    assert (await start(s, s.a2, key, "Faulted sample")).status_code == 503


async def test_http_response_loss_is_after_confirmed_acceptance(inbox):
    s, key = inbox, uuid4()
    confirmed = []

    async def drop_response(scope, receive, send):
        async def capture(message):
            if message["type"] == "http.response.start":
                confirmed.append(message["status"])
            # Simulate transport failure after the app has produced its response,
            # but never deliver its response headers or receipt to the client.

        await s.app(scope, receive, capture)
        raise ReadError("synthetic loss of confirmed HTTP response")

    async with AsyncClient(
        transport=ASGITransport(drop_response), base_url="http://fixture.test"
    ) as client:
        with pytest.raises(ReadError):
            await client.post(
                "/inbox/starts",
                headers={**s.a1, "Idempotency-Key": str(key)},
                json={"text": "Confirmed sample"},
            )
    assert confirmed == [201]
    assert not s.v.acceptance._blocked
    found = await retrieve(s, s.a2, key)
    assert found.status_code == 200
    assert found.json()["items"][0]["text"] == "Confirmed sample"
    assert (await start(s, s.a1, key, "Confirmed sample")).status_code == 409
    assert bundle_counts(s) == "1|1|1|2|1|1"


async def test_cancellation_after_commit_drains_before_readback(inbox, monkeypatch):
    s, key = inbox, uuid4()
    projection = s.v.projection
    original_stage, original_exit = projection.stage, Transaction.__aexit__
    committed = asyncio.Event()
    armed = False

    async def stage(conn, entry, *, deadline):
        nonlocal armed
        result = await original_stage(conn, entry, deadline=deadline)
        armed = True
        return result

    async def exit_transaction(tx, kind, value, tb):
        nonlocal armed
        if not armed:
            return await original_exit(tx, kind, value, tb)
        armed = False
        await original_exit(tx, kind, value, tb)
        assert kind is None
        committed.set()
        # The outer HTTP caller cancels here, before W/H cleanup or any receipt.
        await asyncio.Future()

    with monkeypatch.context() as hooks:
        hooks.setattr(projection, "stage", stage)
        hooks.setattr(Transaction, "__aexit__", exit_transaction)
        call = asyncio.create_task(start(s, s.a1, key, "Cancelled response"))
        try:
            await asyncio.wait_for(committed.wait(), 5)
        finally:
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
    assert s.v.acceptance._blocked
    assert bundle_counts(s) == "1|1|1|2|1|1"
    assert (await retrieve(s, s.a2, key)).status_code == 200


@pytest.mark.parametrize(
    "damage",
    [
        "missing_link",
        "cross_link",
        "native_message",
        "stale",
        "generation",
        "extra_conversation",
        "canonical",
        "unsafe_acceptor_grant",
        "unsafe_grant",
    ],
)
async def test_guarded_reader_refuses_incomplete_evidence(inbox, damage):
    s, key = inbox, uuid4()
    accepted = await start(s, s.a1, key, "Private sample")
    assert accepted.status_code == 201
    async with s.db.connection() as conn:
        before = await conn.fetchrow(
            "SELECT * FROM public.fixture_inbox_links WHERE account_id=$1", s.account
        )
        if damage == "missing_link":
            await conn.execute(
                "DELETE FROM public.fixture_inbox_links WHERE task_id=$1", before["task_id"]
            )
        elif damage == "cross_link":
            await conn.execute(
                """UPDATE public.fixture_inbox_links SET user_message_id=result_message_id,
                          result_message_id=user_message_id WHERE task_id=$1""",
                before["task_id"],
            )
        elif damage == "native_message":
            await conn.execute(
                "UPDATE public.messages SET content=$1 WHERE id=$2",
                s.enc.encrypt("not original"),
                before["user_message_id"],
            )
        elif damage == "stale":
            await conn.execute(
                """UPDATE public.tasks SET status='running',lease_owner='stale',
                   lease_epoch=1,lease_expires_at=now()-interval '1 minute' WHERE id=$1""",
                before["task_id"],
            )
        elif damage == "generation":
            await conn.execute(
                """UPDATE public.entitlement_accounts SET content_generation=content_generation+1
                   WHERE user_id=$1""",
                s.account,
            )
        elif damage == "extra_conversation":
            await conn.execute(
                "INSERT INTO public.conversations (user_id,pipeline) VALUES ($1,'cloud')", s.account
            )
        elif damage == "canonical":
            await conn.execute(
                "UPDATE public.tasks SET request_hash=repeat('0',64) WHERE id=$1",
                before["task_id"],
            )
        elif damage == "unsafe_grant":
            await conn.execute("GRANT UPDATE ON public.fixture_inbox_links TO md_app")
        else:
            await conn.execute(
                "GRANT SELECT (task_id) ON public.fixture_inbox_links TO md_acceptor"
            )
    if damage == "unsafe_acceptor_grant":
        assert (await start(s, s.a1, uuid4(), "Must refuse stage")).status_code == 503
        assert bundle_counts(s) == "1|1|1|2|1|1"
        assert len(entries(s)) == 1
        assert (await retrieve(s, s.a2, key)).json() == accepted.json()
        return
    if damage in {"missing_link", "cross_link"}:
        assert (await start(s, s.a1, uuid4(), "Must refuse new start")).status_code == 503
        assert len(entries(s)) == 1
    for r in (
        await status(s, s.a2, "Unmatched name"),
        await retrieve(s, s.a2, key),
        await s.http.get("/inbox", headers=s.a2),
    ):
        assert r.status_code == 503
        assert r.json() == {"detail": {"code": "inbox_unavailable", "retry_safe": False}}
    assert "Private sample" not in (await s.http.get("/inbox", headers=s.b)).text


async def test_revoked_session_and_invalid_canonical_fields(inbox):
    s = inbox
    forbidden = await s.http.post(
        "/inbox/starts",
        headers={**s.a1, "Idempotency-Key": str(uuid4())},
        json={"text": "Attempted override", "metadata": {"inbox": "fake"}},
    )
    assert forbidden.status_code == 422
    assert bundle_counts(s) == "0|0|0|0|0|0"
    key = uuid4()
    assert (await start(s, s.a1, key, "Retained sample")).status_code == 201
    async with s.db.connection() as conn:
        await conn.execute(
            "UPDATE public.sessions SET revoked_at=now() WHERE access_token_hash=$1",
            hash_token(s.a1["Authorization"].removeprefix("Bearer ")),
        )
    assert (await retrieve(s, s.a1, key)).status_code == 401
    assert (await s.http.get("/inbox", headers=s.a1)).status_code == 401
    assert (await status(s, s.a1, "Retained sample")).status_code == 401
    assert (await retrieve(s, s.a2, key)).status_code == 200
