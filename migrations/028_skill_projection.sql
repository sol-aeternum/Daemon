-- Migration: 028_skill_projection
-- Creates skill_projections table: DB-backed derived index for markdown skills.
-- Markdown files in SKILLS_DIR remain the canonical source of truth.
-- This table stores embeddings, provenance, usage, version, and upgrade metadata.

CREATE TYPE skill_source_type AS ENUM ('system', 'imported', 'manual', 'autonomous');

CREATE TABLE IF NOT EXISTS skill_projections (
    -- Primary identifier (normalized filename stem, matches .md path)
    skill_id TEXT PRIMARY KEY,

    -- Canonical linkage
    source_file_path TEXT NOT NULL,  -- Absolute path to the .md file

    -- Content integrity
    source_hash TEXT NOT NULL,       -- SHA-256 of markdown file at last sync

    -- Core skill fields
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    -- Provenance
    source_type skill_source_type NOT NULL DEFAULT 'manual',
    created_by TEXT DEFAULT 'system',
    origin_url TEXT DEFAULT '',

    -- Embedding (plaintext vector for pgvector semantic search)
    embedding VECTOR(1024),  -- voyage-4-lite output dimension

    -- Version tracking
    repo_version TEXT NOT NULL DEFAULT '0.0.0',
    local_version TEXT NOT NULL DEFAULT '0.0.0',

    -- Upgrade state (pending_update carries upgrade metadata as JSONB)
    pending_update JSONB DEFAULT NULL,

    -- Protection and autonomy
    allow_autonomous_edit BOOLEAN NOT NULL DEFAULT FALSE,

    -- Usage tracking
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ,

    -- Skill metadata for retrieval
    trigger_conditions TEXT NOT NULL DEFAULT '',
    complexity_origin INTEGER NOT NULL DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for semantic similarity search on embedding
CREATE INDEX IF NOT EXISTS idx_skill_projections_embedding_hnsw
    ON skill_projections USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE embedding IS NOT NULL;

-- Index for source_type filtering
CREATE INDEX IF NOT EXISTS idx_skill_projections_source_type
    ON skill_projections(source_type);

-- Index for enabled filtering
CREATE INDEX IF NOT EXISTS idx_skill_projections_enabled
    ON skill_projections(enabled) WHERE enabled = TRUE;

-- Index for use_count sorting (popular skills)
CREATE INDEX IF NOT EXISTS idx_skill_projections_use_count
    ON skill_projections(use_count DESC);

-- Index for last_used_at sorting (recently used)
CREATE INDEX IF NOT EXISTS idx_skill_projections_last_used
    ON skill_projections(last_used_at DESC) WHERE last_used_at IS NOT NULL;

-- Index for pending_update presence (skills needing attention)
CREATE INDEX IF NOT EXISTS idx_skill_projections_pending_update
    ON skill_projections(skill_id) WHERE pending_update IS NOT NULL;

-- Index for local_version vs repo_version drift detection
CREATE INDEX IF NOT EXISTS idx_skill_projections_version_mismatch
    ON skill_projections(skill_id)
    WHERE local_version != repo_version;

COMMENT ON TABLE skill_projections IS
    'DB-backed skill index derived from markdown files in SKILLS_DIR. '
    'Canonical content remains in .md files; this table provides embeddings, '
    'provenance, usage stats, version tracking, and upgrade state.';
