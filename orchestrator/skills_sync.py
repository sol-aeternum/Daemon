from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from orchestrator.skills_projection import (
    SkillProjectionStore,
    compute_content_hash,
    embed_skill_content,
)
from orchestrator.skills_store import SKILLS_DIR, get_skill, list_skills
from orchestrator.skills_upgrade import load_manifest

logger = logging.getLogger(__name__)


class DriftError(Exception):
    pass


class SyncConflictError(Exception):
    pass


def derive_source_type_for_backfill(skill_id: str) -> str:
    manifest = load_manifest()
    if skill_id in manifest.skills:
        return "system"
    return "manual"


@dataclass
class SyncResult:
    skill_id: str
    action: str
    success: bool
    error: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class ReconcileResult:
    orphaned: list[str]
    missing: list[str]
    drifted: list[str]
    total_orphaned: int
    total_missing: int
    total_drifted: int


class SkillSyncService:
    _store: SkillProjectionStore

    def __init__(self, store: SkillProjectionStore) -> None:
        self._store = store

    async def backfill_existing_skills(self) -> list[SyncResult]:
        results: list[SyncResult] = []
        for summary in list_skills():
            skill_id = summary["id"]
            try:
                source_type = derive_source_type_for_backfill(skill_id)
                await self.sync_skill(skill_id, source_type=source_type)
                results.append(
                    SyncResult(
                        skill_id=skill_id,
                        action="backfill",
                        success=True,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Backfill failed for skill %s: %s", skill_id, exc)
                results.append(
                    SyncResult(
                        skill_id=skill_id,
                        action="backfill",
                        success=False,
                        error=str(exc),
                    )
                )
        return results

    async def sync_skill(
        self, skill_id: str, source_type: str = "manual"
    ) -> SyncResult:
        path = SKILLS_DIR / f"{skill_id}.md"
        if not path.exists():
            return SyncResult(
                skill_id=skill_id,
                action="sync",
                success=False,
                error="Markdown file not found",
            )
        content = path.read_text(encoding="utf-8")
        source_hash = compute_content_hash(content)
        metadata, body = self._parse_frontmatter(content)
        name = metadata.get("name") or skill_id
        description = metadata.get("description") or summary_desc(skill_id)
        enabled = metadata.get("enabled", "true").lower() in ("true", "1", "yes", "on")
        projection = await self._store.get_projection(skill_id)
        embedding = await embed_skill_content(name, description, body)
        await self._store.upsert_projection(
            skill_id=skill_id,
            name=name,
            description=description,
            source_file_path=str(path),
            source_hash=source_hash,
            enabled=enabled,
            source_type=projection.get("source_type", source_type)
            if projection
            else source_type,
            created_by=projection.get("created_by", "system")
            if projection
            else "system",
            origin_url=projection.get("origin_url", "") if projection else "",
            embedding=embedding,
            repo_version=projection.get("repo_version", "0.0.0")
            if projection
            else "0.0.0",
            local_version="0.0.0",
            pending_update=projection.get("pending_update") if projection else None,
            allow_autonomous_edit=projection.get("allow_autonomous_edit", False)
            if projection
            else False,
            trigger_conditions=projection.get("trigger_conditions", "")
            if projection
            else "",
            complexity_origin=projection.get("complexity_origin", 0)
            if projection
            else 0,
        )
        return SyncResult(
            skill_id=skill_id,
            action="upsert",
            success=True,
            details={"source_hash": source_hash},
        )

    async def delete_skill_projection(self, skill_id: str) -> SyncResult:
        deleted = await self._store.delete_projection(skill_id)
        return SyncResult(
            skill_id=skill_id,
            action="delete",
            success=deleted,
            error=None if deleted else "Projection not found",
        )

    async def reconcile(self) -> ReconcileResult:
        file_ids = {p.stem for p in SKILLS_DIR.glob("*.md")}
        db_ids = set(await self._store.get_all_skill_ids())
        orphaned = list(db_ids - file_ids)
        missing = list(file_ids - db_ids)
        drifted: list[str] = []
        for skill_id in file_ids & db_ids:
            if await self.detect_drift(skill_id):
                drifted.append(skill_id)
        return ReconcileResult(
            orphaned=orphaned,
            missing=missing,
            drifted=drifted,
            total_orphaned=len(orphaned),
            total_missing=len(missing),
            total_drifted=len(drifted),
        )

    async def detect_drift(self, skill_id: str) -> bool:
        path = SKILLS_DIR / f"{skill_id}.md"
        if not path.exists():
            return False
        current_hash = compute_content_hash(path.read_text(encoding="utf-8"))
        stored_hash = await self._store.get_source_hash(skill_id)
        return stored_hash is not None and stored_hash != current_hash

    async def resync_drifted(self, skill_id: str) -> SyncResult:
        drifted = await self.detect_drift(skill_id)
        if not drifted:
            return SyncResult(
                skill_id=skill_id,
                action="resync",
                success=False,
                error="No drift detected",
            )
        return await self.sync_skill(skill_id)

    async def resync_all_drifted(self) -> list[SyncResult]:
        results: list[SyncResult] = []
        reconcile_result = await self.reconcile()
        for skill_id in reconcile_result.drifted:
            result = await self.resync_drifted(skill_id)
            results.append(result)
        return results

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, str], str]:
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


def summary_desc(skill_id: str) -> str:
    try:
        detail = get_skill(skill_id)
        return detail.get("description", "")
    except Exception:  # noqa: BLE001
        return f"Skill {skill_id}"
