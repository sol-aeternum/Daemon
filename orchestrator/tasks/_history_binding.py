"""Negative execution requirement, not a dispatch permit or a replacement runtime."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.tasks._invocation import invocation
from orchestrator.services.account_control.transaction import ObservationBlocked

if TYPE_CHECKING:
    from orchestrator.attempt_lifecycle import AttemptIdentity
    from orchestrator.services.restore_history.effects import HistoryEffects
    from orchestrator.tasks._invocation import InferenceInvocation
    from orchestrator.tasks.composition import WorkerComposition
    from orchestrator.tasks.inference import InferenceRecovery


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


@dataclass(frozen=True, slots=True)
class ComputeHistoryBinding:
    composition: WorkerComposition
    effects: HistoryEffects
    recovery: InferenceRecovery
    identity: AttemptIdentity
    state: InferenceInvocation

    def validate(self, requirement: WorkerComposition | None) -> None:
        from orchestrator.services.restore_history.effects import HistoryEffects

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
            ):
                raise ObservationBlocked("history_binding_unavailable")
            self.state.require_effect()
        except BaseException:
            self.state.block("history_binding_unavailable")
            if current is not None:
                current.block("history_binding_unavailable")
            raise
