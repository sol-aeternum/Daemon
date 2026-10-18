"""Shared, monotonic invocation fence; neither a receipt nor execution authority.

Context propagation shares the SAME object with nested/scheduled work. Nothing
here opens a connection, grants a send, clears a fence or proves remote cessation.
"""

from __future__ import annotations

from contextvars import ContextVar
from collections.abc import Callable
from dataclasses import dataclass, field


class InferenceInterrupted(Exception):
    """No replacement send; unlike DB uncertainty, not a mutation grant/refusal."""


@dataclass(slots=True)
class InferenceInvocation:
    _uncertainty: str | None = field(default=None, init=False)
    call_failed: bool = False
    send_check: Callable[[], None] | None = None

    @property
    def uncertainty(self) -> str | None:
        return self._uncertainty

    def block(self, code: str) -> None:
        if self.uncertainty is None:
            self._uncertainty = code

    def require_effect(self) -> None:
        if self.uncertainty is not None:
            # Lazy import avoids changing transaction module initialization.
            from orchestrator.services.account_control.transaction import ObservationBlocked

            raise ObservationBlocked(self.uncertainty)

    def require_send(self) -> None:
        self.require_effect()
        if self.call_failed:
            raise InferenceInterrupted("inference_dispatch_unresolved")
        if self.send_check is not None:
            self.send_check()


invocation: ContextVar[InferenceInvocation | None] = ContextVar(
    "task_inference_invocation", default=None
)


def require_effect() -> None:
    state = invocation.get()
    if state is not None:
        state.require_effect()


def block(code: str) -> None:
    state = invocation.get()
    if state is not None:
        state.block(code)
