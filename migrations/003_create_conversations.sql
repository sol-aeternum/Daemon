-- Conversations table
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pipeline TEXT NOT NULL CHECK (pipeline IN ('cloud', 'local')),
    title TEXT,
    summary TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    tokens_total INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for user conversations lookup (most common query)
CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);

-- Index for recent conversations (sorted by last_activity_at)
CREATE INDEX IF NOT EXISTS idx_conversations_last_activity ON conversations(user_id, last_activity_at DESC);

-- Index for pipeline-specific queries
CREATE INDEX IF NOT EXISTS idx_conversations_pipeline ON conversations(pipeline);

-- Index for updated_at (for sync/audit queries)
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);
