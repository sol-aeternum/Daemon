-- Council sessions table for multi-agent deliberation
CREATE TABLE IF NOT EXISTS council_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    rounds JSONB NOT NULL DEFAULT '[]',
    audit_findings JSONB NOT NULL DEFAULT '[]',
    token_costs JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for council sessions by conversation (most common lookup)
CREATE INDEX IF NOT EXISTS idx_council_sessions_conversation_id ON council_sessions(conversation_id);

-- Index for updated_at (for sync/audit queries)
CREATE INDEX IF NOT EXISTS idx_council_sessions_updated_at ON council_sessions(updated_at);

-- Index for created_at (for time-based queries)
CREATE INDEX IF NOT EXISTS idx_council_sessions_created_at ON council_sessions(created_at);

ANALYZE council_sessions;