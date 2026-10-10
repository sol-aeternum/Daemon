"""Authenticated ownership without changing shared pre-auth counter identities."""

import uuid

import pytest

from orchestrator.redis_account import account_prefix
from orchestrator.services.identity.rate_limiter import RateLimiter

OWNER = str(uuid.UUID(int=1))
SESSION = str(uuid.UUID(int=2))


def test_user_and_session_counter_keys_use_authenticated_account_prefix() -> None:
    limiter = RateLimiter(None, hmac_secret="fictional-existing-scope-secret")
    user = limiter.build_key("chat", "user_id", OWNER, owner_id=OWNER)
    session = limiter.build_key("chat", "session_id", SESSION, owner_id=OWNER)
    other_session = limiter.build_key("chat", "session_id", str(uuid.UUID(int=3)), owner_id=OWNER)
    assert user.startswith(account_prefix(OWNER) + ":rl:")
    assert session.startswith(account_prefix(OWNER) + ":rl:")
    assert session != other_session
    assert OWNER not in user and SESSION not in session
    assert session != limiter.build_key(
        "chat", "session_id", SESSION, owner_id=str(uuid.UUID(int=4))
    )


@pytest.mark.parametrize(
    "scope,value,owner",
    [
        ("user_id", OWNER, None),
        ("session_id", SESSION, None),
        ("user_id", OWNER, str(uuid.UUID(int=4))),
    ],
)
def test_ownerless_or_mismatched_account_counter_rejected(scope, value, owner) -> None:
    limiter = RateLimiter(None, hmac_secret="fictional-existing-scope-secret")
    with pytest.raises(ValueError, match="authenticated owner"):
        limiter.build_key("chat", scope, value, owner_id=owner)


@pytest.mark.parametrize(
    "scope,value", [("ip", "192.0.2.1"), ("email", "fictional@example.invalid")]
)
def test_shared_pre_auth_scope_identity_is_unchanged(scope, value) -> None:
    limiter = RateLimiter(None, hmac_secret="fictional-existing-scope-secret")
    shared = limiter.build_key("auth", scope, value)
    assert shared == limiter.build_key("auth", scope, value, owner_id=OWNER)
    assert shared.startswith("rl:auth:")
    assert value not in shared
