from __future__ import annotations

import re


def slugify_filename(name: str, max_length: int = 60) -> str:
    if max_length <= 0:
        return ""

    normalized = re.sub(r"[\s_]+", "-", name.lower())
    normalized = re.sub(r"[^a-z0-9-]", "", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")

    if not normalized:
        return ""

    if len(normalized) <= max_length:
        return normalized

    truncated = normalized[:max_length].rstrip("-")
    boundary = truncated.rfind("-")
    if boundary > 0:
        candidate = truncated[:boundary].rstrip("-")
        if candidate:
            return candidate

    return truncated
