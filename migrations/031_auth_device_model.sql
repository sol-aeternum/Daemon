-- Migration: 031_auth_device_model
-- Devices, sessions, and pending_enrollments for per-device opaque-token auth.
-- Replaces the legacy DAEMON_API_KEY model.
--
-- Security anchors (AUTH_ARCHITECTURE.md):
-- Decision 7:  Tokens are 256-bit opaque values (secrets.token_urlsafe(32))
-- Decision 8:  Tokens stored only as SHA-256 hashes
-- Decision 9:  Enrollment codes use HMAC-SHA256 verifiers keyed by DAEMON_AUTH_PEPPER;
--             no plaintext code, no raw code_hash column or lookup path
-- Decision 11: Access-token TTL is 30 minutes
-- Decision 12: Refresh-token TTL is 90 days
-- Decision 13: Enrollment TTL is 10 minutes
-- Decision 14: Cleanup grace is 7 days; interval is 24 hours
-- Decision 20: Refresh reuse revokes the device

-- devices
CREATE TABLE IF NOT EXISTS devices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name    TEXT NOT NULL,
    platform        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_devices_user_id ON devices(user_id);
-- Partial index for first-boot condition: COUNT(*) FROM devices WHERE revoked_at IS NULL = 0
CREATE INDEX IF NOT EXISTS idx_devices_active_user_id ON devices(user_id) WHERE revoked_at IS NULL;

COMMENT ON TABLE devices IS
    'Registered devices belonging to a user. Active when revoked_at IS NULL. '
    'First-boot setup creates the first active device; no device is backfilled.';

-- sessions
CREATE TABLE IF NOT EXISTS sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id               UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    client_kind             TEXT NOT NULL CHECK (client_kind IN ('web', 'native')),
    access_token_hash       TEXT NOT NULL UNIQUE,
    access_expires_at       TIMESTAMPTZ NOT NULL,
    refresh_token_hash      TEXT NOT NULL UNIQUE,
    refresh_expires_at      TIMESTAMPTZ NOT NULL,
    refresh_consumed_at     TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sessions_device_id ON sessions(device_id);
CREATE INDEX IF NOT EXISTS idx_sessions_access_token_hash ON sessions(access_token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_refresh_token_hash ON sessions(refresh_token_hash);
-- Cleanup index: find stale sessions by expiry + revocation age
CREATE INDEX IF NOT EXISTS idx_sessions_cleanup ON sessions(refresh_expires_at, revoked_at);

COMMENT ON TABLE sessions IS
    'Device sessions. Tokens never stored in plaintext; only SHA-256 hashes. '
    'refresh_consumed_at IS NOT NULL marks a consumed refresh token kept for '
    'reuse detection until cleanup grace expires (Decision 20).';

COMMENT ON COLUMN sessions.client_kind IS
    'web = browser with HttpOnly refresh cookie; native = app with refresh in response body.';

-- pending_enrollments
CREATE TABLE IF NOT EXISTS pending_enrollments (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_by_device_id    UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    code_verifier_hash      TEXT NOT NULL,
    wrong_attempts_remaining INTEGER NOT NULL,
    expires_at              TIMESTAMPTZ NOT NULL,
    consumed_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pending_enrollments_user_id ON pending_enrollments(user_id);
CREATE INDEX IF NOT EXISTS idx_pending_enrollments_expires_at ON pending_enrollments(expires_at);

COMMENT ON TABLE pending_enrollments IS
    'In-progress device enrollments. Stores HMAC-SHA256(code, DAEMON_AUTH_PEPPER). '
    'No plaintext code and no raw code_hash are ever stored (Decision 9).';

COMMENT ON COLUMN pending_enrollments.code_verifier_hash IS
    'HMAC-SHA256(code, DAEMON_AUTH_PEPPER). Verification compares HMAC output; '
    'no direct hash of the low-entropy code is ever stored or looked up.';
