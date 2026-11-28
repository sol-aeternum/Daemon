"""SIGKILL cut points for a single disposable original (used by the exec driver)."""

import asyncio
import json
from types import SimpleNamespace
from typing import cast

import httpx
import redis.asyncio as aioredis
from asyncpg.transaction import Transaction

from orchestrator import compute_runtime as runtime
from orchestrator.services.fetch.service import FetchService
from orchestrator.services.restore_history.source_execution import SourceRun
from orchestrator.services.search_pacing import RedisLike, SearchPacer
from orchestrator.tasks.store import TaskStore
from tests.source_model_fixture import public_notice


SOURCE = "https://station.example/loans"


def source_transports(packet, mode, patch, observations):
    """Exec-local transports; Redis remains owned by the parent test."""
    counts = {"models": 0, "searches": 0, "reads": 0}
    original_task = None

    async def redis_client():
        client = aioredis.Redis(
            unix_socket_path=packet["redis_socket"],
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        try:
            assert (await client.info("server"))["run_id"] == packet["redis_run_id"]
            assert await client.get(packet["redis_marker"]) == packet["redis_nonce"]
        except BaseException:
            await client.aclose()
            raise
        return cast(RedisLike, client)

    async def search(request):
        assert mode == "start" and counts["searches"] == 0
        assert request.url.host == "api.search.brave.com" and request.method == "GET"
        counts["searches"] += 1
        item = {"url": SOURCE, "title": "Field loans", "description": "Visit the station notice."}
        return httpx.Response(200, json={"web": {"results": [item]}})

    async def read(request):
        assert mode == "start" and counts["reads"] == 0 and request.method == "GET"
        counts["reads"] += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=public_notice("The station lends a brass tide gauge."),
        )

    async def model(**kwargs):
        nonlocal original_task
        assert mode == "start" and counts["models"] < 2
        counts["models"] += 1
        identity = runtime.current_scope().inference_identity
        assert identity is not None
        if original_task is None:
            original_task = identity.task_id
        assert identity.task_id == original_task
        name = "web_search" if counts["models"] == 1 else "web_fetch"
        assert [t["function"]["name"] for t in kwargs["tools"]] == [name]
        args = {"query": packet["question"]} if name == "web_search" else {"url": SOURCE}
        call = {
            "index": 0,
            "id": f"call-{counts['models']}",
            "function": {"name": name, "arguments": json.dumps(args)},
        }
        finish = SimpleNamespace(result={"state": "missing"})

        async def stream():
            yield {"choices": [{"delta": {"tool_calls": [call]}}]}
            yield {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }
            finish.result = {
                "state": "confirmed",
                "reason": "tool_calls",
                "provenance": "provider_wire",
            }

        response = stream()
        observations[response] = finish
        return response

    transport = httpx.MockTransport(read)
    if mode == "start":
        install_source_cuts(patch, packet["source_cut"], lambda: original_task, counts)
    return (
        model,
        httpx.MockTransport(search),
        transport,
        SearchPacer(client_factory=redis_client),
        counts,
    )


def install_source_cuts(patch, phase, task_id, counters):
    """Expose a witness, then suspend forever for the parent's actual SIGKILL."""

    async def witness():
        print(json.dumps({"cut": phase, "task": str(task_id()), **counters}), flush=True)
        await asyncio.Future()

    if phase == "before-page-send":
        # The possible-read record is acknowledged; the direct transport has
        # not yet been entered. Neither a rolled-back mark nor an HTTP stub
        # parked after delivery would test this boundary.
        mark = SourceRun.mark

        async def before_page(self, url):
            await mark(self, url)
            await witness()

        patch.setattr(SourceRun, "mark", before_page)
    elif phase == "after-page-before-pin":
        fetch = FetchService.fetch

        async def after_page(self, *args, **kwargs):
            result = await fetch(self, *args, **kwargs)
            assert result is not None and result.content
            await witness()
            return result

        patch.setattr(FetchService, "fetch", after_page)
    elif phase == "after-pin-commit-before-ack":
        # pg has committed the ORIGINAL pin's outer transaction, but the
        # caller of __aexit__ has not received its acknowledgement. Never pause
        # on a nested savepoint or on a heartbeat's unrelated connection.
        pin = TaskStore.pin_page
        exit_transaction = Transaction.__aexit__
        publication = None

        async def remember_transaction(self, conn, claim, key, snapshot_id):
            nonlocal publication
            result = await pin(self, conn, claim, key, snapshot_id)
            assert publication is None
            publication = conn.get_server_pid()
            return result

        async def after_commit(tx, exc_type, exc, tb):
            target = not tx._nested and tx._connection.get_server_pid() == publication
            result = await exit_transaction(tx, exc_type, exc, tb)
            if target and exc_type is None:
                await witness()
            return result

        patch.setattr(TaskStore, "pin_page", remember_transaction)
        patch.setattr(Transaction, "__aexit__", after_commit)
    else:
        raise ValueError("Unknown source cut")
