"""One owned-index original: native holds, sequential pins and exact saved readback."""

import asyncio
import gzip
from copy import copy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from orchestrator.auth import _verify_access_token
from orchestrator.services.fetch.extract import html_to_markdown
from orchestrator.services.restore_history.model import HistoryError, SendPossible
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.services.restore_history.terminal_projection import InboxSourcesHistoryProjection
from orchestrator.services.restore_history.text_execution import HistorySourcesExecution
from tests.history_inbox_two_jobs_browser_postgres import effect_snapshot, originals
from tests.restore_history_support import KEY
from tests.test_history_composition_postgres import deadline
from tests.test_history_inbox_research_postgres import client, facts, start, status
from tests.test_history_text_execution_postgres import (
    db as db,
    device,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)

QUESTION = (
    "For a standard 34-page adult UK passport, what are the online and paper fees, "
    "how long does it usually take, and when does that clock start? Do the two official "
    "pages agree about the extra paper cost?"
)
URLS = [
    "https://www.gov.uk/passport-fees",
    "https://www.gov.uk/government/organisations/hm-passport-office/about/about-our-services",
]
WINDOWS = {
    URLS[0]: "97996fd316f3c246a2cb14523e746cbabf7579b929a573513ee1a13fcba4c2c5",
    URLS[1]: "eddc1ce8f75a1ca20f326a5ef87218e3759ac0ae42727288554988a3f58a0ef5",
}


def test_owned_captures_support_the_preregistered_question():
    root = Path(__file__).parent / "fixtures" / "uk_passport"
    windows = []
    for filename, url in zip(("fees", "service"), URLS, strict=True):
        raw = gzip.decompress((root / f"{filename}.html.gz").read_bytes())
        article = html_to_markdown(raw.decode("utf-8"))
        assert article is not None
        window = article[:1000]
        assert sha256(window.encode()).hexdigest() == WINDOWS[url]
        windows.append(window)
    fees, service = windows
    assert fees[174:214] == "| Apply online | Apply by paper form | |"
    assert fees[229:295] == "| Adult (16 and over) standard 34-page passport | £102 | £115.50 |"
    assert service[138:186] == "You’ll usually get your passport within 3 weeks."
    assert service[604:667] == "The processing time starts from when we receive your documents."
    assert service[772:902].endswith("This costs an extra £16.00.")


async def check_accounting(s, original):
    sends = [
        entry.command.data
        for entry in await facts(s)
        if isinstance(entry.command.data, SendPossible)
    ]
    assert [sent.effect_kind for sent in sends] == [
        "completion",
        "web_search",
        "completion",
        "completion",
    ]
    assert all(sent.task_id == original["id"] and sent.epoch == original["epoch"] for sent in sends)
    scope = sends[0].financial_scope_id
    async with s.db.connection() as conn:
        rows = await conn.fetch(
            "SELECT id,user_id,scope_id,provider,route_id,model,reserved_microusd,actual_microusd,"
            "overage_microusd,status,period_key,charge_kind FROM entitlement_reservations"
        )
        assert len(rows) == 4
        assert len({row["period_key"] for row in rows}) == 1
        by_id = {row["id"]: row for row in rows}
        for sent in sends:
            row = by_id[sent.reservation_id]
            assert row["scope_id"] == scope == sent.financial_scope_id
            assert row["user_id"] == s.account and row["status"] == "settled"
            assert row["overage_microusd"] == 0
            if sent.effect_kind == "web_search":
                assert (row["provider"], row["route_id"], row["model"]) == (
                    "owned",
                    "owned-uk-passport-20261128-v1",
                    None,
                )
                assert row["reserved_microusd"] == row["actual_microusd"] == 0
            else:
                assert row["provider"] == "openrouter"
                assert row["route_id"] == "deepseek-flash-coreweave-fp8"
                assert row["model"] == "openrouter/deepseek/deepseek-v4.1-flash-1017"
                assert 0 < row["actual_microusd"] <= row["reserved_microusd"] <= 14_400
        period = await conn.fetchrow(
            "SELECT spent_microusd,reserved_microusd FROM entitlement_usage_periods "
            "WHERE user_id=$1 AND period_key=$2",
            s.account,
            rows[0]["period_key"],
        )
        assert tuple(period) == (
            sum(row["actual_microusd"] for row in rows if row["charge_kind"] == "plan"),
            0,
        )
        trial = await conn.fetchrow(
            "SELECT trial_consumed_microusd,trial_reserved_microusd "
            "FROM entitlement_accounts WHERE user_id=$1",
            s.account,
        )
        assert tuple(trial) == (
            sum(row["actual_microusd"] for row in rows if row["charge_kind"] == "trial"),
            0,
        )
        assert await conn.fetchval("SELECT count(*) FROM task_attempts") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["owned-index"], indirect=True)
async def test_owned_index_original_settles_and_reopens(text_host):
    s = text_host
    _, desktop = await device(s, s.account)
    _, phone = await device(s, s.account)
    s.release.set()
    async with s.make(), client(s) as http:
        assert type(s.execution) is HistorySourcesExecution
        assert (await start(http, desktop, "What about urgent passports?")).status_code != 200
        assert not s.calls and not (await effect_snapshot(s))[0]
        assert (await start(http, desktop, QUESTION)).status_code == 200
        assert await jobs(s) == ["completed"], s.errors
        (original,) = await originals(s)
        await check_accounting(s, original)
        before = await effect_snapshot(s)
        assert (await status(http, phone, "X"))["can_start"] == []
        (saved_report,) = s.reports
        answer, _ = saved_report.split("\n\nSources:\n\n")
        for claim in (
            "£102 online and £115.50 on paper",
            "difference of £13.50",
            "usually get your passport within 3 weeks",
            "starts from when we receive your documents",
            "an extra £16.00",
            "disagree",
        ):
            assert claim in answer
        report = saved_report.encode()
        for headers in (desktop, phone):
            response = await http.get(
                f"/conversations/{original['conversation_id']}", headers=headers
            )
            assert response.status_code == 200
            message = response.json()["messages"][-1]
            assert message["id"] == str(original["result_message_id"])
            assert message["content"].encode() == report
            lookup, *reads = message["tool_results"]
            assert [item["url"] for item in lookup["result"]["results"]] == URLS
            assert len(reads) == 2
            for result, url in zip(reads, URLS, strict=True):
                page = result["result"]
                assert page["url"] == page["final_url"] == url
                assert (page["start_char"], page["end_char"]) == (0, 1000)
                assert sha256(page["content"].encode()).hexdigest() == WINDOWS[url]
                assert page["snapshot_id"] in message["content"]
        assert (await start(http, phone, QUESTION)).status_code in (409, 503)
        assert (await start(http, phone, "What about urgent passports?")).status_code in (409, 503)
        assert await effect_snapshot(s) == before and len(s.calls) == 3

    s.research_observer = True
    async with s.make(), client(s) as http:
        # Both modes use the same v6 wire profile. Only the reader matching
        # the committed research purpose can expose the original report.
        reader = s.host._reader
        projection = reader._projection
        ordinary = InboxSourcesHistoryProjection(
            s.identity,
            acceptor=projection.acceptor,
            worker=projection.worker,
            publisher=projection.publisher,
        )
        wrong_mode = HistoryObservation(reader._tasks, reader._history, ordinary, reader._key)
        auth = await _verify_access_token(s.w, phone["Authorization"].removeprefix("Bearer "))
        assert auth is not None
        key = original["idempotency_key"]
        assert (await reader.lookup(auth, key, deadline=deadline())).kind == "task"
        refused = await wrong_mode.lookup(auth, key, deadline=deadline())
        assert refused.kind == "recovery" and refused.snapshot is None
        assert await effect_snapshot(s) == before
        path = f"/conversations/{original['conversation_id']}"
        async with s.db.connection() as conn:
            await conn.execute("UPDATE web_snapshots SET expires_at=now()")
        expired = await effect_snapshot(s)
        detail = await http.get(path, headers=phone)
        assert detail.status_code == 200
        assert detail.json()["messages"][-1]["content"].encode() == report
        assert await effect_snapshot(s) == expired
        # Fresh observer must not infer the expected search service from whatever
        # the reservation currently claims, even if no retained page is available.
        async with s.db.connection() as conn:
            changed = await conn.execute(
                "UPDATE entitlement_reservations SET provider='brave',route_id='brave-web-search' "
                "WHERE provider='owned' AND scope_id=$1",
                s.calls[0].financial_scope_id,
            )
            assert changed == "UPDATE 1"
        assert (await http.get(path, headers=phone)).status_code == 409
        assert len(s.calls) == 3 and await effect_snapshot(s) == expired


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["owned-index"], indirect=True)
async def test_source_constructors_reject_crossed_modes_before_admission(text_host):
    s = text_host
    async with s.make():
        host = s.execution
        common = (host.pool, host.encryption, host.settings, host.history)
        kwargs = {"model_transport": host.model_transport, "source_key": KEY}
        owned = host.projection
        ordinary = InboxSourcesHistoryProjection(
            s.identity, acceptor=owned.acceptor, worker=owned.worker, publisher=owned.publisher
        )
        before = await effect_snapshot(s)
        with pytest.raises(HistoryError, match="owned_index_execution_refused"):
            HistorySourcesExecution(*common, owned, **kwargs)
        with pytest.raises(HistoryError, match="owned_index_execution_refused"):
            HistorySourcesExecution.for_owned_index(
                *common, cast(Any, ordinary), captures=s.owned_captures, **kwargs
            )
        assert await effect_snapshot(s) == before and not s.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["owned-index"], indirect=True)
@pytest.mark.parametrize("swap", ["approval", "tool"])
async def test_owned_lookup_requires_original_tool_and_approval(text_host, monkeypatch, swap):
    from orchestrator.services.restore_history import owned_index

    s = text_host
    _, desktop = await device(s, s.account)
    meter = owned_index.metered_tool_call
    substitutions = []

    def substitute(approval, **kwargs):
        if swap == "approval":
            other = replace(approval)
            assert other == approval and other is not approval
            approval = other
        else:
            tool = kwargs["history_tool"]
            kwargs["history_tool"] = copy(tool)
            assert kwargs["history_tool"] is not tool
        substitutions.append(swap)
        return meter(approval, **kwargs)

    monkeypatch.setattr(owned_index, "metered_tool_call", substitute)
    s.release.set()
    async with s.make(), client(s) as http:
        assert (await start(http, desktop, QUESTION)).status_code == 200
        assert await jobs(s) != ["completed"]
        assert substitutions == [swap] and len(s.calls) == 1
        assert not s.search_calls and not s.source_calls and not s.reports
        sends = [
            entry.command.data.effect_kind
            for entry in await facts(s)
            if isinstance(entry.command.data, SendPossible)
        ]
        assert sends == ["completion"]
        assert len((await effect_snapshot(s))[1]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["owned-index"], indirect=True)
async def test_incomplete_owned_synthesis_cannot_start_again(text_host):
    s = text_host
    _, desktop = await device(s, s.account)
    s.finish_reason = "length"
    s.release.set()
    async with s.make(), client(s) as http:
        assert (await start(http, desktop, QUESTION)).status_code == 200
        assert await jobs(s) != ["completed"]
        before = await effect_snapshot(s)
        assert len(s.calls) == 3
        assert len(before[1]) == 4 and len(before[4]) == 2
        assert all(row["kind"] != "TERMINAL_OUTPUT_PREPARE" for row in before[0])
        assert (await start(http, desktop, QUESTION)).status_code in (409, 503)
        assert len(s.calls) == 3 and await effect_snapshot(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["owned-index"], indirect=True)
async def test_owned_index_stop_after_mark_prevents_first_page(text_host, monkeypatch):
    from orchestrator.services.restore_history.source_execution import SourceRun

    s = text_host
    _, desktop = await device(s, s.account)
    _, phone = await device(s, s.account)
    entered = asyncio.Event()
    release = asyncio.Event()
    dispatch = SourceRun.dispatch

    async def delayed(run, request):
        entered.set()
        await release.wait()
        return await dispatch(run, request)

    monkeypatch.setattr(SourceRun, "dispatch", delayed)
    s.release.set()
    async with s.make(), client(s) as http:
        assert (await start(http, desktop, QUESTION)).status_code == 200
        await asyncio.wait_for(entered.wait(), 15)
        try:
            task = s.calls[0].task_id
            assert (await http.post(f"/tasks/{task}/cancel", headers=phone)).status_code == 200
        finally:
            release.set()
        assert await jobs(s) != ["completed"] and len(s.calls) == 2
        state = await effect_snapshot(s)
        assert len(state[1]) == 3 and not state[4] and not state[5]
        assert not s.source_calls and not s.reports


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["owned-index"], indirect=True)
async def test_insufficient_whole_attempt_budget_denies_before_first_send(text_host):
    from orchestrator.entitlements.service import EntitlementService

    s = text_host
    _, desktop = await device(s, s.account)
    s.release.set()
    async with s.make(), client(s) as http:
        period = EntitlementService(s.w).current_period_key()
        async with s.db.connection() as conn:
            await conn.execute(
                "INSERT INTO entitlement_usage_periods(user_id,period_key,spent_microusd) "
                "VALUES($1,$2,206801) ON CONFLICT(user_id,period_key) "
                "DO UPDATE SET spent_microusd=excluded.spent_microusd",
                s.account,
                period,
            )
        # Each individual call would fit; all three worst-case legs miss by 1 µUSD.
        assert (await start(http, desktop, QUESTION)).status_code == 200
        assert await jobs(s) != ["completed"]
        assert not s.calls and not s.search_calls and not s.source_calls
        effects = await effect_snapshot(s)
        assert not effects[1] and not effects[4] and not effects[5]
        assert not any(row["kind"] == "SEND_POSSIBLE" for row in effects[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["owned-index"], indirect=True)
async def test_unknown_original_usage_stops_before_lookup(text_host, monkeypatch):
    from orchestrator import compute_runtime

    s = text_host
    _, desktop = await device(s, s.account)
    s.release.set()
    monkeypatch.setattr(compute_runtime, "_chunk_usage", lambda chunk: None)
    async with s.make(), client(s) as http:
        assert (await start(http, desktop, QUESTION)).status_code == 200
        assert await jobs(s) != ["completed"]
        assert len(s.calls) == 1 and not s.search_calls and not s.source_calls
        async with s.db.connection() as conn:
            (row,) = await conn.fetch(
                "SELECT reserved_microusd,actual_microusd,usage->>'estimated_cost' AS estimated "
                "FROM entitlement_reservations"
            )
            assert row["estimated"] == "true"
            assert row["actual_microusd"] == row["reserved_microusd"] > 0
