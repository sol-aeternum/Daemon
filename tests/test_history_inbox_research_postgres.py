"""Disposable native research: original H-authorized model/search/model sequence."""

import asyncio
import json
from uuid import uuid4

import pytest
from asyncpg.transaction import Transaction

from orchestrator.services.restore_history.model import SendPossible
from orchestrator.tools.web_search import BRAVE_FIXED_MICROUSD
from tests.test_history_inbox_ui_postgres import PATH, client
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_text_execution_postgres import (
    db as db,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)
from tests.test_history_composition_postgres import deadline

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize("text_host", ["research"], indirect=True),
]

QUESTIONS = ("What feeds the fictional Dawn Lake?", "How does the fictional Hill Library work?")
CORPUS = {
    QUESTIONS[0]: [
        {
            "title": "Dawn Lake field notes",
            "url": "https://dawn.example/lake",
            "description": "A snowmelt stream feeds the lake.",
        }
    ],
    QUESTIONS[1]: [
        {
            "title": "Hill Library handbook",
            "url": "https://hill.example/library",
            "description": "Volunteers lend books weekly.",
        }
    ],
}


async def start(http, headers, question, key=None):
    return await http.post(
        PATH,
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json={"text": question},
    )


async def status(http, headers, topic=None):
    response = await http.get(PATH, headers=headers, params={"topic": topic} if topic else {})
    assert response.status_code == 200, response.text
    return response.json()


async def facts(s):
    async with s.host._reader._history.operation(deadline=deadline()) as operation:
        return (await operation.verify()).entries


async def ledger(s):
    async with s.db.connection() as conn:
        return await conn.fetch(
            "SELECT id,scope_id,route_id,provider,model,status,reserved_microusd,"
            "actual_microusd,period_key FROM entitlement_reservations "
            "WHERE user_id=$1 ORDER BY id",
            s.account,
        )


async def test_research_two_originals_exact_report_and_read_only_reopen(text_host):
    s = text_host
    s.research_corpus = CORPUS
    _, desktop = await device(s, s.account)
    _, phone = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert await status(http, phone) == {
            "state": "empty",
            "can_start": ["X"],
            "research_questions": [],
        }
        assert (await start(http, desktop, " " * 2)).status_code == 422
        assert (await start(http, desktop, "q" * 513)).status_code == 422
        keys = [str(uuid4()), str(uuid4())]
        reports = []
        for index, question in enumerate(QUESTIONS):
            s.entered.clear()
            s.release.clear()
            response = await start(http, desktop, " " + question + " ", keys[index])
            assert response.status_code == 200, response.text
            assert response.json()["research_questions"] == list(QUESTIONS[: index + 1])
            await asyncio.wait_for(s.entered.wait(), 10)
            assert (await start(http, phone, "Another question?")).status_code == 409
            if index == 1:
                assert await status(http, phone) == {
                    "state": "ambiguous",
                    "choices": ["X", "Y"],
                    "can_start": [],
                    "research_questions": list(QUESTIONS),
                }
                assert (await status(http, phone, "X"))["conversation_id"] == reports[0]
            s.release.set()
            assert await jobs(s) == ["completed"] * (index + 1), s.errors
            result = await status(http, phone, ("X", "Y")[index])
            assert result["state"] == "completed"
            assert result["can_start"] == (["Y"] if index == 0 else [])
            conv = result["conversation_id"]
            reports.append(conv)
            assert (await start(http, desktop, question)).status_code == 409
            detail = await http.get(f"/conversations/{conv}", headers=phone)
            assert detail.status_code == 200, detail.text
            answer = detail.json()["messages"][-1]
            assert answer["content"] == s.reports[index]
            assert answer["tool_calls"] == [
                {"name": "web_search", "arguments": {"query": question}}
            ]
            assert answer["tool_results"] == [
                {
                    "name": "web_search",
                    "result": {"query": question, "results": CORPUS[question], "total_found": 1},
                    "outcome": "succeeded",
                }
            ]
            assert question in answer["tool_calls"][0]["arguments"]["query"]
            assert CORPUS[question][0]["description"] in json.dumps(
                s.model_requests[index * 2 + 1].messages
            )
        entries = await facts(s)
        sends = [e.command.data for e in entries if type(e.command.data) is SendPossible]
        assert [send.effect_kind for send in sends] == [
            "completion",
            "web_search",
            "completion",
        ] * 2
        assert len({send.reservation_id for send in sends}) == 6
        rows = await ledger(s)
        assert len(rows) == 6 and all(row["status"] == "settled" for row in rows)
        assert len({row["scope_id"] for row in rows}) == 2
        assert [row["actual_microusd"] for row in rows if row["provider"] == "brave"] == [
            5000,
            5000,
        ]
        assert len(s.calls) == 4 and len(s.search_calls) == 2
        for index in range(2):
            original = sends[index * 3 : index * 3 + 3]
            assert len({send.lifecycle_id for send in original}) == 1
            assert len({send.financial_scope_id for send in original}) == 1
            assert s.calls[index * 2] == s.calls[index * 2 + 1] == s.search_calls[index].identity
    # Observer-only reconstruction: no work admission, original report and records unchanged.
    s.research_observer = True
    async with s.make(), client(s) as http:
        for index, topic in enumerate(("X", "Y")):
            result = await status(http, phone, topic)
            assert result["conversation_id"] == reports[index]
            assert result["can_start"] == []
            detail = await http.get(f"/conversations/{reports[index]}", headers=phone)
            assert detail.json()["messages"][-1]["content"] == s.reports[index]
        assert (await start(http, phone, "Third question?")).status_code == 503
        assert await facts(s) == entries
        assert await ledger(s) == rows
        assert not s.execution._jobs
        assert len(s.calls) == 4 and len(s.search_calls) == 2


@pytest.mark.parametrize("failure", ["search", "extra-call"])
async def test_research_failure_never_synthesizes(text_host, failure):
    s = text_host
    s.research_corpus = CORPUS
    s.search_failure = failure == "search"
    s.unsolicited = failure == "extra-call"
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTIONS[0])).status_code == 200
        await asyncio.wait_for(s.entered.wait(), 10)
        s.release.set()
        assert (await jobs(s))[0] != "completed"
        assert len(s.calls) == 1
        assert len(s.search_calls) == int(failure == "search")
        response = await http.get(PATH, headers=headers)
        assert response.status_code in (409, 503)
        assert (await start(http, headers, QUESTIONS[1])).status_code in (409, 503)
        assert len(s.calls) == 1
        if failure == "search":
            search = next(r for r in await ledger(s) if r["provider"] == "brave")
            assert search["status"] == "settled"
            assert search["actual_microusd"] == search["reserved_microusd"]


async def test_research_stop_during_search_no_synthesis(text_host, monkeypatch):
    from orchestrator.tasks import runner

    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.1)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.1)
    s = text_host
    s.research_corpus = CORPUS
    s.search_release.clear()
    _, desktop = await device(s, s.account)
    _, phone = await device(s, s.account)
    async with s.make(), client(s) as http:
        key = str(uuid4())
        assert (await start(http, desktop, QUESTIONS[0], key)).status_code == 200
        s.release.set()
        await asyncio.wait_for(s.search_entered.wait(), 10)
        task = s.calls[0].task_id
        response = await http.post(f"/tasks/{task}/cancel", headers=phone)
        assert response.status_code == 200, response.text
        assert await jobs(s) == ["cancelled"], s.errors
        assert s.search_closed.is_set()
        s.search_release.set()
        result = await status(http, phone)
        assert result["state"] == "cancelled" and result["can_start"] == []
        assert len(s.calls) == 1 and len(s.search_calls) == 1


@pytest.mark.parametrize("change", ["stop", "generation"])
async def test_search_pacing_rechecks_original_admission(text_host, monkeypatch, change):
    from orchestrator.tasks import runner

    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.1)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.1)
    s = text_host
    s.research_corpus = CORPUS
    entered = asyncio.Event()
    release = asyncio.Event()
    pacer = s.research_pacer

    class PausedPacer:
        async def admit(self, *args, **kwargs):
            await pacer.admit(*args, **kwargs)
            entered.set()
            await release.wait()

        async def observe(self, *args, **kwargs):
            await pacer.observe(*args, **kwargs)

    s.research_pacer = PausedPacer()
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        key = str(uuid4())
        assert (await start(http, headers, QUESTIONS[0], key)).status_code == 200
        s.release.set()
        await asyncio.wait_for(entered.wait(), 10)
        assert len(s.calls) == 1 and not s.search_calls
        assert [
            e.command.data.effect_kind
            for e in await facts(s)
            if type(e.command.data) is SendPossible
        ] == ["completion"]
        if change == "stop":
            result = await http.post(f"/tasks/{s.calls[0].task_id}/cancel", headers=headers)
            assert result.status_code == 200
        else:
            async with s.db.connection() as conn:
                await conn.execute(
                    "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
                    s.account,
                )
        try:
            assert (await jobs(s))[0] != "completed"
        finally:
            release.set()
        assert len(s.calls) == 1 and not s.search_calls
        search = next(r for r in await ledger(s) if r["provider"] == "brave")
        # A known premark Stop can settle zero; a refused generation/uncertain
        # owner must not turn the reservation into permission to send again.
        assert search["status"] == "open" or (
            search["status"] == "settled" and search["actual_microusd"] == 0
        )
        assert (await start(http, headers, QUESTIONS[1])).status_code in (409, 503)


@pytest.mark.parametrize("after_commit", [False, True])
@pytest.mark.parametrize("leg", [0, 1, 2])
async def test_research_send_ack_loss_blocks_transport_and_synthesis(
    text_host, monkeypatch, after_commit, leg
):
    s = text_host
    s.research_corpus = CORPUS
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        projection = s.host._acceptance._projection
        stage = projection.stage_effect
        commit = Transaction.commit
        target = None
        injected = False
        ordinal = 0

        async def staged(conn, entry, **kwargs):
            nonlocal target, ordinal
            result = await stage(conn, entry, **kwargs)
            if type(entry.command.data) is SendPossible:
                if ordinal == leg:
                    target = conn._con._top_xact
                    assert target is not None
                ordinal += 1
            return result

        async def lost_ack(tx):
            nonlocal injected, target
            if tx is not target:
                return await commit(tx)
            target = None
            injected = True
            if after_commit:
                await commit(tx)
            else:
                await tx.rollback()
            raise ConnectionError("synthetic search mark acknowledgement loss")

        with monkeypatch.context() as fault:
            fault.setattr(projection, "stage_effect", staged)
            fault.setattr(Transaction, "commit", lost_ack)
            response = await start(http, headers, QUESTIONS[0])
            assert response.status_code in (200, 409, 503)
            s.release.set()
            assert (await jobs(s))[0] != "completed"
        model_calls, search_calls = (0, 0) if leg == 0 else ((1, 0) if leg == 1 else (1, 1))
        assert injected and ordinal == leg + 1
        assert len(s.calls) == model_calls and len(s.search_calls) == search_calls
        rows = await ledger(s)
        holds = [row for row in rows if row["status"] == "open"]
        assert len(rows) == leg + 1 and len(holds) == 1 and holds[0]["actual_microusd"] is None
        async with s.db.connection() as conn:
            assert await conn.fetchval("SELECT count(*) FROM compute_reservation_metadata") == (
                leg + int(after_commit)
            )
        assert (await start(http, headers, QUESTIONS[0])).status_code in (409, 503)
        assert len(s.calls) == model_calls and len(s.search_calls) == search_calls
        assert await ledger(s) == rows


async def test_research_acceptance_lost_ack_never_schedules(text_host, monkeypatch):
    s = text_host
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        projection = s.host._acceptance._projection
        stage = projection.stage
        exit_tx = Transaction.__aexit__
        target = None
        injected = False

        async def staged(conn, entry, **kwargs):
            nonlocal target
            result = await stage(conn, entry, **kwargs)
            target = conn._con._top_xact
            assert target is not None
            return result

        async def lost_ack(tx, kind, value, trace):
            nonlocal injected, target
            if tx is target and kind is None:
                target = None
                injected = True
                await exit_tx(tx, kind, value, trace)
                raise ConnectionError("synthetic research acceptance acknowledgement loss")
            return await exit_tx(tx, kind, value, trace)

        key = str(uuid4())
        with monkeypatch.context() as fault:
            fault.setattr(projection, "stage", staged)
            fault.setattr(Transaction, "__aexit__", lost_ack)
            assert (await start(http, headers, QUESTIONS[0], key)).status_code == 503
        assert injected
        result = await status(http, headers)
        assert result == {
            "state": "queued",
            "topic": "X",
            "can_start": [],
            "research_questions": [QUESTIONS[0]],
        }
        assert (await start(http, headers, QUESTIONS[0], key)).status_code in (409, 503)
        assert not s.execution._jobs and not s.calls and not s.search_calls and not await ledger(s)


async def test_research_saved_trace_tamper_refuses_even_other_read(text_host):
    s = text_host
    s.research_corpus = CORPUS
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTIONS[0])).status_code == 200
        s.release.set()
        assert await jobs(s) == ["completed"]
        original = await status(http, headers)
        detail = await http.get(f"/conversations/{original['conversation_id']}", headers=headers)
        assert detail.status_code == 200
        result_id = detail.json()["messages"][-1]["id"]
        from uuid import UUID
        from orchestrator.memory.encryption import ContentEncryption

        trace = detail.json()["messages"][-1]["tool_results"]
        trace[0]["result"]["results"][0]["url"] = "javascript:bad"
        enc = ContentEncryption(s.encryption_key)
        envelope = {
            "format": "daemon.encrypted_tool_trace",
            "version": 1,
            "ciphertext": enc.encrypt(json.dumps(trace)),
        }
        async with s.db.connection() as conn:
            await conn.execute(
                "UPDATE messages SET tool_results=$2::jsonb WHERE id=$1",
                UUID(result_id),
                json.dumps(envelope),
            )
        assert (await http.get(PATH, headers=headers)).status_code == 409
        assert (
            await http.get(f"/conversations/{original['conversation_id']}", headers=headers)
        ).status_code == 409
        assert (await start(http, headers, QUESTIONS[1])).status_code == 409
        assert len(s.calls) == 2 and len(s.search_calls) == 1
        search = next(r for r in await ledger(s) if r["provider"] == "brave")
        assert search["actual_microusd"] == BRAVE_FIXED_MICROUSD


@pytest.mark.parametrize("amount", [0, BRAVE_FIXED_MICROUSD - 1])
async def test_completed_research_rejects_altered_fixed_search_settlement(text_host, amount):
    s = text_host
    s.research_corpus = CORPUS
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        key = str(uuid4())
        assert (await start(http, headers, QUESTIONS[0], key)).status_code == 200
        s.release.set()
        assert await jobs(s) == ["completed"]
        saved = await status(http, headers)
        async with s.db.connection() as conn:
            await conn.execute(
                "UPDATE entitlement_reservations SET actual_microusd=$2 "
                "WHERE user_id=$1 AND provider='brave'",
                s.account,
                amount,
            )
        assert (await http.get(PATH, headers=headers)).status_code == 409
        assert (
            await http.get(f"/conversations/{saved['conversation_id']}", headers=headers)
        ).status_code == 409
        assert (await start(http, headers, QUESTIONS[1])).status_code == 409
        assert len(s.calls) == 2 and len(s.search_calls) == 1


@pytest.mark.parametrize("leg", ["first", "synthesis"])
async def test_model_transport_failure_after_send_never_replays(text_host, leg):
    s = text_host
    s.research_corpus = CORPUS
    s.model_failure_leg = leg
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        key = str(uuid4())
        response = await start(http, headers, QUESTIONS[0], key)
        assert response.status_code in (200, 409, 503)
        s.release.set()
        assert (await jobs(s))[0] != "completed"
        model_calls, search_calls = (1, 0) if leg == "first" else (2, 1)
        assert len(s.calls) == model_calls and len(s.search_calls) == search_calls
        sends = [e.command.data for e in await facts(s) if type(e.command.data) is SendPossible]
        assert len(sends) == model_calls + search_calls
        rows = await ledger(s)
        assert len(rows) == len(sends)
        assert {r["id"] for r in rows} == {send.reservation_id for send in sends}
        failed = next(r for r in rows if r["id"] == sends[-1].reservation_id)
        assert (
            failed["status"] == "settled"
            and 0 < failed["actual_microusd"] <= failed["reserved_microusd"]
        )
        assert (await start(http, headers, QUESTIONS[0], key)).status_code in (409, 503)
        assert await ledger(s) == rows


@pytest.mark.parametrize("leg", [0, 1, 2])
async def test_research_settlement_ack_loss_with_no_open_holds_is_not_completion_authority(
    text_host, monkeypatch, leg
):
    from orchestrator.entitlements.service import EntitlementService

    s = text_host
    s.research_corpus = CORPUS
    _, headers = await device(s, s.account)
    settle = EntitlementService.settle
    injected = False
    ordinal = 0

    async def lost_ack(owner, reservation, amount, **kwargs):
        nonlocal injected, ordinal
        result = await settle(owner, reservation, amount, **kwargs)
        current = ordinal
        ordinal += 1
        if current == leg:
            assert not injected
            injected = True
            raise ConnectionError("synthetic search settlement acknowledgement loss")
        return result

    monkeypatch.setattr(EntitlementService, "settle", lost_ack)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTIONS[0])).status_code == 200
        s.release.set()
        assert (await jobs(s))[0] != "completed"
        model_calls, search_calls = (1, 0) if leg == 0 else ((1, 1) if leg == 1 else (2, 1))
        assert injected and ordinal == leg + 1
        assert len(s.calls) == model_calls and len(s.search_calls) == search_calls
        rows = await ledger(s)
        assert len(rows) == leg + 1 and all(row["status"] == "settled" for row in rows)
        assert (await start(http, headers, QUESTIONS[1])).status_code in (409, 503)
        assert await ledger(s) == rows
        assert len(s.calls) == model_calls and len(s.search_calls) == search_calls


@pytest.mark.parametrize("cut", ["commit", "terminal", "post-terminal-owner"])
async def test_terminal_commit_ack_loss_never_authorizes_second_original(
    text_host, monkeypatch, cut
):
    s = text_host
    s.research_corpus = CORPUS
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        projection = s.host._acceptance._projection
        stage = projection.stage_terminal
        commit = Transaction.commit
        target = None
        injected = False

        async def staged(conn, entry, **kwargs):
            nonlocal target
            result = await stage(conn, entry, **kwargs)
            target = conn._con._top_xact
            assert target is not None
            return result

        async def lost_ack(tx):
            nonlocal injected, target
            if tx is not target:
                return await commit(tx)
            target = None
            injected = True
            await commit(tx)
            raise ConnectionError("synthetic terminal acknowledgement loss")

        with monkeypatch.context() as fault:
            if cut == "commit":
                fault.setattr(projection, "stage_terminal", staged)
                fault.setattr(Transaction, "commit", lost_ack)
            else:
                from tests.history_restart_faults import install

                install(fault, s.host, s.execution, cut, successor=False)
            assert (await start(http, headers, QUESTIONS[0])).status_code == 200
            s.release.set()
            await jobs(s)
        assert injected == (cut == "commit")
        assert len(s.calls) == 2 and len(s.search_calls) == 1
        saved = await status(http, headers)
        assert saved["state"] == "completed" and saved["can_start"] == []
        detail = await http.get(f"/conversations/{saved['conversation_id']}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["messages"][-1]["content"] == s.reports[0]
        rows = await ledger(s)
        assert len(rows) == 3 and all(row["status"] == "settled" for row in rows)
        assert (await start(http, headers, QUESTIONS[1])).status_code in (409, 503)
        assert len(s.calls) == 2 and len(s.search_calls) == 1
        assert await ledger(s) == rows
