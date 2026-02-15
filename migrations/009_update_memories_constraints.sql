ALTER TABLE memories
    DROP CONSTRAINT IF EXISTS memories_status_check;

ALTER TABLE memories
    ADD CONSTRAINT memories_status_check
    CHECK (status IN (
        'active',
        'pending',
        'superseded',
        'inactive',
        'rejected',
        'deleted'
    ));

ALTER TABLE memories
    DROP CONSTRAINT IF EXISTS memories_category_check;

ALTER TABLE memories
    ADD CONSTRAINT memories_category_check
    CHECK (category IN (
        'fact',
        'preference',
        'project',
        'summary',
        'correction'
    ));
