"""Static guards on the entitlement migration.

These assert the properties that are easy to lose in a later edit: the
migration is additive, the trial has no expiry column, ceilings are enforced in
the database, and nothing about the legacy global tier leaks into the schema.
Applying the SQL and exercising it is covered by ``test_entitlements_service``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "039_entitlements_commercial.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_number_is_the_next_free_one() -> None:
    numbers = sorted(
        int(match.group(1))
        for path in (ROOT / "migrations").glob("*.sql")
        if (match := re.match(r"^(\d+)_", path.name))
    )

    # Historical migrations share prefix 036 and the runner keys by filename.
    # Preserve that history while checking continuity and our new prefix's uniqueness.
    assert sorted(set(numbers)) == list(range(1, max(numbers) + 1)), "migration numbering has a gap"
    assert numbers.count(max(numbers)) == 1, "new migration prefix is already occupied"
    assert MIGRATION.name.startswith(f"{max(numbers):03d}_")


def test_migration_is_additive_and_preserves_memory_and_credit_tables() -> None:
    sql = _sql()

    assert not re.search(r"^\s*(ALTER|DROP|TRUNCATE)\s+", sql, flags=re.MULTILINE | re.IGNORECASE)
    for preserved in ("memories", "video_credit_balances", "video_credit_transactions"):
        assert preserved not in sql
    # `users` is referenced only as the authoritative identity for the FKs.
    assert set(re.findall(r"REFERENCES\s+(\w+)", sql)) == {"users"}


def test_migration_never_mentions_the_legacy_tier_switch() -> None:
    sql = _sql()

    assert "default_tier" not in sql.lower()
    assert "DEFAULT_TIER" not in sql
    # The retired `starter` tier is gone entirely; `max`/`byok` survive only as
    # SQL identifiers (`max()`, `byok_enabled`), never as a plan value.
    assert "starter" not in sql.lower()
    for match in re.finditer(r"CHECK \(plan IN \(([^)]*)\)\)", sql):
        assert "starter" not in match.group(1)
        assert "max" not in match.group(1)
        assert "byok" not in match.group(1)
    assert 'A legacy "byok" tier maps to the pro' in sql


def test_trial_has_no_calendar_expiry_column() -> None:
    sql = _sql()
    account_block = sql.split("CREATE TABLE IF NOT EXISTS entitlement_accounts")[1].split(");")[0]

    assert "trial_state" in account_block
    assert "trial_budget_microusd" in account_block
    for forbidden in ("trial_expires_at", "trial_expires", "trial_started_at", "trial_ends_at"):
        assert forbidden not in account_block
    assert "expires" not in account_block


def test_plan_values_are_constrained_to_the_three_commercial_plans() -> None:
    sql = _sql()

    for match in re.finditer(r"plan TEXT NOT NULL[^\n]*\n\s*CHECK \(plan IN \(([^)]*)\)\)", sql):
        assert set(re.findall(r"'([^']+)'", match.group(1))) == {"free", "pro", "power"}


def test_accounts_table_keeps_consumption_inside_the_trial_budget() -> None:
    sql = _sql()

    assert (
        "CONSTRAINT entitlement_accounts_trial_not_over_reserved\n"
        "        CHECK (trial_consumed_microusd + trial_reserved_microusd <= trial_budget_microusd)"
    ) in sql


def test_reservations_cannot_be_half_settled() -> None:
    sql = _sql()

    assert "CONSTRAINT entitlement_reservations_settlement_consistent CHECK (" in sql
    assert "(status = 'open' AND actual_microusd IS NULL AND settled_at IS NULL)" in sql
    assert "status IN ('settled', 'released') AND actual_microusd IS NOT NULL" in sql


def test_concurrency_index_is_a_literal_partial_index() -> None:
    sql = _sql()

    assert "CREATE INDEX IF NOT EXISTS idx_entitlement_reservations_open" in sql
    assert "ON entitlement_reservations(user_id)\n    WHERE status = 'open';" in sql
    # A NOW() predicate in an index is not immutable and will not be accepted.
    assert "WHERE status = 'open' AND" not in sql


def test_period_key_helper_is_immutable_and_utc() -> None:
    sql = _sql()

    assert "CREATE OR REPLACE FUNCTION entitlement_period_key(ts TIMESTAMPTZ)" in sql
    assert "LANGUAGE sql\nIMMUTABLE" in sql
    assert "to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM')" in sql


def test_hold_and_settle_sql_enforce_ceilings_in_the_database() -> None:
    """The ceilings must be WHERE-clause predicates, not application-only checks."""
    store = (ROOT / "orchestrator" / "entitlements" / "store.py").read_text(encoding="utf-8")

    assert "UPDATE entitlement_usage_periods AS p" in store
    assert "(p.spent_microusd + p.reserved_microusd + $3) <= $6" in store
    assert "p.extended_agents_used + p.extended_agents_reserved + 1) <= $10" in store
    assert "p.requests_in_window < $8" in store
    # Concurrency counts open foreground operations (reservations grouped by
    # account scope), not individual LLM calls.
    assert "count(DISTINCT COALESCE(r.scope_id, r.id))" in store
    assert "AND NOT r.background\n            ) < $9" in store
    # The per-user serialization point every reserve/settle transaction takes.
    assert "FROM entitlement_accounts WHERE user_id = $1 FOR UPDATE" in store


def test_settlement_is_guarded_by_status_for_idempotency() -> None:
    store = (ROOT / "orchestrator" / "entitlements" / "store.py").read_text(encoding="utf-8")

    assert "WHERE id = $1 AND status = 'open'" in store
    assert "ON CONFLICT (event_id) DO NOTHING" in store


def test_economics_ledger_records_the_unit_economics_fields() -> None:
    sql = _sql()
    reservations = sql.split("CREATE TABLE IF NOT EXISTS entitlement_reservations")[1].split(");")[
        0
    ]

    for column in (
        "plan TEXT NOT NULL",
        "operation TEXT NOT NULL",
        "premium BOOLEAN NOT NULL",
        "extended BOOLEAN NOT NULL",
        "charge_kind TEXT NOT NULL",
        "reserved_microusd BIGINT NOT NULL",
        "actual_microusd BIGINT",
        "provider TEXT",
        "model TEXT",
        "route_id TEXT",
    ):
        assert column in reservations, column


def test_no_prompt_or_context_content_is_stored() -> None:
    sql = _sql().lower()
    reservations = sql.split("create table if not exists entitlement_reservations")[1].split(");")[
        0
    ]

    for forbidden in (
        "prompt_text",
        "context_text",
        "message_content",
        "content text",
        "ciphertext",
    ):
        assert forbidden not in sql
    assert "usage jsonb" in reservations


def test_limit_encounters_store_codes_not_content() -> None:
    sql = _sql()
    encounters = sql.split("CREATE TABLE IF NOT EXISTS entitlement_limit_encounters")[1].split(
        ");"
    )[0]

    assert "denial_code TEXT NOT NULL" in encounters
    assert "limits_snapshot JSONB" in encounters
    assert "observed JSONB" in encounters
    for code in (
        "budget_exceeded",
        "concurrency_exceeded",
        "rate_limited",
        "extended_agents_exceeded",
        "extended_budget_exceeded",
        "trial_exhausted",
        "capability_denied",
    ):
        assert f"'{code}'" in encounters


def test_aggregate_views_cover_active_users_cost_shape_and_trial_funnel() -> None:
    sql = _sql()

    for view in (
        "v_entitlement_account_summary",
        "v_entitlement_period_activity",
        "v_entitlement_trial_funnel",
        "v_entitlement_limit_encounter_summary",
    ):
        assert f"CREATE OR REPLACE VIEW {view} AS" in sql

    activity = sql.split("CREATE OR REPLACE VIEW v_entitlement_period_activity AS")[1].split(
        "COMMENT ON VIEW"
    )[0]
    assert "count(DISTINCT r.user_id) AS active_users" in activity
    assert "percentile_cont(0.95) WITHIN GROUP (ORDER BY r.actual_microusd)" in activity
    assert "avg(r.actual_microusd)" in activity
    assert "r.charge_kind = 'trial'" in activity
    assert "r.premium" in activity


def test_subscription_events_table_documents_the_absence_of_a_public_endpoint() -> None:
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS entitlement_subscription_events" in sql
    assert "event_id TEXT PRIMARY KEY" in sql
    assert "previous_plan TEXT" in sql
    assert "No unauthenticated or public endpoint may write here" in sql
