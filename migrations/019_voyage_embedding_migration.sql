DROP INDEX IF EXISTS idx_memories_embedding_hnsw;
DROP INDEX IF EXISTS idx_memories_embedding_ivfflat;

UPDATE memories
SET embedding = NULL
WHERE embedding IS NOT NULL;

ALTER TABLE memories
    ALTER COLUMN embedding TYPE vector(1024);

ALTER TABLE memories
    ALTER COLUMN embedding_model SET DEFAULT 'voyage-4-large';

UPDATE memories
SET embedding_model = 'voyage-4-large'
WHERE embedding_model IS NULL
   OR embedding_model = ''
   OR embedding_model = 'text-embedding-3-small'
   OR embedding_model = 'text-embedding-3-large';

CREATE INDEX IF NOT EXISTS idx_memories_embedding_ivfflat ON memories
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

ANALYZE memories;
