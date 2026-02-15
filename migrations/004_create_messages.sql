-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL, -- Encrypted via application layer (AES-256-GCM)
    model TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tool_calls JSONB DEFAULT '[]'::jsonb,
    tool_results JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for conversation messages lookup (chronological order)
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id, created_at);

-- Index for user messages lookup
CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);

-- Index for role-based queries (e.g., find all assistant messages)
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);

-- Index for created_at (temporal queries)
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);

-- GIN index for tool_calls JSONB queries
CREATE INDEX IF NOT EXISTS idx_messages_tool_calls ON messages USING gin(tool_calls);

-- GIN index for tool_results JSONB queries
CREATE INDEX IF NOT EXISTS idx_messages_tool_results ON messages USING gin(tool_results);
