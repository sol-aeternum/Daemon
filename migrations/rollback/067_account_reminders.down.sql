-- Rollback for 067_account_reminders. Destroys every stored reminder note.
SET LOCAL lock_timeout = '1s';
DROP TABLE IF EXISTS reminders;
