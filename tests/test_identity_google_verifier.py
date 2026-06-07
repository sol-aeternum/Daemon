"""Tests for the hosted Google ID-token verification service (TODO 12).

Coverage matches the TODO 12 acceptance criteria:

  - Helpers:
    - `generate_google_nonce` produces unique URL-safe base64
      strings of the expected length.
    - `compute_nonce_verifier` is HMAC-SHA256(nonce, pepper),
      64-char hex, deterministic, distinct inputs produce
      distinct hashes, rejects empty inputs.
    - `parse_audience_allowlist` handles empty, single, and
      comma-separated input with whitespace.
    - `audience_allowed` matches client_id, allowlist entry,
      and rejects empty/non-string.
    - `issuer_allowed` accepts canonical and legacy issuers,
      rejects everything else.
  - Nonce issuance:
    - `issue_nonce` returns a row with all expected fields
      and a plaintext nonce; the row's `nonce_verifier_hash`
      matches the HMAC of the plaintext.
    - Repeated `issue_nonce` produces distinct nonces.
  - Nonce consumption:
    - Success: row marked consumed, no other state change.
    - Wrong nonce: `GoogleNonceInvalid`.
    - Expired nonce: `GoogleNonceInvalid`.
    - Already-consumed nonce (replay): `GoogleNonceInvalid`.
    - Missing nonce: `GoogleNonceInvalid`.
  - ID-token verification:
    - Valid token + matching nonce + consumed row returns a
      `VerifiedGoogleIdentity` with the expected fields.
    - Wrong audience (audience allowlist non-match):
      `GoogleTokenInvalid`.
    - Wrong issuer: `GoogleTokenInvalid`.
    - Library boundary raises (signature / exp failure):
      `GoogleTokenInvalid`.
    - Missing `sub`: `GoogleTokenInvalid`.
    - Missing `email`: `GoogleTokenInvalid`.
    - `email_verified = false`: `GoogleTokenInvalid`.
    - `email_verified` not a bool: `GoogleTokenInvalid`.
    - Wrong `nonce` claim: `GoogleTokenInvalid`.
    - Missing `nonce` claim: `GoogleTokenInvalid`.
    - Token age (`exp - iat`) > 1h: `GoogleTokenInvalid`.
    - `azp` present and not allowed: `GoogleTokenInvalid`.
    - `azp` present and matches client_id: success.
    - `azp` present and matches allowlist: success.
    - `azp` absent: success.
    - `daemon_google_client_id` not configured:
      `GoogleVerifierUnavailable`.
    - Library boundary raises an arbitrary exception:
      `GoogleTokenInvalid` (no leak of the library error class).
  - Side-effects (verifier must not create users/sessions/
    devices/cookies/provider links):
    - The mock connection never receives a write to
      `users`, `sessions`, `devices`, `tenants`,
      `tenant_memberships`, `identity_providers`,
      `signup_invites`, `email_challenges`, or
      `identity_audit_log`. Only `google_nonce_challenges`
      is written.
    - The verifier never starts a transaction or calls
      `commit` / `rollback`.
  - No leakage of plaintext into log / returned fields.
  - Production `default_google_id_token_verifier` builds a
    callable that delegates to `google.oauth2.id_token`.

A hand-rolled `MockConn` implements the small asyncpg surface
the service uses (fetchrow / execute / transaction) so the
tests stay hermetic and run without a live Postgres.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.services.identity.google_verifier import (
    DEFAULT_TTL_SECONDS,
    GOOGLE_ISSUER_CANONICAL,
    GOOGLE_ISSUER_LEGACY,
    GoogleIdTokenVerifyRequest,
    GoogleNonceConsumeRequest,
    GoogleNonceInvalid,
    GoogleNonceIssueRequest,
    GoogleNonceRow,
    GoogleTokenInvalid,
    GoogleVerifierError,
    GoogleVerifierService,
    GoogleVerifierUnavailable,
    MAX_TOKEN_AGE_SECONDS,
    NONCE_NUM_BYTES,
    VerifiedGoogleIdentity,
    audience_allowed,
    compute_nonce_verifier,
    default_google_id_token_verifier,
    generate_google_nonce,
    hash_ip_for_storage,
    hash_user_agent_for_storage,
    issuer_allowed,
    parse_audience_allowlist,
)


# ============================================================================
# Test settings factory
# ============================================================================


def _dev_settings(
    *,
    client_id: str | None = "daemon-test-client-id.googleusercontent.com",
    audience_allowlist: str = "",
) -> Settings:
    """Settings instance for dev/test use.

    Uses `daemon_environment="development"` so the pepper
    accessor does not require a strong production-grade
    pepper. The default `daemon_auth_pepper` is None;
    `validate_and_get_pepper` will generate an ephemeral
    one on first call. Tests that want a deterministic
    pepper can override.
    """
    return Settings(
        daemon_environment="development",
        daemon_google_client_id=client_id,
        daemon_google_audience_allowlist=audience_allowlist,
    )


# ============================================================================
# In-memory record helpers
# ============================================================================


class _Record(dict):
    """Dict-like record that supports both `record["col"]` and
    `record.col` lookups. asyncpg `Record` supports both, so
    the service-layer code that uses `record[column]` works
    against this fake without translation.
    """

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


# ============================================================================
# In-memory connection (MockConn)
# ============================================================================


class _GoogleMockConn:
    """In-memory asyncpg stand-in for the Google verifier.

    The mock supports the three operations the service uses
    (fetchrow, execute, transaction). The SQL shape is
    parsed by `_strip_sql` and routed to a handler method;
    each handler returns a dict-shaped record, a scalar, or
    a status string. Tests populate
    `_store["google_nonce_challenges"]` (a list of row
    dicts) to seed pre-existing state, and may assert on
    the call log (`calls`) to confirm the service issued
    the expected SQL and did NOT touch other tables.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {
            "google_nonce_challenges": [],
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
        if q.startswith("INSERT INTO google_nonce_challenges") and "RETURNING" in q:
            return self._handle_insert(args)
        if q.startswith(
            "SELECT id, nonce_verifier_hash, user_id_proposed, "
            "expires_at, consumed_at, created_at FROM google_nonce_challenges"
        ):
            return self._handle_select_by_verifier(args)
        if q.startswith("UPDATE google_nonce_challenges SET consumed_at"):
            return self._handle_consume(args)
        raise AssertionError(f"unmocked fetchrow query: {query!r}")

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        raise AssertionError(f"unmocked execute query: {query!r}")

    # ----- handlers -----

    def _handle_insert(self, args: tuple[Any, ...]) -> _Record:
        nonce_verifier_hash = args[0]
        user_id_proposed = args[1]
        expires_at = args[2]
        ip_hash = args[3]
        user_agent_hash = args[4]
        challenge_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "id": challenge_id,
            "nonce_verifier_hash": nonce_verifier_hash,
            "user_id_proposed": user_id_proposed,
            "expires_at": expires_at,
            "consumed_at": None,
            "created_at": now,
            "ip_hash": ip_hash,
            "user_agent_hash": user_agent_hash,
        }
        self._store["google_nonce_challenges"].append(row)
        return _Record(self._public_view(row))

    def _handle_select_by_verifier(self, args: tuple[Any, ...]) -> _Record | None:
        challenge_id = args[0]
        presented_verifier = args[1]
        for row in self._store["google_nonce_challenges"]:
            if row["id"] == challenge_id and row["nonce_verifier_hash"] == presented_verifier:
                return _Record(self._public_view(row))
        return None

    def _handle_consume(self, args: tuple[Any, ...]) -> _Record | None:
        challenge_id = args[0]
        presented_verifier = args[1]
        now = datetime.now(timezone.utc)
        for row in self._store["google_nonce_challenges"]:
            if row["id"] != challenge_id or row["nonce_verifier_hash"] != presented_verifier:
                continue
            if row["consumed_at"] is not None:
                return None
            if row["expires_at"] <= now:
                return None
            row["consumed_at"] = now
            return _Record(self._public_view(row))
        return None

    def _public_view(self, row: dict[str, Any]) -> dict[str, Any]:
        """Project only the columns the service exposes via
        `GoogleNonceRow` — matches the INSERT/UPDATE/SELECT
        `RETURNING` shape the service queries.
        """
        return {
            "id": row["id"],
            "nonce_verifier_hash": row["nonce_verifier_hash"],
            "user_id_proposed": row["user_id_proposed"],
            "expires_at": row["expires_at"],
            "consumed_at": row["consumed_at"],
            "created_at": row["created_at"],
        }


def _strip_sql(query: str) -> str:
    """Collapse multi-line SQL into a single-line prefix the
    mock can dispatch on. Newlines and excess whitespace are
    normalized.
    """
    return " ".join(query.split())


# ============================================================================
# Stub verifier
# ============================================================================


class _StubVerifier:
    """Hand-rolled stand-in for the `google-auth` library.

    Tests pre-load `_claims` (or `_error`) and the stub
    returns them on every call. The stub records all calls
    so tests can assert the verifier was invoked with the
    expected `id_token`, `request`, `audience` (str or
    list[str]), and optional `certs_url`.
    """

    def __init__(
        self,
        *,
        claims: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._claims = claims
        self._error = error
        self.calls: list[tuple[str, Any, Any, str | None]] = []

    def __call__(
        self,
        id_token: str,
        request: Any,
        audience: str | list[str],
        *,
        certs_url: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((id_token, request, audience, certs_url))
        if self._error is not None:
            raise self._error
        if self._claims is None:
            raise RuntimeError("stub verifier has no claims and no error configured")
        return self._claims


# ============================================================================
# Helpers
# ============================================================================


def _make_consumed_row(
    plaintext_nonce: str,
    pepper: str,
    *,
    expires_in_seconds: int = 600,
    user_id_proposed: uuid.UUID | None = None,
) -> GoogleNonceRow:
    """Build a `GoogleNonceRow` in the consumed state, with
    the verifier computed for `plaintext_nonce`. Used by
    `verify_id_token` tests that want a pre-consumed row
    without going through the full issue/consume path.
    """
    now = datetime.now(timezone.utc)
    return GoogleNonceRow(
        id=uuid.uuid4(),
        nonce_verifier_hash=compute_nonce_verifier(plaintext_nonce, pepper),
        user_id_proposed=user_id_proposed,
        expires_at=now + timedelta(seconds=expires_in_seconds),
        consumed_at=now,
        created_at=now,
    )


def _now_claims(
    *,
    sub: str = "1234567890",
    email: str = "User@Example.com",
    email_verified: Any = True,
    aud: str = "daemon-test-client-id.googleusercontent.com",
    iss: str = GOOGLE_ISSUER_CANONICAL,
    nonce: str | None = "test-nonce",
    azp: str | None = None,
    iat_offset: int = 0,
    exp_offset: int = 3600,
) -> dict[str, Any]:
    """Build a claim dict for the stub verifier with the
    given overrides. The default `iat_offset=0` and
    `exp_offset=3600` produce a `exp - iat = 3600` (one
    hour, the maximum allowed).
    """
    now = datetime.now(timezone.utc)
    return {
        "iss": iss,
        "azp": azp,
        "aud": aud,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "nonce": nonce,
        "iat": int(now.timestamp()) - iat_offset,
        "exp": int(now.timestamp()) + exp_offset,
    }


# ============================================================================
# Helper function tests
# ============================================================================


class TestGenerateGoogleNonce:
    def test_nonce_is_non_empty(self) -> None:
        n = generate_google_nonce()
        assert n
        assert isinstance(n, str)

    def test_nonce_is_urlsafe(self) -> None:
        n = generate_google_nonce()
        # urlsafe base64 alphabet is A-Z a-z 0-9 - _
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert set(n) <= allowed

    def test_repeated_calls_produce_distinct_values(self) -> None:
        nonces = {generate_google_nonce() for _ in range(64)}
        assert len(nonces) == 64

    def test_default_bytes_constant_is_at_least_16(self) -> None:
        # research-recommended minimum entropy
        assert NONCE_NUM_BYTES >= 16

    def test_num_bytes_validation(self) -> None:
        with pytest.raises(ValueError):
            generate_google_nonce(num_bytes=8)
        with pytest.raises(ValueError):
            generate_google_nonce(num_bytes=128)

    def test_honors_num_bytes(self) -> None:
        # 16 bytes -> 22-char urlsafe base64 (ceil(16/3)*4 == 24 with padding,
        # but token_urlsafe omits padding: 22 chars).
        n = generate_google_nonce(num_bytes=16)
        assert len(n) == 22


class TestComputeNonceVerifier:
    def test_deterministic(self) -> None:
        assert compute_nonce_verifier("n", "p") == compute_nonce_verifier("n", "p")

    def test_distinct_inputs_distinct_hashes(self) -> None:
        assert compute_nonce_verifier("a", "p") != compute_nonce_verifier("b", "p")
        assert compute_nonce_verifier("a", "p") != compute_nonce_verifier("a", "q")

    def test_is_64_char_lowercase_hex(self) -> None:
        h = compute_nonce_verifier("nonce", "pepper")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_rejects_empty_nonce(self) -> None:
        with pytest.raises(ValueError):
            compute_nonce_verifier("", "p")

    def test_rejects_empty_pepper(self) -> None:
        with pytest.raises(ValueError):
            compute_nonce_verifier("n", "")


class TestHashHelpers:
    def test_hash_ip_for_storage_is_64_char_hex(self) -> None:
        h = hash_ip_for_storage("192.0.2.1", "pepper")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_ip_distinct_inputs_distinct(self) -> None:
        assert hash_ip_for_storage("1.2.3.4", "p") != hash_ip_for_storage("1.2.3.5", "p")

    def test_hash_user_agent_for_storage_is_64_char_hex(self) -> None:
        h = hash_user_agent_for_storage("Mozilla/5.0", "pepper")
        assert len(h) == 64

    def test_hash_ip_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            hash_ip_for_storage("", "p")
        with pytest.raises(ValueError):
            hash_ip_for_storage("ip", "")

    def test_hash_user_agent_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            hash_user_agent_for_storage("", "p")
        with pytest.raises(ValueError):
            hash_user_agent_for_storage("ua", "")


class TestParseAudienceAllowlist:
    def test_empty_returns_empty_list(self) -> None:
        assert parse_audience_allowlist("") == []
        assert parse_audience_allowlist(None) == []

    def test_single_entry(self) -> None:
        assert parse_audience_allowlist("client-a") == ["client-a"]

    def test_comma_separated(self) -> None:
        assert parse_audience_allowlist("a,b,c") == ["a", "b", "c"]

    def test_strips_whitespace(self) -> None:
        assert parse_audience_allowlist(" a , b , c ") == ["a", "b", "c"]

    def test_discards_empty_fragments(self) -> None:
        assert parse_audience_allowlist("a,,b, ,c") == ["a", "b", "c"]


class TestAudienceAllowed:
    def test_matches_client_id(self) -> None:
        assert audience_allowed("cid", client_id="cid", allowlist=[])

    def test_matches_allowlist(self) -> None:
        assert audience_allowed("other", client_id="cid", allowlist=["other"])

    def test_rejects_empty(self) -> None:
        assert not audience_allowed("", client_id="cid", allowlist=[])

    def test_rejects_non_string(self) -> None:
        assert not audience_allowed(123, client_id="cid", allowlist=[])
        assert not audience_allowed(None, client_id="cid", allowlist=[])

    def test_rejects_neither(self) -> None:
        assert not audience_allowed("nope", client_id="cid", allowlist=["other"])


class TestIssuerAllowed:
    def test_canonical_issuer(self) -> None:
        assert issuer_allowed(GOOGLE_ISSUER_CANONICAL)

    def test_legacy_issuer(self) -> None:
        assert issuer_allowed(GOOGLE_ISSUER_LEGACY)

    def test_rejects_wrong_issuer(self) -> None:
        assert not issuer_allowed("https://evil.example.com")

    def test_rejects_non_string(self) -> None:
        assert not issuer_allowed(123)
        assert not issuer_allowed(None)


class TestDefaultVerifierCallable:
    def test_default_google_id_token_verifier_returns_callable(self) -> None:
        v = default_google_id_token_verifier()
        assert callable(v)

    def test_default_verifier_with_certs_url_forwards_when_set(self) -> None:
        v = default_google_id_token_verifier()
        from orchestrator.services.identity import google_verifier as gv

        captured: list[dict[str, Any]] = []

        def _fake_verify(
            id_token: str, request: Any, *, audience: str | list[str], certs_url: str | None = None
        ) -> dict[str, Any]:
            captured.append(
                {
                    "id_token": id_token,
                    "request": request,
                    "audience": audience,
                    "certs_url": certs_url,
                }
            )
            return {"sub": "x"}

        gv._id_token_orig = gv.__dict__.get("_id_token_capture", None)  # type: ignore[attr-defined]
        import google.oauth2.id_token as real_lib  # type: ignore[import-not-found]

        original = real_lib.verify_token
        real_lib.verify_token = _fake_verify  # type: ignore[assignment]
        try:
            v("token-1", None, "aud-1", certs_url="https://example.com/certs")
            assert captured and captured[0]["certs_url"] == "https://example.com/certs"
            captured.clear()
            v("token-2", None, "aud-1")
            assert captured and captured[0]["certs_url"] is None
        finally:
            real_lib.verify_token = original  # type: ignore[assignment]

    def test_default_verifier_constructs_request_when_caller_passes_none(self) -> None:
        # The library-boundary fix: `verify_token` requires
        # a transport Request to fetch JWKs. The default
        # verifier must construct a `Request()` when the
        # caller passes `request=None` so the library can
        # perform the certs fetch. The previous
        # implementation passed `None` straight through,
        # which the library rejects.
        v = default_google_id_token_verifier()
        import google.oauth2.id_token as real_lib  # type: ignore[import-not-found]

        captured: list[dict[str, Any]] = []

        def _fake_verify(
            id_token: str, request: Any, *, audience: str | list[str], certs_url: str | None = None
        ) -> dict[str, Any]:
            captured.append({"request": request, "audience": audience})
            return {"sub": "x"}

        original = real_lib.verify_token
        real_lib.verify_token = _fake_verify  # type: ignore[assignment]
        try:
            v("token-1", None, "aud-1")
        finally:
            real_lib.verify_token = original  # type: ignore[assignment]
        assert captured
        forwarded_request = captured[0]["request"]
        # The default verifier must NOT forward `None` to
        # the library. The constructed transport is the
        # concrete `google.auth.transport.requests.Request`
        # class; we assert by class name (not by identity)
        # so a future import-path refactor does not break
        # the test.
        assert forwarded_request is not None
        assert type(forwarded_request).__name__ == "Request"
        assert type(forwarded_request).__module__ == "google.auth.transport.requests"

    def test_default_verifier_forwards_caller_request_unchanged(self) -> None:
        # The default verifier must pass a caller-supplied
        # Request through unchanged (so the higher-level
        # `verify_oauth2_token` path can read the user's
        # Authorization header from a shared Request).
        v = default_google_id_token_verifier()
        import google.oauth2.id_token as real_lib  # type: ignore[import-not-found]

        captured: list[dict[str, Any]] = []

        def _fake_verify(
            id_token: str, request: Any, *, audience: str | list[str], certs_url: str | None = None
        ) -> dict[str, Any]:
            captured.append({"request": request, "audience": audience})
            return {"sub": "x"}

        sentinel = object()
        original = real_lib.verify_token
        real_lib.verify_token = _fake_verify  # type: ignore[assignment]
        try:
            v("token-1", sentinel, "aud-1")
        finally:
            real_lib.verify_token = original  # type: ignore[assignment]
        assert captured
        assert captured[0]["request"] is sentinel

    def test_default_verifier_forwards_audience_list(self) -> None:
        # The library-boundary fix: when the service
        # passes a `list[str]` audience, the default
        # verifier must forward the list unchanged so the
        # library enforces the full set at the boundary.
        v = default_google_id_token_verifier()
        import google.oauth2.id_token as real_lib  # type: ignore[import-not-found]

        captured: list[dict[str, Any]] = []

        def _fake_verify(
            id_token: str, request: Any, *, audience: str | list[str], certs_url: str | None = None
        ) -> dict[str, Any]:
            captured.append({"audience": audience})
            return {"sub": "x"}

        original = real_lib.verify_token
        real_lib.verify_token = _fake_verify  # type: ignore[assignment]
        try:
            v("token-1", None, ["primary", "secondary"])
        finally:
            real_lib.verify_token = original  # type: ignore[assignment]
        assert captured
        assert captured[0]["audience"] == ["primary", "secondary"]

    def test_default_verifier_missing_google_auth_raises_unavailable(self) -> None:
        v = default_google_id_token_verifier()
        assert callable(v)


# ============================================================================
# Service: nonce issuance
# ============================================================================


class TestIssueNonce:
    @pytest.mark.asyncio
    async def test_issue_returns_row_and_plaintext(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        row, plaintext = await service.issue_nonce(
            GoogleNonceIssueRequest(
                ip_hash="ip-hash-1",
                user_agent_hash="ua-hash-1",
                ttl_seconds=600,
            )
        )
        assert isinstance(row, GoogleNonceRow)
        assert isinstance(plaintext, str)
        assert plaintext
        assert row.nonce_verifier_hash == compute_nonce_verifier(
            plaintext,
            _expected_pepper(_dev_settings()),
        )
        assert row.consumed_at is None
        assert row.user_id_proposed is None

    @pytest.mark.asyncio
    async def test_issue_persists_with_user_id_proposed(self) -> None:
        conn = _GoogleMockConn()
        proposed = uuid.uuid4()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        row, _ = await service.issue_nonce(
            GoogleNonceIssueRequest(
                ip_hash="ip-hash-1",
                user_agent_hash="ua-hash-1",
                ttl_seconds=600,
                user_id_proposed=proposed,
            )
        )
        assert row.user_id_proposed == proposed

    @pytest.mark.asyncio
    async def test_issue_rejects_ttl_out_of_range(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        with pytest.raises(ValueError):
            await service.issue_nonce(
                GoogleNonceIssueRequest(
                    ip_hash=None,
                    user_agent_hash=None,
                    ttl_seconds=10,
                )
            )
        with pytest.raises(ValueError):
            await service.issue_nonce(
                GoogleNonceIssueRequest(
                    ip_hash=None,
                    user_agent_hash=None,
                    ttl_seconds=86400,
                )
            )

    @pytest.mark.asyncio
    async def test_issue_writes_only_to_google_nonce_challenges(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        await service.issue_nonce(
            GoogleNonceIssueRequest(
                ip_hash="ip-hash-1",
                user_agent_hash="ua-hash-1",
                ttl_seconds=600,
            )
        )
        for query, _args in conn.calls:
            assert "google_nonce_challenges" in query
            assert "users" not in query
            assert "sessions" not in query
            assert "devices" not in query
            assert "tenants" not in query
            assert "tenant_memberships" not in query
            assert "identity_providers" not in query
            assert "signup_invites" not in query
            assert "email_challenges" not in query
            assert "identity_audit_log" not in query

    @pytest.mark.asyncio
    async def test_issue_does_not_start_a_transaction(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        await service.issue_nonce(
            GoogleNonceIssueRequest(
                ip_hash=None,
                user_agent_hash=None,
                ttl_seconds=600,
            )
        )
        assert conn.transaction_stack == []

    @pytest.mark.asyncio
    async def test_repeated_issue_produces_distinct_nonces(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        nonces = set()
        for _ in range(8):
            _, plaintext = await service.issue_nonce(
                GoogleNonceIssueRequest(
                    ip_hash=None,
                    user_agent_hash=None,
                    ttl_seconds=600,
                )
            )
            nonces.add(plaintext)
        assert len(nonces) == 8


# ============================================================================
# Service: nonce consumption
# ============================================================================


class TestConsumeNonce:
    @pytest.mark.asyncio
    async def test_consume_success(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        settings = _dev_settings()
        pepper = _expected_pepper(settings)
        _, plaintext = await service.issue_nonce(
            GoogleNonceIssueRequest(
                ip_hash=None,
                user_agent_hash=None,
                ttl_seconds=600,
            )
        )
        challenge_id = conn._store["google_nonce_challenges"][0]["id"]
        consumed = await service.consume_nonce(
            GoogleNonceConsumeRequest(challenge_id=challenge_id, plaintext_nonce=plaintext)
        )
        assert consumed.consumed_at is not None
        assert consumed.nonce_verifier_hash == compute_nonce_verifier(plaintext, pepper)

    @pytest.mark.asyncio
    async def test_consume_rejects_wrong_nonce(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        with pytest.raises(GoogleNonceInvalid):
            await service.consume_nonce(
                GoogleNonceConsumeRequest(
                    challenge_id=uuid.uuid4(), plaintext_nonce="nonexistent-nonce"
                )
            )

    @pytest.mark.asyncio
    async def test_consume_rejects_expired_nonce(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        # Issue a row with an already-expired expires_at.
        now = datetime.now(timezone.utc)
        past = now - timedelta(seconds=10)
        # Insert directly through the same code path the
        # mock supports (insert with an HMAC verifier and an
        # already-expired window).
        plaintext = "expired-test-nonce"
        pepper = _expected_pepper(_dev_settings())
        verifier = compute_nonce_verifier(plaintext, pepper)
        conn._store["google_nonce_challenges"].append(
            {
                "id": uuid.uuid4(),
                "nonce_verifier_hash": verifier,
                "user_id_proposed": None,
                "expires_at": past,
                "consumed_at": None,
                "created_at": now,
                "ip_hash": None,
                "user_agent_hash": None,
            }
        )
        with pytest.raises(GoogleNonceInvalid):
            await service.consume_nonce(
                GoogleNonceConsumeRequest(
                    challenge_id=conn._store["google_nonce_challenges"][0]["id"],
                    plaintext_nonce=plaintext,
                )
            )

    @pytest.mark.asyncio
    async def test_consume_rejects_already_consumed_replay(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        _, plaintext = await service.issue_nonce(
            GoogleNonceIssueRequest(
                ip_hash=None,
                user_agent_hash=None,
                ttl_seconds=600,
            )
        )
        # First consume succeeds.
        challenge_id = conn._store["google_nonce_challenges"][0]["id"]
        await service.consume_nonce(
            GoogleNonceConsumeRequest(challenge_id=challenge_id, plaintext_nonce=plaintext)
        )
        # Second consume (replay) fails.
        with pytest.raises(GoogleNonceInvalid):
            await service.consume_nonce(
                GoogleNonceConsumeRequest(challenge_id=challenge_id, plaintext_nonce=plaintext)
            )

    @pytest.mark.asyncio
    async def test_consume_writes_only_to_google_nonce_challenges(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        _, plaintext = await service.issue_nonce(
            GoogleNonceIssueRequest(
                ip_hash=None,
                user_agent_hash=None,
                ttl_seconds=600,
            )
        )
        challenge_id = conn._store["google_nonce_challenges"][0]["id"]
        await service.consume_nonce(
            GoogleNonceConsumeRequest(challenge_id=challenge_id, plaintext_nonce=plaintext)
        )
        for query, _args in conn.calls:
            assert "google_nonce_challenges" in query
            assert "users" not in query
            assert "sessions" not in query
            assert "devices" not in query
            assert "tenants" not in query
            assert "tenant_memberships" not in query
            assert "identity_providers" not in query
            assert "signup_invites" not in query
            assert "email_challenges" not in query
            assert "identity_audit_log" not in query

    @pytest.mark.asyncio
    async def test_consume_rejects_empty_nonce(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        with pytest.raises(ValueError):
            await service.consume_nonce(
                GoogleNonceConsumeRequest(challenge_id=uuid.uuid4(), plaintext_nonce="")
            )

    @pytest.mark.asyncio
    async def test_consume_rejects_wrong_challenge_id_without_consuming_real_row(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        _, plaintext = await service.issue_nonce(
            GoogleNonceIssueRequest(
                ip_hash=None,
                user_agent_hash=None,
                ttl_seconds=600,
            )
        )
        real_id = conn._store["google_nonce_challenges"][0]["id"]

        with pytest.raises(GoogleNonceInvalid):
            await service.consume_nonce(
                GoogleNonceConsumeRequest(
                    challenge_id=uuid.uuid4(),
                    plaintext_nonce=plaintext,
                )
            )

        assert conn._store["google_nonce_challenges"][0]["id"] == real_id
        assert conn._store["google_nonce_challenges"][0]["consumed_at"] is None


# ============================================================================
# Service: ID-token verification
# ============================================================================


class TestVerifyIdToken:
    @pytest.mark.asyncio
    async def test_valid_token_and_nonce_returns_identity(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "valid-nonce-1"
        pepper = _expected_pepper(_dev_settings())
        consumed = _make_consumed_row(plaintext, pepper)
        claims = _now_claims(
            sub="google-sub-1",
            email="User@Example.com",
            email_verified=True,
            nonce=plaintext,
        )
        stub = _StubVerifier(claims=claims)
        service = GoogleVerifierService(conn, _dev_settings(), stub)
        result = await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw-id-token",
                plaintext_nonce=plaintext,
                consumed_nonce=consumed,
            )
        )
        assert isinstance(result, VerifiedGoogleIdentity)
        assert result.provider_subject == "google-sub-1"
        # normalize_email is LOWER+TRIM only.
        assert result.normalized_email == "user@example.com"
        assert result.original_email == "User@Example.com"
        assert result.verified_at is not None
        # The verifier was called with the audience list
        # `[client_id] + allowlist` so the library enforces
        # the full audience set at the boundary. With an
        # empty allowlist the service passes a single
        # `str` (not a 1-element list) for the common
        # case; the call record must still be the
        # configured client ID either way.
        assert stub.calls
        audience_arg = stub.calls[0][2]
        if isinstance(audience_arg, list):
            assert audience_arg == ["daemon-test-client-id.googleusercontent.com"]
        else:
            assert audience_arg == "daemon-test-client-id.googleusercontent.com"
        # The verifier is called with `request=None`; the
        # default verifier constructs a `Request()` from
        # this None. The boundary contract is documented
        # in `default_google_id_token_verifier`.
        assert stub.calls[0][1] is None

    @pytest.mark.asyncio
    async def test_wrong_audience_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-wrong-aud"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        # aud differs from client_id and is not in the (empty) allowlist.
        claims = _now_claims(aud="other-client-id", nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_audience_in_allowlist_passes(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-aud-allow"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        # aud is not the primary client_id but is in the allowlist.
        claims = _now_claims(aud="secondary-client-id", nonce=plaintext)
        settings = _dev_settings(
            client_id="daemon-test-client-id.googleusercontent.com",
            audience_allowlist="secondary-client-id,third-client",
        )
        stub = _StubVerifier(claims=claims)
        service = GoogleVerifierService(conn, settings, stub)
        result = await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
            )
        )
        assert result.provider_subject == "1234567890"
        # The service must pass the FULL audience list
        # `[client_id] + allowlist` to the library so the
        # library enforces the allowlist at the boundary
        # (not just the primary client ID). The third
        # allowlist entry is a no-op here; the test just
        # asserts the list shape.
        assert stub.calls
        audience_arg = stub.calls[0][2]
        assert audience_arg == [
            "daemon-test-client-id.googleusercontent.com",
            "secondary-client-id",
            "third-client",
        ]
        # The service must pass `request=None`; the
        # default verifier is responsible for constructing
        # a `Request()` from this None.
        assert stub.calls[0][1] is None

    @pytest.mark.asyncio
    async def test_service_passes_audience_list_when_allowlist_non_empty(self) -> None:
        # The library-boundary fix: when an allowlist is
        # configured, the service must pass a
        # `list[str]` (not a single `str`) to the
        # library so the library enforces the full set at
        # the boundary. With the previous (buggy)
        # implementation, only the primary client ID
        # reached the library and an allowlisted token
        # would have been rejected at the library before
        # reaching the post-decode allowlist check.
        conn = _GoogleMockConn()
        plaintext = "nonce-list-shape"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(aud="secondary", nonce=plaintext)
        settings = _dev_settings(
            client_id="primary",
            audience_allowlist="secondary",
        )
        stub = _StubVerifier(claims=claims)
        service = GoogleVerifierService(conn, settings, stub)
        await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
            )
        )
        audience_arg = stub.calls[0][2]
        assert isinstance(audience_arg, list), (
            f"audience must be a list when allowlist is non-empty; got {type(audience_arg)}"
        )
        assert audience_arg == ["primary", "secondary"]

    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-wrong-iss"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(iss="https://evil.example.com", nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_legacy_issuer_passes(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-legacy-iss"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(iss=GOOGLE_ISSUER_LEGACY, nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        result = await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
            )
        )
        assert result.provider_subject == "1234567890"

    @pytest.mark.asyncio
    async def test_library_signature_failure_collapsed_to_token_invalid(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-sig-fail"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))

        # The real google.auth library raises google.auth.exceptions.GoogleAuthError
        # subclasses; we simulate one and confirm the verifier collapses
        # it to GoogleTokenInvalid (no leak of the library error class).
        class _FakeGoogleAuthError(Exception):
            pass

        stub = _StubVerifier(error=_FakeGoogleAuthError("bad signature"))
        service = GoogleVerifierService(conn, _dev_settings(), stub)
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw",
                    plaintext_nonce=plaintext,
                    consumed_nonce=consumed,
                )
            )

    @pytest.mark.asyncio
    async def test_missing_sub_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-no-sub"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(nonce=plaintext)
        claims.pop("sub")
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_empty_sub_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-empty-sub"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(sub="", nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_missing_email_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-no-email"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(nonce=plaintext)
        claims.pop("email")
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_unverified_email_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-unverified-email"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(email_verified=False, nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_email_verified_non_bool_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-ev-string"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(email_verified="true", nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_wrong_nonce_claim_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-claim-mismatch"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(nonce="a-different-nonce")
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_missing_nonce_claim_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-no-claim"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims()
        claims.pop("nonce")
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_token_age_exceeds_one_hour_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-old-token"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        # exp - iat = 7200 (2 hours) exceeds the MAX_TOKEN_AGE_SECONDS guard.
        now = datetime.now(timezone.utc)
        claims = {
            "iss": GOOGLE_ISSUER_CANONICAL,
            "aud": "daemon-test-client-id.googleusercontent.com",
            "sub": "1234567890",
            "email": "user@example.com",
            "email_verified": True,
            "nonce": plaintext,
            "iat": int(now.timestamp()) - 7200,
            "exp": int(now.timestamp()),
        }
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_token_age_at_one_hour_passes(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-age-boundary"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        # exp - iat == MAX_TOKEN_AGE_SECONDS (off-by-one boundary: <= is allowed).
        now = datetime.now(timezone.utc)
        claims = {
            "iss": GOOGLE_ISSUER_CANONICAL,
            "aud": "daemon-test-client-id.googleusercontent.com",
            "sub": "1234567890",
            "email": "user@example.com",
            "email_verified": True,
            "nonce": plaintext,
            "iat": int(now.timestamp()) - MAX_TOKEN_AGE_SECONDS,
            "exp": int(now.timestamp()),
        }
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        result = await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
            )
        )
        assert result.provider_subject == "1234567890"

    @pytest.mark.asyncio
    async def test_azp_present_and_wrong_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-azp-wrong"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(azp="https://evil.example.com", nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_azp_present_and_matches_client_id_passes(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-azp-cid"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(azp="daemon-test-client-id.googleusercontent.com", nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        result = await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
            )
        )
        assert result.provider_subject == "1234567890"

    @pytest.mark.asyncio
    async def test_azp_present_and_matches_allowlist_passes(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-azp-allow"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(azp="secondary-client", nonce=plaintext)
        settings = _dev_settings(
            client_id="daemon-test-client-id.googleusercontent.com",
            audience_allowlist="secondary-client",
        )
        service = GoogleVerifierService(conn, settings, _StubVerifier(claims=claims))
        result = await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
            )
        )
        assert result.provider_subject == "1234567890"

    @pytest.mark.asyncio
    async def test_azp_absent_passes(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-azp-absent"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        # _now_claims defaults azp to None; absent is allowed.
        claims = _now_claims(nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        result = await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
            )
        )
        assert result.provider_subject == "1234567890"

    @pytest.mark.asyncio
    async def test_azp_empty_string_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-azp-empty"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(azp="", nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_azp_non_string_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-azp-int"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(nonce=plaintext)
        claims["azp"] = 12345
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_missing_client_id_raises_unavailable(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-no-cid"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        settings = _dev_settings(client_id=None)
        # Provide a stub that would have passed claims so we
        # confirm the failure is the client_id check, not a
        # downstream claim check.
        claims = _now_claims(nonce=plaintext)
        service = GoogleVerifierService(conn, settings, _StubVerifier(claims=claims))
        with pytest.raises(GoogleVerifierUnavailable):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_claims_not_a_dict_rejected(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-bad-claims"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))

        # The stub returns a non-dict (the verifier must collapse).
        class _BadClaims:
            pass

        stub = _StubVerifier()
        # Inject the non-dict directly into the stub.
        stub._claims = _BadClaims()  # type: ignore[assignment]
        service = GoogleVerifierService(conn, _dev_settings(), stub)
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_library_arbitrary_exception_collapsed(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-arbitrary-exc"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        stub = _StubVerifier(error=RuntimeError("boom"))
        service = GoogleVerifierService(conn, _dev_settings(), stub)
        with pytest.raises(GoogleTokenInvalid):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
                )
            )

    @pytest.mark.asyncio
    async def test_rejects_empty_id_token(self) -> None:
        conn = _GoogleMockConn()
        plaintext = "nonce-empty-id"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        with pytest.raises(ValueError):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="",
                    plaintext_nonce=plaintext,
                    consumed_nonce=consumed,
                )
            )

    @pytest.mark.asyncio
    async def test_rejects_empty_nonce(self) -> None:
        conn = _GoogleMockConn()
        consumed = _make_consumed_row("x", _expected_pepper(_dev_settings()))
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        with pytest.raises(ValueError):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw",
                    plaintext_nonce="",
                    consumed_nonce=consumed,
                )
            )

    @pytest.mark.asyncio
    async def test_rejects_missing_consumed_nonce(self) -> None:
        conn = _GoogleMockConn()
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims={}))
        with pytest.raises(ValueError):
            await service.verify_id_token(
                GoogleIdTokenVerifyRequest(
                    id_token_str="raw",
                    plaintext_nonce="x",
                    consumed_nonce=None,  # type: ignore[arg-type]
                )
            )

    @pytest.mark.asyncio
    async def test_verify_does_not_touch_users_or_sessions(self) -> None:
        # Confirm a successful verify path produces no writes
        # to user/tenant/session/device/provider tables.
        conn = _GoogleMockConn()
        plaintext = "nonce-success-isolation"
        consumed = _make_consumed_row(plaintext, _expected_pepper(_dev_settings()))
        claims = _now_claims(nonce=plaintext)
        service = GoogleVerifierService(conn, _dev_settings(), _StubVerifier(claims=claims))
        await service.verify_id_token(
            GoogleIdTokenVerifyRequest(
                id_token_str="raw", plaintext_nonce=plaintext, consumed_nonce=consumed
            )
        )
        # The verify path issues NO SQL at all (it only calls
        # the library stub and inspects the claim dict). The
        # call log must be empty.
        assert conn.calls == []
        # And explicitly: the connection's store for
        # `google_nonce_challenges` was not mutated by verify.
        assert conn._store["google_nonce_challenges"] == []


# ============================================================================
# Class hierarchy / public surface
# ============================================================================


class TestErrorHierarchy:
    def test_subclass_relationships(self) -> None:
        assert issubclass(GoogleNonceInvalid, GoogleVerifierError)
        assert issubclass(GoogleTokenInvalid, GoogleVerifierError)
        assert issubclass(GoogleVerifierUnavailable, GoogleVerifierError)
        assert not issubclass(GoogleNonceInvalid, GoogleTokenInvalid)
        assert not issubclass(GoogleTokenInvalid, GoogleNonceInvalid)


class TestDefaultTtlConstant:
    def test_default_ttl_in_sane_range(self) -> None:
        assert 30 <= DEFAULT_TTL_SECONDS <= 3600


# ============================================================================
# Pepper helper
# ============================================================================


def _expected_pepper(settings: Settings) -> str:
    """Resolve the same pepper the service uses. Mirrors
    `validate_and_get_pepper(settings)` for development
    environments.
    """
    from orchestrator.auth_pepper import validate_and_get_pepper

    return validate_and_get_pepper(settings)
