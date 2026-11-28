"""Explicit desktop-to-phone two-job proof using real auth and owned transport."""

import asyncio
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests.owned_search_redis import OwnedSearchRedis
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_text_execution_postgres import (
    db as db,
    jobs,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)
from tests.test_history_ui_browser_postgres import actual_servers


async def originals(s):
    async with s.db.connection() as conn:
        rows = await conn.fetch(
            "SELECT t.id,t.idempotency_key,t.conversation_id,t.result_message_id,t.status,"
            "t.attempt_count,t.cancel_requested_at,m.content,a.epoch,a.outcome "
            "FROM tasks t JOIN messages m ON m.id=t.result_message_id "
            "JOIN task_attempts a ON a.task_id=t.id WHERE t.user_id=$1 "
            "ORDER BY t.created_at,t.id",
            s.account,
        )
    assert all(r["attempt_count"] == 1 and r["cancel_requested_at"] is None for r in rows)
    assert len(rows) == len({call.task_id for call in s.calls})
    return [dict(r) for r in rows]


async def effect_snapshot(s):
    """Read-only control: all original effects and accounting in this owned DB."""
    async with s.db.connection() as conn:
        return (
            await conn.fetch(
                "SELECT task_id,seq,kind,digest FROM task_history_projection ORDER BY seq"
            ),
            await conn.fetch(
                "SELECT id,scope_id,period_key,status,actual_microusd,settled_at "
                "FROM entitlement_reservations ORDER BY id"
            ),
            await conn.fetch(
                "SELECT user_id,period_key,spent_microusd,reserved_microusd "
                "FROM entitlement_usage_periods WHERE user_id=$1 ORDER BY period_key",
                s.account,
            ),
            await conn.fetch(
                "SELECT user_id,trial_consumed_microusd,trial_reserved_microusd "
                "FROM entitlement_accounts WHERE user_id=$1",
                s.account,
            ),
            await conn.fetch(
                "SELECT id,conversation_id,content_fingerprint,retrieved_at,expires_at "
                "FROM web_snapshots WHERE user_id=$1 ORDER BY id",
                s.account,
            ),
            await conn.fetch(
                "SELECT task_id,seq,payload_ciphertext FROM task_events "
                "WHERE kind='page_refreshed' ORDER BY task_id,seq"
            ),
        )


def identity(row):
    return {
        "task": str(row["id"]),
        "conversation": str(row["conversation_id"]),
        "message": str(row["result_message_id"]),
    }


def report_for_browser(s, row, *, text=None, sources=None):
    content = s.execution.encryption.decrypt(row["content"])
    if sources is None:
        assert content == text
        return identity(row)
    assert sources and all(url in content for url in sources)
    return {**identity(row), "content": content, "sources": sources}


async def check_research_auth(s, backend, y, receive, send):
    async with s.db.connection() as conn:
        foreign_account = await s.db.account(conn)
    _, foreign = await device(s, foreign_account)
    async with httpx.AsyncClient(base_url=backend, headers=foreign, trust_env=False) as http:
        missing = await http.get("/tasks/inbox-pilot?topic=Y")
        assert missing.status_code == 200
        assert missing.json()["state"] == "missing" and missing.json()["research_questions"] == []
        assert "conversation_id" not in missing.json()
        known = await http.get(f"/conversations/{y['conversation_id']}")
        unknown = await http.get(f"/conversations/{uuid4()}")
        assert known.status_code == unknown.status_code == 404 and known.json() == unknown.json()
    for change in ("expired", "revoked"):
        assert (await receive("auth-check"))["change"] == change
        async with s.db.connection() as conn, conn.transaction():
            if change == "expired":
                await conn.execute(
                    "UPDATE sessions SET access_expires_at=now()-interval '1 minute' WHERE user_id=$1",
                    s.account,
                )
            else:
                # Test revocation independently of expiry using the same phone bearer.
                await conn.execute(
                    "UPDATE sessions SET access_expires_at=now()+interval '1 hour' WHERE user_id=$1",
                    s.account,
                )
                await conn.execute(
                    "UPDATE devices SET revoked_at=now() WHERE user_id=$1", s.account
                )
        await send(change)


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["self-hosted-ui"], indirect=True)
async def test_two_originals_desktop_phone_lost_second_ack(text_host, monkeypatch):
    s = text_host
    s.inbox_pilot = s.inbox_two_jobs = True
    s.synthetic_deltas = ("Synthetic report X.", " First fixture.")
    await exercise_two_originals(s, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["research-self-hosted-ui"], indirect=True)
async def test_research_two_questions_desktop_phone_original_reports(text_host, monkeypatch):
    from tests.test_history_inbox_research_postgres import CORPUS, QUESTIONS

    s = text_host
    s.research_corpus = copy.deepcopy(CORPUS)
    # Different primary sources, plus a common bulletin edited between starts.
    # Both versions reach the model only through a real native search response.
    bulletin = {
        "title": "District field bulletin",
        "url": "https://district.example/bulletin",
        "description": "The field station opens at noon.",
    }
    for q in QUESTIONS:
        s.research_corpus[q].append(bulletin.copy())
    old_passage = bulletin["description"]
    new_passage = "The field station now opens at nine."

    def revise_passage():
        for q in QUESTIONS:
            s.research_corpus[q] = [*CORPUS[q], {**bulletin, "description": new_passage}]

    await exercise_two_originals(
        s,
        monkeypatch,
        questions=QUESTIONS,
        source_urls=[[source["url"] for source in s.research_corpus[q]] for q in QUESTIONS],
        between_questions=revise_passage,
    )
    assert [call.query for call in s.search_calls] == list(QUESTIONS)
    for call, report in zip(s.search_calls, s.reports, strict=True):
        assert all(source["description"] in report for source in call.results)
    assert new_passage in s.reports[1] and old_passage not in s.reports[1]
    assert old_passage in s.reports[0] and new_passage not in s.reports[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["sources-self-hosted-ui"], indirect=True)
async def test_source_passages_survive_changed_bulletin_and_phone_reopen(text_host, monkeypatch):
    s = text_host
    questions = (
        "Which instrument does the estuary station lend, and when does the desk open?",
        "What does the town archive lend for fieldwork, and when does the desk open?",
    )
    water = "https://estuary.example/loans"
    archive = "https://archive.example/fieldwork"
    bulletin = "https://district.example/hours"
    source_urls = [[water, bulletin], [archive, bulletin]]
    water_fact = "The station lends a brass tide gauge."
    archive_fact = "The archive lends a linen map case."
    early = "The loan desk opens at seven."
    late = "The loan desk opens at eleven."

    from tests.source_model_fixture import public_notice

    # Model fixtures can learn the material facts only from retrieved page bodies.
    # In particular the shared search result does not change between originals.
    s.research_corpus = {
        question: [
            {"url": url, "title": "Fieldwork notice", "description": "Consult the notice."}
            for url in urls
        ]
        for question, urls in zip(questions, source_urls, strict=True)
    }
    s.source_pages = {
        water: public_notice(water_fact),
        archive: public_notice(archive_fact),
        bulletin: public_notice(early),
    }
    initial_results = copy.deepcopy(s.research_corpus)

    def change_bulletin():
        s.source_pages[bulletin] = public_notice(late)

    await exercise_two_originals(
        s,
        monkeypatch,
        questions=questions,
        source_urls=source_urls,
        between_questions=change_bulletin,
        retrieved=True,
    )
    assert s.research_corpus == initial_results
    assert [call.query for call in s.search_calls] == list(questions)
    assert list(s.source_calls) == [water, bulletin, archive, bulletin]
    # Use the persisted, independently read messages, not expected transport output.
    x, y = await originals(s)
    first, second = (s.execution.encryption.decrypt(row["content"]) for row in (x, y))
    first_answer = first.split("\n\nSources:\n\n")[0]
    second_answer = second.split("\n\nSources:\n\n")[0]
    assert water_fact in first_answer and early in first_answer and late not in first
    assert archive_fact in second_answer and late in second_answer and early not in second
    async with s.db.connection() as conn:
        for original, expected_pages in (
            (x, {water: water_fact, bulletin: early}),
            (y, {archive: archive_fact, bulletin: late}),
        ):
            pages = await conn.fetch(
                "SELECT id,metadata_encrypted,content_encrypted FROM web_snapshots "
                "WHERE user_id=$1 AND conversation_id=$2",
                s.account,
                original["conversation_id"],
            )
            pins = await conn.fetch(
                "SELECT payload_ciphertext FROM task_events "
                "WHERE task_id=$1 AND kind='page_refreshed'",
                original["id"],
            )
            decrypt = s.execution.encryption.decrypt
            pinned = [json.loads(decrypt(p["payload_ciphertext"])) for p in pins]
            assert {p["snapshot_id"] for p in pinned} == {str(p["id"]) for p in pages}
            assert all(p["epoch"] == original["epoch"] for p in pinned)
            assert len(pages) == len(expected_pages)
            actual_pages = {
                json.loads(decrypt(p["metadata_encrypted"]))["final_url"]: decrypt(
                    p["content_encrypted"]
                )
                for p in pages
            }
            assert actual_pages.keys() == expected_pages.keys()
            for url, passage in expected_pages.items():
                assert passage in actual_pages[url]
    # Retention is independent of the committed report's lifetime. Remove only
    # the first original's retained pages via their normal owned store API;
    # a fresh reader must never consult transport to fill them back in.
    from orchestrator.services.restore_history.acceptance_host import create_isolated_ui_app
    from orchestrator.services.web_snapshots import WebSnapshotStore

    _, credentials = await device(s, s.account)
    s.research_observer = True
    async with s.make():
        store = WebSnapshotStore(s.w, s.execution.encryption, s.settings)
        retained = await store.list(s.account, x["conversation_id"])
        assert len(retained.items) == 2
        for page in retained.items:
            assert await store.delete(s.account, x["conversation_id"], page.id)
        state = await effect_snapshot(s)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_isolated_ui_app(s.host)),
            base_url="http://isolated",
            headers=credentials,
        ) as http:
            selected = await http.get("/tasks/inbox-pilot?topic=X")
            assert selected.status_code == 200
            assert selected.json()["conversation_id"] == str(x["conversation_id"])
            detail = await http.get(f"/conversations/{x['conversation_id']}")
            assert detail.status_code == 200
            message = detail.json()["messages"][-1]
            assert (message["id"], message["content"]) == (str(x["result_message_id"]), first)
        assert await effect_snapshot(s) == state
        assert list(s.source_calls) == [water, bulletin, archive, bulletin]


async def exercise_two_originals(
    s, monkeypatch, *, questions=None, source_urls=None, between_questions=None, retrieved=False
):
    owner = OwnedSearchRedis(Path(os.environ["OPENCODE_WORK_TEST_DIR"]), "inbox-761-two")
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    with (evidence / f"inbox-two-browser-{uuid4().hex}.jsonl").open("xb") as log:
        redis = child = None
        try:
            owner.start()
            redis = await owner.connect()
            async with s.make(), actual_servers(s, monkeypatch, redis) as (packet, environment):
                child = await asyncio.create_subprocess_exec(
                    "node",
                    "tests/history_inbox_two_jobs_browser.mjs",
                    cwd=Path(__file__).resolve().parents[1],
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=log,
                )
                commands, receipts = child.stdin, child.stdout
                assert commands is not None and receipts is not None

                async def send(value):
                    commands.write(value.encode() + b"\n")
                    await commands.drain()

                async def receive(stage, timeout=150):
                    async with asyncio.timeout(timeout):
                        while True:
                            line = await receipts.readline()
                            assert line, f"Browser stopped before {stage}: see {log.name}"
                            log.write(line)
                            log.flush()
                            received = json.loads(line)
                            if received["stage"] != "diagnostic":
                                break
                    assert received["stage"] == stage
                    return received

                await send(json.dumps({**packet, "questions": questions, "retrieved": retrieved}))
                first = await receive("first")
                await asyncio.wait_for(s.entered.wait(), 10)
                s.release.set()
                assert await jobs(s) == ["completed"], s.errors
                (x,) = await originals(s)
                assert x["idempotency_key"] == first["key"]
                first_report = report_for_browser(
                    s,
                    x,
                    text="Synthetic report X. First fixture.",
                    sources=source_urls[0] if source_urls else None,
                )
                if questions and len(questions) == 1:
                    from tests.test_owned_index_postgres import check_accounting

                    await check_accounting(s, x)
                    before_reads = await effect_snapshot(s)
                    await send(json.dumps(first_report))
                    assert await receive("finished") == {
                        "stage": "finished",
                        "starts": 1,
                        "x": str(x["id"]),
                    }
                    await asyncio.wait_for(child.wait(), 20)
                    assert child.returncode == 0 and not s.errors
                    assert await effect_snapshot(s) == before_reads
                    assert await originals(s) == [x] and len(s.calls) == 3
                    async with s.db.connection() as conn:
                        assert (
                            await conn.fetchval(
                                "SELECT count(*) FROM devices WHERE user_id=$1", s.account
                            )
                            == 2
                        )
                    return
                s.release.clear()
                s.entered.clear()
                s.closed.clear()
                if not questions:
                    s.synthetic_deltas = ("Synthetic report Y.", " Second fixture.")
                if between_questions is not None:
                    between_questions()
                await send(json.dumps(first_report))
                second = await receive("second")
                assert second["lost"] == 1
                await asyncio.wait_for(s.entered.wait(), 10)
                actual_x, y = await originals(s)
                assert actual_x == x
                assert y["idempotency_key"] == second["key"] and y["status"] == "running"
                assert y["id"] != x["id"] and y["conversation_id"] != x["conversation_id"]
                running_effects = await effect_snapshot(s)
                await send(json.dumps(identity(y)))
                phone = await receive("phone")
                assert phone["starts"] == 2 and phone["refreshed"]
                assert await effect_snapshot(s) == running_effects
                after_x, after_y = await originals(s)
                assert after_x == x
                assert (identity(after_y), after_y["epoch"], after_y["status"]) == (
                    identity(y),
                    y["epoch"],
                    "running",
                )
                async with s.db.connection() as conn:
                    assert (
                        await conn.fetchval(
                            "SELECT count(*) FROM devices WHERE user_id=$1", s.account
                        )
                        == 2
                    )
                    assert await conn.fetchval("SELECT count(*) FROM tasks") == 2
                    assert await conn.fetchval("SELECT count(*) FROM task_attempts") == 2
                s.release.set()
                assert await jobs(s) == ["completed", "completed"], s.errors
                actual_x, actual_y = await originals(s)
                assert actual_x == x and identity(actual_y) == identity(y)
                assert actual_y["epoch"] == y["epoch"] and actual_y["status"] == "completed"
                second_report = report_for_browser(
                    s,
                    actual_y,
                    text="Synthetic report Y. Second fixture.",
                    sources=source_urls[1] if source_urls else None,
                )
                terminal_effects = await effect_snapshot(s)
                await send("second committed")
                if questions:
                    assert len(s.calls) == (6 if retrieved else 4)
                    assert len(terminal_effects[1]) == (8 if retrieved else 6)
                    assert all(r["status"] == "settled" for r in terminal_effects[1])
                    if retrieved:
                        assert len(terminal_effects[4]) == len(terminal_effects[5]) == 4
                        for row in (x, actual_y):
                            kinds = [
                                f["kind"] for f in terminal_effects[0] if f["task_id"] == row["id"]
                            ]
                            assert kinds.count("SOURCE_READ_POSSIBLE") == 2
                            assert kinds.count("SOURCE_PIN_PREPARE") == 2
                    await send(json.dumps(second_report))
                    await check_research_auth(s, packet["backend"], actual_y, receive, send)
                finished = await receive("finished")
                assert finished["starts"] == 2 and finished["lost"] == 1
                assert (finished["x"], finished["y"]) == (str(x["id"]), str(y["id"]))
                await asyncio.wait_for(child.wait(), 20)
                assert child.returncode == 0 and not s.errors
                assert await originals(s) == [x, actual_y]
                assert await effect_snapshot(s) == terminal_effects
        finally:
            if child is not None and child.returncode is None:
                child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 15)
                except TimeoutError:
                    child.kill()
                    await child.wait()
                    raise AssertionError("Owned two-job browser did not shut down") from None
            if redis is not None:
                await redis.aclose()
            owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["owned-index-self-hosted-ui"], indirect=True)
async def test_owned_index_original_reopens_on_independently_enrolled_browser(
    text_host, monkeypatch
):
    from tests.test_owned_index_postgres import QUESTION, URLS

    await exercise_two_originals(
        text_host, monkeypatch, questions=[QUESTION], source_urls=[URLS], retrieved=True
    )
