-- Migration: 067_account_reminders
-- Account-owned reminder notes (#695).
--
-- Replaces the shared data/reminders.json file, which had no owner. Additive
-- only: one new table and one index. Nothing is imported from the legacy file.
--
--   - Every row is owned by an authenticated account; the foreign key cascades,
--     so deleting the account removes its reminders.
--   - `content_encrypted` is a Fernet envelope over the JSON document
--     {"text", "due_time"}. No reminder text or due time is stored in plaintext.
--   - These are saved notes. Nothing reads `due_time` to send a notification.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content_encrypted TEXT NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reminders_user_created ON reminders (user_id, created_at, id);
