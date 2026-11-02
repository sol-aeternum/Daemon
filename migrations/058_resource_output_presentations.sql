-- Source-only bounded P presentation authority; no roles, grants or runtime host.
-- The caller owns the transaction and must independently qualify H and ciphertext.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE FUNCTION public.resource_output_presentation_principal() RETURNS pg_catalog.void
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

CREATE FUNCTION public.resource_output_presentation_scope(p pg_catalog.jsonb) RETURNS public.tasks
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE c public.attempt_lifecycle_control; t public.tasks;
BEGIN
 PERFORM public.resource_output_presentation_principal();
 c:=public.attempt_lifecycle_lock(p);
 IF c.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.entitlement_accounts
 WHERE user_id=(p->>'account_id')::pg_catalog.uuid AND status='active' AND content_generation=(p->>'generation')::pg_catalog.int8)
 OR EXISTS(SELECT 1 FROM public.account_deletions WHERE account_id=(p->>'account_id')::pg_catalog.uuid AND closed_at IS NULL)
 OR EXISTS(SELECT 1 FROM public.account_suspension_causes WHERE account_id=(p->>'account_id')::pg_catalog.uuid)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 SELECT * INTO t FROM public.tasks WHERE id=(p->>'task_id')::pg_catalog.uuid AND user_id=(p->>'account_id')::pg_catalog.uuid FOR UPDATE;
 IF t.id IS NULL
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 PERFORM 1 FROM public.conversations WHERE id=t.conversation_id AND user_id=t.user_id FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 PERFORM 1 FROM public.messages WHERE id=t.result_message_id AND user_id=t.user_id
 AND conversation_id=t.conversation_id AND role='assistant' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 RETURN t;
END $$;

CREATE FUNCTION public.resource_output_presentation_snapshot(p pg_catalog.jsonb) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE t public.tasks; records pg_catalog.jsonb;
BEGIN
 t:=public.resource_output_presentation_scope(p);
 SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.created_at,r.presentation_id),'[]'::pg_catalog.jsonb) INTO records
 FROM (SELECT * FROM public.resource_presentations WHERE account_id=t.user_id AND source_message_id=t.result_message_id
 ORDER BY created_at,presentation_id LIMIT 101 FOR UPDATE) r;
 IF jsonb_array_length(records)>100 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 -- Original encrypted input is returned only for trusted Python origin validation.
 RETURN jsonb_build_object('task',jsonb_build_object('id',t.id,'user_id',t.user_id,
 'conversation_id',t.conversation_id,'result_message_id',t.result_message_id,
 'input_version',t.input_version,'input_ciphertext',t.input_ciphertext),
 'records',records,'observed_at',clock_timestamp());
END $$;

CREATE FUNCTION public.resource_output_presentation_insert(p pg_catalog.jsonb,ident pg_catalog.uuid,pins pg_catalog.jsonb,token pg_catalog.text,created pg_catalog.timestamptz,record_limit pg_catalog.int4) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE t public.tasks; pin pg_catalog.jsonb; canonical pg_catalog.jsonb; previous public.resource_presentations; count_records pg_catalog.int8;
BEGIN
 t:=public.resource_output_presentation_scope(p);
 IF ident IS NULL OR created IS NULL OR NOT isfinite(created) OR token IS NULL
 OR octet_length(token) NOT BETWEEN 1 AND 131072 OR record_limit IS NULL OR record_limit NOT BETWEEN 1 AND 100
 OR pins IS NULL OR jsonb_typeof(pins)<>'array' OR jsonb_array_length(pins)>100
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 FOR pin IN SELECT value FROM jsonb_array_elements(pins) LOOP
  canonical:=jsonb_build_object('resource_id',((pin->>'resource_id')::pg_catalog.uuid)::pg_catalog.text,'version_id',((pin->>'version_id')::pg_catalog.uuid)::pg_catalog.text);
  IF pin<>canonical OR pin @> '{"resource_id":null}' OR pin @> '{"version_id":null}'
  OR NOT EXISTS(SELECT 1 FROM public.resource_references r JOIN public.resource_versions v
   ON v.account_id=r.account_id AND v.version_id=r.version_id AND v.resource_id=r.resource_id AND v.generation=r.generation
   WHERE r.account_id=t.user_id AND r.generation=(p->>'generation')::pg_catalog.int8 AND r.task_id=t.id
   AND r.message_id=t.result_message_id AND r.conversation_id=t.conversation_id AND r.role='output'
   AND r.resource_id=(pin->>'resource_id')::pg_catalog.uuid AND r.version_id=(pin->>'version_id')::pg_catalog.uuid)
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 END LOOP;
 IF (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(pins))
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 SELECT * INTO previous FROM public.resource_presentations WHERE presentation_id=ident FOR UPDATE;
 IF previous.presentation_id IS NOT NULL THEN
  IF previous.account_id<>t.user_id OR previous.generation<>(p->>'generation')::pg_catalog.int8
  OR previous.source_message_id IS DISTINCT FROM t.result_message_id OR previous.envelope_ciphertext<>token OR previous.created_at<>created
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
  RETURN jsonb_build_object('presentation_id',ident,'inserted',false,'outcome','pending_commit');
 END IF;
 SELECT count(*) INTO count_records FROM public.resource_presentations WHERE account_id=t.user_id AND source_message_id=t.result_message_id;
 IF count_records>=record_limit THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 PERFORM public.attempt_lifecycle_assert_open(p);
 INSERT INTO public.resource_presentations(presentation_id,account_id,generation,source_message_id,envelope_ciphertext,created_at)
 VALUES(ident,t.user_id,(p->>'generation')::pg_catalog.int8,t.result_message_id,token,created);
 PERFORM public.attempt_lifecycle_assert_open(p);
 RETURN jsonb_build_object('presentation_id',ident,'inserted',true,'outcome','pending_commit');
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR unique_violation THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused';
END $$;

DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname LIKE 'resource_output_presentation_%' AND a.grantee<>p.proowner
 AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable))
 THEN RAISE EXCEPTION 'resource_output_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname LIKE 'resource_output_presentation_%' LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
