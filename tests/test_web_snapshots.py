"""Real-PostgreSQL and API tests for conversation-scoped web snapshot storage.

Covers the invariants that make the snapshot store safe to ship:

* owner/conversation authorization on every read, lookup and delete, including
  the account-scoped (never global) fingerprints;
* ciphertext at rest for *both* the metadata envelope and the page body, with
  no plaintext identity column in the schema;
* fail-closed encryption: corrupt or wrongly-keyed envelopes raise rather than
  degrading to empty results;
* transactional quota admission under genuine concurrency across independent
  store instances backed by separate pools (i.e. separate processes);
* delete/insert race safety with no orphan rows;
* immutable versioning and latest-version lookup;
* byte and count ceilings at conversation and account scope;
* expiry enforced on read even when cleanup never runs, plus bounded cleanup;
* the HTTP surface: authentication, 404 equivalence for wrong owner /
  wrong conversation / unknown id / expired, opaque no-store export, and
  non-disclosing error bodies.

Set ``WEB_SNAPSHOT_TEST_DATABASE_URL`` (or ``ENTITLEMENTS_TEST_DATABASE_URL``)
to an isolated disposable database; otherwise every database-backed test skips.
Each test uses its own schema and drops only that schema. No provider requests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
from typing import Any, cast
import uuid
from unittest.mock import AsyncMock

import asyncpg
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from pydantic import ValidationError

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.config import Settings
from orchestrator.memory.encryption import ContentEncryption, EncryptionInitError
from orchestrator.routes import web_snapshots as snapshot_routes
from orchestrator.routes.web_snapshots import (
    EXPORT_SCHEMA,
    EXPORT_VERSION,
    create_web_snapshot_store,
    get_web_snapshot_store,
)
from orchestrator.services.web_snapshots import (
    WebSnapshot,
    WebSnapshotCapacityExceeded,
    WebSnapshotContentTooLarge,
    WebSnapshotExpired,
    WebSnapshotIntegrityError,
    WebSnapshotNotFound,
    WebSnapshotOwnerMismatch,
    WebSnapshotStore,
    WebSnapshotValidationError,
)


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "041_web_snapshots.sql"

# The exact plaintext schema contract. `source_url`, `title`, `extract_mode`
# and `extraction_version` are deliberately absent: they live only inside the
# encrypted metadata envelope.
EXPECTED_COLUMNS = {
    "id",
    "user_id",
    "conversation_id",
    "metadata_encrypted",
    "content_encrypted",
    "identity_fingerprint",
    "content_fingerprint",
    "content_chars",
    "content_bytes",
    "stored_bytes",
    "retrieved_at",
    "expires_at",
    "created_at",
}

WEB_SNAPSHOT_FIELD_NAMES = (
    "web_snapshot_retention_days",
    "web_snapshot_max_response_bytes",
    "web_snapshot_max_content_bytes",
    "web_snapshot_max_conversation_bytes",
    "web_snapshot_max_conversation_count",
    "web_snapshot_max_account_bytes",
    "web_snapshot_max_account_count",
    "web_snapshot_max_new_per_turn",
    "web_snapshot_default_chunk_chars",
    "web_snapshot_max_chunk_chars",
)

APPROVED_DEFAULTS = {
    "web_snapshot_retention_days": 30,
    "web_snapshot_max_response_bytes": 2 * 1024 * 1024,
    "web_snapshot_max_content_bytes": 1 * 1024 * 1024,
    "web_snapshot_max_conversation_bytes": 16 * 1024 * 1024,
    "web_snapshot_max_conversation_count": 64,
    "web_snapshot_max_account_bytes": 128 * 1024 * 1024,
    "web_snapshot_max_account_count": 512,
    "web_snapshot_max_new_per_turn": 8,
    "web_snapshot_default_chunk_chars": 6000,
    "web_snapshot_max_chunk_chars": 12000,
}

# A deterministic pepper keeps fingerprints stable across independent store
# instances, which is exactly what cross-process reuse requires.
TEST_PEPPER = "web-snapshot-test-pepper-" + "a" * 32
# Marker used to prove no plaintext reaches the database, a log or a filename.
MARKER = "PLAINTEXT-MARKER-9c41e7"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _construct(cls: Any, **values: Any) -> Any:
    """Instantiate a settings class without opening a dotenv file.

    Both parameters are ``Any`` because pydantic synthesises a per-class
    ``__init__`` signature that a type checker cannot match against
    ``Settings(**values)``; this mirrors the helper in
    ``tests/test_env_surface_parity.py``. ``_env_file=None`` keeps the
    repository ``.env`` out of every test in this module.
    """
    return cls(_env_file=None, **values)


def _test_settings(**overrides: Any) -> Settings:
    """Explicit test Settings. Never resolved from a dotenv file or the environment."""
    resolved: Settings = _construct(
        Settings,
        daemon_environment="development",
        daemon_auth_pepper=TEST_PEPPER,
        daemon_encryption_key=None,
        **overrides,
    )
    return resolved


@dataclass
class SnapshotDatabase:
    """One disposable schema plus the pools created against it."""

    dsn: str
    schema: str
    pool: asyncpg.Pool
    encryption_key: str
    extra_pools: list[asyncpg.Pool] = field(default_factory=list)

    def __repr__(self) -> str:
        # Never print the DSN, the encryption key or a pool address: a failing
        # assertion must not put test database credentials in the log.
        return f"SnapshotDatabase(schema={self.schema!r})"

    def encryption(self, key: str | None = None) -> ContentEncryption:
        return ContentEncryption(key if key is not None else self.encryption_key)

    def store(self, settings: Settings | None = None, key: str | None = None) -> WebSnapshotStore:
        return WebSnapshotStore(self.pool, self.encryption(key), settings or _test_settings())

    async def new_pool(self, *, max_size: int = 6) -> asyncpg.Pool:
        """A second pool bound to the same schema: a distinct 'process'."""
        pool = await asyncpg.create_pool(
            self.dsn,
            min_size=1,
            max_size=max_size,
            server_settings={"search_path": self.schema},
        )
        assert pool is not None
        self.extra_pools.append(pool)
        return pool

    async def add_user(self) -> uuid.UUID:
        user_id = uuid.uuid4()
        await self.pool.execute("INSERT INTO users (id) VALUES ($1)", user_id)
        return user_id

    async def add_conversation(self, user_id: uuid.UUID | None = None) -> uuid.UUID:
        owner = user_id if user_id is not None else await self.add_user()
        conversation_id = uuid.uuid4()
        await self.pool.execute(
            "INSERT INTO conversations (id, user_id) VALUES ($1, $2)",
            conversation_id,
            owner,
        )
        return conversation_id

    async def row(self, snapshot_id: uuid.UUID) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM web_snapshots WHERE id = $1", snapshot_id)

    async def snapshot_count(self) -> int:
        count = await self.pool.fetchval("SELECT COUNT(*) FROM web_snapshots")
        return int(count or 0)


def _database_url() -> str | None:
    return os.environ.get("WEB_SNAPSHOT_TEST_DATABASE_URL") or os.environ.get(
        "ENTITLEMENTS_TEST_DATABASE_URL"
    )


@pytest_asyncio.fixture
async def snapshot_database() -> AsyncIterator[SnapshotDatabase]:
    dsn = _database_url()
    if not dsn:
        pytest.skip("requires an isolated WEB_SNAPSHOT_TEST_DATABASE_URL")
    schema = f"web_snapshots_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    database = SnapshotDatabase(
        dsn=dsn, schema=schema, pool=admin, encryption_key=Fernet.generate_key().decode()
    )
    pools: list[asyncpg.Pool] = []
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=12, server_settings={"search_path": schema}
        )
        assert pool is not None
        pools.append(pool)
        database.pool = pool
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")
            await conn.execute(
                "CREATE TABLE conversations ("
                "id UUID PRIMARY KEY, "
                "user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE)"
            )
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
        yield database
    finally:
        for extra in database.extra_pools:
            await extra.close()
        for open_pool in pools:
            await open_pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _create(
    store: WebSnapshotStore,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    source_url: str = f"https://example.test/{MARKER}/page",
    content: str = f"extracted body {MARKER}",
    title: str = f"Title {MARKER}",
    extract_mode: str = "article",
    extraction_version: str = "v1",
    now: datetime | None = None,
) -> WebSnapshot:
    return await store.create(
        user_id,
        conversation_id,
        source_url=source_url,
        content=content,
        extract_mode=extract_mode,
        extraction_version=extraction_version,
        final_url=source_url,
        title=title,
        now=now,
    )


# ==========================================================================
# Configuration
# ==========================================================================


def test_settings_defaults_match_the_approved_bounds() -> None:
    settings: Settings = _construct(Settings)
    for name, expected in APPROVED_DEFAULTS.items():
        assert getattr(settings, name) == expected, name


def test_every_web_snapshot_bound_rejects_a_non_positive_value() -> None:
    for name in WEB_SNAPSHOT_FIELD_NAMES:
        with pytest.raises(ValidationError):
            _test_settings(**{name: 0})
        with pytest.raises(ValidationError):
            _test_settings(**{name: -1})


def test_default_chunk_chars_must_not_exceed_the_maximum() -> None:
    assert (
        _test_settings(web_snapshot_default_chunk_chars=12000).web_snapshot_max_chunk_chars
        == (APPROVED_DEFAULTS["web_snapshot_max_chunk_chars"])
    )
    with pytest.raises(ValidationError, match="web_snapshot_default_chunk_chars"):
        _test_settings(web_snapshot_default_chunk_chars=12001)
    with pytest.raises(ValidationError, match="web_snapshot_default_chunk_chars"):
        _test_settings(web_snapshot_default_chunk_chars=20000, web_snapshot_max_chunk_chars=100)


def test_content_bound_must_not_exceed_the_decoded_response_bound() -> None:
    with pytest.raises(ValidationError, match="web_snapshot_max_content_bytes"):
        _test_settings(
            web_snapshot_max_response_bytes=1024,
            web_snapshot_max_content_bytes=2048,
        )


def test_conversation_bounds_must_not_exceed_account_bounds() -> None:
    with pytest.raises(ValidationError, match="web_snapshot_max_conversation_bytes"):
        _test_settings(
            web_snapshot_max_conversation_bytes=2048,
            web_snapshot_max_account_bytes=1024,
        )
    with pytest.raises(ValidationError, match="web_snapshot_max_conversation_count"):
        _test_settings(
            web_snapshot_max_conversation_count=10,
            web_snapshot_max_account_count=5,
        )


def test_bounds_are_resolved_from_the_documented_uppercase_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_SNAPSHOT_RETENTION_DAYS", "7")
    monkeypatch.setenv("WEB_SNAPSHOT_MAX_CONVERSATION_COUNT", "3")
    monkeypatch.setenv("WEB_SNAPSHOT_MAX_CHUNK_CHARS", "9000")
    settings: Settings = _construct(Settings)
    assert settings.web_snapshot_retention_days == 7
    assert settings.web_snapshot_max_conversation_count == 3
    assert settings.web_snapshot_max_chunk_chars == 9000


def test_store_factory_fails_closed_without_an_encryption_key() -> None:
    """No key means no store: plaintext persistence is never a fallback.

    Built from explicit init kwargs with ``_env_file=None`` so a repository
    ``.env`` cannot supply a key and mask the failure.
    """
    keyless: Settings = _construct(Settings, daemon_encryption_key="")
    dummy_pool = cast(asyncpg.Pool, object())
    with pytest.raises(EncryptionInitError):
        create_web_snapshot_store(dummy_pool, keyless)
    with pytest.raises(EncryptionInitError):
        ContentEncryption("")


# ==========================================================================
# Encryption at rest
# ==========================================================================


@pytest.mark.asyncio
async def test_reader_reuses_encrypted_source_across_turns_and_worker_pools(
    snapshot_database, monkeypatch
):
    from orchestrator.services.fetch.models import FetchResult
    from orchestrator.services.fetch.service import FetchService
    from orchestrator.tools.web_fetch import WebFetchTool

    user = await snapshot_database.add_user()
    conversation = await snapshot_database.add_conversation(user)
    text = "Evidence — 日本語. " * 1000
    url = "https://example.com/Case?Document=A"
    fetch = AsyncMock(
        return_value=FetchResult(
            url=url,
            source_url=url,
            final_url=url,
            title="Evidence",
            content=text,
            content_length=len(text),
            cached=False,
            strategy_used="direct",
            fetch_time_ms=1,
        )
    )
    monkeypatch.setattr(FetchService, "fetch", fetch)
    first = WebFetchTool(snapshot_database.store(), user, conversation)
    initial = json.loads(await first.execute(url=url, max_chars=100))
    assert initial["content"] == text[:100]
    second_store = WebSnapshotStore(
        await snapshot_database.new_pool(), snapshot_database.encryption(), _test_settings()
    )
    second = WebFetchTool(second_store, user, conversation)
    continuation = json.loads(
        await second.execute(
            snapshot_id=initial["snapshot_id"], start_char=initial["next_start_char"], max_chars=100
        )
    )
    assert initial["content"] + continuation["content"] == text[:200]
    reused = json.loads(await second.execute(url=url, max_chars=100))
    assert reused["snapshot_id"] == initial["snapshot_id"]
    fetch.assert_awaited_once()
    assert fetch.call_args.kwargs["use_cache"] is False


@pytest.mark.asyncio
async def test_metadata_and_content_are_ciphertext_at_rest(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)

    snapshot = await _create(store, user_id, conversation_id)
    row = await snapshot_database.row(snapshot.id)
    assert row is not None

    # The schema stores no plaintext identity column at all.
    assert set(row.keys()) == EXPECTED_COLUMNS

    for column in ("metadata_encrypted", "content_encrypted"):
        value = str(row[column])
        assert value, column
        assert MARKER not in value, f"{column} holds plaintext"
        assert value != str(snapshot.content)

    # Fingerprints are keyed digests, not the values they are computed over.
    for column in ("identity_fingerprint", "content_fingerprint"):
        value = str(row[column])
        assert MARKER not in value
        assert len(value) == 64, "expected a hex SHA-256 digest"

    # Only numeric/temporal/uuid data is plaintext.
    assert row["content_chars"] == len(snapshot.content)
    assert row["content_bytes"] == len(snapshot.content.encode("utf-8"))
    assert row["stored_bytes"] == len(str(row["metadata_encrypted"]).encode()) + len(
        str(row["content_encrypted"]).encode()
    )
    assert snapshot.stored_bytes == row["stored_bytes"]
    assert snapshot.expires_at - snapshot.retrieved_at == timedelta(days=30)

    # The whole row, rendered as text, carries no plaintext marker.
    assert MARKER not in " ".join(str(value) for value in row.values())


@pytest.mark.asyncio
async def test_corrupt_metadata_or_content_fails_closed(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    snapshot = await _create(store, user_id, conversation_id)

    await snapshot_database.pool.execute(
        "UPDATE web_snapshots SET content_encrypted = $1 WHERE id = $2",
        "not-a-fernet-token",
        snapshot.id,
    )
    with pytest.raises(WebSnapshotIntegrityError):
        await store.get(user_id, conversation_id, snapshot.id)

    await snapshot_database.pool.execute(
        "UPDATE web_snapshots SET metadata_encrypted = $1 WHERE id = $2",
        "also-not-a-token",
        snapshot.id,
    )
    # A corrupt metadata envelope must break the listing loudly rather than
    # silently dropping the row and misreporting retained sources and quota.
    with pytest.raises(WebSnapshotIntegrityError):
        await store.list(user_id, conversation_id)
    with pytest.raises(WebSnapshotIntegrityError):
        await store.find_latest(user_id, conversation_id, snapshot.source_url, "article", "v1")


@pytest.mark.asyncio
async def test_a_different_encryption_key_cannot_read_a_snapshot(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    snapshot = await _create(store, user_id, conversation_id)

    foreign = snapshot_database.store(key=Fernet.generate_key().decode())
    with pytest.raises(WebSnapshotIntegrityError):
        await foreign.get(user_id, conversation_id, snapshot.id)
    with pytest.raises(WebSnapshotIntegrityError):
        await foreign.list(user_id, conversation_id)


# ==========================================================================
# Authorization
# ==========================================================================


@pytest.mark.asyncio
async def test_reads_require_the_matching_account_and_conversation(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    owner = await snapshot_database.add_user()
    conversation = await snapshot_database.add_conversation(owner)
    snapshot = await _create(store, owner, conversation)

    intruder = await snapshot_database.add_user()
    intruder_conversation = await snapshot_database.add_conversation(intruder)
    sibling_conversation = await snapshot_database.add_conversation(owner)

    # A valid id with the wrong account, the wrong conversation, or both is
    # indistinguishable from a nonexistent snapshot. ``delete`` locks the owned
    # conversation first, so an unowned conversation surfaces as the ownership
    # error instead; the HTTP surface collapses both to the same 404.
    for user, conversation_id in (
        (intruder, conversation),
        (owner, intruder_conversation),
        (intruder, intruder_conversation),
        (owner, sibling_conversation),
    ):
        with pytest.raises(WebSnapshotNotFound):
            await store.get(user, conversation_id, snapshot.id)
        with pytest.raises((WebSnapshotNotFound, WebSnapshotOwnerMismatch)):
            await store.delete(user, conversation_id, snapshot.id)

    # Listing refuses a conversation the caller does not own instead of
    # answering with an empty page.
    with pytest.raises(WebSnapshotOwnerMismatch):
        await store.list(intruder, conversation)
    with pytest.raises(WebSnapshotOwnerMismatch):
        await store.list(owner, uuid.uuid4())

    owned_page = await store.list(owner, sibling_conversation)
    assert owned_page.items == ()
    assert owned_page.total == 0

    assert (
        await store.find_latest(intruder, conversation, snapshot.source_url, "article", "v1")
        is None
    )
    assert (
        await store.find_latest(owner, sibling_conversation, snapshot.source_url, "article", "v1")
        is None
    )

    # The real owner is unaffected by every failed attempt above.
    intact = await store.get(owner, conversation, snapshot.id)
    assert intact.content == snapshot.content
    assert await snapshot_database.snapshot_count() == 1


@pytest.mark.asyncio
async def test_writes_require_an_owned_conversation(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    owner = await snapshot_database.add_user()
    conversation = await snapshot_database.add_conversation(owner)
    stranger = await snapshot_database.add_user()

    with pytest.raises(WebSnapshotOwnerMismatch):
        await _create(store, stranger, conversation)
    with pytest.raises(WebSnapshotOwnerMismatch):
        await _create(store, owner, uuid.uuid4())
    with pytest.raises(WebSnapshotOwnerMismatch):
        await _create(store, uuid.uuid4(), conversation)
    assert await snapshot_database.snapshot_count() == 0


@pytest.mark.asyncio
async def test_fingerprints_are_account_scoped_and_never_a_cross_account_key(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    owner_a = await snapshot_database.add_user()
    conversation_a = await snapshot_database.add_conversation(owner_a)
    owner_b = await snapshot_database.add_user()
    conversation_b = await snapshot_database.add_conversation(owner_b)

    url = f"https://example.test/{MARKER}/shared"
    content = f"identical page text {MARKER}"
    snapshot_a = await _create(store, owner_a, conversation_a, source_url=url, content=content)
    snapshot_b = await _create(store, owner_b, conversation_b, source_url=url, content=content)

    row_a = await snapshot_database.row(snapshot_a.id)
    row_b = await snapshot_database.row(snapshot_b.id)
    assert row_a is not None and row_b is not None

    # Same URL, same text, different account: different keyed digests. There is
    # no global content dedup and no shared fingerprint to match on.
    assert row_a["identity_fingerprint"] != row_b["identity_fingerprint"]
    assert row_a["content_fingerprint"] != row_b["content_fingerprint"]

    # Both rows are retained: nothing was merged or deduplicated.
    assert await snapshot_database.snapshot_count() == 2

    # The pepper is what keys the digest, so it is not a bare hash of the input.
    assert (
        store.identity_fingerprint(
            owner_a, source_url=url, extract_mode="article", extraction_version="v1"
        )
        == row_a["identity_fingerprint"]
    )


# ==========================================================================
# Versioning
# ==========================================================================


@pytest.mark.asyncio
async def test_snapshots_are_immutable_and_find_latest_returns_the_newest_version(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    url = f"https://example.test/{MARKER}/versioned"
    first_time = _utc_now() - timedelta(hours=2)
    second_time = _utc_now() - timedelta(hours=1)

    first = await _create(
        store, user_id, conversation_id, source_url=url, content="version one", now=first_time
    )
    second = await _create(
        store, user_id, conversation_id, source_url=url, content="version two", now=second_time
    )

    assert first.id != second.id
    assert await snapshot_database.snapshot_count() == 2

    latest = await store.find_latest(user_id, conversation_id, url, "article", "v1")
    assert latest is not None
    assert latest.id == second.id
    assert latest.content == "version two"

    # A refresh never rewrites the previous version: offsets already handed to
    # a caller keep referring to one fixed textual representation.
    retained = await store.get(user_id, conversation_id, first.id)
    assert retained.content == "version one"
    assert retained.retrieved_at == first.retrieved_at
    assert retained.expires_at == first.expires_at

    row_first = await snapshot_database.row(first.id)
    row_second = await snapshot_database.row(second.id)
    assert row_first is not None and row_second is not None
    assert row_first["identity_fingerprint"] == row_second["identity_fingerprint"]
    assert row_first["content_fingerprint"] != row_second["content_fingerprint"]

    # A different extraction version or mode is a different source identity and
    # must not silently reuse the stored version.
    assert await store.find_latest(user_id, conversation_id, url, "article", "v2") is None
    assert await store.find_latest(user_id, conversation_id, url, "metadata", "v1") is None
    assert await store.find_latest(user_id, conversation_id, url + "#frag", "article", "v1") is None


# ==========================================================================
# Quotas
# ==========================================================================


@pytest.mark.asyncio
async def test_conversation_count_ceiling_binds(snapshot_database: SnapshotDatabase) -> None:
    settings = _test_settings(web_snapshot_max_conversation_count=2)
    store = snapshot_database.store(settings)
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)

    await _create(store, user_id, conversation_id)
    await _create(store, user_id, conversation_id)
    with pytest.raises(WebSnapshotCapacityExceeded) as excinfo:
        await _create(store, user_id, conversation_id)
    assert excinfo.value.limit == "conversation_count"
    assert await snapshot_database.snapshot_count() == 2


@pytest.mark.asyncio
async def test_conversation_byte_ceiling_binds(snapshot_database: SnapshotDatabase) -> None:
    probe = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    url = f"https://example.test/{MARKER}/bytes"
    first = await _create(probe, user_id, conversation_id, source_url=url)
    size = first.stored_bytes

    tight = snapshot_database.store(_test_settings(web_snapshot_max_conversation_bytes=2 * size))
    await _create(tight, user_id, conversation_id, source_url=url)
    with pytest.raises(WebSnapshotCapacityExceeded) as excinfo:
        await _create(tight, user_id, conversation_id, source_url=url)
    assert excinfo.value.limit == "conversation_bytes"
    assert await snapshot_database.snapshot_count() == 2


@pytest.mark.asyncio
async def test_account_ceilings_bind_across_conversations(
    snapshot_database: SnapshotDatabase,
) -> None:
    # --- account count ceiling, spread over two conversations of one account
    counter = await snapshot_database.add_user()
    first_conversation = await snapshot_database.add_conversation(counter)
    second_conversation = await snapshot_database.add_conversation(counter)

    count_limited = snapshot_database.store(
        _test_settings(web_snapshot_max_account_count=2, web_snapshot_max_conversation_count=2)
    )
    await _create(count_limited, counter, first_conversation)
    await _create(count_limited, counter, second_conversation)
    with pytest.raises(WebSnapshotCapacityExceeded) as excinfo:
        await _create(count_limited, counter, second_conversation)
    assert excinfo.value.limit == "account_count"
    # The conversation itself still had room, so the account ceiling is what bound.
    assert await snapshot_database.snapshot_count() == 2

    # --- account byte ceiling, measured from a real stored row
    url = f"https://example.test/{MARKER}/account-bytes"
    probe = snapshot_database.store()
    measured_user = await snapshot_database.add_user()
    measured_conversation = await snapshot_database.add_conversation(measured_user)
    sized = await _create(probe, measured_user, measured_conversation, source_url=url)
    size = sized.stored_bytes

    payer = await snapshot_database.add_user()
    payer_first = await snapshot_database.add_conversation(payer)
    payer_second = await snapshot_database.add_conversation(payer)
    outsider = await snapshot_database.add_user()
    outsider_conversation = await snapshot_database.add_conversation(outsider)

    byte_limited = snapshot_database.store(
        _test_settings(
            web_snapshot_max_account_bytes=2 * size,
            web_snapshot_max_conversation_bytes=2 * size,
        )
    )
    # Three saves of identical stored size against a ceiling of exactly two:
    # the first two fit, and the third is refused by the *account* total while
    # its own conversation still has room, which is what makes this an account
    # ceiling rather than a conversation ceiling.
    await _create(byte_limited, payer, payer_first, source_url=url)
    await _create(byte_limited, payer, payer_second, source_url=url)
    with pytest.raises(WebSnapshotCapacityExceeded) as excinfo:
        await _create(byte_limited, payer, payer_second, source_url=url)
    assert excinfo.value.limit == "account_bytes"

    # A different account has its own budget and is unaffected.
    await _create(byte_limited, outsider, outsider_conversation, source_url=url)


@pytest.mark.asyncio
async def test_oversize_extracted_text_is_rejected(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store(_test_settings(web_snapshot_max_content_bytes=64))
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)

    with pytest.raises(WebSnapshotContentTooLarge):
        await _create(store, user_id, conversation_id, content="x" * 65)
    assert await snapshot_database.snapshot_count() == 0
    # The bound is on UTF-8 bytes, not code points.
    with pytest.raises(WebSnapshotContentTooLarge):
        await _create(store, user_id, conversation_id, content="é" * 33)


@pytest.mark.asyncio
async def test_identity_inputs_are_validated(snapshot_database: SnapshotDatabase) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)

    with pytest.raises(WebSnapshotValidationError):
        await _create(store, user_id, conversation_id, source_url="   ")
    with pytest.raises(WebSnapshotValidationError):
        await _create(store, user_id, conversation_id, source_url="x" * 9000)
    with pytest.raises(WebSnapshotValidationError):
        await _create(store, user_id, conversation_id, title="t" * 2000)
    with pytest.raises(WebSnapshotValidationError):
        await _create(store, user_id, conversation_id, extract_mode="")
    with pytest.raises(WebSnapshotValidationError):
        await _create(store, user_id, conversation_id, extraction_version="")
    with pytest.raises(WebSnapshotValidationError):
        await store.list(user_id, conversation_id, -1, 20)
    with pytest.raises(WebSnapshotValidationError):
        await store.list(user_id, conversation_id, 0, 0)
    with pytest.raises(WebSnapshotValidationError):
        await store.purge_expired(0)
    assert await snapshot_database.snapshot_count() == 0


@pytest.mark.asyncio
async def test_concurrent_admission_across_independent_store_instances(
    snapshot_database: SnapshotDatabase,
) -> None:
    """Eight simultaneous saves over two pools admit exactly the ceiling.

    Two ``WebSnapshotStore`` instances backed by two separate pools model two
    separate processes: the only thing that can serialize them is the database
    account row lock taken inside the admission transaction. Without it, both
    instances would read the same pre-insert usage sum and over-admit.
    """
    settings = _test_settings(web_snapshot_max_conversation_count=3)
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)

    second_pool = await snapshot_database.new_pool()
    stores = (
        WebSnapshotStore(snapshot_database.pool, snapshot_database.encryption(), settings),
        WebSnapshotStore(second_pool, snapshot_database.encryption(), settings),
    )

    results = await asyncio.gather(
        *(
            stores[index % 2].create(
                user_id,
                conversation_id,
                source_url=f"https://example.test/{MARKER}/concurrent",
                content=f"page {index}",
                extract_mode="article",
                extraction_version="v1",
            )
            for index in range(8)
        ),
        return_exceptions=True,
    )

    admitted = [item for item in results if isinstance(item, WebSnapshot)]
    denied = [item for item in results if isinstance(item, WebSnapshotCapacityExceeded)]
    unexpected = [
        item for item in results if not isinstance(item, (WebSnapshot, WebSnapshotCapacityExceeded))
    ]

    assert unexpected == [], f"unexpected outcomes: {unexpected!r}"
    assert len(admitted) == 3
    assert len(denied) == 5
    assert all(item.limit == "conversation_count" for item in denied)
    assert len({item.id for item in admitted}) == 3
    assert await snapshot_database.snapshot_count() == 3


@pytest.mark.asyncio
async def test_concurrent_byte_admission_never_exceeds_the_ceiling(
    snapshot_database: SnapshotDatabase,
) -> None:
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)

    probe_settings = _test_settings()
    probe = snapshot_database.store(probe_settings)
    sized = await _create(
        probe, user_id, conversation_id, source_url=f"https://example.test/{MARKER}/size"
    )
    size = sized.stored_bytes
    await snapshot_database.pool.execute("DELETE FROM web_snapshots")

    settings = _test_settings(web_snapshot_max_conversation_bytes=3 * size)
    second_pool = await snapshot_database.new_pool()
    stores = (
        WebSnapshotStore(snapshot_database.pool, snapshot_database.encryption(), settings),
        WebSnapshotStore(second_pool, snapshot_database.encryption(), settings),
    )

    await asyncio.gather(
        *(
            stores[index % 2].create(
                user_id,
                conversation_id,
                source_url=f"https://example.test/{MARKER}/size",
                content=sized.content,
                extract_mode="article",
                extraction_version="v1",
            )
            for index in range(6)
        ),
        return_exceptions=True,
    )

    total = await snapshot_database.pool.fetchval(
        "SELECT COALESCE(SUM(stored_bytes), 0) FROM web_snapshots"
    )
    assert int(total or 0) <= 3 * size
    assert await snapshot_database.snapshot_count() == 3


@pytest.mark.asyncio
async def test_conversation_deletion_racing_a_save_leaves_no_orphan(
    snapshot_database: SnapshotDatabase,
) -> None:
    """A delete that races an in-flight save never leaves a dangling snapshot.

    Every interleaving is acceptable — the save commits and the cascade removes
    it, or the save is rejected — as long as no row survives without its owning
    conversation and no conversation is resurrected.
    """
    store = snapshot_database.store()
    second_pool = await snapshot_database.new_pool()
    racer = WebSnapshotStore(second_pool, snapshot_database.encryption(), store.settings)

    for _ in range(3):
        user_id = await snapshot_database.add_user()
        conversation_id = await snapshot_database.add_conversation(user_id)

        save = asyncio.create_task(
            _create(racer, user_id, conversation_id, content="raced page body")
        )
        await asyncio.sleep(0)
        deleter = asyncio.create_task(
            snapshot_database.pool.execute(
                "DELETE FROM conversations WHERE id = $1", conversation_id
            )
        )
        save_result, _ = await asyncio.gather(save, deleter, return_exceptions=True)

        assert isinstance(save_result, (WebSnapshot, WebSnapshotOwnerMismatch)), save_result
        orphans = await snapshot_database.pool.fetchval(
            "SELECT COUNT(*) FROM web_snapshots ws "
            "LEFT JOIN conversations c ON c.id = ws.conversation_id "
            "WHERE c.id IS NULL"
        )
        assert int(orphans or 0) == 0
        conversations_left = await snapshot_database.pool.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE id = $1", conversation_id
        )
        assert int(conversations_left or 0) == 0


@pytest.mark.asyncio
async def test_snapshot_delete_racing_an_insert_cannot_over_admit(
    snapshot_database: SnapshotDatabase,
) -> None:
    settings = _test_settings(web_snapshot_max_conversation_count=1)
    store = snapshot_database.store(settings)
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    existing = await _create(store, user_id, conversation_id)

    second_pool = await snapshot_database.new_pool()
    racer = WebSnapshotStore(second_pool, snapshot_database.encryption(), settings)

    await asyncio.gather(
        store.delete(user_id, conversation_id, existing.id),
        _create(racer, user_id, conversation_id, content="competing page"),
        return_exceptions=True,
    )

    # The delete takes the same account-then-conversation lock order as the
    # insert, so the pair can never both observe room under a ceiling of one.
    assert await snapshot_database.snapshot_count() <= 1


# ==========================================================================
# Retention
# ==========================================================================


@pytest.mark.asyncio
async def test_expiry_is_enforced_on_read_before_any_cleanup(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    retrieved_at = _utc_now() - timedelta(days=31)

    expired = await _create(store, user_id, conversation_id, now=retrieved_at)
    assert expired.expires_at <= _utc_now()

    # The row is still physically present: expiry is a read refusal, not an
    # implicit delete, and a delayed or failing sweep cannot resurrect it.
    assert await snapshot_database.snapshot_count() == 1
    with pytest.raises(WebSnapshotExpired):
        await store.get(user_id, conversation_id, expired.id)
    with pytest.raises(WebSnapshotExpired):
        await store.delete(user_id, conversation_id, expired.id)
    assert (
        await store.find_latest(user_id, conversation_id, expired.source_url, "article", "v1")
        is None
    )

    page = await store.list(user_id, conversation_id)
    assert page.items == ()
    assert page.total == 0

    assert await store.purge_expired() == 1
    assert await snapshot_database.snapshot_count() == 0
    with pytest.raises(WebSnapshotNotFound):
        await store.get(user_id, conversation_id, expired.id)


@pytest.mark.asyncio
async def test_expired_rows_do_not_consume_quota_at_admission(
    snapshot_database: SnapshotDatabase,
) -> None:
    settings = _test_settings(web_snapshot_max_conversation_count=1)
    store = snapshot_database.store(settings)
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)

    await _create(store, user_id, conversation_id, now=_utc_now() - timedelta(days=31))

    # No purge call: admission purges the acting account's expired rows itself,
    # so a delayed sweep can never cause a false capacity refusal.
    fresh = await _create(store, user_id, conversation_id, content="still retained")
    assert await store.get(user_id, conversation_id, fresh.id) is not None
    assert await snapshot_database.snapshot_count() == 1


@pytest.mark.asyncio
async def test_purge_expired_is_bounded_and_leaves_retained_rows(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    stale = _utc_now() - timedelta(days=40)

    # The retained row is created first: every admission purges the acting
    # account's already-expired rows, so creating it last would reclaim the
    # stale rows before this test could measure a bounded sweep.
    live = await _create(store, user_id, conversation_id, content="retained page")
    for index in range(3):
        await _create(
            store,
            user_id,
            conversation_id,
            source_url=f"https://example.test/{MARKER}/stale/{index}",
            now=stale - timedelta(minutes=index),
        )
    assert await snapshot_database.snapshot_count() == 4

    assert await store.purge_expired(2) == 2
    assert await snapshot_database.snapshot_count() == 2
    assert await store.purge_expired() == 1
    assert await snapshot_database.snapshot_count() == 1
    assert (await store.get(user_id, conversation_id, live.id)).content == "retained page"


@pytest.mark.asyncio
async def test_conversation_cascade_removes_snapshots(
    snapshot_database: SnapshotDatabase,
) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    await _create(store, user_id, conversation_id)
    assert await snapshot_database.snapshot_count() == 1

    await snapshot_database.pool.execute("DELETE FROM conversations WHERE id = $1", conversation_id)
    assert await snapshot_database.snapshot_count() == 0


@pytest.mark.asyncio
async def test_account_cascade_removes_snapshots(snapshot_database: SnapshotDatabase) -> None:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    await _create(store, user_id, conversation_id)
    assert await snapshot_database.snapshot_count() == 1

    await snapshot_database.pool.execute("DELETE FROM users WHERE id = $1", user_id)
    assert await snapshot_database.snapshot_count() == 0


# ==========================================================================
# HTTP surface
# ==========================================================================


def _build_app(
    store: WebSnapshotStore | None,
    *,
    user_id: uuid.UUID | None = None,
    wire_store: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.include_router(snapshot_routes.router)
    app.state.app_state = None
    if wire_store and store is not None:
        app.dependency_overrides[get_web_snapshot_store] = lambda: store
    if user_id is not None:
        app.dependency_overrides[require_device_auth] = lambda: AuthenticatedDevice(
            user_id=user_id, device_id=uuid.uuid4(), session_id=uuid.uuid4()
        )
    return app


@pytest_asyncio.fixture
async def api_context(
    snapshot_database: SnapshotDatabase,
) -> AsyncIterator[tuple[SnapshotDatabase, WebSnapshotStore, uuid.UUID, uuid.UUID]]:
    store = snapshot_database.store()
    user_id = await snapshot_database.add_user()
    conversation_id = await snapshot_database.add_conversation(user_id)
    yield snapshot_database, store, user_id, conversation_id


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_snapshot_routes_require_authentication(
    api_context: tuple[SnapshotDatabase, WebSnapshotStore, uuid.UUID, uuid.UUID],
) -> None:
    database, store, user_id, conversation_id = api_context
    snapshot = await _create(store, user_id, conversation_id)
    base = f"/conversations/{conversation_id}/web-snapshots"
    # No override for require_device_auth: the real dependency must refuse.
    app = _build_app(store, user_id=None)

    async with await _client(app) as client:
        for method, path in (
            ("GET", base),
            ("GET", f"{base}/{snapshot.id}/export"),
            ("DELETE", f"{base}/{snapshot.id}"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 401, (method, path)
        # A malformed bearer header is refused the same way.
        response = await client.get(base, headers={"Authorization": "Token abc"})
        assert response.status_code == 401

    assert await database.snapshot_count() == 1


@pytest.mark.asyncio
async def test_store_dependency_fails_closed_without_a_database(
    api_context: tuple[SnapshotDatabase, WebSnapshotStore, uuid.UUID, uuid.UUID],
) -> None:
    _, _, user_id, conversation_id = api_context
    app = _build_app(None, user_id=user_id, wire_store=False)

    async with await _client(app) as client:
        response = await client.get(f"/conversations/{conversation_id}/web-snapshots")
    assert response.status_code == 503
    assert MARKER not in response.text
    assert "asyncpg" not in response.text.lower()


@pytest.mark.asyncio
async def test_list_export_delete_roundtrip(
    api_context: tuple[SnapshotDatabase, WebSnapshotStore, uuid.UUID, uuid.UUID],
) -> None:
    database, store, user_id, conversation_id = api_context
    first = await _create(store, user_id, conversation_id, content=f"older {MARKER}")
    second = await _create(
        store,
        user_id,
        conversation_id,
        source_url=f"https://example.test/{MARKER}/second",
        content=f"newer {MARKER}",
    )
    base = f"/conversations/{conversation_id}/web-snapshots"
    app = _build_app(store, user_id=user_id)

    async with await _client(app) as client:
        response = await client.get(base)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert body["offset"] == 0
        assert body["limit"] == 20
        assert [item["id"] for item in body["snapshots"]] == [str(second.id), str(first.id)]
        for item in body["snapshots"]:
            # Metadata only: a listing never carries page text.
            assert "content" not in item
            assert item["conversation_id"] == str(conversation_id)
            assert item["extract_mode"] == "article"
            assert item["expires_at"] > item["retrieved_at"]

        # Bounded pagination.
        paged = await client.get(base, params={"limit": 1, "offset": 1})
        assert paged.status_code == 200
        paged_body = paged.json()
        assert paged_body["total"] == 2
        assert [item["id"] for item in paged_body["snapshots"]] == [str(first.id)]
        for rejected in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            assert (await client.get(base, params=rejected)).status_code == 422

        export = await client.get(f"{base}/{second.id}/export")
        assert export.status_code == 200
        assert export.headers["cache-control"] == "no-store"
        disposition = export.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert f'filename="web-snapshot-{second.id}.json"' in disposition
        # The filename is opaque: no URL fragment, no page title.
        assert MARKER not in disposition
        assert "example.test" not in disposition
        document = export.json()
        assert document["schema"] == EXPORT_SCHEMA
        assert document["version"] == EXPORT_VERSION
        assert document["snapshot_id"] == str(second.id)
        assert document["conversation_id"] == str(conversation_id)
        assert document["content"] == second.content
        assert MARKER in document["source_url"]
        assert document["content_chars"] == len(second.content)
        assert "user_id" not in document

        deleted = await client.delete(f"{base}/{second.id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deleted"}
        assert await database.snapshot_count() == 1

        assert (await client.get(f"{base}/{second.id}/export")).status_code == 404
        assert (await client.delete(f"{base}/{second.id}")).status_code == 404
        remaining = await client.get(base)
        assert remaining.json()["total"] == 1


@pytest.mark.asyncio
async def test_wrong_owner_conversation_and_id_are_the_same_404(
    api_context: tuple[SnapshotDatabase, WebSnapshotStore, uuid.UUID, uuid.UUID],
) -> None:
    database, store, user_id, conversation_id = api_context
    snapshot = await _create(store, user_id, conversation_id)

    intruder = await database.add_user()
    intruder_conversation = await database.add_conversation(intruder)

    # A conversation the actor does not own is refused on all three routes,
    # including the listing: an unauthorized conversation must not answer with
    # an empty page, because that would be indistinguishable from a real
    # conversation that simply has no retained sources.
    for actor, path_conversation in (
        (intruder, conversation_id),
        (user_id, intruder_conversation),
        (user_id, uuid.uuid4()),
    ):
        base = f"/conversations/{path_conversation}/web-snapshots"
        app = _build_app(store, user_id=actor)
        async with await _client(app) as client:
            responses = (
                await client.get(base),
                await client.get(f"{base}/{snapshot.id}/export"),
                await client.delete(f"{base}/{snapshot.id}"),
            )
        for response in responses:
            assert response.status_code == 404, (
                base,
                [item.status_code for item in responses],
            )
            assert response.json()["detail"] == "Snapshot not found"
            assert MARKER not in response.text

    # A conversation the actor *does* own, addressed with a snapshot id that is
    # not in it, is the same 404 as a wrong owner: "not yours" and "not real"
    # are not distinguishable. The listing is unaffected because it does not
    # select by snapshot id.
    for actor, path_conversation, snapshot_id in (
        (user_id, conversation_id, uuid.uuid4()),
        (intruder, intruder_conversation, snapshot.id),
    ):
        base = f"/conversations/{path_conversation}/web-snapshots"
        app = _build_app(store, user_id=actor)
        async with await _client(app) as client:
            export = await client.get(f"{base}/{snapshot_id}/export")
            removal = await client.delete(f"{base}/{snapshot_id}")
            listing = await client.get(base)
        assert export.status_code == 404
        assert removal.status_code == 404
        assert export.json()["detail"] == "Snapshot not found"
        assert removal.json()["detail"] == "Snapshot not found"
        assert MARKER not in export.text
        assert listing.status_code == 200
        assert listing.json()["total"] == (1 if actor == user_id else 0)

    # Nothing was leaked or removed by the failed attempts.
    assert await database.snapshot_count() == 1
    intact = await store.get(user_id, conversation_id, snapshot.id)
    assert intact.content == snapshot.content


@pytest.mark.asyncio
async def test_expired_snapshots_are_404_and_absent_from_the_listing(
    api_context: tuple[SnapshotDatabase, WebSnapshotStore, uuid.UUID, uuid.UUID],
) -> None:
    _, store, user_id, conversation_id = api_context
    expired = await _create(store, user_id, conversation_id, now=_utc_now() - timedelta(days=31))
    base = f"/conversations/{conversation_id}/web-snapshots"
    app = _build_app(store, user_id=user_id)

    async with await _client(app) as client:
        listing = await client.get(base)
        assert listing.status_code == 200
        assert listing.json()["total"] == 0
        assert listing.json()["snapshots"] == []
        export = await client.get(f"{base}/{expired.id}/export")
        assert export.status_code == 404
        assert export.json()["detail"] == "Snapshot not found"
        removal = await client.delete(f"{base}/{expired.id}")
        assert removal.status_code == 404


@pytest.mark.asyncio
async def test_export_of_a_corrupt_snapshot_is_a_generic_500(
    api_context: tuple[SnapshotDatabase, WebSnapshotStore, uuid.UUID, uuid.UUID],
) -> None:
    database, store, user_id, conversation_id = api_context
    snapshot = await _create(store, user_id, conversation_id)
    await database.pool.execute(
        "UPDATE web_snapshots SET content_encrypted = $1 WHERE id = $2",
        "not-a-fernet-token",
        snapshot.id,
    )
    base = f"/conversations/{conversation_id}/web-snapshots"
    app = _build_app(store, user_id=user_id)

    async with await _client(app) as client:
        export = await client.get(f"{base}/{snapshot.id}/export")
    assert export.status_code == 500
    assert "integrity" not in export.text.lower()
    assert "fernet" not in export.text.lower()
    assert MARKER not in export.text
    assert "not-a-fernet-token" not in export.text


@pytest.mark.asyncio
async def test_unauthenticated_dependency_rejects_without_a_store(
    api_context: tuple[SnapshotDatabase, WebSnapshotStore, uuid.UUID, uuid.UUID],
) -> None:
    """A handler that raises before the store is used still returns 401, not 500."""
    _, _, user_id, conversation_id = api_context
    app = _build_app(None, user_id=user_id, wire_store=False)

    async def _refuse() -> AuthenticatedDevice:
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    app.dependency_overrides[require_device_auth] = _refuse
    async with await _client(app) as client:
        response = await client.get(f"/conversations/{conversation_id}/web-snapshots")
    assert response.status_code == 401
