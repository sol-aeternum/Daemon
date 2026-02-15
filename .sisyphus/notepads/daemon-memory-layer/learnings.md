# Daemon Memory Layer - Project Notepad

## Conventions
- Use `uv` for Python packages (pyproject.toml), NOT pip
- Source code in `orchestrator/`, NOT `backend/`
- AppState class separate from pydantic-settings config
- Raw asyncpg SQL (no ORM)
- No hardcoded user names in prompts (use generic "You are Daemon")
- Graceful degradation: memory layer fails open (chat works without DB)

## Architecture Decisions
- PostgreSQL+pgvector for persistence and vector search
- ARQ+Redis for background jobs
- Encryption: AES-256-GCM on content, embeddings cleartext
- Token budget: 1500 tokens for memory context via chars/4 heuristic
- Dedup: cosine ≥0.92 + temporal windows (Spec E)
- Retrieval scoring: similarity × recency × source × confidence (Spec F)
- Memory tools: read (semantic/temporal) + write (create/update/delete)

## File Patterns
- Database layer: `orchestrator/database/`
- Memory layer: `orchestrator/memory/`
- Background worker: `orchestrator/worker/`
- API routes: `orchestrator/routes/`
- Tests: `tests/memory/`

## Gotchas
- `docker-compose.yml` currently builds via `backend/Dockerfile` (plan uses `build: .`)
- Pre-existing LSP error in `orchestrator/tools/completion.py:251` — ignore
- `/v1/chat/completions` must NOT be touched (OpenAI-compat passthrough)
- `/chat` has graceful degradation if DB unavailable

## Task 1 Learnings
- Added `postgres` service using `pgvector/pgvector:pg15` with env refs (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`), persisted volume `postgres_data`, and `pg_isready` healthcheck.
- Added `redis` service using `redis:7-alpine` with persisted volume `redis_data` and `redis-cli ping` healthcheck.
- Preserved existing backend/frontend/open-webui service configuration and existing `backend/Dockerfile` build pattern.
- Added memory infra env vars to `.env.example`: `DATABASE_URL`, `REDIS_URL`, `ENCRYPTION_KEY`, `DAEMON_ENCRYPTION_KEY`, plus `POSTGRES_*` variables.
- `docker compose up -d postgres redis` starts both services successfully; `docker compose ps` reports both as healthy.

## Task 4 Learnings (Encryption)
- Created `orchestrator/memory/encryption.py` with `ContentEncryption` class using `cryptography.fernet.Fernet` (symmetric encryption).
- Added `cryptography>=42.0.0` dependency via `uv add cryptography` (installed v46.0.5).
- `ContentEncryption.__init__` accepts optional key param, falls back to `DAEMON_ENCRYPTION_KEY` env var.
- Graceful degradation: if key missing/empty, logs warning and returns plaintext (passthrough mode).
- `encrypt(plaintext: str) -> str` returns base64-encoded ciphertext; `decrypt(ciphertext: str) -> str` returns plaintext.
- Invalid ciphertext raises `ValueError` with message "Invalid ciphertext: decryption failed (wrong key or corrupted data)".
- All three QA scenarios verified: encryption round-trip passes, empty key passes through plaintext, invalid ciphertext raises proper error.
- Pattern follows existing `orchestrator/tools/web_search.py` external API tool structure (init with optional key, execute with error handling).

## Task 4 Learnings (Embedding)
- Created `orchestrator/memory/embedding.py` with `embed_text` and `embed_batch` async functions using direct OpenAI API.
- Added `openai>=1.10.0` dependency via `uv add openai`.
- Uses `AsyncOpenAI` client with `OPENAI_API_KEY` env var (raises `EmbeddingError` if missing).
- `embed_text(text: str, model: str = "text-embedding-3-small") -> list[float]` returns 1536-dim vector.
- `embed_batch(texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]` returns list of 1536-dim vectors.
- Both functions accept optional `client` param for dependency injection (enables testing with mocks).
- Retry logic: 3 attempts with exponential backoff (1s, 2s, 4s) on `OpenAIError`.
- Empty/whitespace text handling: single text raises `EmbeddingError`, batch filters empties (raises if all empty).
- Batch maintains input order by sorting response data by index.
- All six QA scenarios verified: single/batch embedding produces 1536-dim vectors, empty text rejected, empty batch returns empty list, all-empty batch rejected, retry logic present.

## Task 3 Learnings (Schema Migrations)
- Created 7 idempotent SQL migration files in `migrations/`:
  - `001_create_extensions.sql` - pgcrypto + pgvector extensions
  - `002_create_users.sql` - users table with email/name/preferences (JSONB)
  - `003_create_conversations.sql` - conversations table with pipeline/title/summary/message_count/tokens_total
  - `004_create_messages.sql` - messages table with content (encrypted), tool_calls/tool_results (JSONB)
  - `005_create_memories.sql` - memories table with embedding (vector 1536), category/source_type/status enums, HNSW index (m=16, ef_construction=64)
  - `006_create_extraction_log.sql` - memory_extraction_log table with extracted_facts/dedup_results (JSONB)
  - `007_seed_default_user.sql` - inserts default user with UUID `00000000-0000-0000-0000-000000000001`
- Created `scripts/migrate.py` - standalone asyncpg migration runner (no orchestrator imports)
  - Creates `_migrations` tracking table with filename/applied_at
  - Runs SQL files in sorted order, skipping already-applied
  - Prints progress with emoji markers (▶️ applying, ✓ success, ⏭️ skipped)
- Created `scripts/seed.py` - idempotent default user seeder (separate from migration 007 for manual re-runs)
- Created `scripts/generate_key.py` - Fernet key generator using `cryptography.fernet.Fernet.generate_key()`
- All migrations tested against running postgres container: applied successfully, re-run shows idempotent behavior
- Verified HNSW index created on `memories.embedding` with correct params: `vector_cosine_ops`, m=16, ef_construction=64
- Verified default user seeded with correct UUID: `00000000-0000-0000-0000-000000000001`
- All scripts made executable via `chmod +x scripts/*.py`
- Migration files include SQL comments documenting encryption, performance params, and index purposes (necessary for DB ops/maintenance)

## Task 2 Learnings (DB Layer)
- Created `orchestrator/db.py` with `AppState` dataclass (fields: `settings`, `db_pool`, `redis`), `init_app_state`, `close_app_state`, `get_app_state` (FastAPI dependency via `request.app.state.app_state`), `check_db_health`.
- `AppState` uses `@dataclass` (not pydantic) to hold mutable runtime state separate from `Settings`.
- `init_app_state` creates `asyncpg.create_pool(dsn, min_size=5, max_size=20)` and `arq.create_pool(RedisSettings.from_dsn(...))`. Both wrapped in try/except for graceful degradation — if connection fails or URL not set, pool stays `None`.
- `close_app_state` safely closes both pools if not `None`.
- `check_db_health` runs `SELECT 1` on asyncpg pool and `ping()` on Redis; returns dict with status per service (`ok`, `not_configured`, or error message).
- Added `database_url: str | None = None` and `redis_url: str | None = None` to `Settings` in `config.py`.
- Added `asyncpg>=0.30.0` and `arq>=0.26.1` to `pyproject.toml` dependencies.
- Wired `lifespan` async context manager into `FastAPI(lifespan=lifespan)` in `main.py` — creates `AppState` on startup, closes on shutdown.
- `/health` endpoint now returns `services` dict with postgres/redis health status alongside `status: ok`.
- ARQ uses `RedisSettings.from_dsn(url)` to parse `REDIS_URL` connection string.
- `ArqRedis` (not `arq.Redis`) is the correct type returned by `arq.connections.create_pool`.
- basedpyright LSP not installed in env; used import tests and runtime assertions as verification fallback.

## Task 5 Learnings (MemoryStore)
- Created `orchestrator/memory/store.py` with `MemoryStore` class — central data access layer (24 public methods).
- Constructor: `__init__(db_pool: asyncpg.Pool, encryption: ContentEncryption)`.
- Encrypted fields: `messages.content`, `memories.content`, `extraction_log.input_snippet` — encrypt on write, decrypt on read at the store layer.
- Embeddings stored as plaintext strings via `_format_vector()` helper: `"[0.1,0.2,0.3]"` → cast to `::vector` in SQL.
- Vector search: `1 - (embedding <=> $N::vector)` for cosine similarity; `ORDER BY embedding <=> $N::vector` for nearest-neighbor.
- `supersede_memory()` uses `async with conn.transaction()` for atomic insert+update (only method requiring a transaction).
- `delete_memory()` and `delete_memories_by_source()` are soft-deletes (set `status='deleted'`), not `DELETE FROM`.
- `delete_conversation()` is a hard delete (cascades to messages via FK).
- `asyncpg.Pool.execute()` returns status string like `"UPDATE 5"` or `"DELETE 1"` — parse with `result.split()[-1]`.
- JSONB columns (`tool_calls`, `tool_results`, `extracted_facts`, `dedup_results`) passed as `json.dumps()` with `::jsonb` cast.
- `get_recent_messages()` uses subquery pattern: inner query gets last N DESC, outer re-sorts ASC for chronological order.
- `update_conversation()` uses `COALESCE($N, column)` pattern for optional field updates without overwriting existing values.
- All methods use `pool.fetch/fetchrow/fetchval/execute` directly — no `pool.acquire()` except in `supersede_memory()` transaction.
