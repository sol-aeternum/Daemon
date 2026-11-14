-- Rollback for 072_summary_provenance. Destroys every versioned conversation
-- summary, its source closure and its encrypted payload. Published summaries
-- cleared the legacy conversations.summary text, so those conversations are
-- left with no summary until one is regenerated. Only a binary without the
-- 072 startup check and summary-provenance writers can run afterwards.
SET LOCAL lock_timeout = '1s';
DROP TRIGGER IF EXISTS conversation_summary_head_guard ON conversations;
DROP FUNCTION IF EXISTS conversation_summary_head_guard();
DROP TABLE IF EXISTS conversation_summary_payloads;
DROP TABLE IF EXISTS conversation_summary_sources;
DROP TABLE IF EXISTS conversation_summary_versions;
DROP FUNCTION IF EXISTS summary_payload_guard();
DROP FUNCTION IF EXISTS summary_source_guard();
DROP FUNCTION IF EXISTS summary_version_guard();
ALTER TABLE conversations
    DROP COLUMN IF EXISTS summary_head_version_id,
    DROP COLUMN IF EXISTS summary_state_revision;
