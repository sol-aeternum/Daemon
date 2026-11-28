"""Interrupted source writes in both durability domains cannot authorize a later leg."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from asyncpg.transaction import Transaction

from orchestrator.services.restore_history.coordination import HistoryOperation
from orchestrator.services.restore_history.source_execution import SourceRun
from orchestrator.services.restore_history.model import SourcePinPrepare, SourceReadPossible
from orchestrator.services.restore_history.terminal_projection import InboxSourcesHistoryProjection
from tests.test_history_inbox_research_postgres import client, ledger, start
from tests.test_history_inbox_sources_postgres import QUESTION, URL, configure
from tests.test_history_text_execution_postgres import (
    db as db,
    device,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["sources"], indirect=True)
@pytest.mark.parametrize("record", [SourceReadPossible, SourcePinPrepare])
@pytest.mark.parametrize(
    "boundary", ["before-append", "after-append", "after-stage", "after-commit"]
)
async def test_source_receipt_fault_fences_original_and_successor(
    text_host, monkeypatch, record, boundary
):
    s = text_host
    configure(s)
    _, headers = await device(s, s.account)
    triggered = False
    target = None
    append = HistoryOperation.append
    stage = InboxSourcesHistoryProjection.stage_source
    commit = Transaction.commit
    exit_tx = Transaction.__aexit__

    def interrupt():
        nonlocal triggered
        assert not triggered
        triggered = True
        raise ConnectionError("synthetic source publication interrupted")

    async def interrupted_append(self, command):
        if type(command.data) is record and boundary == "before-append":
            interrupt()
        result = await append(self, command)
        if type(command.data) is record and boundary == "after-append":
            interrupt()
        return result

    async def interrupted_stage(self, conn, entry, *, deadline):
        nonlocal target
        result = await stage(self, conn, entry, deadline=deadline)
        if type(entry.command.data) is record:
            if boundary == "after-stage":
                interrupt()
            if boundary == "after-commit":
                target = conn._con._top_xact
                assert target is not None
        return result

    async def committed(tx):
        await commit(tx)
        if tx is target:
            interrupt()

    async def exited(tx, kind, value, trace):
        result = await exit_tx(tx, kind, value, trace)
        if tx is target and kind is None:
            interrupt()
        return result

    async with s.make(), client(s) as http:
        with monkeypatch.context() as patch:
            patch.setattr(HistoryOperation, "append", interrupted_append)
            patch.setattr(InboxSourcesHistoryProjection, "stage_source", interrupted_stage)
            patch.setattr(Transaction, "commit", committed)
            patch.setattr(Transaction, "__aexit__", exited)
            assert (await start(http, headers, QUESTION)).status_code == 200
            assert (await jobs(s))[0] != "completed"
        assert triggered
        kind = "SOURCE_PIN_PREPARE" if record is SourcePinPrepare else "SOURCE_READ_POSSIBLE"
        async with s.db.connection() as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM task_history_projection WHERE kind=$1", kind
            ) == int(boundary == "after-commit")
            expected_snapshots = int(record is SourcePinPrepare and boundary == "after-commit")
            assert await conn.fetchval("SELECT count(*) FROM web_snapshots") == expected_snapshots
            assert (
                await conn.fetchval("SELECT count(*) FROM task_events WHERE kind='page_refreshed'")
                == expected_snapshots
            )
        rows = await ledger(s)
        assert len(rows) == 3
        denied = await start(http, headers, "What are the opening hours?")
        assert denied.status_code in (409, 503)
        assert len(s.calls) == 2 and len(s.search_calls) == 1
        assert s.source_calls == ([URL] if record is SourcePinPrepare else []) and not s.reports
        assert await ledger(s) == rows


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["sources"], indirect=True)
@pytest.mark.parametrize("revocation", ["stop", "lifecycle-close"])
async def test_revocation_after_possible_before_get_prevents_transport(
    text_host, monkeypatch, revocation
):
    """A possible-read acknowledgement does not survive Stop or original lifecycle closure."""
    s = text_host
    configure(s)
    entered = asyncio.Event()
    resume = asyncio.Event()
    dispatch = SourceRun.dispatch

    async def delayed(self, request):
        entered.set()
        await resume.wait()
        return await dispatch(self, request)

    monkeypatch.setattr(SourceRun, "dispatch", delayed)
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTION)).status_code == 200
        await asyncio.wait_for(entered.wait(), 10)
        try:
            identity = s.calls[0]
            if revocation == "stop":
                stopped = await http.post(f"/tasks/{identity.task_id}/cancel", headers=headers)
                assert stopped.status_code == 200
            else:
                # No Stop intent or task cancellation: the lifecycle check itself
                # must prevent the pending GET after the native close commits.
                async with s.db.connection("md_app") as conn:
                    await conn.fetchval(
                        "SELECT public.attempt_lifecycle_close($1::jsonb,$2,'requested')",
                        identity.wire(),
                        uuid4(),
                    )
        finally:
            resume.set()
        assert (await jobs(s))[0] != "completed"
        assert s.source_calls == [] and not s.reports


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["sources"], indirect=True)
@pytest.mark.parametrize("failure", ["response-close", "omitted-passage", "wrong-citation"])
async def test_cited_report_requires_closed_source_and_unaltered_synthesis(
    text_host, monkeypatch, failure
):
    from orchestrator.tools import completion
    from tests import source_model_fixture

    s = text_host
    configure(s)
    s.source_selection = [URL]
    observed = False
    if failure == "response-close":

        class LostClose(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield source_model_fixture.public_notice("The station lends a tide gauge.").encode()

            async def aclose(self):
                nonlocal observed
                observed = True
                raise ConnectionError("source stream close acknowledgement lost")

        s.source_pages[URL] = httpx.Response(
            200, headers={"content-type": "text/html"}, stream=LostClose()
        )
    elif failure == "omitted-passage":
        prepare = completion._prepare_call_params

        def omitted(*args, **kwargs):
            nonlocal observed
            params = prepare(*args, **kwargs)
            messages = params["messages"]
            if any(m.get("role") == "tool" and m.get("name") == "web_fetch" for m in messages):
                observed = True
                params["messages"] = [
                    m
                    for m in messages
                    if not (m.get("role") == "tool" and m.get("name") == "web_fetch")
                ]
            return params

        monkeypatch.setattr(completion, "_prepare_call_params", omitted)
    else:
        synthesize = source_model_fixture.synthesize

        def misquoted(question, values):
            nonlocal observed
            observed = True
            return synthesize(question, values).replace("`The station", "`A different station")

        monkeypatch.setattr(source_model_fixture, "synthesize", misquoted)

    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTION)).status_code == 200
        assert (await jobs(s))[0] != "completed"
        assert observed and s.source_calls == [URL]
        assert len(s.calls) == (3 if failure == "wrong-citation" else 2)
        assert (
            s.db.sql(
                "SELECT count(*) FROM task_history_projection WHERE kind='TERMINAL_OUTPUT_PREPARE'"
            )
            == "0"
        )
        assert (await start(http, headers, "What are the opening hours?")).status_code in (409, 503)


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["sources"], indirect=True)
@pytest.mark.parametrize("change", ["generation", "lease-expiry", "deleted-conversation", "stop"])
async def test_source_publication_rechecks_after_native_account_lock(
    text_host, monkeypatch, change
):
    from orchestrator.services.web_snapshots import WebSnapshotStore

    s = text_host
    configure(s)
    entered = asyncio.Event()
    resume = asyncio.Event()
    waiting = asyncio.Event()
    lock = WebSnapshotStore._lock_account

    async def returned(_request):
        entered.set()
        await resume.wait()

    async def account_lock(self, conn, user):
        waiting.set()
        await lock(self, conn, user)

    s.source_check = returned
    monkeypatch.setattr(WebSnapshotStore, "_lock_account", account_lock)
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTION)).status_code == 200
        await asyncio.wait_for(entered.wait(), 10)
        task = s.calls[0].task_id
        async with s.db.connection() as owner, owner.transaction():
            # Keep the real native snapshot insert queued on its serialization row.
            await owner.fetchval("SELECT id FROM users WHERE id=$1 FOR NO KEY UPDATE", s.account)
            resume.set()
            await asyncio.wait_for(waiting.wait(), 10)
            if change == "generation":
                await owner.execute(
                    "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
                    s.account,
                )
            elif change == "lease-expiry":
                await owner.execute(
                    "UPDATE tasks SET lease_expires_at=clock_timestamp() WHERE id=$1", task
                )
            elif change == "deleted-conversation":
                await owner.execute(
                    "DELETE FROM conversations WHERE id=(SELECT conversation_id FROM tasks WHERE id=$1)",
                    task,
                )
            else:
                # This is the persisted native Stop fact. The authenticated
                # Stop endpoint and transport cancellation are covered separately.
                await owner.execute(
                    "UPDATE tasks SET cancel_requested_at=clock_timestamp() WHERE id=$1", task
                )
        assert (await jobs(s))[0] != "completed"
        assert len(s.calls) == 2 and s.source_calls == [URL] and not s.reports
        async with s.db.connection() as conn:
            assert await conn.fetchval("SELECT count(*) FROM web_snapshots") == 0
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM task_history_projection WHERE kind='SOURCE_PIN_PREPARE'"
                )
                == 0
            )
