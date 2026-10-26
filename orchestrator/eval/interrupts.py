"""Cooperative SIGINT/SIGTERM/SIGHUP handling for long benchmark phases.

First signal: finish the in-flight item, checkpoint, exit resumable.
Second signal: cancel the in-flight item immediately; the checkpoint still holds
every completed item and resume rolls back the abandoned one.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

HANDLED_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class HarnessInterrupted(RuntimeError):
    """Raised between items after a stop was requested; the run is resumable."""

    def __init__(self, signal_name: str) -> None:
        super().__init__(f"Stopped by {signal_name}; resume with the same command")
        self.signal_name = signal_name


class StopController:
    def __init__(self) -> None:
        self.signal_name: str | None = None
        self.signal_number: int | None = None
        self._install: Callable[[], None] | None = None

    def reassert_handlers(self) -> None:
        """Re-install the harness's handlers after a library replaced them."""
        if self._install is not None:
            self._install()

    @property
    def requested(self) -> bool:
        return self.signal_name is not None

    def request(self, signum: int) -> None:
        self.signal_number = signum
        self.signal_name = signal.Signals(signum).name

    def raise_if_requested(self) -> None:
        if self.signal_name is not None:
            raise HarnessInterrupted(self.signal_name)


async def run_interruptible(
    make_coro: Callable[[StopController], Awaitable[T]],
) -> tuple[T | None, StopController]:
    """Run ``make_coro(controller)`` with signal handlers installed.

    Returns ``(result, controller)``; result is None when interrupted.
    """
    loop = asyncio.get_running_loop()
    controller = StopController()
    task: asyncio.Task[Any] = asyncio.ensure_future(make_coro(controller))

    def on_signal(signum: int) -> None:
        if not controller.requested:
            controller.request(signum)
            logger.warning(
                "%s received: finishing the in-flight item, then exiting resumable "
                "(send again to abandon it now)",
                controller.signal_name,
            )
            return
        logger.warning("Second signal: abandoning the in-flight item")
        task.cancel()

    installed: list[signal.Signals] = []

    def install() -> None:
        for sig in HANDLED_SIGNALS:
            try:
                loop.add_signal_handler(sig, on_signal, int(sig))
            except (NotImplementedError, RuntimeError, ValueError):
                continue
            if sig not in installed:
                installed.append(sig)

    install()
    controller._install = install
    try:
        try:
            return await task, controller
        except HarnessInterrupted:
            return None, controller
        except asyncio.CancelledError:
            if controller.requested:
                return None, controller
            raise
    finally:
        controller._install = None
        for sig in installed:
            loop.remove_signal_handler(sig)
