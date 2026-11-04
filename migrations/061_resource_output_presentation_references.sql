-- Source-only seventh P observation capability. No roles, grants or table changes.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE FUNCTION public.resource_output_reference_principal() RETURNS pg_catalog.void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE who pg_catalog.oid; proc_oid pg_catalog.oid;
BEGIN
 PERFORM public.resource_output_namespace_principal();
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER;
 proc_oid:='public.resource_output_presentation_references(pg_catalog.jsonb,pg_catalog.jsonb)'::pg_catalog.regprocedure;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=proc_oid AND pg_has_role(who,p.proowner,'MEMBER'))
 OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid=proc_oid AND a.grantee=who AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid=proc_oid AND a.grantee<>p.proowner AND (a.grantee<>who OR a.privilege_type<>'EXECUTE' OR a.is_grantable))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid='public.resource_output_reference_principal()'::pg_catalog.regprocedure AND a.grantee<>p.proowner)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
END $$;

CREATE FUNCTION public.resource_output_presentation_references(p pg_catalog.jsonb,pins pg_catalog.jsonb) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE t public.tasks; pin pg_catalog.jsonb; canonical pg_catalog.jsonb; matched pg_catalog.jsonb;
 records pg_catalog.jsonb:='[]'::pg_catalog.jsonb;
BEGIN
 PERFORM public.resource_output_reference_principal();
 t:=public.resource_output_presentation_scope(p);
 IF pins IS NULL OR jsonb_typeof(pins)<>'array'
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 IF jsonb_array_length(pins)>100
 OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(pins))
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 FOR pin IN SELECT value FROM jsonb_array_elements(pins) LOOP
  IF jsonb_typeof(pin)<>'object'
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
  canonical:=jsonb_build_object('resource_id',((pin->>'resource_id')::pg_catalog.uuid)::pg_catalog.text,
   'version_id',((pin->>'version_id')::pg_catalog.uuid)::pg_catalog.text);
  IF pin<>canonical OR pin @> '{"resource_id":null}' OR pin @> '{"version_id":null}'
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
  -- Exact source-task output matches only. Two rows are enough to refuse an
  -- ambiguity; never return a prefix or silently choose a reference.
  SELECT COALESCE(jsonb_agg(to_jsonb(r)),'[]'::pg_catalog.jsonb) INTO matched FROM (
   SELECT r.* FROM public.resource_references r JOIN public.resource_versions v
   ON v.account_id=r.account_id AND v.resource_id=r.resource_id AND v.version_id=r.version_id AND v.generation=r.generation
   WHERE r.account_id=t.user_id AND r.generation=(p->>'generation')::pg_catalog.int8
   AND r.task_id=t.id AND r.message_id=t.result_message_id AND r.conversation_id=t.conversation_id AND r.role='output'
   AND r.resource_id=(pin->>'resource_id')::pg_catalog.uuid AND r.version_id=(pin->>'version_id')::pg_catalog.uuid
   ORDER BY r.reference_id LIMIT 2 FOR SHARE OF r,v
  ) r;
  IF jsonb_array_length(matched)<>1
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
  records:=records||matched;
 END LOOP;
 RETURN jsonb_build_object('schema_version',1,'output_references',records);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused';
END $$;

DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname IN ('resource_output_reference_principal','resource_output_presentation_references')
 AND a.grantee<>p.proowner AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable))
 THEN RAISE EXCEPTION 'resource_output_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN ('resource_output_reference_principal','resource_output_presentation_references') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
