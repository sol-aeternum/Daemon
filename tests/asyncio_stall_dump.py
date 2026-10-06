"""Dump pending asyncio tasks when a test phase stalls (issue #454 diagnostics).

faulthandler prints thread stacks only. A stalled async fixture teardown shows
the event loop parked in ``selectors.select`` with no hint of which coroutine
it is waiting for. When ``DAEMON_PYTEST_ASYNCIO_DUMP_S`` is set, a watchdog
thread notices a setup/call/teardown phase that has run longer than that many
seconds, finds the event loop the main thread is running, and schedules a dump
of every pending task's await chain on that loop (the loop is idle in
``select``, so the callback runs). Diagnostics only: nothing is cancelled,
retried or timed out, and only task stacks are printed (no locals).
"""

from __future__ import annotations

import asyncio
import gc
import io
import inspect
import os
import sys
import threading
import time
from collections.abc import Generator
from typing import Any

import pytest

_ENV = "DAEMON_PYTEST_ASYNCIO_DUMP_S"
_MAX_SUSPENDED = 300


def _threshold() -> float | None:
    raw = os.environ.get(_ENV, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _running_loop_in(thread_id: int) -> asyncio.AbstractEventLoop | None:
    frame = sys._current_frames().get(thread_id)
    while frame is not None:
        candidate = frame.f_locals.get("self")
        if isinstance(candidate, asyncio.AbstractEventLoop) and candidate.is_running():
            return candidate
        frame = frame.f_back
    return None


class _Out(io.StringIO):
    """Buffer a dump, then write it past pytest's output capture.

    Capture redirects fd 2 during test phases, so (like pytest's faulthandler
    plugin) the dump goes to a duplicate of the real stderr taken at
    configure time, when capture is suspended.
    """

    def flush(self) -> None:
        text = self.getvalue()
        self.seek(0)
        self.truncate()
        if text:
            os.write(_stderr_fd if _stderr_fd is not None else 2, text.encode("utf-8", "replace"))


def _dump_tasks(label: str) -> None:
    out = _Out()
    tasks = asyncio.all_tasks()
    print(f"\n[asyncio-stall-dump] {label}: {len(tasks)} pending task(s)", file=out)
    for task in tasks:
        print(f"[asyncio-stall-dump] --- {task!r}", file=out)
        formatter = getattr(asyncio, "format_call_graph", None)
        try:
            if formatter is not None:
                print(formatter(task), file=out)
            else:
                task.print_stack(file=out)
        except Exception as exc:  # diagnostics must never raise into the loop
            print(f"[asyncio-stall-dump] could not format task: {exc!r}", file=out)
    # Task call graphs stop at async generators (such as yield fixtures), so
    # also list every suspended coroutine and async generator by location.
    suspended = []
    for obj in gc.get_objects():
        if inspect.iscoroutine(obj):
            frame, running = obj.cr_frame, obj.cr_running
        elif inspect.isasyncgen(obj):
            frame, running = obj.ag_frame, obj.ag_running
        else:
            continue
        if frame is not None and not running:
            code = frame.f_code
            suspended.append(f"{code.co_filename}:{frame.f_lineno} in {code.co_qualname}")
    print(f"[asyncio-stall-dump] {len(suspended)} suspended coroutine(s):", file=out)
    for line in sorted(suspended)[:_MAX_SUSPENDED]:
        print(f"[asyncio-stall-dump]   {line}", file=out)
    out.flush()


class _Watchdog:
    def __init__(self, threshold_s: float) -> None:
        self._threshold = threshold_s
        self._lock = threading.Lock()
        self._phase: tuple[str, float] | None = None
        self._dumped = False
        self._main = threading.main_thread().ident
        thread = threading.Thread(target=self._run, name="asyncio-stall-dump", daemon=True)
        thread.start()

    def enter(self, label: str) -> None:
        with self._lock:
            self._phase = (label, time.monotonic())
            self._dumped = False

    def leave(self) -> None:
        with self._lock:
            self._phase = None

    def _run(self) -> None:
        while True:
            time.sleep(min(5.0, self._threshold / 4))
            with self._lock:
                phase, dumped = self._phase, self._dumped
            if phase is None or dumped or self._main is None:
                continue
            label, started = phase
            if time.monotonic() - started < self._threshold:
                continue
            with self._lock:
                self._dumped = True
            loop = _running_loop_in(self._main)
            if loop is None:
                out = _Out()
                print(
                    f"\n[asyncio-stall-dump] {label}: stalled, no running event loop found",
                    file=out,
                )
                out.flush()
                continue
            try:
                loop.call_soon_threadsafe(_dump_tasks, label)
            except RuntimeError:
                pass


_watchdog: _Watchdog | None = None
_stderr_fd: int | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _watchdog, _stderr_fd
    threshold = _threshold()
    if threshold is not None and _watchdog is None:
        _stderr_fd = os.dup(2)
        _watchdog = _Watchdog(threshold)


def _phase(item: pytest.Item, name: str) -> Generator[None, Any, None]:
    if _watchdog is None:
        yield
        return
    _watchdog.enter(f"{item.nodeid} [{name}]")
    try:
        yield
    finally:
        _watchdog.leave()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, Any, None]:
    yield from _phase(item, "setup")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, None]:
    yield from _phase(item, "call")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, Any, None]:
    yield from _phase(item, "teardown")
