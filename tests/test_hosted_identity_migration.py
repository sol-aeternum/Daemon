from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_hosted_identity_migration_enforces_one_provider_row_per_user() -> None:
    sql = _read("migrations/032_hosted_identity_claim.sql")

    assert "idx_identity_providers_user_provider_unique" in sql
    assert "ON identity_providers(user_id, provider)" in sql


def test_hosted_identity_migration_expires_stale_invites_before_insert() -> None:
    sql = _read("migrations/032_hosted_identity_claim.sql")

    assert "expire_stale_signup_invites_before_insert" in sql
    assert "BEFORE INSERT ON signup_invites" in sql
    assert "SET status = 'expired'" in sql
    assert "expires_at <= NOW()" in sql


def test_hosted_identity_migration_does_not_use_now_in_partial_invite_index_predicate() -> None:
    sql = _read("migrations/032_hosted_identity_claim.sql")

    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_signup_invites_active_email" in sql
    assert "WHERE status = 'active'" in sql
    assert "WHERE status = 'active' AND expires_at > NOW()" not in sql


def test_hosted_identity_migration_aborts_when_legacy_emails_normalize_to_duplicates() -> None:
    sql = _read("migrations/032_hosted_identity_claim.sql")

    assert "duplicate normalized_email candidate" in sql
    assert "LOWER(TRIM(email)) AS normalized_candidate" in sql
    assert "Resolve duplicates before applying migration 032" in sql
