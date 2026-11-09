-- Approved source/synthetic slice only. Caller owns the migration transaction.
-- No login, application grant, binding, file, runtime host or recovery activation.
SET LOCAL lock_timeout='1s';
SET LOCAL statement_timeout='5s';
DO $$ BEGIN
 IF CURRENT_USER<>(SELECT pg_get_userbyid(relowner) FROM pg_catalog.pg_class
 WHERE oid='public.task_history_binding'::regclass) THEN
 RAISE EXCEPTION 'task_history_owner_required'; END IF;
END $$;

ALTER TABLE public.task_history_projection DROP CONSTRAINT task_history_projection_kind_check;
ALTER TABLE public.task_history_projection ADD CONSTRAINT task_history_projection_kind_check
 CHECK(kind IN ('ACCEPT_PREPARE','SEND_POSSIBLE','STOP_INTENT','ARTIFACT_PREPARE','TERMINAL_OUTPUT_PREPARE'));

CREATE FUNCTION public.task_history_decode_terminal(frame pg_catalog.bytea) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE n pg_catalog.int8; j pg_catalog.jsonb; d pg_catalog.jsonb; payload pg_catalog.text;
 data_wire pg_catalog.text; envelope_wire pg_catalog.text; k pg_catalog.text;
 keys pg_catalog.text[]:=ARRAY['account_id','account_generation','task_id','result_message_id','acceptance_record_id','epoch','terminal_schema'];
BEGIN
 IF frame IS NULL OR octet_length(frame) NOT BETWEEN 37 AND 8228 THEN RAISE EXCEPTION 'invalid'; END IF;
 n:=get_byte(frame,0)::pg_catalog.int8*16777216+get_byte(frame,1)::pg_catalog.int8*65536+get_byte(frame,2)::pg_catalog.int8*256+get_byte(frame,3);
 IF n NOT BETWEEN 1 AND 8192 OR octet_length(frame)<>n+36
 OR substring(frame FROM (n+5)::pg_catalog.int4 FOR 32) IS DISTINCT FROM
 sha256(convert_to('daemon.restore-history.frame.v3'||chr(10),'UTF8')||substring(frame FROM 1 FOR (n+4)::pg_catalog.int4))
 THEN RAISE EXCEPTION 'invalid'; END IF;
 payload:=convert_from(substring(frame FROM 5 FOR n::pg_catalog.int4),'UTF8'); j:=payload::pg_catalog.jsonb; d:=j->'data';
 IF jsonb_typeof(j) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(j))<>8
 OR NOT j ?& ARRAY['v','history_id','seq','prev_digest','record_id','kind','actor','data']
 OR j->'v' IS DISTINCT FROM '3'::pg_catalog.jsonb OR j->>'v'<>'3'
 OR j->'kind' IS DISTINCT FROM '"TERMINAL_OUTPUT_PREPARE"'::pg_catalog.jsonb
 OR j->'actor' IS DISTINCT FROM '"worker"'::pg_catalog.jsonb
 OR jsonb_typeof(d) IS DISTINCT FROM 'object'
 OR (SELECT count(*) FROM jsonb_object_keys(d))<>cardinality(keys) OR NOT d ?& keys
 OR jsonb_typeof(j->'seq') IS DISTINCT FROM 'number' OR j->>'seq' !~ '^[1-9][0-9]*$'
 OR (j->>'seq')::pg_catalog.int8 NOT BETWEEN 1 AND 16384
 OR jsonb_typeof(j->'prev_digest') IS DISTINCT FROM 'string' OR j->>'prev_digest' !~ '^[0-9a-f]{64}$'
 OR d->'terminal_schema' IS DISTINCT FROM '"native-complete-body-v1"'::pg_catalog.jsonb
 THEN RAISE EXCEPTION 'invalid'; END IF;
 FOREACH k IN ARRAY ARRAY['account_generation','epoch'] LOOP
  IF jsonb_typeof(d->k) IS DISTINCT FROM 'number' OR d->>k !~ '^(0|[1-9][0-9]*)$'
  OR (d->>k)::pg_catalog.int8<0 THEN RAISE EXCEPTION 'invalid'; END IF;
 END LOOP;
 IF (d->>'epoch')::pg_catalog.int8<1 THEN RAISE EXCEPTION 'invalid'; END IF;
 FOREACH k IN ARRAY ARRAY['history_id','record_id'] LOOP
  IF jsonb_typeof(j->k) IS DISTINCT FROM 'string' OR ((j->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM j->>k
  OR (j->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN RAISE EXCEPTION 'invalid'; END IF;
 END LOOP;
 FOREACH k IN ARRAY keys LOOP
  IF k NOT IN ('account_generation','epoch','terminal_schema') THEN
   IF jsonb_typeof(d->k) IS DISTINCT FROM 'string' OR ((d->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM d->>k
   OR (d->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN RAISE EXCEPTION 'invalid'; END IF;
  END IF;
 END LOOP;
 SELECT '{'||string_agg(to_jsonb(key)::pg_catalog.text||':'||value::pg_catalog.text,',' ORDER BY key COLLATE pg_catalog."C")||'}'
 INTO data_wire FROM jsonb_each(d);
 SELECT '{'||string_agg(to_jsonb(key)::pg_catalog.text||':'||CASE WHEN key='data' THEN data_wire ELSE value::pg_catalog.text END,
 ',' ORDER BY key COLLATE pg_catalog."C")||'}' INTO envelope_wire FROM jsonb_each(j);
 IF convert_to(envelope_wire,'UTF8') IS DISTINCT FROM convert_to(payload,'UTF8') THEN RAISE EXCEPTION 'invalid'; END IF;
 RETURN j;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
END $$;

CREATE FUNCTION public.task_history_terminal_project(p_frame pg_catalog.bytea)
RETURNS TABLE(history_id pg_catalog.uuid,seq pg_catalog.int8,record_id pg_catalog.uuid,digest pg_catalog.bytea,frame pg_catalog.bytea,created pg_catalog.bool)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j pg_catalog.jsonb; d pg_catalog.jsonb; b public.task_history_binding; a public.task_history_acceptance;
 t public.tasks; account public.entitlement_accounts; who pg_catalog.oid; writer_ids pg_catalog.oid[];
 top_xid pg_catalog.numeric; native_xid pg_catalog.xid; native_rows pg_catalog.int4:=0;
BEGIN
 PERFORM public.task_history_check_caller(false);
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO writer_ids FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_send_project(bytea)'::regprocedure AND x.grantee<>f.proowner;
 IF cardinality(writer_ids) IS DISTINCT FROM 1 OR who IS DISTINCT FROM writer_ids[1]
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 j:=public.task_history_decode_terminal(p_frame);d:=j->'data';SELECT * INTO b FROM public.task_history_binding;
 SELECT * INTO a FROM public.task_history_acceptance x WHERE x.history_id=b.history_id AND x.record_id=(d->>'acceptance_record_id')::pg_catalog.uuid;
 IF b.history_id IS DISTINCT FROM (j->>'history_id')::pg_catalog.uuid OR b.version<>1 OR b.key_id<>1
 OR a.task_id IS NULL OR (a.account_id,a.account_generation,a.task_id,a.result_message_id) IS DISTINCT FROM
 ((d->>'account_id')::pg_catalog.uuid,(d->>'account_generation')::pg_catalog.int8,(d->>'task_id')::pg_catalog.uuid,(d->>'result_message_id')::pg_catalog.uuid)
 OR a.seq>=(j->>'seq')::pg_catalog.int8 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 SELECT * INTO account FROM public.entitlement_accounts WHERE user_id=a.account_id FOR SHARE;
 IF account.user_id IS NULL OR account.status<>'active' OR account.content_generation<>a.account_generation
 OR EXISTS(SELECT 1 FROM public.account_deletions WHERE account_id=a.account_id AND closed_at IS NULL)
 OR EXISTS(SELECT 1 FROM public.account_suspension_causes WHERE account_id=a.account_id)
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 SELECT * INTO t FROM public.tasks WHERE id=a.task_id FOR UPDATE;
 IF t.id IS NULL OR t.user_id<>a.account_id OR t.result_message_id<>a.result_message_id
 OR t.conversation_id<>a.conversation_id OR t.user_message_id<>a.user_message_id
 OR t.lease_epoch<>(d->>'epoch')::pg_catalog.int8 OR t.status<>'completed' OR t.finished_at IS NULL
 OR t.lease_owner IS NOT NULL OR t.lease_expires_at IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM public.messages m WHERE m.id=a.result_message_id AND m.user_id=a.account_id
 AND m.conversation_id=a.conversation_id AND m.role='assistant' AND m.status='complete' AND m.metadata->>'terminal_status'='complete')
 OR NOT EXISTS(SELECT 1 FROM public.task_attempts x WHERE x.task_id=t.id AND x.epoch=t.lease_epoch AND x.outcome='completed' AND x.ended_at IS NOT NULL)
 OR EXISTS(SELECT 1 FROM public.task_history_projection x WHERE x.task_id=a.task_id AND x.kind IN ('STOP_INTENT','TERMINAL_OUTPUT_PREPARE'))
 OR (SELECT count(*) FROM public.task_history_projection x WHERE x.history_id=b.history_id)<>(j->>'seq')::pg_catalog.int8-1
 OR NOT EXISTS(SELECT 1 FROM public.task_history_projection x WHERE x.history_id=b.history_id AND x.seq=(j->>'seq')::pg_catalog.int8-1 AND encode(x.digest,'hex')=j->>'prev_digest')
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 -- Visible active subtransactions belong to this transaction too. Never read
 -- a committed row to bless a lost acknowledgement or backfill prior output.
 top_xid:=pg_current_xact_id()::pg_catalog.text::pg_catalog.numeric;
 FOR native_xid IN
 SELECT x.xmin FROM public.tasks x WHERE x.id=t.id
 UNION ALL SELECT x.xmin FROM public.messages x WHERE x.id=a.result_message_id
 UNION ALL SELECT x.xmin FROM public.task_attempts x WHERE x.task_id=t.id AND x.epoch=t.lease_epoch
 UNION ALL SELECT x.xmin FROM public.task_events x WHERE x.task_id=t.id AND x.seq=t.event_seq AND x.kind='finished'
 LOOP
  IF pg_xact_status((top_xid+mod(native_xid::pg_catalog.text::pg_catalog.numeric-mod(top_xid,4294967296)+4294967296,4294967296))::pg_catalog.text::pg_catalog.xid8)
  IS DISTINCT FROM 'in progress' THEN RAISE EXCEPTION 'task_history_refused'; END IF;
  native_rows:=native_rows+1;
 END LOOP;
 IF native_rows<>4 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 INSERT INTO public.task_history_projection(history_id,seq,record_id,digest,frame,kind,account_id,account_generation,task_id)
 VALUES(b.history_id,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,'TERMINAL_OUTPUT_PREPARE',a.account_id,a.account_generation,a.task_id);
 RETURN QUERY SELECT b.history_id,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,true;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
END $$;

CREATE FUNCTION public.task_history_terminal_page(p_history pg_catalog.uuid,after_seq pg_catalog.int8,expected_key_check pg_catalog.text)
RETURNS TABLE(history_id pg_catalog.uuid,seq pg_catalog.int8,record_id pg_catalog.uuid,digest pg_catalog.bytea,frame pg_catalog.bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b public.task_history_binding; r public.task_history_projection; j pg_catalog.jsonb; d pg_catalog.jsonb;
BEGIN
 PERFORM public.task_history_check_caller(false);
 SELECT * INTO b FROM public.task_history_binding;
 IF p_history IS NULL OR b.history_id IS DISTINCT FROM p_history OR b.version<>1 OR b.key_id<>1
 OR b.request_schema<>'chat-canonical-3715-v1' OR expected_key_check IS NULL
 OR expected_key_check !~ '^[0-9a-f]{64}$' OR b.key_check IS DISTINCT FROM expected_key_check
 OR after_seq IS NULL OR after_seq NOT BETWEEN 0 AND 16384
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 -- A partial restore must not hide an extra/missing acceptance index row.
 IF EXISTS(SELECT 1 FROM public.task_history_acceptance a WHERE NOT EXISTS(
 SELECT 1 FROM public.task_history_projection p WHERE (p.history_id,p.seq,p.record_id,p.digest,p.frame,p.kind)
 =(a.history_id,a.seq,a.record_id,a.digest,a.frame,'ACCEPT_PREPARE')))
 OR EXISTS(SELECT 1 FROM public.task_history_projection p WHERE p.kind='ACCEPT_PREPARE' AND NOT EXISTS(
 SELECT 1 FROM public.task_history_acceptance a WHERE (p.history_id,p.seq,p.record_id,p.digest,p.frame)
 =(a.history_id,a.seq,a.record_id,a.digest,a.frame))) THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 -- Every acceptance in the next <=32 combined facts is in this <=32 acceptance
 -- page too; retain its full existing semantic-column validation.
 PERFORM * FROM public.task_history_acceptance_page(p_history,after_seq,expected_key_check);
 FOR r IN SELECT * FROM public.task_history_projection p WHERE p.history_id=p_history AND p.seq>after_seq
 ORDER BY p.seq LIMIT 32 LOOP
  IF r.kind='ACCEPT_PREPARE' THEN j:=public.task_history_decode_acceptance(r.frame);
  ELSIF r.kind='TERMINAL_OUTPUT_PREPARE' THEN j:=public.task_history_decode_terminal(r.frame);
  ELSIF r.kind='ARTIFACT_PREPARE' THEN j:=public.task_history_decode_artifact(r.frame);
  ELSE j:=public.task_history_decode_effect(r.frame); END IF;
  d:=j->'data';
  IF ((j->>'history_id')::pg_catalog.uuid,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,
   substring(r.frame FROM octet_length(r.frame)-31 FOR 32),j->>'kind',(d->>'account_id')::pg_catalog.uuid,
   (d->>'account_generation')::pg_catalog.int8,(d->>'task_id')::pg_catalog.uuid)
   IS DISTINCT FROM (r.history_id,r.seq,r.record_id,r.digest,r.kind,r.account_id,r.account_generation,r.task_id)
  THEN RAISE EXCEPTION 'task_history_refused'; END IF;
  RETURN QUERY SELECT r.history_id,r.seq,r.record_id,r.digest,r.frame;
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.task_history_check_caller(writing pg_catalog.bool) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u pg_catalog.name:=SESSION_USER; a pg_catalog.bool; w pg_catalog.bool; caller_id pg_catalog.oid;
 write_grants pg_catalog.oid[]; read_grants pg_catalog.oid[];
BEGIN
 IF current_setting('role')<>'none' OR current_setting('transaction_isolation')<>'read committed'
 OR NOT has_schema_privilege(u,'public','USAGE') OR has_schema_privilege(u,'public','CREATE')
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
 (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls OR r.rolname IN
 ('pg_execute_server_program','pg_read_server_files','pg_write_server_files')) AND pg_has_role(u,r.oid,'MEMBER'))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_database x WHERE x.datname=current_database() AND pg_has_role(u,x.datdba,'MEMBER'))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace x WHERE x.nspname='public' AND pg_has_role(u,x.nspowner,'MEMBER'))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid IN
 ('public.task_history_binding'::pg_catalog.regclass,'public.task_history_acceptance'::pg_catalog.regclass,'public.task_history_projection'::pg_catalog.regclass)
 AND (pg_has_role(u,c.relowner,'MEMBER') OR has_table_privilege(u,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 OR has_any_column_privilege(u,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
 -- Both native A and W must use protected metadata/lifecycle APIs, not
 -- direct table/column authority. Mirror the existing native split boundary.
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid IN
 ('public.compute_scope_metadata'::pg_catalog.regclass,'public.compute_reservation_metadata'::pg_catalog.regclass,
 'public.attempt_lifecycle_control'::pg_catalog.regclass,'public.attempt_lifecycle_commands'::pg_catalog.regclass)
 AND (pg_has_role(u,c.relowner,'MEMBER')
 OR has_table_privilege(u,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 OR has_any_column_privilege(u,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc f JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace
 WHERE n.nspname='public' AND (f.proname LIKE 'task_inference_%' OR f.proname LIKE 'attempt_lifecycle_%'
 OR f.proname LIKE 'compute_metadata_%')
 AND (pg_has_role(u,f.proowner,'MEMBER') OR has_function_privilege(u,f.oid,'EXECUTE WITH GRANT OPTION')))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc f JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace
 WHERE n.nspname='public' AND f.proname LIKE 'task_history_%' AND
 (pg_has_role(u,f.proowner,'MEMBER') OR has_function_privilege(u,f.oid,'EXECUTE WITH GRANT OPTION')
 OR (f.proname NOT IN ('task_history_acceptance_project','task_history_acceptance_page','task_history_send_project','task_history_stop_project','task_history_page','task_history_output_page','task_history_artifact_project','task_history_terminal_project','task_history_terminal_page')
 AND has_function_privilege(u,f.oid,'EXECUTE'))))
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 -- EXECUTE inherited from an intended service login is not that login.
 -- Derive the direct grant boundary from the owner-approved function ACLs;
 -- role names/credentials are never initialized or learned as binding data.
 SELECT oid INTO caller_id FROM pg_catalog.pg_roles WHERE rolname=u;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO write_grants
 FROM pg_catalog.pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_acceptance_project(pg_catalog.bytea)'::pg_catalog.regprocedure AND x.grantee<>f.proowner;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO read_grants
 FROM pg_catalog.pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_acceptance_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure AND x.grantee<>f.proowner;
 IF caller_id IS NULL OR cardinality(write_grants) IS DISTINCT FROM 1
 OR cardinality(read_grants) IS DISTINCT FROM 2
 OR NOT coalesce(write_grants[1]=ANY(read_grants),false)
 OR NOT (SELECT count(*)=2 AND bool_and(rolcanlogin) FROM pg_catalog.pg_roles WHERE oid=ANY(read_grants))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c
 CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
 WHERE c.oid IN ('public.task_history_binding'::pg_catalog.regclass,'public.task_history_acceptance'::pg_catalog.regclass,'public.task_history_projection'::pg_catalog.regclass)
 AND x.grantee<>c.relowner)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
 WHERE a.attrelid IN ('public.task_history_binding'::pg_catalog.regclass,'public.task_history_acceptance'::pg_catalog.regclass,'public.task_history_projection'::pg_catalog.regclass)
 AND x.grantee<>(SELECT relowner FROM pg_catalog.pg_class WHERE oid=a.attrelid))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc f JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE n.nspname='public' AND f.proname LIKE 'task_history_%' AND x.grantee<>f.proowner
 AND (x.is_grantable OR x.privilege_type<>'EXECUTE' OR f.oid NOT IN
 ('public.task_history_acceptance_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_acceptance_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure,
 'public.task_history_send_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_stop_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure,
 'public.task_history_output_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure,
 'public.task_history_artifact_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_terminal_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_terminal_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure))) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid IN ('public.task_history_send_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_stop_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure)
 AND x.grantee<>f.proowner AND (x.is_grantable OR x.privilege_type<>'EXECUTE'
 OR NOT x.grantee=ANY(read_grants) OR (f.proname<>'task_history_page' AND x.grantee=write_grants[1])))
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 -- A's four-column coverage INSERT cannot be substituted with W's dispatch capability.
 a:=has_column_privilege(u,'public.task_inference_recovery','task_id','INSERT')
 AND has_column_privilege(u,'public.task_inference_recovery','account_id','INSERT')
 AND has_column_privilege(u,'public.task_inference_recovery','account_generation','INSERT')
 AND has_column_privilege(u,'public.task_inference_recovery','protocol_version','INSERT')
 AND NOT has_table_privilege(u,'public.task_inference_recovery','SELECT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(u,'public.task_inference_recovery','SELECT,UPDATE,REFERENCES')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute x WHERE x.attrelid='public.task_inference_recovery'::pg_catalog.regclass
 AND x.attnum>0 AND NOT x.attisdropped AND
 (has_column_privilege(u,x.attrelid,x.attnum,'INSERT WITH GRANT OPTION') OR
 (x.attname NOT IN ('task_id','account_id','account_generation','protocol_version')
 AND has_column_privilege(u,x.attrelid,x.attnum,'INSERT'))))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc f JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace WHERE n.nspname='public'
 AND (f.proname LIKE 'task_inference_%' OR f.proname LIKE 'attempt_lifecycle_%' OR f.proname LIKE 'compute_metadata_%')
 AND (pg_has_role(u,f.proowner,'MEMBER') OR has_function_privilege(u,f.oid,'EXECUTE')));
 w:=NOT has_any_column_privilege(u,'public.task_inference_recovery','INSERT,UPDATE,SELECT,REFERENCES')
 AND NOT has_table_privilege(u,'public.task_inference_recovery','DELETE,TRUNCATE,TRIGGER')
 AND has_function_privilege(u,'public.task_inference_dispatch_begin(pg_catalog.jsonb,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.uuid)','EXECUTE')
 AND has_function_privilege(u,'public.attempt_lifecycle_assert_open(pg_catalog.jsonb)','EXECUTE');
 IF (writing AND (caller_id<>write_grants[1] OR NOT a))
 OR (NOT writing AND (NOT caller_id=ANY(read_grants)
 OR NOT ((caller_id=write_grants[1] AND a) OR (caller_id<>write_grants[1] AND w)))) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid IN ('public.task_history_output_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure,
 'public.task_history_artifact_project(pg_catalog.bytea)'::pg_catalog.regprocedure) AND x.grantee<>f.proowner)
 AND ((SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_output_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure AND x.grantee<>f.proowner) IS DISTINCT FROM read_grants
 OR (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_artifact_project(pg_catalog.bytea)'::pg_catalog.regprocedure AND x.grantee<>f.proowner)
 IS DISTINCT FROM (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.resource_output_catalog_snapshot(pg_catalog.jsonb,pg_catalog.uuid)'::pg_catalog.regprocedure AND x.grantee<>f.proowner)
 OR (SELECT count(*) FROM pg_catalog.pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_artifact_project(pg_catalog.bytea)'::pg_catalog.regprocedure AND x.grantee<>f.proowner)<>1
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_artifact_project(pg_catalog.bytea)'::pg_catalog.regprocedure AND x.grantee=ANY(read_grants)))
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;

 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid IN ('public.task_history_terminal_project(bytea)'::regprocedure,'public.task_history_terminal_page(uuid,bigint,text)'::regprocedure) AND x.grantee<>f.proowner)
 AND ((SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_terminal_project(bytea)'::regprocedure AND x.grantee<>f.proowner)
 IS DISTINCT FROM (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_send_project(bytea)'::regprocedure AND x.grantee<>f.proowner)
 OR (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_terminal_page(uuid,bigint,text)'::regprocedure AND x.grantee<>f.proowner) IS DISTINCT FROM read_grants)
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
END $$;

-- Refuse unsafe owner defaults; no service grant, role or binding is created.
DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
 WHERE n.nspname='public' AND p.proname IN ('task_history_decode_terminal','task_history_terminal_project','task_history_terminal_page')
 AND x.grantee<>p.proowner AND NOT(x.grantee=0 AND x.privilege_type='EXECUTE' AND NOT x.is_grantable))
 THEN RAISE EXCEPTION 'task_history_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN ('task_history_decode_terminal','task_history_terminal_project','task_history_terminal_page') LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
