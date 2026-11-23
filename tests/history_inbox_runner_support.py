"""Disposable inbox binding for the existing isolated text host. Never installed at startup."""

import json
from uuid import UUID, uuid4

from fastapi import Header, Request, Response
from pydantic import ValidationError
from starlette.responses import JSONResponse

from orchestrator.auth import _extract_bearer_token
from orchestrator.request_body_limit import RequestBodyLimitMiddleware
from orchestrator.routes.tasks import historical_task_out
from orchestrator.services.account_control.guard import CapturedGeneration, guard_content_write
from orchestrator.services.connection_cleanup import pool_connection, retain_pool_cleanup
from orchestrator.services.restore_history.acceptance_host import (
    IsolatedAcceptanceHost,
    create_isolated_ui_app,
)
from orchestrator.services.restore_history.model import AcceptPrepare, HistoryError
from tests.history_inbox_support import (
    DDL,
    HEADERS,
    REFUSED,
    Start,
    ids,
    links_match,
    qualify_inbox,
    refuse,
    root_for,
)
from tests.test_history_composition_postgres import deadline

# The native A transaction already holds the account SHARE lock and has inserted
# both messages. No additional account lock or privileged read of the link.
TRIGGER = """
CREATE FUNCTION public.fixture_bind_inbox_task() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
    IF current_user <> 'md_acceptor' OR current_user <> session_user THEN
        RAISE EXCEPTION 'fixture inbox acceptor required';
    END IF;
    INSERT INTO public.fixture_inbox_links
        (inbox_id,account_id,generation,task_id,conversation_id,user_message_id,result_message_id)
    SELECT i.id, NEW.user_id, i.generation, NEW.id, NEW.conversation_id,
           NEW.user_message_id, NEW.result_message_id
      FROM public.fixture_inboxes i JOIN public.entitlement_accounts a
        ON (i.account_id,i.generation)=(a.user_id,a.content_generation)
     WHERE i.account_id=NEW.user_id AND a.status='active'
       AND split_part(NEW.idempotency_key, ':', 2)=i.id::text;
    IF NOT FOUND THEN RAISE EXCEPTION 'fixture inbox root mismatch'; END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.fixture_bind_inbox_task() FROM PUBLIC;
CREATE TRIGGER fixture_bind_inbox_task AFTER INSERT ON public.tasks
FOR EACH ROW WHEN (NEW.idempotency_key LIKE 'ibox1:%')
EXECUTE FUNCTION public.fixture_bind_inbox_task();
"""


async def add_root(s, account):
    async with s.db.connection() as conn:
        return await conn.fetchval(
            """INSERT INTO public.fixture_inboxes SELECT $1,user_id,content_generation
               FROM public.entitlement_accounts WHERE user_id=$2 RETURNING id""",
            uuid4(),
            account,
        )


async def install_inbox(s, account):
    s.db.sql("BEGIN;" + DDL + TRIGGER + "COMMIT;")
    return await add_root(s, account)


def inbox_key(root, request):
    return f"ibox1:{root}:{request}"


def _key_root(key):
    prefix, raw_root, raw_request = key.split(":")
    root, request = UUID(raw_root), UUID(raw_request)
    if prefix != "ibox1" or request.version != 4 or key != inbox_key(root, request):
        raise ValueError("invalid fixture inbox key")
    return root


async def _check_writer(host):
    async with retain_pool_cleanup(), pool_connection(host._acceptance._tasks._pool) as conn:
        await qualify_inbox(conn, writing=True)
        if not await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger
               WHERE tgrelid='public.tasks'::regclass AND tgname='fixture_bind_inbox_task'
               AND tgenabled='O' AND tgtype=5 AND NOT tgisinternal
               AND tgfoid='public.fixture_bind_inbox_task()'::regprocedure)"""
        ):
            raise HistoryError("fixture_inbox_binding_missing")


async def _check_link(host, token, key, snapshot, limit):
    """Additional restriction only. Native lookup owns the returned status and content."""
    reader = host._reader
    async with reader._history.operation(deadline=limit) as op, retain_pool_cleanup():
        async with pool_connection(reader._tasks._pool) as conn:
            async with conn.transaction(isolation="read_committed"):
                auth = await host._auth(conn, token, limit)
                if auth is None:
                    return refuse("invalid_authority", 401)
                await qualify_inbox(conn, writing=False)
                xid = await conn.fetchval("SELECT txid_current()")
                state = await op.verify()
                if not await reader._parity_owned(op, conn, xid, state, limit):
                    return refuse()
                facts = [
                    e.command.data
                    for e in state.entries
                    if type(e.command.data) is AcceptPrepare
                    and e.command.data.account_id == auth.user_id
                ]
                if len(facts) != 1:
                    return refuse()
                (fact,) = facts
                capture = CapturedGeneration(auth.user_id, fact.account_generation)
                async with guard_content_write(conn, capture):
                    root = await root_for(conn, capture)
                    if root != _key_root(key) or not await links_match(conn, capture, root, facts):
                        return refuse()
                    row = await reader._task_owned(op, conn, xid, auth, fact, key, state, limit)
                    if row is None or ids(fact) != (
                        snapshot.task_id,
                        snapshot.conversation_id,
                        row["user_message_id"],
                        snapshot.result_message_id,
                    ):
                        return refuse()
                    if await host._auth(conn, token, limit) != auth:
                        return refuse("invalid_authority", 401)
                    await reader._check_owned(op, conn, xid, limit)
    return None


def inbox_app(host):
    """Translate the start body and pass through the *actual* /chat response and disconnect."""
    if (
        type(host) is not IsolatedAcceptanceHost
        or host._execution is None
        or host._acceptance._projection.profile != "terminal-v3"
    ):
        raise HistoryError("fixture_inbox_text_host_required")
    app = create_isolated_ui_app(host)

    @app.get("/inbox/starts/by-key/{key}")
    async def reopen(key: str, response: Response, authorization: str | None = Header(None)):
        response.headers.update(HEADERS)
        limit = deadline()
        try:
            observed = await host.lookup(authorization, key, deadline=limit)
            result = historical_task_out(observed)
            token = _extract_bearer_token(authorization)
            assert token is not None and observed.snapshot is not None
            refusal = await host._run(
                _check_link(host, token, key, observed.snapshot, limit),
                limit,
                writing=False,
            )
            if refusal is not None:
                raise refusal
            return result
        except REFUSED:
            raise refuse() from None

    async def adapt(scope, receive, send):
        if (scope["type"], scope.get("method"), scope.get("path")) != (
            "http",
            "POST",
            "/inbox/starts",
        ):
            if scope["type"] == "http" and scope["method"] != "GET":
                return await JSONResponse({"detail": "Not found"}, 404, HEADERS)(
                    scope, receive, send
                )
            return await app(scope, receive, send)
        incoming = Request(scope, receive)
        try:
            _key_root(incoming.headers.get("Idempotency-Key", ""))
            payload = Start.model_validate_json(await incoming.body())
        except (ValidationError, ValueError):
            return await JSONResponse({"detail": "Invalid inbox request"}, 422, HEADERS)(
                scope, receive, send
            )
        try:
            limit = deadline()
            host._admit(limit)
            await host._run(_check_writer(host), limit, writing=False)
        except REFUSED:
            return await JSONResponse({"detail": refuse().detail}, 503, HEADERS)(
                scope, receive, send
            )
        body = json.dumps({"message": payload.text, "disable_memory_write": True}).encode()
        headers = [
            (k, v) for k, v in scope["headers"] if k not in (b"content-length", b"content-type")
        ]
        headers.extend(
            [(b"content-length", str(len(body)).encode()), (b"content-type", b"application/json")]
        )
        pending = True

        async def translated_receive():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await app(
            {**scope, "path": "/chat", "raw_path": b"/chat", "headers": headers},
            translated_receive,
            send,
        )

    return RequestBodyLimitMiddleware(adapt, global_limit=4096, route_limits={})
