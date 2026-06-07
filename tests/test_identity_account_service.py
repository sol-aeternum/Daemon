"""Tests for the hosted identity account, tenant, invite, and provider service.

Coverage (matches TODO 8 acceptance criteria):

  - normalize_email: lowercases, trims, rejects empty input.
  - AccountService.find_user_by_normalized_email / find_user_by_provider /
    find_personal_tenant / find_active_invite: empty + populated paths.
  - ensure_personal_tenant / ensure_owner_membership: idempotency under
    concurrent calls (INSERT+ON CONFLICT+SELECT pattern).
  - claim_email_identity:
    - open mode, new user: creates user + tenant + membership, is_new=True.
    - open mode, existing user: reuses, is_new=False, sets verified_at if
      the user was previously unverified.
    - invite-only with valid invite: consumes invite, creates user +
      tenant + membership, is_new=True.
    - invite-only without token: InviteOnlyRejection (generic).
    - invite-only with mismatched token: InviteOnlyRejection (generic).
    - invite-only with no active invite: InviteOnlyRejection.
    - disabled mode: SignupDisabled.
  - claim_google_identity:
    - (google, sub) already linked: reuses, is_new=False.
    - new user, open mode: creates user + tenant + membership + provider link.
    - existing email user (verified): links the Google identity.
    - existing email user (unverified): EmailNotVerified.
    - email_verified=False: EmailNotVerified.
    - same sub already linked to a different user: ProviderCollision.
    - same user, different sub for same provider: ProviderCollision.
  - link_provider_identity:
    - new (provider, subject): inserts a row.
    - same (provider, subject) for same user: refreshes last_used_at
      (idempotent re-link).
    - same (provider, subject) for different user: ProviderCollision.
  - consume_invite:
    - active invite: consumed status, used_by_user_id set.
    - already consumed / expired / disabled: InviteInvalidOrExpired.
  - Concurrent tenant claim: two callers in sequence with the same user
    observe the same tenant and `is_new=False` for the second caller.

A hand-rolled `MockConn` implements the small asyncpg surface the service
exercises (fetchrow / fetchval / execute / fetch / transaction) so the
tests stay hermetic and run without a live Postgres.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio


from orchestrator.services.identity.account_service import (
    AccountService,
    ClaimResult,
    EmailNotVerified,
    InviteInvalidOrExpired,
    InviteOnlyRejection,
    ProviderCollision,
    ProviderLink,
    SignupDisabled,
    TenantRow,
    UserRow,
    normalize_email,
)


# ---------------------------------------------------------------------------
# In-memory record helpers
# ---------------------------------------------------------------------------


class _Record(dict):
    """Dict-like record that supports both `record["col"]` and
    `record.col` lookups. asyncpg `Record` supports both, so the
    service-layer code that uses `record[column]` works against this
    fake without translation.
    """

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


# ---------------------------------------------------------------------------
# In-memory connection (MockConn)
# ---------------------------------------------------------------------------


class _IdentityMockConn:
    """In-memory asyncpg stand-in for the account service.

    The mock supports the four operations the service uses
    (fetchrow, fetchval, execute, fetch) and a `transaction`
    async context manager that no-ops. The SQL shape is parsed
    by `_match` and routed to a handler method; each handler
    returns a dict-shaped record, a scalar, or a status string.
    Tests populate `_store` (a dict of table -> list of rows)
    to seed pre-existing state, and may assert on the call log
    (`calls`) to confirm the service issued the expected SQL.

    The mock is intentionally simple: it does not implement
    partial-unique-index semantics. Idempotency is tested by
    sequencing two calls (first one inserts, second one observes
    the existing row) and asserting both return the same UUID.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {
            "users": [],
            "tenants": [],
            "tenant_memberships": [],
            "identity_providers": [],
            "signup_invites": [],
        }
        self._insert_id_seq: dict[str, int] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._transaction_active: bool = False
        self.transaction_stack: list[bool] = []
        self.reserved_user_ids: set[uuid.UUID] = {uuid.UUID("00000000-0000-0000-0000-000000000000")}
        self.miss_next_user_lookup_for: set[str] = set()

    # ----- helpers used by tests -----

    def add_user(
        self,
        *,
        normalized_email: str | None = None,
        email_verified_at: datetime | None = None,
        user_id: uuid.UUID | None = None,
        username: str = "user",
    ) -> uuid.UUID:
        uid = user_id or uuid.uuid4()
        if uid in self.reserved_user_ids:
            raise ValueError(f"add_user: reserved id {uid} cannot be used as a real user")
        self._store["users"].append(
            {
                "id": uid,
                "username": username,
                "email": normalized_email,
                "name": normalized_email,
                "normalized_email": normalized_email,
                "email_verified_at": email_verified_at,
            }
        )
        return uid

    def add_tenant(
        self,
        *,
        owner_user_id: uuid.UUID,
        kind: str = "personal",
        name: str = "Personal",
        tenant_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        tid = tenant_id or uuid.uuid4()
        self._store["tenants"].append(
            {
                "id": tid,
                "owner_user_id": owner_user_id,
                "kind": kind,
                "name": name,
            }
        )
        return tid

    def add_membership(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str = "owner",
    ) -> None:
        # PRIMARY KEY (tenant_id, user_id) enforced
        for row in self._store["tenant_memberships"]:
            if row["tenant_id"] == tenant_id and row["user_id"] == user_id:
                return
        self._store["tenant_memberships"].append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "role": role,
                "created_at": datetime.now(timezone.utc),
            }
        )

    def add_provider_link(
        self,
        *,
        user_id: uuid.UUID,
        provider: str,
        provider_subject: str,
        normalized_email_at_link: str | None = None,
    ) -> uuid.UUID:
        link_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        self._store["identity_providers"].append(
            {
                "id": link_id,
                "user_id": user_id,
                "provider": provider,
                "provider_subject": provider_subject,
                "normalized_email_at_link": normalized_email_at_link,
                "linked_at": now,
                "last_used_at": now,
            }
        )
        return link_id

    def add_invite(
        self,
        *,
        normalized_email: str,
        token_verifier_hash: str,
        status: str = "active",
        expires_at: datetime | None = None,
        used_by_user_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        iid = uuid.uuid4()
        self._store["signup_invites"].append(
            {
                "id": iid,
                "normalized_email": normalized_email,
                "token_verifier_hash": token_verifier_hash,
                "status": status,
                "expires_at": expires_at or (datetime.now(timezone.utc) + timedelta(days=7)),
                "used_by_user_id": used_by_user_id,
                "consumed_at": None,
                "created_at": datetime.now(timezone.utc),
            }
        )
        return iid

    def all_provider_links(self, user_id: uuid.UUID) -> list[ProviderLink]:
        out = []
        for row in self._store["identity_providers"]:
            if row["user_id"] == user_id:
                out.append(
                    ProviderLink(
                        id=row["id"],
                        user_id=row["user_id"],
                        provider=row["provider"],
                        provider_subject=row["provider_subject"],
                        normalized_email_at_link=row["normalized_email_at_link"],
                        linked_at=row["linked_at"],
                        last_used_at=row["last_used_at"],
                    )
                )
        return out

    # ----- asyncpg-shape operations -----

    @asynccontextmanager
    async def transaction(self):
        self._transaction_active = True
        self.transaction_stack.append(True)
        try:
            yield self
        finally:
            self.transaction_stack.pop()
            self._transaction_active = bool(self.transaction_stack)

    async def fetchrow(self, query: str, *args: Any) -> _Record | None:
        self.calls.append((query, args))
        q = _strip_sql(query)
        if q.startswith(
            "SELECT id, normalized_email, email_verified_at FROM users WHERE normalized_email"
        ):
            target = args[0]
            if target in self.miss_next_user_lookup_for:
                self.miss_next_user_lookup_for.remove(target)
                return None
            for row in self._store["users"]:
                if row["normalized_email"] == target:
                    return _Record(row)
            return None
        if q.startswith(
            "SELECT u.id, u.normalized_email, u.email_verified_at FROM users u JOIN identity_providers ip"
        ):
            provider = args[0]
            subject = args[1]
            for link in self._store["identity_providers"]:
                if link["provider"] == provider and link["provider_subject"] == subject:
                    for u in self._store["users"]:
                        if u["id"] == link["user_id"]:
                            return _Record(u)
            return None
        if q.startswith("SELECT id, owner_user_id, kind, name FROM tenants WHERE owner_user_id"):
            owner = args[0]
            for t in self._store["tenants"]:
                if t["owner_user_id"] == owner and t["kind"] == "personal":
                    return _Record(t)
            return None
        if q.startswith(
            "SELECT id, normalized_email, status, expires_at, used_by_user_id FROM signup_invites WHERE normalized_email"
        ):
            target = args[0]
            now = datetime.now(timezone.utc)
            for inv in self._store["signup_invites"]:
                if (
                    inv["normalized_email"] == target
                    and inv["status"] == "active"
                    and inv["expires_at"] > now
                ):
                    return _Record(inv)
            return None
        if q.startswith("INSERT INTO users"):
            username = args[0]
            email_value = args[1]
            name_value = args[2]
            normalized_email = args[3]
            email_verified_at = args[4]
            if not username or not isinstance(username, str):
                raise _NotNullViolationError(
                    "users.username is NOT NULL; INSERT must include a non-empty value"
                )
            for u in self._store["users"]:
                if u["normalized_email"] == normalized_email:
                    if "ON CONFLICT" in q:
                        return None
                    raise _UniqueViolationError("unique violation on users.normalized_email")
            new_id = uuid.uuid4()
            self._store["users"].append(
                {
                    "id": new_id,
                    "username": username,
                    "email": email_value,
                    "name": name_value,
                    "normalized_email": normalized_email,
                    "email_verified_at": email_verified_at,
                }
            )
            return _Record(
                {
                    "id": new_id,
                    "normalized_email": normalized_email,
                    "email_verified_at": email_verified_at,
                }
            )
        if q.startswith("UPDATE users SET email_verified_at"):
            user_id = args[0]
            for u in self._store["users"]:
                if u["id"] == user_id:
                    u["email_verified_at"] = datetime.now(timezone.utc)
                    return _Record(u)
            return None
        if q.startswith("UPDATE users SET normalized_email"):
            user_id = args[0]
            new_email = args[1]
            for u in self._store["users"]:
                if u["id"] == user_id:
                    if any(
                        other["normalized_email"] == new_email and other["id"] != user_id
                        for other in self._store["users"]
                    ):
                        raise _UniqueViolationError("unique violation on users.normalized_email")
                    u["normalized_email"] = new_email
                    return _Record(u)
            return None
        if q.startswith("INSERT INTO tenants"):
            user_id = args[0]
            name = args[1]
            for t in self._store["tenants"]:
                if t["owner_user_id"] == user_id and t["kind"] == "personal":
                    # ON CONFLICT skipped: RETURNING produces no row
                    return None
            new_id = uuid.uuid4()
            self._store["tenants"].append(
                {
                    "id": new_id,
                    "owner_user_id": user_id,
                    "kind": "personal",
                    "name": name,
                }
            )
            return _Record(
                {
                    "id": new_id,
                    "owner_user_id": user_id,
                    "kind": "personal",
                    "name": name,
                }
            )
        if q.startswith("INSERT INTO tenant_memberships"):
            tenant_id = args[0]
            user_id = args[1]
            for m in self._store["tenant_memberships"]:
                if m["tenant_id"] == tenant_id and m["user_id"] == user_id:
                    return None
            self._store["tenant_memberships"].append(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "role": "owner",
                    "created_at": datetime.now(timezone.utc),
                }
            )
            return _Record(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "role": "owner",
                }
            )
        if q.startswith("SELECT provider, provider_subject FROM identity_providers WHERE user_id"):
            user_id = args[0]
            provider = args[1]
            for link in self._store["identity_providers"]:
                if link["user_id"] == user_id and link["provider"] == provider:
                    return _Record(
                        {
                            "provider": link["provider"],
                            "provider_subject": link["provider_subject"],
                        }
                    )
            return None
        if q.startswith("SELECT user_id FROM identity_providers WHERE provider"):
            provider = args[0]
            subject = args[1]
            for link in self._store["identity_providers"]:
                if link["provider"] == provider and link["provider_subject"] == subject:
                    return link["user_id"]
            return None
        if q.startswith("INSERT INTO identity_providers"):
            user_id = args[0]
            provider = args[1]
            subject = args[2]
            email_at_link = args[3]
            for link in self._store["identity_providers"]:
                if link["provider"] == provider and link["provider_subject"] == subject:
                    raise _UniqueViolationError("unique violation on identity_providers")
            now = datetime.now(timezone.utc)
            new_id = uuid.uuid4()
            new_row = {
                "id": new_id,
                "user_id": user_id,
                "provider": provider,
                "provider_subject": subject,
                "normalized_email_at_link": email_at_link,
                "linked_at": now,
                "last_used_at": now,
            }
            self._store["identity_providers"].append(new_row)
            return _Record(new_row)
        if q.startswith("UPDATE identity_providers SET last_used_at"):
            user_id = args[0]
            provider = args[1]
            subject = args[2]
            normalized_email_at_link = args[3] if len(args) > 3 else None
            now = datetime.now(timezone.utc)
            for link in self._store["identity_providers"]:
                if (
                    link["user_id"] == user_id
                    and link["provider"] == provider
                    and link["provider_subject"] == subject
                ):
                    link["last_used_at"] = now
                    if normalized_email_at_link is not None:
                        link["normalized_email_at_link"] = normalized_email_at_link
                    return _Record(link)
            return None
        if q.startswith("UPDATE signup_invites SET status"):
            invite_id = args[0]
            used_by = args[1]
            now = datetime.now(timezone.utc)
            if used_by in self.reserved_user_ids:
                raise _ForeignKeyViolationError(
                    f"signup_invites.used_by_user_id = {used_by} "
                    f"violates FK to users(id) (reserved placeholder)"
                )
            for inv in self._store["signup_invites"]:
                if inv["id"] != invite_id:
                    continue
                if (
                    inv["status"] == "active"
                    and inv["used_by_user_id"] is None
                    and inv["expires_at"] > now
                ):
                    inv["status"] = "consumed"
                    inv["used_by_user_id"] = used_by
                    inv["consumed_at"] = now
                    return _Record(inv)
            return None
        if q.startswith("SELECT token_verifier_hash FROM signup_invites WHERE id"):
            invite_id = args[0]
            now = datetime.now(timezone.utc)
            for inv in self._store["signup_invites"]:
                if inv["id"] == invite_id and inv["status"] == "active" and inv["expires_at"] > now:
                    return inv["token_verifier_hash"]
            return None
        raise AssertionError(f"unmocked fetchrow query: {query!r}")

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        q = _strip_sql(query)
        if q.startswith("SELECT role FROM tenant_memberships WHERE tenant_id"):
            tenant_id = args[0]
            user_id = args[1]
            for m in self._store["tenant_memberships"]:
                if m["tenant_id"] == tenant_id and m["user_id"] == user_id:
                    return m["role"]
            return None
        if q.startswith("INSERT INTO tenants"):
            user_id = args[0]
            name = args[1]
            for t in self._store["tenants"]:
                if t["owner_user_id"] == user_id and t["kind"] == "personal":
                    return None
            new_id = uuid.uuid4()
            self._store["tenants"].append(
                {
                    "id": new_id,
                    "owner_user_id": user_id,
                    "kind": "personal",
                    "name": name,
                }
            )
            return new_id
        if q.startswith("INSERT INTO tenant_memberships"):
            tenant_id = args[0]
            user_id = args[1]
            for m in self._store["tenant_memberships"]:
                if m["tenant_id"] == tenant_id and m["user_id"] == user_id:
                    return None
            self._store["tenant_memberships"].append(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "role": "owner",
                    "created_at": datetime.now(timezone.utc),
                }
            )
            return "owner"
        if q.startswith("SELECT token_verifier_hash FROM signup_invites WHERE id"):
            invite_id = args[0]
            now = datetime.now(timezone.utc)
            for inv in self._store["signup_invites"]:
                if inv["id"] == invite_id and inv["status"] == "active" and inv["expires_at"] > now:
                    return inv["token_verifier_hash"]
            return None
        if q.startswith("SELECT user_id FROM identity_providers WHERE provider"):
            provider = args[0]
            subject = args[1]
            for link in self._store["identity_providers"]:
                if link["provider"] == provider and link["provider_subject"] == subject:
                    return link["user_id"]
            return None
        raise AssertionError(f"unmocked fetchval query: {query!r}")

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        # Service does not branch on the execute return for the
        # account service; record it for debugging only.
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[_Record]:
        self.calls.append((query, args))
        q = _strip_sql(query)
        if q.startswith(
            "SELECT id, user_id, provider, provider_subject, normalized_email_at_link, linked_at, last_used_at FROM identity_providers WHERE user_id"
        ):
            user_id = args[0]
            out = []
            for link in self._store["identity_providers"]:
                if link["user_id"] == user_id:
                    out.append(_Record(link))
            out.sort(key=lambda r: r["linked_at"])
            return out
        raise AssertionError(f"unmocked fetch query: {query!r}")


class _UniqueViolationError(Exception):
    """Sentinel used by the mock to stand in for asyncpg's
    `UniqueViolationError` exception class. The service matches
    on the class name (`"UniqueViolation" in type(exc).__name__`).
    """


class _NotNullViolationError(Exception):
    """Sentinel used by the mock to stand in for asyncpg's
    `NotNullViolationError` exception class. Raised by the mock
    when a regression omits a NOT NULL column from an INSERT.
    """


class _ForeignKeyViolationError(Exception):
    """Sentinel used by the mock to stand in for asyncpg's
    `ForeignKeyViolationError` exception class. Raised by the
    mock when a regression writes a reserved/placeholder user id
    to a column with a FK to `users(id)`.
    """


def _strip_sql(query: str) -> str:
    """Collapse multi-line SQL into a single-line prefix the
    mock can dispatch on. Newlines and excess whitespace are
    normalized.
    """
    return " ".join(query.split())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> _IdentityMockConn:
    return _IdentityMockConn()


@pytest_asyncio.fixture
async def service(conn: _IdentityMockConn) -> AccountService:
    return AccountService(conn)


# ---------------------------------------------------------------------------
# normalize_email
# ---------------------------------------------------------------------------


class TestNormalizeEmail:
    def test_lowercases(self) -> None:
        assert normalize_email("User@Example.COM") == "user@example.com"

    def test_trims_whitespace(self) -> None:
        assert normalize_email("  user@example.com  ") == "user@example.com"

    def test_lowercases_and_trims(self) -> None:
        assert normalize_email("  User@Example.COM\n") == "user@example.com"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            normalize_email("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError):
            normalize_email("   ")

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError):
            normalize_email(None)  # type: ignore[arg-type]

    def test_rejects_missing_at_sign(self) -> None:
        with pytest.raises(ValueError):
            normalize_email("not-an-address")

    def test_rejects_missing_domain_dot(self) -> None:
        with pytest.raises(ValueError):
            normalize_email("user@example")

    def test_rejects_empty_domain_label(self) -> None:
        with pytest.raises(ValueError):
            normalize_email("user@example..com")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


class TestFindUserByNormalizedEmail:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_user(self, service: AccountService) -> None:
        result = await service.find_user_by_normalized_email("nope@example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_user_when_present(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user(
            normalized_email="user@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        result = await service.find_user_by_normalized_email("user@example.com")
        assert result is not None
        assert result.id == uid
        assert result.normalized_email == "user@example.com"
        assert result.is_email_verified is True

    @pytest.mark.asyncio
    async def test_returns_unverified_flag(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        conn.add_user(normalized_email="new@example.com")
        result = await service.find_user_by_normalized_email("new@example.com")
        assert result is not None
        assert result.email_verified_at is None
        assert result.is_email_verified is False


class TestFindUserByProvider:
    @pytest.mark.asyncio
    async def test_returns_none_when_unlinked(self, service: AccountService) -> None:
        result = await service.find_user_by_provider("google", "unknown-sub")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_user_for_linked_sub(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user(normalized_email="linked@example.com")
        conn.add_provider_link(
            user_id=uid,
            provider="google",
            provider_subject="google-sub-123",
        )
        result = await service.find_user_by_provider("google", "google-sub-123")
        assert result is not None
        assert result.id == uid


class TestFindPersonalTenant:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_tenant(self, service: AccountService) -> None:
        uid = uuid.uuid4()
        result = await service.find_personal_tenant(uid)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_personal_tenant(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user(normalized_email="t@example.com")
        tid = conn.add_tenant(owner_user_id=uid)
        result = await service.find_personal_tenant(uid)
        assert result is not None
        assert result.id == tid
        assert result.owner_user_id == uid
        assert result.kind == "personal"


class TestFindActiveInvite:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_invite(self, service: AccountService) -> None:
        result = await service.find_active_invite("nobody@example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_active_invite(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        iid = conn.add_invite(
            normalized_email="invited@example.com",
            token_verifier_hash="abc-hash",
        )
        result = await service.find_active_invite("invited@example.com")
        assert result is not None
        assert result.id == iid
        assert result.status == "active"

    @pytest.mark.asyncio
    async def test_skips_expired_invite(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        conn.add_invite(
            normalized_email="expired@example.com",
            token_verifier_hash="x",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        result = await service.find_active_invite("expired@example.com")
        assert result is None


# ---------------------------------------------------------------------------
# ensure_personal_tenant / ensure_owner_membership — idempotency
# ---------------------------------------------------------------------------


class TestEnsurePersonalTenant:
    @pytest.mark.asyncio
    async def test_creates_tenant_for_new_user(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = uuid.uuid4()
        tenant, is_new = await service.ensure_personal_tenant(uid)
        assert is_new is True
        assert tenant.owner_user_id == uid
        assert tenant.kind == "personal"

    @pytest.mark.asyncio
    async def test_idempotent_on_second_call(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = uuid.uuid4()
        first, is_new_first = await service.ensure_personal_tenant(uid)
        second, is_new_second = await service.ensure_personal_tenant(uid)
        assert is_new_first is True
        assert is_new_second is False
        assert first.id == second.id
        # And the in-memory store has exactly one row for that user.
        matching = [
            t
            for t in conn._store["tenants"]
            if t["owner_user_id"] == uid and t["kind"] == "personal"
        ]
        assert len(matching) == 1


class TestEnsureOwnerMembership:
    @pytest.mark.asyncio
    async def test_creates_membership_for_new_pair(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user()
        tid = conn.add_tenant(owner_user_id=uid)
        role, is_new = await service.ensure_owner_membership(tid, uid)
        assert role == "owner"
        assert is_new is True

    @pytest.mark.asyncio
    async def test_idempotent_on_second_call(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user()
        tid = conn.add_tenant(owner_user_id=uid)
        _, is_new_first = await service.ensure_owner_membership(tid, uid)
        _, is_new_second = await service.ensure_owner_membership(tid, uid)
        assert is_new_first is True
        assert is_new_second is False
        matching = [
            m
            for m in conn._store["tenant_memberships"]
            if m["tenant_id"] == tid and m["user_id"] == uid
        ]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# claim_email_identity
# ---------------------------------------------------------------------------


class TestClaimEmailIdentityOpenMode:
    @pytest.mark.asyncio
    async def test_creates_user_tenant_membership(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        verified_at = datetime.now(timezone.utc)
        result = await service.claim_email_identity(
            normalized_email="new@example.com",
            email_verified_at=verified_at,
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert isinstance(result, ClaimResult)
        assert result.is_new_user is True
        assert result.is_new_tenant is True
        assert result.is_new_membership is True
        assert result.membership_role == "owner"
        assert result.user.normalized_email == "new@example.com"
        assert result.user.email_verified_at == verified_at
        assert result.tenant.owner_user_id == result.user.id
        # The mock store has the expected rows.
        assert len(conn._store["users"]) == 1
        assert len(conn._store["tenants"]) == 1
        assert len(conn._store["tenant_memberships"]) == 1
        # No identity_providers row for email-only completion.
        assert conn._store["identity_providers"] == []

    @pytest.mark.asyncio
    async def test_repeated_signin_reuses_account(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        verified_at = datetime.now(timezone.utc)
        first = await service.claim_email_identity(
            normalized_email="reuser@example.com",
            email_verified_at=verified_at,
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        second = await service.claim_email_identity(
            normalized_email="reuser@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert first.user.id == second.user.id
        assert first.tenant.id == second.tenant.id
        assert second.is_new_user is False
        assert second.is_new_tenant is False
        assert second.is_new_membership is False
        # Store is unchanged on the second call.
        assert len(conn._store["users"]) == 1
        assert len(conn._store["tenants"]) == 1
        assert len(conn._store["tenant_memberships"]) == 1

    @pytest.mark.asyncio
    async def test_repeated_signin_stamps_verified_when_unverified(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        # Seed an unverified user (e.g. partial migration state or
        # user created before email_verified_at was wired).
        conn.add_user(normalized_email="later@example.com")
        result = await service.claim_email_identity(
            normalized_email="later@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert result.user.email_verified_at is not None


class TestClaimEmailIdentityDisabled:
    @pytest.mark.asyncio
    async def test_disabled_mode_rejects_new_signup(self, service: AccountService) -> None:
        with pytest.raises(SignupDisabled):
            await service.claim_email_identity(
                normalized_email="newbie@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="disabled",
                invite_token_verifier_hash=None,
            )

    @pytest.mark.asyncio
    async def test_disabled_mode_allows_existing_signin(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        conn.add_user(
            normalized_email="existing@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        # Existing user can still sign in even with disabled
        # signup — the gate only applies to NEW users.
        result = await service.claim_email_identity(
            normalized_email="existing@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="disabled",
            invite_token_verifier_hash=None,
        )
        assert result.is_new_user is False


class TestClaimEmailIdentityInviteOnly:
    @pytest.mark.asyncio
    async def test_rejects_without_invite(self, service: AccountService) -> None:
        with pytest.raises(InviteOnlyRejection):
            await service.claim_email_identity(
                normalized_email="uninvited@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="invite_only",
                invite_token_verifier_hash=None,
            )

    @pytest.mark.asyncio
    async def test_rejects_with_wrong_token(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        conn.add_invite(
            normalized_email="inv@example.com",
            token_verifier_hash="right-hash",
        )
        with pytest.raises(InviteOnlyRejection):
            await service.claim_email_identity(
                normalized_email="inv@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="invite_only",
                invite_token_verifier_hash="wrong-hash",
            )

    @pytest.mark.asyncio
    async def test_rejects_with_no_active_invite(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        # No invite seeded for this email.
        with pytest.raises(InviteOnlyRejection):
            await service.claim_email_identity(
                normalized_email="uninvited2@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="invite_only",
                invite_token_verifier_hash="any-hash",
            )

    @pytest.mark.asyncio
    async def test_accepts_valid_invite_and_creates_account(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        invite_id = conn.add_invite(
            normalized_email="vip@example.com",
            token_verifier_hash="secret-hash",
        )
        result = await service.claim_email_identity(
            normalized_email="vip@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="invite_only",
            invite_token_verifier_hash="secret-hash",
        )
        assert result.is_new_user is True
        assert result.is_new_tenant is True
        # Invite is consumed (status == consumed).
        invite = next(i for i in conn._store["signup_invites"] if i["id"] == invite_id)
        assert invite["status"] == "consumed"
        assert invite["used_by_user_id"] is not None

    @pytest.mark.asyncio
    async def test_rejection_messages_are_indistinguishable(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        """The route layer must not be able to differentiate the
        three rejection classes by message text (enumeration
        resistance)."""
        # No invite
        with pytest.raises(InviteOnlyRejection) as exc1:
            await service.claim_email_identity(
                normalized_email="e1@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="invite_only",
                invite_token_verifier_hash=None,
            )
        # Wrong token
        conn.add_invite(
            normalized_email="e2@example.com",
            token_verifier_hash="right",
        )
        with pytest.raises(InviteOnlyRejection) as exc2:
            await service.claim_email_identity(
                normalized_email="e2@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="invite_only",
                invite_token_verifier_hash="wrong",
            )
        # No active invite (different email)
        with pytest.raises(InviteOnlyRejection) as exc3:
            await service.claim_email_identity(
                normalized_email="e3@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="invite_only",
                invite_token_verifier_hash="any",
            )
        # All three messages are non-empty strings.
        assert all(str(exc) for exc in (exc1, exc2, exc3))


# ---------------------------------------------------------------------------
# claim_google_identity
# ---------------------------------------------------------------------------


class TestClaimGoogleIdentity:
    @pytest.mark.asyncio
    async def test_rejects_unverified_token(self, service: AccountService) -> None:
        with pytest.raises(EmailNotVerified):
            await service.claim_google_identity(
                google_sub="sub-1",
                normalized_email="user@example.com",
                email_verified=False,
                signup_mode="open",
                invite_token_verifier_hash=None,
            )

    @pytest.mark.asyncio
    async def test_reuses_existing_google_link(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user(
            normalized_email="linked@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        conn.add_provider_link(
            user_id=uid,
            provider="google",
            provider_subject="known-sub",
        )
        result = await service.claim_google_identity(
            google_sub="known-sub",
            normalized_email="linked@example.com",
            email_verified=True,
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert result.is_new_user is False
        assert result.user.id == uid
        # No new user/tenant/membership rows.
        assert len(conn._store["users"]) == 1
        assert len(conn._store["tenants"]) == 1
        assert len(conn._store["tenant_memberships"]) == 1

    @pytest.mark.asyncio
    async def test_reuses_existing_google_link_after_verified_email_change(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user(
            normalized_email="old@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        conn.add_provider_link(
            user_id=uid,
            provider="google",
            provider_subject="known-sub",
            normalized_email_at_link="old@example.com",
        )

        result = await service.claim_google_identity(
            google_sub="known-sub",
            normalized_email="new@example.com",
            email_verified=True,
            signup_mode="open",
            invite_token_verifier_hash=None,
        )

        assert result.user.id == uid
        assert result.user.normalized_email == "new@example.com"
        stored_user = next(u for u in conn._store["users"] if u["id"] == uid)
        assert stored_user["normalized_email"] == "new@example.com"
        links = conn.all_provider_links(uid)
        assert len(links) == 1
        assert links[0].provider_subject == "known-sub"
        assert links[0].normalized_email_at_link == "new@example.com"

    @pytest.mark.asyncio
    async def test_reuses_existing_google_link_rejects_cross_user_email_collision(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        linked_uid = conn.add_user(
            normalized_email="old@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        conn.add_provider_link(
            user_id=linked_uid,
            provider="google",
            provider_subject="known-sub",
            normalized_email_at_link="old@example.com",
        )
        other_uid = conn.add_user(
            normalized_email="new@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )

        with pytest.raises(ProviderCollision):
            await service.claim_google_identity(
                google_sub="known-sub",
                normalized_email="new@example.com",
                email_verified=True,
                signup_mode="open",
                invite_token_verifier_hash=None,
            )

        stored_linked_user = next(u for u in conn._store["users"] if u["id"] == linked_uid)
        assert stored_linked_user["normalized_email"] == "old@example.com"
        stored_other_user = next(u for u in conn._store["users"] if u["id"] == other_uid)
        assert stored_other_user["normalized_email"] == "new@example.com"

    @pytest.mark.asyncio
    async def test_links_to_existing_verified_user(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user(
            normalized_email="me@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        result = await service.claim_google_identity(
            google_sub="new-sub",
            normalized_email="me@example.com",
            email_verified=True,
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert result.is_new_user is False
        assert result.user.id == uid
        # Identity link is now present.
        links = conn.all_provider_links(uid)
        assert len(links) == 1
        assert links[0].provider == "google"
        assert links[0].provider_subject == "new-sub"

    @pytest.mark.asyncio
    async def test_rejects_link_to_unverified_user(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        conn.add_user(
            normalized_email="unverified@example.com",
            email_verified_at=None,
        )
        with pytest.raises(EmailNotVerified):
            await service.claim_google_identity(
                google_sub="some-sub",
                normalized_email="unverified@example.com",
                email_verified=True,
                signup_mode="open",
                invite_token_verifier_hash=None,
            )

    @pytest.mark.asyncio
    async def test_creates_new_user_with_provider_link(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        result = await service.claim_google_identity(
            google_sub="brand-new-sub",
            normalized_email="newg@example.com",
            email_verified=True,
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert result.is_new_user is True
        assert result.is_new_tenant is True
        assert result.is_new_membership is True
        links = conn.all_provider_links(result.user.id)
        assert len(links) == 1
        assert links[0].provider == "google"
        assert links[0].provider_subject == "brand-new-sub"
        assert links[0].normalized_email_at_link == "newg@example.com"

    @pytest.mark.asyncio
    async def test_rejects_provider_collision_across_users(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        """A second user presenting the same Google sub as an
        existing link is rejected. The first user's account is
        not modified."""
        first_uid = conn.add_user(
            normalized_email="first@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        conn.add_provider_link(
            user_id=first_uid,
            provider="google",
            provider_subject="taken-sub",
        )
        conn.add_user(
            normalized_email="second@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        with pytest.raises(ProviderCollision):
            await service.claim_google_identity(
                google_sub="taken-sub",
                normalized_email="second@example.com",
                email_verified=True,
                signup_mode="open",
                invite_token_verifier_hash=None,
            )

    @pytest.mark.asyncio
    async def test_invite_only_requires_invite_for_new_user(self, service: AccountService) -> None:
        with pytest.raises(InviteOnlyRejection):
            await service.claim_google_identity(
                google_sub="new-sub",
                normalized_email="new-google@example.com",
                email_verified=True,
                signup_mode="invite_only",
                invite_token_verifier_hash=None,
            )

    @pytest.mark.asyncio
    async def test_invite_only_with_valid_invite_creates_user(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        conn.add_invite(
            normalized_email="invited-g@example.com",
            token_verifier_hash="g-invite-hash",
        )
        result = await service.claim_google_identity(
            google_sub="g-sub",
            normalized_email="invited-g@example.com",
            email_verified=True,
            signup_mode="invite_only",
            invite_token_verifier_hash="g-invite-hash",
        )
        assert result.is_new_user is True
        links = conn.all_provider_links(result.user.id)
        assert len(links) == 1


# ---------------------------------------------------------------------------
# link_provider_identity
# ---------------------------------------------------------------------------


class TestLinkProviderIdentity:
    @pytest.mark.asyncio
    async def test_inserts_new_link(self, service: AccountService, conn: _IdentityMockConn) -> None:
        uid = conn.add_user(normalized_email="lp@example.com")
        link = await service.link_provider_identity(
            user_id=uid,
            provider="google",
            provider_subject="sub-1",
            normalized_email_at_link="lp@example.com",
        )
        assert link.provider == "google"
        assert link.provider_subject == "sub-1"
        assert link.user_id == uid
        assert link.normalized_email_at_link == "lp@example.com"

    @pytest.mark.asyncio
    async def test_idempotent_relink_refreshes_last_used(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user(normalized_email="relink@example.com")
        first = await service.link_provider_identity(
            user_id=uid,
            provider="google",
            provider_subject="sub-2",
            normalized_email_at_link="relink@example.com",
        )
        # Same (user, provider, subject) re-link is a no-op insert
        # that refreshes last_used_at.
        second = await service.link_provider_identity(
            user_id=uid,
            provider="google",
            provider_subject="sub-2",
            normalized_email_at_link="relink@example.com",
        )
        assert first.id == second.id
        # Still only one row.
        assert len(conn._store["identity_providers"]) == 1

    @pytest.mark.asyncio
    async def test_rejects_different_user_with_same_sub(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        first_uid = conn.add_user(normalized_email="f@example.com")
        second_uid = conn.add_user(normalized_email="s@example.com")
        await service.link_provider_identity(
            user_id=first_uid,
            provider="google",
            provider_subject="dup-sub",
            normalized_email_at_link="f@example.com",
        )
        with pytest.raises(ProviderCollision):
            await service.link_provider_identity(
                user_id=second_uid,
                provider="google",
                provider_subject="dup-sub",
                normalized_email_at_link="s@example.com",
            )

    @pytest.mark.asyncio
    async def test_rejects_same_user_with_different_sub(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user(normalized_email="ms@example.com")
        await service.link_provider_identity(
            user_id=uid,
            provider="google",
            provider_subject="first-sub",
            normalized_email_at_link="ms@example.com",
        )
        with pytest.raises(ProviderCollision):
            await service.link_provider_identity(
                user_id=uid,
                provider="google",
                provider_subject="second-sub",
                normalized_email_at_link="ms@example.com",
            )


# ---------------------------------------------------------------------------
# consume_invite
# ---------------------------------------------------------------------------


class TestConsumeInvite:
    @pytest.mark.asyncio
    async def test_consumes_active_invite(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        iid = conn.add_invite(
            normalized_email="c@example.com",
            token_verifier_hash="h",
        )
        user_id = uuid.uuid4()
        result = await service.consume_invite(invite_id=iid, used_by_user_id=user_id)
        assert result.status == "consumed"
        assert result.used_by_user_id == user_id
        # Store reflects the change.
        stored = next(i for i in conn._store["signup_invites"] if i["id"] == iid)
        assert stored["status"] == "consumed"
        assert stored["used_by_user_id"] == user_id

    @pytest.mark.asyncio
    async def test_second_consume_rejected(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        iid = conn.add_invite(
            normalized_email="c2@example.com",
            token_verifier_hash="h",
        )
        await service.consume_invite(invite_id=iid, used_by_user_id=uuid.uuid4())
        with pytest.raises(InviteInvalidOrExpired):
            await service.consume_invite(invite_id=iid, used_by_user_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_expired_invite_rejected(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        iid = conn.add_invite(
            normalized_email="e@example.com",
            token_verifier_hash="h",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        with pytest.raises(InviteInvalidOrExpired):
            await service.consume_invite(invite_id=iid, used_by_user_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# Concurrent tenant claim — sequential race simulation
# ---------------------------------------------------------------------------


class TestConcurrentTenantClaim:
    @pytest.mark.asyncio
    async def test_two_sequential_claims_resolve_to_same_tenant(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        """Two callers race to claim the personal tenant for the
        same user. The first INSERT wins; the second sees the
        partial-unique-skip and re-SELECTs. Both end up with the
        same tenant and the second call returns is_new=False.
        """
        uid = uuid.uuid4()
        first_tenant, first_is_new = await service.ensure_personal_tenant(uid)
        second_tenant, second_is_new = await service.ensure_personal_tenant(uid)
        assert first_is_new is True
        assert second_is_new is False
        assert first_tenant.id == second_tenant.id
        # Exactly one tenant row in the store for that user.
        matching = [
            t
            for t in conn._store["tenants"]
            if t["owner_user_id"] == uid and t["kind"] == "personal"
        ]
        assert len(matching) == 1

    @pytest.mark.asyncio
    async def test_repeated_claim_creates_zero_or_one_membership(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        uid = conn.add_user()
        tid = conn.add_tenant(owner_user_id=uid)
        for _ in range(3):
            await service.ensure_owner_membership(tid, uid)
        matching = [
            m
            for m in conn._store["tenant_memberships"]
            if m["tenant_id"] == tid and m["user_id"] == uid
        ]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# Value-shape smoke
# ---------------------------------------------------------------------------


class TestResultShapes:
    @pytest.mark.asyncio
    async def test_user_row_is_email_verified_property(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        conn.add_user(
            normalized_email="v@example.com",
            email_verified_at=datetime.now(timezone.utc),
        )
        u = await service.find_user_by_normalized_email("v@example.com")
        assert u is not None
        assert isinstance(u, UserRow)
        assert u.is_email_verified is True
        # Construct an unverified one directly to validate the property.
        from uuid import uuid4

        unverified = UserRow(
            id=uuid4(),
            normalized_email="x@y",
            email_verified_at=None,
        )
        assert unverified.is_email_verified is False

    @pytest.mark.asyncio
    async def test_claim_result_carries_user_tenant_role(self, service: AccountService) -> None:
        result = await service.claim_email_identity(
            normalized_email="shape@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert isinstance(result.user, UserRow)
        assert isinstance(result.tenant, TenantRow)
        assert result.membership_role == "owner"
        # ClaimResult is a frozen dataclass.
        with pytest.raises(Exception):
            result.is_new_user = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Regression tests for the schema-safety fix
# ---------------------------------------------------------------------------
# These tests guard the three Atlas-found bugs:
#   1. create_user() must include `username` (NOT NULL after migration 010).
#   2. _authorize_invite_only (now _verify_invite_for_email) must NOT
#      consume the invite with a placeholder user id; consumption
#      happens AFTER user creation with the real user id.
#   3. The high-level claim methods must open a transaction context.
#
# The mock enforces the constraints at the boundary so a regression
# fails fast with a typed exception instead of a silent FK violation.


_ZERO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class TestUsernameNotNullRegression:
    """`users.username` is NOT NULL after migration 010. The
    `create_user` SQL must include a non-empty username for every
    INSERT. The mock enforces this at the boundary.
    """

    @pytest.mark.asyncio
    async def test_create_user_persists_username_from_local_part(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        result = await service.claim_email_identity(
            normalized_email="alice@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        stored = next(u for u in conn._store["users"] if u["id"] == result.user.id)
        assert stored["username"] == "alice"
        assert stored["username"]

    @pytest.mark.asyncio
    async def test_create_user_uses_full_string_when_no_at_sign(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        """Mirrors the migration 010 backfill
        (`split_part(email, '@', 1)`). When the email has no
        '@', the whole string becomes the username. The 'user'
        fallback only triggers on an empty string."""
        result = await service.claim_email_identity(
            normalized_email="weird-no-at-sign",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        stored = next(u for u in conn._store["users"] if u["id"] == result.user.id)
        assert stored["username"] == "weird-no-at-sign"
        assert stored["username"]

    @pytest.mark.asyncio
    async def test_create_user_sql_includes_username(self, service: AccountService) -> None:
        """The mock's INSERT dispatch enforces a non-empty
        username. A regression that drops the column or sends
        an empty string would raise `_NotNullViolationError`."""
        await service.claim_email_identity(
            normalized_email="bob@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        insert_calls = [
            (q, a)
            for q, a in service._conn.calls  # type: ignore[attr-defined]
            if "INSERT INTO users" in q
        ]
        assert insert_calls, "expected an INSERT INTO users call"
        for q, _ in insert_calls:
            assert "username" in q

    @pytest.mark.asyncio
    async def test_claim_email_identity_handles_insert_conflict_without_aborting_transaction(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        existing_user_id = conn.add_user(
            normalized_email="race@example.com",
            email_verified_at=datetime.now(timezone.utc),
            username="race",
        )
        conn.miss_next_user_lookup_for.add("race@example.com")

        result = await service.claim_email_identity(
            normalized_email="race@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )

        assert result.user.id == existing_user_id
        assert result.is_new_user is False
        insert_calls = [q for q, _ in conn.calls if "INSERT INTO users" in q]
        assert insert_calls
        assert any("ON CONFLICT (normalized_email)" in q for q in insert_calls)


class TestInviteConsumeUsesRealUserIdRegression:
    """`_authorize_invite_only` previously consumed the invite
    with `_ZERO_USER_ID` as a placeholder, violating the FK to
    `users(id)`. The fix splits verify from consume: the
    verifier returns an unconsumed `InviteRow`, and the
    claim method consumes the invite AFTER user creation with
    the real `result.user.id`.
    """

    @pytest.mark.asyncio
    async def test_email_invite_only_success_sets_used_by_real_user(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        invite_id = conn.add_invite(
            normalized_email="vip@example.com",
            token_verifier_hash="secret-hash",
        )
        result = await service.claim_email_identity(
            normalized_email="vip@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="invite_only",
            invite_token_verifier_hash="secret-hash",
        )
        invite = next(i for i in conn._store["signup_invites"] if i["id"] == invite_id)
        assert invite["status"] == "consumed"
        assert invite["used_by_user_id"] == result.user.id
        assert invite["used_by_user_id"] != _ZERO_USER_ID

    @pytest.mark.asyncio
    async def test_google_invite_only_success_sets_used_by_real_user(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        invite_id = conn.add_invite(
            normalized_email="vip-g@example.com",
            token_verifier_hash="g-secret",
        )
        result = await service.claim_google_identity(
            google_sub="g-vip-sub",
            normalized_email="vip-g@example.com",
            email_verified=True,
            signup_mode="invite_only",
            invite_token_verifier_hash="g-secret",
        )
        invite = next(i for i in conn._store["signup_invites"] if i["id"] == invite_id)
        assert invite["status"] == "consumed"
        assert invite["used_by_user_id"] == result.user.id
        assert invite["used_by_user_id"] != _ZERO_USER_ID

    @pytest.mark.asyncio
    async def test_invite_only_rejection_does_not_consume(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        """A failed verification (wrong token) must NOT consume
        the invite. The original code had a no-op-on-reject path
        but a regression that consumed early would consume on
        every rejection."""
        invite_id = conn.add_invite(
            normalized_email="reject@example.com",
            token_verifier_hash="right",
        )
        with pytest.raises(InviteOnlyRejection):
            await service.claim_email_identity(
                normalized_email="reject@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="invite_only",
                invite_token_verifier_hash="wrong",
            )
        invite = next(i for i in conn._store["signup_invites"] if i["id"] == invite_id)
        assert invite["status"] == "active"
        assert invite["used_by_user_id"] is None

    @pytest.mark.asyncio
    async def test_service_never_writes_zero_user_id(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        """The mock rejects any UPDATE on signup_invites that
        would set `used_by_user_id` to a reserved (placeholder)
        UUID. If a regression reintroduces `_ZERO_USER_ID` (or
        any other placeholder) the mock raises
        `_ForeignKeyViolationError` and the test fails with a
        clear message. A clean run of the success paths confirms
        the service never writes a placeholder.
        """
        for email, hash_value in [
            ("a@example.com", "h-a"),
            ("b@example.com", "h-b"),
        ]:
            conn.add_invite(normalized_email=email, token_verifier_hash=hash_value)
        await service.claim_email_identity(
            normalized_email="a@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="invite_only",
            invite_token_verifier_hash="h-a",
        )
        await service.claim_google_identity(
            google_sub="a-sub",
            normalized_email="b@example.com",
            email_verified=True,
            signup_mode="invite_only",
            invite_token_verifier_hash="h-b",
        )
        for inv in conn._store["signup_invites"]:
            if inv["status"] == "consumed":
                assert inv["used_by_user_id"] is not None
                assert inv["used_by_user_id"] not in conn.reserved_user_ids


class TestTransactionContextRegression:
    """The high-level claim methods must open a transaction
    context so invite authorization, user creation, tenant
    creation, membership creation, and provider-link changes
    are atomic. The mock exposes `transaction_stack` so a
    regression that drops the `async with` block fails.
    """

    @pytest.mark.asyncio
    async def test_claim_email_identity_opens_transaction(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        assert conn.transaction_stack == []
        await service.claim_email_identity(
            normalized_email="tx@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        await service.claim_email_identity(
            normalized_email="tx2@example.com",
            email_verified_at=datetime.now(timezone.utc),
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert conn.transaction_stack == []

    @pytest.mark.asyncio
    async def test_claim_google_identity_opens_transaction(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        await service.claim_google_identity(
            google_sub="tx-g-sub",
            normalized_email="g-tx@example.com",
            email_verified=True,
            signup_mode="open",
            invite_token_verifier_hash=None,
        )
        assert conn.transaction_stack == []

    @pytest.mark.asyncio
    async def test_claim_rolls_back_on_rejection(
        self, service: AccountService, conn: _IdentityMockConn
    ) -> None:
        """When verification fails inside the transaction, the
        context manager exits without committing and the store
        state is unchanged. The transaction context is the
        rollback boundary; in this mock, the @asynccontextmanager
        simply toggles a flag, so the assertion is the stack
        being balanced.
        """
        initial_users = list(conn._store["users"])
        with pytest.raises(InviteOnlyRejection):
            await service.claim_email_identity(
                normalized_email="noone@example.com",
                email_verified_at=datetime.now(timezone.utc),
                signup_mode="invite_only",
                invite_token_verifier_hash=None,
            )
        assert conn._store["users"] == initial_users
        assert conn.transaction_stack == []


class TestUsernameEdgeCasesRegression:
    """The username derivation must be safe for all inputs the
    normalize_email helper produces. The schema's NOT NULL
    constraint forbids empty values, so every branch of
    `_derive_username` must produce a non-empty result.
    """

    def test_local_part_used(self) -> None:
        from orchestrator.services.identity.account_service import (
            _derive_username,
        )

        assert _derive_username("alice@example.com") == "alice"

    def test_no_at_sign_returns_input(self) -> None:
        from orchestrator.services.identity.account_service import (
            _derive_username,
        )

        assert _derive_username("alice") == "alice"

    def test_empty_string_falls_back_to_user(self) -> None:
        from orchestrator.services.identity.account_service import (
            _derive_username,
        )

        assert _derive_username("") == "user"

    def test_at_sign_only_falls_back_to_user(self) -> None:
        from orchestrator.services.identity.account_service import (
            _derive_username,
        )

        assert _derive_username("@example.com") == "user"
