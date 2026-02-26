ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS memory_slot TEXT,
    ADD COLUMN IF NOT EXISTS access_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE memories
    ALTER COLUMN access_count SET DEFAULT 0;

ALTER TABLE memories
    DROP COLUMN IF EXISTS superseded_by;

CREATE INDEX IF NOT EXISTS idx_memories_active
    ON memories(user_id, memory_slot)
    WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_memories_slot_history
    ON memories(user_id, memory_slot, valid_from DESC);

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
