"""Publish the skills that ship with the application into the skill store.

The repository directory is the source; the database is what every reader
uses. This runs once at backend startup and is the only caller of the store's
shipped-skill writes. It never handles request data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.services.skill_format import MAX_SKILL_FILE_CHARS, parse_skill_markdown
from orchestrator.services.skills import SkillError, SkillStore, normalize_skill_id

logger = logging.getLogger(__name__)

SHIPPED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "shipped_skills"
_SKILL_FILE = "SKILL.md"


@dataclass
class ShippedSkillSync:
    published: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


# UTF-8 needs at most four bytes per character.
_MAX_SKILL_FILE_BYTES = MAX_SKILL_FILE_CHARS * 4


def _read_skill_file(path: Path) -> str:
    # Refuse by size before reading, so an oversized file is never loaded.
    if path.stat().st_size > _MAX_SKILL_FILE_BYTES:
        raise ValueError("shipped skill file too large")
    return path.read_text(encoding="utf-8")


async def sync_shipped_skills(
    store: SkillStore, directory: Path = SHIPPED_SKILLS_DIR
) -> ShippedSkillSync:
    """Make the store's shipped skills match ``directory``.

    A changed file becomes a new revision and an unchanged one writes nothing.
    A shipped skill whose directory is gone is removed, but only when the
    directory itself is present and every skill in it was read: a missing or
    partly unreadable directory never deletes anything.
    """
    result = ShippedSkillSync()
    if not directory.is_dir():
        logger.info("No shipped skills directory; shipped skills left as they are")
        return result

    current = {skill["slug"]: skill["revision"] for skill in await store.list_shipped()}
    present: set[str] = set()
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        try:
            slug = normalize_skill_id(entry.name)
            parsed = parse_skill_markdown(_SKILL_FILE, _read_skill_file(entry / _SKILL_FILE))
            skill = await store.publish_shipped(
                slug, name=parsed.name, description=parsed.description, body=parsed.body
            )
        except (OSError, UnicodeDecodeError, ValueError, SkillError):
            # Neither the content nor the exception text is logged: a refusal
            # reason is enough, and the existing row stays as it is.
            logger.warning("Shipped skill %r could not be published; left unchanged", entry.name)
            result.failed.append(entry.name)
            continue
        present.add(slug)
        if current.get(slug) == skill["revision"]:
            result.unchanged.append(slug)
        else:
            result.published.append(slug)

    if result.failed:
        logger.warning("Shipped skill removal skipped: %d skill(s) failed", len(result.failed))
        return result
    for slug in sorted(set(current) - present):
        if await store.remove_shipped(slug):
            result.removed.append(slug)
    if result.published or result.removed:
        logger.info(
            "Shipped skills: %d published, %d unchanged, %d removed",
            len(result.published),
            len(result.unchanged),
            len(result.removed),
        )
    return result
