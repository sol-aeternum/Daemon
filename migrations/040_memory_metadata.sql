-- Migration: 040_memory_metadata
-- Adds the `metadata` JSONB document to memories.
--
-- Why: `MemoryStore.supersede_memory()` and `MemoryStore.update_memory_metadata()`
-- read and write `memories.metadata`, and `orchestrator/eval/chunk_harness.py`
-- (`insert_chunk_memories()`) inserts it directly. No earlier migration created
-- the column, so a fully migrated database aborted those code paths with
-- `UndefinedColumnError: column "metadata" of relation "memories" does not exist`.
--
-- Idempotent: uses IF NOT EXISTS. Adding a column with a non-volatile default
-- does not rewrite the table on PostgreSQL 11+, so this is a metadata-only change.

ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN memories.metadata IS
    'JSONB annotations attached to a memory (e.g. contradiction/merge evidence); defaults to an empty object.';

-- Existing rows receive the column default from ADD COLUMN; NOT NULL already
-- prevents nulls, so no redundant full-table UPDATE is needed.

ANALYZE memories;
