"""Supported backend/worker launch: safe import window and managed log output."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from typing import Any, TextIO

from orchestrator import safe_logging


@contextlib.contextmanager
def initialization(stream: TextIO) -> Iterator[None]:
    """Discard, never buffer, Python output while dependencies install handlers."""
    safe_logging.configure(stream)
    safe_logging.install_failure_hooks()
    with open(os.devnull, "w") as discard:
        try:
            with contextlib.redirect_stdout(discard), contextlib.redirect_stderr(discard):
                yield
        finally:
            # Runs even for failed imports, before closing a stream that a
            # third-party handler may have captured. No handler retains it.
            safe_logging.configure(stream)


def prepare(role: str, stream: TextIO) -> tuple[Callable[[], None], str]:
    with initialization(stream):
        settings = importlib.import_module("orchestrator.config").get_settings()
        if role == "backend":
            uvicorn = importlib.import_module("uvicorn")
            config = uvicorn.Config(
                "orchestrator.main:app", host="0.0.0.0", port=8000, log_config=None
            )
            config.load()
            server = uvicorn.Server(config)
            loop_factory = config.get_loop_factory()

            async def serve() -> None:
                safe_logging.protect_loop(asyncio.get_running_loop())
                await server.serve()

            def run_backend() -> None:
                with asyncio.Runner(loop_factory=loop_factory) as runner:
                    runner.run(serve())
                # Same startup failure exit as the single-process Uvicorn CLI.
                if not server.started:
                    raise SystemExit(3)

            run = run_backend
        else:
            worker_module = importlib.import_module("orchestrator.worker.worker")
            safe_logging.protect_loop(worker_module.worker.loop)
            run = worker_module.main
        try:
            importlib.import_module("orchestrator.routing_log").initialize_vocabulary()
        except Exception:
            # Diagnostics must not introduce a new application admission gate.
            # Initialization clears its snapshot first, so labels fail closed.
            safe_logging.event("failure", stage="startup")
    return run, settings.log_level


def launch(
    role: str, prepare_service: Callable[[str, TextIO], tuple[Callable[[], None], str]] = prepare
) -> None:
    stream = sys.stderr
    safe_logging.configure(stream)
    safe_logging.install_failure_hooks()
    try:
        run, level = prepare_service(role, stream)
        safe_logging.configure(stream, level)
        # All logger handlers now share managed sinks; repeated routing
        # configuration also uses a SafeHandler, never a raw formatter.
        safe_logging.event("service_start", role=role)
        run()
        safe_logging.event("service_stop", role=role)
    except SystemExit as exc:
        # SystemExit text bypasses sys.excepthook. Preserve numeric status,
        # never let an arbitrary string become interpreter stderr output.
        safe_logging.event("failure", stage="runtime")
        code: Any = exc.code
        raise SystemExit(code if type(code) is int or code is None else 1) from None
    except KeyboardInterrupt:
        # Uvicorn re-raises captured SIGINT after graceful shutdown. Let the
        # interpreter keep its interrupt status; the safe hook emits no text.
        raise
    except BaseException:
        safe_logging.event("failure", stage="runtime")
        raise SystemExit(1) from None


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"backend", "worker"}:
        safe_logging.configure(sys.stderr)
        safe_logging.event("failure", stage="startup")
        raise SystemExit(2)
    launch(args[0])


if __name__ == "__main__":
    main()
