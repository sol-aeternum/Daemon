-- J1b request facts only, not a completed fence or an applied journal entry.
-- No runtime producer, status conversion, phase/closure API or live backfill.
ALTER TABLE entitlement_accounts
    ADD COLUMN content_generation bigint NOT NULL DEFAULT 0
        CHECK (content_generation >= 0);

CREATE TABLE account_deletions (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('deletion', 'reset')),
    journal_id uuid NOT NULL
        REFERENCES deletion_journal_binding(journal_id) ON DELETE RESTRICT,
    request_seq bigint NOT NULL CHECK (request_seq > 0),
    request_event_id uuid NOT NULL,
    request_digest bytea NOT NULL
        CHECK (octet_length(request_digest) = 32),
    command_digest bytea NOT NULL
        CHECK (octet_length(command_digest) = 32),
    requested_at timestamptz NOT NULL,
    prior_status text NOT NULL
        CHECK (prior_status IN ('active', 'suspended')),
    prior_reason text
        CHECK (prior_reason IN
            ('billing', 'abuse', 'operator', 'deletion', 'reset')),
    fence_generation bigint NOT NULL CHECK (fence_generation > 0),
    drain_deadline timestamptz NOT NULL,
    revocation_deadline timestamptz NOT NULL,
    billing_deadline timestamptz NOT NULL,
    no_grace boolean NOT NULL DEFAULT false,
    reason text CHECK (reason IN ('legal', 'abuse')),
    closed_at timestamptz,
    UNIQUE (journal_id, request_seq),
    UNIQUE (journal_id, request_event_id),
    CHECK (prior_status <> 'active' OR prior_reason IS NULL),
    CHECK (kind = 'deletion' OR (NOT no_grace AND reason IS NULL)),
    CHECK (NOT no_grace OR reason IS NOT NULL),
    CHECK (closed_at IS NULL OR closed_at >= requested_at)
);

CREATE UNIQUE INDEX account_deletions_one_open
    ON account_deletions(account_id)
    WHERE closed_at IS NULL;

CREATE TABLE account_suspension_causes (
    account_id uuid NOT NULL
        REFERENCES entitlement_accounts(user_id) ON DELETE CASCADE,
    cause_id uuid NOT NULL,
    cause_code text NOT NULL CHECK (cause_code IN
        ('billing', 'abuse', 'operator', 'deletion', 'reset')),
    PRIMARY KEY (account_id, cause_id)
);
