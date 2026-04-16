"""Repo system-skill manifest and safe upgrade path.

Provides manifest tracking for repo-shipped skill files and safe upgrade
reconciliation that never overwrites locally modified skills.

Architecture (THREE separate sources):
1. REPO SOURCE: caller-provided content (external to this service)
2. LOCAL CANONICAL: SKILLS_DIR/{skill_id}.md (user-editable)
3. SNAPSHOT: .repo_snapshots/{skill_id}.md (immutable record of last repo delivery)

Decision logic:
1. Load current repo content (from caller-provided dict)
2. Load current local canonical content (from SKILLS_DIR/*.md)
3. Load snapshot (what repo delivered last time)
4. Compare:
   - repo_changed = current_repo != snapshot
   - user_modified = current_local != snapshot
5. Apply safe-upgrade rules based on these two distinct comparisons"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.skills_projection import (
    SkillProjectionStore,
    compute_content_hash,
    embed_skill_content,
)
from orchestrator.skills_store import SKILLS_DIR

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".manifest.json"
SNAPSHOT_DIRNAME = ".repo_snapshots"

# Repo skills directory - ships with the application, separate from user-editable SKILLS_DIR
REPO_SKILLS_DIR = SKILLS_DIR.parent / "repo_skills"


def load_repo_contents() -> dict[str, str]:
    """Load all skill markdown files from the repo skills directory.

    Returns a dict mapping skill_id -> raw markdown content.
    Skills without valid frontmatter are skipped.
    If the repo skills directory does not exist, returns an empty dict.
    """
    if not REPO_SKILLS_DIR.is_dir():
        logger.debug("Repo skills directory does not exist: %s", REPO_SKILLS_DIR)
        return {}
    contents: dict[str, str] = {}
    for path in REPO_SKILLS_DIR.glob("*.md"):
        skill_id = path.stem
        if not skill_id:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            # Skip files without frontmatter (not valid repo skills)
            if not raw.startswith("---\n"):
                logger.debug("Skipping repo skill without frontmatter: %s", skill_id)
                continue
            contents[skill_id] = raw
        except OSError as exc:
            logger.warning("Failed to read repo skill %s: %s", skill_id, exc)
    return contents


def _manifest_path() -> Path:
    return SKILLS_DIR / MANIFEST_FILENAME


def _snapshot_dir() -> Path:
    return SKILLS_DIR / SNAPSHOT_DIRNAME


def _snapshot_path(skill_id: str) -> Path:
    return _snapshot_dir() / f"{skill_id}.md"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
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


def _read_skill_versions(content: str) -> tuple[str, str]:
    metadata, _ = _parse_frontmatter(content)
    repo_version = metadata.get("repo_version", "0.0.0")
    local_version = metadata.get("local_version", "0.0.0")
    return repo_version, local_version


def _write_skill_file(skill_id: str, content: str) -> None:
    path = SKILLS_DIR / f"{skill_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {path}")
    metadata, body = _parse_frontmatter(content)
    repo_version, _ = _read_skill_versions(content)
    new_frontmatter = (
        f"---\n"
        f"name: {metadata.get('name', skill_id)}\n"
        f"description: {metadata.get('description', '')}\n"
        f"enabled: {metadata.get('enabled', 'true')}\n"
        f"repo_version: {repo_version}\n"
        f"local_version: {repo_version}\n"
        f"---\n"
    )
    path.write_text(new_frontmatter + body, encoding="utf-8")


def _save_snapshot(skill_id: str, content: str) -> None:
    path = _snapshot_path(skill_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_snapshot(skill_id: str) -> str | None:
    path = _snapshot_path(skill_id)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


@dataclass
class SkillManifestEntry:
    repo_hash: str
    repo_version: str
    local_version: str
    updated_at: str


@dataclass
class SkillManifest:
    version: int = 1
    skills: dict[str, SkillManifestEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "skills": {
                sid: {
                    "repo_hash": e.repo_hash,
                    "repo_version": e.repo_version,
                    "local_version": e.local_version,
                    "updated_at": e.updated_at,
                }
                for sid, e in self.skills.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillManifest:
        skills = {}
        for sid, entry in data.get("skills", {}).items():
            skills[sid] = SkillManifestEntry(
                repo_hash=entry.get("repo_hash", ""),
                repo_version=entry.get("repo_version", "0.0.0"),
                local_version=entry.get("local_version", "0.0.0"),
                updated_at=entry.get("updated_at", ""),
            )
        return cls(version=data.get("version", 1), skills=skills)


def load_manifest() -> SkillManifest:
    path = _manifest_path()
    if not path.exists():
        return SkillManifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SkillManifest.from_dict(data)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Failed to load manifest %s: %s. Returning empty manifest.", path, exc
        )
        return SkillManifest()


def save_manifest(manifest: SkillManifest) -> None:
    path = _manifest_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(path)


@dataclass
class UpgradeAction:
    skill_id: str
    action: str
    success: bool
    error: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class UpgradeResult:
    actions: list[UpgradeAction]
    total_unchanged: int = 0
    total_silent_updates: int = 0
    total_pending_updates: int = 0
    total_inserts: int = 0
    total_deprecated: int = 0
    total_errors: int = 0


class SkillUpgradeService:
    def __init__(self, store: SkillProjectionStore) -> None:
        self._store = store

    async def sync_repo_skills(self, repo_contents: dict[str, str]) -> UpgradeResult:
        # Safety: when repo provides no content, we cannot reliably determine which
        # skills were truly removed vs. temporarily unavailable. Fail safe by no-op.
        if not repo_contents:
            return UpgradeResult(actions=[])

        manifest = load_manifest()
        current_files = {
            p.stem: p
            for p in SKILLS_DIR.glob("*.md")
            if p.stem
            not in (
                MANIFEST_FILENAME.lstrip("."),
                SNAPSHOT_DIRNAME.lstrip("."),
            )
        }
        manifest_skill_ids = set(manifest.skills.keys())
        current_skill_ids = set(current_files.keys())
        repo_skill_ids = set(repo_contents.keys())

        actions: list[UpgradeAction] = []

        for skill_id, path in current_files.items():
            try:
                action = await self._process_skill(
                    skill_id, path, manifest, repo_contents
                )
                actions.append(action)
            except Exception as exc:
                logger.error("Error processing skill %s: %s", skill_id, exc)
                actions.append(
                    UpgradeAction(
                        skill_id=skill_id,
                        action="error",
                        success=False,
                        error=str(exc),
                    )
                )

        removed_skill_ids = manifest_skill_ids - repo_skill_ids - current_skill_ids
        for skill_id in removed_skill_ids:
            try:
                action = await self._deprecate_skill(skill_id, manifest)
                actions.append(action)
            except Exception as exc:
                logger.error("Error deprecating skill %s: %s", skill_id, exc)
                actions.append(
                    UpgradeAction(
                        skill_id=skill_id,
                        action="error",
                        success=False,
                        error=str(exc),
                    )
                )

        return UpgradeResult(
            actions=actions,
            total_unchanged=sum(1 for a in actions if a.action == "unchanged"),
            total_silent_updates=sum(1 for a in actions if a.action == "silent_update"),
            total_pending_updates=sum(
                1 for a in actions if a.action == "pending_update"
            ),
            total_inserts=sum(1 for a in actions if a.action == "insert"),
            total_deprecated=sum(1 for a in actions if a.action == "deprecated"),
            total_errors=sum(1 for a in actions if a.action == "error"),
        )

    async def _process_skill(
        self,
        skill_id: str,
        path: Path,
        manifest: SkillManifest,
        repo_contents: dict[str, str],
    ) -> UpgradeAction:
        current_repo_content = repo_contents.get(skill_id)
        current_local_content = path.read_text(encoding="utf-8")
        current_local_hash = compute_content_hash(current_local_content)
        snapshot_content = _load_snapshot(skill_id)
        manifest_entry = manifest.skills.get(skill_id)

        if current_repo_content is None:
            return await self._deprecate_skill(skill_id, manifest)

        current_repo_hash = compute_content_hash(current_repo_content)

        if manifest_entry is None:
            return await self._insert_new_skill(
                skill_id,
                current_repo_content,
                current_repo_hash,
                current_local_hash,
            )

        snapshot_hash = (
            compute_content_hash(snapshot_content) if snapshot_content else ""
        )

        repo_changed = current_repo_hash != manifest_entry.repo_hash
        user_modified = current_local_hash != snapshot_hash

        if not repo_changed and not user_modified:
            return UpgradeAction(
                skill_id=skill_id,
                action="unchanged",
                success=True,
                details={"repo_hash": current_repo_hash},
            )

        if repo_changed and not user_modified:
            return await self._silent_update_skill(
                skill_id,
                current_repo_content,
                current_repo_hash,
            )

        return await self._mark_pending_update(
            skill_id,
            current_repo_content,
            current_local_content,
            current_repo_hash,
            current_local_hash,
        )

    async def _insert_new_skill(
        self,
        skill_id: str,
        repo_content: str,
        repo_hash: str,
        local_hash: str,
    ) -> UpgradeAction:
        repo_version, local_version = _read_skill_versions(repo_content)
        metadata, body = _parse_frontmatter(repo_content)
        name = metadata.get("name") or skill_id
        description = metadata.get("description") or ""
        enabled = metadata.get("enabled", "true").lower() in ("true", "1", "yes", "on")
        embedding = await embed_skill_content(name, description, body)

        await self._store.upsert_projection(
            skill_id=skill_id,
            name=name,
            description=description,
            source_file_path=str(SKILLS_DIR / f"{skill_id}.md"),
            source_hash=local_hash,
            enabled=enabled,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=embedding,
            repo_version=repo_version,
            local_version=local_version,
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
        )

        _save_snapshot(skill_id, repo_content)

        manifest = load_manifest()
        manifest.skills[skill_id] = SkillManifestEntry(
            repo_hash=repo_hash,
            repo_version=repo_version,
            local_version=local_version,
            updated_at=_now_iso(),
        )
        save_manifest(manifest)

        return UpgradeAction(
            skill_id=skill_id,
            action="insert",
            success=True,
            details={"repo_hash": repo_hash, "repo_version": repo_version},
        )

    async def _silent_update_skill(
        self,
        skill_id: str,
        repo_content: str,
        repo_hash: str,
    ) -> UpgradeAction:
        repo_version, _ = _read_skill_versions(repo_content)
        metadata, body = _parse_frontmatter(repo_content)
        name = metadata.get("name") or skill_id
        description = metadata.get("description") or ""
        enabled = metadata.get("enabled", "true").lower() in ("true", "1", "yes", "on")
        embedding = await embed_skill_content(name, description, body)

        existing = await self._store.get_projection(skill_id)

        _write_skill_file(skill_id, repo_content)
        _save_snapshot(skill_id, repo_content)

        await self._store.upsert_projection(
            skill_id=skill_id,
            name=name,
            description=description,
            source_file_path=str(SKILLS_DIR / f"{skill_id}.md"),
            source_hash=repo_hash,
            enabled=enabled,
            source_type=existing.get("source_type", "system") if existing else "system",
            created_by=existing.get("created_by", "system") if existing else "system",
            origin_url=existing.get("origin_url", "") if existing else "",
            embedding=embedding,
            repo_version=repo_version,
            local_version=repo_version,
            pending_update=None,
            allow_autonomous_edit=existing.get("allow_autonomous_edit", False)
            if existing
            else False,
            trigger_conditions=existing.get("trigger_conditions", "")
            if existing
            else "",
            complexity_origin=existing.get("complexity_origin", 0) if existing else 0,
        )

        manifest = load_manifest()
        manifest.skills[skill_id] = SkillManifestEntry(
            repo_hash=repo_hash,
            repo_version=repo_version,
            local_version=repo_version,
            updated_at=_now_iso(),
        )
        save_manifest(manifest)

        return UpgradeAction(
            skill_id=skill_id,
            action="silent_update",
            success=True,
            details={"repo_hash": repo_hash, "repo_version": repo_version},
        )

    async def _mark_pending_update(
        self,
        skill_id: str,
        repo_content: str,
        local_content: str,
        repo_hash: str,
        local_hash: str,
    ) -> UpgradeAction:
        repo_version, _ = _read_skill_versions(repo_content)
        local_version_actual, _ = _read_skill_versions(local_content)
        repo_metadata, repo_body = _parse_frontmatter(repo_content)
        name = repo_metadata.get("name") or skill_id
        description = repo_metadata.get("description") or ""
        enabled = repo_metadata.get("enabled", "true").lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        embedding = await embed_skill_content(name, description, repo_body)

        existing = await self._store.get_projection(skill_id)

        pending_update = {
            "repo_hash": repo_hash,
            "repo_version": repo_version,
            "repo_content": repo_content,
            "repo_name": name,
            "repo_description": description,
            "updated_at": _now_iso(),
        }

        await self._store.upsert_projection(
            skill_id=skill_id,
            name=name,
            description=description,
            source_file_path=str(SKILLS_DIR / f"{skill_id}.md"),
            source_hash=local_hash,
            enabled=enabled,
            source_type=existing.get("source_type", "system") if existing else "system",
            created_by=existing.get("created_by", "system") if existing else "system",
            origin_url=existing.get("origin_url", "") if existing else "",
            embedding=embedding,
            repo_version=repo_version,
            local_version=local_version_actual,
            pending_update=pending_update,
            allow_autonomous_edit=existing.get("allow_autonomous_edit", False)
            if existing
            else False,
            trigger_conditions=existing.get("trigger_conditions", "")
            if existing
            else "",
            complexity_origin=existing.get("complexity_origin", 0) if existing else 0,
        )

        _save_snapshot(skill_id, repo_content)

        manifest = load_manifest()
        manifest.skills[skill_id] = SkillManifestEntry(
            repo_hash=repo_hash,
            repo_version=repo_version,
            local_version=local_version_actual,
            updated_at=_now_iso(),
        )
        save_manifest(manifest)

        return UpgradeAction(
            skill_id=skill_id,
            action="pending_update",
            success=True,
            details={
                "repo_hash": repo_hash,
                "repo_version": repo_version,
                "local_version": local_version_actual,
            },
        )

    async def _deprecate_skill(
        self,
        skill_id: str,
        manifest: SkillManifest,
    ) -> UpgradeAction:
        existing = await self._store.get_projection(skill_id)
        if existing is None:
            return UpgradeAction(
                skill_id=skill_id,
                action="deprecated",
                success=True,
                details={"note": "not in projection"},
            )

        manifest_entry = manifest.skills.get(
            skill_id,
            SkillManifestEntry(
                repo_hash="", repo_version="0.0.0", local_version="0.0.0", updated_at=""
            ),
        )

        pending_update = {
            "deprecated": True,
            "removed_from_repo": True,
            "previous_hash": manifest_entry.repo_hash,
            "previous_repo_version": manifest_entry.repo_version,
            "updated_at": _now_iso(),
        }

        await self._store.set_pending_update(skill_id, pending_update)

        return UpgradeAction(
            skill_id=skill_id,
            action="deprecated",
            success=True,
            details={
                "previous_hash": manifest_entry.repo_hash,
                "previous_repo_version": manifest_entry.repo_version,
            },
        )

    async def apply_pending_update(self, skill_id: str) -> UpgradeAction:
        projection = await self._store.get_projection(skill_id)
        if projection is None:
            return UpgradeAction(
                skill_id=skill_id,
                action="error",
                success=False,
                error="Skill not found in projection",
            )

        pending = projection.get("pending_update")
        if pending is None:
            return UpgradeAction(
                skill_id=skill_id,
                action="error",
                success=False,
                error="No pending update to apply",
            )

        if pending.get("deprecated"):
            return UpgradeAction(
                skill_id=skill_id,
                action="error",
                success=False,
                error="Cannot apply: skill was deprecated from repo",
            )

        repo_content = pending.get("repo_content", "")
        if not repo_content:
            return UpgradeAction(
                skill_id=skill_id,
                action="error",
                success=False,
                error="Pending update has no repo_content",
            )

        path = SKILLS_DIR / f"{skill_id}.md"
        path.write_text(repo_content, encoding="utf-8")
        _save_snapshot(skill_id, repo_content)

        await self._store.clear_pending_update(skill_id)
        current_hash = compute_content_hash(repo_content)
        repo_version = pending.get("repo_version", "0.0.0")

        metadata, body = _parse_frontmatter(repo_content)
        name = metadata.get("name") or skill_id
        description = metadata.get("description") or ""
        enabled = metadata.get("enabled", "true").lower() in ("true", "1", "yes", "on")
        embedding = await embed_skill_content(name, description, body)

        await self._store.upsert_projection(
            skill_id=skill_id,
            name=name,
            description=description,
            source_file_path=str(path),
            source_hash=current_hash,
            enabled=enabled,
            source_type=projection.get("source_type", "system"),
            created_by=projection.get("created_by", "system"),
            origin_url=projection.get("origin_url", ""),
            embedding=embedding,
            repo_version=repo_version,
            local_version=repo_version,
            pending_update=None,
            allow_autonomous_edit=projection.get("allow_autonomous_edit", False),
            trigger_conditions=projection.get("trigger_conditions", ""),
            complexity_origin=projection.get("complexity_origin", 0),
        )

        manifest = load_manifest()
        manifest.skills[skill_id] = SkillManifestEntry(
            repo_hash=current_hash,
            repo_version=repo_version,
            local_version=repo_version,
            updated_at=_now_iso(),
        )
        save_manifest(manifest)

        return UpgradeAction(
            skill_id=skill_id,
            action="applied",
            success=True,
            details={"repo_version": repo_version},
        )

    async def dismiss_pending_update(self, skill_id: str) -> UpgradeAction:
        projection = await self._store.get_projection(skill_id)
        if projection is None:
            return UpgradeAction(
                skill_id=skill_id,
                action="error",
                success=False,
                error="Skill not found in projection",
            )

        pending = projection.get("pending_update")
        if pending is None:
            return UpgradeAction(
                skill_id=skill_id,
                action="error",
                success=False,
                error="No pending update to dismiss",
            )

        repo_version = pending.get(
            "repo_version", projection.get("repo_version", "0.0.0")
        )
        current_hash = projection.get("source_hash", "")

        await self._store.upsert_projection(
            skill_id=skill_id,
            name=projection.get("name", skill_id),
            description=projection.get("description", ""),
            source_file_path=projection.get(
                "source_file_path", str(SKILLS_DIR / f"{skill_id}.md")
            ),
            source_hash=current_hash,
            enabled=projection.get("enabled", True),
            source_type=projection.get("source_type", "system"),
            created_by=projection.get("created_by", "system"),
            origin_url=projection.get("origin_url", ""),
            embedding=projection.get("embedding"),
            repo_version=repo_version,
            local_version=repo_version,
            pending_update=None,
            allow_autonomous_edit=projection.get("allow_autonomous_edit", False),
            trigger_conditions=projection.get("trigger_conditions", ""),
            complexity_origin=projection.get("complexity_origin", 0),
        )

        snapshot_content = _load_snapshot(skill_id)
        if snapshot_content:
            snapshot_hash = compute_content_hash(snapshot_content)
        else:
            snapshot_hash = current_hash

        manifest = load_manifest()
        manifest.skills[skill_id] = SkillManifestEntry(
            repo_hash=snapshot_hash,
            repo_version=repo_version,
            local_version=repo_version,
            updated_at=_now_iso(),
        )
        save_manifest(manifest)

        return UpgradeAction(
            skill_id=skill_id,
            action="dismissed",
            success=True,
            details={"repo_version": repo_version},
        )


async def run_upgrade_sync(
    db_pool: Any, repo_contents: dict[str, str]
) -> UpgradeResult:
    store = SkillProjectionStore(db_pool)
    service = SkillUpgradeService(store)
    return await service.sync_repo_skills(repo_contents)
