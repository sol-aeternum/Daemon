-- Migration: 039_entitlements_commercial
-- Commercial plan + entitlement foundation.
--
-- Scope and safety anchors:
--   - Additive only. No memory table and no video credit table is altered,
--     dropped, renamed or read by this migration.
--   - `users` is the authoritative account identity (UUID primary key); this
--     migration adds a per-user entitlement row and never infers paid state
--     from any global setting.
--   - There is no subscription column on `users` anywhere in this schema and
--     this migration does not add one. A plan only changes through the
--     trusted subscription event log below, or the deterministic legacy
--     per-user import the service exposes.
--   - Budgets are integer microusd (1 USD = 1000000 microusd). Internal
--     currency is USD; displayed prices are configured separately.
--   - The trial is a finite lifetime allowance stored on the account row. It
--     is consumed only by settled usage and has no calendar expiry column,
--     so it cannot silently lapse on a schedule. It carries two independent
--     lifetime counters: a money budget and an extended-agent count. The
--     extended count is separate from any plan's per-period extended quota.
--   - Every plan has a recurring Daemon-funded monthly budget, free included,
--     so routine compute keeps working after the trial is gone. Only
--     premium=True operations draw the trial.
--   - charge_kind 'external' is reserved for a future credential-funding
--     adapter. Nothing writes it today: a capability flag such as byok must
--     not bypass the Daemon-funded budget.
--
-- Period model: usage is accounted per UTC calendar month
-- (`entitlement_period_key`). A reservation is always charged to the period
-- in which it was created, so a reservation that is still outstanding across
-- a month boundary can never be settled into the new month's allowance and
-- cannot overspend it.

-- Deterministic period key shared by application code and SQL.
CREATE OR REPLACE FUNCTION entitlement_period_key(ts TIMESTAMPTZ)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM')
$$;

COMMENT ON FUNCTION entitlement_period_key(TIMESTAMPTZ) IS
    'UTC calendar-month key (YYYY-MM) used to bucket entitlement usage and reservations.';

-- Per-account commercial state. One row per user, created lazily on first
-- resolution with plan=free and an active finite trial.
CREATE TABLE IF NOT EXISTS entitlement_accounts (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan TEXT NOT NULL DEFAULT 'free'
        CHECK (plan IN ('free', 'pro', 'power')),
    plan_source TEXT NOT NULL DEFAULT 'default'
        CHECK (plan_source IN ('default', 'subscription_import', 'admin', 'legacy_import')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended')),
    byok_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    trial_state TEXT NOT NULL DEFAULT 'active'
        CHECK (trial_state IN ('active', 'exhausted')),
    trial_budget_microusd BIGINT NOT NULL DEFAULT 0 CHECK (trial_budget_microusd >= 0),
    trial_consumed_microusd BIGINT NOT NULL DEFAULT 0 CHECK (trial_consumed_microusd >= 0),
    trial_reserved_microusd BIGINT NOT NULL DEFAULT 0 CHECK (trial_reserved_microusd >= 0),
    trial_extended_agents INTEGER NOT NULL DEFAULT 0 CHECK (trial_extended_agents >= 0),
    trial_extended_agents_used INTEGER NOT NULL DEFAULT 0
        CHECK (trial_extended_agents_used >= 0),
    trial_extended_agents_reserved INTEGER NOT NULL DEFAULT 0
        CHECK (trial_extended_agents_reserved >= 0),
    plan_changed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT entitlement_accounts_trial_not_over_reserved
        CHECK (trial_consumed_microusd + trial_reserved_microusd <= trial_budget_microusd),
    CONSTRAINT entitlement_accounts_trial_agents_not_over_reserved
        CHECK (
            trial_extended_agents_used + trial_extended_agents_reserved
                <= trial_extended_agents
        )
);

COMMENT ON TABLE entitlement_accounts IS
    'Authoritative per-user commercial state and the per-user lock that makes '
    'reservation admission atomic (reserve/settle take this row FOR UPDATE). '
    'plan is set only by the trusted subscription event log or an explicit '
    'per-user legacy import.';

COMMENT ON COLUMN entitlement_accounts.byok_enabled IS
    'Set only by an explicit trusted import. A legacy "byok" tier maps to the pro '
    'plan and does not by itself enable this flag. Enabling it grants the byok '
    'capability only: it never switches charge_kind to external, so the '
    'Daemon-funded budget still applies.';

COMMENT ON COLUMN entitlement_accounts.trial_extended_agents IS
    'Lifetime extended-agent count granted with the trial. Independent of any '
    'plan per-period extended allowance, and consumed only by trial-funded work.';

COMMENT ON COLUMN entitlement_accounts.trial_state IS
    'active or exhausted. Driven by settled usage only; there is deliberately no '
    'trial expiry timestamp, so the trial never lapses on a calendar.';

-- Per-period usage counters. spend and reservations are tracked separately so
-- an in-flight reservation already holds its ceiling before it settles.
CREATE TABLE IF NOT EXISTS entitlement_usage_periods (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_key TEXT NOT NULL,
    spent_microusd BIGINT NOT NULL DEFAULT 0 CHECK (spent_microusd >= 0),
    reserved_microusd BIGINT NOT NULL DEFAULT 0 CHECK (reserved_microusd >= 0),
    extended_spent_microusd BIGINT NOT NULL DEFAULT 0 CHECK (extended_spent_microusd >= 0),
    extended_reserved_microusd BIGINT NOT NULL DEFAULT 0
        CHECK (extended_reserved_microusd >= 0),
    extended_agents_used INTEGER NOT NULL DEFAULT 0 CHECK (extended_agents_used >= 0),
    extended_agents_reserved INTEGER NOT NULL DEFAULT 0
        CHECK (extended_agents_reserved >= 0),
    requests_in_window INTEGER NOT NULL DEFAULT 0 CHECK (requests_in_window >= 0),
    window_started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, period_key)
);

COMMENT ON TABLE entitlement_usage_periods IS
    'Per-UTC-month spend, reservation, extended-agent and rate-window counters '
    'for plan-funded work. Rows are created on demand by the reservation '
    'transaction. Trial-funded work is accounted on entitlement_accounts '
    'instead, because the trial is a lifetime allowance.';

-- Reservation ledger: one row per admitted operation.
CREATE TABLE IF NOT EXISTS entitlement_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_key TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('free', 'pro', 'power')),
    operation TEXT NOT NULL,
    charge_kind TEXT NOT NULL CHECK (charge_kind IN ('trial', 'plan', 'external')),
    premium BOOLEAN NOT NULL DEFAULT FALSE,
    extended BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'settled', 'released')),
    reserved_microusd BIGINT NOT NULL CHECK (reserved_microusd >= 0),
    actual_microusd BIGINT CHECK (actual_microusd IS NULL OR actual_microusd >= 0),
    overage_microusd BIGINT NOT NULL DEFAULT 0 CHECK (overage_microusd >= 0),
    provider TEXT,
    model TEXT,
    route_id TEXT,
    usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT entitlement_reservations_settlement_consistent CHECK (
        (status = 'open' AND actual_microusd IS NULL AND settled_at IS NULL)
        OR (status IN ('settled', 'released') AND actual_microusd IS NOT NULL
            AND settled_at IS NOT NULL)
    )
);

COMMENT ON TABLE entitlement_reservations IS
    'Unit-economics ledger, one row per admitted operation. reserved_microusd is '
    'the estimated cost and actual_microusd the settled cost; plan is a snapshot '
    'taken at admission, so historical plan mix survives later plan changes. '
    'No prompt text, context or message content is stored here or in usage.';

COMMENT ON COLUMN entitlement_reservations.provider IS
    'Provider slug that served the operation. NULL when not supplied.';

COMMENT ON COLUMN entitlement_reservations.overage_microusd IS
    'Portion of actual_microusd that exceeded the remaining allowance. An '
    'operation that costs more than it reserved is always settled and closed: '
    'the cost is recorded here, while the allowance counters stop at their '
    'ceiling. A truthful record must never leave a reservation open, because an '
    'open reservation also holds a concurrency slot.';

COMMENT ON COLUMN entitlement_reservations.route_id IS
    'Inference policy route id that served the operation, for joining against the '
    'approved route policy.';

COMMENT ON COLUMN entitlement_reservations.usage IS
    'Scalar usage counters only (tokens, tool calls, duration). Keys are '
    'allowlisted in code; never prompt, context or message content.';

-- Open reservations are the concurrency accounting unit: a user may hold at
-- most `max_concurrent_operations` of them at once.
CREATE INDEX IF NOT EXISTS idx_entitlement_reservations_open
    ON entitlement_reservations(user_id)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_entitlement_reservations_user_created
    ON entitlement_reservations(user_id, created_at DESC);

-- Idempotency ledger for trusted subscription/import events. There is no
-- public HTTP surface for this table; events are only ever written by the
-- service helper.
CREATE TABLE IF NOT EXISTS entitlement_subscription_events (
    event_id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('free', 'pro', 'power')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    byok BOOLEAN,
    previous_plan TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entitlement_events_user_occurred
    ON entitlement_subscription_events(user_id, occurred_at DESC, event_id);

COMMENT ON COLUMN entitlement_subscription_events.metadata IS
    'Allowlisted, length-capped event metadata only (for example the legacy tier '
    'name or an external reference). Never prompt, context or message content.';

COMMENT ON COLUMN entitlement_subscription_events.occurred_at IS
    'Effective event time (the supplied time, or the receive time when none was '
    'supplied). Enforces monotonic plan ordering per user: an event older than '
    'the newest already-applied event for that user is refused rather than '
    'allowed to regress the plan.';

CREATE INDEX IF NOT EXISTS idx_entitlement_events_user_received
    ON entitlement_subscription_events(user_id, received_at DESC);

COMMENT ON TABLE entitlement_subscription_events IS
    'Idempotency and audit ledger for trusted plan-change events. Replaying an '
    'event_id is a no-op; reusing an event_id with a different payload is a '
    'conflict. No unauthenticated or public endpoint may write here.';

COMMENT ON COLUMN entitlement_subscription_events.previous_plan IS
    'Plan held before the event was applied, recorded for audit and replay.';

-- Which ceilings actually bind, and for whom.
--
-- A refused operation is the clearest signal about plan design: if most
-- refusals are concurrency rather than budget, the plan needs a higher
-- concurrency ceiling, not a bigger wallet. Only codes, counters and the
-- limits in force are recorded -- never prompt, context or message content.
CREATE TABLE IF NOT EXISTS entitlement_limit_encounters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_key TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('free', 'pro', 'power')),
    operation TEXT,
    denial_code TEXT NOT NULL CHECK (
        denial_code IN (
            'budget_exceeded',
            'concurrency_exceeded',
            'rate_limited',
            'extended_agents_exceeded',
            'extended_budget_exceeded',
            'trial_exhausted',
            'trial_extended_agents_exhausted',
            'capability_denied'
        )
    ),
    capability TEXT,
    requested_microusd BIGINT CHECK (requested_microusd IS NULL OR requested_microusd >= 0),
    limits_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entitlement_encounters_period_code
    ON entitlement_limit_encounters(period_key, denial_code, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_entitlement_encounters_user_created
    ON entitlement_limit_encounters(user_id, created_at DESC);

COMMENT ON TABLE entitlement_limit_encounters IS
    'Record of refused operations: the ceiling that bound, the plan in force and '
    'the observed counters. Contains no prompt, context or message content.';

-- ---------------------------------------------------------------------------
-- Aggregate views.
--
-- Enough to answer the unit-economics questions (how many active users, what
-- an operation costs, how much of it is premium or trial-funded, which
-- ceilings bind) with plain SQL. No analytics platform, no per-request
-- telemetry pipeline.
-- ---------------------------------------------------------------------------

-- Current per-account commercial state plus this account's latest period usage.
CREATE OR REPLACE VIEW v_entitlement_account_summary AS
SELECT
    a.user_id,
    a.plan,
    a.plan_source,
    a.status,
    a.byok_enabled,
    a.trial_state,
    a.trial_budget_microusd,
    a.trial_consumed_microusd,
    a.trial_reserved_microusd,
    a.trial_budget_microusd - a.trial_consumed_microusd - a.trial_reserved_microusd
        AS trial_remaining_microusd,
    a.trial_extended_agents,
    a.trial_extended_agents_used,
    a.trial_extended_agents_reserved,
    a.trial_extended_agents - a.trial_extended_agents_used - a.trial_extended_agents_reserved
        AS trial_extended_agents_remaining,
    latest.period_key,
    COALESCE(latest.spent_microusd, 0) AS period_spent_microusd,
    COALESCE(latest.reserved_microusd, 0) AS period_reserved_microusd,
    COALESCE(latest.extended_spent_microusd, 0) AS period_extended_spent_microusd,
    (
        SELECT count(*) FROM entitlement_reservations r
        WHERE r.user_id = a.user_id AND r.status = 'open'
    ) AS open_reservations,
    a.updated_at
FROM entitlement_accounts a
LEFT JOIN LATERAL (
    SELECT p.period_key, p.spent_microusd, p.reserved_microusd, p.extended_spent_microusd
    FROM entitlement_usage_periods p
    WHERE p.user_id = a.user_id
    ORDER BY p.period_key DESC
    LIMIT 1
) latest ON TRUE;

COMMENT ON VIEW v_entitlement_account_summary IS
    'One row per account: current plan, BYOK flag, trial state, and the latest '
    'period usage counters.';

-- Per period and plan snapshot: active users, operation mix and cost shape.
CREATE OR REPLACE VIEW v_entitlement_period_activity AS
SELECT
    r.period_key,
    r.plan,
    count(DISTINCT r.user_id) AS active_users,
    count(*) AS operations,
    count(*) FILTER (WHERE r.premium) AS premium_operations,
    count(*) FILTER (WHERE r.charge_kind = 'trial') AS trial_operations,
    count(*) FILTER (WHERE r.charge_kind = 'external') AS external_operations,
    count(*) FILTER (WHERE r.extended) AS extended_operations,
    count(*) FILTER (WHERE r.status = 'settled') AS settled_operations,
    count(*) FILTER (WHERE r.status = 'open') AS open_operations,
    COALESCE(avg(r.actual_microusd) FILTER (WHERE r.status = 'settled'), 0)
        AS avg_actual_microusd,
    COALESCE(
        percentile_cont(0.5) WITHIN GROUP (ORDER BY r.actual_microusd)
            FILTER (WHERE r.status = 'settled'),
        0
    ) AS p50_actual_microusd,
    COALESCE(
        percentile_cont(0.95) WITHIN GROUP (ORDER BY r.actual_microusd)
            FILTER (WHERE r.status = 'settled'),
        0
    ) AS p95_actual_microusd,
    COALESCE(max(r.actual_microusd) FILTER (WHERE r.status = 'settled'), 0)
        AS max_actual_microusd,
    COALESCE(sum(r.actual_microusd) FILTER (WHERE r.status = 'settled'), 0)
        AS total_actual_microusd,
    COALESCE(sum(r.overage_microusd), 0) AS total_overage_microusd,
    COALESCE(sum(r.reserved_microusd) FILTER (WHERE r.status = 'open'), 0)
        AS outstanding_reserved_microusd
FROM entitlement_reservations r
GROUP BY r.period_key, r.plan;

COMMENT ON VIEW v_entitlement_period_activity IS
    'Per period and plan snapshot: active users, premium/trial/external mix, '
    'avg/p50/p95/max settled cost per operation, and any settled overage.';

-- Trial funnel and conversion inputs, recomputed on demand.
CREATE OR REPLACE VIEW v_entitlement_trial_funnel AS
SELECT
    count(*) AS accounts,
    count(*) FILTER (WHERE plan = 'free') AS free_accounts,
    count(*) FILTER (WHERE plan IN ('pro', 'power')) AS paying_accounts,
    count(*) FILTER (WHERE byok_enabled) AS byok_accounts,
    count(*) FILTER (WHERE trial_state = 'active') AS trial_active_accounts,
    count(*) FILTER (WHERE trial_state = 'exhausted') AS trial_exhausted_accounts,
    COALESCE(sum(trial_consumed_microusd), 0) AS trial_consumed_microusd,
    COALESCE(
        sum(trial_budget_microusd - trial_consumed_microusd - trial_reserved_microusd), 0
    ) AS trial_remaining_microusd
FROM entitlement_accounts;

COMMENT ON VIEW v_entitlement_trial_funnel IS
    'Account counts by plan and trial state, plus total trial consumed and still '
    'remaining. No calendar expiry means this is a pure consumption funnel.';

-- Which ceilings bind, aggregated.
CREATE OR REPLACE VIEW v_entitlement_limit_encounter_summary AS
SELECT
    e.period_key,
    e.denial_code,
    e.plan,
    e.operation,
    count(*) AS encounters,
    count(DISTINCT e.user_id) AS affected_users,
    min(e.created_at) AS first_seen_at,
    max(e.created_at) AS last_seen_at
FROM entitlement_limit_encounters e
GROUP BY e.period_key, e.denial_code, e.plan, e.operation;

COMMENT ON VIEW v_entitlement_limit_encounter_summary IS
    'Encounter counts per period, ceiling, plan and operation, for plan tuning.';
