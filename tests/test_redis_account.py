"""Fictional keys only; no real secret material or provider/network calls."""

import base64
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from orchestrator.config import Settings
from orchestrator.redis_account import (
    RedisAccountKeyError,
    account_prefix,
    fetch_identity,
    source_version,
    validate_redis_account_key,
)

TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")


def settings(key: str | None = TEST_KEY) -> Settings:
    # BaseSettings' runtime _env_file control is absent from its generated stub.
    kwargs: dict[str, Any] = {"_env_file": None}
    return Settings(**kwargs, redis_url="redis://fictional", daemon_redis_account_hash_key=key)


@pytest.mark.parametrize("key", [None, "", "short", " " + TEST_KEY, TEST_KEY + "=", "A" * 43])
def test_key_failure_is_fixed_and_secret_free(key: str | None) -> None:
    with pytest.raises(RedisAccountKeyError) as caught:
        validate_redis_account_key(settings(key))
    assert str(caught.value) == "DAEMON_REDIS_ACCOUNT_HASH_KEY requires a dedicated 32-byte key"


def test_canonical_encoding_and_no_secret_reuse() -> None:
    assert validate_redis_account_key(settings()) == bytes(range(32))
    for field in ("daemon_auth_pepper", "daemon_encryption_key"):
        value = settings()
        setattr(value, field, TEST_KEY)
        with pytest.raises(RedisAccountKeyError):
            validate_redis_account_key(value)
    # Noncanonical low padding bits decode to the same bytes but are rejected.
    with pytest.raises(RedisAccountKeyError):
        validate_redis_account_key(settings(TEST_KEY[:-1] + "9"))


def test_stable_owner_and_separate_domains() -> None:
    owner = uuid.UUID("00000000-0000-4000-8000-000000000001")
    a = account_prefix(owner, settings())
    assert a == account_prefix(str(owner), settings())
    assert a.startswith("account:v1:{") and a.endswith("}") and len(a) == 77
    assert a != account_prefix(uuid.UUID(int=2), settings())
    assert str(owner) not in a
    assert source_version(owner.bytes, settings()) not in a
    assert source_version(b"fictional-source", settings()) != fetch_identity(
        b"fictional-source", settings()
    )


def test_no_redis_optional_but_worker_effective_redis_is_required() -> None:
    kwargs: dict[str, Any] = {"_env_file": None}
    value = Settings(**kwargs, redis_url=None, daemon_redis_account_hash_key=None)
    assert validate_redis_account_key(value) is None
    with pytest.raises(RedisAccountKeyError):
        validate_redis_account_key(value, redis_configured=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "malformed", "A" * 43])
async def test_backend_invalid_key_fails_before_any_connection(monkeypatch, key) -> None:
    from orchestrator import db

    postgres = AsyncMock(side_effect=AssertionError("must not connect"))
    redis = AsyncMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(db.asyncpg, "create_pool", postgres)
    monkeypatch.setattr(db, "arq_create_pool", redis)
    with pytest.raises(RedisAccountKeyError):
        await db.init_app_state(settings(key))
    postgres.assert_not_awaited()
    redis.assert_not_awaited()
