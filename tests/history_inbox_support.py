"""Closed #761 acceptance proof; never imported by an application or a worker."""

from dataclasses import dataclass
import hmac
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, UUID4

from orchestrator.request_body_limit import RequestBodyLimitMiddleware
from orchestrator.services.account_control.guard import (
    WriteGuardRefusal,
    capture_generation,
    guard_content_write,
)
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.services.connection_cleanup import (
    CleanupUnavailable,
    pool_connection,
    retain_pool_cleanup,
)
from orchestrator.services.restore_history.acceptance_host import IsolatedAcceptanceHost
from orchestrator.services.restore_history.model import AcceptPrepare, Command, HistoryError
from orchestrator.services.restore_history.projection import compare_projections
from orchestrator.services.restore_history.store import _check_time
from orchestrator.tasks.inputs import chat_request_fingerprint
from orchestrator.tasks.store import AcceptedTask
from tests.test_history_composition_postgres import deadline

DDL = """
CREATE TABLE public.fixture_inboxes (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES public.users(id),
    generation bigint NOT NULL CHECK (generation >= 0),
    UNIQUE (account_id, generation),
    UNIQUE (id, account_id, generation)
);
CREATE TABLE public.fixture_inbox_links (
    inbox_id uuid NOT NULL,
    account_id uuid NOT NULL,
    generation bigint NOT NULL,
    task_id uuid PRIMARY KEY REFERENCES public.tasks(id),
    conversation_id uuid NOT NULL UNIQUE REFERENCES public.conversations(id),
    user_message_id uuid NOT NULL UNIQUE REFERENCES public.messages(id),
    result_message_id uuid NOT NULL UNIQUE REFERENCES public.messages(id),
    FOREIGN KEY (inbox_id, account_id, generation)
        REFERENCES public.fixture_inboxes(id, account_id, generation),
    CHECK (user_message_id <> result_message_id)
);
GRANT SELECT ON public.fixture_inboxes TO md_acceptor, md_app;
GRANT INSERT ON public.fixture_inbox_links TO md_acceptor;
GRANT SELECT ON public.fixture_inbox_links TO md_app;
"""

LIMIT = 2
KEY_PREFIX = "fixture-inbox-v1:"
HEADERS = {"Cache-Control": "no-store"}
REFUSED = (
    CleanupUnavailable,
    HistoryError,
    WriteGuardRefusal,
    ObservationBlocked,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
    ValueError,
)


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=50)


class Status(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=100)
    reply_to_item: str | None = Field(default=None, max_length=50)


def refuse(code="inbox_unavailable", status=503):
    return HTTPException(status, {"code": code, "retry_safe": False}, headers=HEADERS)


def fingerprint(pepper, inbox, text):
    return chat_request_fingerprint(
        key=pepper,
        conversation_id=None,
        message=text,
        attachments=None,
        model="auto",
        provider=None,
        metadata={"inbox": str(inbox), "purpose": "fixture-inbox-v1"},
        disable_memory_write=True,
    )


async def qualify_inbox(conn, *, writing):
    # Check effective grants, including inherited/table and column grants. The
    # separate existing projection qualifier still checks all native authority.
    root, link = "public.fixture_inboxes", "public.fixture_inbox_links"
    role = "md_acceptor" if writing else "md_app"
    result = await conn.fetchrow(
        """SELECT CURRENT_USER::text AS login,
           has_table_privilege(CURRENT_USER, $1, 'SELECT') AS root_read,
           has_table_privilege(CURRENT_USER, $2, 'SELECT') AS link_read,
           has_table_privilege(CURRENT_USER, $2, 'INSERT') AS link_insert,
           has_any_column_privilege(CURRENT_USER, $1, 'INSERT,UPDATE,REFERENCES') OR
             has_table_privilege(CURRENT_USER, $1, 'DELETE,TRUNCATE,TRIGGER') AS root_other,
           has_any_column_privilege(CURRENT_USER, $2, 'UPDATE,REFERENCES') OR
             has_table_privilege(CURRENT_USER, $2, 'DELETE,TRUNCATE,TRIGGER') AS link_other,
           has_any_column_privilege(CURRENT_USER, $2, 'SELECT') AS link_any_read,
           has_any_column_privilege(CURRENT_USER, $2, 'INSERT') AS link_any_insert,
           has_any_column_privilege(CURRENT_USER, $1, 'SELECT WITH GRANT OPTION') OR
             has_any_column_privilege(CURRENT_USER, $2, 'SELECT WITH GRANT OPTION,INSERT WITH GRANT OPTION')
             AS grantable,
           EXISTS (SELECT 1 FROM pg_catalog.pg_class
             WHERE oid IN (to_regclass($1),to_regclass($2))
               AND pg_has_role(CURRENT_USER, relowner, 'MEMBER')) AS owns""",
        root,
        link,
    )
    if tuple(result.values()) != (
        role,
        True,
        not writing,
        writing,
        False,
        False,
        not writing,
        writing,
        False,
        False,
    ):
        raise HistoryError("fixture_inbox_principal_refused")


async def root_for(conn, capture):
    root = await conn.fetchval(
        "SELECT id FROM public.fixture_inboxes WHERE account_id=$1 AND generation=$2",
        capture.account_id,
        capture.generation,
    )
    if not isinstance(root, UUID):
        raise HistoryError("fixture_inbox_root_missing")
    return root


def ids(task):
    return (task.task_id, task.conversation_id, task.user_message_id, task.result_message_id)


async def links_match(conn, capture, inbox, accepted):
    # This is a completeness check, not independently sufficient evidence.
    links = await conn.fetch(
        """SELECT inbox_id,account_id,generation,task_id,conversation_id,user_message_id,result_message_id
           FROM public.fixture_inbox_links WHERE account_id=$1 OR inbox_id=$2 LIMIT 3""",
        capture.account_id,
        inbox,
    )
    expected = {(inbox, capture.account_id, capture.generation, *ids(e)) for e in accepted}
    return len(links) == len(accepted) and {tuple(link.values()) for link in links} == expected


@dataclass(frozen=True)
class Job:
    accepted: AcceptPrepare
    key: str
    text: str

    def items(self):
        ref = self.accepted.task_id
        return [
            {"id": f"request:{ref}", "kind": "request", "text": self.text},
            {"id": f"receipt:{ref}", "kind": "receipt", "text": "Saved."},
        ]


class InboxFixture:
    def __init__(self, host: IsolatedAcceptanceHost):
        if (
            type(host) is not IsolatedAcceptanceHost
            or host._execution is not None
            or host._acceptance._projection.profile != "terminal-v3"
        ):
            raise HistoryError("fixture_inbox_composition_refused")
        self.host = host

    async def _accept(self, token, key, text, limit):
        host = self.host
        acceptor, reader = host._acceptance, host._reader
        projection, tasks = acceptor._projection, acceptor._tasks
        async with acceptor._history.operation(deadline=limit) as operation, retain_pool_cleanup():
            async with pool_connection(reader._tasks._pool) as w:
                if w.is_in_transaction():
                    raise HistoryError("fixture_inbox_transaction_owner_refused")
                async with w.transaction(isolation="read_committed"):
                    auth = await host._auth(w, token, limit)
                    if auth is None:
                        return refuse("invalid_authority", 401)
                    await qualify_inbox(w, writing=False)
                    capture = await capture_generation(w, auth.user_id)
                    inbox = await root_for(w, capture)
                    fp = fingerprint(host._fingerprint_key, inbox, text)
                    options: dict[str, Any] = dict(
                        user_id=auth.user_id,
                        conversation_id=None,
                        new_conversation_title=text,
                        pipeline="cloud",
                        user_message=text,
                        task_input={
                            "message": text,
                            "provider": None,
                            "disable_memory_write": True,
                        },
                        request_hash=fp.digest,
                        request_canonical=fp.canonical,
                        idempotency_key=key,
                        assistant_model=None,
                        captured_generation=capture,
                    )
                    lookup, request = acceptor._request(options)
                    state = await operation.verify()
                    async with pool_connection(tasks._pool) as a:
                        if a.is_in_transaction():
                            raise HistoryError("fixture_inbox_transaction_owner_refused")
                        async with a.transaction(isolation="read_committed"):
                            await projection.qualify(a, writing=True, deadline=limit)
                            facts = []
                            cursor = 0
                            while True:
                                page = await projection.page(a, after_seq=cursor, deadline=limit)
                                facts.extend(page)
                                if len(page) < 32:
                                    break
                                cursor = page[-1].seq
                            native = await a.fetchval(
                                "SELECT id FROM public.tasks WHERE user_id=$1 AND idempotency_key=$2",
                                auth.user_id,
                                key,
                            )
                    parity = compare_projections(
                        projection.expected.history_id,
                        state,
                        tuple(facts),
                        profile=projection.profile,
                    )
                    if (
                        parity.missing
                        or parity.extra
                        or parity.mismatched
                        or parity.restore_required
                    ):
                        raise HistoryError("fixture_inbox_acceptance_parity_refused")
                    owned = [
                        e.command.data
                        for e in state.entries
                        if type(e.command.data) is AcceptPrepare
                        and e.command.data.account_id == auth.user_id
                    ]
                    if (
                        len(owned) > LIMIT
                        or any(e.account_generation != capture.generation for e in owned)
                        or not await links_match(w, capture, inbox, owned)
                    ):
                        raise HistoryError("fixture_inbox_acceptance_links_refused")
                    for e in owned:
                        if hmac.compare_digest(e.key_commitment, lookup):
                            if native != e.task_id:
                                raise HistoryError("fixture_inbox_acceptance_identity_refused")
                            return (
                                refuse("acceptance_recovery_required", 409)
                                if hmac.compare_digest(e.request_commitment, request)
                                else refuse("idempotency_conflict", 409)
                            )
                    if native is not None:
                        raise HistoryError("fixture_inbox_sql_only_key")
                    if len(owned) >= LIMIT:
                        return refuse("fixture_inbox_limit", 409)

                    staged = False

                    async def stage(conn, pending):
                        nonlocal staged
                        if staged or type(pending) is not AcceptedTask or not pending.created:
                            raise HistoryError("fixture_inbox_stage_refused")
                        operation._check()
                        await qualify_inbox(conn, writing=True)
                        if await root_for(conn, capture) != inbox:
                            raise HistoryError("fixture_inbox_root_changed")
                        await conn.execute(
                            """INSERT INTO public.fixture_inbox_links VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                            inbox,
                            auth.user_id,
                            capture.generation,
                            *ids(pending),
                        )
                        data = AcceptPrepare(
                            auth.user_id,
                            capture.generation,
                            *ids(pending),
                            1,
                            1,
                            lookup,
                            request,
                            "chat-canonical-3715-v1",
                        )
                        prepared = await operation.append(Command(uuid4(), data))
                        if (
                            not prepared.created
                            or not (
                                await projection.stage(conn, prepared.entry, deadline=limit)
                            ).inserted
                        ):
                            raise HistoryError("fixture_inbox_stage_refused")
                        staged = True

                    pending = await tasks._accept_with_stage(stage=stage, **options)
                    if not staged or not pending.created:
                        raise HistoryError("fixture_inbox_stage_refused")
        _check_time(limit)
        return pending

    async def _jobs(self, token, limit):
        host, reader = self.host, self.host._reader
        async with reader._history.operation(deadline=limit) as operation, retain_pool_cleanup():
            async with pool_connection(reader._tasks._pool) as conn:
                if conn.is_in_transaction():
                    raise HistoryError("fixture_inbox_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    auth = await host._auth(conn, token, limit)
                    if auth is None:
                        return refuse("invalid_authority", 401)
                    xid = await conn.fetchval("SELECT txid_current()")
                    state = await operation.verify()
                    if not await reader._parity_owned(operation, conn, xid, state, limit):
                        return refuse()
                    await qualify_inbox(conn, writing=False)
                    capture = await capture_generation(conn, auth.user_id)
                    async with guard_content_write(conn, capture):
                        inbox = await root_for(conn, capture)
                        accepted = [
                            e.command.data
                            for e in state.entries
                            if type(e.command.data) is AcceptPrepare
                            and e.command.data.account_id == auth.user_id
                        ]
                        jobs = await self._scoped_jobs(
                            operation, conn, xid, auth, capture, inbox, accepted, state, limit
                        )
                        if await host._auth(conn, token, limit) != auth:
                            return refuse("invalid_authority", 401)
                        await reader._check_owned(operation, conn, xid, limit)
                        return jobs

    async def _scoped_jobs(
        self, operation, conn, xid, auth, capture, inbox, accepted, state, limit
    ):
        # A limit+1 probe diagnoses overflow; no truncated projection is returned.
        if len(accepted) > LIMIT or any(
            e.account_generation != capture.generation for e in accepted
        ):
            return refuse()
        if not await links_match(conn, capture, inbox, accepted):
            return refuse()
        conv_ids = [e.conversation_id for e in accepted]
        conversations = await conn.fetch(
            """SELECT id,user_id,title,pipeline FROM public.conversations
               WHERE user_id=$1 OR id=ANY($2::uuid[]) ORDER BY id LIMIT 3 FOR SHARE""",
            auth.user_id,
            conv_ids,
        )
        if len(conversations) != len(accepted) or {c["id"] for c in conversations} != set(conv_ids):
            return refuse()
        native = await conn.fetch(
            """SELECT id FROM public.tasks WHERE user_id=$1 OR conversation_id=ANY($2::uuid[])
               ORDER BY id LIMIT 3 FOR SHARE""",
            auth.user_id,
            conv_ids,
        )
        if {r["id"] for r in native} != {e.task_id for e in accepted}:
            return refuse()
        messages = await conn.fetch(
            """SELECT id,user_id,conversation_id,role,status,content,created_at FROM public.messages
               WHERE user_id=$1 OR conversation_id=ANY($2::uuid[])
               ORDER BY id LIMIT 5 FOR SHARE""",
            auth.user_id,
            conv_ids,
        )
        message_by_id = {m["id"]: m for m in messages}
        if set(message_by_id) != {m for e in accepted for m in ids(e)[-2:]}:
            return refuse()
        reader = self.host._reader
        jobs = []
        for fact in accepted:
            row = await reader._task_owned(operation, conn, xid, auth, fact, None, state, limit)
            if row is None or not await self._queued(conn, row):
                return refuse()
            sealed = reader._tasks._open(row["input_ciphertext"])
            text = sealed.get("message")
            if type(text) is not str or not text.strip() or text != text.strip() or len(text) > 50:
                return refuse()
            fp = fingerprint(self.host._fingerprint_key, inbox, text)
            key = row["idempotency_key"]
            raw_key = UUID(key[len(KEY_PREFIX) :]) if key.startswith(KEY_PREFIX) else None
            if (
                raw_key is None
                or raw_key.version != 4
                or key != KEY_PREFIX + str(raw_key)
                or row["request_hash"] != fp.digest
                or sealed
                != {
                    "message": text,
                    "provider": None,
                    "disable_memory_write": True,
                    "origin_account_id": str(auth.user_id),
                    "origin_account_generation": capture.generation,
                    "summary_origin_account_id": str(auth.user_id),
                    "summary_origin_account_generation": capture.generation,
                    "_inference_recovery_version": 1,
                    "_request": fp.canonical,
                }
            ):
                return refuse()
            child = next(c for c in conversations if c["id"] == fact.conversation_id)
            user, result = (message_by_id[i] for i in ids(fact)[-2:])
            if (child["user_id"], child["title"], child["pipeline"]) != (
                auth.user_id,
                text,
                "cloud",
            ):
                return refuse()
            for msg, role, status, content in (
                (user, "user", "complete", text),
                (result, "assistant", "streaming", ""),
            ):
                if (
                    msg["user_id"],
                    msg["conversation_id"],
                    msg["role"],
                    msg["status"],
                    reader._tasks._enc.decrypt(msg["content"]),
                ) != (auth.user_id, fact.conversation_id, role, status, content):
                    return refuse()
            if user["created_at"] >= result["created_at"]:
                return refuse()
            jobs.append(Job(fact, key, text))
        return jobs

    @staticmethod
    async def _queued(conn, row):
        return (
            row["kind"] == "chat_answer"
            and row["status"] == "queued"
            and all(
                row[k] == 0
                for k in (
                    "lease_epoch",
                    "attempt_count",
                    "wake_seq",
                    "content_generation",
                    "content_delta_seq",
                )
            )
            and all(
                row[k] is None
                for k in (
                    "lease_owner",
                    "lease_expires_at",
                    "last_wake_at",
                    "cancel_requested_at",
                    "finished_at",
                    "terminal_code",
                )
            )
            and row["event_seq"] == 1
            and await conn.fetchval(
                """SELECT NOT EXISTS (SELECT 1 FROM public.task_attempts WHERE task_id=$1)
                   AND NOT EXISTS (SELECT 1 FROM public.task_operations WHERE task_id=$1)
                   AND EXISTS (SELECT 1 FROM public.task_events
                     WHERE task_id=$1 AND seq=1 AND kind='accepted' AND payload_ciphertext IS NULL)
                   AND NOT EXISTS (SELECT 1 FROM public.task_events WHERE task_id=$1 AND seq<>1)""",
                row["id"],
            )
            is True
        )

    async def read(self, token, limit):
        self.host._admit(limit)
        result = await self.host._run(self._jobs(token, limit), limit, writing=False)
        if isinstance(result, HTTPException):
            raise result
        if self.host._reader._history._blocked:
            raise HistoryError("fixture_inbox_reader_blocked")
        return result

    async def submit(self, token, key, text, limit):
        self.host._admit(limit)
        pending = await self.host._run(self._accept(token, key, text, limit), limit, writing=True)
        if isinstance(pending, HTTPException):
            raise pending
        # Not a replay path: the original writer returned normally, with A and W
        # committed/released and H unlocked. Now verify in a *separate* W read.
        jobs = await self.read(token, limit)
        _check_time(limit)
        original = next((j for j in jobs if j.key == key and ids(j.accepted) == ids(pending)), None)
        if original is None:
            raise HistoryError("fixture_inbox_original_unverified")
        return original


def normalize(text):
    return " ".join(text.casefold().replace("’", "'").split())


def create_fixture_inbox_app(host):
    fixture = InboxFixture(host)
    app = FastAPI(openapi_url=None)
    app.add_middleware(RequestBodyLimitMiddleware, global_limit=4096, route_limits={})

    def bearer(value):
        if not value or not value.startswith("Bearer ") or not value[7:]:
            raise refuse("invalid_authority", 401)
        return value[7:]

    async def read(auth):
        try:
            return await fixture.read(bearer(auth), deadline())
        except REFUSED:
            raise refuse() from None

    @app.middleware("http")
    async def no_store(request, call_next):
        result = await call_next(request)
        result.headers.update(HEADERS)
        return result

    @app.post("/inbox/starts", status_code=201)
    async def start(
        payload: Start, idempotency_key: UUID4 = Header(), authorization: str | None = Header(None)
    ):
        try:
            job = await fixture.submit(
                bearer(authorization), KEY_PREFIX + str(idempotency_key), payload.text, deadline()
            )
        except REFUSED:
            raise refuse("acceptance_unconfirmed") from None
        return {"items": job.items()}

    @app.get("/inbox")
    async def inbox(authorization: str | None = Header(None)):
        return {"items": [item for job in await read(authorization) for item in job.items()]}

    @app.get("/inbox/starts/by-key/{key}")
    async def by_key(key: UUID4, authorization: str | None = Header(None)):
        for job in await read(authorization):
            if job.key == KEY_PREFIX + str(key):
                return {"items": job.items()}
        raise refuse("no_match", 404)

    @app.post("/inbox/status")
    async def status(payload: Status, authorization: str | None = Header(None)):
        jobs = await read(authorization)
        if payload.reply_to_item is not None:
            jobs = [j for j in jobs if payload.reply_to_item in {i["id"] for i in j.items()}]
        elif normalize(payload.text) != "how's that going?":
            jobs = [j for j in jobs if normalize(j.text) == normalize(payload.text)]
        if not jobs:
            raise refuse("no_match", 404)
        if len(jobs) > 1:
            return {
                "kind": "clarify",
                "text": "Which job?",
                "choices": [{"ref": j.items()[1]["id"], "title": j.text} for j in jobs],
            }
        return {
            "kind": "queued",
            "ref": jobs[0].items()[1]["id"],
            "text": "Saved. I can't confirm it has started.",
        }

    return app
