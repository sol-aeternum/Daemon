SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';
ALTER TABLE retrieval_log DROP COLUMN query_role;
