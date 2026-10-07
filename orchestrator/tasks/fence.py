"""Effect fence for tools run by durable tasks (DURABLE_REQUEST_DESIGN §9).

Slice 1 treats every tool not shown to be safe to repeat as *material*. Before
a material tool runs, its operation is committed under the task's lease fence,
so a later retry can tell "never attempted" from "may have happened" and stop
at ``needs_attention`` instead of repeating it.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from orchestrator.tasks.store import EffectRefused, LeaseLost, TaskStore
from orchestrator.tools.registry import Tool

logger = logging.getLogger(__name__)

#: Tools whose repetition has no material external effect: reads, arithmetic
#: and the clock. Anything else, including any tool added later, is material
#: until it is deliberately classified (slice 3 maps every tool's recovery class).
REPEATABLE_TOOLS = frozenset(
    {
        "calculate",
        "get_time",
        "memory_read",
        "memory_reflect",
        "reminder_list",
        "web_fetch",
        "web_search",
    }
)

#: Lease time that must remain when a material operation is recorded.
MIN_LEASE_MARGIN_S = 15.0


def is_material(tool_name: str) -> bool:
    return tool_name not in REPEATABLE_TOOLS


def _target_summary(tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """What the operation was aimed at, without bodies or credentials."""
    summary: dict[str, Any] = {"tool": tool_name}
    for key in ("url", "method", "channel", "topic", "title", "when", "name", "action"):
        value = kwargs.get(key)
        if isinstance(value, (str, int, float, bool)):
            summary[key] = value if not isinstance(value, str) else value[:200]
    return summary


class FencedTool(Tool):
    """Proxy that records a material operation before delegating."""

    def __init__(
        self,
        inner: Tool,
        store: TaskStore,
        task_id: uuid.UUID,
        epoch: int,
        on_lease_lost: Callable[[], None] | None = None,
    ) -> None:
        self._inner = inner
        self._store = store
        self._task_id = task_id
        self._epoch = epoch
        self._on_lease_lost = on_lease_lost
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters

    def __getattr__(self, attribute: str) -> Any:
        # Optional hooks such as set_result_allowance stay on the inner tool.
        return getattr(self._inner, attribute)

    def to_openai_schema(self) -> dict[str, Any]:
        return self._inner.to_openai_schema()

    async def execute(self, **kwargs: Any) -> str:
        try:
            operation_id = await self._store.begin_operation(
                self._task_id,
                self._epoch,
                tool_name=self.name,
                target=_target_summary(self.name, kwargs),
                min_lease_margin_s=MIN_LEASE_MARGIN_S,
            )
        except LeaseLost:
            # This attempt no longer owns the task: the effect is not attempted,
            # and the attempt stops now rather than at its next heartbeat.
            if self._on_lease_lost is not None:
                self._on_lease_lost()
            return json.dumps(
                {"success": False, "error": "Not performed: this task attempt was superseded."}
            )
        except EffectRefused:
            # Cancelled, suspended or out of lease time: the effect is not attempted.
            return json.dumps(
                {"success": False, "error": "Not performed: this task can no longer act."}
            )
        try:
            result = await self._inner.execute(**kwargs)
        except BaseException:
            await self._finish(operation_id, "unknown")
            raise
        await self._finish(operation_id, "succeeded")
        return result

    async def _finish(self, operation_id: uuid.UUID, outcome: str) -> None:
        try:
            await self._store.finish_operation(operation_id, outcome=outcome)
        except Exception:
            # The started row already marks the effect as uncertain.
            logger.warning("Could not record task operation outcome", exc_info=True)


def guard_registry(
    registry: Any,
    store: TaskStore,
    task_id: uuid.UUID,
    epoch: int,
    on_lease_lost: Callable[[], None] | None = None,
) -> None:
    """Wrap every material tool in ``registry`` with the effect fence."""
    for name in registry.names():
        tool = registry.get(name)
        if tool is not None and is_material(name) and not isinstance(tool, FencedTool):
            registry.register(FencedTool(tool, store, task_id, epoch, on_lease_lost))
