-- J1a source substrate only. No account fence, purge, replay applier or pruning.
CREATE TABLE deletion_journal_binding (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    journal_id uuid NOT NULL UNIQUE
);

CREATE TABLE deletion_journal_applied (
    journal_id uuid NOT NULL,
    seq bigint NOT NULL CHECK (seq > 0),
    event_id uuid NOT NULL,
    account_id uuid NOT NULL,
    operation_id uuid,
    entry_digest bytea NOT NULL CHECK (octet_length(entry_digest) = 32),
    PRIMARY KEY (journal_id, seq),
    UNIQUE (journal_id, event_id),
    FOREIGN KEY (journal_id) REFERENCES deletion_journal_binding(journal_id)
        ON DELETE RESTRICT
);

CREATE INDEX deletion_journal_applied_account
    ON deletion_journal_applied (account_id, seq);
