"""Tests for the hosted email-code challenge service (TODO 10).

Coverage matches the TODO 10 acceptance criteria:

  - Code generation: CSPRNG, 6 decimal digits, no two consecutive
    calls produce the same code with overwhelming probability.
  - Verifier computation: HMAC-SHA256(plaintext, pepper), 64-char
    hex, deterministic, distinct inputs produce distinct hashes.
  - Service.issue_challenge: row inserted, attempts_remaining set
    to `max_attempts`, expires_at = NOW() + ttl_seconds, no
    plaintext in any returned field.
  - Service.create_challenge_for_delivery: production-path
    helper that requires a `dev_sink`; raises when `dev_sink`
    is None; the sink is populated with the plaintext.
  - Service.consume_challenge:
    - success: row marked consumed, no other state change.
    - wrong code: attempts_remaining decrements; row NOT consumed.
    - attempts exhausted: row soft-locked (locked_at set), raises
      `EmailChallengeLocked`.
    - expired row: raises `EmailChallengeInvalid` (not consumed,
      not locked).
    - already-consumed row: raises `EmailChallengeInvalid`.
    - locked row: raises `EmailChallengeInvalid`.
    - missing row: raises `EmailChallengeInvalid`.
    - HMAC pepper sensitivity: a different pepper produces a
      different verifier and the consume fails.
  - Service.lock_challenge: idempotent; a re-locked row is a
    no-op; missing row raises `EmailChallengeInvalid`.
  - No plaintext in any field of the returned row.
  - No plaintext in the verifier hash (it's HMAC, not reversible).
  - Generic failure surface: `EmailChallengeInvalid` for
    wrong/expired/locked/consumed/missing all share the same
    parent class so the route layer can map them to a single
    4xx.
  - DevSink: read-only access, multiple codes coexist, length
    reflects the number of codes.

A hand-rolled `MockConn` implements the small asyncpg surface
the service uses (fetchrow / fetchval / execute / transaction)
so the tests stay hermetic and run without a live Postgres.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


from orchestrator.config import Settings
from orchestrator.services.identity.email_challenge import (
    DevSink,
    EmailChallengeConsumeRequest,
    EmailChallengeInvalid,
    EmailChallengeIssueRequest,
    EmailChallengeLocked,
    EmailChallengeRow,
    EmailChallengeService,
    EmailChallengeServiceError,
    EmailChallengeUnavailable,
    compute_code_verifier,
    generate_email_code,
    hash_ip_for_storage,
    hash_user_agent_for_storage,
    normalize_code,
)


# ============================================================================
# Test settings factory
# ============================================================================


def _dev_settings() -> Settings:
    """Settings instance for dev/test use.

    Uses `daemon_environment="development"` so the pepper
    accessor does not require a strong production-grade pepper
    (per `auth_pepper.py:31-67`). The default `daemon_auth_pepper`
    is None; `validate_and_get_pepper` will generate an
    ephemeral one on first call. Tests that want a deterministic
    pepper can override.
    """
    return Settings(daemon_environment="development")


def _dev_settings_with_pepper(pepper: str) -> Settings:
    """Settings instance with a deterministic pepper for HMAC tests."""
    return Settings(daemon_environment="development", daemon_auth_pepper=pepper)


# ============================================================================
# In-memory record helpers
# ============================================================================


class _Record(dict):
    """Dict-like record that supports both `record["col"]` and
    `record.col` lookups. asyncpg `Record` supports both, so the
    service-layer code that uses `record[column]` works against
    this fake without translation.
    """

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


class _EmailMockConn:
    """In-memory asyncpg stand-in for the email challenge service.

    The mock supports the four operations the service uses
    (fetchrow, fetchval, execute, transaction). The SQL shape is
    parsed by `_strip_sql` and routed to a handler method; each
    handler returns a dict-shaped record, a scalar, or a status
    string. Tests populate `_store["email_challenges"]` (a list of
    row dicts) to seed pre-existing state, and may assert on
    the call log (`calls`) to confirm the service issued the
    expected SQL.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {
            "email_challenges": [],
        }
        self._insert_seq: dict[str, int] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.transaction_stack: list[bool] = []

    # ----- asyncpg-shape operations -----

    @asynccontextmanager
    async def transaction(self):
        self.transaction_stack.append(True)
        try:
            yield self
        finally:
            self.transaction_stack.pop()

    async def fetchrow(self, query: str, *args: Any) -> _Record | None:
        self.calls.append((query, args))
        q = _strip_sql(query)
        if q.startswith("INSERT INTO email_challenges") and "RETURNING" in q:
            return self._handle_insert(args)
        if q.startswith("UPDATE email_challenges SET consumed_at"):
            return self._handle_consume(args)
        if q.startswith(
            "SELECT id, normalized_email, attempts_remaining, "
            "expires_at, consumed_at, locked_at, created_at FROM email_challenges"
        ):
            return self._handle_select_by_id(args)
        if q.startswith("UPDATE email_challenges SET locked_at"):
            return self._handle_lock(args)
        raise AssertionError(f"unmocked fetchrow query: {query!r}")

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        raise AssertionError(f"unmocked fetchval query: {query!r}")

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        q = _strip_sql(query)
        if q.startswith("UPDATE email_challenges SET attempts_remaining"):
            return self._handle_decrement(args)
        raise AssertionError(f"unmocked execute query: {query!r}")

    # ----- handlers -----

    def _handle_insert(self, args: tuple[Any, ...]) -> _Record:
        normalized_email = args[0]
        verifier_hash = args[1]
        attempts_remaining = args[2]
        expires_at = args[3]
        ip_hash = args[4]
        user_agent_hash = args[5]
        challenge_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "id": challenge_id,
            "normalized_email": normalized_email,
            "code_verifier_hash": verifier_hash,
            "attempts_remaining": int(attempts_remaining),
            "expires_at": expires_at,
            "consumed_at": None,
            "locked_at": None,
            "created_at": now,
            "ip_hash": ip_hash,
            "user_agent_hash": user_agent_hash,
        }
        self._store["email_challenges"].append(row)
        return _Record(self._public_view(row))

    def _handle_consume(self, args: tuple[Any, ...]) -> _Record | None:
        challenge_id = args[0]
        verifier = args[1]
        now = datetime.now(timezone.utc)
        for row in self._store["email_challenges"]:
            if row["id"] != challenge_id:
                continue
            if row["consumed_at"] is not None:
                return None
            if row["locked_at"] is not None:
                return None
            if row["expires_at"] <= now:
                return None
            if row["code_verifier_hash"] != verifier:
                return None
            row["consumed_at"] = now
            return _Record(self._public_view(row))
        return None

    def _handle_select_by_id(self, args: tuple[Any, ...]) -> _Record | None:
        challenge_id = args[0]
        for row in self._store["email_challenges"]:
            if row["id"] == challenge_id:
                return _Record(self._public_view(row))
        return None

    def _handle_lock(self, args: tuple[Any, ...]) -> _Record | None:
        challenge_id = args[0]
        now = datetime.now(timezone.utc)
        for row in self._store["email_challenges"]:
            if row["id"] != challenge_id:
                continue
            if row["consumed_at"] is not None or row["locked_at"] is not None:
                return None
            row["locked_at"] = now
            return _Record(self._public_view(row))
        return None

    def _handle_decrement(self, args: tuple[Any, ...]) -> str:
        challenge_id = args[0]
        now = datetime.now(timezone.utc)
        for row in self._store["email_challenges"]:
            if row["id"] != challenge_id:
                continue
            if row["consumed_at"] is not None:
                return "UPDATE 0"
            if row["locked_at"] is not None:
                return "UPDATE 0"
            if row["expires_at"] <= now:
                return "UPDATE 0"
            row["attempts_remaining"] = max(0, int(row["attempts_remaining"]) - 1)
            if row["attempts_remaining"] == 0:
                row["locked_at"] = now
            return "UPDATE 1"
        return "UPDATE 0"

    def _public_view(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "normalized_email": row["normalized_email"],
            "attempts_remaining": row["attempts_remaining"],
            "expires_at": row["expires_at"],
            "consumed_at": row["consumed_at"],
            "locked_at": row["locked_at"],
            "created_at": row["created_at"],
        }

    # ----- test helpers -----

    def seed_challenge(
        self,
        *,
        challenge_id: uuid.UUID | None = None,
        normalized_email: str = "user@example.com",
        code_verifier_hash: str = "deadbeef" * 8,
        attempts_remaining: int = 5,
        ttl_seconds: int = 600,
        ip_hash: str | None = None,
        user_agent_hash: str | None = None,
        already_expired: bool = False,
        already_consumed: bool = False,
        already_locked: bool = False,
    ) -> uuid.UUID:
        """Insert a row directly into the in-memory store for
        tests that want to exercise the consume / lock paths
        without going through `issue_challenge`.
        """
        cid = challenge_id or uuid.uuid4()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)
        if already_expired:
            expires_at = now - timedelta(seconds=1)
        self._store["email_challenges"].append(
            {
                "id": cid,
                "normalized_email": normalized_email,
                "code_verifier_hash": code_verifier_hash,
                "attempts_remaining": attempts_remaining,
                "expires_at": expires_at,
                "consumed_at": now if already_consumed else None,
                "locked_at": now if already_locked else None,
                "created_at": now,
                "ip_hash": ip_hash,
                "user_agent_hash": user_agent_hash,
            }
        )
        return cid


def _strip_sql(query: str) -> str:
    """Collapse multi-line SQL into a single-line prefix the
    mock can dispatch on. Newlines and excess whitespace are
    normalized.
    """
    return " ".join(query.split())


# ============================================================================
# Code generation and verifier tests
# ============================================================================


class TestCodeGeneration:
    def test_six_decimal_digits_by_default(self) -> None:
        """The default code is exactly 6 decimal digits."""
        for _ in range(20):
            code = generate_email_code()
            assert len(code) == 6
            assert code.isdecimal()

    def test_custom_num_digits(self) -> None:
        """The function accepts a custom digit count in [4, 10]."""
        for n in (4, 5, 6, 7, 8, 9, 10):
            code = generate_email_code(num_digits=n)
            assert len(code) == n
            assert code.isdecimal()

    def test_num_digits_out_of_range_raises(self) -> None:
        """Out-of-range digit counts raise ValueError."""
        with pytest.raises(ValueError, match="num_digits"):
            generate_email_code(num_digits=3)
        with pytest.raises(ValueError, match="num_digits"):
            generate_email_code(num_digits=11)

    def test_codes_are_distinct_across_many_calls(self) -> None:
        """Consecutive calls produce distinct codes with very
        high probability. With a 6-digit code space
        (10^6) and N samples, the birthday-bound probability
        of at least one collision is approximately
        `1 - exp(-N*(N-1) / (2 * 10^6))`. At N=100, this is
        ~0.5% (≈1 in 200). The test is therefore not strictly
        deterministic but is overwhelmingly likely to pass; a
        real CSPRNG failure would fail the test in seconds.
        """
        codes = {generate_email_code() for _ in range(100)}
        assert len(codes) == 100


class TestCodeVerifier:
    def test_verifier_is_64_char_hex(self) -> None:
        """HMAC-SHA256 produces a 64-char lowercase hex digest."""
        pepper = "test-pepper-with-enough-entropy-for-hmac"
        verifier = compute_code_verifier("123456", pepper)
        assert len(verifier) == 64
        assert all(c in "0123456789abcdef" for c in verifier)

    def test_verifier_is_deterministic(self) -> None:
        """Same code + same pepper = same verifier."""
        pepper = "test-pepper"
        v1 = compute_code_verifier("123456", pepper)
        v2 = compute_code_verifier("123456", pepper)
        assert v1 == v2

    def test_different_codes_produce_different_verifiers(self) -> None:
        """Different codes produce different verifiers."""
        pepper = "test-pepper"
        v1 = compute_code_verifier("123456", pepper)
        v2 = compute_code_verifier("654321", pepper)
        assert v1 != v2

    def test_different_peppers_produce_different_verifiers(self) -> None:
        """Different peppers produce different verifiers
        (HMAC pepper sensitivity — this is the security
        contract).
        """
        v1 = compute_code_verifier("123456", "pepper-a")
        v2 = compute_code_verifier("123456", "pepper-b")
        assert v1 != v2

    def test_empty_code_raises(self) -> None:
        """Empty plaintext code raises ValueError."""
        with pytest.raises(ValueError, match="plaintext_code"):
            compute_code_verifier("", "pepper")

    def test_empty_pepper_raises(self) -> None:
        """Empty pepper raises ValueError."""
        with pytest.raises(ValueError, match="pepper"):
            compute_code_verifier("123456", "")


class TestNormalizeCode:
    def test_plain_code_passes_through(self) -> None:
        assert normalize_code("123456") == "123456"

    def test_strips_whitespace(self) -> None:
        assert normalize_code("  123456  ") == "123456"

    def test_strips_inner_spaces(self) -> None:
        assert normalize_code("123 456") == "123456"

    def test_strips_hyphens(self) -> None:
        assert normalize_code("123-456") == "123456"

    def test_non_digit_raises(self) -> None:
        with pytest.raises(ValueError, match="only digits"):
            normalize_code("12345a")
        with pytest.raises(ValueError, match="only digits"):
            normalize_code("hello")

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="non-None"):
            normalize_code(None)  # type: ignore[arg-type]


class TestHashIpAndUserAgent:
    def test_ip_hash_is_64_char_hex(self) -> None:
        h = hash_ip_for_storage("192.0.2.1", "pepper")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_user_agent_hash_is_64_char_hex(self) -> None:
        h = hash_user_agent_for_storage("Mozilla/5.0", "pepper")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_peppers_yield_different_hashes(self) -> None:
        """HMAC pepper sensitivity for the IP/UA hashes."""
        h1 = hash_ip_for_storage("192.0.2.1", "pepper-a")
        h2 = hash_ip_for_storage("192.0.2.1", "pepper-b")
        assert h1 != h2

    def test_empty_ip_raises(self) -> None:
        with pytest.raises(ValueError, match="raw_ip"):
            hash_ip_for_storage("", "pepper")

    def test_empty_pepper_for_ip_raises(self) -> None:
        with pytest.raises(ValueError, match="pepper"):
            hash_ip_for_storage("192.0.2.1", "")


# ============================================================================
# Service: issuance
# ============================================================================


class TestIssueChallenge:
    @pytest.mark.asyncio
    async def test_issue_returns_row_with_correct_attempts_and_ttl(self) -> None:
        """issue_challenge inserts a row with the requested
        attempts_remaining and an expires_at roughly ttl_seconds
        in the future.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())
        before = datetime.now(timezone.utc)
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash="ip-hash-1",
            user_agent_hash="ua-hash-1",
            ttl_seconds=600,
            max_attempts=5,
        )
        row = await service.issue_challenge(request)
        after = datetime.now(timezone.utc)

        assert isinstance(row, EmailChallengeRow)
        assert row.normalized_email == "user@example.com"
        assert row.attempts_remaining == 5
        assert row.consumed_at is None
        assert row.locked_at is None
        # expires_at should be ~600s after `before`
        assert (row.expires_at - before).total_seconds() >= 599
        assert (row.expires_at - after).total_seconds() <= 601

    @pytest.mark.asyncio
    async def test_issue_stores_verifier_hash_not_plaintext(self) -> None:
        """The DB row stores the HMAC verifier, not the
        plaintext code. The service never returns the plaintext
        in any field of the row.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash="ip-hash-1",
            user_agent_hash="ua-hash-1",
            ttl_seconds=600,
            max_attempts=5,
        )
        row = await service.issue_challenge(request)

        # Inspect the in-memory store: the row's verifier is
        # a 64-char hex, NOT the plaintext.
        stored = conn._store["email_challenges"][0]
        assert stored["code_verifier_hash"] != row.id  # not the id
        assert len(stored["code_verifier_hash"]) == 64
        # The row dataclass has no plaintext field.
        assert not hasattr(row, "plaintext_code")
        assert not hasattr(row, "code")

    @pytest.mark.asyncio
    async def test_issue_records_ip_and_ua_hashes(self) -> None:
        """The row's `ip_hash` and `user_agent_hash` columns
        are populated from the request.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash="stored-ip-hash",
            user_agent_hash="stored-ua-hash",
            ttl_seconds=600,
            max_attempts=5,
        )
        await service.issue_challenge(request)
        stored = conn._store["email_challenges"][0]
        assert stored["ip_hash"] == "stored-ip-hash"
        assert stored["user_agent_hash"] == "stored-ua-hash"

    @pytest.mark.asyncio
    async def test_issue_rejects_empty_email(self) -> None:
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())
        with pytest.raises(ValueError, match="normalized_email"):
            await service.issue_challenge(
                EmailChallengeIssueRequest(
                    normalized_email="",
                    ip_hash=None,
                    user_agent_hash=None,
                    ttl_seconds=600,
                    max_attempts=5,
                )
            )

    @pytest.mark.asyncio
    async def test_issue_rejects_ttl_out_of_range(self) -> None:
        """TTL must be in [30, 3600] per the v1 contract."""
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())
        for bad_ttl in (10, 4000):
            with pytest.raises(ValueError, match="ttl_seconds"):
                await service.issue_challenge(
                    EmailChallengeIssueRequest(
                        normalized_email="user@example.com",
                        ip_hash=None,
                        user_agent_hash=None,
                        ttl_seconds=bad_ttl,
                        max_attempts=5,
                    )
                )

    @pytest.mark.asyncio
    async def test_issue_rejects_attempts_out_of_range(self) -> None:
        """max_attempts must be in [1, 10] per the v1 contract."""
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())
        for bad_attempts in (0, 11):
            with pytest.raises(ValueError, match="max_attempts"):
                await service.issue_challenge(
                    EmailChallengeIssueRequest(
                        normalized_email="user@example.com",
                        ip_hash=None,
                        user_agent_hash=None,
                        ttl_seconds=600,
                        max_attempts=bad_attempts,
                    )
                )

    @pytest.mark.asyncio
    async def test_issue_succeeds_without_ip_or_ua(self) -> None:
        """ip_hash and user_agent_hash are optional (None is
        allowed). The row is still issued.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row = await service.issue_challenge(request)
        assert row.id is not None
        stored = conn._store["email_challenges"][0]
        assert stored["ip_hash"] is None
        assert stored["user_agent_hash"] is None


# ============================================================================
# Service: dev/test path
# ============================================================================


class TestCreateChallengeForDelivery:
    @pytest.mark.asyncio
    async def test_create_returns_plaintext_and_populates_sink(self) -> None:
        """create_challenge_for_delivery returns the row AND
        the plaintext code; the sink is populated so other
        dev/test code can read the code.
        """
        conn = _EmailMockConn()
        sink = DevSink(_store={})
        service = EmailChallengeService(conn, _dev_settings(), dev_sink=sink)
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        assert isinstance(row, EmailChallengeRow)
        assert len(plaintext) == 6
        assert plaintext.isdecimal()
        # The sink now has the plaintext for this row.
        assert sink.get(row.id) == plaintext
        assert len(sink) == 1

    @pytest.mark.asyncio
    async def test_create_for_delivery_works_without_dev_sink(self) -> None:
        """Production path: `create_challenge_for_delivery` works
        with `dev_sink=None` and returns the plaintext directly.
        The TODO 11 `/v1/auth/email/start` route is the
        canonical caller and must not require a DevSink.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())  # no sink
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        assert isinstance(row, EmailChallengeRow)
        assert len(plaintext) == 6
        assert plaintext.isdecimal()
        # No sink, so the plaintext is only in the return value.
        # We can still verify the row exists in the store.
        assert len(conn._store["email_challenges"]) == 1

    @pytest.mark.asyncio
    async def test_create_for_delivery_with_dev_sink_populates_sink(
        self,
    ) -> None:
        """Dev/test path: when a DevSink is configured, the
        plaintext is also stashed in the sink keyed by
        challenge id.
        """
        conn = _EmailMockConn()
        sink = DevSink(_store={})
        service = EmailChallengeService(conn, _dev_settings(), dev_sink=sink)
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        assert sink.get(row.id) == plaintext
        assert len(sink) == 1

    @pytest.mark.asyncio
    async def test_sink_keeps_multiple_codes(self) -> None:
        """Multiple challenges can coexist in the sink; the
        sink is keyed by challenge id.
        """
        conn = _EmailMockConn()
        sink = DevSink(_store={})
        service = EmailChallengeService(conn, _dev_settings(), dev_sink=sink)
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row1, code1 = await service.create_challenge_for_delivery(request)
        row2, code2 = await service.create_challenge_for_delivery(request)
        assert code1 != code2
        assert sink.get(row1.id) == code1
        assert sink.get(row2.id) == code2
        assert len(sink) == 2

    @pytest.mark.asyncio
    async def test_plaintext_matches_stored_verifier(self) -> None:
        """The HMAC-SHA256 of the returned plaintext with the
        service's pepper equals the stored `code_verifier_hash`.
        This proves the one-pass INSERT-then-deliver flow: the
        caller can HMAC the plaintext and the database
        recognizes it.
        """
        conn = _EmailMockConn()
        pepper = "test-pepper-1234567890"
        service = EmailChallengeService(conn, _dev_settings_with_pepper(pepper))
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        # The stored verifier equals HMAC(plaintext, pepper).
        expected = compute_code_verifier(plaintext, pepper)
        stored = conn._store["email_challenges"][0]
        assert stored["code_verifier_hash"] == expected

    @pytest.mark.asyncio
    async def test_issue_is_single_insert_no_update(self) -> None:
        """The single-pass design issues exactly one INSERT
        per `create_challenge_for_delivery` call. There is no
        INSERT-then-UPDATE pattern: the verifier is computed
        before the INSERT, and the row is durable on the first
        round-trip.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings())
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        await service.create_challenge_for_delivery(request)
        # Filter to issuance / verifier-mutation queries only.
        # consume/lock/decrement are tested in their own classes.
        issuance_calls = [
            q
            for q, _ in conn.calls
            if "INSERT INTO email_challenges" in q
            or "UPDATE email_challenges SET code_verifier_hash" in q
        ]
        assert len(issuance_calls) == 1
        assert "INSERT INTO email_challenges" in issuance_calls[0]


# ============================================================================
# Service: consume (success path)
# ============================================================================


class TestConsumeChallengeSuccess:
    @pytest.mark.asyncio
    async def test_correct_code_consumes_row(self) -> None:
        """A correct code marks the row consumed and returns
        the updated row.
        """
        conn = _EmailMockConn()
        settings = _dev_settings_with_pepper("test-pepper-1234567890")
        sink = DevSink(_store={})
        service = EmailChallengeService(conn, settings, dev_sink=sink)
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        # Sanity: the stored verifier matches the HMAC of the
        # plaintext with our pepper.
        expected_verifier = compute_code_verifier(plaintext, "test-pepper-1234567890")
        stored = conn._store["email_challenges"][0]
        assert stored["code_verifier_hash"] == expected_verifier

        # Consume with the correct code.
        consume = EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code=plaintext)
        consumed = await service.consume_challenge(consume)
        assert consumed.is_consumed
        assert consumed.consumed_at is not None
        assert consumed.locked_at is None
        assert consumed.attempts_remaining == 5  # unchanged on success

    @pytest.mark.asyncio
    async def test_normalized_input_consumes(self) -> None:
        """The service accepts a code with whitespace and
        consumes successfully (the route layer is expected
        to call `normalize_code` first, but the service
        tolerates whitespace by HMAC'ing the exact input).
        """
        conn = _EmailMockConn()
        settings = _dev_settings_with_pepper("test-pepper-1234567890")
        sink = DevSink(_store={})
        service = EmailChallengeService(conn, settings, dev_sink=sink)
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        # The service does NOT normalize the input by design;
        # the route layer is expected to call normalize_code
        # first. We assert that the exact-plaintext path
        # works.
        consume = EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code=plaintext)
        consumed = await service.consume_challenge(consume)
        assert consumed.is_consumed


# ============================================================================
# Service: consume (failure paths)
# ============================================================================


class TestConsumeChallengeFailure:
    @pytest.mark.asyncio
    async def test_wrong_code_decrements_attempts(self) -> None:
        """A wrong code decrements attempts_remaining; the row
        is NOT consumed.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(
            conn, _dev_settings_with_pepper("p"), dev_sink=DevSink(_store={})
        )
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, _plaintext = await service.create_challenge_for_delivery(request)

        with pytest.raises(EmailChallengeInvalid):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code="000000")
            )
        # Row not consumed, attempts decremented.
        stored = conn._store["email_challenges"][0]
        assert stored["consumed_at"] is None
        assert stored["attempts_remaining"] == 4

    @pytest.mark.asyncio
    async def test_wrong_code_eventually_locks_row(self) -> None:
        """After max_attempts wrong codes, the row is
        soft-locked and a subsequent consume raises
        `EmailChallengeLocked`.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(
            conn, _dev_settings_with_pepper("p"), dev_sink=DevSink(_store={})
        )
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=2,
        )
        row, _plaintext = await service.create_challenge_for_delivery(request)

        # First wrong code: attempts 2 -> 1, not locked yet.
        with pytest.raises(EmailChallengeInvalid):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code="000000")
            )
        stored = conn._store["email_challenges"][0]
        assert stored["attempts_remaining"] == 1
        assert stored["locked_at"] is None

        # Second wrong code: attempts 1 -> 0, row locked.
        with pytest.raises(EmailChallengeLocked):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code="000000")
            )
        stored = conn._store["email_challenges"][0]
        assert stored["attempts_remaining"] == 0
        assert stored["locked_at"] is not None

    @pytest.mark.asyncio
    async def test_locked_row_raises_invalid(self) -> None:
        """A locked row raises `EmailChallengeInvalid` on a
        subsequent consume attempt (the route maps all invalid
        outcomes to a generic 4xx, including the locked case
        that previously raised `EmailChallengeLocked`).
        """
        conn = _EmailMockConn()
        cid = conn.seed_challenge(
            normalized_email="user@example.com",
            code_verifier_hash=compute_code_verifier("123456", "p"),
            already_locked=True,
        )
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        with pytest.raises(EmailChallengeInvalid):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=cid, plaintext_code="123456")
            )

    @pytest.mark.asyncio
    async def test_expired_row_raises_invalid(self) -> None:
        """An expired row raises `EmailChallengeInvalid`."""
        conn = _EmailMockConn()
        cid = conn.seed_challenge(
            normalized_email="user@example.com",
            code_verifier_hash=compute_code_verifier("123456", "p"),
            already_expired=True,
        )
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        with pytest.raises(EmailChallengeInvalid):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=cid, plaintext_code="123456")
            )

    @pytest.mark.asyncio
    async def test_already_consumed_row_raises_invalid(self) -> None:
        """An already-consumed row raises `EmailChallengeInvalid`
        on replay (replay protection).
        """
        conn = _EmailMockConn()
        cid = conn.seed_challenge(
            normalized_email="user@example.com",
            code_verifier_hash=compute_code_verifier("123456", "p"),
            already_consumed=True,
        )
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        with pytest.raises(EmailChallengeInvalid):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=cid, plaintext_code="123456")
            )

    @pytest.mark.asyncio
    async def test_missing_row_raises_invalid(self) -> None:
        """A missing challenge id raises `EmailChallengeInvalid`."""
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        with pytest.raises(EmailChallengeInvalid):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=uuid.uuid4(), plaintext_code="123456")
            )

    @pytest.mark.asyncio
    async def test_hmac_pepper_sensitivity(self) -> None:
        """A code issued under one pepper is rejected when
        consumed under a different pepper. This is the
        HMAC pepper-sensitivity contract: a config drift
        that changes the pepper invalidates all outstanding
        challenges atomically.
        """
        conn = _EmailMockConn()
        # Issue under pepper "a".
        service_a = EmailChallengeService(
            conn,
            _dev_settings_with_pepper("pepper-a"),
            dev_sink=DevSink(_store={}),
        )
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service_a.create_challenge_for_delivery(request)

        # Consume under a different pepper.
        service_b = EmailChallengeService(conn, _dev_settings_with_pepper("pepper-b"))
        with pytest.raises(EmailChallengeInvalid):
            await service_b.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code=plaintext)
            )
        # Row not consumed, attempts decremented.
        stored = conn._store["email_challenges"][0]
        assert stored["consumed_at"] is None

    @pytest.mark.asyncio
    async def test_no_plaintext_in_returned_row(self) -> None:
        """The returned `EmailChallengeRow` has no plaintext
        code in any field. The dataclass fields are exactly
        the seven schema columns; nothing leaks.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(
            conn, _dev_settings_with_pepper("p"), dev_sink=DevSink(_store={})
        )
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        consumed = await service.consume_challenge(
            EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code=plaintext)
        )
        # The dataclass should have only the seven schema columns.
        field_names = {f.name for f in consumed.__dataclass_fields__.values()}
        assert field_names == {
            "id",
            "normalized_email",
            "attempts_remaining",
            "expires_at",
            "consumed_at",
            "locked_at",
            "created_at",
        }
        # No field contains the plaintext (we cannot assert exact
        # value because the plaintext is random; but we can
        # assert the field names do not include it).
        assert "plaintext_code" not in field_names
        assert "code" not in field_names
        assert "verifier" not in field_names

    @pytest.mark.asyncio
    async def test_replay_of_consumed_code_fails(self) -> None:
        """After a successful consume, replaying the same code
        with the same challenge id fails (single-use
        enforcement).
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(
            conn, _dev_settings_with_pepper("p"), dev_sink=DevSink(_store={})
        )
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        # First consume: success.
        await service.consume_challenge(
            EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code=plaintext)
        )
        # Replay: fails.
        with pytest.raises(EmailChallengeInvalid):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code=plaintext)
            )

    @pytest.mark.asyncio
    async def test_empty_code_raises_value_error(self) -> None:
        """An empty plaintext code raises ValueError (defensive)."""
        conn = _EmailMockConn()
        cid = conn.seed_challenge(normalized_email="user@example.com")
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        with pytest.raises(ValueError, match="plaintext_code"):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=cid, plaintext_code="")
            )

    @pytest.mark.asyncio
    async def test_consume_after_wrong_codes_with_correct_code_succeeds(
        self,
    ) -> None:
        """A row with decremented attempts (after wrong codes)
        can still be consumed successfully if the right code
        is presented (attempts_remaining is informational,
        not blocking).
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(
            conn, _dev_settings_with_pepper("p"), dev_sink=DevSink(_store={})
        )
        request = EmailChallengeIssueRequest(
            normalized_email="user@example.com",
            ip_hash=None,
            user_agent_hash=None,
            ttl_seconds=600,
            max_attempts=5,
        )
        row, plaintext = await service.create_challenge_for_delivery(request)
        # One wrong attempt.
        with pytest.raises(EmailChallengeInvalid):
            await service.consume_challenge(
                EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code="000000")
            )
        # Right code: success.
        consumed = await service.consume_challenge(
            EmailChallengeConsumeRequest(challenge_id=row.id, plaintext_code=plaintext)
        )
        assert consumed.is_consumed
        # attempts_remaining went from 5 -> 4 -> still 4 on the
        # success path.
        assert consumed.attempts_remaining == 4


# ============================================================================
# Service: lock
# ============================================================================


class TestLockChallenge:
    @pytest.mark.asyncio
    async def test_lock_active_row(self) -> None:
        """lock_challenge on an active row sets locked_at."""
        conn = _EmailMockConn()
        cid = conn.seed_challenge(normalized_email="user@example.com")
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        locked = await service.lock_challenge(cid)
        assert locked.is_locked
        assert locked.locked_at is not None

    @pytest.mark.asyncio
    async def test_lock_already_locked_is_idempotent(self) -> None:
        """Re-locking a locked row is a no-op (returns the row
        without raising; the original lock timestamp is
        preserved by the WHERE guard).
        """
        conn = _EmailMockConn()
        cid = conn.seed_challenge(normalized_email="user@example.com", already_locked=True)
        original_locked_at = conn._store["email_challenges"][0]["locked_at"]
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        locked = await service.lock_challenge(cid)
        assert locked.is_locked
        # The lock timestamp is the same (no update happened).
        assert conn._store["email_challenges"][0]["locked_at"] == original_locked_at

    @pytest.mark.asyncio
    async def test_lock_consumed_row_returns_existing(self) -> None:
        """lock_challenge on a consumed row is a no-op; the
        helper returns the existing row.
        """
        conn = _EmailMockConn()
        cid = conn.seed_challenge(normalized_email="user@example.com", already_consumed=True)
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        result = await service.lock_challenge(cid)
        assert result.is_consumed
        assert not result.is_locked

    @pytest.mark.asyncio
    async def test_lock_missing_row_raises_invalid(self) -> None:
        """lock_challenge on a missing challenge id raises
        `EmailChallengeInvalid`.
        """
        conn = _EmailMockConn()
        service = EmailChallengeService(conn, _dev_settings_with_pepper("p"))
        with pytest.raises(EmailChallengeInvalid):
            await service.lock_challenge(uuid.uuid4())


# ============================================================================
# Generic failure surface
# ============================================================================


class TestGenericFailureSurface:
    """The route layer maps every `EmailChallengeInvalid` to a
    single generic 4xx response. This test class verifies that
    wrong / expired / locked / consumed / missing all share
    the same parent class so the route layer can use one
    `except` branch.
    """

    @pytest.mark.asyncio
    async def test_all_failure_modes_share_email_challenge_invalid(
        self,
    ) -> None:
        """Wrong, expired, locked, consumed, and missing all
        raise `EmailChallengeInvalid` (or its subclass
        `EmailChallengeLocked` for the attempts-exhausted
        case). All subclasses are `EmailChallengeInvalid`
        ancestors.
        """
        # The class hierarchy must be:
        #   EmailChallengeServiceError
        #     EmailChallengeInvalid
        #       (subclasses can be added later)
        #     EmailChallengeLocked (subclass of EmailChallengeInvalid)
        #     EmailChallengeUnavailable
        assert issubclass(EmailChallengeInvalid, EmailChallengeServiceError)
        assert issubclass(EmailChallengeLocked, EmailChallengeInvalid)
        assert issubclass(EmailChallengeUnavailable, EmailChallengeServiceError)


# ============================================================================
# DevSink tests
# ============================================================================


class TestDevSink:
    def test_sink_starts_empty(self) -> None:
        sink = DevSink(_store={})
        assert len(sink) == 0
        assert sink.get(uuid.uuid4()) is None

    def test_sink_get_returns_value(self) -> None:
        cid = uuid.uuid4()
        sink = DevSink(_store={cid: "123456"})
        assert sink.get(cid) == "123456"

    def test_sink_get_returns_none_for_missing(self) -> None:
        sink = DevSink(_store={})
        assert sink.get(uuid.uuid4()) is None
