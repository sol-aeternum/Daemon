ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS summary_updated_at TIMESTAMPTZ;
