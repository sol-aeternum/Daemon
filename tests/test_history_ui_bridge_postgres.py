"""Explicit synthetic UI reads: original runner, SQL/POSIX and actual bearer auth."""

import asyncio
import json
from dataclasses import replace
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
import pytest

from orchestrator.services.restore_history.acceptance_host import create_isolated_ui_app
from orchestrator.services.restore_history.model import AcceptPrepare, SendPossible
from orchestrator.history_pagination import decode_cursor, encode_cursor
from tests.test_history_text_execution_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
    submitted,
    jobs,
    device,
)


async def saved(s, http, headers, conversation):
    async with asyncio.timeout(5):
        while True:
            response = await http.get(
                f"/conversations/{conversation}?pagination=cursor&limit=100", headers=headers
            )
            assert response.status_code == 200, response.text
            if response.json()["messages"][-1]["content"]:
                return response.json()
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_ui_real_original_partial_completion_and_scoped_cursors(text_host):
    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        app = create_isolated_ui_app(s.host)
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
            empty = await http.get("/conversations?pagination=cursor", headers=headers)
            assert empty.status_code == 200 and empty.json()["total"] == 0
            accepted = await submitted(
                s, headers, model="auto", answer_detail="balanced", research_effort="auto"
            )
            cid = accepted.task.conversation_id
            value = await saved(s, http, headers, cid)
            assert value["messages"][-1]["content"] == "original partial"
            assert value["active_task"]["id"] == str(accepted.task.task_id)
            assert value["message_count"] == 2 and value["selected_model"] == "auto"
            start = value["message_page"]["start_cursor"]
            end = value["message_page"]["end_cursor"]
            boundary = decode_cursor(end, user_id=s.account, conversation_id=cid)
            low = encode_cursor(
                replace(boundary, id=UUID(int=0)), user_id=s.account, conversation_id=cid
            )
            high = encode_cursor(
                replace(boundary, id=UUID(int=2**128 - 1)), user_id=s.account, conversation_id=cid
            )
            for bounds, roles in (
                (f"before={low}", ["user"]),
                (f"before={high}", ["user", "assistant"]),
                (f"through={low}", ["assistant"]),
                (f"through={high}", []),
            ):
                tied = await http.get(
                    f"/conversations/{cid}?pagination=cursor&limit=100&{bounds}", headers=headers
                )
                assert tied.status_code == 200, tied.text
                assert [message["role"] for message in tied.json()["messages"]] == roles
            foreign = encode_cursor(boundary, user_id=uuid4(), conversation_id=cid)
            assert (
                await http.get(
                    f"/conversations/{cid}?pagination=cursor&before={foreign}", headers=headers
                )
            ).status_code == 400
            for bounds, roles in (
                (f"before={end}", ["user"]),
                (f"through={end}", ["assistant"]),
                (f"before={start}", []),
                (f"before={start}&through={end}", []),
                (f"through={start}", ["user", "assistant"]),
            ):
                page = await http.get(
                    f"/conversations/{cid}?pagination=cursor&limit=100&{bounds}", headers=headers
                )
                assert page.status_code == 200, page.text
                assert [m["role"] for m in page.json()["messages"]] == roles
                if not roles:
                    assert page.json()["message_page"] == dict(
                        has_more=False, next_cursor=None, start_cursor=None, end_cursor=None
                    )
            newest = (
                await http.get(f"/conversations/{cid}?pagination=cursor&limit=1", headers=headers)
            ).json()
            assert (
                newest["messages"][0]["role"] == "assistant" and newest["message_page"]["has_more"]
            )
            older = (
                await http.get(
                    f"/conversations/{cid}?pagination=cursor&limit=1&before={newest['message_page']['next_cursor']}",
                    headers=headers,
                )
            ).json()
            assert older["messages"][0]["role"] == "user" and not older["message_page"]["has_more"]
            listing = (await http.get("/conversations?pagination=cursor", headers=headers)).json()
            list_boundary = decode_cursor(listing["page"]["end_cursor"], user_id=s.account)
            for ident, count in ((UUID(int=0), 0), (UUID(int=2**128 - 1), 1)):
                cursor = encode_cursor(replace(list_boundary, id=ident), user_id=s.account)
                tied = await http.get(
                    f"/conversations?pagination=cursor&cursor={cursor}", headers=headers
                )
                assert tied.status_code == 200, tied.text
                assert tied.json()["total"] == 1 and len(tied.json()["conversations"]) == count
            next_list = (
                await http.get(
                    f"/conversations?pagination=cursor&cursor={listing['page']['end_cursor']}",
                    headers=headers,
                )
            ).json()
            assert next_list["conversations"] == [] and next_list["total"] == 1
            assert (
                await http.get(
                    f"/conversations/{cid}?pagination=cursor&before=bad", headers=headers
                )
            ).status_code == 400
            assert (
                await http.get(
                    f"/conversations/{cid}?pagination=cursor&before={listing['page']['end_cursor']}",
                    headers=headers,
                )
            ).status_code == 400
            assert (
                await http.get(f"/conversations/{cid}?before={start}", headers=headers)
            ).status_code == 400
            assert (
                await http.get("/conversations?search=unsearched", headers=headers)
            ).status_code == 400
            for path in (f"/tasks/{uuid4()}", f"/conversations/{uuid4()}"):
                response = await http.get(path, headers=headers)
                assert (
                    response.status_code == 404 and response.headers["cache-control"] == "no-store"
                )
            s.release.set()
            assert await jobs(s) == ["completed"]
            final = await http.get(
                f"/conversations/{cid}?pagination=cursor&through={start}", headers=headers
            )
            assert final.status_code == 200, final.text
            assert final.json()["messages"][-1]["metadata"] == {"terminal_status": "complete"}
            assert (
                final.json()["messages"][-1]["reasoning_model"]
                == final.json()["messages"][-1]["model"]
            )
            assert final.json()["latest_task"]["content"] == "original partial and final"
            assert final.json()["active_task"] is None
            task = await http.get(f"/tasks/{accepted.task.task_id}", headers=headers)
            by_key = await http.get("/tasks/by-key/stable", headers=headers)
            assert task.json() == by_key.json() and len(s.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "metadata",
        "usage",
        "trace",
        "model",
        "user",
        "conversation",
        "extra_message",
        "foreign_message",
        "extra_conversation",
        "extra_task",
        "generation",
    ],
)
async def test_ui_refuses_unsupported_or_incomplete_account_without_ids(text_host, damage):
    s = text_host
    _, headers = await device(s, s.account)
    s.release.set()
    async with s.make():
        accepted = await submitted(s, headers)
        assert await jobs(s) == ["completed"]
        task = accepted.task
        async with s.db.connection() as conn:
            if damage in ("metadata", "usage"):
                await conn.execute(
                    "UPDATE messages SET metadata=metadata || $2::jsonb WHERE id=$1",
                    task.result_message_id,
                    json.dumps({"unknown" if damage == "metadata" else "usage": {}}),
                )
            elif damage == "trace":
                await conn.execute(
                    "UPDATE messages SET tool_calls='{}'::jsonb WHERE id=$1", task.result_message_id
                )
            elif damage == "model":
                await conn.execute(
                    "UPDATE messages SET reasoning_model='other' WHERE id=$1",
                    task.result_message_id,
                )
            elif damage == "user":
                await conn.execute(
                    "UPDATE messages SET content=$2 WHERE id=$1",
                    task.user_message_id,
                    s.execution.encryption.encrypt("changed"),
                )
            elif damage == "conversation":
                await conn.execute(
                    "UPDATE conversations SET summary='hidden' WHERE id=$1", task.conversation_id
                )
            elif damage in ("extra_message", "foreign_message"):
                owner = s.account if damage == "extra_message" else await s.db.account(conn)
                await conn.execute(
                    "INSERT INTO messages(conversation_id,user_id,role,content) VALUES($1,$2,'user','unqualified')",
                    task.conversation_id,
                    owner,
                )
            elif damage == "extra_conversation":
                await conn.execute(
                    "INSERT INTO conversations(user_id,pipeline) VALUES($1,'cloud')", s.account
                )
            elif damage == "extra_task":
                extra_user = await conn.fetchval(
                    "INSERT INTO messages(conversation_id,user_id,role,content) VALUES($1,$2,'user','unqualified') RETURNING id",
                    task.conversation_id,
                    s.account,
                )
                extra_result = await conn.fetchval(
                    "INSERT INTO messages(conversation_id,user_id,role,content) VALUES($1,$2,'assistant','unqualified') RETURNING id",
                    task.conversation_id,
                    s.account,
                )
                await conn.execute(
                    "INSERT INTO tasks(user_id,conversation_id,user_message_id,result_message_id,input_ciphertext,input_version,request_hash) SELECT user_id,conversation_id,$2,$3,input_ciphertext,input_version,request_hash FROM tasks WHERE id=$1",
                    task.task_id,
                    extra_user,
                    extra_result,
                )
            else:
                await conn.execute(
                    "UPDATE entitlement_accounts SET content_generation=content_generation+1 WHERE user_id=$1",
                    s.account,
                )
        async with AsyncClient(
            transport=ASGITransport(create_isolated_ui_app(s.host)), base_url="http://test"
        ) as http:
            for path in (
                "/conversations?pagination=cursor",
                f"/conversations/{task.conversation_id}",
                f"/tasks/{task.task_id}",
            ):
                response = await http.get(path, headers=headers)
                assert response.status_code == 409, response.text
                assert response.json()["detail"] == dict(
                    code="restore_recovery_required", task_id=None, retry_safe=False
                )
                assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_default_app_does_not_install_ui_reads(text_host):
    s = text_host
    async with s.make():
        routes = {getattr(route, "path", None) for route in s.app.routes}
        assert "/conversations" not in routes and "/tasks/{task_id}" not in routes


@pytest.mark.asyncio
async def test_ui_current_host_stop_and_distinct_device_isolation(text_host, monkeypatch):
    from orchestrator.tasks import runner

    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.03)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.03)
    s = text_host
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.db.connection() as conn:
        other = await s.db.account(conn)
    _, foreign = await device(s, other)
    async with s.make():
        accepted = await submitted(s, first)
        async with AsyncClient(
            transport=ASGITransport(create_isolated_ui_app(s.host)), base_url="http://test"
        ) as http:
            await saved(s, http, second, accepted.task.conversation_id)
            for path in (
                f"/tasks/{accepted.task.task_id}",
                f"/conversations/{accepted.task.conversation_id}",
            ):
                assert (await http.get(path, headers=foreign)).status_code == 404
                assert (await http.get(path)).status_code == 401
            stop = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=second)
            assert stop.status_code == 200
            assert await jobs(s) == ["cancelled"]
            async with s.db.connection() as conn:
                native = await conn.fetchrow(
                    "SELECT t.status,t.terminal_code,m.metadata,m.model,m.reasoning_model,m.reasoning_text,m.reasoning_duration_secs,m.advisor_traces FROM tasks t JOIN messages m ON m.id=t.result_message_id WHERE t.id=$1",
                    accepted.task.task_id,
                )
            result = await http.get(
                f"/conversations/{accepted.task.conversation_id}", headers=second
            )
            assert result.status_code == 200, (result.text, dict(native))
            assert result.json()["latest_task"]["status"] == "cancelled"
            metadata = result.json()["messages"][-1]["metadata"]
            assert metadata == {
                "terminal_status": "cancelled",
                "terminal_reason": "cancelled",
                "inference_dispatch_possible": True,
                "material_effect_possible": True,
            }
            # Negative fixture mutations only: never claimed as native positives.
            invalid = []
            for flag in ("inference_dispatch_possible", "material_effect_possible"):
                for value in (False, None, 0, 1, "true", {}):
                    invalid.append({**metadata, flag: value})
            invalid.extend(
                [
                    {"terminal_status": "cancelled", "terminal_reason": "cancelled"},
                    {
                        "terminal_status": "cancelled",
                        "terminal_reason": "cancelled",
                        "material_effect_possible": True,
                    },
                    {
                        "terminal_status": "cancelled",
                        "terminal_reason": "cancelled",
                        "inference_dispatch_possible": True,
                    },
                    {**metadata, "unexpected": True},
                    {**metadata, "terminal_reason": "other"},
                    {key: value for key, value in metadata.items() if key != "terminal_reason"},
                ]
            )
            for damaged in invalid:
                async with s.db.connection() as conn:
                    await conn.execute(
                        "UPDATE messages SET metadata=$2::jsonb WHERE id=$1",
                        accepted.task.result_message_id,
                        json.dumps(damaged),
                    )
                refused = await http.get(f"/tasks/{accepted.task.task_id}", headers=second)
                assert refused.status_code == 409, (damaged, refused.text)
            async with s.db.connection() as conn:
                await conn.execute(
                    "UPDATE messages SET metadata=$2::jsonb WHERE id=$1",
                    accepted.task.result_message_id,
                    json.dumps(metadata),
                )
                await conn.execute(
                    "UPDATE tasks SET terminal_code='other' WHERE id=$1", accepted.task.task_id
                )
            assert (
                await http.get(f"/tasks/{accepted.task.task_id}", headers=second)
            ).status_code == 409
            async with s.db.connection() as conn:
                await conn.execute(
                    "UPDATE tasks SET terminal_code='cancelled' WHERE id=$1", accepted.task.task_id
                )
            assert (
                await http.get(f"/tasks/{accepted.task.task_id}", headers=second)
            ).status_code == 200
            async with s.db.connection() as conn:
                await conn.execute(
                    "UPDATE tasks SET lease_epoch=lease_epoch+1 WHERE id=$1", accepted.task.task_id
                )
            assert (
                await http.get(f"/tasks/{accepted.task.task_id}", headers=second)
            ).status_code == 409
            async with s.db.connection() as conn:
                await conn.execute(
                    "UPDATE tasks SET lease_epoch=lease_epoch-1 WHERE id=$1", accepted.task.task_id
                )
            # Predicate-only synthetic H variants over an actual PG operation:
            # these do not claim projection parity or positive HTTP qualification.
            reader = s.host._reader
            from tests.test_history_composition_postgres import deadline

            async with reader._history.operation(deadline=deadline()) as operation:
                state = await operation.verify()
                send_entry = next(e for e in state.entries if type(e.command.data) is SendPossible)
                send = send_entry.command.data
                assert type(send) is SendPossible
                native_row = {"lease_epoch": send.epoch, "attempt_count": 1}
                original = next(
                    e.command.data for e in state.entries if type(e.command.data) is AcceptPrepare
                )
                async with s.w.acquire() as conn, conn.transaction():
                    xid = await conn.fetchval("SELECT txid_current()")
                    for changed in (
                        replace(send, epoch=send.epoch + 1),
                        replace(send, account_generation=send.account_generation + 1),
                        replace(send, account_id=uuid4()),
                        replace(send, effect_kind="embedding"),
                    ):
                        changed_entry = replace(
                            send_entry, command=replace(send_entry.command, data=changed)
                        )
                        altered = replace(
                            state,
                            entries=tuple(
                                changed_entry if e is send_entry else e for e in state.entries
                            ),
                        )
                        with pytest.raises(ValueError, match="unexpected original send"):
                            await reader._cancelled_owned(
                                operation,
                                conn,
                                xid,
                                original,
                                native_row,
                                altered,
                                metadata,
                                deadline(),
                            )
                    with pytest.raises(ValueError, match="unexpected original send"):
                        await reader._cancelled_owned(
                            operation,
                            conn,
                            xid,
                            original,
                            native_row,
                            replace(state, entries=(*state.entries, send_entry)),
                            metadata,
                            deadline(),
                        )
            assert s.closed.is_set() and len(s.calls) == 1


@pytest.mark.asyncio
async def test_reconstructed_host_cannot_adopt_cancelled_control_terminal(text_host, monkeypatch):
    from orchestrator.tasks import runner

    monkeypatch.setattr(runner, "HEARTBEAT_S", 0.03)
    monkeypatch.setattr(runner, "STOP_GRACE_S", 0.03)
    s = text_host
    _, headers = await device(s, s.account)
    async with s.make():
        accepted = await submitted(s, headers)
        async with AsyncClient(
            transport=ASGITransport(create_isolated_ui_app(s.host)), base_url="http://test"
        ) as http:
            await saved(s, http, headers, accepted.task.conversation_id)
            assert (
                await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
            ).status_code == 200
            assert await jobs(s) == ["cancelled"]
            assert (
                await http.get(f"/tasks/{accepted.task.task_id}", headers=headers)
            ).status_code == 200
    async with s.make():
        async with AsyncClient(
            transport=ASGITransport(create_isolated_ui_app(s.host)), base_url="http://test"
        ) as http:
            assert (
                await http.get(f"/tasks/{accepted.task.task_id}", headers=headers)
            ).status_code == 409
            assert (await http.get("/tasks/by-key/stable", headers=headers)).json()[
                "status"
            ] == "cancelled"
        assert len(s.calls) == 1 and not s.execution._jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_claim", "before_send"])
async def test_original_runner_gated_stop_before_transport_preserves_native_flags(
    text_host, monkeypatch, phase
):
    from orchestrator.services.restore_history import text_execution
    from orchestrator import daemon
    from orchestrator.tasks import runner

    s = text_host
    entered, resume = asyncio.Event(), asyncio.Event()
    if phase == "before_claim":
        run_original = text_execution.run_chat_task

        async def gated_run(*args, **kwargs):
            entered.set()
            await resume.wait()
            return await run_original(*args, **kwargs)

        monkeypatch.setattr(text_execution, "run_chat_task", gated_run)
    else:
        stream_original = daemon.stream_sse_chat
        monkeypatch.setattr(runner, "HEARTBEAT_S", 0.03)
        monkeypatch.setattr(runner, "STOP_GRACE_S", 0.03)

        async def gated_stream(*args, **kwargs):
            entered.set()
            await resume.wait()
            async for frame in stream_original(*args, **kwargs):
                yield frame

        monkeypatch.setattr(daemon, "stream_sse_chat", gated_stream)
    _, headers = await device(s, s.account)
    async with s.make():
        accepted = await submitted(s, headers)
        try:
            await asyncio.wait_for(entered.wait(), 5)
            async with AsyncClient(
                transport=ASGITransport(create_isolated_ui_app(s.host)), base_url="http://test"
            ) as http:
                stopped = await http.post(f"/tasks/{accepted.task.task_id}/cancel", headers=headers)
                assert stopped.status_code == 200, stopped.text
                if phase == "before_send":
                    assert await jobs(s) == ["cancelled"]
                result = await http.get(
                    f"/conversations/{accepted.task.conversation_id}", headers=headers
                )
                assert result.status_code == 200, result.text
                metadata = result.json()["messages"][-1]["metadata"]
                assert metadata == {
                    "terminal_status": "cancelled",
                    "terminal_reason": "cancelled",
                    **({"material_effect_possible": True} if phase == "before_send" else {}),
                }
                assert result.json()["latest_task"]["status"] == "cancelled"
                if phase == "before_send":
                    async with s.db.connection() as conn:
                        assert (
                            await conn.fetchval(
                                "SELECT count(*) FROM task_events WHERE kind='execution_started'"
                            )
                            == 1
                        )
                        await conn.execute(
                            "UPDATE messages SET metadata=metadata-'material_effect_possible' WHERE id=$1",
                            accepted.task.result_message_id,
                        )
                    assert (
                        await http.get(f"/tasks/{accepted.task.task_id}", headers=headers)
                    ).status_code == 409
                assert not s.calls
        finally:
            resume.set()
        outcomes = await jobs(s)
        assert outcomes == (
            ["history_effect_unavailable"] if phase == "before_claim" else ["cancelled"]
        ), outcomes
        assert not s.calls
