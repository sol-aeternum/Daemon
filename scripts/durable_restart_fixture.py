"""Explicit, disposable-only provider fixture for durable_restart_drill.sh.

This is not an application configuration option. The drill selects this worker
entry point explicitly and creates its permit in its own project volume. The
normal worker never imports it. Provider dispatch remains inside the real
reservation/settlement layer; only LiteLLM's transport and one synthetic effect
are replaced. File gates, not elapsed time, hold the interrupted attempts.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import runpy
import sys
from typing import Any

CONTROL = Path("/app/.daemon/durable-drill")
ANSWERS = {name: f"(drill) committed answer {name}" for name in "ABCD"}


def validate_isolation(control: Path = CONTROL) -> None:
    from orchestrator.config import Settings

    Settings.assert_durable_drill_isolated_environment()
    if not (control / "permit").is_file():
        raise RuntimeError("drill project permit missing")


def _dispatch_number(control: Path, scenario: str) -> int:
    path = control / f"dispatch-{scenario}"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("dispatch\n")
        handle.flush()
        os.fsync(handle.fileno())
    return len(path.read_text().splitlines())


async def wait_gate(control: Path, name: str) -> None:
    # Polling is only a means of observing an explicit release. It never
    # decides when an attempt is killed or how long the lease lasts.
    async with asyncio.timeout(80):
        while not (control / name).exists():
            await asyncio.sleep(0.05)


def _chunk(text: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": text}}]}


def _tool_chunk(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"drill-{name}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ]
                }
            }
        ]
    }


async def fake_completion(control: Path = CONTROL, **params: Any) -> Any:
    messages = params.get("messages") or []
    users = [
        str(message.get("content", "")) for message in messages if message.get("role") == "user"
    ]
    scenario = next((name for name in "ABCD" if users and users[-1] == f"drill {name}"), None)
    # Best-effort title/memory follow-ups also cannot reach a real provider.
    if scenario is None or not params.get("stream"):
        return {
            "choices": [{"message": {"content": "Synthetic drill follow-up"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    dispatch = _dispatch_number(control, scenario)
    has_tool_result = any(message.get("role") == "tool" for message in messages)

    async def chunks():
        if scenario in {"C", "D"} and not has_tool_result:
            if scenario == "C":
                yield _tool_chunk("calculate", {"expression": "2+2"})
            else:
                yield _tool_chunk(
                    "notification_send", {"message": "synthetic effect", "topic": "drill"}
                )
        else:
            if scenario == "D":
                await wait_gate(control, "release-D")
            prefix = ANSWERS[scenario][:8]
            yield _chunk(prefix)
            if scenario == "B" and dispatch == 1:
                await wait_gate(control, "release-B")
            if scenario == "C":
                await wait_gate(control, "release-C")
            # Modest pacing exercises streaming, but acceptance observes
            # committed state and explicit gates rather than guessing timing.
            for token in ANSWERS[scenario][8:]:
                await asyncio.sleep(0.01)
                yield _chunk(token)
        yield {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return chunks()


async def fake_effect(control: Path = CONTROL, **_kwargs: Any) -> str:
    path = control / "effects"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("performed\n")
        handle.flush()
        os.fsync(handle.fileno())
    return json.dumps({"success": True, "performed": True})


def install(control: Path = CONTROL) -> None:
    validate_isolation(control)
    # Prevent a library's implicit dotenv lookup from crossing the checkout.
    os.environ["LITELLM_MODE"] = "PRODUCTION"
    from orchestrator import compute_runtime
    from orchestrator.config import Settings, get_settings
    from orchestrator.tasks import runner
    from orchestrator.tools.notification import NotificationSendTool

    async def completion(**params: Any) -> Any:
        return await fake_completion(control, **params)

    async def effect(self: Any, **kwargs: Any) -> str:
        return await fake_effect(control, **kwargs)

    original_publish = runner._publish
    original_provider = Settings.get_provider_config

    def provider(self: Settings, provider_name: str | None = None):
        # No credential is introduced. Only this explicitly selected synthetic
        # transport omits the helper's missing-key preflight; guarded routing,
        # admission, reservations and settlement still execute normally.
        return original_provider(self, provider_name).model_copy(update={"requires_auth": False})

    async def publish(redis: Any, state: Any, message: dict[str, Any]) -> None:
        if not (control / "drop-redis").exists():
            await original_publish(redis, state, message)

    compute_runtime.litellm.acompletion = completion
    NotificationSendTool.execute = effect
    runner._publish = publish
    Settings.get_provider_config = provider
    # Exercise the real tool-completion, content persistence and ledger paths,
    # not the application's canned mock (which bypasses the ledger).
    get_settings().mock_llm = False
    (control / "installed").write_text("guarded synthetic transport\n")


def main() -> None:
    if sys.argv[1:] != ["worker"]:
        raise SystemExit("usage: durable_restart_fixture.py worker (disposable drill only)")
    install()
    runpy.run_module("orchestrator.worker.worker", run_name="__main__")


if __name__ == "__main__":
    main()
