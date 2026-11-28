"""Grounding controls: evaluate saved model prose separately from the citations."""

import asyncio

import pytest

from tests.source_model_fixture import public_notice
from orchestrator.services.restore_history.source_execution import validate_report
from orchestrator.services.restore_history.model import (
    SourceReadPossible,
    SourcePinPrepare,
    StopIntent,
    TerminalOutputPrepare,
)
from tests.test_history_inbox_research_postgres import client, facts, ledger, start, status
from tests.test_history_text_execution_postgres import (
    db as db,
    device,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What lighting do the reading room notices specify?", "disagree"),
        ("Who financed the excavation?", "do not establish"),
    ],
)
@pytest.mark.parametrize("text_host", ["sources"], indirect=True)
async def test_saved_prose_distinguishes_conflict_from_missing_fact(text_host, question, expected):
    s = text_host
    passages = ["Lighting: amber", "Lighting: violet"]
    s.research_corpus = {question: []}
    for i, passage in enumerate(passages, 1):
        url = f"https://archive.example/notice-{i}"
        s.research_corpus[question].append(
            {"url": url, "title": "Notice", "description": "Public notice."}
        )
        s.source_pages[url] = public_notice(passage)
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, question)).status_code == 200
        s.release.set()
        assert await jobs(s) == ["completed"], s.errors
        saved = await status(http, headers)
        detail = await http.get(f"/conversations/{saved['conversation_id']}", headers=headers)
        assert detail.status_code == 200
        answer, evidence = detail.json()["messages"][-1]["content"].split("\n\nSources:\n\n")
        assert expected in answer and answer.endswith("[1, 2]")
        if expected == "disagree":
            assert "amber" in answer and "violet" in answer and "unresolved" in answer
        assert all(f"`{p}`" in evidence for p in passages)
        assert len(s.calls) == 3 and len(s.search_calls) == 1 and len(s.source_calls) == 2


def test_report_validates_quoted_unicode_span_without_prescribing_model_prose():
    uid = "5e752940-3da1-42b7-b650-e548e356afe5"
    url = "https://station.example/desk"
    value = {
        "url": url,
        "snapshot_id": uid,
        "start_char": 0,
        "end_char": 9,
        "content": "🌊 At nine",
    }
    reference = f"`At nine` [Source {uid} 2:9](<{url}>)"
    for claim in ("The desk opens at nine.", "Nine is the time given; the day is unspecified."):
        validate_report(f"{claim} [1]\n\nSources:\n\n{reference}", [value])
    good = f"The notice gives a time. [1]\n\nSources:\n\n{reference}"
    for bad in (
        good.replace("At nine", "At noon"),
        good.replace("2:9", "1:8"),
        good.replace(uid, "48ef3f8d-6d93-4af9-b36b-b43ed8c9b85d"),
        good.replace(url, "https://other.example/desk"),
        good.replace("[1]", "[2]"),
        good.replace("The notice", "https://other.example"),
        good + "\n\n" + reference,
    ):
        with pytest.raises(ValueError):
            validate_report(bad, [value])


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["sources"], indirect=True)
async def test_phone_stop_during_first_page_closes_transport_without_synthesis(
    text_host, monkeypatch
):
    from orchestrator.tasks import runner

    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.1)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.1)
    s = text_host
    question = "When does the library open?"
    urls = ["https://archive.example/hours", "https://library.example/hours"]
    s.research_corpus = {
        question: [{"url": url, "title": "Hours", "description": "Public notice."} for url in urls]
    }
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def unfinished(request):
        assert str(request.url) == urls[0]
        entered.set()
        try:
            await asyncio.Future()
        finally:
            closed.set()

    s.source_check = unfinished
    _, desktop = await device(s, s.account)
    _, phone = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, desktop, question)).status_code == 200
        s.release.set()
        await asyncio.wait_for(entered.wait(), 10)
        task = s.calls[0].task_id
        stopped = await http.post(f"/tasks/{task}/cancel", headers=phone)
        assert stopped.status_code == 200, stopped.text
        assert await jobs(s) == ["cancelled"], s.errors
        assert closed.is_set()
        saved = await status(http, phone)
        assert saved["state"] == "cancelled" and saved["can_start"] == []
        assert len(s.calls) == 2 and len(s.search_calls) == 1
        assert s.source_calls == urls[:1] and not s.reports
        assert len(await ledger(s)) == 3
        kinds = [type(e.command.data) for e in await facts(s)]
        assert kinds.count(SourceReadPossible) == kinds.count(StopIntent) == 1
        assert not {SourcePinPrepare, TerminalOutputPrepare}.intersection(kinds)


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["sources"], indirect=True)
@pytest.mark.parametrize(
    "change", ["expire", "foreign-expired", "changed-snapshot", "changed-report"]
)
async def test_saved_report_distinguishes_retention_from_inconsistent_evidence(text_host, change):
    from uuid import UUID
    from tests.test_history_inbox_sources_postgres import configure, QUESTION

    s = text_host
    configure(s)
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTION)).status_code == 200
        assert await jobs(s) == ["completed"]
        saved = await status(http, headers)
        path = f"/conversations/{saved['conversation_id']}"
        original = (await http.get(path, headers=headers)).json()["messages"][-1]
        charges = await ledger(s)
        reads = list(s.source_calls)
        async with s.db.connection() as conn:
            page = await conn.fetchval("SELECT id FROM web_snapshots ORDER BY id LIMIT 1")
            if change == "expire":
                await conn.execute("UPDATE web_snapshots SET expires_at=now() WHERE id=$1", page)
            elif change == "foreign-expired":
                other = await s.db.account(conn)
                conversation = await conn.fetchval(
                    "INSERT INTO conversations(user_id,pipeline) VALUES($1,'cloud') RETURNING id",
                    other,
                )
                await conn.execute(
                    "UPDATE web_snapshots SET expires_at=now(),user_id=$2,conversation_id=$3 WHERE id=$1",
                    page,
                    other,
                    conversation,
                )
            elif change == "changed-snapshot":
                encrypted = s.execution.encryption.encrypt("Different station notice")
                await conn.execute(
                    "UPDATE web_snapshots SET content_encrypted=$2 WHERE id=$1", page, encrypted
                )
            else:
                # A different answer can have valid quotes yet lack the original's receipt.
                _, sources = original["content"].split("\n\nSources:\n\n")
                changed = "The readings are inconclusive. [1, 2]\n\nSources:\n\n" + sources
                await conn.execute(
                    "UPDATE messages SET content=$2 WHERE id=$1",
                    UUID(original["id"]),
                    s.execution.encryption.encrypt(changed),
                )
        read = await http.get(path, headers=headers)
        if change == "expire":
            assert read.status_code == 200
            assert read.json()["messages"][-1] == original
        else:
            assert read.status_code == 409
            assert (
                await start(http, headers, "Which other instruments are available?")
            ).status_code == 409
        assert s.source_calls == reads and len(s.calls) == 3 and await ledger(s) == charges
