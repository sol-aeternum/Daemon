"""Account-owned, database-authoritative skills (docs/ACCOUNT_OWNED_SKILLS_DESIGN.md).

Step 1 of the design: the store and the single write path. Nothing in the
running application calls this module yet; the file-backed store stays in use
until the cutover change.

Rules this module holds:

- Every read and write takes the owning ``user_id`` from trusted execution
  context. A skill with ``user_id`` NULL is shipped: readable by every account,
  writable only through :meth:`SkillStore.publish_shipped`.
- Authority (actor, source type, the autonomous-edit grant) is decided here and
  under the row lock, never taken from a request.
- Every column that can carry text is encrypted. The slug is looked up through
  a keyed digest bound to its owner; no hash of content is stored.
- A save is one transaction: it appends a revision, moves the head and trims
  old revisions together.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import datetime
from typing import Any, Literal, cast

import asyncpg
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from orchestrator.config import Settings, get_settings
from orchestrator.memory.encryption import ContentEncryption, EncryptionKeyMissing
from orchestrator.skills_store import normalize_skill_id, slugify_skill_name

MAX_SKILLS_PER_ACCOUNT = 200
MAX_NAME_CHARS = 200
MAX_DESCRIPTION_CHARS = 1536
MAX_TRIGGER_CONDITIONS_CHARS = 1536
MAX_BODY_CHARS = 64_000
REVISION_RETENTION = 20
EMBEDDING_DIMENSIONS = 1024

Actor = Literal["user", "autonomous", "consolidation", "shipped"]

# A pooled connection inside a transaction; asyncpg types the proxy separately.
_Connection = Any

_ACCOUNT_ACTORS: frozenset[str] = frozenset({"user", "autonomous", "consolidation"})
# Actors that act without the owner present and therefore need the owner's grant.
_GRANT_ACTORS: frozenset[str] = frozenset({"autonomous", "consolidation"})

_SLUG_LOOKUP_INFO = b"daemon/skills/slug-lookup/v1"
_SHIPPED_SCOPE = b"shipped"

# The one query that reads skill content. Every statement in this module is a
# plain literal so the account writer catalogue can classify each write.
_SELECT_HEADS = (
    "SELECT id, user_id, slug_encrypted, name_encrypted, description_encrypted, "
    "body_encrypted, trigger_conditions_encrypted, revision, source_type, "
    "allow_autonomous_edit, enabled, embedding_revision, use_count, last_used_at, "
    "complexity_origin, created_at, updated_at FROM skills WHERE id = ANY($1::uuid[])"
)


class SkillError(Exception):
    """A refusal with a stable, content-free reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def derive_slug_lookup_key(encryption_key: str) -> bytes:
    """Derive the slug-lookup key from the content-encryption key.

    The digest key is separate from the Fernet key so a digest never exposes
    key material used for encryption. Rotating the content-encryption key
    changes this key, so a rotation must recompute ``slug_lookup`` for every
    row in the same operation that re-encrypts content.
    """
    try:
        material = base64.urlsafe_b64decode(encryption_key.encode())
    except (ValueError, TypeError) as exc:
        raise EncryptionKeyMissing("DAEMON_ENCRYPTION_KEY is not valid base64") from exc
    if len(material) != 32:
        raise EncryptionKeyMissing("DAEMON_ENCRYPTION_KEY must decode to 32 bytes")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_SLUG_LOOKUP_INFO).derive(
        material
    )


def _validated_text(value: object, limit: int, reason: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise SkillError(f"{reason}_invalid")
    if required and not value.strip():
        raise SkillError(f"{reason}_required")
    if len(value) > limit:
        raise SkillError(f"{reason}_too_long")
    return value


def _slug_from_id(slug: object) -> str:
    if not isinstance(slug, str):
        raise SkillError("slug_invalid")
    try:
        return normalize_skill_id(slug)
    except ValueError as exc:
        raise SkillError("slug_invalid") from exc


def _slug_from_name(name: str) -> str:
    try:
        return slugify_skill_name(name)
    except ValueError as exc:
        raise SkillError("slug_invalid") from exc


def _format_vector(embedding: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"


class SkillStore:
    def __init__(
        self, pool: asyncpg.Pool, encryption: ContentEncryption, slug_lookup_key: bytes
    ) -> None:
        if len(slug_lookup_key) < 32:
            raise ValueError("slug lookup key must be at least 32 bytes")
        self._pool = pool
        self._enc = encryption
        self._lookup_key = slug_lookup_key

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _lookup(self, user_id: uuid.UUID | None, slug: str) -> bytes:
        scope = user_id.bytes if user_id is not None else _SHIPPED_SCOPE
        message = len(scope).to_bytes(1, "big") + scope + slug.encode()
        return hmac.new(self._lookup_key, message, hashlib.sha256).digest()

    async def _heads(self, executor: Any, skill_ids: list[uuid.UUID]) -> list[asyncpg.Record]:
        """Head rows for ``skill_ids``, in the order given."""
        rows = {row["id"]: row for row in await executor.fetch(_SELECT_HEADS, skill_ids)}
        return [rows[skill_id] for skill_id in skill_ids if skill_id in rows]

    async def _head(self, executor: Any, skill_id: uuid.UUID) -> asyncpg.Record:
        rows = await self._heads(executor, [skill_id])
        if not rows:
            raise SkillError("not_found")
        return rows[0]

    def _decode_head(self, row: asyncpg.Record) -> dict[str, Any]:
        # A wrong key or corrupt envelope raises: never a blank or partial skill.
        last_used = cast("datetime | None", row["last_used_at"])
        return {
            "id": str(row["id"]),
            "scope": "shipped" if row["user_id"] is None else "account",
            "slug": self._enc.decrypt(row["slug_encrypted"]),
            "name": self._enc.decrypt(row["name_encrypted"]),
            "description": self._enc.decrypt(row["description_encrypted"]),
            "body": self._enc.decrypt(row["body_encrypted"]),
            "trigger_conditions": self._enc.decrypt(row["trigger_conditions_encrypted"]),
            "revision": row["revision"],
            "source_type": row["source_type"],
            "allow_autonomous_edit": row["allow_autonomous_edit"],
            "enabled": row["enabled"],
            "has_current_embedding": row["embedding_revision"] == row["revision"],
            "use_count": row["use_count"],
            "last_used_at": last_used.isoformat() if last_used is not None else None,
            "complexity_origin": row["complexity_origin"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def _lock_account_skill(
        self, conn: _Connection, user_id: uuid.UUID, slug: str
    ) -> asyncpg.Record:
        """Lock the caller's own skill, or refuse.

        A slug that only names a shipped skill refuses as read-only; anything
        else, including another account's skill, is simply not found.
        """
        skill_id = await conn.fetchval(
            "SELECT id FROM skills WHERE user_id = $1 AND slug_lookup = $2 FOR UPDATE",
            user_id,
            self._lookup(user_id, slug),
        )
        if skill_id is not None:
            return await self._head(conn, skill_id)
        shipped = await conn.fetchval(
            "SELECT 1 FROM skills WHERE user_id IS NULL AND slug_lookup = $1",
            self._lookup(None, slug),
        )
        raise SkillError("shipped_read_only" if shipped else "not_found")

    @staticmethod
    def _require_grant(row: asyncpg.Record, actor: str) -> None:
        if actor not in _ACCOUNT_ACTORS:
            raise SkillError("actor_invalid")
        if actor in _GRANT_ACTORS and row["allow_autonomous_edit"] is not True:
            raise SkillError("autonomous_edit_not_granted")

    async def _append_revision(
        self,
        conn: _Connection,
        skill_id: uuid.UUID,
        revision: int,
        envelopes: tuple[str, str, str, str],
        actor: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO skill_revisions (skill_id, revision, name_encrypted, "
            "description_encrypted, body_encrypted, trigger_conditions_encrypted, actor) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            skill_id,
            revision,
            *envelopes,
            actor,
        )
        await conn.execute(
            "DELETE FROM skill_revisions WHERE skill_id = $1 AND revision <= $2",
            skill_id,
            revision - REVISION_RETENTION,
        )

    async def _move_head(
        self,
        conn: _Connection,
        skill_id: uuid.UUID,
        revision: int,
        envelopes: tuple[str, str, str, str],
    ) -> asyncpg.Record:
        await conn.execute(
            "UPDATE skills SET name_encrypted = $3, description_encrypted = $4, "
            "body_encrypted = $5, trigger_conditions_encrypted = $6, revision = $2, "
            "updated_at = NOW() WHERE id = $1",
            skill_id,
            revision,
            *envelopes,
        )
        return await self._head(conn, skill_id)

    def _encrypt_content(
        self, name: str, description: str, body: str, trigger_conditions: str
    ) -> tuple[str, str, str, str]:
        return (
            self._enc.encrypt(name),
            self._enc.encrypt(description),
            self._enc.encrypt(body),
            self._enc.encrypt(trigger_conditions),
        )

    # ------------------------------------------------------------------
    # Account writes
    # ------------------------------------------------------------------

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        name: str,
        description: str,
        body: str,
        trigger_conditions: str = "",
        actor: Actor = "user",
        imported: bool = False,
        complexity_origin: int = 0,
    ) -> dict[str, Any]:
        """Create a skill owned by ``user_id``.

        Source type and the autonomous-edit grant are derived from ``actor``:
        a skill the evaluator creates starts open to autonomous edits (it may
        refine its own work, as today) until the owner revokes the grant; a
        skill a person writes or imports starts closed.
        """
        if actor not in _ACCOUNT_ACTORS:
            raise SkillError("actor_invalid")
        name = _validated_text(name, MAX_NAME_CHARS, "name", required=True)
        description = _validated_text(description, MAX_DESCRIPTION_CHARS, "description")
        body = _validated_text(body, MAX_BODY_CHARS, "body")
        trigger_conditions = _validated_text(
            trigger_conditions, MAX_TRIGGER_CONDITIONS_CHARS, "trigger_conditions"
        )
        slug = _slug_from_name(name)
        autonomous = actor in _GRANT_ACTORS
        source_type = "autonomous" if autonomous else ("imported" if imported else "manual")

        skill_id = uuid.uuid4()
        # Encrypt before taking the account lock.
        envelopes = self._encrypt_content(name, description, body, trigger_conditions)
        slug_envelope = self._enc.encrypt(slug)
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                # Same per-account serialization point as reminders and web
                # snapshots, so the count limit holds across processes.
                found = await conn.fetchval(
                    "SELECT id FROM users WHERE id = $1 FOR NO KEY UPDATE", user_id
                )
                if found is None:
                    raise SkillError("account_unavailable")
                count = await conn.fetchval(
                    "SELECT count(*) FROM skills WHERE user_id = $1", user_id
                )
                if count >= MAX_SKILLS_PER_ACCOUNT:
                    raise SkillError("limit_reached")
                await conn.execute(
                    "INSERT INTO skills (id, user_id, slug_encrypted, slug_lookup, "
                    "name_encrypted, description_encrypted, body_encrypted, "
                    "trigger_conditions_encrypted, source_type, allow_autonomous_edit, "
                    "complexity_origin) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::skill_source_type, $10, $11)",
                    skill_id,
                    user_id,
                    slug_envelope,
                    self._lookup(user_id, slug),
                    *envelopes,
                    source_type,
                    autonomous,
                    complexity_origin,
                )
                await self._append_revision(conn, skill_id, 1, envelopes, actor)
                row = await self._head(conn, skill_id)
        except asyncpg.UniqueViolationError as exc:
            raise SkillError("slug_taken") from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise SkillError("account_unavailable") from exc
        return self._decode_head(row)

    async def save(
        self,
        user_id: uuid.UUID,
        slug: str,
        *,
        name: str | None = None,
        description: str | None = None,
        body: str | None = None,
        trigger_conditions: str | None = None,
        actor: Actor = "user",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Save new content as the next revision. ``None`` keeps a field.

        The slug is the skill's stable address and does not change on rename.
        """
        slug = _slug_from_id(slug)
        if name is not None:
            name = _validated_text(name, MAX_NAME_CHARS, "name", required=True)
        if description is not None:
            description = _validated_text(description, MAX_DESCRIPTION_CHARS, "description")
        if body is not None:
            body = _validated_text(body, MAX_BODY_CHARS, "body")
        if trigger_conditions is not None:
            trigger_conditions = _validated_text(
                trigger_conditions, MAX_TRIGGER_CONDITIONS_CHARS, "trigger_conditions"
            )
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._lock_account_skill(conn, user_id, slug)
            self._require_grant(row, actor)
            if expected_revision is not None and expected_revision != row["revision"]:
                raise SkillError("revision_conflict")
            envelopes = (
                self._enc.encrypt(name) if name is not None else row["name_encrypted"],
                self._enc.encrypt(description)
                if description is not None
                else row["description_encrypted"],
                self._enc.encrypt(body) if body is not None else row["body_encrypted"],
                self._enc.encrypt(trigger_conditions)
                if trigger_conditions is not None
                else row["trigger_conditions_encrypted"],
            )
            revision = row["revision"] + 1
            await self._append_revision(conn, row["id"], revision, envelopes, actor)
            head = await self._move_head(conn, row["id"], revision, envelopes)
        return self._decode_head(head)

    async def revert(
        self,
        user_id: uuid.UUID,
        slug: str,
        revision: int,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Write a retained revision's content as a new revision. Owner only."""
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._lock_account_skill(conn, user_id, slug)
            if expected_revision is not None and expected_revision != row["revision"]:
                raise SkillError("revision_conflict")
            old = await conn.fetchrow(
                "SELECT name_encrypted, description_encrypted, body_encrypted, "
                "trigger_conditions_encrypted FROM skill_revisions "
                "WHERE skill_id = $1 AND revision = $2",
                row["id"],
                revision,
            )
            if old is None:
                raise SkillError("revision_not_found")
            envelopes = (
                old["name_encrypted"],
                old["description_encrypted"],
                old["body_encrypted"],
                old["trigger_conditions_encrypted"],
            )
            next_revision = row["revision"] + 1
            await self._append_revision(conn, row["id"], next_revision, envelopes, "user")
            head = await self._move_head(conn, row["id"], next_revision, envelopes)
        return self._decode_head(head)

    async def delete(self, user_id: uuid.UUID, slug: str, *, actor: Actor = "user") -> None:
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._lock_account_skill(conn, user_id, slug)
            self._require_grant(row, actor)
            await conn.execute("DELETE FROM skills WHERE id = $1", row["id"])

    async def set_enabled(self, user_id: uuid.UUID, slug: str, enabled: bool) -> dict[str, Any]:
        """Owner-only switch; not a content revision."""
        if not isinstance(enabled, bool):
            raise SkillError("enabled_invalid")
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._lock_account_skill(conn, user_id, slug)
            await conn.execute(
                "UPDATE skills SET enabled = $2, updated_at = NOW() WHERE id = $1",
                row["id"],
                enabled,
            )
            head = await self._head(conn, row["id"])
        return self._decode_head(head)

    async def set_autonomous_edit(
        self, user_id: uuid.UUID, slug: str, allowed: bool
    ) -> dict[str, Any]:
        """The owner's grant. This is the only way the grant changes after creation."""
        if not isinstance(allowed, bool):
            raise SkillError("allowed_invalid")
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._lock_account_skill(conn, user_id, slug)
            await conn.execute(
                "UPDATE skills SET allow_autonomous_edit = $2, updated_at = NOW() WHERE id = $1",
                row["id"],
                allowed,
            )
            head = await self._head(conn, row["id"])
        return self._decode_head(head)

    # ------------------------------------------------------------------
    # Shipped skills
    # ------------------------------------------------------------------

    async def publish_shipped(
        self, slug: str, *, name: str, description: str, body: str
    ) -> dict[str, Any]:
        """Create or update a shipped skill from application content.

        Called only by the application's own loader, never with request data.
        Unchanged content writes nothing.
        """
        slug = _slug_from_id(slug)
        name = _validated_text(name, MAX_NAME_CHARS, "name", required=True)
        description = _validated_text(description, MAX_DESCRIPTION_CHARS, "description")
        body = _validated_text(body, MAX_BODY_CHARS, "body")
        envelopes = self._encrypt_content(name, description, body, "")
        lookup = self._lookup(None, slug)
        async with self._pool.acquire() as conn, conn.transaction():
            # Serialize publication of one slug across processes.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", lookup.hex()
            )
            existing = await conn.fetchval(
                "SELECT id FROM skills WHERE user_id IS NULL AND slug_lookup = $1 FOR UPDATE",
                lookup,
            )
            if existing is None:
                skill_id = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO skills (id, user_id, slug_encrypted, slug_lookup, "
                    "name_encrypted, description_encrypted, body_encrypted, "
                    "trigger_conditions_encrypted, source_type) "
                    "VALUES ($1, NULL, $2, $3, $4, $5, $6, $7, 'system')",
                    skill_id,
                    self._enc.encrypt(slug),
                    lookup,
                    *envelopes,
                )
                await self._append_revision(conn, skill_id, 1, envelopes, "shipped")
                head = await self._head(conn, skill_id)
            else:
                row = await self._head(conn, existing)
                current = self._decode_head(row)
                if (current["name"], current["description"], current["body"]) == (
                    name,
                    description,
                    body,
                ):
                    return current
                revision = row["revision"] + 1
                await self._append_revision(conn, row["id"], revision, envelopes, "shipped")
                head = await self._move_head(conn, row["id"], revision, envelopes)
        return self._decode_head(head)

    async def remove_shipped(self, slug: str) -> bool:
        slug = _slug_from_id(slug)
        result = await self._pool.execute(
            "DELETE FROM skills WHERE user_id IS NULL AND slug_lookup = $1",
            self._lookup(None, slug),
        )
        return result == "DELETE 1"

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, user_id: uuid.UUID, slug: str) -> dict[str, Any]:
        """The caller's own skill, else the shipped skill with that slug."""
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn:
            skill_id = await conn.fetchval(
                "SELECT id FROM skills WHERE (user_id = $1 AND slug_lookup = $2) "
                "OR (user_id IS NULL AND slug_lookup = $3) "
                "ORDER BY user_id NULLS LAST LIMIT 1",
                user_id,
                self._lookup(user_id, slug),
                self._lookup(None, slug),
            )
            if skill_id is None:
                raise SkillError("not_found")
            return self._decode_head(await self._head(conn, skill_id))

    async def list(
        self, user_id: uuid.UUID, *, enabled: bool | None = None, include_shipped: bool = True
    ) -> list[dict[str, Any]]:
        """The account's skills, then shipped skills its own slugs do not shadow."""
        async with self._pool.acquire() as conn:
            ids = await conn.fetch(
                "SELECT id FROM skills WHERE (user_id = $1 OR ($3 AND user_id IS NULL)) "
                "AND ($2::boolean IS NULL OR enabled = $2) "
                "ORDER BY user_id NULLS LAST, created_at, id",
                user_id,
                enabled,
                include_shipped,
            )
            rows = await self._heads(conn, [row["id"] for row in ids])
        skills: list[dict[str, Any]] = []
        own_slugs: set[str] = set()
        for row in rows:
            skill = self._decode_head(row)
            if skill["scope"] == "account":
                own_slugs.add(skill["slug"])
            elif skill["slug"] in own_slugs:
                continue
            skills.append(skill)
        return skills

    async def list_revisions(self, user_id: uuid.UUID, slug: str) -> list[dict[str, Any]]:
        """Retained revisions of the caller's own skill, newest first."""
        slug = _slug_from_id(slug)
        rows = await self._pool.fetch(
            "SELECT r.revision, r.name_encrypted, r.description_encrypted, r.body_encrypted, "
            "r.trigger_conditions_encrypted, r.actor, r.created_at "
            "FROM skill_revisions r JOIN skills s ON s.id = r.skill_id "
            "WHERE s.user_id = $1 AND s.slug_lookup = $2 ORDER BY r.revision DESC",
            user_id,
            self._lookup(user_id, slug),
        )
        if not rows:
            raise SkillError("not_found")
        return [
            {
                "revision": row["revision"],
                "name": self._enc.decrypt(row["name_encrypted"]),
                "description": self._enc.decrypt(row["description_encrypted"]),
                "body": self._enc.decrypt(row["body_encrypted"]),
                "trigger_conditions": self._enc.decrypt(row["trigger_conditions_encrypted"]),
                "actor": row["actor"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    async def store_embedding(
        self, skill_id: uuid.UUID, revision: int, embedding: list[float]
    ) -> bool:
        """Store an embedding only if ``revision`` is still the head.

        Returns False when a newer save has landed, so a slow embedding call
        never describes content that is no longer current.
        """
        if len(embedding) != EMBEDDING_DIMENSIONS:
            raise SkillError("embedding_invalid")
        result = await self._pool.execute(
            "UPDATE skills SET embedding = $3::vector, embedding_revision = $2 "
            "WHERE id = $1 AND revision = $2",
            skill_id,
            revision,
            _format_vector(embedding),
        )
        return result == "UPDATE 1"

    async def touch_usage(self, user_id: uuid.UUID, slug: str) -> bool:
        """Count a use of the caller's own skill."""
        slug = _slug_from_id(slug)
        result = await self._pool.execute(
            "UPDATE skills SET use_count = use_count + 1, last_used_at = NOW() "
            "WHERE user_id = $1 AND slug_lookup = $2",
            user_id,
            self._lookup(user_id, slug),
        )
        return result == "UPDATE 1"


def create_skill_store(pool: asyncpg.Pool, settings: Settings | None = None) -> SkillStore:
    """Build a store from the shared pool; a deployment with no key fails closed."""
    resolved = settings if settings is not None else get_settings()
    encryption = ContentEncryption(resolved.daemon_encryption_key)
    # ContentEncryption has already refused a missing or malformed key.
    return SkillStore(
        pool, encryption, derive_slug_lookup_key(resolved.daemon_encryption_key or "")
    )
