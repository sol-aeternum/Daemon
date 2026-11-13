"""SKILL.md import and export for account-owned skills.

A skill file is Markdown with a small ``key: value`` frontmatter block. Only
``name``, ``description`` and ``enabled`` are read on import. Every other
field, including anything that looks like authority (``source_type``,
``allow_autonomous_edit``, a path or an owner), is ignored: authority comes
from the authenticated request, never from a file. Nothing here touches disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from orchestrator.services.skills import MAX_BODY_CHARS, SkillError

# Bound the whole file before any parsing: the body limit plus room for the
# frontmatter block.
MAX_SKILL_FILE_CHARS = MAX_BODY_CHARS + 8192
_MAX_FRONTMATTER_LINES = 64
_IMPORTED_DESCRIPTION_CHARS = 500

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


@dataclass(frozen=True)
class ParsedSkill:
    name: str
    description: str
    body: str
    enabled: bool


def _split_frontmatter(markdown: str) -> tuple[dict[str, str] | None, str]:
    if not markdown.startswith("---\n"):
        return None, markdown
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return None, markdown
    lines = markdown[4:end].splitlines()
    if len(lines) > _MAX_FRONTMATTER_LINES:
        raise SkillError("frontmatter_too_long")
    metadata: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip()
    return metadata, markdown[end + 5 :]


def _section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>[\s\S]*?)(?=^##\s+|\Z)",
        markdown,
        flags=re.MULTILINE,
    )
    return match.group("body").strip() if match else ""


def parse_skill_markdown(filename: str, markdown: str) -> ParsedSkill:
    """Parse an uploaded skill file. The filename is used only for its suffix."""
    if not filename.lower().endswith(".md"):
        raise SkillError("file_type_unsupported")
    if len(markdown) > MAX_SKILL_FILE_CHARS:
        raise SkillError("file_too_long")

    metadata, body = _split_frontmatter(markdown)
    if metadata is not None:
        name = metadata.get("name", "")
        description = metadata.get("description", "")
        if not name:
            raise SkillError("name_required")
        if not description:
            raise SkillError("description_required")
        instructions = body.strip()
        if not instructions:
            raise SkillError("body_required")
        raw_enabled = metadata.get("enabled", "true").lower()
        if raw_enabled not in _TRUE and raw_enabled not in _FALSE:
            raise SkillError("enabled_invalid")
        return ParsedSkill(name, description, instructions, raw_enabled in _TRUE)

    stripped = markdown.strip()
    if not stripped:
        raise SkillError("body_required")
    title = re.search(r"^#\s+(.+)$", stripped, flags=re.MULTILINE)
    if title is None:
        raise SkillError("format_invalid")
    purpose = re.sub(r"\s+", " ", _section(stripped, "Purpose")).strip()
    description = purpose[:_IMPORTED_DESCRIPTION_CHARS] or "Imported skill"
    return ParsedSkill(title.group(1).strip(), description, stripped, True)


def _one_line(value: str) -> str:
    return " ".join(value.split())


def render_skill_markdown(skill: dict[str, Any]) -> str:
    """Render the current revision as a file the import accepts back."""
    body = str(skill["body"])
    if not body.endswith("\n"):
        body = f"{body}\n"
    return (
        "---\n"
        f"name: {_one_line(str(skill['name']))}\n"
        f"description: {_one_line(str(skill['description']))}\n"
        f"version: {skill['revision']}\n"
        f"enabled: {'true' if skill['enabled'] else 'false'}\n"
        "---\n"
        f"{body}"
    )
