from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from orchestrator import runtime, safe_logging

# Fixed synthetic content, not a credential or private input. The deliberate
# logger/print injections below verify redaction by inspecting actual sink bytes.
# Name the fixture for what it is rather than a credential source: CodeQL's
# sensitive-variable heuristic otherwise misclassifies these negative tests.
CONTENT_SENTINEL = "PRIVATE-CONTENT-ACCOUNT-URL-TOKEN"


@pytest.fixture
def isolated_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    loggers = [logging.getLogger()] + [
        value
        for value in logging.root.manager.loggerDict.values()
        if isinstance(value, logging.Logger)
    ]
    saved = [(logger, logger.handlers[:], logger.propagate, logger.level) for logger in loggers]
    last = logging.lastResort
    monkeypatch.setattr(safe_logging, "_stream", None)
    monkeypatch.setattr(safe_logging, "_managed_level", logging.NOTSET)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    stream = io.StringIO()
    yield stream
    for logger, handlers, propagate, level in saved:
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.setLevel(level)
    logging.lastResort = last


def lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


class Poison:
    def __str__(self) -> str:
        raise AssertionError("sensitive value was formatted")

    def __repr__(self) -> str:
        raise AssertionError("sensitive value was represented")


@pytest.mark.parametrize(
    "level", [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]
)
def test_legacy_sink_never_formats_payload_or_metadata(isolated_logging: io.StringIO, level: int):
    safe_logging.configure(isolated_logging, "DEBUG")
    record = logging.LogRecord(
        CONTENT_SENTINEL,
        level,
        CONTENT_SENTINEL,
        12,
        Poison(),
        (Poison(),),
        (RuntimeError, RuntimeError(CONTENT_SENTINEL), None),
    )
    record.exc_text = CONTENT_SENTINEL
    record.stack_info = CONTENT_SENTINEL
    record.__dict__["extra"] = Poison()
    logging.getLogger().handle(record)
    assert lines(isolated_logging) == [
        {"event": "legacy_log", "level": logging.getLevelName(level)}
    ]
    assert CONTENT_SENTINEL not in isolated_logging.getvalue()


@pytest.mark.parametrize(
    "payload",
    [
        Poison(),
        {"kind": CONTENT_SENTINEL},
        {"kind": "failure", "stage": CONTENT_SENTINEL},
        {"kind": "failure", "stage": "runtime", "account_id": CONTENT_SENTINEL},
        {"kind": "routing", "fields": Poison()},
    ],
)
def test_forged_safe_extras_fail_closed(isolated_logging: io.StringIO, payload: object):
    safe_logging.configure(isolated_logging)
    logging.getLogger().info(CONTENT_SENTINEL, extra={"_daemon_event": payload})
    assert lines(isolated_logging) == [{"event": "invalid_diagnostic", "level": "INFO"}]


def test_safe_positive_and_hooks(isolated_logging: io.StringIO):
    safe_logging.configure(isolated_logging)
    safe_logging.install_failure_hooks()
    safe_logging.event("service_start", role="backend")
    sys.excepthook(RuntimeError, RuntimeError(CONTENT_SENTINEL), None)
    threading.excepthook(cast(Any, Poison()))
    assert [item["event"] for item in lines(isolated_logging)] == [
        "service_start",
        "failure",
        "failure",
    ]
    assert CONTENT_SENTINEL not in isolated_logging.getvalue()


def test_existing_nonpropagating_handlers_are_rehomed(isolated_logging: io.StringIO):
    raw = io.StringIO()
    logger = logging.getLogger("test.p03.private_dependency")
    previous = logger.handlers[:], logger.propagate, logger.level
    try:
        logger.addHandler(logging.StreamHandler(raw))
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        safe_logging.configure(isolated_logging, "DEBUG")
        logger.debug("%s", Poison())
        assert raw.getvalue() == ""
        assert lines(isolated_logging) == [{"event": "legacy_log", "level": "DEBUG"}]
        logging.getLogger().handlers.clear()
        logger.warning(CONTENT_SENTINEL)
        assert lines(isolated_logging)[-1] == {"event": "legacy_log", "level": "WARNING"}
    finally:
        logger.handlers[:], logger.propagate = previous[0], previous[1]
        logger.setLevel(previous[2])


def test_output_failure_cannot_dump_record(capsys: pytest.CaptureFixture[str]):
    class Broken(io.StringIO):
        def write(self, text: str) -> int:
            raise OSError(CONTENT_SENTINEL)

    handler = safe_logging.handler(Broken())
    handler.handle(
        logging.LogRecord(
            CONTENT_SENTINEL, logging.ERROR, CONTENT_SENTINEL, 1, CONTENT_SENTINEL, (), None
        )
    )
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("fail", [False, True])
def test_import_window_discards_and_detaches_captured_stream(
    isolated_logging: io.StringIO, capsys: pytest.CaptureFixture[str], fail: bool
):
    logger = logging.getLogger("test.p03.import_installed")
    previous = logger.handlers[:], logger.propagate
    try:
        with pytest.raises(RuntimeError) if fail else __import__("contextlib").nullcontext():
            with runtime.initialization(isolated_logging):
                print(CONTENT_SENTINEL)
                print(CONTENT_SENTINEL, file=sys.stderr)
                logger.addHandler(logging.StreamHandler(sys.stderr))
                logger.propagate = False
                logger.error(CONTENT_SENTINEL)
                if fail:
                    raise RuntimeError(CONTENT_SENTINEL)
        assert capsys.readouterr() == ("", "")
        assert all(
            not getattr(getattr(handler, "stream", None), "closed", False)
            for handler in logging.getLogger().handlers
        )
        logger.error(CONTENT_SENTINEL)
        assert CONTENT_SENTINEL not in isolated_logging.getvalue()
    finally:
        logger.handlers[:], logger.propagate = previous


@pytest.mark.parametrize("role", ["backend", "worker"])
def test_launcher_failure_exit_and_no_exception_text(tmp_path: Path, role: str):
    code = f"""
from orchestrator import runtime
import logging, sys
def prepare(role, stream):
    with runtime.initialization(stream):
        print({CONTENT_SENTINEL!r})
        logging.getLogger('dependency').addHandler(logging.StreamHandler(sys.stderr))
        raise RuntimeError({CONTENT_SENTINEL!r})
runtime.launch({role!r}, prepare)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert CONTENT_SENTINEL not in result.stderr
    assert "Traceback" not in result.stderr
    assert json.loads(result.stderr.splitlines()[-1])["event"] == "failure"


def test_supported_source_commands_use_launcher():
    root = Path(__file__).parents[1]
    compose = (root / "docker-compose.yml").read_text()
    assert "command: python -m orchestrator.runtime backend" in compose
    assert "command: python -m orchestrator.runtime worker" in compose
    assert '"orchestrator.runtime", "backend"' in (root / "backend/Dockerfile").read_text()
    assert '"orchestrator.runtime", "backend"' in (root / "Dockerfile").read_text()


@pytest.mark.parametrize(
    "level,expected",
    [
        ("DEBUG", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
        ("WARNING", ["WARNING", "ERROR", "CRITICAL"]),
        ("CRITICAL", ["CRITICAL"]),
        ("", ["INFO", "WARNING", "ERROR", "CRITICAL"]),
        ("unsupported", ["INFO", "WARNING", "ERROR", "CRITICAL"]),
    ],
)
def test_log_level_controls_all_managed_sinks_but_not_fixed_events(
    isolated_logging: io.StringIO, level: str, expected: list[str]
):
    safe_logging.configure(isolated_logging, level)
    logger = logging.getLogger("test.p03.verbosity")
    logger.setLevel(logging.DEBUG)
    for number in [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]:
        logger.log(number, CONTENT_SENTINEL)
    assert [item["level"] for item in lines(isolated_logging)] == expected
    isolated_logging.truncate(0)
    isolated_logging.seek(0)
    logger.handlers[:] = [safe_logging.handler()]
    logger.propagate = False
    try:
        for number in [
            logging.DEBUG,
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        ]:
            logger.log(number, CONTENT_SENTINEL)
        assert [item["level"] for item in lines(isolated_logging)] == expected
        safe_logging.event("service_start", role="backend")
        safe_logging.event("failure", stage="startup")
        assert [item["event"] for item in lines(isolated_logging)[-2:]] == [
            "service_start",
            "failure",
        ]
    finally:
        logger.handlers.clear()
        logger.propagate = True


def test_routing_schema_is_revalidated_at_actual_sink(isolated_logging: io.StringIO):
    from orchestrator import routing_log

    safe_logging.register_routing_validator(routing_log.build_record)
    safe_logging.configure(isolated_logging, "DEBUG")
    logger = logging.getLogger("test.p03.structured")
    payloads = [
        {"event": "scope_close", "attempts": 2, "exit": "normal", "request_id": CONTENT_SENTINEL},
        {
            "event": "candidates",
            "profile": CONTENT_SENTINEL,
            "route_ids": [CONTENT_SENTINEL],
            "exclusions": {CONTENT_SENTINEL: 3, "budget": 1},
        },
        {"event": "scope_close", "account_id": CONTENT_SENTINEL},
        {"event": "scope_close", "attempts": float("nan")},
        {"event": "decision", "profile": Poison()},
    ]
    for fields in payloads:
        logger.info(
            CONTENT_SENTINEL, extra={"_daemon_event": {"kind": "routing", "fields": fields}}
        )
    output = lines(isolated_logging)
    assert output[0] == {
        "event": "scope_close",
        "kind": "routing",
        "level": "INFO",
        "attempts": 2,
        "exit": "normal",
    }
    assert output[1]["profile"] == "unrecognized"
    assert output[1]["route_ids"] == []
    assert output[1]["exclusions"] == {"budget": 1}
    assert all(item["event"] == "invalid_diagnostic" for item in output[2:])
    assert CONTENT_SENTINEL not in isolated_logging.getvalue()
