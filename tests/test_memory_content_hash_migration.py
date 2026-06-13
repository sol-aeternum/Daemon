from __future__ import annotations

from pathlib import Path


def test_memory_content_hash_migration_adds_partial_unique_index() -> None:
    sql = Path("migrations/036_memory_content_hash.sql").read_text()

    assert "ADD COLUMN IF NOT EXISTS content_hash TEXT" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_active_content_hash_unique" in sql
    assert "ON memories(user_id, content_hash)" in sql
    assert "status = 'active'" in sql
    assert "valid_to IS NULL" in sql
