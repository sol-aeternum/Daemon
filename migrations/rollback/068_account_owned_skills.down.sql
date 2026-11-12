-- Rollback for 068_account_owned_skills. Destroys every account-owned and
-- shipped skill row and every revision. The file-backed store and
-- skill_projections are untouched by 068 and by this rollback.
SET LOCAL lock_timeout = '1s';
DROP TABLE IF EXISTS skill_revisions;
DROP TABLE IF EXISTS skills;
DROP TABLE IF EXISTS skill_store_key;
