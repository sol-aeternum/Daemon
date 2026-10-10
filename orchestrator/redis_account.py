"""Stable, independently keyed ownership for Redis state (deletion §6.3)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import uuid

from orchestrator.config import Settings, get_settings


class RedisAccountKeyError(ValueError):
    """A fixed diagnostic: never includes configured secret material."""


def validate_redis_account_key(
    settings: Settings, redis_configured: bool | None = None
) -> bytes | None:
    """Validate encoding, not entropy; operators must provision random bytes.

    The worker passes its effective Redis configuration (including its local
    fallback). No generated/process-local/DB-restored fallback is permitted.
    """
    if redis_configured is None:
        redis_configured = bool(settings.redis_url)
    value = settings.daemon_redis_account_hash_key
    if not redis_configured and not value:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        raise RedisAccountKeyError("DAEMON_REDIS_ACCOUNT_HASH_KEY requires a dedicated 32-byte key")
    decoded = base64.urlsafe_b64decode(value + "=")
    if (
        len(decoded) != 32
        or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value
        or len(set(decoded)) == 1
        or value == settings.daemon_internal_proxy_hmac_secret.strip()
        or (
            settings.daemon_admin_api_key is not None
            # The canonical ownership text is ASCII; no other candidate can
            # match it. Avoid encoding unrelated unencodable admin strings.
            and settings.daemon_admin_api_key.isascii()
            and hmac.compare_digest(value.encode(), settings.daemon_admin_api_key.encode())
        )
        or any(
            value == other.rstrip("=")
            for other in (settings.daemon_auth_pepper, settings.daemon_encryption_key)
            if isinstance(other, str) and other
        )
    ):
        raise RedisAccountKeyError("DAEMON_REDIS_ACCOUNT_HASH_KEY requires a dedicated 32-byte key")
    return decoded


def _digest(domain: bytes, value: bytes, settings: Settings | None = None) -> str:
    key = validate_redis_account_key(settings or get_settings(), redis_configured=True)
    assert key is not None
    return hmac.new(key, domain + b"\x00" + value, hashlib.sha256).hexdigest()


def account_prefix(user_id: uuid.UUID | str, settings: Settings | None = None) -> str:
    owner = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    token = _digest(b"daemon.redis.account.v1", owner.bytes, settings)
    return f"account:v1:{{{token}}}"


def source_version(value: bytes, settings: Settings | None = None) -> str:
    """Opaque source-version token; not a reversible content digest."""
    return _digest(b"daemon.redis.source.v1", value, settings)


def fetch_identity(value: bytes, settings: Settings | None = None) -> str:
    return _digest(b"daemon.redis.fetch.v1", value, settings)
