-- ONE guarded rollback entry point; execute as migration owner, not inside another transaction.
-- Supported operator entry: fresh psql -X -v ON_ERROR_STOP=1 -f this-file.
BEGIN;
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';
-- Same advisory key as scripts/migrate.py; xact and session locks share its namespace.
DO $$ BEGIN
 IF NOT pg_catalog.pg_try_advisory_xact_lock(x'4441454D4F4E'::bigint) THEN RAISE EXCEPTION 'migration_lock_unavailable'; END IF;
 IF (SELECT count(*) FROM public._migrations WHERE filename='048_compute_metadata.sql')<>1 THEN
   RAISE EXCEPTION 'compute_metadata_rollback_marker_mismatch';
 END IF;
END $$;
LOCK TABLE public.compute_scope_metadata,public.compute_reservation_metadata IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.compute_scope_metadata) OR EXISTS(SELECT 1 FROM public.compute_reservation_metadata)
 THEN RAISE EXCEPTION 'compute_metadata_rollback_refused_nonempty'; END IF;
END $$;
DROP FUNCTION public.compute_metadata_read_binding(uuid,uuid);
DROP FUNCTION public.compute_metadata_read_scope(uuid,uuid);
DROP FUNCTION public.compute_metadata_attach(uuid,uuid,uuid,uuid,uuid);
DROP FUNCTION public.compute_metadata_create_scope(uuid,uuid);
DROP FUNCTION public.compute_metadata_check_account(uuid);
DROP TABLE public.compute_reservation_metadata;
DROP TABLE public.compute_scope_metadata;
DELETE FROM public._migrations WHERE filename='048_compute_metadata.sql';
COMMIT;
-- Any error aborts the transaction: no marker/schema partial rollback. No CASCADE.
