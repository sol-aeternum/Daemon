"""Negative execution requirement, not a dispatch permit or a replacement runtime."""

from __future__ import annotations

from contextvars import ContextVar
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
import inspect

from orchestrator.tasks._invocation import invocation
from orchestrator.services.account_control.transaction import ObservationBlocked

if TYPE_CHECKING:
    from orchestrator.attempt_lifecycle import AttemptIdentity
    from orchestrator.services.restore_history.effects import HistoryEffects
    from orchestrator.tasks._invocation import InferenceInvocation
    from orchestrator.tasks.composition import WorkerComposition
    from orchestrator.tasks.inference import InferenceRecovery
    from orchestrator.tools.web_search import WebSearchTool
    from orchestrator.services.restore_history.source_execution import SourceRun
    from orchestrator.services.restore_history.owned_index import OwnedIndexSearchTool


# Set only by the original runner, before claim, independently of compute arguments.
required_history: ContextVar[WorkerComposition | None] = ContextVar(
    "required_task_history", default=None
)


def text_only_execution() -> bool:
    # A lost current marker cannot expose tools/background work while a scope
    # still retains its independent requirement. This is negative admission,
    # never an alternative way of manufacturing a binding or a send permit.
    from orchestrator.compute_runtime import _scope

    current = required_history.get()
    scope = _scope.get()
    captured = scope.history_requirement if scope is not None else None
    if captured is not None and current is not captured:
        assert scope is not None
        scope.require_inference_effect()
    return current is not None or captured is not None


def research_tool() -> WebSearchTool | OwnedIndexSearchTool | None:
    """Exact original scoped research tool, never a broader registry grant."""
    from orchestrator.compute_runtime import _scope

    scope = _scope.get()
    if text_only_execution() and scope is not None:
        binding = scope.history_binding
        if binding is not None and binding.research_tool is not None:
            scope.require_inference_effect()
            return binding.research_tool
    return None


def source_run() -> SourceRun | None:
    from orchestrator.compute_runtime import _scope

    scope = _scope.get()
    if text_only_execution() and scope is not None and scope.history_binding is not None:
        scope.require_inference_effect()
        return scope.history_binding.source_run
    return None


@dataclass(frozen=True, slots=True)
class ComputeHistoryBinding:
    composition: WorkerComposition
    effects: HistoryEffects
    recovery: InferenceRecovery
    identity: AttemptIdentity
    state: InferenceInvocation
    research_tool: WebSearchTool | OwnedIndexSearchTool | None = None
    research_model_transport: Callable[..., Awaitable[Any]] | None = None
    source_run: SourceRun | None = None

    def validate(self, requirement: WorkerComposition | None) -> None:
        from orchestrator.services.restore_history.effects import HistoryEffects
        from orchestrator.tools.web_search import WebSearchTool
        from orchestrator.services.restore_history.source_execution import SourceRun
        from orchestrator.services.restore_history.owned_index import OwnedIndexSearchTool
        from orchestrator.services.restore_history.terminal_projection import (
            InboxOwnedIndexHistoryProjection,
        )
        import httpx

        current = invocation.get()
        try:
            if type(self.effects) is not HistoryEffects:
                raise ObservationBlocked("history_binding_unavailable")
            self.composition.validate()
            self.effects._check()
            if (
                requirement is not self.composition
                or required_history.get() is not requirement
                or not self.composition.requires_history
                or self.effects._tasks is not self.composition.tasks
                or self.recovery is not self.composition.recovery
                or current is not self.state
                or self.effects._blocked
                or self.effects._history._blocked
                or self.effects._history._closing
                or self.effects._history._closed
                or (self.effects._projection.profile in ("inbox-research-v1", "inbox-sources-v1"))
                != (self.research_tool is not None)
                or (self.effects._projection.profile == "inbox-sources-v1")
                != (type(self.source_run) is SourceRun)
                or (self.source_run is not None and self.source_run.effects is not self.effects)
                or (self.research_tool is not None) != (self.research_model_transport is not None)
                or (
                    self.research_tool is not None
                    and (
                        (
                            type(self.effects._projection) is InboxOwnedIndexHistoryProjection
                            and (
                                type(self.research_tool) is not OwnedIndexSearchTool
                                or self.research_tool._projection is not self.effects._projection
                            )
                        )
                        or (
                            type(self.effects._projection) is not InboxOwnedIndexHistoryProjection
                            and (
                                type(self.research_tool) is not WebSearchTool
                                or type(self.research_tool._transport) is not httpx.MockTransport
                                or self.research_tool._pacer is None
                                or self.research_tool.provider != "brave"
                            )
                        )
                        or not inspect.iscoroutinefunction(self.research_model_transport)
                    )
                )
            ):
                raise ObservationBlocked("history_binding_unavailable")
            self.state.require_effect()
        except BaseException:
            self.state.block("history_binding_unavailable")
            if current is not None:
                current.block("history_binding_unavailable")
            raise
