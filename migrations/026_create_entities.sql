-- Migration: 026_create_entities
-- Creates entities table for best-effort entity resolution
-- Stores canonical names (encrypted), normalized lookup keys, aliases, and memory links

CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Canonical name stored encrypted (user-derived text)
    canonical_name TEXT NOT NULL,
    
    -- Normalized lookup key derived from canonical_name (lowercase, stripped, no special chars)
    -- Stored as plaintext for efficient searching without decryption
    lookup_key TEXT NOT NULL,
    
    -- Aliases stored encrypted for privacy; lookup keys stored separately for searching
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    alias_lookup_keys TEXT[] NOT NULL DEFAULT '{}',
    
    -- Linked memories for entity-aware retrieval expansion
    linked_memory_ids UUID[] NOT NULL DEFAULT '{}',
    
    -- Source tracking
    source_memory_id UUID REFERENCES memories(id) ON DELETE SET NULL,
    first_mentioned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unique constraint on lookup_key per user (one canonical entity per normalized name)
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_user_lookup_key ON entities(user_id, lookup_key);

-- Index for user-based entity listing
CREATE INDEX IF NOT EXISTS idx_entities_user ON entities(user_id);

-- Index for alias lookup
CREATE INDEX IF NOT EXISTS idx_entities_alias_lookup_keys ON entities USING gin(alias_lookup_keys);

-- Index for linked memory lookups
CREATE INDEX IF NOT EXISTS idx_entities_linked_memories ON entities USING gin(linked_memory_ids);

-- Index for temporal queries
CREATE INDEX IF NOT EXISTS idx_entities_created_at ON entities(created_at DESC);
