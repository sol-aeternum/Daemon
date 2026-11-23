"""#761 proof on disposable PostgreSQL, actual bearer sessions and protected H files."""

import asyncio
from dataclasses import dataclass
import json
from typing import Literal
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

from asyncpg.transaction import Transaction
from httpx import ASGITransport, AsyncClient, ReadError
import pytest
import pytest_asyncio

from orchestrator.auth_tokens import hash_token
from orchestrator.memory.store import MemoryStore
from orchestrator.services.connection_cleanup import pool_connection
from orchestrator.services.restore_history.acceptance_host import create_isolated_ui_app
from orchestrator.services.restore_history.coordination import HistoryOperation
from orchestrator.services.restore_history.model import AcceptPrepare, Command, commitments
from orchestrator.tasks.inputs import chat_request_fingerprint
from orchestrator.tasks.store import TaskStore
from tests.history_inbox_support import DDL, KEY_PREFIX, create_fixture_inbox_app
from tests.restore_history_support import KEY
from tests.test_history_composition_postgres import deadline
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
    staged = None

    async def append(operation, command):
        nonlocal staged
        if operation._coordinator is not history or type(command.data) is not AcceptPrepare:
            return await real_append(operation, command)
        assert bundle_counts(s) == "0|0|0|0|0|0"
        if point == "before_prepare":
            raise ConnectionError("synthetic failure before durable prepare")
        result = await real_append(operation, command)
        staged = command.data
        return result

    async def exit_transaction(tx, kind, value, traceback):
        nonlocal staged
        if staged is None or kind is not None:
            return await real_exit(tx, kind, value, traceback)
        prepared, staged = staged, None
        # The projection and link have now been staged. Count the tables
        # separately: an inner join alone would hide a partially visible bundle.
        assert bundle_counts(s) == "0|0|0|0|0|0"
        if point == "durable_prepare_rollback":
            error = ConnectionError("synthetic rollback before database commit")
            await real_exit(tx, type(error), error, None)
            raise error
        await real_exit(tx, kind, value, traceback)
        # Observe from a separate connection immediately after COMMIT, before
        # returning even an uncertain acknowledgement to the application.
        async with s.db.connection() as observer:
            rows = await observer.fetch(
                """SELECT l.task_id, l.conversation_id, l.user_message_id, l.result_message_id
                   FROM fixture_inbox_links l
                   JOIN fixture_inboxes i ON (i.id,i.account_id,i.generation)
                                           = (l.inbox_id,l.account_id,l.generation)
                   JOIN tasks t ON (t.id,t.user_id,t.conversation_id,t.user_message_id,
                                    t.result_message_id)
                                 = (l.task_id,l.account_id,l.conversation_id,l.user_message_id,
                                    l.result_message_id)
                   JOIN conversations c ON (c.id,c.user_id) = (l.conversation_id,l.account_id)
                   WHERE i.account_id=$1 AND i.generation=$2 AND t.idempotency_key=$3""",
                s.account,
                prepared.account_generation,
                KEY_PREFIX + str(key),
            )
        assert [tuple(row) for row in rows] == [
            (
                prepared.task_id,
                prepared.conversation_id,
                prepared.user_message_id,
                prepared.result_message_id,
            )
        ]
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
        original = entries(s)[0].command.data
        assert isinstance(original, AcceptPrepare)
        assert observed.json()["items"][0]["id"] == f"request:{original.task_id}"
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
    if point == "lost_database_ack":
        assert bundle_counts(s) == "1|1|1|2|1|1"


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


# Read-only prelinked proof. This setup seeds the native and history stores
# directly; only the tests above exercise the fixture acceptance transaction.
LOOKUP_KEY_PREFIX = "fixture-lookup-v1:"


@dataclass(frozen=True, slots=True)
class LinkedJob:
    key: str
    text: str
    task_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    result_message_id: UUID


@dataclass(frozen=True, slots=True)
class Resolution:
    kind: Literal["job", "ambiguous", "missing"]
    job: LinkedJob | None = None
    candidates: tuple[LinkedJob, ...] = ()


def new_key() -> str:
    return LOOKUP_KEY_PREFIX + uuid4().hex


def normalize(text: str) -> str:
    return " ".join(text.casefold().replace("’", "'").split())


def resolve(
    jobs: list[LinkedJob], *, ref: str | None = None, text: str | None = None
) -> Resolution:
    """Client-script contract, not product inference or an inbox endpoint."""
    if ref is not None:
        matches = [
            job for job in jobs if ref in {job.key, str(job.task_id), str(job.conversation_id)}
        ]
    elif text is not None and normalize(text):
        needle = normalize(text)
        matches = (
            list(jobs)
            if needle == "how's that going?"
            else [job for job in jobs if needle in normalize(job.text)]
        )
    else:
        matches = []
    if not matches:
        return Resolution("missing")
    if len(matches) > 1:
        return Resolution("ambiguous", candidates=tuple(matches))
    return Resolution("job", job=matches[0])


def saved_state(s) -> tuple[str, bytes, bytes]:
    """Detect SQL row creation and any durable H append/rewrite during lookup."""
    counts = s.db.sql(
        """SELECT (SELECT count(*) FROM public.conversations),
                  (SELECT count(*) FROM public.messages),
                  (SELECT count(*) FROM public.tasks),
                  (SELECT count(*) FROM public.task_events),
                  (SELECT count(*) FROM public.task_attempts),
                  (SELECT count(*) FROM public.task_operations),
                  (SELECT count(*) FROM public.task_history_projection)"""
    )
    return counts, (s.path / "LOG").read_bytes(), (s.path / "HEAD").read_bytes()


async def seed_job(
    s,
    v,
    account: UUID,
    text: str,
    *,
    key: str | None = None,
    with_history: bool = True,
    with_projection: bool = True,
) -> LinkedJob:
    """Seed one queued job directly; no acceptance, claim or scheduling path."""
    key = key or new_key()
    task_id, conversation_id = uuid4(), uuid4()
    user_message_id, result_message_id = uuid4(), uuid4()
    async with s.db.connection() as conn:
        generation = await conn.fetchval(
            "SELECT content_generation FROM public.entitlement_accounts WHERE user_id=$1",
            account,
        )
        if type(generation) is not int:
            raise AssertionError("fixture account generation missing")
        fingerprint = chat_request_fingerprint(
            key=v.host._fingerprint_key,
            conversation_id=None,
            message=text,
            attachments=None,
            model="auto",
            provider=None,
            metadata=None,
            disable_memory_write=True,
        )
        sealed = {
            "message": text,
            "provider": None,
            "disable_memory_write": True,
            "_history_text_only": True,
            "auto_route": True,
            "origin_account_id": str(account),
            "origin_account_generation": generation,
            "summary_origin_account_id": str(account),
            "summary_origin_account_generation": generation,
            "_inference_recovery_version": 1,
            "_request": fingerprint.canonical,
        }
        title = text[:50] + ("..." if len(text) > 50 else "")
        await conn.execute(
            "INSERT INTO public.conversations"
            "(id,user_id,pipeline,title,message_count,tokens_total) "
            "VALUES($1,$2,'cloud',$3,2,0)",
            conversation_id,
            account,
            title,
        )
        await conn.execute(
            "INSERT INTO public.messages"
            "(id,conversation_id,user_id,role,content,status,metadata,model,"
            "tokens_in,tokens_out,created_at) "
            "VALUES($1,$2,$3,'user',$4,'complete','{}'::jsonb,NULL,0,0,now())",
            user_message_id,
            conversation_id,
            account,
            s.enc.encrypt(text),
        )
        await conn.execute(
            "INSERT INTO public.messages"
            "(id,conversation_id,user_id,role,content,status,metadata,model,"
            "tokens_in,tokens_out,created_at) "
            "VALUES($1,$2,$3,'assistant',$4,'streaming','{}'::jsonb,NULL,0,0,"
            "now()+interval '1 microsecond')",
            result_message_id,
            conversation_id,
            account,
            s.enc.encrypt(""),
        )
        await conn.execute(
            "INSERT INTO public.tasks"
            "(id,user_id,conversation_id,user_message_id,result_message_id,"
            "input_ciphertext,input_version,idempotency_key,request_hash,event_seq) "
            "VALUES($1,$2,$3,$4,$5,$6,1,$7,$8,1)",
            task_id,
            account,
            conversation_id,
            user_message_id,
            result_message_id,
            s.enc.encrypt(json.dumps(sealed)),
            key,
            fingerprint.digest,
        )
        await conn.execute(
            "INSERT INTO public.task_inference_recovery"
            "(task_id,account_id,account_generation,protocol_version) VALUES($1,$2,$3,1)",
            task_id,
            account,
            generation,
        )
        await conn.execute(
            "INSERT INTO public.task_events(task_id,seq,kind,payload_ciphertext) "
            "VALUES($1,1,'accepted',NULL)",
            task_id,
        )
    job = LinkedJob(key, text, task_id, conversation_id, user_message_id, result_message_id)
    if not with_history:
        return job
    lookup, request = commitments(KEY, account, key, fingerprint.canonical.encode("utf-8"))
    command = Command(
        uuid4(),
        AcceptPrepare(
            account,
            generation,
            task_id,
            conversation_id,
            user_message_id,
            result_message_id,
            1,
            1,
            lookup,
            request,
            "chat-canonical-3715-v1",
        ),
    )
    async with v.acceptance._history.operation(deadline=deadline()) as operation:
        prepared = await operation.append(command)
        if with_projection:
            async with pool_connection(v.tasks._pool) as acceptor:
                async with acceptor.transaction(isolation="read_committed"):
                    await v.projection.stage(acceptor, prepared.entry, deadline=deadline())
    return job


RECOVERY = {"code": "restore_recovery_required", "task_id": None, "retry_safe": False}
UNAVAILABLE = {"code": "history_unavailable", "task_id": None, "retry_safe": False}


def job_count(s):
    return s.db.sql("SELECT count(*) FROM public.tasks")


def install_spies(monkeypatch):
    accept = Mock(side_effect=AssertionError("lookup attempted acceptance"))
    stage = Mock(side_effect=AssertionError("lookup attempted acceptance"))
    claim = Mock(side_effect=AssertionError("lookup attempted a claim"))
    create = Mock(side_effect=AssertionError("lookup attempted to create a conversation"))
    schedule = Mock(side_effect=AssertionError("lookup attempted to schedule"))
    enqueue = Mock(side_effect=AssertionError("lookup attempted to enqueue"))
    monkeypatch.setattr(TaskStore, "accept", accept)
    monkeypatch.setattr(TaskStore, "_accept_with_stage", stage)
    monkeypatch.setattr(TaskStore, "claim", claim)
    monkeypatch.setattr(MemoryStore, "create_conversation", create)
    monkeypatch.setattr(
        "orchestrator.services.restore_history.text_execution.HistoryTextExecution.schedule",
        schedule,
    )
    monkeypatch.setattr("arq.connections.ArqRedis.enqueue_job", enqueue)
    return accept, stage, claim, create, schedule, enqueue


async def ui_get(s, path, headers):
    """One real client read on a fresh host; a refusal blocks only that host."""
    async with s.make() as v:
        app = create_isolated_ui_app(v.host)
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://fixture.test"
        ) as http:
            return await http.get(path, headers=headers)


async def read_job(http, headers, job):
    return await http.get(f"/tasks/by-key/{job.key}", headers=headers)


def assert_only_job(body, job):
    assert body["id"] == str(job.task_id)
    assert body["conversation_id"] == str(job.conversation_id)
    assert body["result_message_id"] == str(job.result_message_id)
    assert body["status"] == "queued"
    assert body["terminal_code"] is None
    assert body["attempt_count"] == 0
    assert body["content_generation"] == 0
    assert body["content_delta_seq"] == 0
    assert body["content"] == ""
    assert body["event_seq"] == 1
    assert body["cancel_requested"] is False


def assert_no_execution(s):
    assert s.db.sql("SELECT count(*) FROM public.task_attempts") == "0"
    assert s.db.sql("SELECT count(*) FROM public.task_operations") == "0"
    assert (
        s.db.sql("SELECT count(*) FROM public.tasks WHERE attempt_count<>0 OR wake_seq<>0") == "0"
    )
    assert s.db.sql("SELECT count(*) FROM public.task_events WHERE kind<>'accepted'") == "0"
    assert (
        s.db.sql("SELECT count(*) FROM public.task_inference_recovery WHERE may_have_dispatched")
        == "0"
    )


async def test_prelinked_lookup_ui_guard_and_cross_account(terminal_host, monkeypatch):
    s = terminal_host
    async with s.make() as v:
        async with s.db.connection() as conn:
            s.foreign = await s.db.account(conn)
        a1 = await seed_job(s, v, s.account, "Roof insulation")
        a2 = await seed_job(s, v, s.account, "Wall insulation")
        b1 = await seed_job(s, v, s.foreign, "Solar panels")
        _, a1_headers = await device(s, s.account)
        _, a2_headers = await device(s, s.account)
        _, b_headers = await device(s, s.foreign)
        app = create_isolated_ui_app(v.host)
        spies = install_spies(monkeypatch)
        before = saved_state(s)
        # Close the first client before opening a new client with a distinct
        # authenticated session; no response or task selection is carried over.
        for headers in (a1_headers, a2_headers):
            async with AsyncClient(
                transport=ASGITransport(app), base_url="http://fixture.test"
            ) as http:
                for job, other in ((a1, a2), (a2, a1)):
                    response = await read_job(http, headers, job)
                    assert response.status_code == 200, response.text
                    body = response.json()
                    assert_only_job(body, job)
                    assert body["id"] != str(other.task_id)
                    assert body["conversation_id"] != str(other.conversation_id)

        async with (
            AsyncClient(transport=ASGITransport(app), base_url="http://fixture.test") as first,
            AsyncClient(transport=ASGITransport(app), base_url="http://fixture.test") as foreign,
        ):
            # B's own single-job envelope and legitimate state are untouched.
            own = await foreign.get(f"/tasks/{b1.task_id}", headers=b_headers)
            assert own.status_code == 200, own.text
            own_body = own.json()
            assert own_body["id"] == str(b1.task_id)
            listing = await foreign.get("/conversations", headers=b_headers)
            assert listing.status_code == 200, listing.text
            assert listing.json()["total"] == 1
            assert [c["id"] for c in listing.json()["conversations"]] == [str(b1.conversation_id)]

            # B's known A key is the same non-disclosure as a random key.
            random = await foreign.get(f"/tasks/by-key/{new_key()}", headers=b_headers)
            for job in (a1, a2):
                known = await read_job(foreign, b_headers, job)
                assert known.status_code == random.status_code == 404
                assert known.json() == random.json() == {"detail": "Task not found"}

            resolved = resolve([a1, a2], ref=str(a1.task_id))
            assert resolved.kind == "job" and resolved.job == a1
            response = await read_job(first, a1_headers, resolved.job)
            assert response.status_code == 200, response.text
            assert_only_job(response.json(), a1)
            assert resolve([a1, a2], ref=a2.key).job == a2
            assert resolve([a1, a2], text="Roof").job == a1
            ambiguous = resolve([a1, a2], text="insulation")
            assert ambiguous.kind == "ambiguous"
            assert ambiguous.candidates == (a1, a2)
            followup = resolve([a1, a2], text="How’s that going?")
            assert followup.kind == "ambiguous" and followup.job is None
            assert followup.candidates == (a1, a2)
            assert resolve([a1, a2], ref=str(uuid4())).kind == "missing"

            assert saved_state(s) == before
            assert job_count(s) == "3"
            assert_no_execution(s)

        # B's known A task/conversation are indistinguishable from random refs.
        pairs = (
            (f"/tasks/{a1.task_id}", f"/tasks/{uuid4()}"),
            (f"/tasks/{a2.task_id}", f"/tasks/{uuid4()}"),
            (f"/conversations/{a1.conversation_id}", f"/conversations/{uuid4()}"),
            (f"/conversations/{a2.conversation_id}", f"/conversations/{uuid4()}"),
        )
        for known_path, random_path in pairs:
            known = await ui_get(s, known_path, b_headers)
            random = await ui_get(s, random_path, b_headers)
            assert known.status_code == random.status_code == 404
            assert known.json() == random.json()

        # The existing single-job envelope refuses two accepted jobs, unchanged.
        for path in (
            "/conversations",
            f"/conversations/{a1.conversation_id}",
            f"/tasks/{a1.task_id}",
        ):
            response = await ui_get(s, path, a1_headers)
            assert response.status_code == 409, response.text
            assert response.json() == {"detail": RECOVERY}
            assert response.headers["cache-control"] == "no-store"

        assert saved_state(s) == before
        assert job_count(s) == "3"
        assert_no_execution(s)
        for spy in spies:
            spy.assert_not_called()

    # Reopen the physical host/pools too, with a newly issued real session.
    _, fresh_headers = await device(s, s.account)
    async with s.make() as restarted:
        async with AsyncClient(
            transport=ASGITransport(create_isolated_ui_app(restarted.host)),
            base_url="http://fixture.test",
        ) as fresh:
            for job in (a1, a2):
                response = await read_job(fresh, fresh_headers, job)
                assert response.status_code == 200, response.text
                assert_only_job(response.json(), job)
    assert saved_state(s) == before
    assert_no_execution(s)
    for spy in spies:
        spy.assert_not_called()


async def test_sql_only_and_unknown_are_honest(terminal_host, monkeypatch):
    s = terminal_host
    async with s.make() as v:
        normal = await seed_job(s, v, s.account, "Normal job")
        sql_only = await seed_job(s, v, s.account, "SQL-only job", with_history=False)
        _, headers = await device(s, s.account)
        app = create_isolated_ui_app(v.host)
        spies = install_spies(monkeypatch)
        before = saved_state(s)
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://fixture.test"
        ) as http:
            response = await read_job(http, headers, normal)
            assert response.status_code == 200, response.text
            assert_only_job(response.json(), normal)

            response = await read_job(http, headers, sql_only)
            assert response.status_code == 409, response.text
            assert response.json() == {"detail": RECOVERY}
            assert "SQL-only" not in response.text

            unknown = await http.get(f"/tasks/by-key/{new_key()}", headers=headers)
            assert unknown.status_code == 404
            assert unknown.json() == {"detail": "Task not found"}

        # One accepted task but two conversations still hits the existing refusal.
        response = await ui_get(s, f"/tasks/{normal.task_id}", headers)
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": RECOVERY}

        assert saved_state(s) == before
        assert job_count(s) == "2"
        assert_no_execution(s)
        for spy in spies:
            spy.assert_not_called()


async def test_missing_projection_is_recovery(terminal_host, monkeypatch):
    s = terminal_host
    async with s.make() as v:
        job = await seed_job(s, v, s.account, "Unprojected job", with_projection=False)
        _, headers = await device(s, s.account)
        app = create_isolated_ui_app(v.host)
        spies = install_spies(monkeypatch)
        before = saved_state(s)
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://fixture.test"
        ) as http:
            response = await read_job(http, headers, job)
            assert response.status_code == 409, response.text
            assert response.json() == {
                "detail": {
                    "code": "restore_recovery_required",
                    "task_id": str(job.task_id),
                    "retry_safe": False,
                }
            }
            missing = await http.get(f"/tasks/by-key/{new_key()}", headers=headers)
            assert missing.status_code == 503
            assert missing.json() == {"detail": UNAVAILABLE}

        assert saved_state(s) == before
        assert job_count(s) == "1"
        assert_no_execution(s)
        for spy in spies:
            spy.assert_not_called()
