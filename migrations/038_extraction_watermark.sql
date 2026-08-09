ALTER TABLE memory_extraction_log
    ADD COLUMN IF NOT EXISTS last_message_observed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_extraction_log_conversation_observed_at
    ON memory_extraction_log(conversation_id, last_message_observed_at DESC);

COMMENT ON COLUMN memory_extraction_log.last_message_observed_at IS
    'Max message created_at included in this extraction; used as the watermark for the next extraction so in-flight turns are not skipped.';