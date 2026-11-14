-- Rollback for 071_message_correction_admission. Destroys every message's
-- source revision and admission record; they cannot be reconstructed. Only a
-- binary without the 071 startup check and live-user writer can run afterwards.
SET LOCAL lock_timeout = '1s';
DROP TRIGGER IF EXISTS message_source_revision_guard ON messages;
DROP FUNCTION IF EXISTS memory_message_revision_guard();
ALTER TABLE messages
    DROP CONSTRAINT IF EXISTS messages_correction_source_consistent,
    DROP COLUMN IF EXISTS correction_source_revision,
    DROP COLUMN IF EXISTS correction_origin_generation,
    DROP COLUMN IF EXISTS source_revision;
