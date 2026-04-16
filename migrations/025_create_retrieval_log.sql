-- Migration: 025_create_retrieval_log
-- Creates retrieval_log table for retrieval trajectory diagnostics
-- Captures query text, embedding, candidate scores, selected results, latency

CREATE TABLE IF NOT EXISTS retrieval_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    
    -- Query information
    query_text TEXT NOT NULL,
    query_embedding_model TEXT NOT NULL,
    query_embedding vector(1024),
    
    -- Candidate information
    candidate_memory_ids UUID[] NOT NULL DEFAULT '{}',
    candidate_scores JSONB NOT NULL DEFAULT '{}',
    
    -- Selection information
    selected_memory_ids UUID[] NOT NULL DEFAULT '{}',
    l0_included BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Performance
    latency_ms INTEGER NOT NULL,
    
    -- Context
    retrieval_context TEXT,
    retrieval_triggered_by TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for user-based lookup
CREATE INDEX IF NOT EXISTS idx_retrieval_log_user ON retrieval_log(user_id);

-- Index for conversation-based lookup
CREATE INDEX IF NOT EXISTS idx_retrieval_log_conversation ON retrieval_log(conversation_id);

-- Index for temporal queries
CREATE INDEX IF NOT EXISTS idx_retrieval_log_created_at ON retrieval_log(created_at DESC);

-- Composite index for analytics queries
CREATE INDEX IF NOT EXISTS idx_retrieval_log_user_created ON retrieval_log(user_id, created_at DESC);
