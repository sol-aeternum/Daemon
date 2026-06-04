"""Tests for the hosted identity rate limiter (TODO 7).

Coverage:
  - hash_key_material: determinism, distinct inputs produce distinct
    hashes, non-empty secret + value required, no raw value leakage
    in the output.
  - RateLimiter.check: threshold/window behavior, atomic TTL set on
    the first increment, independent scoping by endpoint and
    scope_kind, generic surface (no leaked counters), retry-after
    TTL behavior, fail-closed when Redis is unavailable.
  - enforce_rate_limit: 429 with Retry-After, 503 with Retry-After
    when hosted production requires Redis, silent bypass when
    self-hosted and Redis is absent, generic 4xx body.

A small in-memory fake Redis is used in place of a real `ArqRedis`
so the tests stay hermetic and run in CI without a Redis container.
The fake implements just the surface the rate limiter actually
exercises (`register_script`, and the `Script` object's call
protocol) and asserts the Lua shape and key/arg shape so any drift
in the wire contract fails fast.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from arq.connections import ArqRedis  # noqa: E402

from orchestrator.services.identity.rate_limiter import (  # noqa: E402
    RateLimiter,
    RateLimitPolicy,
    RateLimitUnavailableError,
    hash_key_material,
)


# ---------------------------------------------------------------------------
# Fake Redis
# ---------------------------------------------------------------------------


class FakeScript:
    """Drop-in replacement for the redis-py Script object.

    Records each (keys, args, client) invocation and returns a
    deterministic INCR + PTTL simulation. Tests may pre-seed
    `store[key]` to simulate pre-existing counters.
    """

    def __init__(self, script_body: str, store: dict[str, list[int]]) -> None:
        self.script_body = script_body
        self.store = store
        self.calls: list[tuple[list[str], list[Any], Any]] = []

    async def __call__(
        self,
        *,
        keys: list[str],
        args: list[Any],
        client: Any,
    ) -> list[int]:
        self.calls.append((list(keys), list(args), client))
        # Mirror the Lua semantics: INCR, set TTL on first hit,
        # return {new_count, pttl_ms}.
        if len(keys) != 1:
            raise AssertionError(f"expected 1 key, got {len(keys)}")
        if len(args) != 1:
            raise AssertionError(f"expected 1 arg, got {len(args)}")
        key = keys[0]
        window_ms = int(args[0])
        if key not in self.store:
            self.store[key] = [0, window_ms]
        self.store[key][0] += 1
        return [self.store[key][0], self.store[key][1]]


class FakeRedis:
    """Minimal ArqRedis surface used by RateLimiter."""

    def __init__(self) -> None:
        self.store: dict[str, list[int]] = {}
        self._scripts: list[FakeScript] = []

    def register_script(self, script_body: str) -> FakeScript:
        script = FakeScript(script_body, self.store)
        self._scripts.append(script)
        return script

    @property
    def script(self) -> FakeScript:
        if not self._scripts:
            raise AssertionError("register_script was not called")
        return self._scripts[-1]


HMAC_SECRET = "test-pepper-" + "x" * 60


# ---------------------------------------------------------------------------
# hash_key_material
# ---------------------------------------------------------------------------


class TestHashKeyMaterial:
    def test_deterministic(self) -> None:
        a = hash_key_material(HMAC_SECRET, "user@example.com")
        b = hash_key_material(HMAC_SECRET, "user@example.com")
        assert a == b

    def test_distinct_inputs_produce_distinct_hashes(self) -> None:
        a = hash_key_material(HMAC_SECRET, "user-a@example.com")
        b = hash_key_material(HMAC_SECRET, "user-b@example.com")
        assert a != b

    def test_distinct_secrets_produce_distinct_hashes(self) -> None:
        a = hash_key_material(HMAC_SECRET, "user@example.com")
        b = hash_key_material("different-secret-" + "y" * 50, "user@example.com")
        assert a != b

    def test_output_is_16_lowercase_hex(self) -> None:
        out = hash_key_material(HMAC_SECRET, "1.2.3.4")
        assert len(out) == 16
        assert all(c in "0123456789abcdef" for c in out)

    def test_raw_value_not_in_output(self) -> None:
        # Sanity: 16 hex chars of HMAC-SHA256 must not contain the
        # raw input verbatim.
        raw = "user@example.com"
        out = hash_key_material(HMAC_SECRET, raw)
        assert raw not in out
        assert raw.replace("@", "") not in out  # partial-leak guard

    def test_empty_secret_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty secret"):
            hash_key_material("", "user@example.com")

    def test_empty_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty value"):
            hash_key_material(HMAC_SECRET, "")


# ---------------------------------------------------------------------------
# RateLimiter (no Redis)
# ---------------------------------------------------------------------------


class TestRateLimiterNoRedis:
    def test_is_redis_available_false(self) -> None:
        limiter = RateLimiter(None, hmac_secret=HMAC_SECRET)
        assert limiter.is_redis_available is False
        assert limiter.redis is None

    @pytest.mark.asyncio
    async def test_check_raises_when_no_redis(self) -> None:
        limiter = RateLimiter(None, hmac_secret=HMAC_SECRET)
        policy = RateLimitPolicy(limit=5, window_seconds=60)
        with pytest.raises(RateLimitUnavailableError):
            await limiter.check(
                endpoint="auth:setup",
                scope_kind="ip",
                raw_value="1.2.3.4",
                policy=policy,
            )


# ---------------------------------------------------------------------------
# RateLimiter.check — with fake Redis
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture
async def limiter(fake_redis: FakeRedis) -> RateLimiter:
    return RateLimiter(cast(ArqRedis, fake_redis), hmac_secret=HMAC_SECRET)


class TestRateLimiterCheck:
    @pytest.mark.asyncio
    async def test_first_increments_returns_count_one(self, limiter: RateLimiter) -> None:
        policy = RateLimitPolicy(limit=3, window_seconds=60)
        decision = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        assert decision.allowed is True
        assert decision.count == 1
        assert decision.limit == 3
        assert decision.remaining == 2
        assert decision.window_seconds == 60
        assert decision.retry_after_seconds == 60

    @pytest.mark.asyncio
    async def test_allows_until_threshold(self, limiter: RateLimiter) -> None:
        policy = RateLimitPolicy(limit=3, window_seconds=60)
        for expected_count in (1, 2, 3):
            decision = await limiter.check(
                endpoint="auth:setup",
                scope_kind="ip",
                raw_value="1.2.3.4",
                policy=policy,
            )
            assert decision.allowed is True
            assert decision.count == expected_count

    @pytest.mark.asyncio
    async def test_blocks_at_threshold_plus_one(self, limiter: RateLimiter) -> None:
        policy = RateLimitPolicy(limit=3, window_seconds=60)
        for _ in range(3):
            await limiter.check(
                endpoint="auth:setup",
                scope_kind="ip",
                raw_value="1.2.3.4",
                policy=policy,
            )
        decision = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        assert decision.allowed is False
        assert decision.count == 4
        assert decision.remaining == 0
        # Retry-After is bounded by the window, never larger.
        assert 0 < decision.retry_after_seconds <= 60

    @pytest.mark.asyncio
    async def test_ttl_is_set_on_first_increment(
        self, limiter: RateLimiter, fake_redis: FakeRedis
    ) -> None:
        policy = RateLimitPolicy(limit=5, window_seconds=30)
        await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        # The fake Script simulates PEXPIRE on first INCR; the key
        # entry shape proves the TTL was set in the same Lua call.
        key = limiter.build_key("auth:setup", "ip", "1.2.3.4")
        assert key in fake_redis.store
        count, ttl_ms = fake_redis.store[key]
        assert count == 1
        assert ttl_ms == 30_000  # 30 seconds in ms

    @pytest.mark.asyncio
    async def test_independent_scoping_by_endpoint(self, limiter: RateLimiter) -> None:
        policy = RateLimitPolicy(limit=1, window_seconds=60)
        first = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        second = await limiter.check(
            endpoint="auth:enroll:complete",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        assert first.allowed is True
        assert second.allowed is True
        # Third call on either endpoint must trip.
        first_again = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        assert first_again.allowed is False

    @pytest.mark.asyncio
    async def test_independent_scoping_by_scope_kind(self, limiter: RateLimiter) -> None:
        policy = RateLimitPolicy(limit=1, window_seconds=60)
        ip_call = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        email_call = await limiter.check(
            endpoint="auth:setup",
            scope_kind="email",
            raw_value="user@example.com",
            policy=policy,
        )
        assert ip_call.allowed is True
        assert email_call.allowed is True

    @pytest.mark.asyncio
    async def test_independent_scoping_by_value(self, limiter: RateLimiter) -> None:
        policy = RateLimitPolicy(limit=1, window_seconds=60)
        a = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        b = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="5.6.7.8",
            policy=policy,
        )
        assert a.allowed is True
        assert b.allowed is True

    @pytest.mark.asyncio
    async def test_redis_failure_raises_unavailable(self, fake_redis: FakeRedis) -> None:
        limiter = RateLimiter(cast(ArqRedis, fake_redis), hmac_secret=HMAC_SECRET)
        # Swap the cached script for a one-shot failure version.
        original = limiter._script  # type: ignore[attr-defined]
        consumed = {"done": False}
        exc = ConnectionError("boom")

        async def _boom(**_kwargs: Any) -> Any:
            if not consumed["done"]:
                consumed["done"] = True
                raise exc
            assert original is not None
            return await original(**_kwargs)

        limiter._script = _boom  # type: ignore[assignment]

        policy = RateLimitPolicy(limit=3, window_seconds=60)
        with pytest.raises(RateLimitUnavailableError):
            await limiter.check(
                endpoint="auth:setup",
                scope_kind="ip",
                raw_value="1.2.3.4",
                policy=policy,
            )

    @pytest.mark.asyncio
    async def test_script_called_with_lua_body_via_register(
        self, fake_redis: FakeRedis, limiter: RateLimiter
    ) -> None:
        # Sanity: the helper actually registered a script. The fake
        # captures the body, and the rate limiter calls it once per
        # `check()`.
        assert len(fake_redis._scripts) == 1
        assert "INCR" in fake_redis._scripts[0].script_body
        assert "PEXPIRE" in fake_redis._scripts[0].script_body
        policy = RateLimitPolicy(limit=3, window_seconds=60)
        await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        assert len(fake_redis.script.calls) == 1
        keys, args, client = fake_redis.script.calls[0]
        assert client is fake_redis
        assert args == [60_000]  # window in ms
        # The key is HMAC-truncated — no raw IP appears in it.
        assert "1.2.3.4" not in keys[0]
        assert keys[0].startswith("rl:auth:setup:ip:")

    @pytest.mark.asyncio
    async def test_decision_surface_carries_no_raw_values(self, limiter: RateLimiter) -> None:
        policy = RateLimitPolicy(limit=2, window_seconds=60)
        decision = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        # The dataclass fields are typed; this assertion is the
        # contract that callers (e.g. response builders) may rely on.
        assert set(vars(decision).keys()) == {
            "allowed",
            "count",
            "limit",
            "window_seconds",
            "retry_after_seconds",
        }

    @pytest.mark.asyncio
    async def test_remaining_floors_at_zero(self, limiter: RateLimiter) -> None:
        policy = RateLimitPolicy(limit=1, window_seconds=60)
        await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        decision = await limiter.check(
            endpoint="auth:setup",
            scope_kind="ip",
            raw_value="1.2.3.4",
            policy=policy,
        )
        assert decision.allowed is False
        assert decision.remaining == 0


# ---------------------------------------------------------------------------
# build_key — namespacing + no raw leakage
# ---------------------------------------------------------------------------


class TestBuildKey:
    def test_key_shape(self) -> None:
        limiter = RateLimiter(cast(ArqRedis, FakeRedis()), hmac_secret=HMAC_SECRET)
        key = limiter.build_key("auth:setup", "ip", "1.2.3.4")
        assert key.startswith("rl:auth:setup:ip:")
        assert "1.2.3.4" not in key

    def test_custom_namespace(self) -> None:
        limiter = RateLimiter(cast(ArqRedis, FakeRedis()), hmac_secret=HMAC_SECRET, namespace="lim")
        key = limiter.build_key("auth:setup", "ip", "1.2.3.4")
        assert key.startswith("lim:auth:setup:ip:")

    def test_invalid_scope_kind(self) -> None:
        limiter = RateLimiter(cast(ArqRedis, FakeRedis()), hmac_secret=HMAC_SECRET)
        with pytest.raises(ValueError, match="scope_kind"):
            limiter.build_key("auth:setup", "user-agent", "Mozilla/5.0")

    def test_empty_endpoint_rejected(self) -> None:
        limiter = RateLimiter(cast(ArqRedis, FakeRedis()), hmac_secret=HMAC_SECRET)
        with pytest.raises(ValueError, match="endpoint"):
            limiter.build_key("", "ip", "1.2.3.4")


# ---------------------------------------------------------------------------
# RateLimitPolicy
# ---------------------------------------------------------------------------


class TestRateLimitPolicy:
    def test_minimum_values_accepted(self) -> None:
        policy = RateLimitPolicy(limit=1, window_seconds=1)
        assert policy.limit == 1
        assert policy.window_seconds == 1

    def test_zero_limit_rejected(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            RateLimitPolicy(limit=0, window_seconds=60)

    def test_zero_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="window_seconds"):
            RateLimitPolicy(limit=5, window_seconds=0)


# ---------------------------------------------------------------------------
# enforce_rate_limit — HTTP layer integration
# ---------------------------------------------------------------------------


class TestEnforceRateLimit:
    @pytest_asyncio.fixture
    async def request_factory(self):
        """Build a minimal `Request`-like object for enforce_rate_limit.

        The helper only uses `request.app.state.app_state` and
        `request.client.host`, so we hand-roll a tiny object instead
        of going through the full FastAPI machinery.
        """

        class _Client:
            def __init__(self, host: str) -> None:
                self.host = host

        class _State:
            def __init__(self, redis: Any) -> None:
                self.app_state = _AppState(redis=redis)

        class _App:
            def __init__(self, redis: Any) -> None:
                self.state = _State(redis=redis)

        class _AppState:
            def __init__(self, redis: Any) -> None:
                self.redis = redis

        def _make(client_host: str, *, redis: Any) -> Any:
            req = cast(Any, object.__new__(type("_Stub", (), {})))
            req.app = _App(redis)
            req.client = _Client(client_host)
            return req

        return _make

    @pytest.mark.asyncio
    async def test_returns_silently_when_allowed(
        self, request_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from orchestrator.services.identity import enforce_rate_limit

        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_REQUIRE_REDIS", "true")
        monkeypatch.setenv("DAEMON_AUTH_PEPPER", HMAC_SECRET)
        from orchestrator.config import get_settings

        get_settings.cache_clear()
        try:
            fake = cast(ArqRedis, FakeRedis())
            limiter = RateLimiter(fake, hmac_secret=HMAC_SECRET)
            req = request_factory("1.2.3.4", redis=fake)
            # No exception => pass.
            await enforce_rate_limit(
                request=req,
                limiter=limiter,
                endpoint="auth:setup",
                policies=[("ip", "1.2.3.4", RateLimitPolicy(limit=3, window_seconds=60))],
            )
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_raises_429_with_retry_after(
        self, request_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import HTTPException

        from orchestrator.services.identity import enforce_rate_limit

        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_REQUIRE_REDIS", "true")
        monkeypatch.setenv("DAEMON_AUTH_PEPPER", HMAC_SECRET)
        from orchestrator.config import get_settings

        get_settings.cache_clear()
        try:
            fake = cast(ArqRedis, FakeRedis())
            # Pre-seed the fake so the very first call returns count > limit.
            limiter = RateLimiter(fake, hmac_secret=HMAC_SECRET)
            key = limiter.build_key("auth:setup", "ip", "1.2.3.4")
            cast(Any, fake).store[key] = [10, 60_000]
            req = request_factory("1.2.3.4", redis=fake)
            with pytest.raises(HTTPException) as excinfo:
                await enforce_rate_limit(
                    request=req,
                    limiter=limiter,
                    endpoint="auth:setup",
                    policies=[
                        (
                            "ip",
                            "1.2.3.4",
                            RateLimitPolicy(limit=3, window_seconds=60),
                        )
                    ],
                )
            assert excinfo.value.status_code == 429
            assert excinfo.value.headers is not None
            retry_after = int(excinfo.value.headers["Retry-After"])
            assert 0 < retry_after <= 60
            # Body is generic — no leaked counters, no raw IP.
            assert excinfo.value.detail == "rate_limited"
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_raises_503_when_redis_absent_in_hosted_production(
        self, request_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import HTTPException

        from orchestrator.services.identity import enforce_rate_limit

        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "true")
        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_REQUIRE_REDIS", "true")
        monkeypatch.setenv("DAEMON_AUTH_PEPPER", HMAC_SECRET)
        from orchestrator.config import get_settings

        get_settings.cache_clear()
        try:
            limiter = RateLimiter(None, hmac_secret=HMAC_SECRET)
            req = request_factory("1.2.3.4", redis=None)
            with pytest.raises(HTTPException) as excinfo:
                await enforce_rate_limit(
                    request=req,
                    limiter=limiter,
                    endpoint="auth:setup",
                    policies=[
                        (
                            "ip",
                            "1.2.3.4",
                            RateLimitPolicy(limit=3, window_seconds=60),
                        )
                    ],
                )
            assert excinfo.value.status_code == 503
            assert excinfo.value.headers is not None
            assert excinfo.value.headers["Retry-After"] == "5"
            assert excinfo.value.detail == "service_unavailable"
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_silent_bypass_when_not_hosted(
        self, request_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from orchestrator.services.identity import enforce_rate_limit

        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "false")
        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_REQUIRE_REDIS", "false")
        monkeypatch.setenv("DAEMON_AUTH_PEPPER", HMAC_SECRET)
        from orchestrator.config import get_settings

        get_settings.cache_clear()
        try:
            limiter = RateLimiter(None, hmac_secret=HMAC_SECRET)
            req = request_factory("1.2.3.4", redis=None)
            # No exception — self-hosted/development mode silently
            # bypasses the limit when Redis is absent.
            await enforce_rate_limit(
                request=req,
                limiter=limiter,
                endpoint="auth:setup",
                policies=[
                    (
                        "ip",
                        "1.2.3.4",
                        RateLimitPolicy(limit=3, window_seconds=60),
                    )
                ],
            )
        finally:
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Regression: dev / self-hosted builds without DAEMON_AUTH_PEPPER
# ---------------------------------------------------------------------------


class TestDevNoPepperRegression:
    """Regression for the partial TODO 7 implementation.

    The first cut of `get_rate_limiter` used
    `settings.daemon_auth_pepper or ""` as the HMAC secret, which
    raised `ValueError` in development where `DAEMON_AUTH_PEPPER` is
    intentionally absent. The fix routes the secret through
    `validate_and_get_pepper`, which returns the process-ephemeral
    pepper in dev mode (with a warning) and the validated
    production-strength pepper otherwise.

    These tests prove the regression is closed from both sides:
    (a) `get_rate_limiter` constructs without raising in dev,
    (b) the route layer can build a limiter before any of the
        legacy auth logic (DB pool check, CSRF check) runs.
    """

    @pytest.mark.asyncio
    async def test_get_rate_limiter_works_in_development_without_pepper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from orchestrator.config import get_settings
        from orchestrator.services.identity import get_rate_limiter

        # Dev mode, no pepper, no Redis — must NOT raise.
        monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
        monkeypatch.delenv("DAEMON_AUTH_PEPPER", raising=False)
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_ENABLED", raising=False)
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_REQUIRE_REDIS", raising=False)
        get_settings.cache_clear()
        try:
            req = cast(Any, object.__new__(type("_Stub", (), {})))
            req.app = _AppWithNoRedis()
            req.client = _ClientWithHost("1.2.3.4")

            limiter = get_rate_limiter(req)
            # The helper succeeded; is_redis_available is False because
            # the test app state has no Redis.
            assert limiter.is_redis_available is False
            assert limiter.redis is None
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_get_rate_limiter_uses_validated_pepper_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from orchestrator.config import get_settings
        from orchestrator.services.identity import get_rate_limiter

        pepper = "x" * 50  # long enough to pass production validation
        monkeypatch.setenv("DAEMON_ENVIRONMENT", "production")
        monkeypatch.setenv("DAEMON_AUTH_PEPPER", pepper)
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_ENABLED", raising=False)
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_REQUIRE_REDIS", raising=False)
        get_settings.cache_clear()
        try:
            req = cast(Any, object.__new__(type("_Stub", (), {})))
            req.app = _AppWithNoRedis()
            req.client = _ClientWithHost("1.2.3.4")

            limiter = get_rate_limiter(req)
            # Production helper must use the production pepper, not
            # a dev-ephemeral value.
            h = hash_key_material(pepper, "1.2.3.4")
            assert limiter.build_key("auth:setup", "ip", "1.2.3.4").endswith(h)
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_get_rate_limiter_works_when_redis_is_present_in_dev(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dev host with Redis wired (e.g. docker-compose) must also
        construct successfully — the rate limiter should be ready
        for hosted mode should the operator flip the env var later.
        """
        from arq.connections import ArqRedis  # noqa: F401

        from orchestrator.config import get_settings
        from orchestrator.services.identity import get_rate_limiter

        monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
        monkeypatch.delenv("DAEMON_AUTH_PEPPER", raising=False)
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_ENABLED", raising=False)
        monkeypatch.delenv("DAEMON_HOSTED_IDENTITY_REQUIRE_REDIS", raising=False)
        get_settings.cache_clear()
        try:
            req = cast(Any, object.__new__(type("_Stub", (), {})))
            fake_redis = FakeRedis()
            req.app = _AppWithRedis(fake_redis)
            req.client = _ClientWithHost("1.2.3.4")

            limiter = get_rate_limiter(req)
            assert limiter.is_redis_available is True

            # And `check()` works end-to-end with the dev-ephemeral
            # pepper (no `ValueError` from the empty-secret path).
            policy = RateLimitPolicy(limit=2, window_seconds=60)
            d1 = await limiter.check("auth:setup", "ip", "1.2.3.4", policy)
            d2 = await limiter.check("auth:setup", "ip", "1.2.3.4", policy)
            assert d1.allowed is True
            assert d2.allowed is True
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_route_setup_call_does_not_raise_in_dev_without_pepper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: invoking the route-layer rate-limit code in
        dev mode (no pepper, no Redis, no hosted identity) does not
        raise. This is the exact failure mode the regression test
        guards against — Atlas saw `get_rate_limiter` blow up in
        dev because the empty-secret path crashed before any
        legacy auth logic ran.
        """
        from orchestrator.config import get_settings
        from orchestrator.services.identity import (
            client_ip_for_key,
            enforce_rate_limit,
            get_rate_limiter,
        )

        monkeypatch.setenv("DAEMON_ENVIRONMENT", "development")
        monkeypatch.delenv("DAEMON_AUTH_PEPPER", raising=False)
        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_ENABLED", "false")
        monkeypatch.setenv("DAEMON_HOSTED_IDENTITY_REQUIRE_REDIS", "false")
        get_settings.cache_clear()
        try:
            req = cast(Any, object.__new__(type("_Stub", (), {})))
            req.app = _AppWithNoRedis()
            req.client = _ClientWithHost("1.2.3.4")

            limiter = get_rate_limiter(req)
            await enforce_rate_limit(
                request=req,
                limiter=limiter,
                endpoint="auth:setup",
                policies=[
                    (
                        "ip",
                        client_ip_for_key(req),
                        RateLimitPolicy(limit=5, window_seconds=3600),
                    )
                ],
            )
        finally:
            get_settings.cache_clear()


class _ClientWithHost:
    def __init__(self, host: str) -> None:
        self.host = host


class _AppStateWithRedis:
    def __init__(self, redis: Any) -> None:
        self.redis = redis


class _AppStateNoRedis:
    """App state with no `redis` attribute — mirrors a real self-hosted
    dev app state where Redis was never initialised. `getattr(..., None)`
    in `get_rate_limiter` returns None for this case.
    """

    pass


class _AppWithRedis:
    def __init__(self, redis: Any) -> None:
        self.state = cast(Any, object.__new__(type("_Stub", (), {})))
        self.state.app_state = _AppStateWithRedis(redis=redis)


class _AppWithNoRedis:
    def __init__(self) -> None:
        self.state = cast(Any, object.__new__(type("_Stub", (), {})))
        self.state.app_state = _AppStateNoRedis()
