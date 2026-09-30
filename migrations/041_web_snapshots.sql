-- Migration: 041_web_snapshots
-- Conversation-scoped, encrypted web reading snapshots.
--
-- Scope and safety anchors:
--   - Additive only: creates one new table plus its indexes. No existing table
--     is altered, dropped, renamed or read.
--   - Every row is owned by an authenticated account (`users.id`) and by one
--     conversation (`conversations.id`). Both foreign keys cascade, so deleting
--     a user or a conversation removes its snapshots; the conversation FK is
--     also the last integrity boundary when a deletion races an in-flight save.
--   - Snapshots are immutable. There is no update path and therefore no
--     `updated_at` column: a refresh creates a new row with a new random UUID
--     and the previous version survives until its own expiry or deletion.
--   - No plaintext page content, URL, title or extraction metadata is stored.
--     `metadata_encrypted` is a Fernet envelope over the source-identity JSON
--     (original URL, final URL, title, extract mode, extraction version) and
--     `content_encrypted` is a *separately* encrypted envelope over the
--     extracted text, so a bounded listing never has to decrypt a page body.
--   - `identity_fingerprint` / `content_fingerprint` are keyed HMAC-SHA256
--     values with domain separation and account scoping (see
--     orchestrator/services/web_snapshots.py). They are lookup/version-compare
--     aids only: neither a fingerprint nor `id` is an authority, and there is
--     no unique index on them, so no cross-account content dedup is possible.
--   - Quota accounting counts retained *encrypted* payload bytes
--     (`stored_bytes`) plus row count; row/index overhead is additionally
--     bounded by the per-conversation and per-account count limits.
--   - `expires_at` is immutable and set at insert time. Reads reject expired
--     rows in SQL even when the cleanup sweep is delayed or failing.
--
-- Idempotent: every statement uses IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS web_snapshots (
    -- Random UUID supplied by the application, never a content hash: an
    -- attacker who can guess or enumerate a value still learns nothing
    -- without the owner/conversation pair.
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,

    -- Fernet envelope over the source-identity JSON document.
    metadata_encrypted TEXT NOT NULL,
    -- Fernet envelope over the extracted page text, encrypted separately so
    -- listing sources does not require decrypting any page body.
    content_encrypted TEXT NOT NULL,

    -- Keyed, domain-separated, account-scoped fingerprints (hex digest).
    identity_fingerprint TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,

    -- Plain numeric accounting columns. `content_chars` counts Unicode code
    -- points of the extracted text (chunk offsets are code-point offsets);
    -- `content_bytes` is that text's UTF-8 size; `stored_bytes` is the size of
    -- both ciphertext envelopes and is the unit the byte quotas charge.
    content_chars INTEGER NOT NULL CHECK (content_chars >= 0),
    content_bytes BIGINT NOT NULL CHECK (content_bytes >= 0),
    stored_bytes BIGINT NOT NULL CHECK (stored_bytes > 0),

    retrieved_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Retention must move forward; a zero/negative window would make every
    -- row unreadable at insert time.
    CHECK (expires_at > retrieved_at)
);

COMMENT ON TABLE web_snapshots IS
    'Immutable conversation-scoped web reading snapshots: Fernet-encrypted source metadata and page text, keyed fingerprints, and plain numeric quota accounting.';

COMMENT ON COLUMN web_snapshots.metadata_encrypted IS
    'Fernet envelope over the source-identity JSON (source_url, final_url, title, extract_mode, extraction_version). Never plaintext.';

COMMENT ON COLUMN web_snapshots.content_encrypted IS
    'Fernet envelope over the extracted page text, encrypted independently of metadata_encrypted so bounded listings skip body decryption.';

COMMENT ON COLUMN web_snapshots.identity_fingerprint IS
    'HMAC-SHA256 keyed by DAEMON_AUTH_PEPPER over a domain-separated, account-scoped source identity. Lookup aid only; grants no authorization.';

COMMENT ON COLUMN web_snapshots.content_fingerprint IS
    'HMAC-SHA256 keyed by DAEMON_AUTH_PEPPER over a domain-separated, account-scoped content digest. Version-comparison aid only; grants no authorization.';

COMMENT ON COLUMN web_snapshots.stored_bytes IS
    'UTF-8 bytes of both ciphertext envelopes; the unit charged against conversation and account byte quotas.';

COMMENT ON COLUMN web_snapshots.expires_at IS
    'Immutable retrieval time plus the configured retention window. Reads reject rows at or past this timestamp even when the cleanup sweep is delayed.';

-- Bounded listing: newest retained sources of one conversation first.
CREATE INDEX IF NOT EXISTS idx_web_snapshots_conversation_recent
    ON web_snapshots(conversation_id, retrieved_at DESC);

-- Account-scoped quota sums and the per-account expiry cleanup that runs
-- before every admission.
CREATE INDEX IF NOT EXISTS idx_web_snapshots_account_expiry
    ON web_snapshots(user_id, expires_at);

-- Latest-version lookup for one source identity inside one conversation.
CREATE INDEX IF NOT EXISTS idx_web_snapshots_identity
    ON web_snapshots(user_id, conversation_id, identity_fingerprint, retrieved_at DESC);

-- Global expired-row housekeeping sweep.
CREATE INDEX IF NOT EXISTS idx_web_snapshots_expiry
    ON web_snapshots(expires_at);

ANALYZE web_snapshots;
