-- Approved source/fixture payload, numbered after merged C1 migrations 052--055.
-- Caller owns the transaction. No runtime binding, grants or activation.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE TABLE public.task_history_binding (
 singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
 history_id uuid NOT NULL UNIQUE CHECK (history_id<>'00000000-0000-0000-0000-000000000000'),
 version smallint NOT NULL CHECK (version=1),
 key_id smallint NOT NULL CHECK (key_id=1),
 key_check text NOT NULL CHECK (key_check ~ '^[0-9a-f]{64}$'),
 request_schema text NOT NULL CHECK (request_schema='chat-canonical-3715-v1')
);
CREATE TABLE public.task_history_acceptance (
 history_id uuid NOT NULL,
 seq bigint NOT NULL CHECK (seq BETWEEN 1 AND 16384),
 record_id uuid NOT NULL CHECK (record_id<>'00000000-0000-0000-0000-000000000000'),
 digest bytea NOT NULL CHECK (octet_length(digest)=32),
 frame bytea NOT NULL CHECK (octet_length(frame) BETWEEN 37 AND 8228),
 account_id uuid NOT NULL CHECK (account_id<>'00000000-0000-0000-0000-000000000000'),
 account_generation bigint NOT NULL CHECK (account_generation>=0),
 task_id uuid NOT NULL UNIQUE CHECK (task_id<>'00000000-0000-0000-0000-000000000000'),
 conversation_id uuid NOT NULL CHECK (conversation_id<>'00000000-0000-0000-0000-000000000000'),
 user_message_id uuid NOT NULL CHECK (user_message_id<>'00000000-0000-0000-0000-000000000000'),
 result_message_id uuid NOT NULL CHECK (result_message_id<>'00000000-0000-0000-0000-000000000000'),
 commitment_version smallint NOT NULL CHECK (commitment_version=1),
 key_id smallint NOT NULL CHECK (key_id=1),
 key_commitment text NOT NULL CHECK (key_commitment ~ '^[0-9a-f]{64}$'),
 request_commitment text NOT NULL CHECK (request_commitment ~ '^[0-9a-f]{64}$'),
 request_schema text NOT NULL CHECK (request_schema='chat-canonical-3715-v1'),
 PRIMARY KEY(history_id,seq), UNIQUE(history_id,record_id),
 UNIQUE(account_id,key_id,key_commitment),
 FOREIGN KEY(history_id) REFERENCES public.task_history_binding(history_id) ON DELETE RESTRICT
);
COMMENT ON TABLE public.task_history_acceptance IS
 'Negative H1 acceptance evidence, never execution authority. No native account/task/message cascade or pruning.';

CREATE FUNCTION public.task_history_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END $$;
CREATE TRIGGER task_history_binding_immutable BEFORE UPDATE OR DELETE ON public.task_history_binding
 FOR EACH ROW EXECUTE FUNCTION public.task_history_immutable();
CREATE TRIGGER task_history_binding_no_truncate BEFORE TRUNCATE ON public.task_history_binding
 FOR EACH STATEMENT EXECUTE FUNCTION public.task_history_immutable();
CREATE TRIGGER task_history_acceptance_immutable BEFORE UPDATE OR DELETE ON public.task_history_acceptance
 FOR EACH ROW EXECUTE FUNCTION public.task_history_immutable();
CREATE TRIGGER task_history_acceptance_no_truncate BEFORE TRUNCATE ON public.task_history_acceptance
 FOR EACH STATEMENT EXECUTE FUNCTION public.task_history_immutable();

CREATE FUNCTION public.task_history_decode_acceptance(frame bytea) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE n pg_catalog.int8; j pg_catalog.jsonb; d pg_catalog.jsonb; payload pg_catalog.text; data_wire pg_catalog.text; envelope_wire pg_catalog.text;
 k pg_catalog.text;
BEGIN
 IF frame IS NULL OR octet_length(frame) NOT BETWEEN 37 AND 8228 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 n:=get_byte(frame,0)::pg_catalog.int8*16777216+get_byte(frame,1)::pg_catalog.int8*65536
    +get_byte(frame,2)::pg_catalog.int8*256+get_byte(frame,3);
 IF n NOT BETWEEN 1 AND 8192 OR octet_length(frame)<>n+36
 OR substring(frame FROM (n+5)::pg_catalog.int4 FOR 32) IS DISTINCT FROM
   sha256(convert_to('daemon.restore-history.frame.v1'||chr(10),'UTF8')
          ||substring(frame FROM 1 FOR (n+4)::pg_catalog.int4)) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 payload:=convert_from(substring(frame FROM 5 FOR n::pg_catalog.int4),'UTF8'); j:=payload::pg_catalog.jsonb;
 IF jsonb_typeof(j) IS DISTINCT FROM 'object' OR
 (SELECT count(*) FROM jsonb_object_keys(j))<>8 OR
 NOT j ?& ARRAY['v','history_id','seq','prev_digest','record_id','kind','actor','data'] OR
 j->'v' IS DISTINCT FROM '1'::pg_catalog.jsonb OR j->>'v'<>'1' OR j->>'kind' IS DISTINCT FROM 'ACCEPT_PREPARE'
 OR j->>'actor' IS DISTINCT FROM 'backend' OR jsonb_typeof(j->'data') IS DISTINCT FROM 'object'
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 d:=j->'data';
 IF (SELECT count(*) FROM jsonb_object_keys(d))<>11 OR NOT d ?& ARRAY[
 'account_id','account_generation','task_id','conversation_id','user_message_id','result_message_id',
 'commitment_version','key_id','key_commitment','request_commitment','request_schema']
 OR d->'commitment_version' IS DISTINCT FROM '1'::pg_catalog.jsonb OR d->>'commitment_version'<>'1'
 OR d->'key_id' IS DISTINCT FROM '1'::pg_catalog.jsonb OR d->>'key_id'<>'1'
 OR d->>'request_schema' IS DISTINCT FROM 'chat-canonical-3715-v1'
 OR jsonb_typeof(j->'seq') IS DISTINCT FROM 'number' OR j->>'seq' !~ '^[1-9][0-9]*$'
 OR (j->>'seq')::pg_catalog.numeric NOT BETWEEN 1 AND 16384
 OR jsonb_typeof(d->'account_generation') IS DISTINCT FROM 'number'
 OR d->>'account_generation' !~ '^(0|[1-9][0-9]*)$'
 OR (d->>'account_generation')::pg_catalog.numeric NOT BETWEEN 0 AND 9223372036854775807
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 FOREACH k IN ARRAY ARRAY['history_id','record_id'] LOOP
  IF jsonb_typeof(j->k) IS DISTINCT FROM 'string'
  OR ((j->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM j->>k
  OR (j->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['account_id','task_id','conversation_id','user_message_id','result_message_id'] LOOP
  IF jsonb_typeof(d->k) IS DISTINCT FROM 'string'
  OR ((d->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM d->>k
  OR (d->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 END LOOP;
 IF jsonb_typeof(j->'prev_digest') IS DISTINCT FROM 'string' OR j->>'prev_digest' !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(d->'key_commitment') IS DISTINCT FROM 'string' OR d->>'key_commitment' !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(d->'request_commitment') IS DISTINCT FROM 'string' OR d->>'request_commitment' !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 -- Every allowed string is closed ASCII UUID/hash/literal; every value is scalar.
 -- PostgreSQL JSONB order is not canonical H1 order. Rebuild sorted keys exactly.
 SELECT '{'||string_agg(to_jsonb(key)::pg_catalog.text||':'||value::pg_catalog.text,',' ORDER BY key COLLATE pg_catalog."C")||'}'
 INTO data_wire FROM jsonb_each(d);
 SELECT '{'||string_agg(to_jsonb(key)::pg_catalog.text||':'||CASE WHEN key='data' THEN data_wire ELSE value::pg_catalog.text END,
 ',' ORDER BY key COLLATE pg_catalog."C")||'}' INTO envelope_wire FROM jsonb_each(j);
 IF convert_to(envelope_wire,'UTF8') IS DISTINCT FROM convert_to(payload,'UTF8') THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 RETURN j;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
END $$;

CREATE FUNCTION public.task_history_check_caller(writing boolean) RETURNS void
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
 ('public.task_history_binding'::pg_catalog.regclass,'public.task_history_acceptance'::pg_catalog.regclass)
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
 OR (f.proname NOT IN ('task_history_acceptance_project','task_history_acceptance_page')
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
 WHERE c.oid IN ('public.task_history_binding'::pg_catalog.regclass,'public.task_history_acceptance'::pg_catalog.regclass)
 AND x.grantee<>c.relowner)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
 WHERE a.attrelid IN ('public.task_history_binding'::pg_catalog.regclass,'public.task_history_acceptance'::pg_catalog.regclass)
 AND x.grantee<>(SELECT relowner FROM pg_catalog.pg_class WHERE oid=a.attrelid))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc f JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE n.nspname='public' AND f.proname LIKE 'task_history_%' AND x.grantee<>f.proowner
 AND (x.is_grantable OR x.privilege_type<>'EXECUTE' OR f.oid NOT IN
 ('public.task_history_acceptance_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_acceptance_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure))) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
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
END $$;

CREATE FUNCTION public.task_history_acceptance_project(p_frame bytea)
RETURNS TABLE(history_id uuid,seq bigint,record_id uuid,digest bytea,frame bytea,created boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j pg_catalog.jsonb; d pg_catalog.jsonb; b public.task_history_binding; old public.task_history_acceptance;
 t public.tasks; c public.task_inference_recovery; account public.entitlement_accounts;
 target_history pg_catalog.uuid; target_seq pg_catalog.int8; target_record pg_catalog.uuid; target_digest pg_catalog.bytea;
BEGIN
 -- Preserve a caller's stricter native lock bound; zero means unlimited.
 PERFORM set_config('lock_timeout',least(coalesce(nullif((SELECT setting::pg_catalog.int4
 FROM pg_catalog.pg_settings WHERE name='lock_timeout'),0),250),250)::pg_catalog.text||'ms',true);
 PERFORM public.task_history_check_caller(true);
 j:=public.task_history_decode_acceptance(p_frame); d:=j->'data';
 SELECT * INTO b FROM public.task_history_binding;
 target_history:=(j->>'history_id')::pg_catalog.uuid; target_seq:=(j->>'seq')::pg_catalog.int8;
 target_record:=(j->>'record_id')::pg_catalog.uuid; target_digest:=substring(p_frame FROM octet_length(p_frame)-31 FOR 32);
 IF b.history_id IS DISTINCT FROM target_history OR b.version<>1 OR b.key_id<>1
 OR b.request_schema IS DISTINCT FROM d->>'request_schema' THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 -- Native account SHARE first; fresh subordinate snapshot after any wait.
 SELECT * INTO account FROM public.entitlement_accounts WHERE user_id=(d->>'account_id')::pg_catalog.uuid FOR SHARE;
 IF account.user_id IS NULL OR account.status<>'active'
 OR account.content_generation<>(d->>'account_generation')::pg_catalog.int8 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 IF EXISTS(SELECT 1 FROM public.account_deletions WHERE account_id=account.user_id AND closed_at IS NULL)
 OR EXISTS(SELECT 1 FROM public.account_suspension_causes WHERE account_id=account.user_id) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 -- Keep semantic checks live even for historical duplicates; no backfill/recovery API.
 SELECT * INTO t FROM public.tasks WHERE id=(d->>'task_id')::pg_catalog.uuid FOR SHARE;
 SELECT * INTO c FROM public.task_inference_recovery WHERE task_id=t.id FOR SHARE;
 IF t.id IS NULL OR t.user_id<>account.user_id
 OR t.conversation_id IS DISTINCT FROM (d->>'conversation_id')::pg_catalog.uuid
 OR t.user_message_id IS DISTINCT FROM (d->>'user_message_id')::pg_catalog.uuid
 OR t.result_message_id IS DISTINCT FROM (d->>'result_message_id')::pg_catalog.uuid
 OR c.task_id IS NULL OR c.account_id<>account.user_id OR c.account_generation<>account.content_generation
 OR c.protocol_version<>1
 OR NOT EXISTS(SELECT 1 FROM public.conversations x WHERE x.id=t.conversation_id AND x.user_id=account.user_id)
 OR NOT EXISTS(SELECT 1 FROM public.messages x WHERE x.id=t.user_message_id AND x.user_id=account.user_id
 AND x.conversation_id=t.conversation_id AND x.role='user')
 OR NOT EXISTS(SELECT 1 FROM public.messages x WHERE x.id=t.result_message_id AND x.user_id=account.user_id
 AND x.conversation_id=t.conversation_id AND x.role='assistant') THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 SELECT * INTO old FROM public.task_history_acceptance x WHERE x.history_id=target_history AND x.record_id=target_record;
 IF old.history_id IS NOT NULL THEN
  IF old.frame IS DISTINCT FROM p_frame THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
  RETURN QUERY SELECT old.history_id,old.seq,old.record_id,old.digest,old.frame,false;
  RETURN;
 END IF;
 BEGIN
 INSERT INTO public.task_history_acceptance(history_id,seq,record_id,digest,frame,
 account_id,account_generation,task_id,conversation_id,user_message_id,result_message_id,
 commitment_version,key_id,key_commitment,request_commitment,request_schema)
 VALUES(target_history,target_seq,target_record,target_digest,p_frame,
 (d->>'account_id')::pg_catalog.uuid,(d->>'account_generation')::pg_catalog.int8,(d->>'task_id')::pg_catalog.uuid,
 (d->>'conversation_id')::pg_catalog.uuid,(d->>'user_message_id')::pg_catalog.uuid,(d->>'result_message_id')::pg_catalog.uuid,
 1,1,d->>'key_commitment',d->>'request_commitment',d->>'request_schema');
 EXCEPTION WHEN unique_violation THEN
  -- Concurrent equal operation is historical only. No overwrite or fresh permit.
  SELECT * INTO old FROM public.task_history_acceptance x WHERE x.history_id=target_history AND x.record_id=target_record;
  IF old.history_id IS NULL OR old.frame IS DISTINCT FROM p_frame THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
  RETURN QUERY SELECT old.history_id,old.seq,old.record_id,old.digest,old.frame,false;
  RETURN;
 END;
 RETURN QUERY SELECT target_history,target_seq,target_record,target_digest,p_frame,true;
END $$;

CREATE FUNCTION public.task_history_acceptance_page(p_history uuid,after_seq bigint,expected_key_check text)
RETURNS TABLE(history_id uuid,seq bigint,record_id uuid,digest bytea,frame bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b public.task_history_binding; r public.task_history_acceptance; j pg_catalog.jsonb; d pg_catalog.jsonb;
BEGIN
 -- Preserve a caller's stricter native lock bound; zero means unlimited.
 PERFORM set_config('lock_timeout',least(coalesce(nullif((SELECT setting::pg_catalog.int4
 FROM pg_catalog.pg_settings WHERE name='lock_timeout'),0),250),250)::pg_catalog.text||'ms',true);
 PERFORM public.task_history_check_caller(false);
 SELECT * INTO b FROM public.task_history_binding;
 IF p_history IS NULL OR b.history_id IS DISTINCT FROM p_history OR b.version<>1 OR b.key_id<>1
 OR b.request_schema<>'chat-canonical-3715-v1' OR expected_key_check IS NULL
 OR expected_key_check !~ '^[0-9a-f]{64}$' OR b.key_check IS DISTINCT FROM expected_key_check
 OR after_seq IS NULL OR after_seq NOT BETWEEN 0 AND 16384
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
 FOR r IN SELECT * FROM public.task_history_acceptance x WHERE x.history_id=p_history AND x.seq>after_seq
 ORDER BY x.seq LIMIT 32 LOOP
  j:=public.task_history_decode_acceptance(r.frame); d:=j->'data';
  IF ((j->>'history_id')::pg_catalog.uuid,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,
    substring(r.frame FROM octet_length(r.frame)-31 FOR 32),(d->>'account_id')::pg_catalog.uuid,
    (d->>'account_generation')::pg_catalog.int8,(d->>'task_id')::pg_catalog.uuid,(d->>'conversation_id')::pg_catalog.uuid,
    (d->>'user_message_id')::pg_catalog.uuid,(d->>'result_message_id')::pg_catalog.uuid,
    (d->>'commitment_version')::pg_catalog.int2,(d->>'key_id')::pg_catalog.int2,d->>'key_commitment',
    d->>'request_commitment',d->>'request_schema') IS DISTINCT FROM
   (r.history_id,r.seq,r.record_id,r.digest,r.account_id,r.account_generation,r.task_id,
    r.conversation_id,r.user_message_id,r.result_message_id,r.commitment_version,r.key_id,
    r.key_commitment,r.request_commitment,r.request_schema) THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused'; END IF;
  RETURN QUERY SELECT r.history_id,r.seq,r.record_id,r.digest,r.frame;
 END LOOP;
END $$;

-- Publication remains owner-only. No binding data, roles, memberships or app grants.
DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 WHERE c.oid IN ('public.task_history_binding'::pg_catalog.regclass,'public.task_history_acceptance'::pg_catalog.regclass)
 AND a.grantee<>c.relowner)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname LIKE 'task_history_%' AND a.grantee<>p.proowner
 AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)) THEN
  RAISE EXCEPTION 'task_history_unsafe_default_privileges'; END IF;
 REVOKE ALL ON public.task_history_binding,public.task_history_acceptance FROM PUBLIC;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname LIKE 'task_history_%' LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
