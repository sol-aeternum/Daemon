"""Optional "noted on" dates in the rendered memory block (#601, option C).

Dormant: ``RENDER_NOTED_DATES`` is False, so production output is unchanged. The
LongMemEval campaign tests the rendering as a declared candidate whose only
change is this flag set to True. Enabling it in production needs its own decision.
"""

from __future__ import annotations

import datetime as dt

RENDER_NOTED_DATES = True

NOTED_DATES_HEADER = (
    'Dates show when each fact was noted; relative words like "today" refer to that date.'
)


def noted_suffix(memory: dict[str, object]) -> str:
    """`` (noted YYYY-MM-DD)`` from the memory's valid_from (UTC date), else ``""``."""
    if not RENDER_NOTED_DATES:
        return ""
    noted = memory.get("valid_from") or memory.get("created_at")
    if not isinstance(noted, dt.datetime):
        return ""
    if noted.tzinfo is None:
        noted = noted.replace(tzinfo=dt.UTC)
    return f" (noted {noted.astimezone(dt.UTC):%Y-%m-%d})"


def header_lines() -> list[str]:
    return [NOTED_DATES_HEADER] if RENDER_NOTED_DATES else []
