-- Rollback: 069_skill_consolidation_log_content_free
-- Restores the previous column shapes. The dropped captions, slugs and
-- free-text reasons are not recoverable; restored columns are NULL and ids
-- written after the migration are discarded.

DROP INDEX IF EXISTS idx_skill_consolidation_log_skill;

ALTER TABLE skill_consolidation_log
    DROP CONSTRAINT IF EXISTS skill_consolidation_log_reason_check,
    ALTER COLUMN reason SET DEFAULT '';

ALTER TABLE skill_consolidation_log
    ALTER COLUMN skill_id TYPE TEXT USING NULL::text,
    ALTER COLUMN target_skill_id TYPE TEXT USING NULL::text;

ALTER TABLE skill_consolidation_log
    ADD COLUMN IF NOT EXISTS skill_name TEXT,
    ADD COLUMN IF NOT EXISTS skill_description TEXT;

CREATE INDEX IF NOT EXISTS idx_skill_consolidation_log_skill
    ON skill_consolidation_log(skill_id)
    WHERE skill_id IS NOT NULL;
