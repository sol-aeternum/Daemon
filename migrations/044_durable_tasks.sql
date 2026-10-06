-- Migration: 044_durable_tasks
-- Durable task continuity, slice 1 (docs/DURABLE_REQUEST_DESIGN.md §4,
-- architecture approved by the product owner on 6 October 2026).
--
-- Scope and safety anchors:
--   - Additive only: four new tables. Nothing existing changes, and no runtime
--     path writes them until the durable-chat flag is enabled.
--   - PostgreSQL is the task authority. arq only wakes workers; a queued or
--     expired-lease row is found again by the dispatch sweep after queue loss.
--   - lease_epoch is the fencing token. Every worker write checks it under a
--     row lock, so a stale attempt can never publish over a newer one.
--   - User content (accepted input, partial attempt output, operation targets,
--     event payloads) is stored only as application-layer ciphertext.
--   - The owner is always the authenticated account; it is never taken from a
--     client payload.

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    result_message_id UUID NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'chat_answer' CHECK (kind IN ('chat_answer')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'needs_attention')),
    input_ciphertext TEXT NOT NULL,
    input_version SMALLINT NOT NULL DEFAULT 1 CHECK (input_version >= 1),
    idempotency_key TEXT CHECK (idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 1 AND 128),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 2 CHECK (max_attempts BETWEEN 1 AND 10),
    lease_epoch BIGINT NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    next_wakeup_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    wake_seq BIGINT NOT NULL DEFAULT 0,
    last_wake_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    content_generation BIGINT NOT NULL DEFAULT 0,
    content_delta_seq BIGINT NOT NULL DEFAULT 0,
    event_seq BIGINT NOT NULL DEFAULT 0,
    terminal_code TEXT CHECK (terminal_code IS NULL OR terminal_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    CONSTRAINT tasks_running_has_lease CHECK (
        (status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    CONSTRAINT tasks_terminal_has_finish CHECK (
        (status IN ('completed', 'failed', 'cancelled', 'needs_attention')) = (finished_at IS NOT NULL)
    ),
    CONSTRAINT tasks_attempts_within_cap CHECK (attempt_count <= max_attempts)
);

COMMENT ON TABLE tasks IS
    'Durable account-owned task. PostgreSQL authority for acceptance, dispatch, '
    'ownership and outcome; arq jobs carry only the task id.';
COMMENT ON COLUMN tasks.input_ciphertext IS
    'Fernet ciphertext of the canonical accepted input (input_version schema).';
COMMENT ON COLUMN tasks.request_hash IS
    'SHA-256 of the canonical accepted input; decides idempotent replay versus conflict.';
COMMENT ON COLUMN tasks.lease_epoch IS
    'Fencing token. Incremented by every claim; worker writes must match it.';
COMMENT ON COLUMN tasks.content_generation IS
    'Lease epoch of the attempt whose content the result message currently holds.';
COMMENT ON COLUMN tasks.content_delta_seq IS
    'Sequence of the last streamed delta included in the persisted content of this generation.';

-- Idempotency is scoped per account; another account's key is independent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_user_idempotency
    ON tasks(user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- At most one non-terminal task per conversation.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_conversation_active
    ON tasks(conversation_id)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS idx_tasks_dispatch_queued
    ON tasks(next_wakeup_at)
    WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_tasks_dispatch_lease
    ON tasks(lease_expires_at)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_tasks_user_created
    ON tasks(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    epoch BIGINT NOT NULL CHECK (epoch >= 1),
    worker_id TEXT NOT NULL CHECK (char_length(worker_id) BETWEEN 1 AND 200),
    outcome TEXT NOT NULL DEFAULT 'running'
        CHECK (outcome IN ('running', 'completed', 'lost', 'failed_retryable',
                           'failed_terminal', 'cancelled', 'needs_attention', 'deferred')),
    terminal_code TEXT CHECK (terminal_code IS NULL OR terminal_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    partial_ciphertext TEXT,
    compute_scope_id UUID,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    CONSTRAINT task_attempts_epoch_unique UNIQUE (task_id, epoch),
    CONSTRAINT task_attempts_end_consistent CHECK ((outcome = 'running') = (ended_at IS NULL))
);

COMMENT ON COLUMN task_attempts.compute_scope_id IS
    'Account compute scope of this attempt. When the attempt is lost, its still-open '
    'reservations are settled at their full hold before a recovery attempt is admitted, '
    'so a dead attempt cannot occupy the account''s concurrency slot.';

COMMENT ON COLUMN task_attempts.partial_ciphertext IS
    'Fernet ciphertext of the content this attempt had persisted when it ended, '
    'kept for interruption disclosure after a later attempt overwrites the result.';

CREATE TABLE IF NOT EXISTS task_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    epoch BIGINT NOT NULL CHECK (epoch >= 1),
    tool_name TEXT NOT NULL CHECK (char_length(tool_name) BETWEEN 1 AND 128),
    effect_class TEXT NOT NULL CHECK (effect_class IN ('material')),
    target_ciphertext TEXT,
    outcome TEXT NOT NULL DEFAULT 'started'
        CHECK (outcome IN ('started', 'succeeded', 'failed', 'unknown')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT task_operations_completion_consistent CHECK (
        (outcome = 'started') = (completed_at IS NULL)
    )
);

COMMENT ON TABLE task_operations IS
    'Effect fence: one row is committed under the lease fence before any material '
    'tool call. A row means the effect may have happened; whole-attempt retries '
    'stop at needs_attention instead of regenerating.';

CREATE INDEX IF NOT EXISTS idx_task_operations_task
    ON task_operations(task_id, started_at);

CREATE TABLE IF NOT EXISTS task_events (
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq BIGINT NOT NULL CHECK (seq >= 1),
    kind TEXT NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]{0,63}$'),
    payload_ciphertext TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, seq)
);

COMMENT ON TABLE task_events IS
    'Append-only, low-volume task lifecycle events for replay on reattach. '
    'Never token deltas or hidden reasoning.';
