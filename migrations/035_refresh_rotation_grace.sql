-- Migration: 035_refresh_rotation_grace
-- Short lost-response tolerance for refresh-token rotation.
--
-- Security anchor:
--   - refresh-token reuse still revokes the device by default
--   - only the immediate predecessor token can replay the already-issued
--     successor pair, only while the successor is still unconsumed/unrevoked,
--     and only for a short application-enforced grace window

CREATE TABLE IF NOT EXISTS refresh_rotation_grace (
    predecessor_session_id UUID PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    successor_session_id   UUID NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
    access_token           TEXT NOT NULL,
    refresh_token          TEXT NOT NULL,
    grace_expires_at       TIMESTAMPTZ NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refresh_rotation_grace_expires_at
    ON refresh_rotation_grace(grace_expires_at);

COMMENT ON TABLE refresh_rotation_grace IS
    'Short-lived plaintext cache for refresh-rotation lost-response tolerance. '
    'Rows allow only the consumed immediate predecessor session to replay the '
    'already-issued successor token pair during the grace window.';

COMMENT ON COLUMN refresh_rotation_grace.access_token IS
    'Plaintext successor access token retained only until grace_expires_at so '
    'a lost refresh response can be replayed idempotently.';

COMMENT ON COLUMN refresh_rotation_grace.refresh_token IS
    'Plaintext successor refresh token retained only until grace_expires_at so '
    'a lost refresh response can be replayed idempotently.';
