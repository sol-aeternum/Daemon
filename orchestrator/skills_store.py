from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from typing_extensions import TypedDict


class SkillSummary(TypedDict):
    id: str
    name: str
    description: str
    enabled: bool
    updated_at: str
    # Provenance metadata (from projection, optional until backfilled)
    source_type: str | None  # 'system', 'imported', 'manual', 'autonomous'
    allow_autonomous_edit: bool | None
    repo_version: str | None
    local_version: str | None
    pending_update: dict[str, Any] | None
    use_count: int | None
    last_used_at: str | None


class SkillDetail(SkillSummary):
    content: str
    created_by: str | None
    origin_url: str | None


SKILLS_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"
_SAFE_ID_PATTERN = re.compile(r"[^a-z0-9_-]+")
_MAX_SKILLS_FOR_PROMPT = 8
_MAX_CHARS_PER_SKILL = 2000

# L0 Skill Index budget (tokens)
SKILL_INDEX_TOKEN_BUDGET = 500
# Max skills to include in L0 index
L0_MAX_SKILLS = 20
# Estimated tokens per L0 entry (name + description + provenance tag + cues)
L0_TOKENS_PER_ENTRY = 25


def ensure_skills_dir() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def normalize_skill_id(name: str) -> str:
    candidate = name.strip().lower().replace(" ", "-")
    candidate = _SAFE_ID_PATTERN.sub("-", candidate).strip("-_")
    candidate = candidate[:64]
    if not candidate:
        raise ValueError("Skill name must include at least one alphanumeric character")
    return candidate


def _skill_path(skill_id: str) -> Path:
    safe_skill_id = normalize_skill_id(skill_id)
    return SKILLS_DIR / f"{safe_skill_id}.md"


def _format_timestamp(path: Path) -> str:
    updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return updated.isoformat()


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


def _serialize_skill(name: str, description: str, enabled: bool, content: str) -> str:
    body = content if content.endswith("\n") else f"{content}\n"
    return (
        "---\n"
        f"name: {name.strip()}\n"
        f"description: {description.strip()}\n"
        f"enabled: {'true' if enabled else 'false'}\n"
        "---\n"
        f"{body}"
    )


def _extract_section(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>[\s\S]*?)(?=^##\s+|\Z)",
        flags=re.MULTILINE,
    )
    match = pattern.search(markdown)
    if not match:
        return ""
    return match.group("body").strip()


def _parse_standard_markdown_skill(filename: str, markdown: str) -> tuple[str, str, bool, str]:
    stripped = markdown.strip()
    if not stripped:
        raise ValueError("Skill markdown file is empty")

    title_match = re.search(r"^#\s+(.+)$", stripped, flags=re.MULTILINE)
    if not title_match:
        raise ValueError(
            "Invalid skill format. Provide frontmatter or markdown with a top-level '# Title' heading"
        )

    title = title_match.group(1).strip()
    purpose = _extract_section(stripped, "Purpose")

    description = ""
    if purpose:
        compact = re.sub(r"\s+", " ", purpose).strip()
        description = compact[:500]

    if not description:
        description = f"Imported from {Path(filename).name}"

    return title, description, True, stripped


def _parse_enabled_value(raw_value: str | None) -> bool:
    value = (raw_value or "true").strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValueError("Field 'enabled' must be true or false")


def _serialize_skill_to_markdown(
    skill_id: str, name: str, description: str, enabled: bool, content: str
) -> str:
    """Serialize skill back to canonical markdown format for export/download.

    This ensures exported skills can be re-imported without data loss.
    """
    body = content if content.endswith("\n") else f"{content}\n"
    return (
        "---\n"
        f"name: {name.strip()}\n"
        f"description: {description.strip()}\n"
        f"enabled: {'true' if enabled else 'false'}\n"
        "---\n"
        f"{body}"
    )


def _skill_from_path(path: Path, projection: dict[str, Any] | None = None) -> SkillDetail:
    skill_id = path.stem
    raw = path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(raw)
    name = metadata.get("name") or skill_id
    description = metadata.get("description") or ""
    enabled = (metadata.get("enabled") or "true").lower() != "false"

    result: SkillDetail = {
        "id": skill_id,
        "name": name,
        "description": description,
        "enabled": enabled,
        "updated_at": _format_timestamp(path),
        "content": body.strip(),
        # Projection-backed metadata (None until backfilled)
        "source_type": projection.get("source_type") if projection else None,
        "allow_autonomous_edit": projection.get("allow_autonomous_edit") if projection else None,
        "repo_version": projection.get("repo_version") if projection else None,
        "local_version": projection.get("local_version") if projection else None,
        "pending_update": projection.get("pending_update") if projection else None,
        "use_count": projection.get("use_count") if projection else None,
        "last_used_at": (
            projection["last_used_at"].isoformat()
            if projection and projection.get("last_used_at")
            else None
        ),
        "created_by": projection.get("created_by") if projection else None,
        "origin_url": projection.get("origin_url") if projection else None,
    }
    return result


def list_skills() -> list[SkillSummary]:
    ensure_skills_dir()
    skills: list[SkillSummary] = []
    for path in sorted(SKILLS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        detail = _skill_from_path(path)
        skills.append(
            {
                "id": detail["id"],
                "name": detail["name"],
                "description": detail["description"],
                "enabled": detail["enabled"],
                "updated_at": detail["updated_at"],
                # Projection-backed metadata (populated via API layer)
                "source_type": None,
                "allow_autonomous_edit": None,
                "repo_version": None,
                "local_version": None,
                "pending_update": None,
                "use_count": None,
                "last_used_at": None,
            }
        )
    return skills


def get_skill(skill_id: str) -> SkillDetail:
    path = _skill_path(skill_id)
    if not path.exists():
        raise FileNotFoundError(f"Skill '{skill_id}' not found")
    return _skill_from_path(path)


def create_skill(
    *,
    name: str,
    description: str,
    content: str,
    enabled: bool,
) -> SkillDetail:
    ensure_skills_dir()
    skill_id = normalize_skill_id(name)
    path = _skill_path(skill_id)
    if path.exists():
        raise FileExistsError(f"Skill '{skill_id}' already exists")
    serialized = _serialize_skill(
        name=name, description=description, enabled=enabled, content=content
    )
    _ = path.write_text(serialized, encoding="utf-8")
    return get_skill(skill_id)


def update_skill(
    skill_id: str,
    *,
    name: str | None,
    description: str | None,
    content: str | None,
    enabled: bool | None,
) -> SkillDetail:
    current = get_skill(skill_id)
    next_name = name if name is not None else current["name"]
    next_description = description if description is not None else current["description"]
    next_content = content if content is not None else current["content"]
    next_enabled = enabled if enabled is not None else current["enabled"]
    path = _skill_path(skill_id)
    serialized = _serialize_skill(
        name=next_name,
        description=next_description,
        enabled=next_enabled,
        content=next_content,
    )
    _ = path.write_text(serialized, encoding="utf-8")
    return get_skill(skill_id)


def delete_skill(skill_id: str) -> None:
    path = _skill_path(skill_id)
    if not path.exists():
        raise FileNotFoundError(f"Skill '{skill_id}' not found")
    path.unlink()


def export_skill_markdown(skill_id: str) -> str:
    """Export skill as canonical markdown for download.

    Returns the full markdown content (frontmatter + body) exactly as stored,
    suitable for re-import via the upload endpoint.
    """
    detail = get_skill(skill_id)
    return _serialize_skill_to_markdown(
        skill_id=skill_id,
        name=detail["name"],
        description=detail["description"],
        enabled=detail["enabled"],
        content=detail["content"],
    )


def import_skill_markdown(
    *,
    filename: str,
    markdown: str,
    overwrite: bool = False,
) -> SkillDetail:
    ensure_skills_dir()

    if not filename.lower().endswith(".md"):
        raise ValueError("Only .md skill files are supported")

    metadata, body = _parse_frontmatter(markdown)

    if metadata:
        raw_name = (metadata.get("name") or "").strip()
        description = (metadata.get("description") or "").strip()
        if not raw_name:
            raise ValueError("Skill frontmatter must include a non-empty 'name' field")
        if not description:
            raise ValueError("Skill frontmatter must include a non-empty 'description' field")

        instructions = body.strip()
        if not instructions:
            raise ValueError("Skill markdown must include instructions content below frontmatter")

        enabled = _parse_enabled_value(metadata.get("enabled"))
    else:
        raw_name, description, enabled, instructions = _parse_standard_markdown_skill(
            filename=filename,
            markdown=markdown,
        )

    skill_id = normalize_skill_id(raw_name)
    path = _skill_path(skill_id)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Skill '{skill_id}' already exists. Enable overwrite to replace it")

    serialized = _serialize_skill(
        name=raw_name,
        description=description,
        enabled=enabled,
        content=instructions,
    )
    _ = path.write_text(serialized, encoding="utf-8")
    return get_skill(skill_id)


def build_enabled_skills_block() -> str:
    ensure_skills_dir()
    enabled_skills: list[SkillDetail] = []
    for summary in list_skills():
        if not summary["enabled"]:
            continue
        enabled_skills.append(get_skill(summary["id"]))
        if len(enabled_skills) >= _MAX_SKILLS_FOR_PROMPT:
            break

    if not enabled_skills:
        return ""

    parts: list[str] = [
        "Enabled Skills: Apply the following user-authored skills when relevant. "
        + "Treat them as high-priority behavioral and task guidance.",
    ]

    for skill in enabled_skills:
        trimmed_content = skill["content"][:_MAX_CHARS_PER_SKILL].strip()
        parts.append(
            "\n".join(
                [
                    f"[Skill: {skill['name']}]",
                    f"Description: {skill['description'] or 'No description provided.'}",
                    "Instructions:",
                    trimmed_content or "(No instructions provided)",
                ]
            )
        )

    return "\n\n".join(parts)


def _estimate_tokens(text: str) -> int:
    return len(text) // 4 + text.count("\n")


async def build_skill_index(
    db_pool: Any = None,
) -> str:
    ensure_skills_dir()

    summaries: list[Any] = []
    if db_pool is not None:
        try:
            from orchestrator.skills_projection import SkillProjectionStore

            store = SkillProjectionStore(db_pool)
            projections = await store.list_projections(enabled=True, limit=100)
            skill_ids_in_projection = {p["skill_id"] for p in projections}  # noqa: F841
            projection_map = {p["skill_id"]: p for p in projections}

            for summary in list_skills():
                if not summary["enabled"]:
                    continue
                sid = summary["id"]
                if sid in projection_map:
                    proj = projection_map[sid]
                    summary["source_type"] = proj.get("source_type")
                    summary["use_count"] = proj.get("use_count")
                    summary["last_used_at"] = (
                        proj["last_used_at"].isoformat() if proj.get("last_used_at") else None
                    )
                summaries.append(summary)
        except Exception:
            summaries = [s for s in list_skills() if s["enabled"]]
    else:
        summaries = [s for s in list_skills() if s["enabled"]]

    if not summaries:
        return ""

    summaries.sort(
        key=lambda s: (
            -(s.get("use_count") or 0),
            s.get("updated_at") or "",
            s["id"],
        )
    )

    budget = SKILL_INDEX_TOKEN_BUDGET
    selected: list[str] = []
    used_tokens = 0

    for summary in summaries:
        source = summary.get("source_type") or "unknown"
        provenance = f"[{source}]"
        entry = f"- {summary['name']}: {summary['description'] or 'No description'} {provenance}"
        tokens = _estimate_tokens(entry)
        if used_tokens + tokens + 1 > budget:
            break
        selected.append(entry)
        used_tokens += tokens
        if len(selected) >= L0_MAX_SKILLS:
            break

    if not selected:
        return ""

    header = "Skill Index (L0):"
    result = f"{header}\n" + "\n".join(selected)
    return result
