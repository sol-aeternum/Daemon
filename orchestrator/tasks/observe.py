"""Observe a durable task as the existing chat SSE stream (DURABLE_REQUEST_DESIGN §8).

The observer starts from the owner-scoped snapshot, then follows live deltas
tagged ``(content_generation, delta_seq)``. It applies a delta only when it is
the next one in the current generation, drops stale-generation frames,
re-reads the snapshot on a gap, a newer generation or a quiet interval, and
always ends from the committed snapshot. A generation change is announced
with a ``task`` event carrying ``reset: true`` and the replacement content.
Disconnecting an observer never affects the task.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from orchestrator.daemon import now_rfc3339, sse
from orchestrator.tasks.runner import live_channel
from orchestrator.tasks.states import TERMINAL_STATUSES, TaskStatus
from orchestrator.tasks.store import TaskSnapshot, TaskStore

logger = logging.getLogger(__name__)

#: How long the observer waits for a live message before re-reading the snapshot.
POLL_S = 2.0

_TERMINAL_MESSAGES = {
    "uncertain_effect": (
        "This task was interrupted after an action that may already have happened. "
        "Check its result before retrying."
    ),
    "interrupted": "This task was interrupted and could not be completed.",
}


class _Observation:
    def __init__(self, conversation_id: str, request_id: str) -> None:
        self.conversation_id = conversation_id
        self.request_id = request_id
        self.generation = 0
        self.delta_seq = 0
        self.displayed = ""
        self._counter = 0

    def frame(self, event: str, data: dict[str, Any], evt_id: str | None = None) -> str:
        self._counter += 1
        return sse(
            event,
            {
                "type": event,
                "id": evt_id or f"evt_{event}_{self._counter:06d}",
                "ts": now_rfc3339(),
                "conversation_id": self.conversation_id,
                "request_id": self.request_id,
                "data": data,
            },
        )

    def task_frame(self, snapshot: TaskSnapshot, *, reset: bool = False) -> str:
        data: dict[str, Any] = {
            "task_id": str(snapshot.task_id),
            "status": snapshot.status.value,
            "content_generation": snapshot.content_generation,
        }
        if reset:
            data["reset"] = True
            data["content"] = snapshot.content
        return self.frame("task", data)

    def catch_up(self, snapshot: TaskSnapshot) -> list[str]:
        """Frames that bring the client from what it shows to ``snapshot``."""
        frames: list[str] = []
        replaced = (
            snapshot.content_generation != self.generation
            or not snapshot.content.startswith(self.displayed)
        )
        if replaced and self.displayed:
            frames.append(self.task_frame(snapshot, reset=True))
            self.displayed = snapshot.content
        elif snapshot.content.startswith(self.displayed) and len(snapshot.content) > len(
            self.displayed
        ):
            frames.append(self.frame("token", {"text": snapshot.content[len(self.displayed) :]}))
            self.displayed = snapshot.content
        self.generation = snapshot.content_generation
        self.delta_seq = max(self.delta_seq, snapshot.content_delta_seq)
        return frames

    def terminal(self, snapshot: TaskSnapshot) -> list[str]:
        status = snapshot.status
        frames = [self.task_frame(snapshot)]
        if status is TaskStatus.COMPLETED:
            frames.append(
                self.frame(
                    "final",
                    {"text": snapshot.content, "finish_reason": "stop"},
                    evt_id="evt_final",
                )
            )
        elif status in (TaskStatus.FAILED, TaskStatus.NEEDS_ATTENTION):
            code = snapshot.terminal_code or "failed"
            frames.append(
                self.frame(
                    "error",
                    {
                        "code": code,
                        "message": _TERMINAL_MESSAGES.get(
                            code, "This task could not be completed."
                        ),
                        "retryable": status is TaskStatus.FAILED,
                    },
                    evt_id="evt_error",
                )
            )
        done = {"completed": "completed", "cancelled": "cancelled"}.get(status.value, "error")
        data: dict[str, Any] = {"status": done}
        if snapshot.terminal_code:
            data["reason"] = snapshot.terminal_code
        frames.append(self.frame("done", data, evt_id="evt_done"))
        return frames


async def observe_task(
    store: TaskStore,
    redis: Any,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    request_id: str,
    poll_s: float | None = None,
) -> AsyncIterator[str]:
    """Yield chat SSE frames for ``task_id`` until it reaches a terminal status."""
    poll_s = POLL_S if poll_s is None else poll_s
    pubsub = None
    if redis is not None:
        try:
            pubsub = redis.pubsub()
            # Subscribe before the snapshot so no delta can fall between them.
            await pubsub.subscribe(live_channel(task_id))
        except Exception:
            logger.info("Live task updates unavailable; observing by snapshot")
            pubsub = None
    try:
        snapshot = await store.snapshot(user_id, task_id)
        if snapshot is None:
            return
        view = _Observation(f"conv_{snapshot.conversation_id}", request_id)
        yield view.task_frame(snapshot)
        yield view.frame(
            "conversation",
            {"conversation_id": str(snapshot.conversation_id)},
            evt_id="evt_conversation",
        )
        view.generation = snapshot.content_generation
        for frame in view.catch_up(snapshot):
            yield frame
        while snapshot.status not in TERMINAL_STATUSES:
            message = await _next_message(pubsub, poll_s)
            resync = message is None
            if message is not None:
                kind = message.get("t")
                generation = int(message.get("gen") or 0)
                if generation < view.generation:
                    continue  # stale attempt
                if kind == "delta" and generation == view.generation:
                    seq = int(message.get("seq") or 0)
                    text = message.get("text")
                    if seq <= view.delta_seq:
                        continue  # already included in the snapshot
                    if seq == view.delta_seq + 1 and isinstance(text, str):
                        view.delta_seq = seq
                        view.displayed += text
                        yield view.frame("token", {"text": text})
                        continue
                    resync = True  # gap
                elif kind == "frame" and generation == view.generation:
                    frame = message.get("frame")
                    if isinstance(frame, str):
                        yield frame
                    continue
                else:
                    resync = True  # newer generation or terminal
            if resync:
                refreshed = await store.snapshot(user_id, task_id)
                if refreshed is None:
                    return
                snapshot = refreshed
                for frame in view.catch_up(snapshot):
                    yield frame
        for frame in view.terminal(snapshot):
            yield frame
    finally:
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await pubsub.aclose()


async def _next_message(pubsub: Any, poll_s: float) -> dict[str, Any] | None:
    if pubsub is None:
        await asyncio.sleep(poll_s)
        return None
    try:
        raw = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_s)
    except Exception:
        await asyncio.sleep(poll_s)
        return None
    if not raw or raw.get("type") != "message":
        return None
    payload = raw.get("data")
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
