from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


COUNCIL_RETIRED_MESSAGE = "Council has been retired."
_RETIRED_COUNCIL_COMMAND = re.compile(r"/council(?:\s|:|$)", re.IGNORECASE)


def is_retired_council_command(message: object) -> bool:
    """Recognize only a leading command, including legacy colon subcommands."""
    return isinstance(message, str) and bool(_RETIRED_COUNCIL_COMMAND.match(message.lstrip()))


@dataclass(frozen=True)
class RoutingDecision:
    pipeline: str
    local_requested: bool
    user_message: str
    reason: str


def route_message(message: str, metadata: dict[str, Any] | None) -> RoutingDecision:
    raw = message
    stripped = raw.lstrip()
    local_requested = False
    user_message = raw
    reason = "default_cloud"

    if stripped.startswith("/local"):
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
    )
