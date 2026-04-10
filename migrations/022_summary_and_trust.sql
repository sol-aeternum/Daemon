-- Migration: 022_summary_and_trust
-- Adds summary and last_retrieved_memory_ids to conversations
-- Adds trust_score, last_retrieved_at, and tier to memories
--
-- Idempotent: uses IF NOT EXISTS for all column additions

-- Add summary text field to conversations
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary TEXT;

-- Add last_retrieved_memory_ids JSONB array to conversations
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS last_retrieved_memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Add trust_score with CHECK constraint (0.0 to 1.0)
ALTER TABLE memories ADD COLUMN IF NOT EXISTS trust_score FLOAT NOT NULL DEFAULT 0.5;
ALTER TABLE memories DROP CONSTRAINT IF EXISTS ck_memories_trust_score_range;
ALTER TABLE memories ADD CONSTRAINT ck_memories_trust_score_range CHECK (trust_score >= 0.0 AND trust_score <= 1.0);

-- Add last_retrieved_at timestamp to memories
ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_retrieved_at TIMESTAMPTZ;

-- Add tier column for L0/L1/L2 tier support (VARCHAR(10))
ALTER TABLE memories ADD COLUMN IF NOT EXISTS tier VARCHAR(10) NOT NULL DEFAULT 'l1';
ALTER TABLE memories DROP CONSTRAINT IF EXISTS ck_memories_tier_values;
ALTER TABLE memories ADD CONSTRAINT ck_memories_tier_values CHECK (tier IN ('l0', 'l1', 'l2'));

ANALYZE conversations;
ANALYZE memories;
