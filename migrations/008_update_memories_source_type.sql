ALTER TABLE memories
    DROP CONSTRAINT IF EXISTS memories_source_type_check;

ALTER TABLE memories
    ADD CONSTRAINT memories_source_type_check
    CHECK (source_type IN (
        'conversation',
        'manual',
        'import',
        'extracted',
        'user_confirmed',
        'user_corrected',
        'user_created',
        'bootstrapped'
    ));
