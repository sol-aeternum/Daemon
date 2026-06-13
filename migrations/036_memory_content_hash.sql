-- Add a keyed plaintext-content fingerprint for active-memory uniqueness.
--
-- The content column is Fernet ciphertext, so equal plaintext memories do not
-- have equal database values. The application computes content_hash as
-- HMAC-SHA256(normalized_plaintext, DAEMON_AUTH_PEPPER) before encryption.

ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_active_content_hash_unique
    ON memories(user_id, content_hash)
    WHERE content_hash IS NOT NULL
      AND status = 'active'
      AND valid_to IS NULL;

COMMENT ON COLUMN memories.content_hash IS
    'HMAC-SHA256 of normalized plaintext memory content keyed by DAEMON_AUTH_PEPPER; used to prevent duplicate active memories without storing plaintext.';
