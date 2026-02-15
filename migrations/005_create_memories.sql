-- Memories table
CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL, -- Encrypted via application layer (AES-256-GCM)
    embedding vector(1536), -- OpenAI text-embedding-3-small dimension
    category TEXT NOT NULL CHECK (category IN ('fact', 'preference', 'project', 'summary')),
    source_type TEXT NOT NULL CHECK (source_type IN ('conversation', 'manual', 'import')),
    source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    local_only BOOLEAN NOT NULL DEFAULT FALSE,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'deleted')),
    superseded_by UUID REFERENCES memories(id) ON DELETE SET NULL,
    last_accessed_at TIMESTAMPTZ,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW index for vector similarity search (cosine distance)
-- m=16: max connections per layer, ef_construction=64: build-time search depth
CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw ON memories 
USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- Index for user memories lookup
CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories(user_id);

-- Index for active memories filtering
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status) WHERE status = 'active';

-- Index for category-based queries
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);

-- Index for source conversation lookup
CREATE INDEX IF NOT EXISTS idx_memories_source_conversation ON memories(source_conversation_id);

-- Index for local_only filtering
CREATE INDEX IF NOT EXISTS idx_memories_local_only ON memories(local_only);

-- Index for temporal queries (recently created)
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC);

-- Index for access patterns (recently accessed)
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed_at DESC);

-- Index for updated_at (for sync/audit queries)
CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at);

-- Composite index for common query pattern: active memories for a user
CREATE INDEX IF NOT EXISTS idx_memories_user_status ON memories(user_id, status) WHERE status = 'active';
