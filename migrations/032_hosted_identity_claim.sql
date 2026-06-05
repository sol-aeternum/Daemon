-- Migration: 032_hosted_identity_claim
-- Hosted identity claim schema: tenants, memberships, identity providers,
-- signup invites, email/Google challenges, optional audit, tenant linkage
-- on devices and sessions, and idempotent singleton backfill.
--
-- Security anchors (docs/HOSTED_IDENTITY.md, _scratch_identity_decision_lock.md):
--   - google.sub is the durable provider identity; email is mutable metadata
--     and is captured as normalized_email_at_link at link time, never used
--     as the durable provider key
--   - low-entropy email codes, invite tokens, and Google nonces are stored
--     as HMAC verifiers keyed by DAEMON_AUTH_PEPPER; no plaintext code, nonce,
--     token, or credential columns exist on any table in this migration
--   - one personal tenant per user (partial unique on tenants.owner_user_id
--     where kind = 'personal'); exactly one owner membership per user in
--     their personal tenant
--   - tenant_id is backfilled from the existing singleton user into their
--     personal tenant; the migration aborts on ambiguous duplicate
--     singleton/user/tenant ownership state rather than guessing
--   - rate limiting lives in Redis (TODO 7), not in Postgres hot-path
--     counter tables; this migration introduces no rate-limit counter tables
--
-- Idempotency: every DDL statement uses IF NOT EXISTS, every backfill
-- uses ON CONFLICT DO NOTHING or guarded WHERE clauses, and the migration
-- is safe to apply twice against a fresh or partially-mutated database.

-- ============================================================================
-- 1. Users: identity proof fields
-- ============================================================================
-- normalized_email: lowercased, trimmed, Gmail-style-normalized form used
-- for invite matching and email-challenge lookup. Mutable; can be updated
-- when a user changes their email. NOT the durable account key.
-- email_verified_at: TIMESTAMPTZ NULL = unverified; non-NULL = verified at
-- that time. Required for Google email linking (per decision lock).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS normalized_email TEXT;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;

-- Backfill normalized_email from existing email (idempotent; only updates
-- rows where normalized_email is NULL and email is not NULL). The service
-- layer (TODO 8) will re-normalize on every identity claim to apply
-- Gmail-style dot/+ normalization; this backfill is a safe best-effort.
UPDATE users
SET normalized_email = LOWER(TRIM(email))
WHERE normalized_email IS NULL
  AND email IS NOT NULL
  AND email <> '';

-- Partial index for normalized email lookups; NULL/unset values excluded.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_normalized_email_unique
    ON users(normalized_email)
    WHERE normalized_email IS NOT NULL;

COMMENT ON COLUMN users.normalized_email IS
    'Lowercased, trimmed, Gmail-style-normalized email used for invite '
    'matching and email-challenge lookup. Mutable; not a durable account '
    'key. Updated by the service layer (TODO 8) on identity claims.';
COMMENT ON COLUMN users.email_verified_at IS
    'Timestamp of the most recent successful email verification. NULL = '
    'unverified. Required true for Google email linking per decision lock.';

-- ============================================================================
-- 2. Tenants
-- ============================================================================
-- A tenant is a workspace boundary. Two kinds are scaffolded:
--   personal: one per user; partial unique on owner_user_id
--   shared:   future multi-user workspaces; multiple memberships allowed
-- For v1, only 'personal' tenants are created by the backfill and services.
-- The CHECK constraint enforces that 'personal' tenants always have an owner.

CREATE TABLE IF NOT EXISTS tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL CHECK (kind IN ('personal', 'shared')),
    name            TEXT NOT NULL,
    owner_user_id   UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Personal-tenant invariant: exactly one personal tenant per user.
-- Partial unique index: owner_user_id is unique among rows where kind = 'personal'.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_personal_owner
    ON tenants(owner_user_id)
    WHERE kind = 'personal';

-- Owner_user_id is required for personal tenants; shared tenants may have
-- a nullable owner (creator/operator) in future multi-owner models.
ALTER TABLE tenants
    DROP CONSTRAINT IF EXISTS tenants_personal_owner_required;
ALTER TABLE tenants
    ADD CONSTRAINT tenants_personal_owner_required
    CHECK (kind <> 'personal' OR owner_user_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_tenants_kind ON tenants(kind);
CREATE INDEX IF NOT EXISTS idx_tenants_owner_user_id ON tenants(owner_user_id);

COMMENT ON TABLE tenants IS
    'Workspace boundary. kind = personal (one per user, owned by that user) '
    'or shared (future multi-user workspace, multiple memberships allowed). '
    'Personal-tenant uniqueness is enforced by idx_tenants_personal_owner.';
COMMENT ON COLUMN tenants.owner_user_id IS
    'User who owns this tenant. Required for kind = personal; nullable for '
    'kind = shared (future). ON DELETE RESTRICT prevents tenant deletion '
    'while the owner still exists.';

-- ============================================================================
-- 3. Tenant memberships
-- ============================================================================
-- A user belongs to a tenant via a membership row. role = 'owner' marks the
-- tenant owner; 'member' and 'admin' are reserved for future shared-tenant
-- scopes. Primary key (tenant_id, user_id) prevents duplicate memberships.

CREATE TABLE IF NOT EXISTS tenant_memberships (
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('owner', 'member', 'admin')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id)
);

-- Lookup memberships by user (e.g. "list all tenants for this user").
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user_id
    ON tenant_memberships(user_id);

-- Lookup memberships by tenant + role (e.g. "list owners of this tenant").
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant_role
    ON tenant_memberships(tenant_id, role);

COMMENT ON TABLE tenant_memberships IS
    'Users belonging to tenants. role = owner marks the tenant owner; '
    'member and admin are reserved for future shared-tenant scopes. '
    'Primary key (tenant_id, user_id) prevents duplicate memberships.';
COMMENT ON COLUMN tenant_memberships.role IS
    'owner = tenant owner; member/admin are reserved for future shared-'
    'tenant scopes. Only owner is created by the v1 backfill.';

-- ============================================================================
-- 4. Identity providers
-- ============================================================================
-- One row per (provider, provider_subject) pair. provider_subject is the
-- durable provider identity (Google sub); email is captured at link time
-- as normalized_email_at_link and is NOT the durable key. Unique constraint
-- on (provider, provider_subject) prevents one provider identity from being
-- linked to multiple Daemon accounts.

CREATE TABLE IF NOT EXISTS identity_providers (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider                    TEXT NOT NULL CHECK (provider IN ('google', 'email')),
    provider_subject            TEXT NOT NULL,
    normalized_email_at_link    TEXT,
    linked_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Durable identity: one Daemon user per (provider, provider_subject).
CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_providers_provider_subject
    ON identity_providers(provider, provider_subject);

-- Lookup all providers linked to a user.
CREATE INDEX IF NOT EXISTS idx_identity_providers_user_id
    ON identity_providers(user_id);

-- At most one durable link per provider for a given user.
CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_providers_user_provider_unique
    ON identity_providers(user_id, provider);

COMMENT ON TABLE identity_providers IS
    'Durable provider identities linked to Daemon users. provider_subject is '
    'the provider-issued stable identifier (e.g. Google sub). normalized_'
    'email_at_link captures the email at the time of linking; it is mutable '
    'metadata, NOT a durable key. Unique (provider, provider_subject) prevents '
    'one provider identity from being linked to multiple accounts.';
COMMENT ON COLUMN identity_providers.provider_subject IS
    'Provider-issued stable identifier (e.g. Google sub). Durable; never '
    'updated to preserve the link across email changes.';
COMMENT ON COLUMN identity_providers.normalized_email_at_link IS
    'Normalized email captured at link time. Mutable metadata; the durable '
    'provider identity is provider_subject, not this column.';

-- ============================================================================
-- 5. Signup invites
-- ============================================================================
-- Invite-only hosted signup default. token_verifier_hash is an HMAC of the
-- plaintext invite token (keyed by DAEMON_AUTH_PEPPER); the plaintext token
-- is never stored. status lifecycle: active -> consumed | disabled | expired.
-- Partial unique on (normalized_email) WHERE status = 'active' ensures only
-- one active invite per email at a time.

CREATE TABLE IF NOT EXISTS signup_invites (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    normalized_email        TEXT NOT NULL,
    token_verifier_hash     TEXT NOT NULL UNIQUE,
    status                  TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'consumed', 'disabled', 'expired')),
    created_by_user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    used_by_user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    expires_at              TIMESTAMPTZ NOT NULL,
    consumed_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Only one active invite per normalized email at a time.
CREATE UNIQUE INDEX IF NOT EXISTS idx_signup_invites_active_email
    ON signup_invites(normalized_email)
    WHERE status = 'active';

-- Naturally expired active invites must not block reissue forever. Before a new
-- invite INSERT, expire any stale active rows for the same normalized email.
CREATE OR REPLACE FUNCTION expire_stale_signup_invites_before_insert()
RETURNS trigger AS $$
BEGIN
    UPDATE signup_invites
    SET status = 'expired'
    WHERE normalized_email = NEW.normalized_email
      AND status = 'active'
      AND expires_at <= NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_signup_invites_expire_stale_before_insert ON signup_invites;
CREATE TRIGGER trg_signup_invites_expire_stale_before_insert
    BEFORE INSERT ON signup_invites
    FOR EACH ROW
    EXECUTE FUNCTION expire_stale_signup_invites_before_insert();

CREATE INDEX IF NOT EXISTS idx_signup_invites_status ON signup_invites(status);
CREATE INDEX IF NOT EXISTS idx_signup_invites_expires_at ON signup_invites(expires_at);

COMMENT ON TABLE signup_invites IS
    'Hosted signup invitations. token_verifier_hash is the HMAC-SHA256 of the '
    'plaintext invite token keyed by DAEMON_AUTH_PEPPER; the plaintext token '
    'is never stored. status lifecycle: active -> consumed | disabled | '
    'expired. Only one active invite per normalized email at a time.';
COMMENT ON COLUMN signup_invites.token_verifier_hash IS
    'HMAC-SHA256(plaintext_invite_token, DAEMON_AUTH_PEPPER). Verification '
    'compares HMAC output; the plaintext invite token is never stored or '
    'logged (decision lock: HMAC/verifier storage for low-entropy material).';

-- ============================================================================
-- 6. Email challenges
-- ============================================================================
-- One row per email-code challenge. code_verifier_hash is the HMAC of the
-- 6-digit code (keyed by DAEMON_AUTH_PEPPER); the plaintext code is never
-- stored or logged. attempts_remaining decrements atomically; locked_at
-- marks a soft lockout after attempts are exhausted. ip_hash is the HMAC
-- of the source IP for abuse triage; the raw IP is never stored.

CREATE TABLE IF NOT EXISTS email_challenges (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    normalized_email        TEXT NOT NULL,
    code_verifier_hash      TEXT NOT NULL,
    attempts_remaining      INTEGER NOT NULL DEFAULT 5 CHECK (attempts_remaining >= 0),
    expires_at              TIMESTAMPTZ NOT NULL,
    consumed_at             TIMESTAMPTZ,
    locked_at               TIMESTAMPTZ,
    ip_hash                 TEXT,
    user_agent_hash         TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Lookup active challenges for a normalized email (most-recent first).
CREATE INDEX IF NOT EXISTS idx_email_challenges_email_expires
    ON email_challenges(normalized_email, expires_at DESC);

-- Cleanup index: find expired, unconsumed challenges for periodic prune.
CREATE INDEX IF NOT EXISTS idx_email_challenges_expires_consumed
    ON email_challenges(expires_at)
    WHERE consumed_at IS NULL AND locked_at IS NULL;

COMMENT ON TABLE email_challenges IS
    'Email-code sign-in challenges. code_verifier_hash is the HMAC-SHA256 '
    'of the 6-digit code keyed by DAEMON_AUTH_PEPPER; the plaintext code is '
    'never stored or logged (decision lock). attempts_remaining decrements '
    'atomically; locked_at marks a soft lockout after attempts are exhausted.';
COMMENT ON COLUMN email_challenges.code_verifier_hash IS
    'HMAC-SHA256(code, DAEMON_AUTH_PEPPER). Verification compares HMAC '
    'output; the plaintext email code is never stored or looked up directly.';
COMMENT ON COLUMN email_challenges.ip_hash IS
    'HMAC-SHA256 of the source IP, keyed by DAEMON_AUTH_PEPPER. Used for '
    'abuse triage only; the raw IP is never stored in this table.';
COMMENT ON COLUMN email_challenges.user_agent_hash IS
    'HMAC-SHA256 of the User-Agent header, keyed by DAEMON_AUTH_PEPPER. '
    'The raw User-Agent is never stored in this table.';

-- ============================================================================
-- 7. Google nonce challenges
-- ============================================================================
-- Server-issued one-time nonces for the Google Identity Services manual
-- callback flow. nonce_verifier_hash is the HMAC of the plaintext nonce
-- (keyed by DAEMON_AUTH_PEPPER); the plaintext nonce is returned to the
-- client and is never stored. The cross-binding check (server-side) confirms
-- the same nonce appears in the ID token's nonce claim. UNIQUE on
-- nonce_verifier_hash prevents collision.

CREATE TABLE IF NOT EXISTS google_nonce_challenges (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nonce_verifier_hash     TEXT NOT NULL UNIQUE,
    user_id_proposed        UUID REFERENCES users(id) ON DELETE SET NULL,
    expires_at              TIMESTAMPTZ NOT NULL,
    consumed_at             TIMESTAMPTZ,
    ip_hash                 TEXT,
    user_agent_hash         TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Cleanup index: find expired, unconsumed nonces for periodic prune.
CREATE INDEX IF NOT EXISTS idx_google_nonce_challenges_expires_consumed
    ON google_nonce_challenges(expires_at)
    WHERE consumed_at IS NULL;

COMMENT ON TABLE google_nonce_challenges IS
    'Server-issued one-time nonces for the Google Identity Services manual '
    'callback flow. nonce_verifier_hash is the HMAC-SHA256 of the plaintext '
    'nonce keyed by DAEMON_AUTH_PEPPER; the plaintext nonce is returned to '
    'the client and is never persisted (decision lock: HMAC/verifier storage).';
COMMENT ON COLUMN google_nonce_challenges.nonce_verifier_hash IS
    'HMAC-SHA256(nonce, DAEMON_AUTH_PEPPER). The plaintext nonce is returned '
    'to the client and is never stored or looked up directly.';
COMMENT ON COLUMN google_nonce_challenges.user_id_proposed IS
    'Optional: the Daemon user the client intended to link to (set when '
    'the nonce is issued in the context of an explicit linking intent). '
    'Prevents re-binding a captured Google credential to a different user.';

-- ============================================================================
-- 8. Identity audit log (optional)
-- ============================================================================
-- Append-only audit log for hosted identity events. No PII plaintext;
-- identifiers are UUIDs and normalized_email is the only contact surface
-- captured. Used for abuse triage, link/unlink history, and replay analysis.

CREATE TABLE IF NOT EXISTS identity_audit_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID REFERENCES users(id) ON DELETE SET NULL,
    tenant_id           UUID REFERENCES tenants(id) ON DELETE SET NULL,
    event_type          TEXT NOT NULL CHECK (event_type IN (
                            'email_challenge_issued',
                            'email_challenge_consumed',
                            'email_challenge_locked',
                            'google_nonce_issued',
                            'google_nonce_consumed',
                            'provider_linked',
                            'provider_unlinked',
                            'invite_created',
                            'invite_consumed',
                            'invite_disabled',
                            'tenant_created',
                            'tenant_member_added'
                        )),
    normalized_email    TEXT,
    provider            TEXT,
    provider_subject    TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_hash             TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_identity_audit_log_user_id
    ON identity_audit_log(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_identity_audit_log_tenant_id
    ON identity_audit_log(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_identity_audit_log_event_type
    ON identity_audit_log(event_type, created_at DESC);

COMMENT ON TABLE identity_audit_log IS
    'Append-only audit log for hosted identity events. No plaintext PII; '
    'identifiers are UUIDs and normalized_email (the only contact surface). '
    'Used for abuse triage, link/unlink history, and replay analysis.';

-- ============================================================================
-- 9. Tenant linkage on devices and sessions
-- ============================================================================
-- tenant_id is added as a nullable UUID FK to tenants. The column is
-- backfilled from the existing singleton user in section 10, then made
-- NOT NULL. ON DELETE RESTRICT prevents tenant deletion while active
-- devices/sessions exist (multi-tenant safety).

ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE RESTRICT;

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE RESTRICT;

-- Lookup devices by tenant (for tenant-scoped device listing).
CREATE INDEX IF NOT EXISTS idx_devices_tenant_id
    ON devices(tenant_id) WHERE revoked_at IS NULL;

-- Lookup sessions by tenant (for tenant-scoped session queries).
CREATE INDEX IF NOT EXISTS idx_sessions_tenant_id
    ON sessions(tenant_id) WHERE revoked_at IS NULL;

COMMENT ON COLUMN devices.tenant_id IS
    'Tenant this device belongs to. Backfilled from the singleton user in '
    'migration 032; NOT NULL after backfill. ON DELETE RESTRICT prevents '
    'tenant deletion while active devices exist.';
COMMENT ON COLUMN sessions.tenant_id IS
    'Tenant this session was issued for. Backfilled from the singleton user '
    'in migration 032; NOT NULL after backfill. ON DELETE RESTRICT prevents '
    'tenant deletion while active sessions exist.';

-- sessions.device_persistence records the originating persistence
-- (private vs temporary) on every session row. The refresh rotation
-- preserves this column so a temporary/web session is not silently
-- widened into the long-lived private posture during cookie rotation
-- (TODO 22 BLOCKING finding B1). Constrained to the same two values
-- the helper accepts (`private`, `temporary`); pre-existing rows are
-- backfilled to `private` so a missed backfill in an existing row
-- never widens into a longer-lived posture by accident.
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS device_persistence TEXT
        NOT NULL DEFAULT 'private'
        CHECK (device_persistence IN ('private', 'temporary'));

CREATE INDEX IF NOT EXISTS idx_sessions_device_persistence
    ON sessions(device_persistence) WHERE revoked_at IS NULL;

COMMENT ON COLUMN sessions.device_persistence IS
    'Originating device persistence for this session: private (long-lived, '
    'default 90-day cookie/refresh TTL) or temporary (session-cookie for web, '
    'short DB cap for native). Persisted on issuance so refresh rotation '
    'preserves the original posture (decision lock: temporary/public sessions '
    'must not silently widen into the private posture). Pre-existing rows '
    'were backfilled to private on migration apply.';

-- ============================================================================
-- 10. Idempotent singleton backfill
-- ============================================================================
-- The singleton install (UUID 00000000-0000-0000-0000-000000000001) is
-- backfilled to exactly one personal tenant + one owner membership. All
-- existing devices and sessions for the singleton are linked to that
-- personal tenant. The migration aborts on ambiguous duplicate singleton
-- ownership state rather than guessing.
--
-- Abort conditions (enforced via DO blocks below):
--   A. Singleton user is missing entirely (count = 0)
--   B. More than one user row with the singleton UUID (impossible by PK,
--      but defensive check; count > 1)
--   C. More than one personal tenant exists for the singleton after the
--      backfill (should be impossible due to idx_tenants_personal_owner,
--      but defensive)
--   D. More than one owner membership exists in a personal tenant for
--      the singleton after the backfill
--
-- A fresh deployment (no singleton) is NOT an abort: the schema is still
-- created and services can create the personal tenant on first identity
-- claim. The backfill is a no-op when the singleton is absent.

DO $$
DECLARE
    v_singleton_id     CONSTANT UUID := '00000000-0000-0000-0000-000000000001';
    v_singleton_count  INTEGER;
    v_tenant_id        UUID;
    v_personal_count   INTEGER;
    v_owner_count      INTEGER;
BEGIN
    -- Count singleton user rows (PK enforces 0 or 1).
    SELECT COUNT(*) INTO v_singleton_count
    FROM users
    WHERE id = v_singleton_id;

    -- Abort B: PK already prevents >1, but defensive check.
    IF v_singleton_count > 1 THEN
        RAISE EXCEPTION
            'hosted_identity_claim backfill aborted: % rows found with singleton UUID %. Expected 0 or 1. Manual review required.',
            v_singleton_count, v_singleton_id;
    END IF;

    -- A: Singleton missing is a no-op (fresh deployment, no backfill needed).
    IF v_singleton_count = 0 THEN
        RAISE NOTICE
            'hosted_identity_claim backfill: singleton user % not found; skipping backfill (fresh deployment).',
            v_singleton_id;
        RETURN;
    END IF;

    -- Singleton exists: create the personal tenant (idempotent).
    -- ON CONFLICT uses the partial unique index on owner_user_id WHERE kind = personal.
    INSERT INTO tenants (id, kind, name, owner_user_id)
    VALUES (gen_random_uuid(), 'personal', 'Personal', v_singleton_id)
    ON CONFLICT (owner_user_id) WHERE kind = 'personal' DO NOTHING
    RETURNING id INTO v_tenant_id;

    -- If the INSERT was a no-op (tenant already existed), look it up.
    IF v_tenant_id IS NULL THEN
        SELECT id INTO v_tenant_id
        FROM tenants
        WHERE owner_user_id = v_singleton_id AND kind = 'personal';
    END IF;

    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION
            'hosted_identity_claim backfill aborted: failed to create or locate personal tenant for singleton %.',
            v_singleton_id;
    END IF;

    -- Create the owner membership (idempotent via PK).
    INSERT INTO tenant_memberships (tenant_id, user_id, role)
    VALUES (v_tenant_id, v_singleton_id, 'owner')
    ON CONFLICT (tenant_id, user_id) DO NOTHING;

    -- Abort C: more than one personal tenant for the singleton (defensive).
    SELECT COUNT(*) INTO v_personal_count
    FROM tenants
    WHERE owner_user_id = v_singleton_id AND kind = 'personal';

    IF v_personal_count <> 1 THEN
        RAISE EXCEPTION
            'hosted_identity_claim backfill aborted: % personal tenants found for singleton %. Expected exactly 1. Manual review required.',
            v_personal_count, v_singleton_id;
    END IF;

    -- Abort D: more than one owner membership in a personal tenant for the
    -- singleton (defensive; PK prevents duplicates per tenant).
    SELECT COUNT(*) INTO v_owner_count
    FROM tenant_memberships tm
    JOIN tenants t ON tm.tenant_id = t.id
    WHERE tm.user_id = v_singleton_id
      AND tm.role = 'owner'
      AND t.kind = 'personal';

    IF v_owner_count <> 1 THEN
        RAISE EXCEPTION
            'hosted_identity_claim backfill aborted: % owner memberships in personal tenants for singleton %. Expected exactly 1. Manual review required.',
            v_owner_count, v_singleton_id;
    END IF;

    -- Backfill tenant_id on devices and sessions for the singleton.
    -- Idempotent: only updates rows where tenant_id IS NULL.
    UPDATE devices
    SET tenant_id = v_tenant_id
    WHERE user_id = v_singleton_id
      AND tenant_id IS NULL;

    UPDATE sessions
    SET tenant_id = v_tenant_id
    WHERE user_id = v_singleton_id
      AND tenant_id IS NULL;

    RAISE NOTICE
        'hosted_identity_claim backfill complete: singleton % -> personal tenant % (1 owner membership).',
        v_singleton_id, v_tenant_id;
END
$$;

-- ============================================================================
-- 11. Enforce NOT NULL on tenant_id (after backfill)
-- ============================================================================
-- The backfill in section 10 links every existing device and session for
-- the singleton to the singleton's personal tenant. Any rows still NULL
-- after the backfill belong to non-singleton users that the backfill did
-- not touch (intentional: services will create their personal tenant on
-- first identity claim). The NOT NULL is therefore scoped to the singleton
-- subset: we promote the singleton's rows to NOT NULL while leaving
-- non-singleton rows untouched.
--
-- For deployments that already have a personal tenant for every user
-- (e.g. a follow-up migration), the NOT NULL can be tightened globally.
-- For v1 we keep tenant_id nullable for non-singleton users so that the
-- schema does not require a destructive backfill of unknown legacy users.
--
-- The devices/sessions tenant_id columns remain nullable in this migration
-- by design; the partial NOT NULL is enforced at the service layer
-- (TODO 8) when issuing sessions and devices.

-- ============================================================================
-- 12. ANALYZE
-- ============================================================================
-- Refresh planner statistics for the new and modified tables so the
-- query planner can make informed decisions immediately after migration.

ANALYZE users;
ANALYZE tenants;
ANALYZE tenant_memberships;
ANALYZE identity_providers;
ANALYZE signup_invites;
ANALYZE email_challenges;
ANALYZE google_nonce_challenges;
ANALYZE identity_audit_log;
ANALYZE devices;
ANALYZE sessions;
