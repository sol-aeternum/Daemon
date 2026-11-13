"""The skill index injected into a prompt, and skill embeddings.

Both read and write through :class:`SkillStore` for one account. There is no
filesystem fallback: a database failure yields an empty index and a logged
error, never another source of skills.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

from orchestrator.services.skills import SkillStore, create_skill_store

logger = logging.getLogger(__name__)

# L0 skill index budget (tokens) and entry cap.
SKILL_INDEX_TOKEN_BUDGET = 500
L0_MAX_SKILLS = 20
_EMBEDDED_BODY_CHARS = 2000


class _EmbeddingSink(Protocol):
    async def store_embedding(
        self, skill_id: uuid.UUID, revision: int, embedding: list[float]
    ) -> bool: ...


def _estimate_tokens(text: str) -> int:
    return len(text) // 4 + text.count("\n")


def _as_account(user_id: object) -> uuid.UUID | None:
    if isinstance(user_id, uuid.UUID):
        return user_id
    try:
        return uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return None


def render_skill_index(skills: list[dict[str, Any]]) -> str:
    """Render enabled skills, most used first, within the token budget."""
    ordered = sorted(
        skills,
        key=lambda skill: (-(skill.get("use_count") or 0), skill["updated_at"], skill["slug"]),
    )
    selected: list[str] = []
    used_tokens = 0
    for skill in ordered:
        entry = (
            f"- {skill['name']}: {skill['description'] or 'No description'} "
            f"[{skill['source_type']}]"
        )
        tokens = _estimate_tokens(entry)
        if used_tokens + tokens + 1 > SKILL_INDEX_TOKEN_BUDGET:
            break
        selected.append(entry)
        used_tokens += tokens
        if len(selected) >= L0_MAX_SKILLS:
            break
    if not selected:
        return ""
    return "Skill Index (L0):\n" + "\n".join(selected)


async def build_skill_index(
    db_pool: Any, user_id: object, *, store: SkillStore | None = None
) -> str:
    """The account's enabled skills plus enabled shipped skills, or nothing.

    Without a database or an account there is no index. Any failure is logged
    and yields an empty index.
    """
    account = _as_account(user_id)
    if account is None:
        return ""
    try:
        if store is not None:
            skill_store = store
        elif db_pool is not None:
            skill_store = create_skill_store(db_pool)
        else:
            return ""
        skills = await skill_store.list(account, enabled=True)
    except Exception:
        logger.error("Skill index unavailable; continuing without skills", exc_info=True)
        return ""
    return render_skill_index(skills)


async def embed_skill(store: _EmbeddingSink, skill: dict[str, Any]) -> bool:
    """Embed a saved skill and store the vector if that revision is still current.

    Runs after the save has committed. A skill with no embedding stays usable;
    it is only left out of similarity search, so every failure here is logged
    and swallowed.
    """
    from orchestrator.memory.embedding import EmbeddingConfigurationError, embed_documents

    text = f"{skill['name']}\n{skill['description']}\n{skill['body'][:_EMBEDDED_BODY_CHARS]}"
    try:
        embeddings = await embed_documents([text])
        if not embeddings:
            return False
        return await store.store_embedding(uuid.UUID(skill["id"]), skill["revision"], embeddings[0])
    except EmbeddingConfigurationError:
        # No approved embedding route: the skill is saved and simply unsearchable.
        return False
    except Exception:
        logger.warning("Skill embedding failed; skill saved without one", exc_info=True)
        return False
