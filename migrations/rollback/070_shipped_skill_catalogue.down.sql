-- Rollback for 070_shipped_skill_catalogue. Drops the applied-version record.
-- Shipped skill rows are left as they are. Without the table the loader
-- cannot fence releases and publishes nothing.
SET LOCAL lock_timeout = '1s';
DROP TABLE IF EXISTS shipped_skill_catalogue;
