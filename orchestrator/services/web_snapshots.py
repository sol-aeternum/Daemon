"""Conversation-scoped web reading snapshot persistence.

This module owns the durable half of chunked web reading: an immutable,
encrypted, owner- and conversation-scoped snapshot of one extracted page, plus
the transactional quota admission that decides whether a new snapshot may be
retained. It deliberately does *not* fetch anything.

Design references: ``docs/CHUNKED_WEB_READING_DESIGN.md`` ("Storage choice and
lifecycle"). Schema: ``migrations/041_web_snapshots.sql``.

Security and concurrency invariants
-----------------------------------
Owner and conversation are always a pair.
    Every read, lookup and delete takes ``(user_id, conversation_id)`` together
    and filters on both in SQL. A caller that supplies a valid snapshot id with
    the wrong conversation or the wrong account gets ``WebSnapshotNotFound`` /
    ``WebSnapshotOwnerMismatch`` — never another account's bytes. Model-supplied
    arguments cannot select the owner or the conversation; the caller (tool
    registry / route dependency) injects them from authenticated state.

Authorization is never derived from a guessable value.
    ``id`` is a random UUID4 and both fingerprints are keyed HMAC-SHA256 digests
    with domain separation and account scoping. Neither is an authority: they
    only narrow a query that is already constrained by owner and conversation.
    There is no unique index on a fingerprint and no global (cross-account)
    content dedup, so one account's page text can never be matched, merged or
    deduplicated against another's.

Admission is atomic and serialized.
    ``create()`` runs one transaction that (1) locks the owner's ``users`` row
    ``FOR NO KEY UPDATE`` — the per-account serialization point, so two concurrent
    saves for the same account cannot both read the same pre-insert usage sum
    and both pass; (2) locks and re-reads the owned ``conversations`` row
    ``FOR NO KEY UPDATE``, which both proves ownership and blocks behind a concurrent
    conversation deletion; (3) deletes that account's already-expired rows so a
    delayed cleanup sweep can never consume live quota; (4) recomputes
    conversation and account byte/count usage from retained rows only; (5)
    inserts. Fetching happens *before* any of this, so no network I/O is
    performed while holding a row lock.

Deletion races fail closed.
    If the conversation (or the account) is deleted while a fetch is in flight,
    the locked re-read finds no owned row and the save is rejected with
    ``WebSnapshotOwnerMismatch``. If a delete commits between the re-read and
    the insert, the foreign key raises and is translated into the same typed
    error. The store never recreates a conversation and never writes an orphan
    snapshot.

Expiry is enforced on read, not only by cleanup.
    Every selecting query carries ``expires_at > $now``. A delayed, failed or
    never-scheduled ``purge_expired()`` therefore cannot resurrect retained-but-
    expired content; it only affects how much quota stale rows occupy, and
    ``create()`` purges the account's expired rows before it sums usage.

Encryption fails closed.
    Metadata (source URL, final URL, title, extract mode, extraction version)
    and page text are encrypted as two independent Fernet envelopes, so a
    bounded listing never decrypts a body. A missing key raises at
    ``ContentEncryption`` construction; a corrupt or wrongly-keyed envelope
    raises ``WebSnapshotIntegrityError`` rather than degrading to ``None``,
    empty text, or a partially populated record. One corrupt row therefore
    surfaces as an error instead of silently vanishing from a listing and
    misreporting retained sources and quota.

Nothing sensitive reaches logs or exception text.
    URLs, titles, page text, ciphertext and fingerprint digests are never
    logged and never interpolated into an error message. Log records and
    ``str(exc)`` carry only stable reason codes and the random UUIDs involved,
    which is why the typed errors take no payload arguments. Callers must map
    them to generic HTTP details.

Caller-side bounds this module does not enforce
-----------------------------------------------
``web_snapshot_max_response_bytes`` belongs to the transport that streams the
decoded body, and ``web_snapshot_max_new_per_turn`` belongs to the loop-owned
per-turn allowance object. Both are exposed on ``self.settings`` for those
owners; neither is a storage quota, and this store deliberately does not
re-implement either one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
from typing import Any, Final, NoReturn, TypeAlias, cast
import uuid

import asyncpg
import asyncpg.pool

from orchestrator.auth_pepper import validate_and_get_pepper
from orchestrator.config import Settings, get_settings
from orchestrator.memory.encryption import ContentEncryption


logger = logging.getLogger(__name__)

#: A pooled connection. ``pool.acquire()`` yields a proxy rather than a raw
#: ``asyncpg.Connection``; the query methods are identical either way. This is
#: the same alias ``orchestrator/entitlements/store.py`` uses.
Connection: TypeAlias = asyncpg.pool.PoolConnectionProxy


# --------------------------------------------------------------------------
# Envelope and input bounds
# --------------------------------------------------------------------------

#: Version marker inside the decrypted metadata envelope. An unknown version is
#: an integrity failure, not a best-effort parse.
METADATA_ENVELOPE_VERSION: Final = 1

#: Domain-separation labels for the keyed fingerprints. Changing one silently
#: invalidates every stored fingerprint, so these are part of the storage
#: contract and must only change with an explicit migration.
IDENTITY_FINGERPRINT_DOMAIN: Final = "daemon.web_snapshot.identity.v1"
CONTENT_FINGERPRINT_DOMAIN: Final = "daemon.web_snapshot.content.v1"

# Input bounds that keep the metadata envelope itself bounded. The extracted
# text bound is the configurable ``web_snapshot_max_content_bytes``; these
# cover the small identity fields, which come from untrusted page markup.
_MAX_URL_CHARS: Final = 8192
_MAX_TITLE_CHARS: Final = 1024
_MAX_EXTRACT_MODE_CHARS: Final = 32
_MAX_EXTRACTION_VERSION_CHARS: Final = 64

# Hard ceiling on a serialized export document, independent of the tunable
# content bound so that lowering ``web_snapshot_max_content_bytes`` later can
# never make an already-retained snapshot unexportable. Unreachable for any
# row admitted under the default bounds; it exists so the export path is
# provably bounded rather than bounded-by-assumption.
EXPORT_HARD_CEILING_BYTES: Final = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# Typed errors
# --------------------------------------------------------------------------


class WebSnapshotError(Exception):
    """Base class for every snapshot failure.

    Subclasses carry a stable ``reason`` code and no payload: their ``str()``
    is safe to log and safe to surface as a generic client detail. Never add a
    URL, title, content excerpt or ciphertext to one of these messages.
    """

    reason: str = "web_snapshot_error"


class WebSnapshotNotFound(WebSnapshotError):
    """No snapshot exists for this owner/conversation/id triple."""

    reason = "not_found"


class WebSnapshotExpired(WebSnapshotError):
    """The snapshot exists but its immutable retention window has passed."""

    reason = "expired"


class WebSnapshotOwnerMismatch(WebSnapshotError):
    """The owner/conversation pair does not own the target, or no longer exists.

    Also raised when a concurrent account or conversation deletion races a
    save: the correct outcome is a rejected write, never an orphan row or a
    recreated conversation.
    """

    reason = "owner_mismatch"


class WebSnapshotCapacityExceeded(WebSnapshotError):
    """A conversation or account byte/count ceiling binds.

    Admission fails closed. Still-retained sources are never silently evicted
    to make room, so the caller must surface an honest capacity refusal.
    """

    reason = "capacity_exceeded"

    def __init__(self, limit: str) -> None:
        # ``limit`` is one of this module's stable scope names, never a value
        # derived from stored content.
        super().__init__(limit)
        self.limit = limit


class WebSnapshotContentTooLarge(WebSnapshotError):
    """The extracted text exceeds ``web_snapshot_max_content_bytes``."""

    reason = "content_too_large"


class WebSnapshotValidationError(WebSnapshotError):
    """A caller-supplied identity field is missing, malformed or oversized."""

    reason = "validation_error"


class WebSnapshotIntegrityError(WebSnapshotError):
    """Stored ciphertext could not be decrypted or parsed.

    Fail-closed: a wrong key, a rotated key, a truncated column or an unknown
    envelope version is an error, never a silent empty result.
    """

    reason = "integrity_error"


class WebSnapshotConfigurationError(WebSnapshotError):
    """The store was constructed without the dependencies it requires."""

    reason = "configuration_error"


# --------------------------------------------------------------------------
# Typed results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WebSnapshotMetadata:
    """Source identity and accounting for one snapshot, without the page text.

    This is what a bounded listing returns: decrypting the small metadata
    envelope is enough, so listing N sources never decrypts N page bodies.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    source_url: str
    final_url: str
    title: str
    extract_mode: str
    extraction_version: str
    content_chars: int
    content_bytes: int
    stored_bytes: int
    retrieved_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class WebSnapshot(WebSnapshotMetadata):
    """A complete retained snapshot: metadata plus the extracted page text."""

    content: str


@dataclass(frozen=True)
class WebSnapshotPage:
    """One bounded page of a conversation's retained snapshot metadata."""

    items: tuple[WebSnapshotMetadata, ...]
    total: int
    offset: int
    limit: int


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    """Normalize a caller-supplied timestamp to aware UTC.

    A naive timestamp is interpreted as UTC rather than rejected: every writer
    in this codebase produces aware UTC, and reinterpreting a naive value in
    the server's local zone would silently shift retention windows.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _encoded_size(*values: str) -> int:
    return sum(len(value.encode("utf-8")) for value in values)


def _reject(reason: str) -> NoReturn:
    """Raise a validation error carrying only a stable reason code."""
    raise WebSnapshotValidationError(reason)


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class WebSnapshotStore:
    """Encrypted, quota-admitting persistence for web reading snapshots.

    Construct one per process from the shared asyncpg pool; instances hold no
    mutable state, so independent instances in the backend and in the worker
    see the same rows and serialize against each other through the database
    row locks. There is no Redis or in-process plaintext cache: the encrypted
    table *is* the cache of record.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        encryption: ContentEncryption,
        settings: Settings | None = None,
    ) -> None:
        if pool is None:  # pragma: no cover - guarded by the caller/factory
            raise WebSnapshotConfigurationError("pool_required")
        self._pool = pool
        self._enc = encryption
        self._settings = settings if settings is not None else get_settings()

    @property
    def settings(self) -> Settings:
        """The resolved bounds. Transport and per-turn callers read their own
        knobs (``web_snapshot_max_response_bytes``,
        ``web_snapshot_max_new_per_turn``) from here rather than duplicating
        them."""
        return self._settings

    # ------------------------------------------------------------------
    # Fingerprints
    # ------------------------------------------------------------------

    def _fingerprint_key(self) -> bytes:
        """Resolve the keyed-fingerprint secret.

        Reuses the existing deployment pepper (the same secret that keys
        ``memories.content_hash``) so no new credential is introduced.
        ``validate_and_get_pepper`` fails closed in production when the pepper
        is missing or weak. In development without an explicit pepper it can
        resolve to a process-ephemeral value, which makes fingerprints
        process-local; that is the pre-existing development behaviour for
        memory content hashes and only degrades ``find_latest`` reuse, never
        authorization.
        """
        return validate_and_get_pepper(self._settings).encode("utf-8")

    def _digest(self, domain: str, *parts: str) -> str:
        """Domain-separated, account-scoped keyed digest.

        The domain label is hashed first so an identity digest and a content
        digest can never collide, and every caller passes ``user_id`` as a
        part so the same page text at the same URL yields different digests
        for different accounts. That is what makes cross-account dedup
        impossible even if two accounts fetch an identical page.
        """
        mac = hmac.new(self._fingerprint_key(), domain.encode("utf-8"), hashlib.sha256)
        for part in parts:
            # Length-prefix each part so field boundaries cannot be shifted by
            # a value containing the separator.
            encoded = part.encode("utf-8")
            mac.update(b"|")
            mac.update(str(len(encoded)).encode("ascii"))
            mac.update(b":")
            mac.update(encoded)
        return mac.hexdigest()

    def identity_fingerprint(
        self,
        user_id: uuid.UUID,
        *,
        source_url: str,
        extract_mode: str,
        extraction_version: str,
    ) -> str:
        """Keyed digest of one source identity for one account.

        Deliberately excludes the title and the final redirect URL: a redirect
        chain or a retitled page is still the same source identity, and
        ``find_latest`` must be able to reuse it.
        """
        return self._digest(
            IDENTITY_FINGERPRINT_DOMAIN,
            str(user_id),
            source_url,
            extract_mode,
            extraction_version,
        )

    def content_fingerprint(self, user_id: uuid.UUID, content: str) -> str:
        """Keyed digest of the extracted text for one account.

        A version-comparison aid only. It cannot detect that a *remote* page
        changed, and it grants no access.
        """
        return self._digest(CONTENT_FINGERPRINT_DOMAIN, str(user_id), content)

    # ------------------------------------------------------------------
    # Encryption envelopes
    # ------------------------------------------------------------------

    def _encrypt_metadata(
        self,
        *,
        source_url: str,
        final_url: str,
        title: str,
        extract_mode: str,
        extraction_version: str,
    ) -> str:
        envelope = {
            "v": METADATA_ENVELOPE_VERSION,
            "source_url": source_url,
            "final_url": final_url,
            "title": title,
            "extract_mode": extract_mode,
            "extraction_version": extraction_version,
        }
        try:
            return self._enc.encrypt(json.dumps(envelope, ensure_ascii=False))
        except Exception as exc:
            # Fail closed: never store a snapshot whose metadata is plaintext.
            logger.warning("web_snapshot_metadata_encryption_failed reason=%s", "encrypt_error")
            raise WebSnapshotIntegrityError("metadata_encrypt_failed") from exc

    def _decrypt_metadata(self, ciphertext: str, snapshot_id: uuid.UUID) -> dict[str, Any]:
        try:
            plaintext = self._enc.decrypt(ciphertext)
        except Exception as exc:
            logger.warning(
                "web_snapshot_metadata_decrypt_failed snapshot_id=%s reason=%s",
                snapshot_id,
                "decrypt_error",
            )
            raise WebSnapshotIntegrityError("metadata_decrypt_failed") from exc

        try:
            envelope = json.loads(plaintext)
        except ValueError as exc:
            logger.warning(
                "web_snapshot_metadata_unparseable snapshot_id=%s reason=%s",
                snapshot_id,
                "parse_error",
            )
            raise WebSnapshotIntegrityError("metadata_parse_failed") from exc

        if not isinstance(envelope, dict):
            raise WebSnapshotIntegrityError("metadata_shape_invalid")
        version = envelope.get("v")
        if version != METADATA_ENVELOPE_VERSION:
            # An unknown envelope version is not best-effort parseable.
            logger.warning(
                "web_snapshot_metadata_version_unsupported snapshot_id=%s reason=%s",
                snapshot_id,
                "version_error",
            )
            raise WebSnapshotIntegrityError("metadata_version_unsupported")
        for field in ("source_url", "final_url", "title", "extract_mode", "extraction_version"):
            if not isinstance(envelope.get(field), str):
                raise WebSnapshotIntegrityError("metadata_field_invalid")
        return envelope

    def _decrypt_content(self, ciphertext: str, snapshot_id: uuid.UUID) -> str:
        try:
            return self._enc.decrypt(ciphertext)
        except Exception as exc:
            logger.warning(
                "web_snapshot_content_decrypt_failed snapshot_id=%s reason=%s",
                snapshot_id,
                "decrypt_error",
            )
            raise WebSnapshotIntegrityError("content_decrypt_failed") from exc

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------

    def _metadata_from_row(self, row: asyncpg.Record) -> WebSnapshotMetadata:
        snapshot_id = cast(uuid.UUID, row["id"])
        envelope = self._decrypt_metadata(cast(str, row["metadata_encrypted"]), snapshot_id)
        return WebSnapshotMetadata(
            id=snapshot_id,
            user_id=cast(uuid.UUID, row["user_id"]),
            conversation_id=cast(uuid.UUID, row["conversation_id"]),
            source_url=cast(str, envelope["source_url"]),
            final_url=cast(str, envelope["final_url"]),
            title=cast(str, envelope["title"]),
            extract_mode=cast(str, envelope["extract_mode"]),
            extraction_version=cast(str, envelope["extraction_version"]),
            content_chars=cast(int, row["content_chars"]),
            content_bytes=cast(int, row["content_bytes"]),
            stored_bytes=cast(int, row["stored_bytes"]),
            retrieved_at=cast(datetime, row["retrieved_at"]),
            expires_at=cast(datetime, row["expires_at"]),
        )

    def _snapshot_from_row(self, row: asyncpg.Record) -> WebSnapshot:
        metadata = self._metadata_from_row(row)
        content = self._decrypt_content(cast(str, row["content_encrypted"]), metadata.id)
        return WebSnapshot(
            id=metadata.id,
            user_id=metadata.user_id,
            conversation_id=metadata.conversation_id,
            source_url=metadata.source_url,
            final_url=metadata.final_url,
            title=metadata.title,
            extract_mode=metadata.extract_mode,
            extraction_version=metadata.extraction_version,
            content_chars=metadata.content_chars,
            content_bytes=metadata.content_bytes,
            stored_bytes=metadata.stored_bytes,
            retrieved_at=metadata.retrieved_at,
            expires_at=metadata.expires_at,
            content=content,
        )

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def _validate_identity_inputs(
        self,
        *,
        source_url: str,
        final_url: str,
        title: str,
        extract_mode: str,
        extraction_version: str,
        content: str,
    ) -> None:
        if not source_url or not source_url.strip():
            _reject("source_url_required")
        if len(source_url) > _MAX_URL_CHARS:
            _reject("source_url_too_long")
        if len(final_url) > _MAX_URL_CHARS:
            _reject("final_url_too_long")
        if len(title) > _MAX_TITLE_CHARS:
            _reject("title_too_long")
        if not extract_mode or len(extract_mode) > _MAX_EXTRACT_MODE_CHARS:
            _reject("extract_mode_invalid")
        if not extraction_version or len(extraction_version) > _MAX_EXTRACTION_VERSION_CHARS:
            _reject("extraction_version_invalid")
        max_content_bytes = self._settings.web_snapshot_max_content_bytes
        if _encoded_size(content) > max_content_bytes:
            # Reject oversize text instead of storing a falsely complete
            # snapshot. The bound itself is never part of the message.
            raise WebSnapshotContentTooLarge("content_exceeds_limit")

    # ------------------------------------------------------------------
    # SQL fragments
    # ------------------------------------------------------------------

    _METADATA_COLUMNS: Final = (
        "id, user_id, conversation_id, metadata_encrypted, "
        "content_chars, content_bytes, stored_bytes, retrieved_at, expires_at"
    )
    _FULL_COLUMNS: Final = _METADATA_COLUMNS + ", content_encrypted"

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        source_url: str,
        content: str,
        extract_mode: str,
        extraction_version: str,
        final_url: str | None = None,
        title: str | None = None,
        now: datetime | None = None,
        on_created: Callable[[Connection, uuid.UUID], Awaitable[None]] | None = None,
    ) -> WebSnapshot:
        """Retain one immutable snapshot under atomic owner and quota admission.

        The page must already have been fetched and extracted: this method
        performs no network I/O and revalidates ownership, conversation
        existence, expiry-driven quota and both byte/count ceilings inside one
        transaction.

        Raises:
            WebSnapshotValidationError: a missing/malformed/oversized identity field.
            WebSnapshotContentTooLarge: extracted text above the content bound.
            WebSnapshotOwnerMismatch: the account or the owned conversation is
                gone (including a delete that raced this save).
            WebSnapshotCapacityExceeded: a conversation or account ceiling binds.
            WebSnapshotIntegrityError: encryption failed.
        """
        resolved_final_url = final_url if final_url is not None else source_url
        resolved_title = title if title is not None else ""
        self._validate_identity_inputs(
            source_url=source_url,
            final_url=resolved_final_url,
            title=resolved_title,
            extract_mode=extract_mode,
            extraction_version=extraction_version,
            content=content,
        )

        retrieved_at = _as_aware_utc(now if now is not None else _utc_now())
        expires_at = retrieved_at + timedelta(days=self._settings.web_snapshot_retention_days)

        snapshot_id = uuid.uuid4()
        # Encrypt before taking any lock: key expansion and Fernet over up to
        # 1 MiB are the expensive part of this call and must not happen while
        # the account row lock is held.
        metadata_cipher = self._encrypt_metadata(
            source_url=source_url,
            final_url=resolved_final_url,
            title=resolved_title,
            extract_mode=extract_mode,
            extraction_version=extraction_version,
        )
        content_cipher = self._enc.encrypt(content)
        stored_bytes = _encoded_size(metadata_cipher, content_cipher)
        content_bytes = _encoded_size(content)
        identity = self.identity_fingerprint(
            user_id,
            source_url=source_url,
            extract_mode=extract_mode,
            extraction_version=extraction_version,
        )
        content_fp = self.content_fingerprint(user_id, content)

        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await self._lock_account(conn, user_id)
                    await self._lock_owned_conversation(conn, user_id, conversation_id)
                    await self._purge_account_expired(conn, user_id, retrieved_at)
                    await self._admit(
                        conn,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        stored_bytes=stored_bytes,
                        now=retrieved_at,
                    )
                    await conn.execute(
                        """
                        INSERT INTO web_snapshots (
                            id, user_id, conversation_id,
                            metadata_encrypted, content_encrypted,
                            identity_fingerprint, content_fingerprint,
                            content_chars, content_bytes, stored_bytes,
                            retrieved_at, expires_at
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                        """,
                        snapshot_id,
                        user_id,
                        conversation_id,
                        metadata_cipher,
                        content_cipher,
                        identity,
                        content_fp,
                        len(content),
                        content_bytes,
                        stored_bytes,
                        retrieved_at,
                        expires_at,
                    )
                    if on_created is not None:
                        # Trusted worker hook: locks task only AFTER account and
                        # conversation, then fences and pins this exact row in
                        # the same transaction. No network I/O in this hook.
                        await on_created(conn, snapshot_id)
        except asyncpg.ForeignKeyViolationError as exc:
            # The account or the conversation was deleted between the locked
            # re-read and the insert. Reject the save; never recreate the
            # conversation and never keep an orphan snapshot.
            logger.info(
                "web_snapshot_save_rejected_parent_gone snapshot_id=%s reason=%s",
                snapshot_id,
                "foreign_key_violation",
            )
            raise WebSnapshotOwnerMismatch("conversation_unavailable") from exc

        return WebSnapshot(
            id=snapshot_id,
            user_id=user_id,
            conversation_id=conversation_id,
            source_url=source_url,
            final_url=resolved_final_url,
            title=resolved_title,
            extract_mode=extract_mode,
            extraction_version=extraction_version,
            content_chars=len(content),
            content_bytes=content_bytes,
            stored_bytes=stored_bytes,
            retrieved_at=retrieved_at,
            expires_at=expires_at,
            content=content,
        )

    async def _lock_account(self, conn: Connection, user_id: uuid.UUID) -> None:
        """Take the per-account serialization point.

        Locking the ``users`` row (rather than an advisory lock) means account
        quota admission serializes against every other snapshot save for the
        same account across every backend and worker process, and it does so
        without introducing a second locking primitive. NO KEY UPDATE still
        excludes saves, updates and deletion, but permits foreign-key KEY SHARE
        checks by task control while publication waits for its task fence.
        """
        found = await conn.fetchval("SELECT id FROM users WHERE id = $1 FOR NO KEY UPDATE", user_id)
        if found is None:
            raise WebSnapshotOwnerMismatch("account_unavailable")

    async def _require_owned_conversation(
        self,
        pool: asyncpg.Pool,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> None:
        """Fail closed when the conversation is absent or belongs to someone else.

        Reads must not answer with an empty result for a conversation the
        caller does not own: that would make "you have no snapshots here"
        indistinguishable from a valid empty conversation, and would let a
        listing route return 200 for another account's conversation id.
        Non-locking, because a read must never contend with an admission.
        """
        found = await pool.fetchval(
            "SELECT id FROM conversations WHERE id = $1 AND user_id = $2",
            conversation_id,
            user_id,
        )
        if found is None:
            raise WebSnapshotOwnerMismatch("conversation_unavailable")

    async def _lock_owned_conversation(
        self, conn: Connection, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> None:
        """Lock and re-read the owned conversation in the same transaction.

        Ownership is re-verified *after* the account lock and *after* the
        network fetch, so a conversation deleted while the page was downloading
        cannot be resurrected or written through.
        """
        found = await conn.fetchval(
            "SELECT id FROM conversations WHERE id = $1 AND user_id = $2 FOR NO KEY UPDATE",
            conversation_id,
            user_id,
        )
        if found is None:
            raise WebSnapshotOwnerMismatch("conversation_unavailable")

    async def _purge_account_expired(
        self, conn: Connection, user_id: uuid.UUID, now: datetime
    ) -> None:
        """Drop this account's expired rows before summing usage.

        Bounded by the account's own count ceiling, and run under the account
        lock, so it cannot interleave with another admission for the same
        account. A failing global sweep therefore never causes a live-quota
        overcount.
        """
        await conn.execute(
            "DELETE FROM web_snapshots WHERE user_id = $1 AND expires_at <= $2",
            user_id,
            now,
        )

    async def _admit(
        self,
        conn: Connection,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        stored_bytes: int,
        now: datetime,
    ) -> None:
        """Enforce the conversation and account byte/count ceilings.

        Both sums count retained rows only (``expires_at > now``) and are read
        inside the same transaction that holds the account lock, so the
        check-and-insert is atomic with respect to every other save for this
        account. Reaching full storage raises; still-retained sources are
        never evicted to make room.
        """
        settings = self._settings
        conversation_row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS snapshot_count, COALESCE(SUM(stored_bytes), 0) AS total_bytes
            FROM web_snapshots
            WHERE conversation_id = $1 AND user_id = $2 AND expires_at > $3
            """,
            conversation_id,
            user_id,
            now,
        )
        account_row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS snapshot_count, COALESCE(SUM(stored_bytes), 0) AS total_bytes
            FROM web_snapshots
            WHERE user_id = $1 AND expires_at > $2
            """,
            user_id,
            now,
        )
        conversation_count = (
            cast(int, conversation_row["snapshot_count"]) if conversation_row else 0
        )
        conversation_bytes = cast(int, conversation_row["total_bytes"]) if conversation_row else 0
        account_count = cast(int, account_row["snapshot_count"]) if account_row else 0
        account_bytes = cast(int, account_row["total_bytes"]) if account_row else 0

        if conversation_count + 1 > settings.web_snapshot_max_conversation_count:
            logger.info(
                "web_snapshot_admission_denied reason=%s scope=%s",
                "count",
                "conversation",
            )
            raise WebSnapshotCapacityExceeded("conversation_count")
        if conversation_bytes + stored_bytes > settings.web_snapshot_max_conversation_bytes:
            logger.info(
                "web_snapshot_admission_denied reason=%s scope=%s",
                "bytes",
                "conversation",
            )
            raise WebSnapshotCapacityExceeded("conversation_bytes")
        if account_count + 1 > settings.web_snapshot_max_account_count:
            logger.info(
                "web_snapshot_admission_denied reason=%s scope=%s",
                "count",
                "account",
            )
            raise WebSnapshotCapacityExceeded("account_count")
        if account_bytes + stored_bytes > settings.web_snapshot_max_account_bytes:
            logger.info(
                "web_snapshot_admission_denied reason=%s scope=%s",
                "bytes",
                "account",
            )
            raise WebSnapshotCapacityExceeded("account_bytes")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> WebSnapshot:
        """Return one complete snapshot owned by this account and conversation.

        The id alone is never sufficient: owner and conversation are filtered
        in SQL, so a correct id from another conversation or account is
        indistinguishable from a nonexistent one.

        Raises:
            WebSnapshotNotFound: no row matches the owner/conversation/id triple.
            WebSnapshotExpired: the row matches but its retention window passed.
            WebSnapshotIntegrityError: an envelope could not be decrypted.
        """
        resolved_now = _as_aware_utc(now if now is not None else _utc_now())
        row = await self._pool.fetchrow(
            f"""
            SELECT {self._FULL_COLUMNS}, (expires_at > $4) AS is_live
            FROM web_snapshots
            WHERE id = $1 AND user_id = $2 AND conversation_id = $3
            """,
            snapshot_id,
            user_id,
            conversation_id,
            resolved_now,
        )
        if row is None:
            raise WebSnapshotNotFound("snapshot_not_found")
        if not cast(bool, row["is_live"]):
            # Expiry is enforced here even when the cleanup sweep is delayed or
            # failing; retained-but-expired content is never served.
            raise WebSnapshotExpired("snapshot_expired")
        return self._snapshot_from_row(row)

    async def find_latest(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        source_url: str,
        extract_mode: str,
        extraction_version: str,
        *,
        now: datetime | None = None,
    ) -> WebSnapshot | None:
        """Return the newest retained version of one source identity, or None.

        This is the reuse path for an ordinary (non-refresh) read: it returns
        an *immutable* previously stored version rather than refetching, so
        chunk offsets keep referring to one fixed textual representation. An
        explicit refresh must not call this; it creates a new version with a
        new id and leaves this one retained until its own expiry.

        Matching is by keyed fingerprint, but the query is still constrained by
        owner and conversation, and expired rows are excluded, so a fingerprint
        can never widen access.
        """
        resolved_now = _as_aware_utc(now if now is not None else _utc_now())
        identity = self.identity_fingerprint(
            user_id,
            source_url=source_url,
            extract_mode=extract_mode,
            extraction_version=extraction_version,
        )
        row = await self._pool.fetchrow(
            f"""
            SELECT {self._FULL_COLUMNS}
            FROM web_snapshots
            WHERE user_id = $1
              AND conversation_id = $2
              AND identity_fingerprint = $3
              AND expires_at > $4
            ORDER BY retrieved_at DESC, id ASC
            LIMIT 1
            """,
            user_id,
            conversation_id,
            identity,
            resolved_now,
        )
        if row is None:
            return None
        return self._snapshot_from_row(row)

    async def list(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        offset: int = 0,
        limit: int = 20,
        *,
        now: datetime | None = None,
    ) -> WebSnapshotPage:
        """Return one bounded page of this conversation's retained sources.

        Newest first. Expired rows are excluded from both the items and the
        total, so a listing never advertises a snapshot that a subsequent
        export would refuse. Page bodies are not decrypted.

        Raises:
            WebSnapshotValidationError: a negative offset or a limit below 1.
            WebSnapshotOwnerMismatch: the conversation is absent or is not owned
                by this account. A listing must not answer 200-with-nothing for
                a conversation the caller does not own.
            WebSnapshotIntegrityError: a metadata envelope could not be decrypted.
        """
        if offset < 0:
            raise WebSnapshotValidationError("offset_out_of_range")
        if limit < 1:
            raise WebSnapshotValidationError("limit_out_of_range")
        resolved_now = _as_aware_utc(now if now is not None else _utc_now())

        await self._require_owned_conversation(self._pool, user_id, conversation_id)

        total = await self._pool.fetchval(
            """
            SELECT COUNT(*)
            FROM web_snapshots
            WHERE user_id = $1 AND conversation_id = $2 AND expires_at > $3
            """,
            user_id,
            conversation_id,
            resolved_now,
        )
        rows = await self._pool.fetch(
            f"""
            SELECT {self._METADATA_COLUMNS}
            FROM web_snapshots
            WHERE user_id = $1 AND conversation_id = $2 AND expires_at > $3
            ORDER BY retrieved_at DESC, id ASC
            LIMIT $4 OFFSET $5
            """,
            user_id,
            conversation_id,
            resolved_now,
            limit,
            offset,
        )
        return WebSnapshotPage(
            items=tuple(self._metadata_from_row(row) for row in rows),
            total=cast(int, total) if total is not None else 0,
            offset=offset,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Deletes
    # ------------------------------------------------------------------

    async def delete(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Explicitly delete one owned snapshot. Returns True when a row was removed.

        Takes the same account-then-conversation lock order as ``create()`` so
        a delete cannot interleave between another save's usage sum and its
        insert. A wrong owner, a wrong conversation or an unknown id raises
        ``WebSnapshotNotFound`` — the caller maps all three to the same 404, so
        the difference between "not yours" and "not real" is not observable.

        An expired row raises ``WebSnapshotExpired`` rather than being deleted
        here: expiry is a read refusal, and expired rows are reclaimed by
        ``purge_expired()`` and by the pre-admission account purge, so refusing
        cannot leak quota.
        """
        resolved_now = _as_aware_utc(now if now is not None else _utc_now())
        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await self._lock_account(conn, user_id)
                    await self._lock_owned_conversation(conn, user_id, conversation_id)
                    is_live = await conn.fetchval(
                        """
                        SELECT (expires_at > $4)
                        FROM web_snapshots
                        WHERE id = $1 AND user_id = $2 AND conversation_id = $3
                        """,
                        snapshot_id,
                        user_id,
                        conversation_id,
                        resolved_now,
                    )
                    if is_live is None:
                        raise WebSnapshotNotFound("snapshot_not_found")
                    if not cast(bool, is_live):
                        raise WebSnapshotExpired("snapshot_expired")
                    status = await conn.execute(
                        """
                        DELETE FROM web_snapshots
                        WHERE id = $1 AND user_id = $2 AND conversation_id = $3
                        """,
                        snapshot_id,
                        user_id,
                        conversation_id,
                    )
                    return _deleted_row_count(status) > 0
        except asyncpg.ForeignKeyViolationError as exc:  # pragma: no cover - defensive
            logger.info(
                "web_snapshot_delete_rejected snapshot_id=%s reason=%s",
                snapshot_id,
                "foreign_key_violation",
            )
            raise WebSnapshotOwnerMismatch("conversation_unavailable") from exc

    async def purge_expired(self, limit: int = 1000, *, now: datetime | None = None) -> int:
        """Delete up to ``limit`` expired rows. Returns the number removed.

        Housekeeping for the daily worker sweep. Reads already refuse expired
        rows and ``create()`` purges the acting account's expired rows before
        summing usage, so a delayed or failing sweep degrades storage
        reclamation only — never correctness, and never retention.
        """
        if limit < 1:
            raise WebSnapshotValidationError("limit_out_of_range")
        resolved_now = _as_aware_utc(now if now is not None else _utc_now())
        status = await self._pool.execute(
            """
            WITH expired AS (
                SELECT id FROM web_snapshots
                WHERE expires_at <= $1
                ORDER BY expires_at
                LIMIT $2
            )
            DELETE FROM web_snapshots
            WHERE id IN (SELECT id FROM expired)
            """,
            resolved_now,
            limit,
        )
        removed = _deleted_row_count(status)
        if removed:
            logger.info("web_snapshot_purge_removed count=%s", removed)
        return removed


def _deleted_row_count(status: str) -> int:
    """Parse an asyncpg ``DELETE n`` command status without trusting its shape."""
    _, _, tail = status.rpartition(" ")
    return int(tail) if tail.isdigit() else 0
