-- Migration: 036_worker_job_failures
-- Durable audit trail for arq worker failures.

CREATE TABLE IF NOT EXISTS job_failures (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          TEXT NOT NULL,
    job_name        TEXT NOT NULL,
    queue_name      TEXT NOT NULL,
    args_json       JSONB NOT NULL DEFAULT '[]'::jsonb,
    kwargs_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_type      TEXT NOT NULL,
    error_message   TEXT NOT NULL,
    traceback       TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_failures_created_at
    ON job_failures(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_failures_job_name_created_at
    ON job_failures(job_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_failures_job_id_created_at
    ON job_failures(job_id, created_at DESC);

COMMENT ON TABLE job_failures IS
    'Durable arq worker failure audit records retained outside Redis result TTLs.';

COMMENT ON COLUMN job_failures.args_json IS
    'JSON-safe job positional arguments with large strings capped to avoid storing full raw payloads.';

COMMENT ON COLUMN job_failures.kwargs_json IS
    'JSON-safe job keyword arguments with large strings capped to avoid storing full raw payloads.';
