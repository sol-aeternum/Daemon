"""Publish the skills that ship with the application into the skill store.

The repository directory is the source; the database is what every reader
uses. This runs once at backend startup and is the only caller of the store's
catalogue write. It never handles request data.

Instances of different releases run side by side during a deploy and after a
rollback, each with its own copy of the directory. The directory therefore
carries a manifest, ``catalogue.json``, with a catalogue version, and the
store applies a directory only when its version is higher than the one the
database already holds. An older release changes nothing: it can neither
delete nor roll back what a newer one published.

The manifest also names every skill and the SHA-256 of its file. A directory
is applied only when it holds exactly those skills with exactly those bytes,
so a package that lost some or all of its skill directories, or holds a
truncated file, is refused instead of being read as "these skills were
removed". Removing every shipped skill takes a manifest that says so: a
version above zero with an empty skill list.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.services.skill_format import MAX_SKILL_FILE_CHARS, parse_skill_markdown
from orchestrator.services.skills import SkillError, SkillStore, normalize_skill_id

logger = logging.getLogger(__name__)

SHIPPED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "shipped_skills"
MANIFEST_FILE = "catalogue.json"
_SKILL_FILE = "SKILL.md"
# UTF-8 needs at most four bytes per character.
_MAX_SKILL_FILE_BYTES = MAX_SKILL_FILE_CHARS * 4
_MAX_MANIFEST_BYTES = 64 * 1024
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class ManifestError(ValueError):
    """The manifest file exists but is not a usable manifest."""


@dataclass(frozen=True)
class Manifest:
    """A directory's declared catalogue: its version and each skill's digest."""

    version: int
    skills: dict[str, str]


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


def skill_file_digest(data: bytes) -> str:
    """The digest a manifest records for a skill file with these bytes."""
    return hashlib.sha256(data).hexdigest()


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ManifestError("duplicate key")
    return dict(pairs)


def read_manifest(directory: Path) -> Manifest | None:
    """The directory's manifest, or None if it has none.

    Raises :class:`ManifestError` for a manifest that is present but unusable.
    """
    path = directory / MANIFEST_FILE
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ManifestError("manifest too large")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError("manifest unreadable") from exc
    try:
        raw = json.loads(text, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError) as exc:
        raise ManifestError("manifest is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "skills"}:
        raise ManifestError("manifest must hold exactly 'version' and 'skills'")
    version, skills = raw["version"], raw["skills"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ManifestError("version must be a whole number")
    if not isinstance(skills, dict):
        raise ManifestError("skills must map each skill to its digest")
    for slug, digest in skills.items():
        try:
            canonical = normalize_skill_id(slug)
        except (ValueError, SkillError) as exc:
            raise ManifestError("skill name invalid") from exc
        if canonical != slug:
            raise ManifestError("skill name invalid")
        if not isinstance(digest, str) or _SHA256_HEX.fullmatch(digest) is None:
            raise ManifestError("digest must be 64 lowercase hex characters")
    if version == 0 and skills:
        raise ManifestError("version 0 means no catalogue and cannot list skills")
    return Manifest(version, dict(skills))


def read_catalogue(
    directory: Path, manifest: Manifest
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """The manifest's skills as read from ``directory``.

    Returns the skills, the directory names that are missing, unexpected or
    unreadable, and the names whose file does not match its digest. The
    catalogue is complete only when both lists are empty.
    """
    present = {entry.name for entry in directory.iterdir() if entry.is_dir()}
    failed = sorted(present ^ set(manifest.skills))
    mismatched: list[str] = []
    skills: list[dict[str, str]] = []
    for slug in sorted(present & set(manifest.skills)):
        path = directory / slug / _SKILL_FILE
        try:
            # Refuse by size before reading, so an oversized file is never loaded.
            if path.stat().st_size > _MAX_SKILL_FILE_BYTES:
                raise ValueError("shipped skill file too large")
            data = path.read_bytes()
            if skill_file_digest(data) != manifest.skills[slug]:
                mismatched.append(slug)
                continue
            parsed = parse_skill_markdown(_SKILL_FILE, data.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, SkillError):
            failed.append(slug)
            continue
        skills.append(
            {
                "slug": slug,
                "name": parsed.name,
                "description": parsed.description,
                "body": parsed.body,
            }
        )
    return skills, sorted(failed), mismatched


async def sync_shipped_skills(
    store: SkillStore, directory: Path = SHIPPED_SKILLS_DIR
) -> ShippedSkillSync:
    """Apply ``directory`` as the shipped catalogue if it is a newer version.

    Nothing is written unless the directory has a manifest with a version
    higher than the database's and holds exactly the manifest's skills with
    matching digests. A catalogue is applied whole, in one transaction, or
    not at all.
    """
    result = ShippedSkillSync()
    try:
        manifest = read_manifest(directory) if directory.is_dir() else None
    except ManifestError as exc:
        # The message is one of this module's fixed strings, never file content.
        result.reason = "invalid_manifest"
        logger.warning(
            "Shipped skill manifest not usable (%s); shipped skills left as they are", exc
        )
        return result
    if manifest is None:
        result.reason = "no_catalogue"
        logger.info("No shipped skill catalogue; shipped skills left as they are")
        return result
    version = result.version = manifest.version
    if version == 0:
        # Version 0 is "no catalogue yet": nothing to publish, and it can never
        # outrank what a database already holds.
        result.reason = "empty_catalogue"
        return result

    skills, incomplete, mismatched = read_catalogue(directory, manifest)
    if incomplete or mismatched:
        # Neither content nor exception text is logged, only directory names.
        result.failed = sorted({*incomplete, *mismatched})
        result.reason = "incomplete_catalogue" if incomplete else "checksum_mismatch"
        logger.warning(
            "Shipped skill catalogue %d not applied: the directory does not match its "
            "manifest. Missing, unexpected or unreadable: %s. Digest mismatch: %s",
            version,
            ", ".join(repr(name) for name in incomplete) or "none",
            ", ".join(repr(name) for name in mismatched) or "none",
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
                "Increase the version in shipped_skills/catalogue.json",
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
