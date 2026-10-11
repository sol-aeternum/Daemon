-- P0.5 only: identity provenance and finite terminal-record retention.
-- Internal normalized addresses are PII, not encrypted content. No credentials
-- or arbitrary metadata are added. This does not implement account deletion.
-- The migration runner applies this transactionally before application startup.
LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE IF NOT EXISTS account_email_ownership (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    normalized_email TEXT NOT NULL CHECK (normalized_email <> ''),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    source TEXT NOT NULL CHECK (source IN ('migration_baseline', 'identity_write')),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);
REVOKE ALL ON account_email_ownership FROM PUBLIC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_email_ownership_open_user
    ON account_email_ownership(user_id) WHERE ended_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_ownership_open_email
    ON account_email_ownership(normalized_email) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_email_ownership_user_start
    ON account_email_ownership(user_id, started_at, id);
CREATE INDEX IF NOT EXISTS idx_email_ownership_email_start
    ON account_email_ownership(normalized_email, started_at, id);

-- Observation, NOT a reconstruction from account creation, provider link time,
-- or an address found in old audit/challenge rows. Rerunning never backdates it.
INSERT INTO account_email_ownership (user_id, normalized_email, started_at, source)
SELECT u.id, u.normalized_email, clock_timestamp(), 'migration_baseline'
FROM users u
WHERE u.normalized_email IS NOT NULL AND u.normalized_email <> ''
  AND NOT EXISTS (SELECT 1 FROM account_email_ownership h WHERE h.user_id = u.id);

-- Private clock seam; tests replace its implementation only in disposable schemas.
-- No caller-controlled runtime clock, SECURITY DEFINER, or new secret is needed.
CREATE OR REPLACE FUNCTION identity_provenance_now()
RETURNS TIMESTAMPTZ AS $$ SELECT clock_timestamp(); $$ LANGUAGE sql VOLATILE;

CREATE OR REPLACE FUNCTION record_account_email_ownership()
RETURNS trigger AS $$
DECLARE
    previous account_email_ownership%ROWTYPE;
    transition_at TIMESTAMPTZ;
    last_release TIMESTAMPTZ;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.normalized_email IS NOT DISTINCT FROM OLD.normalized_email THEN
        RETURN NEW;
    END IF;
    -- The users row/unique index is already locked: AFTER avoids provenance
    -- for an INSERT candidate discarded by ON CONFLICT DO NOTHING.
    IF TG_OP = 'UPDATE' AND OLD.normalized_email IS NOT NULL AND OLD.normalized_email <> '' THEN
        SELECT * INTO previous FROM account_email_ownership
        WHERE user_id = NEW.id AND ended_at IS NULL FOR UPDATE;
        IF previous.id IS NULL OR previous.normalized_email <> OLD.normalized_email THEN
            RAISE EXCEPTION 'identity ownership invariant violated';
        END IF;
    END IF;
    IF NEW.normalized_email IS NOT NULL AND NEW.normalized_email <> '' THEN
        SELECT MAX(ended_at) INTO last_release FROM account_email_ownership
        WHERE normalized_email = NEW.normalized_email;
    END IF;
    -- Read the clock AFTER locking, clamp backward time, and share one boundary
    -- for closing/opening. Half-open [start,end); a zero-length span claims no time.
    transition_at := GREATEST(identity_provenance_now(), previous.started_at, last_release);
    IF previous.id IS NOT NULL THEN
        UPDATE account_email_ownership SET ended_at = transition_at WHERE id = previous.id;
    END IF;
    IF NEW.normalized_email IS NOT NULL AND NEW.normalized_email <> '' THEN
        INSERT INTO account_email_ownership (user_id, normalized_email, started_at, source)
        VALUES (NEW.id, NEW.normalized_email, transition_at, 'identity_write');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_email_ownership ON users;
CREATE TRIGGER trg_users_email_ownership AFTER INSERT OR UPDATE OF normalized_email ON users
    FOR EACH ROW EXECUTE FUNCTION record_account_email_ownership();

ALTER TABLE email_challenges ADD COLUMN IF NOT EXISTS email_ownership_id UUID
    REFERENCES account_email_ownership(id) ON DELETE SET NULL;
ALTER TABLE signup_invites ADD COLUMN IF NOT EXISTS email_ownership_id UUID
    REFERENCES account_email_ownership(id) ON DELETE SET NULL;
ALTER TABLE identity_audit_log ADD COLUMN IF NOT EXISTS email_ownership_id UUID
    REFERENCES account_email_ownership(id) ON DELETE SET NULL;
ALTER TABLE email_challenges ADD COLUMN IF NOT EXISTS claimed_user_id UUID
    REFERENCES users(id) ON DELETE CASCADE
    CHECK (claimed_user_id IS NULL OR consumed_at IS NOT NULL);
ALTER TABLE google_nonce_challenges ADD COLUMN IF NOT EXISTS claimed_user_id UUID
    REFERENCES users(id) ON DELETE CASCADE
    CHECK (claimed_user_id IS NULL OR consumed_at IS NOT NULL);

CREATE OR REPLACE FUNCTION capture_email_creation_ownership()
RETURNS trigger AS $$
BEGIN
    -- Never accept caller-supplied provenance or retrospectively fill old rows.
    -- SHARE prevents closing this interval until record creation commits. No
    -- user lock is taken here: identity mutations lock user -> interval.
    NEW.email_ownership_id := NULL;
    SELECT id INTO NEW.email_ownership_id FROM account_email_ownership
    WHERE normalized_email = NEW.normalized_email AND ended_at IS NULL FOR SHARE;
    IF NEW.email_ownership_id IS NULL THEN
        -- A lock wait can close the matching row; a fresh READ COMMITTED lookup
        -- may observe its successor. Still absent means explicitly unknown.
        SELECT id INTO NEW.email_ownership_id FROM account_email_ownership
        WHERE normalized_email = NEW.normalized_email AND ended_at IS NULL FOR SHARE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_email_challenges_creation_ownership ON email_challenges;
CREATE TRIGGER trg_email_challenges_creation_ownership BEFORE INSERT ON email_challenges
    FOR EACH ROW EXECUTE FUNCTION capture_email_creation_ownership();
DROP TRIGGER IF EXISTS trg_signup_invites_creation_ownership ON signup_invites;
CREATE TRIGGER trg_signup_invites_creation_ownership BEFORE INSERT ON signup_invites
    FOR EACH ROW EXECUTE FUNCTION capture_email_creation_ownership();
DROP TRIGGER IF EXISTS trg_identity_audit_creation_ownership ON identity_audit_log;
CREATE TRIGGER trg_identity_audit_creation_ownership BEFORE INSERT ON identity_audit_log
    FOR EACH ROW EXECUTE FUNCTION capture_email_creation_ownership();

ALTER TABLE signup_invites ADD COLUMN IF NOT EXISTS terminal_at TIMESTAMPTZ;
-- Known anchors stay known. Disabled/missing consumed times are observed now,
-- without inventing a historical disablement/consumption time or account owner.
UPDATE signup_invites SET terminal_at = CASE
    WHEN status = 'consumed' THEN COALESCE(consumed_at, clock_timestamp())
    WHEN status = 'expired' THEN expires_at
    WHEN status = 'disabled' THEN clock_timestamp()
END
WHERE status <> 'active' AND terminal_at IS NULL;

CREATE OR REPLACE FUNCTION record_invite_terminal_time()
RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'active' THEN
        NEW.terminal_at := NULL;
    ELSIF TG_OP = 'UPDATE' AND NEW.status = OLD.status AND OLD.terminal_at IS NOT NULL THEN
        NEW.terminal_at := OLD.terminal_at;
    ELSIF NEW.status = 'consumed' THEN
        NEW.terminal_at := COALESCE(NEW.consumed_at, identity_provenance_now());
    ELSIF NEW.status = 'expired' THEN
        NEW.terminal_at := NEW.expires_at;
    ELSE
        NEW.terminal_at := identity_provenance_now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_signup_invites_terminal_time ON signup_invites;
CREATE TRIGGER trg_signup_invites_terminal_time BEFORE INSERT OR UPDATE ON signup_invites
    FOR EACH ROW EXECUTE FUNCTION record_invite_terminal_time();

CREATE INDEX IF NOT EXISTS idx_email_challenges_ownership
    ON email_challenges(email_ownership_id) WHERE email_ownership_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signup_invites_ownership
    ON signup_invites(email_ownership_id) WHERE email_ownership_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_identity_audit_ownership
    ON identity_audit_log(email_ownership_id) WHERE email_ownership_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_email_challenges_claimed_user
    ON email_challenges(claimed_user_id) WHERE claimed_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_google_nonces_claimed_user
    ON google_nonce_challenges(claimed_user_id) WHERE claimed_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_identity_audit_retention
    ON identity_audit_log(created_at, id);
CREATE INDEX IF NOT EXISTS idx_email_challenges_retention
    ON email_challenges(COALESCE(consumed_at, expires_at), id);
CREATE INDEX IF NOT EXISTS idx_google_nonces_retention
    ON google_nonce_challenges(COALESCE(consumed_at, expires_at), id);
CREATE INDEX IF NOT EXISTS idx_signup_invites_retention
    ON signup_invites(COALESCE(terminal_at, expires_at), id);

COMMENT ON TABLE account_email_ownership IS
    'Internal PII: observed account/email intervals [started_at,ended_at). Survives audit expiry; removed with account. No address-only legacy attribution.';
COMMENT ON COLUMN email_challenges.claimed_user_id IS
    'Actual successfully claimed account; authoritative over issuance email_ownership_id. NULL means no recoverable successful claim, not the current email owner.';
COMMENT ON COLUMN google_nonce_challenges.claimed_user_id IS
    'Actual successfully claimed account; never inferred from user_id_proposed. NULL legacy rows expire under finite retention.';
COMMENT ON COLUMN signup_invites.terminal_at IS
    'Seven-day retention anchor: consumed_at, natural expires_at, or observed disablement. Unknown historical consumed/disabled times use migration observation.';
