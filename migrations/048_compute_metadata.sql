-- EXPERIMENTAL 048_compute_metadata.sql, storage only. Runner supplies transaction.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';
DO $$ BEGIN
 IF pg_catalog.to_regclass('public.entitlement_accounts') IS NULL OR pg_catalog.to_regclass('public.entitlement_reservations') IS NULL
 THEN RAISE EXCEPTION 'compute_metadata_base_schema_missing'; END IF;
END $$;
CREATE TABLE public.compute_scope_metadata (
 id uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES public.entitlement_accounts(user_id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
CREATE INDEX compute_scope_metadata_account ON public.compute_scope_metadata(account_id,id);
CREATE TABLE public.compute_reservation_metadata (
 reservation_id uuid PRIMARY KEY REFERENCES public.entitlement_reservations(id) ON DELETE CASCADE,
 metadata_scope_id uuid NOT NULL REFERENCES public.compute_scope_metadata(id) ON DELETE CASCADE,
 runtime_identity uuid NOT NULL UNIQUE,
 dispatch_nonce uuid NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
CREATE INDEX compute_reservation_metadata_scope ON public.compute_reservation_metadata(metadata_scope_id,reservation_id);
COMMENT ON TABLE public.compute_scope_metadata IS 'Immutable storage identity; not entitlement scope_id or origin/cleanup authority.';
COMMENT ON TABLE public.compute_reservation_metadata IS 'Metadata extension only. No financial copy, dispatch state or clock-order guarantee.';
CREATE FUNCTION public.compute_metadata_check_account(p_account uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_status text;
BEGIN
 IF pg_catalog.current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='metadata_isolation_required'; END IF;
 IF p_account IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
 SELECT status INTO v_status FROM public.entitlement_accounts WHERE user_id=p_account FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='metadata_account_unavailable'; END IF;
 RETURN v_status;
END $$;
CREATE FUNCTION public.compute_metadata_create_scope(p_account uuid,p_scope uuid)
RETURNS public.compute_scope_metadata LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_status text; v_row public.compute_scope_metadata;
BEGIN
 v_status:=public.compute_metadata_check_account(p_account);
 IF p_scope IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
 SELECT * INTO v_row FROM public.compute_scope_metadata WHERE id=p_scope FOR UPDATE;
 IF FOUND THEN
   IF v_row.account_id<>p_account THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
   RETURN v_row;
 END IF;
 IF v_status<>'active' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='metadata_account_unavailable'; END IF;
 BEGIN
   INSERT INTO public.compute_scope_metadata(id,account_id) VALUES(p_scope,p_account) RETURNING * INTO v_row;
 EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END;
 RETURN v_row;
END $$;
CREATE FUNCTION public.compute_metadata_attach(p_account uuid,p_scope uuid,p_reservation uuid,p_runtime uuid,p_nonce uuid)
RETURNS public.compute_reservation_metadata LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_status text; v_scope public.compute_scope_metadata; v_res public.entitlement_reservations; v_row public.compute_reservation_metadata;
BEGIN
 v_status:=public.compute_metadata_check_account(p_account);
 IF p_scope IS NULL OR p_reservation IS NULL OR p_runtime IS NULL OR p_nonce IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
 SELECT * INTO v_scope FROM public.compute_scope_metadata WHERE id=p_scope FOR UPDATE;
 IF NOT FOUND OR v_scope.account_id<>p_account THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
 SELECT * INTO v_res FROM public.entitlement_reservations WHERE id=p_reservation FOR UPDATE;
 IF NOT FOUND OR v_res.user_id<>p_account THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
 SELECT * INTO v_row FROM public.compute_reservation_metadata WHERE reservation_id=p_reservation FOR UPDATE;
 IF FOUND THEN
   IF v_row.metadata_scope_id<>p_scope OR v_row.runtime_identity<>p_runtime OR v_row.dispatch_nonce<>p_nonce THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
   RETURN v_row;
 END IF;
 IF v_status<>'active' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='metadata_account_unavailable'; END IF;
 -- NO comparison between application-clock reservation time and DB-clock scope time.
 -- Explicit attachment to an existing open owned row proves no temporal provenance.
 IF v_res.status<>'open' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
 BEGIN
   INSERT INTO public.compute_reservation_metadata(reservation_id,metadata_scope_id,runtime_identity,dispatch_nonce)
     VALUES(p_reservation,p_scope,p_runtime,p_nonce) RETURNING * INTO v_row;
 EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END;
 RETURN v_row;
END $$;
CREATE FUNCTION public.compute_metadata_read_scope(p_account uuid,p_scope uuid)
RETURNS public.compute_scope_metadata LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_row public.compute_scope_metadata;
BEGIN
 PERFORM public.compute_metadata_check_account(p_account);
 IF p_scope IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
 SELECT * INTO v_row FROM public.compute_scope_metadata WHERE id=p_scope AND account_id=p_account;
 RETURN v_row;
END $$;
CREATE FUNCTION public.compute_metadata_read_binding(p_account uuid,p_reservation uuid)
RETURNS public.compute_reservation_metadata LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_row public.compute_reservation_metadata;
BEGIN
 PERFORM public.compute_metadata_check_account(p_account);
 IF p_reservation IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='identity_conflict'; END IF;
 SELECT m.* INTO v_row FROM public.compute_reservation_metadata m JOIN public.compute_scope_metadata s ON s.id=m.metadata_scope_id
 JOIN public.entitlement_reservations r ON r.id=m.reservation_id
 WHERE m.reservation_id=p_reservation AND s.account_id=p_account AND r.user_id=p_account;
 RETURN v_row;
END $$;
-- Check inherited table grants BEFORE narrowing them, including PUBLIC defaults.
-- PostgreSQL intrinsically gives PUBLIC EXECUTE on new functions; permit that
-- intrinsic ACL only here and revoke it below, atomically before publication.
DO $$ BEGIN
 IF EXISTS (
   SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
   WHERE c.oid IN ('public.compute_scope_metadata'::regclass,'public.compute_reservation_metadata'::regclass) AND a.grantee<>c.relowner
 ) OR EXISTS (
   SELECT 1 FROM pg_catalog.pg_proc f JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace
   CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(f.proacl,pg_catalog.acldefault('f',f.proowner))) a
   WHERE n.nspname='public' AND f.proname IN ('compute_metadata_check_account','compute_metadata_create_scope','compute_metadata_attach','compute_metadata_read_scope','compute_metadata_read_binding')
    AND a.grantee<>f.proowner AND NOT (a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
 ) THEN RAISE EXCEPTION 'compute_metadata_unsafe_default_privileges'; END IF;
END $$;
REVOKE ALL ON TABLE public.compute_scope_metadata,public.compute_reservation_metadata FROM PUBLIC;
REVOKE ALL ON FUNCTION public.compute_metadata_check_account(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.compute_metadata_create_scope(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.compute_metadata_attach(uuid,uuid,uuid,uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.compute_metadata_read_scope(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.compute_metadata_read_binding(uuid,uuid) FROM PUBLIC;
-- No roles, memberships or application grants. Precondition remains mandatory.
