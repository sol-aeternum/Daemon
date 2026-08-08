-- Track the exact messages included in rolling conversation summaries.
--
-- A nullable marker is used instead of count/OFFSET cursors so messages from
-- transactions that commit late cannot be skipped permanently.

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS summary_included_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_messages_pending_summary
    ON messages (conversation_id, created_at, id)
    WHERE summary_included_at IS NULL;
