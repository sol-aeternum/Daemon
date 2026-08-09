-- Migration: 036_skill_consolidation_pending_status
-- Allow destructive skill consolidation actions to be audited before they run.

ALTER TABLE skill_consolidation_log
    DROP CONSTRAINT IF EXISTS skill_consolidation_log_status_check;

ALTER TABLE skill_consolidation_log
    ADD CONSTRAINT skill_consolidation_log_status_check
    CHECK (status IN ('pending', 'applied', 'skipped', 'recorded', 'failed'));

COMMENT ON COLUMN skill_consolidation_log.status IS
    'Lifecycle status for consolidation actions. Destructive deletes are written '
    'as pending before deletion, then updated to applied or failed.';
