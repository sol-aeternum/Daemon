"""Native original source retrieval on separately owned PostgreSQL."""

from uuid import uuid4

import pytest
from httpx import Response

from orchestrator.services.restore_history.model import SourcePinPrepare, SourceReadPossible
from tests.test_history_inbox_research_postgres import facts, ledger, start, status
from tests.source_model_fixture import public_notice
from tests.test_history_inbox_ui_postgres import client
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_text_execution_postgres import (
    db as db,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize("text_host", ["sources"], indirect=True),
]

QUESTION = "What does the field station lend?"
URL = "https://station.example/loans"
OTHER = "https://station.example/other"
PASSAGE = "The station lends a brass tide gauge."


def configure(s):
    s.research_corpus = {
        QUESTION: [
            {"url": URL, "title": "Loans", "description": "Consult the station notice."},
            {"url": OTHER, "title": "Other", "description": "Consult the station notice."},
        ]
    }
    s.source_pages = {
        URL: public_notice(PASSAGE),
        OTHER: public_notice("The loan desk opens at seven."),
    }
    s.release.set()


async def test_native_sources_complete_and_reopen_after_expiration(text_host):
    s = text_host
    configure(s)
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        response = await start(http, headers, QUESTION)
        assert response.status_code == 200, response.text
        assert await jobs(s) == ["completed"], "\n".join(s.errors)
        assert s.source_calls == [URL, OTHER]
        assert len(s.calls) == 3 and len(s.search_calls) == 1
        entries = await facts(s)
        assert len([e for e in entries if isinstance(e.command.data, SourceReadPossible)]) == 2
        assert len([e for e in entries if isinstance(e.command.data, SourcePinPrepare)]) == 2
        assert len(await ledger(s)) == 4
        saved = await status(http, headers, "X")
        assert saved["state"] == "completed"
        conversation = saved["conversation_id"]
        detail = await http.get(f"/conversations/{conversation}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert PASSAGE in detail.json()["messages"][-1]["content"]
        assert detail.json()["messages"][-1]["content"] == s.reports[0]
        async with s.db.connection() as conn:
            await conn.execute("DELETE FROM public.web_snapshots")
        detail = await http.get(f"/conversations/{conversation}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["messages"][-1]["content"] == s.reports[0]
        assert s.source_calls == [URL, OTHER]


@pytest.mark.parametrize("bad", ["duplicate", "foreign", "404", "redirect"])
async def test_source_defensive_selection_and_transport_refusal(text_host, bad):
    s = text_host
    configure(s)
    if bad == "duplicate":
        s.source_selection = [URL, URL]
    elif bad == "foreign":
        s.source_selection = [URL, "https://other.example/not-selected"]
    elif bad == "404":
        s.source_pages.pop(OTHER)
    else:
        s.source_pages[OTHER] = Response(302, headers={"Location": "https://other.example/"})
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTION, str(uuid4()))).status_code == 200
        assert await jobs(s) != ["completed"]
        assert len(s.calls) == 2
        assert s.source_calls == ([] if bad in ("duplicate", "foreign") else [URL, OTHER])
    assert not s.reports


@pytest.mark.parametrize("changed", [False, True])
async def test_pin_requires_the_actual_fresh_snapshot(text_host, monkeypatch, changed):
    """A retained row does not become a new publication through the native hook."""
    from orchestrator.services.web_snapshots import WebSnapshotStore

    s = text_host
    configure(s)
    s.source_selection = [URL]
    create = WebSnapshotStore.create

    async def misplaced_hook(self, user, conversation, **kwargs):
        hook = kwargs.pop("on_created")
        retained = await create(self, user, conversation, **kwargs)

        async def select_retained(conn, native_id):
            assert native_id != retained.id
            if changed:
                await conn.execute("UPDATE public.web_snapshots SET id=id WHERE id=$1", retained.id)
            await hook(conn, retained.id)

        return await create(self, user, conversation, **kwargs, on_created=select_retained)

    monkeypatch.setattr(WebSnapshotStore, "create", misplaced_hook)
    _, headers = await device(s, s.account)
    async with s.make(), client(s) as http:
        assert (await start(http, headers, QUESTION)).status_code == 200
        assert await jobs(s) != ["completed"]
        assert s.source_calls == [URL] and len(s.calls) == 2
        assert (
            s.db.sql("SELECT count(*) FROM task_history_projection WHERE kind='SOURCE_PIN_PREPARE'")
            == "0"
        )
        assert s.db.sql("SELECT count(*) FROM task_events WHERE kind='page_refreshed'") == "0"
        assert s.db.sql("SELECT count(*) FROM public.web_snapshots") == "1"
