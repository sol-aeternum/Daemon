-- Source-only eighth P reader. No roles, grants, table or stored-format changes.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

-- Preserve public interfaces; enforce the existing clean-P membership rule even
-- when a forbidden capability is available only through SET ROLE.
CREATE OR REPLACE FUNCTION public.resource_output_presentation_principal() RETURNS pg_catalog.void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE who pg_catalog.oid; owner_id pg_catalog.oid; signature pg_catalog.text; proc_oid pg_catalog.oid;
BEGIN
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER AND rolcanlogin
 AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);
 -- The older lifecycle helpers use implicit builtin type names. Do not enter
 -- them with a caller-owned temporary namespace that could shadow those names.
 IF who IS NULL OR pg_my_temp_schema()<>0 OR current_setting('role')<>'none'
 OR has_database_privilege(SESSION_USER,current_database(),'CREATE')
 OR has_schema_privilege(SESSION_USER,'public','CREATE')
 OR NOT has_schema_privilege(SESSION_USER,'public','USAGE')
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
 (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
 OR r.rolname IN ('pg_execute_server_program','pg_read_server_files','pg_write_server_files'))
 AND pg_has_role(who,r.oid,'MEMBER'))

 -- Noninherited grants still provide a SET ROLE path. Qualify membership in
 -- actual native-capability/table grantees, not only currently inherited rights.
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND (p.proname LIKE 'attempt_lifecycle_%'
 OR p.proname LIKE 'task_inference_%' OR p.proname LIKE 'compute_metadata_%'
 OR p.proname LIKE 'artifact_output_%' OR p.proname LIKE 'task_history_%')
 AND CASE WHEN a.grantee=0 THEN false ELSE pg_has_role(who,a.grantee,'MEMBER') END)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL aclexplode(c.relacl) a
 WHERE n.nspname='public' AND c.relkind IN ('r','p','S')
 AND CASE WHEN a.grantee=0 THEN false ELSE pg_has_role(who,a.grantee,'MEMBER') END)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute c
 JOIN pg_catalog.pg_class t ON t.oid=c.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
 CROSS JOIN LATERAL aclexplode(c.attacl) a
 WHERE n.nspname='public' AND c.attnum>0 AND NOT c.attisdropped
 AND CASE WHEN a.grantee=0 THEN false ELSE pg_has_role(who,a.grantee,'MEMBER') END)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname='public' AND pg_has_role(who,n.nspowner,'MEMBER'))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_database d WHERE d.datname=current_database() AND pg_has_role(who,d.datdba,'MEMBER'))
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 FOREACH signature IN ARRAY ARRAY[
 'public.resource_output_presentation_snapshot(pg_catalog.jsonb)',
 'public.resource_output_presentation_insert(pg_catalog.jsonb,pg_catalog.uuid,pg_catalog.jsonb,pg_catalog.text,pg_catalog.timestamptz,pg_catalog.int4)'] LOOP
  proc_oid:=signature::pg_catalog.regprocedure;
  SELECT proowner INTO owner_id FROM pg_catalog.pg_proc WHERE oid=proc_oid;
  IF pg_has_role(who,owner_id,'MEMBER')
  OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   WHERE p.oid=proc_oid AND a.grantee=who AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
  OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   WHERE p.oid=proc_oid AND a.grantee<>p.proowner AND (a.grantee<>who OR a.privilege_type<>'EXECUTE' OR a.is_grantable))
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname IN ('resource_output_presentation_principal','resource_output_presentation_scope')
 AND a.grantee<>p.proowner)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 -- P cannot be A/W, an owner, or a login with direct native/C1 table privileges.
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND
 (pg_has_role(who,c.relowner,'MEMBER')
 OR has_table_privilege(who,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 OR has_any_column_privilege(who,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='S' AND
 (pg_has_role(who,c.relowner,'MEMBER') OR has_sequence_privilege(who,c.oid,'USAGE,SELECT,UPDATE')))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND (p.proname LIKE 'attempt_lifecycle_%'
 OR p.proname LIKE 'task_inference_%' OR p.proname LIKE 'compute_metadata_%'
 OR p.proname IN ('resource_output_presentation_principal','resource_output_presentation_scope'))
 AND (pg_has_role(who,p.proowner,'MEMBER') OR has_function_privilege(who,p.oid,'EXECUTE')))
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
END $$;

CREATE FUNCTION public.resource_output_preparation_principal() RETURNS pg_catalog.void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE who pg_catalog.oid; proc_oid pg_catalog.oid;
BEGIN
 PERFORM public.resource_output_reference_principal();
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER;
 proc_oid:='public.resource_output_preparation_snapshot(pg_catalog.jsonb)'::pg_catalog.regprocedure;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=proc_oid AND pg_has_role(who,p.proowner,'MEMBER'))
 OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid=proc_oid AND a.grantee=who AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid=proc_oid AND a.grantee<>p.proowner AND (a.grantee<>who OR a.privilege_type<>'EXECUTE' OR a.is_grantable))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.oid='public.resource_output_preparation_principal()'::pg_catalog.regprocedure AND a.grantee<>p.proowner)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
END $$;

CREATE FUNCTION public.resource_output_preparation_snapshot(p pg_catalog.jsonb) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE t public.tasks; attempt pg_catalog.uuid;
BEGIN
 PERFORM public.resource_output_preparation_principal();
 -- New preparation requires current native admission, not historical readback.
 PERFORM public.attempt_lifecycle_assert_open(p);
 t:=public.resource_output_presentation_scope(p);
 SELECT id INTO attempt FROM public.task_attempts
 WHERE task_id=t.id AND epoch=(p->>'epoch')::pg_catalog.int8 FOR UPDATE;
 IF attempt IS NULL THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 -- Include all source/attempt lock waits in the final native lease/Stop check.
 PERFORM public.attempt_lifecycle_assert_open(p);
 RETURN jsonb_build_object('schema_version',1,'attempt_id',attempt,
 'task',jsonb_build_object('id',t.id,'user_id',t.user_id,
 'conversation_id',t.conversation_id,'result_message_id',t.result_message_id,
 'input_version',t.input_version,'input_ciphertext',t.input_ciphertext));
END $$;

DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname IN ('resource_output_preparation_principal','resource_output_preparation_snapshot')
 AND a.grantee<>p.proowner AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable))
 THEN RAISE EXCEPTION 'resource_output_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN ('resource_output_preparation_principal','resource_output_preparation_snapshot') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
