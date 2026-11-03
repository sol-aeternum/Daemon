-- Source-only bounded P read capability. No roles, grants, tables or appliers.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE FUNCTION public.resource_output_namespace_principal() RETURNS pg_catalog.void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE who pg_catalog.oid; proc_oid pg_catalog.oid;
BEGIN
 PERFORM public.resource_output_catalog_principal();
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER;
 proc_oid:='public.resource_output_namespace_inventory(pg_catalog.uuid,pg_catalog.int4)'::pg_catalog.regprocedure;
 IF current_setting('transaction_isolation')<>'read committed'
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=proc_oid AND pg_has_role(who,p.proowner,'MEMBER'))
 OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid=proc_oid AND a.grantee=who AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid=proc_oid AND a.grantee<>p.proowner AND (a.grantee<>who OR a.privilege_type<>'EXECUTE' OR a.is_grantable))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid='public.resource_output_namespace_principal()'::pg_catalog.regprocedure AND a.grantee<>p.proowner)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
END $$;

CREATE FUNCTION public.resource_output_namespace_inventory(expected_journal pg_catalog.uuid,record_limit pg_catalog.int4) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE observed pg_catalog.jsonb;
BEGIN
 PERFORM public.resource_output_namespace_principal();
 -- Required per-call qualification budget, not a production default or pruning
 -- policy. Complete arrays or refusal; never an apparently complete prefix.
 IF expected_journal IS NULL OR record_limit IS NULL OR record_limit NOT BETWEEN 1 AND 10000
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 -- All three projections share one statement/MVCC snapshot. Deliberately read
 -- the WHOLE inventory, including other accounts and unexpected journal IDs.
 WITH marks AS MATERIALIZED (
  SELECT * FROM public.deletion_journal_applied ORDER BY journal_id,seq LIMIT record_limit+1
 ), births AS MATERIALIZED (
  SELECT * FROM public.artifact_namespaces ORDER BY journal_id,birth_seq LIMIT record_limit+1
 )
 SELECT jsonb_build_object('schema_version',1,
 'journal_id',(SELECT journal_id FROM public.deletion_journal_binding WHERE singleton=true),
 'markers',COALESCE((SELECT jsonb_agg(jsonb_build_object('journal_id',journal_id,'seq',seq,
 'event_id',event_id,'account_id',account_id,'operation_id',operation_id,'entry_digest',encode(entry_digest,'hex')) ORDER BY journal_id,seq) FROM marks),'[]'::pg_catalog.jsonb),
 'namespaces',COALESCE((SELECT jsonb_agg(jsonb_build_object('namespace',namespace,'account_id',account_id,
 'generation',generation,'journal_id',journal_id,'birth_seq',birth_seq,'birth_event_id',birth_event_id,
 'birth_digest',encode(birth_digest,'hex'),'command_digest',encode(command_digest,'hex'),'state',state) ORDER BY journal_id,birth_seq) FROM births),'[]'::pg_catalog.jsonb)) INTO observed;
 IF observed->>'journal_id' IS DISTINCT FROM expected_journal::pg_catalog.text
 OR jsonb_array_length(observed->'markers')>record_limit OR jsonb_array_length(observed->'namespaces')>record_limit
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 RETURN observed; -- Observation only. No binding, namespace or marker mutation.
END $$;

DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname IN ('resource_output_namespace_principal','resource_output_namespace_inventory')
 AND a.grantee<>p.proowner AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable))
 THEN RAISE EXCEPTION 'resource_output_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN ('resource_output_namespace_principal','resource_output_namespace_inventory') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
