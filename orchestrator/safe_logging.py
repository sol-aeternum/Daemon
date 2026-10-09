"""Managed Python log sinks: never format legacy messages or exceptions.

This is an output contract for the supported launcher, not protection against
native fd writes or an in-process actor replacing logging configuration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from collections.abc import Callable
from typing import TextIO

_stream: TextIO | None = None
_managed_level = logging.NOTSET
_routing_validator: Callable[[str, dict[str, object]], dict[str, object]] | None = None
_LEVELS = {10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL"}
_STAGES = frozenset({"startup", "runtime", "shutdown", "uncaught", "thread", "asyncio"})


def register_routing_validator(
    validator: Callable[[str, dict[str, object]], dict[str, object]],
) -> None:
    """Register the application-owned semantic schema, not a record exemption."""
    global _routing_validator
    _routing_validator = validator


def validated_payload(payload: object) -> dict[str, object]:
    """Revalidate each output, including purportedly safe LogRecord extras."""
    if type(payload) is not dict:
        raise ValueError("invalid diagnostic record")
    kind = payload.get("kind")
    if type(kind) is not str:
        raise ValueError("invalid diagnostic kind")
    if kind == "routing" and set(payload) == {"kind", "fields"}:
        fields = payload["fields"]
        if type(fields) is not dict or _routing_validator is None:
            raise ValueError("invalid routing diagnostic")
        event = fields.get("event")
        if type(event) is not str or any(type(key) is not str for key in fields):
            raise ValueError("invalid routing diagnostic")
        return {
            "kind": "routing",
            **_routing_validator(event, {k: v for k, v in fields.items() if k != "event"}),
        }
    if kind in {"service_start", "service_stop"} and set(payload) == {"kind", "role"}:
        role = payload["role"]
        if type(role) is str and role in {"backend", "worker"}:
            return {"event": kind, "role": role}
    if kind == "failure" and set(payload) == {"kind", "stage"}:
        stage = payload["stage"]
        if type(stage) is str and stage in _STAGES:
            return {"event": "failure", "stage": stage}
    raise ValueError("invalid diagnostic fields")


class SafeHandler(logging.StreamHandler):
    """Fresh JSON only; neither Formatter nor handleError may render the record."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.__dict__.get("levelno")
            output: dict[str, object] = {
                "event": "legacy_log",
                "level": _LEVELS.get(level, "UNKNOWN") if type(level) is int else "UNKNOWN",
            }
            payload = record.__dict__.get("_daemon_event")
            if payload is not None:
                try:
                    output.update(validated_payload(payload))
                except Exception:
                    output["event"] = "invalid_diagnostic"
            self.stream.write(json.dumps(output, allow_nan=False, separators=(",", ":")) + "\n")
            self.flush()
        except Exception:
            self.handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:
        # Python's default dumps msg/args/traceback to stderr, even if writing
        # the original output failed. Never fall back to that implementation.
        try:
            self.stream.write('{"event":"logging_failure"}\n')
            self.flush()
        except Exception:
            pass


def handler(stream: TextIO | None = None) -> SafeHandler:
    sink = SafeHandler(stream if stream is not None else (_stream or sys.stderr))
    sink.setLevel(_managed_level)
    return sink


def configure(stream: TextIO, level: str = "INFO") -> None:
    """Replace discovered sinks; call after dependency/config initialization."""
    global _stream, _managed_level
    _stream = stream
    _managed_level = getattr(logging, level) if level in _LEVELS.values() else logging.INFO
    safe = handler(stream)
    loggers = [logging.getLogger()]
    loggers.extend(
        value
        for value in logging.root.manager.loggerDict.values()
        if isinstance(value, logging.Logger)
    )
    for logger in loggers:
        for existing in logger.handlers[:]:
            logger.removeHandler(existing)
        logger.propagate = True
    root = logging.getLogger()
    level_number = _managed_level
    safe.setLevel(level_number)
    root.addHandler(safe)
    root.setLevel(level_number)
    fallback = handler(stream)
    fallback.setLevel(level_number)
    logging.lastResort = fallback


def event(kind: str, **fields: object) -> None:
    payload = {"kind": kind, **fields}
    # Fixed control diagnostics survive a quiet LOG_LEVEL (including fatal
    # import errors). They still pass the same schema and fresh-output sink.
    record = logging.LogRecord(
        "daemon.diagnostic",
        logging.ERROR if kind == "failure" else logging.INFO,
        "",
        0,
        "",
        (),
        None,
    )
    record.__dict__["_daemon_event"] = payload
    handler().handle(record)


def install_failure_hooks() -> None:
    def uncaught(_type: object, _value: object, _traceback: object) -> None:
        event("failure", stage="uncaught")

    def thread_failure(_args: object) -> None:
        event("failure", stage="thread")

    sys.excepthook = uncaught
    threading.excepthook = thread_failure


def protect_loop(loop: asyncio.AbstractEventLoop) -> None:
    def failure(_loop: asyncio.AbstractEventLoop, _context: dict[str, object]) -> None:
        event("failure", stage="asyncio")

    loop.set_exception_handler(failure)
