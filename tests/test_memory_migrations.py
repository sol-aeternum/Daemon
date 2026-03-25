from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_voyage_up_migration_clears_embeddings_before_dimension_change() -> None:
    sql = _read("migrations/019_voyage_embedding_migration.sql")

    assert "UPDATE memories" in sql
    assert "SET embedding = NULL" in sql
    assert "ALTER COLUMN embedding TYPE vector(1024)" in sql
    assert "ALTER COLUMN embedding_model SET DEFAULT 'voyage-4-large'" in sql


def test_voyage_down_migration_clears_embeddings_before_dimension_change() -> None:
    sql = _read("migrations/rollback/019_voyage_embedding_migration.down.sql")

    assert "UPDATE memories" in sql
    assert "SET embedding = NULL" in sql
    assert "ALTER COLUMN embedding TYPE vector(1536)" in sql
    assert "ALTER COLUMN embedding_model SET DEFAULT 'text-embedding-3-small'" in sql
