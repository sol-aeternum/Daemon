-- Migration: 033_auth_runtime_state
-- Shared auth runtime state for multi-worker setup/enrollment.
--
-- Security anchors:
--   - first-boot setup stores only the SHA-256 verifier for the one-time
--     setup token; plaintext is logged only by the worker that creates it
--   - development-mode fallback pepper is shared through Postgres when
--     DAEMON_AUTH_PEPPER is unset and DATABASE_URL is configured, so
--     enrollment verifier HMACs remain consistent across workers
--   - production still requires DAEMON_AUTH_PEPPER and never stores the
--     production pepper here

CREATE TABLE IF NOT EXISTS system_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE system_state IS
    'Singleton runtime state shared by all backend workers. Values may include '
    'secret verifiers or development-only secrets and must not be exposed.';

COMMENT ON COLUMN system_state.key IS
    'Stable runtime state key such as auth.setup_token_hash or auth.development_pepper.';

COMMENT ON COLUMN system_state.value IS
    'Runtime state value. May contain secret verifier material; never expose through APIs or logs.';
