"""Observe a durable task as the existing chat SSE stream (DURABLE_REQUEST_DESIGN §8).

The observer starts from the owner-scoped snapshot, then follows live deltas
tagged ``(content_generation, delta_seq)``. It applies a delta only when it is
the next one in the current generation, drops stale-generation frames,
re-reads the snapshot on a gap, a newer generation or a fixed deadline, and
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
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from orchestrator.daemon import now_rfc3339, sse
from orchestrator.tasks.fence import outcome_of_tool_result
from orchestrator.tasks.runner import live_channel
from orchestrator.tasks.states import TERMINAL_STATUSES, TaskStatus
from orchestrator.tasks.store import TaskEvent, TaskSnapshot, TaskStore

logger = logging.getLogger(__name__)

#: Failed outcomes a new submission may reasonably succeed at (transient
#: capacity, an interrupted attempt, an internal error). Budget, policy,
#: route and account outcomes are not advertised as retryable.
RETRYABLE_TERMINAL_CODES = frozenset(
    {
        "interrupted",
        "internal_error",
        "rate_limited",
        "concurrency_exceeded",
        "capacity_unavailable",
    }
)

#: How often a long-lived observer re-checks that its caller is still authorised.
REAUTH_S = 15.0

#: Fixed catch-up cadence, independent of live traffic.
POLL_S = 2.0

_TERMINAL_MESSAGES = {
    "uncertain_effect": (
        "This task was interrupted after an action that may already have happened. "
        "Check its result before retrying."
    ),
    "interrupted": "This task was interrupted and could not be completed.",
}


class _Observation:
    def __init__(
        self, conversation_id: str, request_id: str, *, supports_reset: bool = True
    ) -> None:
        self.conversation_id = conversation_id
        self.request_id = request_id
        self.supports_reset = supports_reset
        self.needs_reload = False
        self.generation = 0
        self.delta_seq = 0
        self.displayed = ""
        #: Highest persisted event already replayed to this client.
        self.event_seq = 0
        self.finished_operations: dict[tuple[int, str], int] = {}
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
            "regenerated_after_interruption": snapshot.regenerated_after_interruption,
        }
        if reset:
            data["reset"] = True
            data["content"] = snapshot.content
        return self.frame("task", data)

    def catch_up(self, snapshot: TaskSnapshot) -> list[str]:
        """Frames that bring the client from what it shows to ``snapshot``.

        Text and sequence always come from the same snapshot, so after a
        generation change the next applied delta is the new generation's
        ``snapshot.content_delta_seq + 1``.
        """
        frames: list[str] = []
        content = snapshot.content
        if snapshot.content_generation != self.generation:
            if self.displayed and not self.supports_reset:
                # This client would append the regenerated text to what it
                # already shows. End its stream honestly instead; reopening
                # the conversation shows the saved result.
                self.needs_reload = True
                return self._reload_frames()
            if self.displayed or self.generation:
                frames.append(self.task_frame(snapshot, reset=True))
            elif content:
                frames.append(
                    self.frame(
                        "token",
                        {
                            "text": content,
                            "content_generation": snapshot.content_generation,
                        },
                    )
                )
            self.displayed = content
            self.generation = snapshot.content_generation
            self.delta_seq = snapshot.content_delta_seq
            return frames
        if content.startswith(self.displayed):
            if len(content) > len(self.displayed):
                frames.append(
                    self.frame(
                        "token",
                        {
                            "text": content[len(self.displayed) :],
                            "content_generation": snapshot.content_generation,
                        },
                    )
                )
                self.displayed = content
                self.delta_seq = snapshot.content_delta_seq
        elif self.displayed.startswith(content):
            # The snapshot lags what live deltas already showed (content is
            # persisted about once a second): keep the newer view.
            pass
        else:
            frames.append(self.task_frame(snapshot, reset=True))
            self.displayed = content
            self.delta_seq = snapshot.content_delta_seq
        return frames

    def _reload_frames(self) -> list[str]:
        return [
            self.frame(
                "error",
                {
                    "code": "task_regenerating",
                    "message": "This answer is being regenerated after an "
                    "interruption. Reopen the conversation to see it.",
                    "retryable": False,
                },
                evt_id="evt_error",
            ),
            self.frame("done", {"status": "error", "reason": "task_regenerating"}),
        ]

    def terminal(self, snapshot: TaskSnapshot) -> list[str]:
        status = snapshot.status
        # The terminal snapshot is authoritative: live text it did not commit
        # (for example a result withheld for a suspended account) is replaced.
        # A lagging snapshot is only tolerated while the task is running.
        stale = self.displayed != snapshot.content
        if stale and not self.supports_reset and not snapshot.content.startswith(self.displayed):
            # This client cannot replace what it shows: tell it to reopen the
            # conversation, which shows the committed result.
            return self._reload_frames()
        reset = stale and self.supports_reset
        frames = [self.task_frame(snapshot, reset=reset)]
        if reset:
            self.displayed = snapshot.content
        if status is TaskStatus.COMPLETED:
            frames.append(
                self.frame(
                    "final",
                    {
                        "text": snapshot.content,
                        "finish_reason": "stop",
                        "content_generation": snapshot.content_generation,
                    },
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
                        "retryable": status is TaskStatus.FAILED
                        and code in RETRYABLE_TERMINAL_CODES,
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
    authorized: Callable[[], Awaitable[bool]] | None = None,
    supports_reset: bool = True,
) -> AsyncIterator[str]:
    """Yield chat SSE frames for ``task_id`` until it reaches a terminal status.

    ``authorized`` is re-checked every ``REAUTH_S``: an observer whose device
    or session was revoked, or whose access expired, is closed (the task
    keeps running; the client reattaches with fresh credentials).
    ``supports_reset=False`` marks a client that cannot replace text it has
    shown; on a generation change it is told to reload instead of receiving
    a reset it would render as appended text.
    """
    poll_s = POLL_S if poll_s is None else poll_s
    loop = asyncio.get_running_loop()
    next_auth_check = loop.time() + REAUTH_S
    pubsub = None
    if redis is not None:
        try:
            pubsub = redis.pubsub()
            # Subscribe before the snapshot so no delta can fall between them.
            await pubsub.subscribe(live_channel(task_id))
        except Exception:
            logger.info("Live task updates unavailable; observing by snapshot")
            if pubsub is not None:
                # A failed subscribe may already hold a pool connection.
                await _close_pubsub(pubsub)
            pubsub = None
    try:
        snapshot = await store.snapshot(user_id, task_id)
        if snapshot is None:
            return
        view = _Observation(
            f"conv_{snapshot.conversation_id}", request_id, supports_reset=supports_reset
        )
        yield view.task_frame(snapshot)
        yield view.frame(
            "conversation",
            {"conversation_id": str(snapshot.conversation_id)},
            evt_id="evt_conversation",
        )
        view.generation = snapshot.content_generation
        for frame in view.catch_up(snapshot):
            yield frame
        # Tool progress the client missed while detached (#472): the current
        # generation's persisted events, before switching to live updates.
        async for frame in _drain_events(
            store, user_id, task_id, view, snapshot, authorized=authorized
        ):
            yield frame
        next_catch_up = loop.time() + poll_s
        pending: deque[dict[str, Any]] = deque()
        while snapshot.status not in TERMINAL_STATUSES:
            if authorized is not None and loop.time() >= next_auth_check:
                next_auth_check = loop.time() + REAUTH_S
                if not await authorized():
                    return
            message = (
                pending.popleft()
                if pending
                else await _next_message(pubsub, max(0, next_catch_up - loop.time()))
            )
            # A fixed deadline, not a quiet timeout: steady live traffic cannot
            # starve recovery of a committed event whose publish was lost.
            resync = message is None or loop.time() >= next_catch_up
            if message is not None:
                kind = message.get("t")
                raw_generation = message.get("gen")
                generation = (
                    raw_generation
                    if isinstance(raw_generation, int) and not isinstance(raw_generation, bool)
                    else 0
                )
                if kind == "delta" and generation == view.generation and not resync:
                    raw_seq = message.get("seq")
                    seq = (
                        raw_seq if isinstance(raw_seq, int) and not isinstance(raw_seq, bool) else 0
                    )
                    text = message.get("text")
                    if seq <= view.delta_seq:
                        continue  # deadline still applies on the next iteration
                    if seq == view.delta_seq + 1 and isinstance(text, str):
                        view.delta_seq = seq
                        view.displayed += text
                        yield view.frame(
                            "token",
                            {
                                "text": text,
                                "content_generation": view.generation,
                            },
                        )
                        continue
                    resync = True  # gap
                elif kind == "frame" and generation == view.generation:
                    event_seq = message.get("seq")
                    if isinstance(event_seq, int) and not isinstance(event_seq, bool):
                        # Redis sequence is only a hint. Even an out-of-order
                        # later frame must drain the DB gap, never advance it.
                        # Its payload can enrich only the matching DB projection.
                        resync = resync or event_seq > view.event_seq
                    elif not resync:
                        frame = _tag_live_frame(message.get("frame"), view.generation)
                        if frame is not None:
                            yield frame
                else:
                    resync = resync or generation > view.generation or kind == "terminal"
            if resync:
                refreshed = await store.snapshot(user_id, task_id)
                if refreshed is None:
                    return
                snapshot = refreshed
                previous_generation = view.generation
                for frame in view.catch_up(snapshot):
                    yield frame
                if previous_generation != view.generation:
                    yield view.task_frame(snapshot)
                if view.needs_reload:
                    return
                # Consider already-ready sibling frames before projecting the
                # watermark. Keep non-tool messages for normal processing; do
                # not wait for future content or let traffic starve DB recovery.
                while pubsub is not None and len(pending) < REPLAY_EVENT_LIMIT:
                    ready = await _next_message(pubsub, 0)
                    if ready is None:
                        break
                    pending.append(ready)
                async for frame in _drain_events(
                    store,
                    user_id,
                    task_id,
                    view,
                    snapshot,
                    authorized=authorized,
                    live_message=message,
                    ready_messages=tuple(pending),
                ):
                    yield frame
                next_catch_up = loop.time() + poll_s
                # The delta that revealed the gap or new generation may now be
                # exactly the next one; apply it rather than lose it.
                if (
                    message is not None
                    and message.get("t") == "delta"
                    and isinstance(message.get("gen"), int)
                    and not isinstance(message.get("gen"), bool)
                    and message.get("gen") == view.generation
                    and isinstance(message.get("seq"), int)
                    and not isinstance(message.get("seq"), bool)
                    and message.get("seq") == view.delta_seq + 1
                    and isinstance(message.get("text"), str)
                ):
                    view.delta_seq += 1
                    view.displayed += message["text"]
                    yield view.frame(
                        "token",
                        {
                            "text": message["text"],
                            "content_generation": view.generation,
                        },
                    )
        # Terminal snapshot captures the watermark; flush its whole event
        # history before final/done, including operation evidence committed
        # after an earlier terminal snapshot while the pages were draining.
        while True:
            async for frame in _drain_events(
                store, user_id, task_id, view, snapshot, authorized=authorized
            ):
                yield frame
            refreshed = await store.snapshot(user_id, task_id)
            if refreshed is None:
                return
            snapshot = refreshed
            if view.event_seq >= snapshot.event_seq:
                break
        for frame in view.terminal(snapshot):
            yield frame
    except _ObserverRevoked:
        return
    finally:
        if pubsub is not None:
            await _close_pubsub(pubsub)


#: Maximum records per durable catch-up page (not a history truncation).
REPLAY_EVENT_LIMIT = 500


async def _drain_events(
    store: TaskStore,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    view: _Observation,
    snapshot: TaskSnapshot,
    *,
    authorized: Callable[[], Awaitable[bool]] | None = None,
    live_message: dict[str, Any] | None = None,
    ready_messages: tuple[dict[str, Any], ...] = (),
) -> AsyncIterator[str]:
    """Ordered, owner-scoped authority. Cursor advances only handled records."""
    while view.event_seq < snapshot.event_seq:
        if authorized is not None and not await authorized():
            raise _ObserverRevoked
        events = await store.events_since(
            user_id, task_id, after_seq=view.event_seq, limit=REPLAY_EVENT_LIMIT
        )
        if not events:
            # Deletion/revocation or a damaged history: never jump the cursor.
            raise _ObserverRevoked
        for event in events:
            if event.seq > snapshot.event_seq:
                break
            if event.seq != view.event_seq + 1:
                raise _ObserverRevoked
            payload = event.payload
            epoch = payload.get("epoch")
            data: dict[str, Any] = {
                "replayed": True,
                "event_seq": event.seq,
                "content_generation": snapshot.content_generation,
                "lifecycle_kind": event.kind,
            }
            if isinstance(epoch, int) and not isinstance(epoch, bool):
                data["lifecycle_epoch"] = epoch
            operation_id = payload.get("operation_id")
            if isinstance(operation_id, str):
                data["operation_id"] = operation_id
            projected = "task"
            if event.kind in {"tool_call", "tool_result", "operation_finished"}:
                if (
                    not isinstance(epoch, int)
                    or isinstance(epoch, bool)
                    or epoch != snapshot.content_generation
                ):
                    # Explicitly handle old/unattributed progress without
                    # mutating the current generation's tool indicators.
                    view.event_seq = event.seq
                    continue
                name = str(payload.get("name") or payload.get("tool") or "tool")[:100]
                key = (snapshot.content_generation, name)
                if event.kind == "tool_result" and view.finished_operations.get(key, 0):
                    view.finished_operations[key] -= 1
                    if not view.finished_operations[key]:
                        del view.finished_operations[key]
                    view.event_seq = event.seq
                    continue  # operation_finished already projected its result
                data["name"] = name
                projected = "tool_call" if event.kind == "tool_call" else "tool_result"
                if projected == "tool_call":
                    data["arguments"] = {}
                else:
                    outcome = payload.get("outcome")
                    if not isinstance(outcome, str) or outcome not in {
                        "succeeded",
                        "failed",
                        "unknown",
                    }:
                        outcome = "unknown"
                    data["outcome"] = outcome
                    summary: dict[str, Any] = {"outcome": outcome}
                    if outcome != "unknown":
                        summary["success"] = outcome == "succeeded"
                    data["result"] = summary
                    if event.kind == "operation_finished":
                        view.finished_operations[key] = view.finished_operations.get(key, 0) + 1
                live = _live_tool_payload(live_message, event, snapshot, view)
                if live is None:
                    for ready in ready_messages:
                        live = _live_tool_payload(ready, event, snapshot, view)
                        if live is not None:
                            break
                if live is not None:
                    data.update(live)
                    data["replayed"] = False
            else:
                # Historical lifecycle metadata, not a status transition.
                data.update(
                    {
                        "task_id": str(snapshot.task_id),
                        "status": snapshot.status.value,
                        "regenerated_after_interruption": snapshot.regenerated_after_interruption,
                    }
                )
            yield view.frame(projected, data, evt_id=f"evt_{projected}_r{event.seq}")
            view.event_seq = event.seq


class _ObserverRevoked(Exception):
    """Fail closed if authorization/history disappears during catch-up."""


def _live_tool_payload(
    message: dict[str, Any] | None,
    event: TaskEvent,
    snapshot: TaskSnapshot,
    view: _Observation,
) -> dict[str, Any] | None:
    """Use available content, never Redis attribution/cursor/state authority.

    The owner-scoped contiguous DB drain has already validated the record. A
    full live frame can supply only that record's arguments or result, not its
    envelope or authoritative fields. Late content cannot replace an emitted
    summary; material operation summaries cannot be correlated to progress by
    an operation ID in the current contract, so their existing dedup stays.
    """
    if message is None or message.get("t") != "frame":
        return None
    if event.kind not in {"tool_call", "tool_result"}:
        return None
    for value, expected in (
        (message.get("seq"), event.seq),
        (message.get("gen"), snapshot.content_generation),
        (event.payload.get("epoch"), snapshot.content_generation),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value != expected:
            return None
    frame = message.get("frame")
    if not isinstance(frame, str):
        return None
    from orchestrator.tasks.runner import _parse_frame

    kind, envelope = _parse_frame(frame)
    if (
        kind != event.kind
        or envelope.get("type") != event.kind
        or envelope.get("conversation_id") != view.conversation_id
    ):
        return None
    data = envelope.get("data")
    if not isinstance(data, dict) or data.get("name") != event.payload.get("name"):
        return None
    for value, expected in (
        (data.get("event_seq"), event.seq),
        (data.get("content_generation"), snapshot.content_generation),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value != expected:
            return None
    if event.kind == "tool_call":
        arguments = data.get("arguments")
        return {"arguments": arguments} if isinstance(arguments, dict) else None
    outcome = event.payload.get("outcome")
    if (
        not isinstance(outcome, str)
        or outcome not in {"succeeded", "failed", "unknown"}
        or data.get("outcome") != outcome
        or "result" not in data
        or outcome_of_tool_result(data.get("name"), data["result"]) != outcome
    ):
        return None
    return {"result": data["result"]}


def _tag_live_frame(frame: Any, generation: int) -> str | None:
    if not isinstance(frame, str):
        return None
    from orchestrator.tasks.runner import _parse_frame

    event, envelope = _parse_frame(frame)
    if event not in {"routing", "thinking", "metadata"}:
        return None  # tool progress must have durable attribution
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    data["content_generation"] = generation
    return sse(event, envelope)


async def _close_pubsub(pubsub: Any) -> None:
    """Release a pub/sub connection; closing proceeds even if unsubscribing fails."""
    with contextlib.suppress(Exception):
        await pubsub.unsubscribe()
    with contextlib.suppress(Exception):
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
    if raw is None:
        return None
    # An empty mapping represents a consumed unusable item, not an empty
    # transport. Ready-batch collection must continue past it, counting it
    # toward the same bound so malformed traffic cannot starve DB recovery.
    if not isinstance(raw, dict) or raw.get("type") != "message":
        return {}
    payload = raw.get("data")
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    if not isinstance(payload, str):
        return {}
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
