"""Timezone preferences shared by chat prompts and background scheduling."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timezone, tzinfo
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def extract_timezone_name(user_settings: Mapping[str, object] | None) -> str | None:
    if not user_settings:
        return None
    candidates = [user_settings.get("timezone"), user_settings.get("time_zone")]
    preferences = user_settings.get("preferences")
    if isinstance(preferences, dict):
        candidates.extend([preferences.get("timezone"), preferences.get("time_zone")])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def resolve_runtime_timezone(user_timezone: str | None, default_timezone: str) -> tzinfo:
    """Prefer the user's zone, then the deployment default, then UTC."""
    for name in (user_timezone, default_timezone):
        if not name or not name.strip():
            continue
        try:
            return ZoneInfo(name.strip())
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Invalid runtime timezone %r; trying fallback", name)
    return timezone.utc
