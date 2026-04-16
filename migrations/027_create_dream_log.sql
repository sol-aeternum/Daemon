-- Migration: 027_create_dream_log
-- Creates dream_log table for tracking dreaming synthesis runs

CREATE TABLE IF NOT EXISTS dream_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Run status
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed', 'skipped')),
    
    -- Family tracking
    eligible_families JSONB NOT NULL DEFAULT '[]'::jsonb,
    skipped_families JSONB NOT NULL DEFAULT '[]'::jsonb,
    families_processed INTEGER NOT NULL DEFAULT 0,
    
    -- Results
    observations_created INTEGER NOT NULL DEFAULT 0,
    observation_memory_ids UUID[] NOT NULL DEFAULT '{}',
    
    -- Error tracking
    error_message TEXT,
    
    -- Timing
    run_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_completed_at TIMESTAMPTZ,
    
    -- Model used for synthesis
    model_used TEXT
);

-- Index for user-based lookup
CREATE INDEX IF NOT EXISTS idx_dream_log_user ON dream_log(user_id);

-- Index for temporal queries
CREATE INDEX IF NOT EXISTS idx_dream_log_created_at ON dream_log(run_started_at DESC);

-- Index for status filtering
CREATE INDEX IF NOT EXISTS idx_dream_log_status ON dream_log(status) WHERE status IN ('completed', 'failed');
