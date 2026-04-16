-- Migration: 024_extend_memories_for_reasoning
-- Adds 'observation' to category, 'dream' to source_type, and entity_ids column
-- Supports Tier 3 reasoning layer: dreaming and entity resolution
--
-- Idempotent: drops and re-adds constraints with new values

-- Add 'observation' to category enum
ALTER TABLE memories
    DROP CONSTRAINT IF EXISTS memories_category_check;

ALTER TABLE memories
    ADD CONSTRAINT memories_category_check
    CHECK (category IN (
        'fact',
        'preference',
        'project',
        'summary',
        'correction',
        'observation'
    ));

-- Add 'dream' to source_type enum
ALTER TABLE memories
    DROP CONSTRAINT IF EXISTS memories_source_type_check;

ALTER TABLE memories
    ADD CONSTRAINT memories_source_type_check
    CHECK (source_type IN (
        'conversation',
        'manual',
        'import',
        'extracted',
        'user_confirmed',
        'user_corrected',
        'user_created',
        'bootstrapped',
        'consolidation',
        'dream'
    ));

-- Add entity_ids column: references to entity rows for entity-aware retrieval
-- Stored as JSONB array of UUID strings to avoid join overhead in typical queries
ALTER TABLE memories ADD COLUMN IF NOT EXISTS entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Index for entity-based lookups (when searching memories linked to an entity)
CREATE INDEX IF NOT EXISTS idx_memories_entity_ids ON memories USING gin(entity_ids);

-- Index for finding memories by entity membership efficiently
CREATE INDEX IF NOT EXISTS idx_memories_entity_contains ON memories USING gin(entity_ids) WHERE jsonb_array_length(entity_ids) > 0;

COMMENT ON COLUMN memories.entity_ids IS
'JSONB array of entity UUIDs (as strings) linking this memory to resolved entities.';

ANALYZE memories;
