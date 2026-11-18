"""Owned exec-only synthetic restart driver; invoked solely by the PG test."""

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, cast
from uuid import UUID

import asyncpg
import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.models import ChatRequest
from orchestrator.services.restore_history.acceptance_host import IsolatedAcceptanceHost
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.model import Identity
from orchestrator.services.restore_history.observation import HistoryObservation
from orchestrator.services.restore_history.store import Ownership
from orchestrator.services.restore_history.terminal_projection import TerminalHistoryProjection
from orchestrator.services.restore_history.text_execution import HistoryTextExecution
from orchestrator.tasks.runner import run_chat_task
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import KEY
from tests.test_compute_runtime import _qualified_policy, _route


def deadline():
    return time.monotonic() + 10


async def main(packet, mode):
    settings = cast(Any, Settings)(
        _env_file=None,
        durable_chat_enabled=True,
        mock_llm=False,
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
    projection = TerminalHistoryProjection(
        identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
    )
    histories = [
        HistoryCoordinator(Path(packet["path"]), identity, ownership, profile="terminal-v3")
        for _ in range(3)
    ]
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
    )
    calls = 0

    async def provider(**kwargs):
        nonlocal calls
        calls += 1
        assert mode in ("start", "complete") and calls == 1
        scope = runtime.current_scope()
        identity = scope.inference_identity
        assert identity is not None
        # W retains its denied direct marker-table access. The parent performs
        # independent committed SQL inspection after this child's H witness.
        async with execution.history.operation(deadline=deadline()) as operation:
            assert len((await operation.verify()).entries) == 2

        async def stream():
            yield {"choices": [{"delta": {"content": "process original partial"}}]}
            row = await w.fetchrow(
                "SELECT id,result_message_id FROM tasks WHERE id=$1",
                identity.task_id,
            )
            content = await w.fetchval(
                "SELECT content FROM messages WHERE id=$1", row["result_message_id"]
            )
            partial = encryption.decrypt(content)
            assert partial == "process original partial"
            if mode == "complete":
                yield {"choices": [{"delta": {"content": " and final"}}]}
                yield {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
                return
            print(
                json.dumps({"task": str(row["id"]), "partial": partial, "calls": calls}), flush=True
            )
            await asyncio.Future()

        return stream()

    patch.setattr(runtime.litellm, "acompletion", provider)
    try:
        await host.open(deadline=deadline())
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
            accepted = await host.submit(
                packet["authorization"],
                "stable",
                ChatRequest(message="synthetic process text"),
                deadline=deadline(),
            )
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
                        "task": str(accepted.task.task_id),
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
            await pool.close()
        patch.undo()


if __name__ == "__main__":
    asyncio.run(main(json.loads(sys.stdin.readline()), sys.argv[1]))
