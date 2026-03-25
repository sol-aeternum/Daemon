DROP INDEX IF EXISTS idx_memories_embedding_ivfflat;
DROP INDEX IF EXISTS idx_memories_embedding_hnsw;

UPDATE memories
SET embedding = NULL
WHERE embedding IS NOT NULL;

ALTER TABLE memories
    ALTER COLUMN embedding TYPE vector(1536);

ALTER TABLE memories
    ALTER COLUMN embedding_model SET DEFAULT 'text-embedding-3-small';

UPDATE memories
SET embedding_model = 'text-embedding-3-small'
WHERE embedding_model IS NULL
   OR embedding_model = ''
   OR embedding_model = 'voyage-4-large'
   OR embedding_model = 'voyage-4-lite';

CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw ON memories
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

ANALYZE memories;
