"""Owned exec-only synthetic restart driver; invoked solely by the PG test."""

import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import asyncpg
import httpx
import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.models import ChatRequest
from orchestrator.services.restore_history.acceptance_host import (
    IsolatedAcceptanceHost,
    create_isolated_ui_app,
)
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.inbox_lineage import InboxChild
from orchestrator.services.restore_history.model import Identity
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.services.restore_history.store import Ownership
from orchestrator.services.restore_history.terminal_projection import (
    InboxResearchHistoryProjection,
    InboxRetiredHistoryProjection,
    InboxSourcesHistoryProjection,
    TerminalHistoryProjection,
)
from orchestrator.services.restore_history.text_execution import (
    HistoryResearchExecution,
    HistorySourcesExecution,
    HistoryTextExecution,
)
from orchestrator.services.search_pacing import SearchPacer
from orchestrator.tasks.runner import run_chat_task
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import KEY
from tests.test_compute_runtime import _qualified_policy, _route


def deadline():
    return time.monotonic() + 10


async def main(packet, mode):
    if mode not in {"start", "complete", "ui-reopen", "reopen", "managed"}:
        raise ValueError("unknown fixture mode")
    settings = cast(Any, Settings)(
        _env_file=None,
        durable_chat_enabled=True,
        mock_llm=False,
        daemon_auth_pepper="synthetic-only-isolated-source-process-fixture",
        daemon_encryption_key=packet["encryption_key"],
        daemon_task_acceptor_role="md_acceptor",
        daemon_task_worker_role="md_app",
        daemon_deletion_journal_dir=packet["journal"],
        daemon_deletion_journal_id=packet["journal_id"],
        openrouter_api_key="synthetic-no-network",
    )
    patch = pytest.MonkeyPatch()
    _qualified_policy(patch, route=_route(input_price=1000))
    patch.setattr(runtime, "get_settings", lambda: settings)
    encryption = ContentEncryption(packet["encryption_key"])
    identity = Identity(UUID(packet["history_id"]), packet["key_check"])
    ownership = Ownership(os.geteuid(), frozenset({os.geteuid()}), os.getegid())
    pools = []
    for role in ("md_acceptor", "md_app"):
        pools.append(
            await asyncpg.create_pool(
                host=packet["socket"],
                database=packet["database"],
                user=role,
                password="",
                ssl=False,
                min_size=0,
                max_size=3,
            )
        )
    a, w = pools
    channel = lineage = None
    release = asyncio.Event()
    entered = asyncio.get_running_loop().create_future()
    source_cut = packet.get("source_cut")
    if mode == "managed":
        channel = socket.socket(fileno=packet["control_fd"])
        channel.set_inheritable(False)
        with socket.fromfd(packet["listener_fd"], socket.AF_INET, socket.SOCK_STREAM) as listener:
            lineage = InboxChild(channel, listener, Path(packet["path"]), identity.history_id)
    research = packet.get("inbox_research", False)
    projection_type = InboxRetiredHistoryProjection if lineage else TerminalHistoryProjection
    if research:
        assert not lineage and mode in {"start", "ui-reopen"}
        projection_type = (
            InboxSourcesHistoryProjection if source_cut else InboxResearchHistoryProjection
        )
        from orchestrator.entitlements.policy import parse_inference_policy
        from tests.test_metered_web_search import _approved_document

        # The original route and the native tool admission both use this fixture policy.
        previous = runtime.load_inference_policy()
        services = parse_inference_policy(_approved_document())
        policy = SimpleNamespace(
            routes=previous.routes,
            requirements=services.requirements,
            tool_service=services.tool_service,
        )
        patch.setattr(runtime, "load_inference_policy", lambda: policy)
    projection = projection_type(
        identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    histories = [
        HistoryCoordinator(Path(packet["path"]), identity, ownership, profile=projection.profile)
        for _ in range(3)
    ]
    calls = 0
    observations = {}
    source_counts = None
    if isinstance(projection, InboxSourcesHistoryProjection):
        from tests.history_source_process_faults import source_transports

        model, search, source, pacer, source_counts = source_transports(
            packet, mode, patch, observations
        )
        execution = HistorySourcesExecution(
            w,
            encryption,
            settings,
            histories[2],
            projection,
            model_transport=model,
            search_transport=search,
            search_pacer=pacer,
            source_transport=source,
            source_key=KEY,
        )
    elif isinstance(projection, InboxResearchHistoryProjection):

        def no_search(request):
            raise AssertionError("Process interruption before search must never reach transport")

        async def interrupted_model(**kwargs):
            nonlocal calls
            calls += 1
            assert mode == "start" and calls == 1
            assert [tool["function"]["name"] for tool in kwargs["tools"]] == ["web_search"]
            attempt = runtime.current_scope().inference_identity
            assert attempt is not None
            content = await w.fetchval(
                "SELECT m.content FROM tasks t JOIN messages m ON m.id=t.result_message_id "
                "WHERE t.id=$1",
                attempt.task_id,
            )
            print(
                json.dumps(
                    {
                        "task": str(attempt.task_id),
                        "partial": encryption.decrypt(content),
                        "calls": calls,
                    }
                ),
                flush=True,
            )
            await asyncio.Future()  # The owning test kills this exact interpreter.

        execution = HistoryResearchExecution(
            w,
            encryption,
            settings,
            histories[2],
            projection,
            model_transport=interrupted_model,
            search_transport=httpx.MockTransport(no_search),
            search_pacer=SearchPacer(client_factory=None),
        )
    else:
        execution = HistoryTextExecution(w, encryption, settings, histories[2], projection)
    host = IsolatedAcceptanceHost(
        HistoryAcceptance(
            TaskStore(a, encryption, MemoryStore(a, encryption), cover_new_inference=True),
            histories[0],
            projection,
            KEY,
        ),
        HistoryObservation(
            TaskStore(w, encryption, MemoryStore(w, encryption)), histories[1], projection, KEY
        ),
        fingerprint_key="synthetic-pepper",
        max_body_bytes=4096,
        keepalive_s=1,
        execution=execution,
        inbox_pilot=packet.get("inbox_two_jobs", False),
        inbox_two_jobs=packet.get("inbox_two_jobs", False),
        _inbox_child=lineage,
    )
    patch.setattr(runtime.inference_transport, "observe_stream_finish", observations.get)

    async def provider(**kwargs):
        nonlocal calls
        assert not research, "Research dispatch must use its explicitly constructed transport"
        calls += 1
        assert mode in ("start", "complete", "managed") and calls == 1
        scope = runtime.current_scope()
        identity = scope.inference_identity
        assert identity is not None
        # W retains its denied direct marker-table access. The parent performs
        # independent committed SQL inspection after this child's H witness.
        async with execution.history.operation(deadline=deadline()) as operation:
            assert len((await operation.verify()).entries) == (
                6 if lineage and lineage.handoff else 2
            )

        raw_finish = SimpleNamespace(result={"state": "missing"})

        async def stream():
            first = "process original partial"
            last = " and final"
            if lineage:
                first, last = (
                    ("Synthetic report Y.", " Second fixture.")
                    if lineage.handoff
                    else ("Synthetic report X.", " First fixture.")
                )
            yield {"choices": [{"delta": {"content": first}}]}
            row = await w.fetchrow(
                "SELECT id,result_message_id FROM tasks WHERE id=$1",
                identity.task_id,
            )
            content = await w.fetchval(
                "SELECT content FROM messages WHERE id=$1", row["result_message_id"]
            )
            partial = encryption.decrypt(content)
            assert partial == first
            if lineage:
                entered.set_result(str(identity.task_id))
                await release.wait()
            if mode in ("complete", "managed"):
                yield {"choices": [{"delta": {"content": last}}]}
                yield {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
                raw_finish.result = {
                    "state": "confirmed",
                    "reason": "stop",
                    "provenance": "provider_wire",
                }
                return
            print(
                json.dumps({"task": str(row["id"]), "partial": partial, "calls": calls}), flush=True
            )
            await asyncio.Future()

        response = stream()
        if lineage and packet.get("fault") == "transport":
            from tests.history_restart_faults import FailedTransportClose

            response = FailedTransportClose(response)
        observations[response] = raw_finish
        return response

    patch.setattr(runtime.litellm, "acompletion", provider)
    retired = False
    try:
        await host.open(deadline=deadline())
        if lineage:
            assert channel is not None
            from tests.history_restart_faults import install
            from tests.history_restart_server import serve

            install(
                patch, host, execution, packet.get("fault"), successor=lineage.handoff is not None
            )
            retired = await serve(
                host,
                execution,
                settings,
                w,
                packet,
                patch,
                channel,
                release,
                entered,
                lambda: calls,
            )
            return
        if packet.get("inbox_two_jobs") and mode == "ui-reopen":
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_isolated_ui_app(host)),
                base_url="http://isolated",
                headers={"Authorization": packet["authorization"]},
            ) as http:
                observed = await http.get("/tasks/inbox-pilot", params={"topic": "X"})
                if source_cut and observed.status_code == 409:
                    # A mixed/unsupported projection must not expose an old
                    # report or promote partial evidence to a replacement run.
                    denied = await http.get(f"/tasks/{packet['task']}")
                    assert denied.status_code == 409
                    denied_start = await http.post(
                        "/tasks/inbox-pilot",
                        headers={"Idempotency-Key": "new-source-original"},
                        json={"text": "Research: When does the town archive open?"},
                    )
                    assert denied_start.status_code in (409, 503)
                    assert calls == 0 and not execution._scheduled and not execution._jobs
                    assert source_counts == {"models": 0, "searches": 0, "reads": 0}
                    print(
                        json.dumps(
                            {
                                "task": packet["task"],
                                "status": "unavailable",
                                "calls": 0,
                                "ui_bridge": True,
                            }
                        ),
                        flush=True,
                    )
                    return
                assert observed.status_code == 200
                status = observed.json()
                assert status["can_start"] == []
                response = await http.get(f"/tasks/{packet['task']}")
                assert response.status_code == 200
                task = response.json()
                conversation = await http.get(
                    f"/conversations/{task['conversation_id']}", params={"pagination": "cursor"}
                )
                assert conversation.status_code == 200
                detail = conversation.json()
                assert detail["latest_task"]["id"] == packet["task"]
                assert detail["messages"][-1]["content"] == task["content"]
                assert status["state"] == task["status"]
                if task["status"] == "completed":
                    assert status["conversation_id"] == task["conversation_id"]
                else:
                    assert "conversation_id" not in status
                refused = await http.post(
                    "/tasks/inbox-pilot",
                    headers={"Idempotency-Key": "independent-y"},
                    json={
                        "text": "Research: When does the town archive open?"
                        if source_cut
                        else "Do a deep dive on Y."
                    },
                )
                assert refused.status_code == 409
                assert not execution._scheduled and not execution._jobs
                if source_counts is not None:
                    assert source_counts == {"models": 0, "searches": 0, "reads": 0}
                print(
                    json.dumps(
                        {
                            "task": task["id"],
                            "partial": task["content"],
                            "status": task["status"],
                            "calls": calls,
                            "ui_bridge": True,
                        }
                    ),
                    flush=True,
                )
            return
        if mode == "ui-reopen":
            # Fresh exec-only read of the new bridge, not the old by-key runner
            # recovery test and never a repeated claim or transport invocation.
            task = await host.ui_read(
                packet["authorization"], "task", UUID(packet["task"]), deadline=deadline()
            )
            conversation = await host.ui_read(
                packet["authorization"],
                "conversation",
                task.conversation_id,
                deadline=deadline(),
                pagination="cursor",
            )
            listing = await host.ui_read(
                packet["authorization"], "list", None, deadline=deadline(), pagination="cursor"
            )
            assert len(conversation.messages) == 2 and listing.total == 1
            assert conversation.messages[-1].content == task.content
            assert not execution._jobs and not execution._scheduled
            print(
                json.dumps(
                    {
                        "task": str(task.id),
                        "partial": task.content,
                        "status": task.status,
                        "calls": calls,
                        "ui_bridge": True,
                    }
                ),
                flush=True,
            )
            return
        if mode in ("start", "complete"):
            if packet.get("inbox_two_jobs"):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_isolated_ui_app(host)),
                    base_url="http://isolated",
                ) as http:
                    response = await http.post(
                        "/tasks/inbox-pilot",
                        headers={
                            "Authorization": packet["authorization"],
                            "Idempotency-Key": "stable",
                        },
                        json={"text": packet.get("question", "Do a deep dive on X.")},
                    )
                    assert response.status_code == 200
                task_id = next(iter(execution._scheduled))
            else:
                accepted = await host.submit(
                    packet["authorization"],
                    "stable",
                    ChatRequest(message="synthetic process text"),
                    deadline=deadline(),
                )
                task_id = accepted.task.task_id
            results = await asyncio.gather(*execution._jobs)
            if mode == "start":
                raise AssertionError("original process unexpectedly finished")
            assert results == ["completed"]
            observed = await host.lookup(packet["authorization"], "stable", deadline=deadline())
            assert observed.snapshot is not None
            assert observed.snapshot.status.value == "completed"
            print(
                json.dumps(
                    {
                        "task": str(task_id),
                        "partial": observed.snapshot.content,
                        "calls": calls,
                    }
                ),
                flush=True,
            )
            return
        observed = await host.lookup(packet["authorization"], "stable", deadline=deadline())
        assert observed.snapshot is not None
        assert observed.kind == "task" and str(observed.snapshot.task_id) == packet["task"]
        outcome = await run_chat_task(execution.context(), packet["task"])
        print(
            json.dumps(
                {
                    "calls": calls,
                    "outcome": outcome,
                    "partial": observed.snapshot.content,
                    "status": observed.snapshot.status.value,
                    "task": packet["task"],
                }
            ),
            flush=True,
        )
    finally:
        await host.aclose(deadline=deadline())
        for pool in pools:
            if retired and pool.is_closing():
                raise RuntimeError("retiring pool was already closing")
            await pool.close()
        if retired and packet.get("fault") == "pool-close":
            raise ConnectionError("synthetic pool close acknowledgement lost")
        if retired and packet.get("fault") != "retirement-ack":
            host.acknowledge_retirement()
        if channel is not None:
            channel.close()
        patch.undo()
        if retired and packet.get("fault") == "nonzero-exit":
            raise RuntimeError("synthetic exit failure after retirement acknowledgement")


if __name__ == "__main__":
    asyncio.run(main(json.loads(sys.stdin.readline()), sys.argv[1]))
