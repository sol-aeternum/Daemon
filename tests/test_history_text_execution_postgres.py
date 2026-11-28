"""Actual authenticated host -> original runner/stream/compute; synthetic transport."""

import asyncio
import traceback
import sys
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient, MockTransport, ReadError, Response
import pytest
import pytest_asyncio

from orchestrator import compute_runtime as runtime
from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.models import ChatRequest
from orchestrator.services.deletion_journal.store import initialize
from orchestrator.services.restore_history.acceptance_host import (
    IsolatedAcceptanceHost,
    create_isolated_acceptance_app,
    create_isolated_ui_app,
)
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.model import (
    NonsuccessOutputPrepare,
    SendPossible,
    TerminalOutputPrepare,
)
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.services.restore_history.terminal_projection import (
    InboxRetiredHistoryProjection,
    InboxResearchHistoryProjection,
    TerminalHistoryProjection,
    TerminalV4HistoryProjection,
)
from orchestrator.services.restore_history.text_execution import (
    HistoryResearchExecution,
    HistoryTextExecution,
)
from orchestrator.tasks.store import TaskStore
from orchestrator.tasks import runner
from orchestrator.turn_finish import TurnFinish
from orchestrator.auth_tokens import hash_token
from orchestrator.entitlements.service import EntitlementService
from tests.restore_history_support import KEY
from tests.test_compute_runtime import _qualified_policy, _route
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_acceptance_host_postgres import install_terminal, post
from tests.test_history_composition_postgres import deadline
from tests.test_history_effects_postgres import install
from tests.test_history_observation_boundary_postgres import device
from tests.test_restore_history_store import owned


@pytest_asyncio.fixture
async def text_host(projection_db, tmp_path, monkeypatch, request):
    database = projection_db
    variant = getattr(request, "param", None)
    restart = variant == "managed-restart"
    research = variant in ("research", "research-self-hosted-ui")
    if restart:
        # Only the create-new launcher fixture supplies this directory.
        tmp_path = database.cluster.resource
        from tests.test_inbox_retired_projection_postgres import install_retired

        install_retired(database)
    else:
        install(database)
        install_terminal(database)
    v4 = getattr(request, "param", None) == "terminal-v4"
    if v4:
        from tests.test_terminal_v4_postgres import SQL

        database.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    # The shared inference fixture installs memory revisions and grants W's exact
    # map before this composition; do not reapply migration 066 or add grants.
    journal = tmp_path / "j1"
    journal_id = uuid4()
    initialize(journal, journal_id)
    async with database.connection() as conn:
        if restart or variant in ("self-hosted-ui", "research-self-hosted-ui"):
            from orchestrator.routes.auth_setup import SINGLETON_USER_ID

            # Initial disposable fixture data only, never runtime identity repair.
            account = SINGLETON_USER_ID
            await conn.execute(
                "INSERT INTO users(id,username) VALUES($1,'synthetic-ui') ON CONFLICT(id) DO NOTHING",
                account,
            )
            await conn.execute(
                "INSERT INTO entitlement_accounts(user_id) VALUES($1) ON CONFLICT(user_id) DO NOTHING",
                account,
            )
        else:
            account = await database.account(conn)
        await conn.execute("INSERT INTO deletion_journal_binding VALUES(true,$1)", journal_id)
    encryption_key = Fernet.generate_key().decode()
    enc = ContentEncryption(encryption_key)
    settings = cast(Any, Settings)(
        _env_file=None,
        durable_chat_enabled=True,
        mock_llm=False,
        daemon_encryption_key=encryption_key,
        daemon_task_acceptor_role="md_acceptor",
        daemon_task_worker_role="md_app",
        daemon_deletion_journal_dir=str(journal),
        daemon_deletion_journal_id=str(journal_id),
        openrouter_api_key="synthetic-no-network",
    )
    route = _route(input_price=1000)
    _qualified_policy(monkeypatch, route=route)
    if research:
        from orchestrator.entitlements.policy import parse_inference_policy
        from tests.test_metered_web_search import _approved_document

        previous = runtime.load_inference_policy()
        services = parse_inference_policy(_approved_document())
        policy = SimpleNamespace(
            routes=previous.routes,
            requirements=services.requirements,
            tool_service=services.tool_service,
        )
        monkeypatch.setattr(runtime, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    path = tmp_path / "h"
    if restart:
        from tests.restore_history_support import initialize_test_directory
        from orchestrator.services.restore_history.store import Ownership

        identity = initialize_test_directory(path, history_id=database.cluster.inbox_history_id)
        ownership = Ownership(os.geteuid(), frozenset({os.geteuid()}), os.getegid())
    else:
        identity, ownership = owned(path)
    state = SimpleNamespace(
        db=database,
        account=account,
        path=path,
        identity=identity,
        ownership=ownership,
        calls=[],
        entered=asyncio.Event(),
        release=asyncio.Event(),
        closed=asyncio.Event(),
        unsolicited=False,
        errors=[],
        encryption_key=encryption_key,
        journal=journal,
        journal_id=journal_id,
        settings=settings,
        finish_reason="stop",
        research_corpus={},
        search_calls=[],
        model_requests=[],
        reports=[],
        search_entered=asyncio.Event(),
        search_release=asyncio.Event(),
        search_closed=asyncio.Event(),
    )
    state.search_release.set()
    from orchestrator import daemon

    monkeypatch.setattr(
        daemon.logger,
        "error",
        lambda *a, **kw: state.errors.append("".join(traceback.format_exception(sys.exception()))),
    )

    # Raw OpenRouter provenance is qualified in the transport fixture. In this
    # runner fixture provide only the eventual result of that parser; the stub
    # SDK chunk's normalized finish_reason is deliberately not used as proof.
    observations = {}
    monkeypatch.setattr(runtime.inference_transport, "observe_stream_finish", observations.get)

    async def provider(**kwargs):
        scope = runtime.current_scope()
        identity = scope.inference_identity
        assert scope.history_binding is not None
        state.calls.append(identity)
        assert kwargs["num_retries"] == 0 and not kwargs.get("tools")
        assert scope.history_binding.effects is state.execution.effects
        operation = state.execution.history._operation
        # An independent heartbeat may already own its own short H operation.
        # The original mark owner must be gone, and this transport independently
        # acquires H below (would deadlock if H enclosed the provider call).
        assert (
            operation is None
            or operation._task.get_coro().__qualname__ == "HistoryEffects._heartbeat"
        )
        async with database.connection() as conn:
            if getattr(state, "inbox_two_jobs", False):
                assert identity is not None
                assert (
                    await conn.fetchval(
                        "SELECT may_have_dispatched FROM task_inference_recovery WHERE task_id=$1",
                        identity.task_id,
                    )
                    is True
                )
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM entitlement_reservations WHERE scope_id=$1 AND status='open'",
                        identity.financial_scope_id,
                    )
                    == 1
                )
                assert await conn.fetchval(
                    "SELECT count(*) FROM compute_reservation_metadata"
                ) == len(state.calls)
            else:
                assert (
                    await conn.fetchval("SELECT may_have_dispatched FROM task_inference_recovery")
                    is True
                )
                assert await conn.fetchval("SELECT count(*) FROM compute_reservation_metadata") == 1
                assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 2
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM entitlement_reservations WHERE status='open'"
                )
                == 1
            )
        async with state.execution.history.operation(deadline=deadline()) as operation:
            entries = (await operation.verify()).entries
            assert type(entries[-1].command.data) is SendPossible
            if getattr(state, "inbox_two_jobs", False):
                assert identity is not None
                assert entries[-1].command.data.task_id == identity.task_id
        hook = getattr(state, "provider_check", None)
        if hook is not None:
            await hook(scope)
        state.entered.set()

        raw_finish = SimpleNamespace(result={"state": "missing"})

        async def stream():
            try:
                if state.unsolicited:
                    yield {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "bad",
                                            "function": {"name": "web_search", "arguments": "{}"},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                else:
                    first, last = getattr(
                        state, "synthetic_deltas", ("original partial", " and final")
                    )
                    yield {"choices": [{"delta": {"content": first}}]}
                    # The original engine persists this first delta before waiting.
                    await state.release.wait()
                    yield {"choices": [{"delta": {"content": last}}]}
                yield {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
                if state.finish_reason is not None:
                    raw_finish.result = {
                        "state": "confirmed",
                        "reason": state.finish_reason,
                        "provenance": "provider_wire",
                    }
            finally:
                state.closed.set()

        response = stream()
        observations[response] = raw_finish
        return response

    async def assert_research_mark(kind):
        scope = runtime.current_scope()
        identity = scope.inference_identity
        assert scope.history_binding is not None
        assert identity is not None and scope.history_binding.effects is state.execution.effects
        async with state.execution.history.operation(deadline=deadline()) as operation:
            entries = (await operation.verify()).entries
            sends = [
                e.command.data
                for e in entries
                if type(e.command.data) is SendPossible
                and e.command.data.task_id == identity.task_id
            ]
            assert sends[-1].effect_kind == kind
        async with database.connection() as conn:
            mark = sends[-1]
            assert await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM compute_reservation_metadata m "
                "JOIN entitlement_reservations r ON r.id=m.reservation_id "
                "WHERE r.id=$1 AND m.runtime_identity=$2 AND m.dispatch_nonce=$3 "
                "AND r.scope_id=$4 AND r.status='open')",
                mark.reservation_id,
                mark.runtime_identity,
                mark.dispatch_nonce,
                identity.financial_scope_id,
            )
        return identity

    async def research_search(wire):
        identity = await assert_research_mark("web_search")
        assert wire.url.host == "api.search.brave.com" and wire.method == "GET"
        query = wire.url.params["q"]
        entries = state.research_corpus.get(query, [])
        state.search_calls.append(SimpleNamespace(query=query, results=entries, identity=identity))
        state.search_entered.set()
        try:
            await state.search_release.wait()
            if getattr(state, "search_failure", False):
                return Response(503)
            return Response(200, json={"web": {"results": entries}})
        finally:
            state.search_closed.set()

    async def research_provider(**kwargs):
        from orchestrator.tools.completion import _unwrap_tool_result

        identity = await assert_research_mark("completion")
        state.calls.append(identity)
        assert kwargs["num_retries"] == 0
        messages = kwargs["messages"]
        tools = kwargs.get("tools")
        state.model_requests.append(
            SimpleNamespace(messages=messages, tools=tools, identity=identity)
        )
        first = bool(tools)
        if getattr(state, "model_failure_leg", None) == ("first" if first else "synthesis"):
            raise ConnectionError("synthetic model transport failure")
        raw_finish = SimpleNamespace(result={"state": "missing"})

        async def stream():
            try:
                if first:
                    assert tools is not None
                    assert [t["function"]["name"] for t in tools] == ["web_search"]
                    state.entered.set()
                    await state.release.wait()
                    question = next(m["content"] for m in messages if m["role"] == "user")
                    query = question[:400]
                    tool_calls = [
                        {
                            "index": 0,
                            "id": "original-search",
                            "function": {
                                "name": "web_search",
                                "arguments": json.dumps({"query": query}),
                            },
                        }
                    ]
                    if state.unsolicited:
                        tool_calls.append(
                            {
                                "index": 1,
                                "id": "extra-search",
                                "function": {
                                    "name": "web_search",
                                    "arguments": json.dumps({"query": query}),
                                },
                            }
                        )
                    yield {"choices": [{"delta": {"tool_calls": tool_calls}}]}
                    reason = "tool_calls"
                else:
                    evidence = next(m["content"] for m in messages if m["role"] == "tool")
                    results = json.loads(_unwrap_tool_result(evidence))["results"]
                    report = (
                        "\n\n".join(
                            f"{item['title']}: {item['description']} [{item['title']}]({item['url']})"
                            for item in results
                        )
                        or "No matching local corpus evidence."
                    )
                    state.reports.append(report)
                    yield {"choices": [{"delta": {"content": report}}]}
                    reason = state.finish_reason
                yield {
                    "choices": [{"delta": {}, "finish_reason": reason}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                }
                if reason is not None:
                    raw_finish.result = {
                        "state": "confirmed",
                        "reason": reason,
                        "provenance": "provider_wire",
                    }
            finally:
                state.closed.set()

        response = stream()
        observations[response] = raw_finish
        return response

    monkeypatch.setattr(runtime.litellm, "acompletion", research_provider if research else provider)
    default_pacer = None
    if research:
        from orchestrator.services.search_pacing import SearchPacer

        redis_owner = request.getfixturevalue("owned_search_redis")
        default_pacer = SearchPacer(
            client_factory=redis_owner.connect, namespace="research:" + uuid4().hex
        )
        state.research_pacer = default_pacer

    @asynccontextmanager
    async def make():
        async with database.pool() as w:
            a = await asyncpg.create_pool(
                host=str(database.host),
                user="md_acceptor",
                password="",
                database=database.name,
                ssl=False,
                min_size=0,
                max_size=2,
            )
            projection_type = TerminalV4HistoryProjection if v4 else TerminalHistoryProjection
            if restart:
                projection_type = InboxRetiredHistoryProjection
            if research:
                projection_type = InboxResearchHistoryProjection
            projection = projection_type(
                identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
            )
            history = HistoryCoordinator(path, identity, ownership, profile=projection.profile)
            reader_history = HistoryCoordinator(
                path, identity, ownership, profile=projection.profile
            )
            worker_history = HistoryCoordinator(
                path, identity, ownership, profile=projection.profile
            )
            tasks = TaskStore(a, enc, MemoryStore(a, enc), cover_new_inference=True)
            reader = HistoryObservation(
                TaskStore(w, enc, MemoryStore(w, enc)), reader_history, projection, KEY
            )
            execution = (
                HistoryResearchExecution(
                    w,
                    enc,
                    settings,
                    worker_history,
                    cast(InboxResearchHistoryProjection, projection),
                    model_transport=research_provider,
                    search_transport=MockTransport(research_search),
                    search_pacer=state.research_pacer,
                )
                if research
                else HistoryTextExecution(w, enc, settings, worker_history, projection)
            )
            host = IsolatedAcceptanceHost(
                HistoryAcceptance(tasks, history, projection, KEY),
                reader,
                fingerprint_key="synthetic-pepper",
                max_body_bytes=4096,
                keepalive_s=1,
                execution=None
                if restart or getattr(state, "research_observer", False)
                else execution,
                inbox_pilot=restart or research or getattr(state, "inbox_pilot", False),
                inbox_two_jobs=restart or research or getattr(state, "inbox_two_jobs", False),
            )
            state.execution = execution
            state.host, state.app, state.w = host, create_isolated_acceptance_app(host), w
            try:
                await host.open(deadline=deadline())
                if restart or getattr(state, "research_observer", False):
                    assert not execution._started
                    yield state
                    return
                assert execution.effects is not None
                native_run = execution.effects._run

                async def observed_run(call, limit):
                    try:
                        return await native_run(call, limit)
                    except BaseException:
                        state.errors.append("".join(traceback.format_exception(sys.exception())))
                        raise

                monkeypatch.setattr(execution.effects, "_run", observed_run)
                yield state
            finally:
                state.release.set()
                state.search_release.set()
                try:
                    await host.aclose(deadline=deadline())
                except TimeoutError:
                    if not getattr(state, "expected_shutdown_timeout", False):
                        raise
                    # Test-owned cleanup only after the retained original shutdown
                    # has physically finished. No owner renewal or execution retry.
                    assert host._shutdown_task is not None
                    assert host._shutdown_task.done() and execution._closed
                    assert all(child.done() for child in execution._jobs)
                    assert reader_history._closed and history._closed
                finally:
                    await a.close()

    state.make = make
    try:
        yield state
    finally:
        if default_pacer is not None:
            await default_pacer.aclose()


async def submitted(s, headers, **kwargs):
    return await s.host.submit(
        headers["Authorization"],
        "stable",
        ChatRequest(message="synthetic text", **kwargs),
        deadline=deadline(),
    )


async def jobs(s):
    results = await asyncio.wait_for(asyncio.gather(*s.execution._jobs), 10)
    assert results, "no original runner was scheduled"
    return results


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["terminal-v4"], indirect=True)
@pytest.mark.parametrize(
    "reason,status,finish_outcome",
    [
        ("stop", "completed", "complete"),
        ("length", "failed", "incomplete"),
        (None, "needs_attention", "unknown"),
    ],
)
async def test_v4_actual_runner_by_key_and_isolated_ui_agree(
    text_host, reason, status, finish_outcome
):
    s = text_host
    s.finish_reason = reason
    _, headers = await device(s, s.account)
    async with s.make():
        accepted = await submitted(s, headers, model="auto")
        await asyncio.wait_for(s.entered.wait(), 5)
        s.release.set()
        if await jobs(s) != [status]:
            pytest.fail("\n".join(s.errors))
        task_id, conversation = accepted.task.task_id, accepted.task.conversation_id
        async with AsyncClient(
            transport=ASGITransport(create_isolated_ui_app(s.host)), base_url="http://fixture.test"
        ) as http:
            by_key = await http.get("/tasks/by-key/stable", headers=headers)
            assert by_key.status_code == 200, by_key.text
            task = by_key.json()
            assert (task["status"], task["content"]) == (status, "original partial and final")
            assert task["turn_finish"]["outcome"] == finish_outcome
            response = await http.get(f"/conversations/{conversation}", headers=headers)
            assert response.status_code == 200, response.text
            assistant = response.json()["messages"][-1]
            assert assistant["content"] == task["content"]
            assert assistant["metadata"]["turn_finish"] == TurnFinish.model_validate(
                task["turn_finish"]
            ).model_dump(exclude_none=True)
            if status == "needs_attention":
                assert assistant["metadata"]["uncertain_tools"] == []
            if status == "completed":
                s.db.sql("UPDATE tasks SET lease_epoch=lease_epoch+1;")
            else:
                s.db.sql("UPDATE tasks SET terminal_code='other';")
            for url in (
                "/tasks/by-key/stable",
                f"/tasks/{task_id}",
                f"/conversations/{conversation}",
            ):
                refused = await http.get(url, headers=headers)
                assert refused.status_code == 409, refused.text
                assert refused.json()["detail"]["retry_safe"] is False
        assert len(s.calls) == 1
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations WHERE status='open'") == "0"
        async with s.execution.history.operation(deadline=deadline()) as op:
            data = (await op.verify()).entries[-1].command.data
        assert type(data) is (
            TerminalOutputPrepare if status == "completed" else NonsuccessOutputPrepare
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["terminal-v4"], indirect=True)
async def test_v4_append_without_sql_never_looks_running_or_unknown(text_host, monkeypatch):
    s = text_host
    s.finish_reason = "length"
    _, headers = await device(s, s.account)
    async with s.make():
        stages = 0

        async def refuse_after_append(*args, **kwargs):
            nonlocal stages
            stages += 1
            raise OSError("synthetic missing projection")

        monkeypatch.setattr(s.execution.projection, "stage_terminal", refuse_after_append)
        accepted = await submitted(s, headers, model="auto")
        await asyncio.wait_for(s.entered.wait(), 5)
        s.release.set()
        await jobs(s)
        assert stages == 1 and len(s.calls) == 1
        assert s.execution.effects is not None and s.execution.effects._blocked
        assert s.db.sql("SELECT status FROM tasks") == "running"
        async with AsyncClient(
            transport=ASGITransport(create_isolated_ui_app(s.host)), base_url="http://fixture.test"
        ) as http:
            for url in ("/tasks/by-key/stable", f"/tasks/{accepted.task.task_id}"):
                refused = await http.get(url, headers=headers)
                assert refused.status_code == 409, refused.text
                assert refused.json()["detail"]["retry_safe"] is False


@pytest.mark.asyncio
async def test_original_stream_partial_final_second_device_and_disconnect(text_host):
    s = text_host
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make():
        response = await post(s.app, first, {"message": "synthetic text"})
        assert response.status == 200 and b"event: token" not in response.body
        await asyncio.wait_for(s.entered.wait(), 5)
        await asyncio.sleep(0.2)
        assert not s.errors, s.errors
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            async with asyncio.timeout(5):
                while True:
                    found = await http.get("/tasks/by-key/stable", headers=second)
                    assert found.status_code == 200
                    if found.json()["content"]:
                        break
                    await asyncio.sleep(0.02)
            assert found.json()["content"] == "original partial"
            task_id = found.json()["id"]
            assert task_id == response.headers[b"x-daemon-task-id"].decode()
            s.release.set()
            assert await jobs(s) == ["completed"]
            final = await http.get("/tasks/by-key/stable", headers=second)
            assert final.status_code == 200 and final.json()["status"] == "completed"
            assert final.json()["content"] == "original partial and final"
            assert final.json()["id"] == task_id and len(s.calls) == 1
        async with s.execution.history.operation(deadline=deadline()) as operation:
            entries = (await operation.verify()).entries
            assert type(entries[-1].command.data) is TerminalOutputPrepare


@pytest.mark.asyncio
async def test_confirmed_response_lost_recovers_running_original_by_key(text_host):
    s = text_host
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    key = str(uuid4())
    committed = []

    async def lose_response(scope, receive, send):
        disconnected = asyncio.Event()
        body_complete = False

        async def incoming():
            nonlocal body_complete
            if not body_complete:
                message = await receive()
                body_complete = not message.get("more_body", False)
                return message
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def drop(message):
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                committed.append(dict(message["headers"])[b"x-daemon-task-id"].decode())
                disconnected.set()

        await s.app(scope, incoming, drop)
        raise ReadError("confirmed response did not reach the client")

    async with s.make():
        async with AsyncClient(
            transport=ASGITransport(lose_response), base_url="http://fixture.test"
        ) as client:
            with pytest.raises(ReadError):
                async with asyncio.timeout(10):
                    await client.post(
                        "/chat",
                        headers={**first, "Idempotency-Key": key},
                        json={"message": "synthetic text"},
                    )
        await asyncio.wait_for(s.entered.wait(), 5)  # synthetic dispatch is already in flight
        assert len(committed) == 1
        async with s.db.connection() as observer:
            (row,) = await observer.fetch(
                "SELECT id,conversation_id FROM tasks WHERE user_id=$1 AND idempotency_key=$2",
                s.account,
                key,
            )
        assert str(row["id"]) == committed[0]
        async with AsyncClient(
            transport=ASGITransport(s.app), base_url="http://fixture.test"
        ) as reopened:
            # Recovery retries the read by the original key; it needs no receipt or task ID.
            for _ in range(2):
                observed = await reopened.get(f"/tasks/by-key/{key}", headers=second)
                assert observed.status_code == 200, observed.text
                result = observed.json()
                assert (result["id"], result["conversation_id"], result["status"]) == (
                    str(row["id"]),
                    str(row["conversation_id"]),
                    "running",
                )
        s.release.set()
        assert await jobs(s) == ["completed"]
        assert len(s.calls) == 1
        assert s.db.sql("SELECT count(*) FROM tasks") == "1"
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"


@pytest.mark.asyncio
async def test_unsolicited_tool_call_never_executes_or_dispatches_again(text_host):
    s = text_host
    _, headers = await device(s, s.account)
    s.unsolicited = True
    async with s.make():
        await submitted(s, headers)
        assert await jobs(s) == ["needs_attention"], s.errors
        assert len(s.calls) == 1 and s.closed.is_set()
        assert s.db.sql("SELECT count(*) FROM task_operations") == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"model": "explicit"},
        {"provider": "openrouter"},
        {"metadata": {"tools": True}},
        {"research_effort": "research"},
        {"messages": [{"role": "user", "content": "text", "model": "explicit"}]},
    ],
)
async def test_unsupported_choices_reject_before_acceptance(text_host, options):
    from fastapi import HTTPException

    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        with pytest.raises(HTTPException):
            await submitted(s, headers, **options)
        assert s.db.sql("SELECT count(*) FROM tasks") == "0" and not s.calls


@pytest.mark.asyncio
async def test_retired_council_config_is_not_accepted_or_scheduled(text_host):
    from fastapi import HTTPException

    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        with pytest.raises(HTTPException) as refused:
            await submitted(
                s,
                headers,
                messages=[{"role": "user", "content": "/council config: preset=deep"}],
            )
        assert refused.value.status_code == 410
        assert refused.value.detail == {"message": "Council has been retired."}
        assert s.db.sql("SELECT count(*) FROM tasks") == "0"
        assert not s.execution._jobs and not s.calls


@pytest.mark.asyncio
async def test_legacy_accepted_council_text_never_reaches_transport(text_host, monkeypatch):
    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        # Simulate the old admission check only; schedule the original worker
        # through the real acceptance/claim/terminal path.
        monkeypatch.setattr(
            "orchestrator.services.restore_history.acceptance_host.is_retired_council_command",
            lambda _: False,
        )
        await submitted(
            s, headers, messages=[{"role": "user", "content": "/council --default old request"}]
        )
        assert await jobs(s) == ["failed"], s.errors
        assert s.db.sql("SELECT terminal_code FROM tasks") == "council_retired"
        assert not s.calls
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "0"
        assert s.db.sql("SELECT count(*) FROM task_operations") == "0"


@pytest.mark.asyncio
async def test_stop_during_original_stream_closes_local_fake_not_disconnect(text_host, monkeypatch):
    s = text_host
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.03)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.03)
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make():
        accepted = await submitted(s, first)
        await asyncio.wait_for(s.entered.wait(), 5)
        assert not s.closed.is_set()
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            # This case tests stalled-provider Stop after acknowledged partial;
            # the separate Stop/positive-publication race is allowed to latch.
            async with asyncio.timeout(5):
                while True:
                    partial = await http.get("/tasks/by-key/stable", headers=second)
                    if partial.status_code == 200 and partial.json()["content"]:
                        break
                    await asyncio.sleep(0.02)
            stopped = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=second)
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["cancel_requested"] is True
            assert await jobs(s) == ["cancelled"], s.errors
            assert s.closed.is_set() and len(s.calls) == 1
            found = await http.get("/tasks/by-key/stable", headers=second)
            assert found.json()["status"] == "cancelled"
        async with s.execution.history.operation(deadline=deadline()) as operation:
            entries = (await operation.verify()).entries
            assert not any(type(e.command.data) is TerminalOutputPrepare for e in entries)


@pytest.mark.asyncio
async def test_stop_before_dispatch_preserves_stop_and_never_transports(text_host, monkeypatch):
    from orchestrator.services.restore_history.effects import HistoryEffects

    s = text_host
    ready, resume = asyncio.Event(), asyncio.Event()
    native = HistoryEffects.register_attempt

    async def held(self, *args, **kwargs):
        ready.set()
        await resume.wait()
        return await native(self, *args, **kwargs)

    monkeypatch.setattr(HistoryEffects, "register_attempt", held)
    _, headers = await device(s, s.account)
    async with s.make():
        accepted = await submitted(s, headers)
        await asyncio.wait_for(ready.wait(), 5)
        try:
            async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
                stopped = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
                assert stopped.status_code == 200
        finally:
            resume.set()
        assert await jobs(s) == ["history_effect_unavailable"]
        assert not s.calls
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "0"
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"


@pytest.mark.asyncio
async def test_completed_terminal_wins_stop_without_appending_stop(text_host):
    s = text_host
    _, headers = await device(s, s.account)
    s.release.set()
    async with s.make():
        accepted = await submitted(s, headers)
        assert await jobs(s) == ["completed"]
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            stopped = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
            assert stopped.status_code == 409 and stopped.json()["detail"]["status"] == "completed"
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,status",
    [("bad", 401), ("expired", 401), ("session", 401), ("device", 401), ("foreign", 404)],
)
async def test_cancel_actual_bearer_account_session_device_checks(text_host, kind, status):
    s = text_host
    _, headers = await device(s, s.account)
    s.release.set()
    async with s.make():
        accepted = await submitted(s, headers)
        assert await jobs(s) == ["completed"]
        token_hash = hash_token(headers["Authorization"].removeprefix("Bearer "))
        async with s.db.connection() as conn:
            if kind == "expired":
                await conn.execute(
                    "UPDATE sessions SET access_expires_at=now()-interval '1s' WHERE access_token_hash=$1",
                    token_hash,
                )
            elif kind == "session":
                await conn.execute(
                    "UPDATE sessions SET revoked_at=now() WHERE access_token_hash=$1", token_hash
                )
            elif kind == "device":
                await conn.execute(
                    "UPDATE devices SET revoked_at=now() WHERE id IN (SELECT device_id FROM sessions WHERE access_token_hash=$1)",
                    token_hash,
                )
            elif kind == "foreign":
                other = await s.db.account(conn)
                _, headers = await device(s, other)
            else:
                headers = {"Authorization": "Bearer invalid"}
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            result = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
            assert result.status_code == status, result.text
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "3"
        assert len(s.calls) == 1


@pytest.mark.asyncio
async def test_empty_original_budget_never_dispatches(text_host):
    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        service = EntitlementService(s.w)
        policy = await service.resolve(s.account)
        amount = policy.remaining_for(False)
        hold = await service.reserve(s.account, amount, operation="chat", scope_id=uuid4())
        await service.settle(hold, amount)
        await submitted(s, headers)
        assert await jobs(s) == ["failed"], s.errors
        assert not s.calls


@pytest.mark.asyncio
async def test_missing_worker_collaborator_refuses_before_claim(text_host):
    from orchestrator.services.account_control.transaction import ObservationBlocked

    s = text_host
    async with s.make():
        ctx = s.execution.context()
        ctx.pop("history_text_execution")
        with pytest.raises(ObservationBlocked, match="history_execution_required"):
            await runner.run_chat_task(ctx, str(uuid4()))
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "0" and not s.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [False, True])
async def test_actual_process_death_retains_partial_and_fresh_owner_refuses_old_send(
    text_host, tmp_path, completed
):
    s = text_host
    _, headers = await device(s, s.account)
    packet = dict(
        socket=str(s.db.host),
        database=s.db.name,
        encryption_key=s.encryption_key,
        journal=str(s.journal),
        journal_id=str(s.journal_id),
        path=str(s.path),
        history_id=str(s.identity.history_id),
        key_check=s.identity.key_check,
        authorization=headers["Authorization"],
    )
    root = Path(__file__).resolve().parents[1]
    # Exec a new interpreter with only synthetic input; no inherited pools,
    # environment secrets, application DSN, or fork-adopted H executor.
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(root),
        "TMPDIR": os.environ["TMPDIR"],
        "DAEMON_ENCRYPTION_KEY": s.encryption_key,
    }

    async def start(mode):
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.history_text_process_driver",
            mode,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=root,
            env=environment,
        )
        assert child.stdin is not None
        child.stdin.write(json.dumps(packet).encode() + b"\n")
        await child.stdin.drain()
        child.stdin.close()
        return child

    expected_text = "process original partial" + (" and final" if completed else "")
    first = await start("complete" if completed else "start")
    try:
        assert first.stdout is not None
        assert first.stderr is not None
        raw = await asyncio.wait_for(first.stdout.readline(), 20)
        assert raw, (await first.stderr.read()).decode()
        witness = json.loads(raw)
        assert witness["partial"] == expected_text and witness["calls"] == 1
        assert s.db.sql("SELECT may_have_dispatched FROM task_inference_recovery") == "t"
        assert s.db.sql("SELECT count(*) FROM compute_reservation_metadata") == "1"
        packet["task"] = witness["task"]
        if completed:
            await asyncio.wait_for(first.wait(), 5)
    finally:
        if first.returncode is None:
            first.kill()  # Only this exact test-owned child, never a name/process scan.
        await asyncio.wait_for(first.wait(), 5)
    assert first.returncode == (0 if completed else -9)
    second = await start("reopen")
    try:
        stdout, stderr = await asyncio.wait_for(second.communicate(), 20)
        assert second.returncode == 0, stderr.decode()
        receipt = json.loads(stdout)
        assert receipt == {
            "calls": 0,
            "outcome": "history_effect_unavailable",
            "partial": expected_text,
            "status": "completed" if completed else "running",
            "task": packet["task"],
        }
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "1"
    finally:
        if second.returncode is None:
            second.kill()
        await asyncio.wait_for(second.wait(), 5)


@pytest.mark.asyncio
async def test_native_cancel_wait_rechecks_expiry_before_any_stop_mutation(text_host, monkeypatch):
    from orchestrator.tasks.inference import InferenceRecovery

    s = text_host
    _, headers = await device(s, s.account)
    native_entered = asyncio.Event()
    async with s.make():
        accepted = await submitted(s, headers)
        await asyncio.wait_for(s.entered.wait(), 5)
        # Let the original partial publication settle before introducing SQL contention.
        async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
            async with asyncio.timeout(5):
                while not (await http.get("/tasks/by-key/stable", headers=headers)).json()[
                    "content"
                ]:
                    await asyncio.sleep(0.02)
            original = InferenceRecovery.lock

            async def observed_lock(self, *args, **kwargs):
                native_entered.set()
                return await original(self, *args, **kwargs)

            monkeypatch.setattr(InferenceRecovery, "lock", observed_lock)
            async with s.db.connection() as owner:
                expires_after = asyncio.get_running_loop().time() + 0.22
                await owner.execute(
                    "UPDATE sessions SET access_expires_at=clock_timestamp()+interval '200 milliseconds' WHERE access_token_hash=$1",
                    hash_token(headers["Authorization"].removeprefix("Bearer ")),
                )
                tx = owner.transaction()
                await tx.start()
                await owner.fetchrow(
                    "SELECT id FROM tasks WHERE id=$1 FOR UPDATE", accepted.task.task_id
                )
                pending = asyncio.create_task(
                    http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
                )
                try:
                    await asyncio.wait_for(native_entered.wait(), 3)
                    # Stay within the unchanged native 250ms lock_timeout while
                    # crossing the session's wall-clock expiry after auth proof.
                    await asyncio.sleep(max(0, expires_after - asyncio.get_running_loop().time()))
                finally:
                    await tx.rollback()
                refused = await asyncio.wait_for(pending, 5)
                assert refused.status_code == 401, refused.text
            assert s.db.sql("SELECT cancel_requested_at IS NULL FROM tasks") == "t"
            assert s.db.sql("SELECT count(*) FROM task_history_projection") == "2"
            s.release.set()
            assert await jobs(s) == ["completed"]


@pytest.mark.asyncio
async def test_h_append_then_native_stage_failure_latches_no_transport_or_requeue(
    text_host, monkeypatch
):
    from orchestrator.services.restore_history.model import HistoryError

    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():

        async def failed_stage(conn, entry, *, deadline):
            raise ConnectionError("synthetic native projection failure after H append")

        monkeypatch.setattr(s.execution.projection, "stage_effect", failed_stage)
        await submitted(s, headers)
        outcomes = await jobs(s)
        assert outcomes == [s.execution._invocations[0].uncertainty]
        assert not s.calls and s.execution._blocked
        assert s.execution._invocations[0].uncertainty is not None
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert s.db.sql("SELECT status FROM tasks") == "running"
        assert s.db.sql("SELECT count(*) FROM task_history_projection") == "1"
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations WHERE status='open'") == "1"
        with pytest.raises(HistoryError):
            await runner.run_chat_task(s.execution.context(), s.db.sql("SELECT id FROM tasks"))
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"


@pytest.mark.asyncio
async def test_scope_link_ack_then_registration_failure_is_inert_no_second_registration(
    text_host, monkeypatch
):
    from orchestrator.tasks.inference import InferenceRecovery
    from orchestrator.services.account_control.transaction import ObservationBlocked

    s = text_host
    _, headers = await device(s, s.account)
    calls = []

    async def failed_registration(self, identity, shared):
        calls.append(identity)
        assert s.db.sql("SELECT compute_scope_id FROM task_attempts") == str(
            identity.financial_scope_id
        )
        shared.block("inference_registration_unavailable")
        raise ObservationBlocked("inference_registration_unavailable")

    monkeypatch.setattr(InferenceRecovery, "register", failed_registration)
    async with s.make():
        await submitted(s, headers)
        result = await jobs(s)
        assert result == ["inference_registration_unavailable"]
        assert len(calls) == 1 and not s.calls
        assert s.db.sql("SELECT count(*) FROM entitlement_reservations") == "0"
        assert s.db.sql("SELECT count(*) FROM attempt_lifecycle_control") == "0"
        assert s.db.sql("SELECT status FROM tasks") == "running"


@pytest.mark.asyncio
async def test_lost_acceptance_commit_ack_never_schedules_even_when_task_committed(
    text_host, monkeypatch
):
    from asyncpg.transaction import Transaction

    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        projection = s.host._acceptance._projection
        native_stage, native_exit = projection.stage, Transaction.__aexit__
        armed = False

        async def stage(conn, entry, *, deadline):
            nonlocal armed
            result = await native_stage(conn, entry, deadline=deadline)
            armed = True
            return result

        async def lost(tx, kind, value, traceback):
            nonlocal armed
            if not armed or kind is not None:
                return await native_exit(tx, kind, value, traceback)
            armed = False
            await native_exit(tx, kind, value, traceback)
            raise ConnectionError("synthetic lost A commit ACK")

        with monkeypatch.context() as patch:
            patch.setattr(projection, "stage", stage)
            patch.setattr(Transaction, "__aexit__", lost)
            with pytest.raises(ConnectionError):
                await submitted(s, headers)
        assert not s.execution._jobs and not s.calls
        assert s.db.sql("SELECT count(*) FROM tasks") == "1"
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "0"
        observed = await s.host.lookup(headers["Authorization"], "stable", deadline=deadline())
        assert observed.kind == "task" and not s.execution._jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["self-hosted-ui"], indirect=True)
async def test_real_enrollment_lost_ack_post_chat_and_by_key(text_host, monkeypatch):
    """Protected POST /chat under the core commit-then-lost-ACK fault, real devices."""
    from asyncpg.transaction import Transaction

    from tests.history_ui_support import actual_auth_app
    from tests.owned_search_redis import OwnedSearchRedis

    s = text_host
    owner = OwnedSearchRedis(
        Path(os.environ["OPENCODE_WORK_TEST_DIR"]), "daily-driver-app-flow-20261113"
    )
    redis = None
    tables = (
        "tasks",
        "task_attempts",
        "task_operations",
        "task_history_projection",
        "entitlement_reservations",
        "compute_reservation_metadata",
    )

    def counts():
        return tuple(s.db.sql(f"SELECT count(*) FROM {table}") for table in tables)

    try:
        owner.start()
        redis = await owner.connect()
        async with s.make():
            wired = await actual_auth_app(s, monkeypatch, redis, "http://fixture.test")
            transport = ASGITransport(wired.app)
            async with (
                AsyncClient(
                    transport=transport,
                    base_url="http://fixture.test",
                    headers={"Origin": "http://fixture.test"},
                ) as first,
                AsyncClient(
                    transport=transport,
                    base_url="http://fixture.test",
                    headers={"Origin": "http://fixture.test"},
                ) as second,
            ):
                # Only real setup and independent enrollment issue these devices.
                assert counts() == ("0",) * len(tables)
                setup = await first.post("/v1/auth/setup", json={"setup_token": wired.bootstrap})
                assert setup.status_code == 200, setup.text
                a = {"Authorization": "Bearer " + setup.json()["access_token"]}
                start = await first.post("/v1/auth/enroll/start", headers=a)
                assert start.status_code == 200, start.text
                enrollment = start.json()
                complete = await second.post(
                    "/v1/auth/enroll/complete",
                    json={
                        "pending_id": enrollment["pending_id"],
                        "code": enrollment["code"],
                        "client_kind": "web",
                    },
                )
                assert complete.status_code == 200, complete.text
                b = {"Authorization": "Bearer " + complete.json()["access_token"]}
                assert a != b
                assert s.db.sql("SELECT count(*) FROM devices") == "2"
                assert counts() == ("0",) * len(tables)

                # The unchanged SQL core fault: the acceptance projection stages,
                # the native transaction COMMITs, then its ACK is lost. Only the
                # acceptance stage arms it; setup/auth/read commits never do.
                projection = s.host._acceptance._projection
                native_stage, native_exit = projection.stage, Transaction.__aexit__
                armed = False

                async def stage(conn, entry, *, deadline):
                    nonlocal armed
                    result = await native_stage(conn, entry, deadline=deadline)
                    armed = True
                    return result

                async def lost(tx, kind, value, traceback):
                    nonlocal armed
                    if not armed or kind is not None:
                        return await native_exit(tx, kind, value, traceback)
                    armed = False
                    await native_exit(tx, kind, value, traceback)
                    raise ConnectionError("synthetic lost A commit ACK")

                with monkeypatch.context() as patch:
                    patch.setattr(projection, "stage", stage)
                    patch.setattr(Transaction, "__aexit__", lost)
                    refused = await first.post(
                        "/chat",
                        headers={**a, "Idempotency-Key": "stable"},
                        json={"message": "synthetic text"},
                    )
                assert refused.status_code == 503, refused.text
                assert refused.json()["detail"] == {
                    "code": "history_unavailable",
                    "task_id": None,
                    "retry_safe": False,
                }
                assert refused.headers["cache-control"] == "no-store"
                assert not s.execution._jobs and not s.calls
                assert counts() == ("1", "0", "0", "1", "0", "0")

                # Exactly one committed original task with this account, key and input.
                async with s.db.connection() as conn:
                    original = await conn.fetchrow(
                        "SELECT t.id,t.user_id,t.idempotency_key,t.conversation_id,"
                        "t.status,t.cancel_requested_at,t.request_hash,t.attempt_count,"
                        "m.content,m.role "
                        "FROM tasks t JOIN messages m ON m.id=t.result_message_id"
                    )
                    user_message = await conn.fetchval(
                        "SELECT content FROM messages WHERE id=(SELECT user_message_id FROM tasks)"
                    )
                assert original is not None
                assert original["user_id"] == s.account
                assert original["idempotency_key"] == "stable"
                assert original["status"] == "queued" and original["attempt_count"] == 0
                assert original["cancel_requested_at"] is None
                assert original["role"] == "assistant"
                assert s.execution.encryption.decrypt(original["content"]) == ""
                assert s.execution.encryption.decrypt(user_message) == "synthetic text"
                assert s.db.sql("SELECT request_hash ~ '^[0-9a-f]{64}$' FROM tasks") == "t"

                # The independently enrolled device reads that same surviving task.
                before_reads = counts()
                found = await second.get("/tasks/by-key/stable", headers=b)
                assert found.status_code == 200, found.text
                assert found.headers["cache-control"] == "no-store"
                body = found.json()
                assert body["id"] == str(original["id"])
                assert body["status"] == "queued" and body["content"] == ""
                assert body["attempt_count"] == 0 and body["cancel_requested"] is False
                # A read alone neither schedules work nor creates a second task.
                for no_auth in (
                    await second.get("/tasks/by-key/stable"),
                    await second.get(
                        "/tasks/by-key/stable", headers={"Authorization": "Bearer invalid"}
                    ),
                ):
                    assert no_auth.status_code == 401
                    assert no_auth.headers["cache-control"] == "no-store"
                assert counts() == before_reads
                assert not s.execution._jobs and not s.calls

                # Deliberate same-key probe keeps the existing uncertainty refusal.
                probe = await first.post(
                    "/chat",
                    headers={**a, "Idempotency-Key": "stable"},
                    json={"message": "synthetic text"},
                )
                assert probe.status_code == 503, probe.text
                assert probe.json()["detail"] == {
                    "code": "history_unavailable",
                    "task_id": None,
                    "retry_safe": False,
                }
                assert probe.headers["cache-control"] == "no-store"
                assert counts() == before_reads
                assert not s.execution._jobs and not s.calls
                assert s.db.sql("SELECT count(*) FROM devices") == "2"
    finally:
        if redis is not None:
            await redis.aclose()
        owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("old", ["terminal", "stop"])
async def test_fresh_owners_refuse_old_stop_or_terminal_before_claim_and_body_clear(
    text_host, old, monkeypatch
):
    s = text_host
    _, headers = await device(s, s.account)
    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.03)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.03)
    async with s.make():
        if old == "terminal":
            s.release.set()
        accepted = await submitted(s, headers)
        if old == "stop":
            await asyncio.wait_for(s.entered.wait(), 5)
            async with AsyncClient(transport=ASGITransport(s.app), base_url="http://test") as http:
                async with asyncio.timeout(5):
                    while not (await http.get("/tasks/by-key/stable", headers=headers)).json()[
                        "content"
                    ]:
                        await asyncio.sleep(0.02)
                assert (
                    await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
                ).status_code == 200
        assert await jobs(s) == (["completed"] if old == "terminal" else ["cancelled"])
        before = s.db.sql("SELECT content FROM messages WHERE role='assistant'")
    async with s.make():
        assert (
            await runner.run_chat_task(s.execution.context(), str(accepted.task.task_id))
            == "history_effect_unavailable"
        )
        assert s.db.sql("SELECT content FROM messages WHERE role='assistant'") == before
        assert s.db.sql("SELECT count(*) FROM task_attempts") == "1"
        assert len(s.calls) == 1
