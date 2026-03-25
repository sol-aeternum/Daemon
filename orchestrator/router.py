from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoutingDecision:
    pipeline: str
    local_requested: bool
    user_message: str
    reason: str
    command: str | None = None


def route_message(message: str, metadata: dict[str, Any] | None) -> RoutingDecision:
    raw = message
    stripped = raw.lstrip()
    local_requested = False
    user_message = raw
    reason = "default_cloud"
    command = None

    if stripped.startswith("/council"):
        command = "council"
        user_message = (
            stripped[len("/council") :].lstrip()
            if len(stripped) > len("/council")
            else ""
        )
        reason = "council_command"
    elif stripped.startswith("/local"):
        local_requested = True
        user_message = stripped[len("/local") :].lstrip()
        reason = "local_flag_prefix"
    elif metadata is not None and metadata.get("local") is True:
        local_requested = True
        user_message = raw
        reason = "local_flag_metadata"

    return RoutingDecision(
        pipeline="cloud",
        local_requested=local_requested,
        user_message=user_message,
        reason=reason,
        command=command,
    )
