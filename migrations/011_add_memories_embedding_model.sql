ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'text-embedding-3-small';

UPDATE memories
SET embedding_model = 'text-embedding-3-small'
WHERE embedding_model IS NULL OR embedding_model = '';
