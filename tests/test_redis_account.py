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


@pytest.mark.parametrize("trust_proxy", [False, True])
@pytest.mark.parametrize("surrounding", ["", " \t\n"])
def test_proxy_hmac_reuse_rejected_after_consumer_normalization(trust_proxy, surrounding):
    value = settings()
    value.daemon_trust_proxy_forwarded_client_ip = trust_proxy
    value.daemon_internal_proxy_hmac_secret = surrounding + TEST_KEY + surrounding
    with pytest.raises(RedisAccountKeyError) as caught:
        validate_redis_account_key(value)
    assert str(caught.value) == "DAEMON_REDIS_ACCOUNT_HASH_KEY requires a dedicated 32-byte key"


@pytest.mark.parametrize(
    "proxy_key", ["", base64.urlsafe_b64encode(bytes(range(1, 33))).decode().rstrip("=")]
)
def test_empty_or_distinct_proxy_hmac_key_does_not_reject_ownership_key(proxy_key):
    value = settings()
    value.daemon_internal_proxy_hmac_secret = proxy_key
    assert validate_redis_account_key(value) == bytes(range(32))


def test_admin_bearer_key_reuse_is_rejected_with_fixed_diagnostic():
    value = settings()
    value.daemon_admin_api_key = TEST_KEY
    with pytest.raises(RedisAccountKeyError) as caught:
        validate_redis_account_key(value)
    assert str(caught.value) == "DAEMON_REDIS_ACCOUNT_HASH_KEY requires a dedicated 32-byte key"


@pytest.mark.parametrize(
    "admin_key",
    [None, "", "fictional-clé", base64.urlsafe_b64encode(bytes(range(1, 33))).decode().rstrip("=")],
)
def test_absent_empty_or_distinct_admin_key_preserves_valid_ownership_key(admin_key):
    value = settings()
    value.daemon_admin_api_key = admin_key
    assert validate_redis_account_key(value) == bytes(range(32))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret_field", ["daemon_internal_proxy_hmac_secret", "daemon_admin_api_key"]
)
async def test_backend_auth_secret_reuse_fails_before_any_connection(monkeypatch, secret_field):
    from orchestrator import db

    value = settings()
    secret = (
        " \t" + TEST_KEY + " \n"
        if secret_field == "daemon_internal_proxy_hmac_secret"
        else TEST_KEY
    )
    setattr(value, secret_field, secret)
    postgres = AsyncMock(side_effect=AssertionError("must not connect"))
    redis = AsyncMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(db.asyncpg, "create_pool", postgres)
    monkeypatch.setattr(db, "arq_create_pool", redis)
    with pytest.raises(RedisAccountKeyError) as caught:
        await db.init_app_state(value)
    assert str(caught.value) == "DAEMON_REDIS_ACCOUNT_HASH_KEY requires a dedicated 32-byte key"
    postgres.assert_not_awaited()
    redis.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret_field", ["daemon_internal_proxy_hmac_secret", "daemon_admin_api_key"]
)
async def test_worker_effective_redis_rejects_auth_reuse_before_native_main(
    monkeypatch, secret_field
):
    from arq.worker import Worker
    from orchestrator.worker.audit import AuditedWorker

    value = settings()
    value.redis_url = None  # ARQ's effective localhost fallback still requires independence.
    secret = (
        " \t" + TEST_KEY + " \n"
        if secret_field == "daemon_internal_proxy_hmac_secret"
        else TEST_KEY
    )
    setattr(value, secret_field, secret)
    native = AsyncMock(side_effect=AssertionError("native main must not connect"))
    monkeypatch.setattr(Worker, "main", native)

    async def unused_job(ctx):
        raise AssertionError("no job may execute")

    worker = AuditedWorker(functions=[unused_job], ctx={"settings": value}, handle_signals=False)
    with pytest.raises(RedisAccountKeyError) as caught:
        await worker.main()
    assert str(caught.value) == "DAEMON_REDIS_ACCOUNT_HASH_KEY requires a dedicated 32-byte key"
    native.assert_not_awaited()


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
