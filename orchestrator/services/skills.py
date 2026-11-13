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
- The database is bound to one encryption key (``skill_store_key``). Every
  operation checks the configured key against that binding first, so a
  misconfigured key refuses instead of reading the store as empty or writing
  rows that the correct key cannot read. The first write binds the key;
  :func:`rotate_skill_store_key` is the only way the binding changes.
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
_KEY_VERIFIER_INFO = b"daemon/skills/key-verifier/v1"
_KEY_VERIFIER_MESSAGE = b"skill-store-key-binding"
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


def _derive(encryption_key: str, info: bytes) -> bytes:
    try:
        material = base64.urlsafe_b64decode(encryption_key.encode())
    except (ValueError, TypeError) as exc:
        raise EncryptionKeyMissing("DAEMON_ENCRYPTION_KEY is not valid base64") from exc
    if len(material) != 32:
        raise EncryptionKeyMissing("DAEMON_ENCRYPTION_KEY must decode to 32 bytes")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(material)


def derive_slug_lookup_key(encryption_key: str) -> bytes:
    """Derive the slug-lookup key from the content-encryption key.

    The digest key is separate from the Fernet key so a digest never exposes
    key material used for encryption. A different encryption key gives a
    different lookup key, which is why the store is bound to one key and
    rotation recomputes ``slug_lookup`` (:func:`rotate_skill_store_key`).
    """
    return _derive(encryption_key, _SLUG_LOOKUP_INFO)


def derive_key_verifier(encryption_key: str) -> bytes:
    """A keyed check value identifying the encryption key, not any content.

    It is an HMAC of a fixed message under its own derived key, so it reveals
    neither the encryption key nor the lookup key, and says nothing about what
    the store holds.
    """
    return hmac.new(
        _derive(encryption_key, _KEY_VERIFIER_INFO), _KEY_VERIFIER_MESSAGE, hashlib.sha256
    ).digest()


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


def slug_for_name(name: str) -> str:
    """The slug a skill created with ``name`` is addressed by."""
    return _slug_from_name(name)


def _format_vector(embedding: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"


class SkillStore:
    def __init__(self, pool: asyncpg.Pool, encryption_key: str) -> None:
        """Bind the content cipher and the slug-lookup key to one key.

        They are never supplied separately: a store that could find a row by
        slug but not read it would otherwise be constructible by mistake.
        """
        self._pool = pool
        self._enc = ContentEncryption(encryption_key)
        self._lookup_key = derive_slug_lookup_key(encryption_key)
        self._verifier = derive_key_verifier(encryption_key)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _verify_key_for_write(self, conn: _Connection) -> None:
        """Refuse unless this store's key is the one the database is bound to.

        Must be the first statement of a write transaction. The first write
        ever binds the key: concurrent first writers race on the insert, one
        wins, and every other one then compares against the winner's binding.
        The shared row lock is held to commit, so a rotation (which takes the
        row exclusively) never overlaps a write under the old key.
        """
        await conn.execute(
            "INSERT INTO skill_store_key (verifier) VALUES ($1) ON CONFLICT (singleton) DO NOTHING",
            self._verifier,
        )
        bound = await conn.fetchval(
            "SELECT verifier FROM skill_store_key WHERE singleton FOR SHARE"
        )
        if bound is None or not hmac.compare_digest(bytes(bound), self._verifier):
            raise SkillError("encryption_key_mismatch")

    async def _verify_key_for_read(self, conn: _Connection) -> None:
        """Refuse a read under a key the database is not bound to.

        No binding means no write has ever committed, so the store really is
        empty. With a binding, a lookup under any other key would find nothing
        and must not be reported as "not found" or as an empty list.
        """
        bound = await conn.fetchval("SELECT verifier FROM skill_store_key WHERE singleton")
        if bound is not None and not hmac.compare_digest(bytes(bound), self._verifier):
            raise SkillError("encryption_key_mismatch")

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
            row = await self._head(conn, skill_id)
            # Refuse before any mutation if the active key cannot read the
            # row: writing next to ciphertext this key cannot decrypt would
            # commit a skill that no single key can read.
            self._decode_head(row)
            return row
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
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create a skill owned by ``user_id``.

        ``enabled`` is part of the same insert: a skill requested disabled is
        never visible enabled, not even briefly.

        Source type and the autonomous-edit grant are derived from ``actor``:
        a skill the evaluator creates starts open to autonomous edits (it may
        refine its own work, as today) until the owner revokes the grant; a
        skill a person writes or imports starts closed.
        """
        if actor not in _ACCOUNT_ACTORS:
            raise SkillError("actor_invalid")
        if not isinstance(enabled, bool):
            raise SkillError("enabled_invalid")
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
                await self._verify_key_for_write(conn)
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
                    "complexity_origin, enabled) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::skill_source_type, $10, $11, "
                    "$12)",
                    skill_id,
                    user_id,
                    slug_envelope,
                    self._lookup(user_id, slug),
                    *envelopes,
                    source_type,
                    autonomous,
                    complexity_origin,
                    enabled,
                )
                await self._append_revision(conn, skill_id, 1, envelopes, actor)
                # Decode inside the transaction: a failure rolls the write back.
                created = self._decode_head(await self._head(conn, skill_id))
        except asyncpg.UniqueViolationError as exc:
            raise SkillError("slug_taken") from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise SkillError("account_unavailable") from exc
        return created

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
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Save new content as the next revision. ``None`` keeps a field.

        The slug is the skill's stable address and does not change on rename.
        ``enabled`` is the owner's switch, applied in the same transaction as
        the content so the two are never visible half-applied.
        """
        slug = _slug_from_id(slug)
        if enabled is not None and (not isinstance(enabled, bool) or actor != "user"):
            raise SkillError("enabled_invalid")
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
            await self._verify_key_for_write(conn)
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
            if enabled is not None:
                await conn.execute(
                    "UPDATE skills SET enabled = $2 WHERE id = $1", row["id"], enabled
                )
            head = await self._move_head(conn, row["id"], revision, envelopes)
            # Decoded before commit: a failure rolls the write back.
            return self._decode_head(head)

    async def patch(
        self,
        user_id: uuid.UUID,
        slug: str,
        old_text: str,
        new_text: str,
        *,
        actor: Actor = "autonomous",
        trigger_conditions: str | None = None,
        complexity_origin: int | None = None,
    ) -> dict[str, Any]:
        """Replace the first occurrence of ``old_text`` in the body.

        The body is read under the row lock, so concurrent patches cannot
        interleave and a grant revoked before the lock stops the write.
        """
        slug = _slug_from_id(slug)
        if not isinstance(old_text, str) or not old_text:
            raise SkillError("patch_text_required")
        if not isinstance(new_text, str):
            raise SkillError("patch_text_invalid")
        if trigger_conditions is not None:
            trigger_conditions = _validated_text(
                trigger_conditions, MAX_TRIGGER_CONDITIONS_CHARS, "trigger_conditions"
            )
        async with self._pool.acquire() as conn, conn.transaction():
            await self._verify_key_for_write(conn)
            row = await self._lock_account_skill(conn, user_id, slug)
            self._require_grant(row, actor)
            current = self._enc.decrypt(row["body_encrypted"])
            if old_text not in current:
                raise SkillError("patch_text_not_found")
            before, _, after = current.partition(old_text)
            body = _validated_text(f"{before}{new_text}{after}", MAX_BODY_CHARS, "body")
            envelopes = (
                row["name_encrypted"],
                row["description_encrypted"],
                self._enc.encrypt(body),
                self._enc.encrypt(trigger_conditions)
                if trigger_conditions is not None
                else row["trigger_conditions_encrypted"],
            )
            revision = row["revision"] + 1
            await self._append_revision(conn, row["id"], revision, envelopes, actor)
            if complexity_origin is not None:
                await conn.execute(
                    "UPDATE skills SET complexity_origin = GREATEST(complexity_origin, $2) "
                    "WHERE id = $1",
                    row["id"],
                    complexity_origin,
                )
            head = await self._move_head(conn, row["id"], revision, envelopes)
            # Decoded before commit: a failure rolls the write back.
            return self._decode_head(head)

    async def merge(
        self,
        user_id: uuid.UUID,
        kept_slug: str,
        absorbed_slugs: list[str],
        *,
        actor: Actor = "consolidation",
        audit_run_id: uuid.UUID | None = None,
        audit_similarity: float | None = None,
    ) -> dict[str, Any]:
        """Fold the absorbed skills' bodies into the kept skill, then delete them.

        One transaction: every skill involved is the caller's own and, for an
        unattended actor, carries the grant, or nothing changes.

        With ``audit_run_id`` the consolidation audit rows (one ``merge`` and
        one ``delete`` per absorbed skill) are written in the same
        transaction, so a merge never commits without its audit record and an
        audit failure rolls the merge back. Their ids are returned as
        ``audit_ids``, in that order.
        """
        kept = _slug_from_id(kept_slug)
        absorbed = [_slug_from_id(slug) for slug in absorbed_slugs]
        if not absorbed or kept in absorbed or len(set(absorbed)) != len(absorbed):
            raise SkillError("merge_invalid")
        async with self._pool.acquire() as conn, conn.transaction():
            await self._verify_key_for_write(conn)
            # Lock in one order regardless of the caller's, so two merges
            # over the same skills cannot deadlock.
            rows: dict[str, asyncpg.Record] = {}
            for slug in sorted([kept, *absorbed]):
                rows[slug] = await self._lock_account_skill(conn, user_id, slug)
                self._require_grant(rows[slug], actor)
            parts = [self._enc.decrypt(rows[kept]["body_encrypted"])]
            for slug in absorbed:
                name = self._enc.decrypt(rows[slug]["name_encrypted"])
                body = self._enc.decrypt(rows[slug]["body_encrypted"])
                parts.append(f"\n\n-- Merged from {name} --\n{body}")
            body = _validated_text("\n".join(parts), MAX_BODY_CHARS, "body")
            row = rows[kept]
            envelopes = (
                row["name_encrypted"],
                row["description_encrypted"],
                self._enc.encrypt(body),
                row["trigger_conditions_encrypted"],
            )
            revision = row["revision"] + 1
            await self._append_revision(conn, row["id"], revision, envelopes, actor)
            audit_ids: list[uuid.UUID] = []
            for slug in absorbed:
                gone = rows[slug]
                await conn.execute("DELETE FROM skills WHERE id = $1", gone["id"])
                if audit_run_id is None:
                    continue
                merge_id, delete_id = uuid.uuid4(), uuid.uuid4()
                await conn.execute(
                    "INSERT INTO skill_consolidation_log (id, user_id, run_id, action_type, "
                    "skill_id, target_skill_id, reason, similarity, status, skill_use_count, "
                    "skill_last_used_at) "
                    "VALUES ($1, $2, $3, 'merge', $4, $5, 'model_merge', $6, 'applied', $7, $8)",
                    merge_id,
                    user_id,
                    audit_run_id,
                    row["id"],
                    gone["id"],
                    audit_similarity,
                    row["use_count"],
                    row["last_used_at"],
                )
                await conn.execute(
                    "INSERT INTO skill_consolidation_log (id, user_id, run_id, action_type, "
                    "skill_id, target_skill_id, reason, status, skill_use_count, "
                    "skill_last_used_at) "
                    "VALUES ($1, $2, $3, 'delete', $4, $5, 'merged_into', 'applied', $6, $7)",
                    delete_id,
                    user_id,
                    audit_run_id,
                    gone["id"],
                    row["id"],
                    gone["use_count"],
                    gone["last_used_at"],
                )
                audit_ids += [merge_id, delete_id]
            head = await self._move_head(conn, row["id"], revision, envelopes)
            # Decoded before commit: a failure rolls the write back.
            merged = self._decode_head(head)
            if audit_run_id is not None:
                merged["audit_ids"] = audit_ids
            return merged

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
            await self._verify_key_for_write(conn)
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
            for envelope in envelopes:
                self._enc.decrypt(envelope)
            next_revision = row["revision"] + 1
            await self._append_revision(conn, row["id"], next_revision, envelopes, "user")
            head = await self._move_head(conn, row["id"], next_revision, envelopes)
            # Decoded before commit: a failure rolls the write back.
            return self._decode_head(head)

    async def delete(self, user_id: uuid.UUID, slug: str, *, actor: Actor = "user") -> None:
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn, conn.transaction():
            await self._verify_key_for_write(conn)
            row = await self._lock_account_skill(conn, user_id, slug)
            self._require_grant(row, actor)
            await conn.execute("DELETE FROM skills WHERE id = $1", row["id"])

    async def set_enabled(self, user_id: uuid.UUID, slug: str, enabled: bool) -> dict[str, Any]:
        """Owner-only switch; not a content revision."""
        if not isinstance(enabled, bool):
            raise SkillError("enabled_invalid")
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn, conn.transaction():
            await self._verify_key_for_write(conn)
            row = await self._lock_account_skill(conn, user_id, slug)
            await conn.execute(
                "UPDATE skills SET enabled = $2, updated_at = NOW() WHERE id = $1",
                row["id"],
                enabled,
            )
            head = await self._head(conn, row["id"])
            # Decoded before commit: a failure rolls the write back.
            return self._decode_head(head)

    async def set_autonomous_edit(
        self, user_id: uuid.UUID, slug: str, allowed: bool
    ) -> dict[str, Any]:
        """The owner's grant. This is the only way the grant changes after creation."""
        if not isinstance(allowed, bool):
            raise SkillError("allowed_invalid")
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn, conn.transaction():
            await self._verify_key_for_write(conn)
            row = await self._lock_account_skill(conn, user_id, slug)
            await conn.execute(
                "UPDATE skills SET allow_autonomous_edit = $2, updated_at = NOW() WHERE id = $1",
                row["id"],
                allowed,
            )
            head = await self._head(conn, row["id"])
            # Decoded before commit: a failure rolls the write back.
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
            await self._verify_key_for_write(conn)
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
            # Decoded before commit: a failure rolls the write back.
            return self._decode_head(head)

    async def remove_shipped(self, slug: str) -> bool:
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn, conn.transaction():
            await self._verify_key_for_write(conn)
            result = await conn.execute(
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
            await self._verify_key_for_read(conn)
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
            await self._verify_key_for_read(conn)
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

    async def search_by_embedding(
        self,
        user_id: uuid.UUID,
        embedding: list[float],
        *,
        limit: int = 5,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """The account's own enabled skills nearest to ``embedding``, most similar first.

        Never returns shipped skills or another account's: a match is offered
        for refinement, and only the account's own skills can be refined.
        """
        if len(embedding) != EMBEDDING_DIMENSIONS:
            raise SkillError("embedding_invalid")
        async with self._pool.acquire() as conn:
            await self._verify_key_for_read(conn)
            matches = await conn.fetch(
                "SELECT id, 1 - (embedding <=> $2::vector) AS similarity FROM skills "
                "WHERE user_id = $1 AND enabled AND embedding IS NOT NULL "
                "AND embedding_revision = revision "
                "AND 1 - (embedding <=> $2::vector) >= $3 "
                "ORDER BY embedding <=> $2::vector, id LIMIT $4",
                user_id,
                _format_vector(embedding),
                min_similarity,
                limit,
            )
            rows = await self._heads(conn, [match["id"] for match in matches])
        similarity = {match["id"]: float(match["similarity"]) for match in matches}
        return [{**self._decode_head(row), "similarity": similarity[row["id"]]} for row in rows]

    async def list_autonomous_candidates(
        self, user_id: uuid.UUID, *, limit: int
    ) -> list[dict[str, Any]]:
        """The account's enabled autonomous skills with a current embedding.

        These are what consolidation may merge or retire; each carries its
        embedding so near-duplicates can be grouped without another query.
        """
        async with self._pool.acquire() as conn:
            await self._verify_key_for_read(conn)
            candidates = await conn.fetch(
                "SELECT id, embedding::text AS embedding FROM skills "
                "WHERE user_id = $1 AND source_type = 'autonomous' AND enabled "
                "AND embedding IS NOT NULL AND embedding_revision = revision "
                "ORDER BY use_count DESC, created_at, id LIMIT $2",
                user_id,
                limit,
            )
            rows = await self._heads(conn, [candidate["id"] for candidate in candidates])
        vectors = {
            candidate["id"]: [float(value) for value in candidate["embedding"][1:-1].split(",")]
            for candidate in candidates
        }
        return [{**self._decode_head(row), "embedding": vectors[row["id"]]} for row in rows]

    async def list_revisions(self, user_id: uuid.UUID, slug: str) -> list[dict[str, Any]]:
        """Retained revisions of the caller's own skill, newest first."""
        slug = _slug_from_id(slug)
        async with self._pool.acquire() as conn:
            await self._verify_key_for_read(conn)
            rows = await conn.fetch(
                "SELECT r.revision, r.name_encrypted, r.description_encrypted, "
                "r.body_encrypted, r.trigger_conditions_encrypted, r.actor, r.created_at "
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
        async with self._pool.acquire() as conn, conn.transaction():
            await self._verify_key_for_write(conn)
            result = await conn.execute(
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
        async with self._pool.acquire() as conn, conn.transaction():
            await self._verify_key_for_write(conn)
            result = await conn.execute(
                "UPDATE skills SET use_count = use_count + 1, last_used_at = NOW() "
                "WHERE user_id = $1 AND slug_lookup = $2",
                user_id,
                self._lookup(user_id, slug),
            )
        return result == "UPDATE 1"


def create_skill_store(pool: asyncpg.Pool, settings: Settings | None = None) -> SkillStore:
    """Build a store from the shared pool; a deployment with no key fails closed."""
    resolved = settings if settings is not None else get_settings()
    key = resolved.daemon_encryption_key
    if not key:
        raise EncryptionKeyMissing("DAEMON_ENCRYPTION_KEY is required")
    return SkillStore(pool, key)


async def rotate_skill_store_key(pool: asyncpg.Pool, old_key: str, new_key: str) -> int:
    """Re-encrypt every skill under ``new_key`` and rebind the store to it.

    One transaction: it takes the key binding exclusively, so no write under
    the old key overlaps it and writers that were waiting see the new binding
    and refuse. Every envelope is decrypted with ``old_key`` first; any row the
    old key cannot read aborts the whole rotation with nothing changed. Slug
    lookup digests are recomputed, since they depend on the key.

    Returns the number of skills re-encrypted. The caller is responsible for
    switching the deployment's configured key to ``new_key`` afterwards; until
    then every store built from the old key refuses.
    """
    old, new = SkillStore(pool, old_key), SkillStore(pool, new_key)

    def recode(envelope: str) -> str:
        return new._enc.encrypt(old._enc.decrypt(envelope))

    async with pool.acquire() as conn, conn.transaction():
        bound = await conn.fetchval(
            "SELECT verifier FROM skill_store_key WHERE singleton FOR UPDATE"
        )
        if bound is None:
            # Nothing has ever been written, so there is nothing to re-encrypt
            # and no binding to move: the first write under the new key binds.
            return 0
        if not hmac.compare_digest(bytes(bound), old._verifier):
            raise SkillError("encryption_key_mismatch")
        skills = await conn.fetch(
            "SELECT id, user_id, slug_encrypted, name_encrypted, description_encrypted, "
            "body_encrypted, trigger_conditions_encrypted FROM skills ORDER BY id FOR UPDATE"
        )
        for row in skills:
            slug = old._enc.decrypt(row["slug_encrypted"])
            await conn.execute(
                "UPDATE skills SET slug_encrypted = $2, slug_lookup = $3, name_encrypted = $4, "
                "description_encrypted = $5, body_encrypted = $6, "
                "trigger_conditions_encrypted = $7 WHERE id = $1",
                row["id"],
                new._enc.encrypt(slug),
                new._lookup(row["user_id"], slug),
                recode(row["name_encrypted"]),
                recode(row["description_encrypted"]),
                recode(row["body_encrypted"]),
                recode(row["trigger_conditions_encrypted"]),
            )
        revisions = await conn.fetch(
            "SELECT skill_id, revision, name_encrypted, description_encrypted, "
            "body_encrypted, trigger_conditions_encrypted FROM skill_revisions "
            "ORDER BY skill_id, revision"
        )
        for row in revisions:
            await conn.execute(
                "UPDATE skill_revisions SET name_encrypted = $3, description_encrypted = $4, "
                "body_encrypted = $5, trigger_conditions_encrypted = $6 "
                "WHERE skill_id = $1 AND revision = $2",
                row["skill_id"],
                row["revision"],
                recode(row["name_encrypted"]),
                recode(row["description_encrypted"]),
                recode(row["body_encrypted"]),
                recode(row["trigger_conditions_encrypted"]),
            )
        await conn.execute(
            "UPDATE skill_store_key SET verifier = $1, bound_at = NOW() WHERE singleton",
            new._verifier,
        )
    return len(skills)
