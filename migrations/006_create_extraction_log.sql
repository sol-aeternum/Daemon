-- Memory extraction log table (for debugging and metrics)
CREATE TABLE IF NOT EXISTS memory_extraction_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    input_snippet TEXT NOT NULL, -- First 500 chars of input for debugging
    extracted_facts JSONB DEFAULT '[]'::jsonb, -- Array of extracted fact objects
    dedup_results JSONB DEFAULT '{}'::jsonb, -- Dedup analysis results
    model_used TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for conversation-based lookup
CREATE INDEX IF NOT EXISTS idx_extraction_log_conversation ON memory_extraction_log(conversation_id);

-- Index for user-based lookup
CREATE INDEX IF NOT EXISTS idx_extraction_log_user ON memory_extraction_log(user_id);

-- Index for temporal queries
CREATE INDEX IF NOT EXISTS idx_extraction_log_created_at ON memory_extraction_log(created_at DESC);

-- GIN index for extracted_facts JSONB queries
CREATE INDEX IF NOT EXISTS idx_extraction_log_facts ON memory_extraction_log USING gin(extracted_facts);
