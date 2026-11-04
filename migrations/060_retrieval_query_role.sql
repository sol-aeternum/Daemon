-- Add only provenance metadata. Original query_text remains NOT NULL.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';
ALTER TABLE retrieval_log ADD COLUMN query_role TEXT
    CHECK (query_role IS NULL OR query_role = 'original' OR query_role ~ '^sub:[0-9]+$');
