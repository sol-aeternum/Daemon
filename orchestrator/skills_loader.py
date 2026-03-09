"""Subagent skill loader - loads document skills for subagent context injection."""

from __future__ import annotations

import logging
from pathlib import Path


SKILLS_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"
SUBAGENT_SKILL_CHAR_LIMIT = 16000

logger = logging.getLogger(__name__)


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from markdown content.

    Returns:
        Tuple of (metadata dict, markdown body)
    """
    if not content.startswith("---\n"):
        return {}, content

    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content

    raw_header = content[4:end]
    body = content[end + 5 :]
    metadata: dict[str, str] = {}
    for line in raw_header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip()
    return metadata, body


def load_document_skill(format: str) -> str:
    """Load document skill instructions for a given format.

    Args:
        format: The document format (e.g., "docx", "pdf", "md")

    Returns:
        The markdown body (instructions) from the skill file

    Raises:
        ValueError: If the skill file for the requested format is not found
    """
    skill_path = SKILLS_DIR / f"document-{format}.md"

    if not skill_path.exists():
        raise ValueError(f"Document skill for format '{format}' not found")

    content = skill_path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(content)
    body = body.strip()

    if len(body) > SUBAGENT_SKILL_CHAR_LIMIT:
        logger.warning(
            "Document skill '%s' exceeded %d chars (%d); truncating",
            skill_path.name,
            SUBAGENT_SKILL_CHAR_LIMIT,
            len(body),
        )
        return body[:SUBAGENT_SKILL_CHAR_LIMIT]

    return body
