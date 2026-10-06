-- Rollback: 044_durable_tasks
-- Drops durable task state. Disable the durable-chat flag first: accepted tasks
-- that have not finished are lost with these tables, while their conversation
-- and user messages remain.

DROP TABLE IF EXISTS task_events;
DROP INDEX IF EXISTS idx_task_operations_task;
DROP TABLE IF EXISTS task_operations;
DROP TABLE IF EXISTS task_attempts;
DROP INDEX IF EXISTS idx_tasks_user_created;
DROP INDEX IF EXISTS idx_tasks_dispatch_lease;
DROP INDEX IF EXISTS idx_tasks_dispatch_queued;
DROP INDEX IF EXISTS uq_tasks_conversation_active;
DROP INDEX IF EXISTS uq_tasks_user_idempotency;
DROP TABLE IF EXISTS tasks;
