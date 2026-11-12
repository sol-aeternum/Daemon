-- Migration: 068_account_owned_skills
-- Account-owned, database-authoritative skills
-- (docs/ACCOUNT_OWNED_SKILLS_DESIGN.md, step 1).
--
-- Additive only: three new tables and their indexes. Nothing reads or writes
-- them yet; the file-backed store and skill_projections stay in use until the
-- cutover change. Nothing is imported from data/skills or skill_projections:
-- those skills have no owner.
--
--   - `user_id` is the owning account and cascades on account deletion.
--     NULL means a read-only skill shipped with the application.
--   - Every column that can carry text written by a person or a model is a
--     Fernet envelope. The slug is derived from the name, so it is encrypted
--     too; `slug_lookup` is a keyed digest over the owner and the slug
--     (orchestrator/services/skills.py), not a hash of content.
--   - No content hash is stored. `embedding_revision` records which revision
--     the plaintext pgvector embedding was computed from.
--   - `skills` holds the current revision; `skill_revisions` holds every
--     retained save, including the current one.
--   - `skill_store_key` is one row binding these tables to one encryption key.
--     It holds a keyed check value of the key, never the key or any content.
--     The store refuses every operation under a different key, so a
--     misconfigured key cannot read the store as empty or add rows the correct
--     key cannot read. The first write inserts it; only key rotation updates it.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE TABLE IF NOT EXISTS skills (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    slug_encrypted TEXT NOT NULL,
    slug_lookup BYTEA NOT NULL,
    name_encrypted TEXT NOT NULL,
    description_encrypted TEXT NOT NULL,
    body_encrypted TEXT NOT NULL,
    trigger_conditions_encrypted TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    source_type skill_source_type NOT NULL,
    allow_autonomous_edit BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    embedding VECTOR(1024),
    embedding_revision INTEGER,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    complexity_origin INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- A shipped skill is exactly a 'system' skill, and is never open to
    -- autonomous edits.
    CONSTRAINT skills_shipped_is_system CHECK ((user_id IS NULL) = (source_type = 'system')),
    CONSTRAINT skills_shipped_no_grant CHECK (user_id IS NOT NULL OR NOT allow_autonomous_edit)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_account_slug
    ON skills (user_id, slug_lookup) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_shipped_slug
    ON skills (slug_lookup) WHERE user_id IS NULL;

CREATE TABLE IF NOT EXISTS skill_revisions (
    skill_id UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    name_encrypted TEXT NOT NULL,
    description_encrypted TEXT NOT NULL,
    body_encrypted TEXT NOT NULL,
    trigger_conditions_encrypted TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('user', 'autonomous', 'consolidation', 'shipped')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (skill_id, revision)
);

CREATE TABLE IF NOT EXISTS skill_store_key (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    verifier BYTEA NOT NULL CHECK (octet_length(verifier) = 32),
    bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
