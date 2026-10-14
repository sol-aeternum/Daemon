"""Birth-only entitlement initialization, never a missing-account repair API.

The successful user INSERT on this connection is the authority. Its transaction
also owns defaults, tenant/session creation and commit; no caller boolean, absent
entitlement, timestamp or CapturedGeneration establishes birth. Existing identity
claims and every ordinary entitlement consumer remain non-provisioning.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from orchestrator.config import Settings
from orchestrator.entitlements.policy import load_policy
from orchestrator.services.account_control.store import Connection, account_lock_key
from orchestrator.services.deletion_journal.barrier import (
    _io,
    _verify_schema,
    configured_journal,
    verify_applied,
)
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.store import Journal

SINGLETON_ID = UUID("00000000-0000-0000-0000-000000000001")


class IdentityInitializationRefused(Exception):
    """Safe opaque refusal; database errors/cancellation also deny initialization."""


async def require_existing_entitlement(conn: Connection, user_id: UUID) -> None:
    """Presence only: sign-in never activates/refills or repairs account state."""
    if (
        await conn.fetchval("SELECT user_id FROM entitlement_accounts WHERE user_id=$1", user_id)
        is None
    ):
        raise IdentityInitializationRefused("missing_entitlement")


async def _verify_birth_history(conn: Connection, settings: Settings, uid: UUID) -> None:
    # Never wait for account control after touching a published identity row.
    if not conn.is_in_transaction():
        raise IdentityInitializationRefused("transaction_required")
    if await conn.fetchval("SHOW transaction_isolation") != "read committed":
        raise IdentityInitializationRefused("unsupported_isolation")
    await conn.execute("SELECT pg_advisory_xact_lock($1)", account_lock_key(uid))
    account = await conn.fetchrow(
        "SELECT user_id FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", uid
    )
    # Separate statements after possibly waiting locks see committed subordinate
    # state. ANY history, including closed operations, disqualifies a UUID.
    operations = await conn.fetch(
        "SELECT id FROM account_deletions WHERE account_id=$1 FOR UPDATE", uid
    )
    evidence = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM account_suspension_causes WHERE account_id=$1) "
        "OR EXISTS(SELECT 1 FROM entitlement_usage_periods WHERE user_id=$1) "
        "OR EXISTS(SELECT 1 FROM entitlement_reservations WHERE user_id=$1) "
        "OR EXISTS(SELECT 1 FROM entitlement_subscription_events WHERE user_id=$1)",
        uid,
    )
    if account is not None or operations or evidence:
        raise IdentityInitializationRefused("account_history")
    config = configured_journal(settings)
    if not await _verify_schema(conn, config is not None):
        raise JournalError("schema_required")
    binding = await conn.fetch("SELECT singleton,journal_id FROM deletion_journal_binding")
    if config is None:
        if binding or await conn.fetchval("SELECT EXISTS(SELECT 1 FROM deletion_journal_applied)"):
            raise JournalError("invalid_config")
        return
    directory, identity = config
    # Startup, not initialization, owns binding creation. Never release readiness
    # or synthesize an applied marker as a side effect of creating an identity.
    if (
        len(binding) != 1
        or binding[0]["singleton"] is not True
        or binding[0]["journal_id"] != identity
    ):
        raise JournalError("marker_mismatch")

    def scan():
        with Journal(directory, identity) as journal:
            return journal.verify()

    view = await _io(scan)
    await verify_applied(conn, view)
    if any(entry.command.account_id == uid for entry in view.entries):
        raise IdentityInitializationRefused("account_history")


async def insert_hosted_identity(
    conn: Connection,
    settings: Settings,
    *,
    normalized_email: str,
    username: str,
    email_verified_at: datetime,
) -> Mapping[str, Any] | None:
    """Claim caller already proved identity/invite policy; conflict is NOT birth."""
    return await _insert_identity(conn, settings, normalized_email, username, email_verified_at)


async def _insert_identity(
    conn: Connection,
    settings: Settings,
    email: str,
    username: str,
    verified_at: datetime,
) -> Mapping[str, Any] | None:
    if not conn.is_in_transaction():
        raise IdentityInitializationRefused("transaction_required")
    uid = uuid4()
    if not isinstance(uid, UUID) or uid == SINGLETON_ID:
        raise IdentityInitializationRefused("reserved_identity")
    # Savepoint also prevents partial helper writes if an internal caller catches
    # an exception; the OUTER identity/session transaction retains locks to commit.
    async with conn.transaction():
        await _verify_birth_history(conn, settings, uid)
        row = await conn.fetchrow(
            "INSERT INTO users (id,username,email,name,normalized_email,email_verified_at) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (normalized_email) "
            "WHERE normalized_email IS NOT NULL DO NOTHING "
            "RETURNING id,normalized_email,email_verified_at",
            uid,
            username,
            email,
            email,
            email,
            verified_at,
        )
        if row is None:
            # The concurrent winner owns initialization. Never repair its row.
            return None
        if row["id"] != uid:
            raise IdentityInitializationRefused("identity_mismatch")
        policy = load_policy()
        budget = policy.trial.budget_for_new_account()
        await conn.execute(
            "INSERT INTO entitlement_accounts "
            "(user_id,plan,plan_source,status,byok_enabled,trial_state,trial_budget_microusd,"
            "trial_extended_agents) VALUES ($1,'free','default','active',FALSE,$2,$3,$4)",
            uid,
            "active" if budget > 0 else "exhausted",
            budget,
            policy.trial.extended_agents_for_new_account(),
        )
        return row
