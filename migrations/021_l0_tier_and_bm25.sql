-- Migration: 021_l0_tier_and_bm25
-- Adds L0 tier support and BM25 full-text search via tsvector
--
-- IMPORTANT: content is encrypted via Fernet before reaching PostgreSQL.
-- content_tsv must be populated in application code, NOT via a trigger.
-- In Python: decrypt content, then UPDATE memories SET content_tsv = to_tsvector('english', decrypted)

-- Add tier column for L0/L1/L2 tier support
ALTER TABLE memories ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'l1'
CHECK (tier IN ('l0', 'l1', 'l2'));

-- Add content_tsv tsvector column for BM25 full-text search
-- Populated in application code after Fernet decryption
ALTER TABLE memories ADD COLUMN IF NOT EXISTS content_tsv tsvector;

-- Create GIN index on content_tsv for fast BM25 lookups
CREATE INDEX IF NOT EXISTS idx_memories_content_tsv ON memories USING GIN (content_tsv);

COMMENT ON COLUMN memories.content_tsv IS
'TSVECTOR for BM25 search. Populated in application code after Fernet decryption. NOT via trigger.';

ANALYZE memories;
