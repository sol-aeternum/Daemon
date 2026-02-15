ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS title_locked BOOLEAN DEFAULT false;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS status TEXT,
    ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_conversations_pinned_updated
    ON conversations(pinned DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_status
    ON messages(status);

CREATE INDEX IF NOT EXISTS idx_messages_metadata
    ON messages USING gin(metadata);
