-- #731 native fence, binder side (owner-approved). Any INSERT into the H
-- binding (including COPY) takes advisory key (1145588051, 1) exclusively for
-- its transaction and refuses while any unselected host holds it shared.
-- ENABLE ALWAYS keeps it firing under session_replication_role=replica.
-- Unselected hosts refuse unless this exact trigger and body are present.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE FUNCTION public.history_native_fence() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NOT pg_catalog.pg_try_advisory_xact_lock(1145588051, 1) THEN
  RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='history_native_hosts_active';
 END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.history_native_fence() FROM PUBLIC;

CREATE TRIGGER task_history_binding_native_fence BEFORE INSERT ON public.task_history_binding
 FOR EACH STATEMENT EXECUTE FUNCTION public.history_native_fence();
ALTER TABLE public.task_history_binding ENABLE ALWAYS TRIGGER task_history_binding_native_fence;
