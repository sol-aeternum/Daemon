"""Publish the skills that ship with the application into the skill store.

The repository directory is the source; the database is what every reader
uses. This runs once at backend startup and is the only caller of the store's
catalogue write. It never handles request data.

Instances of different releases run side by side during a deploy and after a
rollback, each with its own copy of the directory. The directory therefore
carries a catalogue ``VERSION``, and the store applies a directory only when
its version is higher than the one the database already holds. An older
release changes nothing: it can neither delete nor roll back what a newer one
published.
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
_VERSION_FILE = "VERSION"
# UTF-8 needs at most four bytes per character.
_MAX_SKILL_FILE_BYTES = MAX_SKILL_FILE_CHARS * 4


@dataclass
class ShippedSkillSync:
    """What one startup did. ``applied`` is False whenever nothing was written."""

    applied: bool = False
    version: int = 0
    reason: str = ""
    published: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _read_skill_file(path: Path) -> str:
    # Refuse by size before reading, so an oversized file is never loaded.
    if path.stat().st_size > _MAX_SKILL_FILE_BYTES:
        raise ValueError("shipped skill file too large")
    return path.read_text(encoding="utf-8")


def read_catalogue_version(directory: Path) -> int | None:
    """The directory's catalogue version, or None if it has no usable one."""
    try:
        text = (directory / _VERSION_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not text.isascii() or not text.isdigit():
        return None
    return int(text)


def read_catalogue(directory: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Every skill in ``directory``, and the names of those that failed to read."""
    skills: list[dict[str, str]] = []
    failed: list[str] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        try:
            slug = normalize_skill_id(entry.name)
            parsed = parse_skill_markdown(_SKILL_FILE, _read_skill_file(entry / _SKILL_FILE))
        except (OSError, UnicodeDecodeError, ValueError, SkillError):
            failed.append(entry.name)
            continue
        skills.append(
            {
                "slug": slug,
                "name": parsed.name,
                "description": parsed.description,
                "body": parsed.body,
            }
        )
    return skills, failed


async def sync_shipped_skills(
    store: SkillStore, directory: Path = SHIPPED_SKILLS_DIR
) -> ShippedSkillSync:
    """Apply ``directory`` as the shipped catalogue if it is a newer version.

    Nothing is written unless the directory has a version higher than the
    database's and every skill in it was read. A catalogue is applied whole,
    in one transaction, or not at all.
    """
    result = ShippedSkillSync()
    version = read_catalogue_version(directory) if directory.is_dir() else None
    if version is None:
        result.reason = "no_catalogue"
        logger.info("No shipped skill catalogue; shipped skills left as they are")
        return result
    result.version = version
    if version == 0:
        # Version 0 is "no catalogue yet": nothing to publish, and it can never
        # outrank what a database already holds.
        result.reason = "empty_catalogue"
        return result

    skills, result.failed = read_catalogue(directory)
    if result.failed:
        # Neither content nor exception text is logged, only directory names.
        result.reason = "unreadable_skill"
        logger.warning(
            "Shipped skill catalogue %d not applied: could not read %s",
            version,
            ", ".join(repr(name) for name in result.failed),
        )
        return result

    stored = await store.shipped_catalogue_version()
    if version <= stored:
        result.reason = "not_newer"
        if version < stored:
            logger.info(
                "Shipped skill catalogue %d is older than applied catalogue %d; left as it is",
                version,
                stored,
            )
        elif await _differs_from_store(store, skills):
            logger.warning(
                "Shipped skills changed without a new catalogue version (%d); not applied. "
                "Increase the number in shipped_skills/VERSION",
                version,
            )
        return result

    outcome = await store.apply_shipped_catalogue(version, skills)
    if not outcome["applied"]:
        # Another instance applied this or a newer catalogue first.
        result.reason = "not_newer"
        return result
    result.applied = True
    result.published = outcome["published"]
    result.unchanged = outcome["unchanged"]
    result.removed = outcome["removed"]
    logger.info(
        "Shipped skill catalogue %d applied: %d published, %d unchanged, %d removed",
        version,
        len(result.published),
        len(result.unchanged),
        len(result.removed),
    )
    return result


async def _differs_from_store(store: SkillStore, skills: list[dict[str, str]]) -> bool:
    held = {
        skill["slug"]: (skill["name"], skill["description"], skill["body"])
        for skill in await store.list_shipped()
    }
    wanted = {
        skill["slug"]: (skill["name"], skill["description"], skill["body"]) for skill in skills
    }
    return held != wanted
