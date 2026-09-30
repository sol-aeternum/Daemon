"""Shared search pacing: parsing, atomic Redis admission and bounded refusal.

Unit tests cover the pure pieces (scope keys, strict header parsing, status
cooldown mapping). Integration tests run against the local Redis container
with an isolated random namespace per test and are skipped when it is
unreachable. No test contacts a search provider.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, cast

import pytest
import pytest_asyncio
from typing_extensions import AsyncIterator

from orchestrator.services import search_pacing as pacing
from orchestrator.services.search_pacing import (
    COOLDOWN_CAP_S,
    PacingRefused,
    PacingUnavailable,
    RedisLike,
    SearchPacer,
    parse_rate_headers,
)

REDIS_DSN = "redis://localhost:6379/0"
CONNECT_TIMEOUT_S = 2.0

BRAVE_WINDOWS: tuple[tuple[int, float], ...] = ((1, 1.0), (15000, 2592000.0))
BRAVE_HEADERS = {
    "X-RateLimit-Limit": "1, 15000",
    "X-RateLimit-Policy": "1;w=1, 15000;w=2592000",
    "X-RateLimit-Remaining": "1, 14523",
    "X-RateLimit-Reset": "1, 1419704",
}


def _unique_namespace() -> str:
    return f"{pacing.DEFAULT_NAMESPACE}:test:{uuid.uuid4().hex}"


async def _connect() -> Any:
    from redis.asyncio import Redis

    client = Redis.from_url(
        REDIS_DSN,
        decode_responses=True,
        socket_connect_timeout=CONNECT_TIMEOUT_S,
        socket_timeout=CONNECT_TIMEOUT_S,
    )
    await client.ping()
    return client


# --------------------------------------------------------------------------- #
# unit: scope keys
# --------------------------------------------------------------------------- #
def test_scope_prefix_is_opaque_and_high_entropy() -> None:
    prefix = pacing._scope_prefix(pacing.DEFAULT_NAMESPACE, "brave", "secret-key")
    assert prefix.startswith(f"{pacing.DEFAULT_NAMESPACE}:brave:")
    digest = prefix.rsplit(":", 1)[-1]
    assert len(digest) == 48
    assert "secret" not in prefix
    assert int(digest, 16) >= 0


def test_scope_prefix_separates_credentials_and_providers() -> None:
    brave = pacing._scope_prefix("ns", "brave", "one-credential")
    brave_other = pacing._scope_prefix("ns", "brave", "another-credential")
    tavily = pacing._scope_prefix("ns", "tavily", "one-credential")
    assert brave != brave_other
    assert brave != tavily
    assert pacing._scope_prefix("ns", "brave", "one-credential") == pacing._scope_prefix(
        "ns", "brave", "one-credential"
    )


# --------------------------------------------------------------------------- #
# unit: strict, bounded header parsing
# --------------------------------------------------------------------------- #
def test_brave_headers_parse_into_windows_and_no_cooldown() -> None:
    evidence = parse_rate_headers(BRAVE_HEADERS)
    assert evidence.windows == BRAVE_WINDOWS
    # A partly-used monthly quota is not exhaustion: no cooldown at all.
    assert evidence.cooldown_s is None


def test_exhausted_window_sets_a_bounded_cooldown() -> None:
    evidence = parse_rate_headers(
        {
            "X-RateLimit-Policy": "1;w=1, 15000;w=2592000",
            "X-RateLimit-Remaining": "0, 0",
            "X-RateLimit-Reset": "1, 1419704",
        }
    )
    assert evidence.windows == BRAVE_WINDOWS
    assert evidence.cooldown_s == 1419704.0


def test_retry_after_is_parsed_and_out_of_range_is_discarded() -> None:
    assert parse_rate_headers({"Retry-After": "7"}).cooldown_s == 7.0
    assert parse_rate_headers({"Retry-After": "999999999"}).cooldown_s is None


@pytest.mark.parametrize(
    "headers",
    [
        {"X-RateLimit-Policy": "abc;w=1"},
        {"X-RateLimit-Policy": "1;w=0"},
        {"X-RateLimit-Policy": "1;w=NaN"},
        {"X-RateLimit-Policy": "²;w=1"},
        {"X-RateLimit-Policy": "1;w=１"},
        {"X-RateLimit-Policy": "1;w=-5"},
        {"X-RateLimit-Policy": "1;w=1e9"},
        {"X-RateLimit-Policy": "1;w=99999999"},
        {"X-RateLimit-Policy": "1;w=1, "},
        {"X-RateLimit-Policy": "1;w=1, 15000;w=2592000, extra"},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5"},
        {"Retry-After": "  "},
        {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
        {"Retry-After": "-3"},
        {"Retry-After": "1e3"},
        {"Retry-After": "NaN"},
        {"Retry-After": "²"},
        {"X-RateLimit-Policy": "1;w=1," * 10000},
    ],
)
def test_malformed_or_hostile_headers_are_discarded(headers: dict[str, str]) -> None:
    evidence = parse_rate_headers(headers)
    assert evidence.windows is None
    assert evidence.cooldown_s is None


def test_windows_parse_without_remaining_or_reset() -> None:
    evidence = parse_rate_headers({"X-RateLimit-Policy": "1;w=1, 15000;w=2592000"})
    assert evidence.windows == BRAVE_WINDOWS
    assert evidence.cooldown_s is None


def test_inconsistent_or_unparsable_remaining_reset_is_discarded_but_windows_survive() -> None:
    evidence = parse_rate_headers(
        {
            "X-RateLimit-Policy": "1;w=1",
            "X-RateLimit-Remaining": "0, 0",
            "X-RateLimit-Reset": "1, 1",
        }
    )
    assert evidence.windows == ((1, 1.0),)
    assert evidence.cooldown_s is None
    evidence = parse_rate_headers(
        {"X-RateLimit-Policy": "1;w=1", "X-RateLimit-Remaining": "zero", "X-RateLimit-Reset": "1"}
    )
    assert evidence.windows == ((1, 1.0),)
    assert evidence.cooldown_s is None


def test_a_huge_reset_value_is_discarded_not_capped() -> None:
    evidence = parse_rate_headers(
        {
            "X-RateLimit-Policy": "15000;w=2592000",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "99999999999999",
        }
    )
    assert evidence.windows == ((15000, 2592000.0),)
    assert evidence.cooldown_s is None


def test_a_valid_long_reset_is_retained() -> None:
    evidence = parse_rate_headers(
        {
            "X-RateLimit-Policy": "15000;w=2592000",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "700000",
        }
    )
    assert evidence.cooldown_s == 700000.0


def test_header_names_are_matched_case_insensitively() -> None:
    evidence = parse_rate_headers(
        {"x-ratelimit-policy": "2;w=2", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "3"}
    )
    assert evidence.windows == ((2, 2.0),)
    assert evidence.cooldown_s == 3.0


def test_status_cooldown_mapping_is_closed_vocabulary() -> None:
    assert pacing._status_cooldown_s(429) == pacing.RATE_COOLDOWN_NO_EVIDENCE_S
    assert pacing._status_cooldown_s(402) == COOLDOWN_CAP_S
    assert pacing._status_cooldown_s(432) == COOLDOWN_CAP_S
    assert pacing._status_cooldown_s(433) == COOLDOWN_CAP_S
    assert pacing._status_cooldown_s(200) is None
    assert pacing._status_cooldown_s(401) is None
    assert pacing._status_cooldown_s(500) is None


# --------------------------------------------------------------------------- #
# integration: real Redis, isolated random namespace
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Any]:
    try:
        client = await asyncio.wait_for(_connect(), timeout=CONNECT_TIMEOUT_S)
    except Exception:
        pytest.skip("local Redis (localhost:6379) is not reachable")
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def namespace(redis_client: Any) -> AsyncIterator[str]:
    ns = _unique_namespace()
    try:
        yield ns
    finally:
        keys = await redis_client.keys(f"{ns}:*")
        if keys:
            await redis_client.delete(*keys)


def _pacer(
    redis_client: Any, namespace: str, *, fallback_min_interval_s: float = 0.15
) -> SearchPacer:
    async def factory() -> RedisLike:
        return redis_client

    return SearchPacer(
        client_factory=factory,
        namespace=namespace,
        fallback_min_interval_s=fallback_min_interval_s,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    )


async def _admit_scores(redis_client: Any, namespace: str) -> list[float]:
    keys = await redis_client.keys(f"{namespace}:*:admit")
    scores: list[float] = []
    for key in keys:
        pairs = await redis_client.zrange(key, 0, -1, withscores=True)
        scores.extend(float(score) for _member, score in pairs)
    return sorted(scores)


@pytest.mark.asyncio
async def test_second_admission_waits_the_fallback_spacing(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace)
    await pacer.admit("brave", "cred-a", wait_budget_s=5.0)
    started = time.monotonic()
    await pacer.admit("brave", "cred-a", wait_budget_s=5.0)
    waited = time.monotonic() - started
    assert waited >= 0.13
    assert waited < 2.0


@pytest.mark.asyncio
async def test_parallel_independent_pacers_cannot_overshoot(
    redis_client: Any, namespace: str
) -> None:
    pacers = [_pacer(redis_client, namespace) for _ in range(5)]
    grants: list[float] = []

    async def admit(pacer: SearchPacer) -> None:
        await pacer.admit("brave", "shared-cred", wait_budget_s=10.0)
        # Captured at the admit return: the moment this pacer may dispatch.
        grants.append(time.monotonic())

    await asyncio.gather(*(admit(pacer) for pacer in pacers))

    # No two dispatch grants inside one spacing window: the shared gate held.
    grants.sort()
    gaps = [later - earlier for earlier, later in zip(grants, grants[1:])]
    assert len(grants) == 5
    assert all(gap >= 0.14 for gap in gaps)


@pytest.mark.asyncio
async def test_learned_capacity_admits_concurrent_bursts(redis_client: Any, namespace: str) -> None:
    pacer = _pacer(redis_client, namespace)
    await pacer.observe(
        "brave",
        "cred",
        status=200,
        headers={
            "X-RateLimit-Policy": "3;w=1",
            "X-RateLimit-Remaining": "3",
            "X-RateLimit-Reset": "1",
        },
    )
    grants: list[float] = []

    async def admit() -> None:
        await pacer.admit("brave", "cred", wait_budget_s=5.0)
        grants.append(time.monotonic())

    started = time.monotonic()
    # A learned 3-per-second allowance admits three CONCURRENT dispatch grants
    # inside one window — no artificial spacing stands between them.
    await asyncio.gather(admit(), admit(), admit())
    elapsed = time.monotonic() - started
    assert elapsed < 0.25
    assert len(grants) == 3
    assert max(grants) - min(grants) < 0.2

    scores = await _admit_scores(redis_client, namespace)
    assert len(scores) == 3

    # A fourth admission in the same window refuses fast rather than waiting.
    started = time.monotonic()
    with pytest.raises(PacingRefused):
        await pacer.admit("brave", "cred", wait_budget_s=0.2)
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_credential_and_provider_scopes_are_independent(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace, fallback_min_interval_s=0.5)
    started = time.monotonic()
    await asyncio.gather(
        pacer.admit("brave", "cred-a", wait_budget_s=5.0),
        pacer.admit("brave", "cred-b", wait_budget_s=5.0),
        pacer.admit("tavily", "cred-a", wait_budget_s=5.0),
    )
    # Different credentials and providers never block each other.
    assert time.monotonic() - started < 0.3


@pytest.mark.asyncio
async def test_cancellation_during_wait_records_no_reservation(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace, fallback_min_interval_s=0.5)
    await pacer.admit("brave", "cred", wait_budget_s=5.0)

    task = asyncio.create_task(pacer.admit("brave", "cred", wait_budget_s=5.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Nothing was reserved by the cancelled waiter.
    scores = await _admit_scores(redis_client, namespace)
    assert len(scores) == 1
    # And the gate still works afterwards.
    await pacer.admit("brave", "cred", wait_budget_s=5.0)


@pytest.mark.asyncio
async def test_redis_outage_fails_closed(redis_client: Any, namespace: str) -> None:
    attempts = {"count": 0}

    async def broken_factory() -> RedisLike:
        attempts["count"] += 1
        raise ConnectionError("redis down")

    pacer = SearchPacer(
        client_factory=broken_factory, namespace=namespace, fallback_min_interval_s=0.15
    )
    started = time.monotonic()
    with pytest.raises(PacingUnavailable):
        await pacer.admit("brave", "cred", wait_budget_s=5.0)
    assert time.monotonic() - started < 1.0
    # The leash: a second attempt fails fast without re-dialling.
    with pytest.raises(PacingUnavailable):
        await pacer.admit("brave", "cred", wait_budget_s=5.0)
    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_missing_redis_configuration_fails_closed(namespace: str) -> None:
    pacer = SearchPacer(client_factory=None, namespace=namespace)
    with pytest.raises(PacingUnavailable):
        await pacer.admit("brave", "cred", wait_budget_s=5.0)


@pytest.mark.asyncio
async def test_failing_redis_call_fails_closed(redis_client: Any, namespace: str) -> None:
    class _Broken:
        async def eval(self, *_args: Any) -> Any:
            raise ConnectionError("dropped")

    async def factory() -> RedisLike:
        return _Broken()  # type: ignore[return-value]

    pacer = SearchPacer(client_factory=factory, namespace=namespace)
    with pytest.raises(PacingUnavailable):
        await pacer.admit("brave", "cred", wait_budget_s=5.0)


@pytest.mark.asyncio
async def test_rate_refusal_short_cooldown_waits_bounded(redis_client: Any, namespace: str) -> None:
    pacer = _pacer(redis_client, namespace, fallback_min_interval_s=0.05)
    await pacer.admit("brave", "cred", wait_budget_s=5.0)
    await pacer.observe("brave", "cred", status=429, headers={"Retry-After": "1"})

    # A wait longer than the budget refuses fast (never sleeps unbounded).
    started = time.monotonic()
    with pytest.raises(PacingRefused):
        await pacer.admit("brave", "cred", wait_budget_s=0.2)
    assert time.monotonic() - started < 0.5

    # A fitting budget is admitted once the cooldown has passed.
    started = time.monotonic()
    await pacer.admit("brave", "cred", wait_budget_s=5.0)
    assert time.monotonic() - started >= 0.8


@pytest.mark.asyncio
async def test_long_window_exhaustion_refuses_bounded_without_month_sleep(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace, fallback_min_interval_s=0.05)
    await pacer.observe("brave", "cred", status=402, headers={})
    started = time.monotonic()
    with pytest.raises(PacingRefused):
        await pacer.admit("brave", "cred", wait_budget_s=0.2)
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_cooldown_updates_are_monotonic(redis_client: Any, namespace: str) -> None:
    pacer = _pacer(redis_client, namespace, fallback_min_interval_s=0.05)
    await pacer.observe("brave", "cred", status=429, headers={"Retry-After": "3"})
    # A later, shorter cooldown must not shorten the existing one.
    await pacer.observe("brave", "cred", status=429, headers={"Retry-After": "0"})

    started = time.monotonic()
    with pytest.raises(PacingRefused):
        await pacer.admit("brave", "cred", wait_budget_s=1.0)
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_malformed_refusal_headers_cannot_create_huge_waits(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace, fallback_min_interval_s=0.05)
    await pacer.admit("brave", "cred", wait_budget_s=5.0)
    await pacer.observe(
        "brave",
        "cred",
        status=429,
        headers={"Retry-After": "999999999999"},
    )
    # The hostile header is discarded, so only the status default applies: the
    # documented 1-second window — never the million-second claimed wait.
    started = time.monotonic()
    await pacer.admit("brave", "cred", wait_budget_s=5.0)
    waited = time.monotonic() - started
    assert 0.9 <= waited < 2.0


@pytest.mark.asyncio
async def test_a_long_window_uses_a_fixed_counter_not_per_admission_history(
    redis_client: Any, namespace: str
) -> None:
    """A monthly-scale quota stays O(1) Redis memory and still bursts."""
    pacer = _pacer(redis_client, namespace)
    await pacer.observe(
        "brave",
        "cred",
        status=200,
        headers={
            "X-RateLimit-Policy": "100000;w=2592000",
            "X-RateLimit-Remaining": "100000",
            "X-RateLimit-Reset": "2592000",
        },
    )
    grants: list[float] = []

    async def admit() -> None:
        await pacer.admit("brave", "cred", wait_budget_s=5.0)
        grants.append(time.monotonic())

    started = time.monotonic()
    await asyncio.gather(admit(), admit(), admit())
    # Burst-capable: three grants within one 60-second window, unspaced.
    assert time.monotonic() - started < 0.25
    assert max(grants) - min(grants) < 0.2

    # Memory shape: a quota larger than the sliding-ZSET entry bound is one
    # fixed counter field — O(1) — not one entry per admission.
    count_key = pacing._scope_prefix(namespace, "brave", "cred") + ":count"
    fields = await redis_client.hkeys(count_key)
    assert fields == ["2592000000"]
    bucket, previous, count = (await redis_client.hget(count_key, "2592000000")).split(":")
    assert int(count) == 3
    assert int(previous) == 0
    assert int(bucket) > 0
    # And the grant history did not grow per admission.
    assert len(await _admit_scores(redis_client, namespace)) <= 3


@pytest.mark.asyncio
async def test_capacity_metadata_expires_back_to_fallback(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace, fallback_min_interval_s=0.05)
    await pacer.observe(
        "brave",
        "cred",
        status=200,
        headers={
            "X-RateLimit-Policy": "5;w=1",
            "X-RateLimit-Remaining": "5",
            "X-RateLimit-Reset": "1",
        },
    )
    cap_key = pacing._scope_prefix(namespace, "brave", "cred") + ":cap"
    ttl = await redis_client.ttl(cap_key)
    assert 0 < ttl <= int(pacing.CAPACITY_TTL_S) + 5


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["factory", "eval", "lock"])
async def test_all_redis_admission_awaits_are_inside_the_budget(stage: str) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    class Client:
        async def eval(self, *_args: Any) -> Any:
            await hang()

    async def factory() -> RedisLike:
        if stage == "factory":
            await hang()
        return cast(RedisLike, Client())

    pacer = SearchPacer(client_factory=factory)
    if stage == "lock":
        await pacer._client_lock.acquire()
    started = time.monotonic()
    try:
        with pytest.raises((PacingRefused, PacingUnavailable)):
            await pacer.admit("brave", "cred", wait_budget_s=0.05)
    finally:
        if stage == "lock":
            pacer._client_lock.release()
    assert 0.04 <= time.monotonic() - started < 0.3
    if stage != "lock":
        assert entered.is_set() and cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        None,
        [],
        [1],
        [0, 0],
        [2, 0],
        [-1, 0],
        [1, 3],
        [0, -1],
        [True, 0],
        [1, False],
        ["1", "0"],
        [0, 10_000_000_001],
        [1, 0, 0],
    ],
)
async def test_malformed_script_reply_never_grants(reply: Any) -> None:
    class Client:
        async def eval(self, *_args: Any) -> Any:
            return reply

    async def factory() -> RedisLike:
        return cast(RedisLike, Client())

    pacer = SearchPacer(client_factory=factory)
    with pytest.raises(PacingUnavailable):
        await pacer.admit("brave", "cred", wait_budget_s=0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["factory", "eval"])
async def test_cancel_during_redis_await_propagates(stage: str) -> None:
    entered = asyncio.Event()

    async def hang() -> None:
        entered.set()
        await asyncio.Event().wait()

    class Client:
        async def eval(self, *_args: Any) -> Any:
            await hang()

    async def factory() -> RedisLike:
        if stage == "factory":
            await hang()
        return cast(RedisLike, Client())

    task = asyncio.create_task(
        SearchPacer(client_factory=factory).admit("brave", "cred", wait_budget_s=10.0)
    )
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["factory", "eval"])
async def test_observation_redis_awaits_are_bounded(stage: str) -> None:
    cancelled = asyncio.Event()

    async def hang() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    class Client:
        async def eval(self, *_args: Any) -> Any:
            await hang()

    async def factory() -> RedisLike:
        if stage == "factory":
            await hang()
        return cast(RedisLike, Client())

    pacer = SearchPacer(client_factory=factory)
    started = time.monotonic()
    await pacer.observe(
        "brave",
        "cred",
        status=429,
        headers={"X-RateLimit-Policy": "2;w=1", "Retry-After": "1"},
        wait_budget_s=0.05,
    )
    assert 0.04 <= time.monotonic() - started < 0.3
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_long_cooldown_is_retained_but_refuses_without_sleep(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace)
    await pacer.observe(
        "brave",
        "cred",
        status=429,
        headers={
            "X-RateLimit-Policy": "15000;w=2592000",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1419704",
        },
    )
    prefix = pacing._scope_prefix(namespace, "brave", "cred")
    assert 1419700 <= await redis_client.ttl(prefix + ":cooldown") <= 1419704
    assert 0 < await redis_client.ttl(prefix + ":cap") <= 5184000
    started = time.monotonic()
    with pytest.raises(PacingRefused):
        await pacer.admit("brave", "cred", wait_budget_s=0.05)
    assert time.monotonic() - started < 0.2
    assert await redis_client.zcard(prefix + ":admit") == 0
    assert await redis_client.hlen(prefix + ":count") == 0


@pytest.mark.asyncio
async def test_large_quota_cannot_double_at_a_bucket_boundary(
    monkeypatch: pytest.MonkeyPatch, redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace)
    window = 4_000_000
    await pacer.observe(
        "brave", "cred", status=200, headers={"X-RateLimit-Policy": f"2;w={window}"}
    )
    script = pacing._ADMIT_SCRIPT

    def now(seconds: int) -> None:
        monkeypatch.setattr(
            pacing,
            "_ADMIT_SCRIPT",
            script.replace("local tm = redis.call('TIME')", f"local tm = {{{seconds}, 0}}"),
        )

    now(window)
    await pacer.admit("brave", "cred", wait_budget_s=0.1)
    now(2 * window - 1)
    await pacer.admit("brave", "cred", wait_budget_s=0.1)
    now(2 * window)
    with pytest.raises(PacingRefused):
        await pacer.admit("brave", "cred", wait_budget_s=0.1)
    now(3 * window)
    await pacer.admit("brave", "cred", wait_budget_s=0.1)


@pytest.mark.asyncio
async def test_eight_million_allowance_windows_have_constant_storage(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace)
    windows = list(range(1, 9))
    await pacer.observe(
        "brave",
        "cred",
        status=200,
        headers={
            "X-RateLimit-Policy": ", ".join(f"1000000;w={w}" for w in windows),
        },
    )
    prefix = pacing._scope_prefix(namespace, "brave", "cred")
    # Seed the final credit in each bucket: exercising a million-call allowance
    # does not require a million Redis round trips or per-call records.
    seconds, microseconds = await redis_client.time()
    now_ms = seconds * 1000 + microseconds // 1000
    await redis_client.hset(
        prefix + ":count",
        mapping={str(w * 1000): f"{now_ms // (w * 1000)}:0:999999" for w in windows},
    )
    await pacer.admit("brave", "cred", wait_budget_s=0.1)
    assert await redis_client.zcard(prefix + ":admit") == 0
    assert await redis_client.hlen(prefix + ":count") == 8
    assert await redis_client.hlen(prefix + ":cap") == 8
    assert await redis_client.memory_usage(prefix + ":count") < 2048
    with pytest.raises(PacingRefused):
        await pacer.admit("brave", "cred", wait_budget_s=0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [{"0": "10"}, {"1000": "1000001"}, {"1000": "NaN"}, {str(i * 1000): "1" for i in range(1, 10)}],
)
async def test_unsupported_stored_metadata_fails_closed(
    redis_client: Any, namespace: str, metadata: dict[str, str]
) -> None:
    prefix = pacing._scope_prefix(namespace, "brave", "cred")
    await redis_client.hset(prefix + ":cap", mapping=metadata)
    with pytest.raises(PacingUnavailable):
        await _pacer(redis_client, namespace).admit("brave", "cred", wait_budget_s=0.1)
    assert await redis_client.zcard(prefix + ":admit") == 0
    assert await redis_client.hlen(prefix + ":count") == 0


@pytest.mark.asyncio
async def test_unsupported_provider_metadata_fails_closed(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace)
    await pacer.observe("brave", "cred", status=200, headers={"X-RateLimit-Policy": "1000001;w=1"})
    with pytest.raises(PacingUnavailable):
        await pacer.admit("brave", "cred", wait_budget_s=0.1)


@pytest.mark.asyncio
async def test_multiple_redis_attempts_share_one_wall_budget() -> None:
    evaluations = 0

    class Client:
        async def eval(self, *_args: Any) -> Any:
            nonlocal evaluations
            evaluations += 1
            if evaluations == 1:
                await asyncio.sleep(0.02)
                return [0, 20]
            await asyncio.Event().wait()

    async def factory() -> RedisLike:
        return cast(RedisLike, Client())

    pacer = SearchPacer(client_factory=factory)
    started = time.monotonic()
    with pytest.raises((PacingRefused, PacingUnavailable)):
        await pacer.admit("brave", "cred", wait_budget_s=0.07)
    assert evaluations == 2
    assert 0.06 <= time.monotonic() - started < 0.2


@pytest.mark.asyncio
async def test_learned_window_concurrent_contenders_do_not_overshoot(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace)
    await pacer.observe("brave", "cred", status=200, headers={"X-RateLimit-Policy": "3;w=1"})
    outcomes = await asyncio.gather(
        *(
            _pacer(redis_client, namespace).admit("brave", "cred", wait_budget_s=0.1)
            for _ in range(12)
        ),
        return_exceptions=True,
    )
    assert sum(outcome is None for outcome in outcomes) == 3
    assert sum(isinstance(outcome, PacingRefused) for outcome in outcomes) == 9
    assert len(await _admit_scores(redis_client, namespace)) == 3


@pytest.mark.asyncio
async def test_learning_a_counter_preserves_the_bootstrap_grant(
    redis_client: Any, namespace: str
) -> None:
    pacer = _pacer(redis_client, namespace)
    await pacer.admit("brave", "cred", wait_budget_s=0.1)
    await pacer.observe("brave", "cred", status=200, headers={"X-RateLimit-Policy": "1;w=4000000"})
    # Repeated refusal must not lose the seeded past grant when ZSET history
    # is pruned; neither refusal records another admission.
    for _ in range(2):
        with pytest.raises(PacingRefused):
            await pacer.admit("brave", "cred", wait_budget_s=0.1)
    key = pacing._scope_prefix(namespace, "brave", "cred") + ":count"
    _bucket, previous, current = (await redis_client.hget(key, "4000000000")).split(":")
    assert int(previous) + int(current) == 1
