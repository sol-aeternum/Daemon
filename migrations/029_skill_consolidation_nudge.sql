-- Migration: 029_skill_consolidation_nudge
-- Creates tables for skill consolidation nudge audit logging and
-- per-user conversation count tracking to trigger consolidation at intervals.

-- Table: skill_consolidation_log
-- Audit log of all consolidation nudge actions taken
CREATE TABLE IF NOT EXISTS skill_consolidation_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- User and timing
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_id UUID NOT NULL DEFAULT gen_random_uuid(),
    run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Action details
    action_type TEXT NOT NULL CHECK (action_type IN (
        'merge', 'delete', 'flag_stale', 'suggest_promotion', 'skip'
    )),
    skill_id TEXT,
    target_skill_id TEXT,

    -- Context
    reason TEXT NOT NULL DEFAULT '',
    similarity DOUBLE PRECISION,

    -- Result status
    status TEXT NOT NULL CHECK (status IN (
        'applied', 'skipped', 'recorded', 'failed'
    )) DEFAULT 'recorded',

    -- Skill metadata at time of action
    skill_name TEXT,
    skill_description TEXT,
    skill_use_count INTEGER,
    skill_last_used_at TIMESTAMPTZ
);

-- Table: skill_nudge_user_state
-- Per-user tracking of conversation count since last consolidation nudge
CREATE TABLE IF NOT EXISTS skill_nudge_user_state (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    -- Conversation counter since last nudge
    conversations_since_nudge INTEGER NOT NULL DEFAULT 0,

    -- Last nudge run timestamp
    last_nudge_at TIMESTAMPTZ,

    -- Last nudge run_id for correlation
    last_nudge_run_id UUID
);

-- Indexes for consolidation log
CREATE INDEX IF NOT EXISTS idx_skill_consolidation_log_user
    ON skill_consolidation_log(user_id);

CREATE INDEX IF NOT EXISTS idx_skill_consolidation_log_run
    ON skill_consolidation_log(run_id);

CREATE INDEX IF NOT EXISTS idx_skill_consolidation_log_action_type
    ON skill_consolidation_log(action_type)
    WHERE action_type IN ('merge', 'delete', 'flag_stale');

CREATE INDEX IF NOT EXISTS idx_skill_consolidation_log_skill
    ON skill_consolidation_log(skill_id)
    WHERE skill_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_skill_consolidation_log_run_at
    ON skill_consolidation_log(run_at DESC);

-- Index for user state lookup
CREATE INDEX IF NOT EXISTS idx_skill_nudge_user_state_user
    ON skill_nudge_user_state(user_id);

COMMENT ON TABLE skill_consolidation_log IS
    'Audit log for skill consolidation nudge actions. Records merge, delete, '
    'flag_stale, and suggest_promotion actions with structured metadata.';

COMMENT ON TABLE skill_nudge_user_state IS
    'Per-user conversation counter for triggering consolidation nudge at intervals.';