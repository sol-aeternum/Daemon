-- Migration: 069_skill_consolidation_log_content_free
-- Removes skill content from the consolidation audit log
-- (docs/ACCOUNT_OWNED_SKILLS_DESIGN.md §3.1, owner decision O7).
--
-- The log held plaintext copies of skill names and descriptions, slugs
-- derived from skill names, and free-text reasons written by a model. Skills
-- are now encrypted at rest, so the log references a skill only by its
-- internal id and records a reason as one of a fixed set of codes.
--
-- Existing rows keep their action, status, similarity, usage count and times.
-- Their slugs cannot be mapped to an id (the file-backed skills they named
-- have no owner and are not imported), so those become NULL, and their
-- reasons become 'legacy'.

DROP INDEX IF EXISTS idx_skill_consolidation_log_skill;

ALTER TABLE skill_consolidation_log
    DROP COLUMN IF EXISTS skill_name,
    DROP COLUMN IF EXISTS skill_description;

ALTER TABLE skill_consolidation_log
    ALTER COLUMN skill_id TYPE UUID USING NULL::uuid,
    ALTER COLUMN target_skill_id TYPE UUID USING NULL::uuid;

UPDATE skill_consolidation_log SET reason = 'legacy';

ALTER TABLE skill_consolidation_log
    ALTER COLUMN reason SET DEFAULT 'unspecified',
    ADD CONSTRAINT skill_consolidation_log_reason_check CHECK (reason IN (
        'legacy',
        'unspecified',
        'model_merge',
        'merged_into',
        'model_delete',
        'model_stale',
        'not_candidate',
        'target_not_candidate',
        'not_granted',
        'merge_failed',
        'delete_failed'
    ));

CREATE INDEX IF NOT EXISTS idx_skill_consolidation_log_skill
    ON skill_consolidation_log(skill_id)
    WHERE skill_id IS NOT NULL;

COMMENT ON COLUMN skill_consolidation_log.skill_id IS
    'Internal id of the skill acted on (skills.id). Not a foreign key, so the '
    'audit row survives deletion of the skill. NULL for rows written before '
    'migration 069.';

COMMENT ON COLUMN skill_consolidation_log.reason IS
    'Fixed reason code. Never free text: the log holds no skill content.';
