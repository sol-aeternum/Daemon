# Memory Layer Architecture

## Overview
PostgreSQL + pgvector for persistent conversation memory with semantic search capabilities.

## Schema Design

### 1. Conversations Table
```sql
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    title TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    model_tier TEXT DEFAULT 'pro',
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);
```

### 2. Messages Table
```sql
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    token_count INTEGER,
    
    -- For tool calls
    tool_calls JSONB,
    tool_results JSONB,
    
    -- For image generation
    image_url TEXT,
    
    -- Vector embedding for semantic search
    embedding VECTOR(1536) -- OpenAI text-embedding-3-small
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX idx_messages_embedding ON messages USING ivfflat (embedding vector_cosine_ops);
```

### 3. Memory Snapshots (RAG Context)
```sql
CREATE TABLE memory_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    summary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    relevance_score FLOAT,
    
    -- Vector for similarity search
    embedding VECTOR(1536)
);

CREATE INDEX idx_snapshots_conversation ON memory_snapshots(conversation_id);
CREATE INDEX idx_snapshots_embedding ON memory_snapshots USING ivfflat (embedding vector_cosine_ops);
```

### 4. Tool Executions (Audit Trail)
```sql
CREATE TABLE tool_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    tool_args JSONB NOT NULL,
    tool_result JSONB,
    execution_time_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tool_executions_message ON tool_executions(message_id);
CREATE INDEX idx_tool_executions_name ON tool_executions(tool_name, created_at DESC);
```

## Migration Strategy

### Phase 1: JSON File Fallback
```typescript
// lib/memory/fileStorage.ts
export class FileStorage {
  private dataDir: string;
  
  async saveConversation(conv: Conversation) { }
  async getConversation(id: string) { }
  async searchConversations(query: string) { }
}
```

### Phase 2: PostgreSQL Integration
```typescript
// lib/memory/postgres.ts
import { Pool } from 'pg';

export class PostgresStorage {
  private pool: Pool;
  
  async saveConversation(conv: Conversation) { }
  async getConversation(id: string) { }
  async searchSimilar(query: string, embedding: number[]) { }
}
```

### Phase 3: Hybrid Layer
```typescript
// lib/memory/index.ts
export class MemoryLayer {
  private primary: PostgresStorage;
  private fallback: FileStorage;
  
  async save(conversation: Conversation) {
    try {
      await this.primary.saveConversation(conversation);
    } catch {
      await this.fallback.saveConversation(conversation);
    }
  }
}
```

## pgvector Setup

```bash
# Add to docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: daemon
      POSTGRES_PASSWORD: daemon
      POSTGRES_DB: daemon_memory
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

# Enable extension
psql -U daemon -d daemon_memory -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

## RAG Implementation

### Similarity Search
```sql
-- Find most relevant past conversations
SELECT 
    c.id,
    c.title,
    m.content,
    1 - (m.embedding <=> $1) as similarity
FROM conversations c
JOIN messages m ON m.conversation_id = c.id
WHERE c.user_id = $2
  AND m.role = 'assistant'
  AND 1 - (m.embedding <=> $1) > 0.7
ORDER BY similarity DESC
LIMIT 5;
```

### Context Window Management
```typescript
async function buildContext(
  conversationId: string,
  currentMessage: string,
  maxTokens: number = 4000
): Promise<Message[]> {
  // 1. Get conversation history
  const history = await getRecentMessages(conversationId, 10);
  
  // 2. Get semantically similar messages
  const embedding = await getEmbedding(currentMessage);
  const similar = await searchSimilar(embedding, 5);
  
  // 3. Combine and deduplicate
  // 4. Token-aware truncation
  // 5. Return optimized context
}
```

## API Design

```typescript
// lib/memory/types.ts
interface Conversation {
  id: string;
  userId: string;
  title: string;
  messages: Message[];
  createdAt: Date;
  updatedAt: Date;
}

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  embedding?: number[];
  toolCalls?: ToolCall[];
  imageUrl?: string;
}

// lib/memory/api.ts
export const memoryApi = {
  createConversation: (userId: string, title?: string) => Promise<Conversation>,
  addMessage: (conversationId: string, message: Message) => Promise<void>,
  getConversation: (id: string) => Promise<Conversation>,
  searchConversations: (userId: string, query: string) => Promise<Conversation[]>,
  deleteConversation: (id: string) => Promise<void>,
};
```

## Environment Variables

```bash
# .env
DATABASE_URL=postgresql://daemon:daemon@localhost:5432/daemon_memory
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=your-openai-api-key
MAX_CONTEXT_MESSAGES=50
SIMILARITY_THRESHOLD=0.7
```

**Note:** Embeddings use direct OpenAI API calls via `AsyncOpenAI` client, not OpenRouter. The `OPENAI_API_KEY` environment variable must be set for the memory layer to function.