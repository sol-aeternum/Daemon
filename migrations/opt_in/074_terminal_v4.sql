-- SOURCE ONLY, explicitly selected isolated terminal-v4 qualification.
-- Caller owns the migration transaction. The default migration glob excludes
-- this directory. No service grants, logins, bindings, backfill or activation.
SET LOCAL lock_timeout='1s';
SET LOCAL statement_timeout='5s';
DO $$ BEGIN
 IF CURRENT_USER<>(SELECT pg_get_userbyid(relowner) FROM pg_catalog.pg_class
 WHERE oid='public.task_history_binding'::regclass) THEN
 RAISE EXCEPTION 'task_history_owner_required'; END IF;
END $$;

ALTER TABLE public.task_history_projection DROP CONSTRAINT task_history_projection_kind_check;
ALTER TABLE public.task_history_projection ADD CONSTRAINT task_history_projection_kind_check
 CHECK(kind IN ('ACCEPT_PREPARE','SEND_POSSIBLE','STOP_INTENT','ARTIFACT_PREPARE','TERMINAL_OUTPUT_PREPARE','NONSUCCESS_OUTPUT_PREPARE'));

-- Existing private H1 staging helper: both terminal kinds fence fresh Send
-- and Stop, without changing acceptance's original duplicate behavior.
CREATE OR REPLACE FUNCTION public.task_history_project_frame(p_frame pg_catalog.bytea,fresh pg_catalog.bool) RETURNS pg_catalog.bool
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j pg_catalog.jsonb; d pg_catalog.jsonb; b public.task_history_binding; old public.task_history_projection; inserted pg_catalog.bool:=false;
BEGIN
 BEGIN j:=public.task_history_decode_acceptance(p_frame);
 EXCEPTION WHEN OTHERS THEN j:=public.task_history_decode_effect(p_frame); END;
 d:=j->'data'; SELECT * INTO b FROM public.task_history_binding;
 IF fresh IS NULL OR b.history_id IS DISTINCT FROM (j->>'history_id')::pg_catalog.uuid
 OR b.version<>1 OR b.key_id<>1 OR b.request_schema<>'chat-canonical-3715-v1'
 OR NOT EXISTS(SELECT 1 FROM public.task_history_acceptance a WHERE a.task_id=(d->>'task_id')::pg_catalog.uuid
 AND a.history_id=b.history_id AND a.account_id=(d->>'account_id')::pg_catalog.uuid
 AND a.account_generation=(d->>'account_generation')::pg_catalog.int8
 AND ((j->>'kind'='ACCEPT_PREPARE' AND a.frame=p_frame AND a.seq=(j->>'seq')::pg_catalog.int8)
 OR (j->>'kind'<>'ACCEPT_PREPARE' AND a.seq<(j->>'seq')::pg_catalog.int8)))
 OR (j->>'kind' IN ('SEND_POSSIBLE','STOP_INTENT') AND EXISTS(
 SELECT 1 FROM public.task_history_projection x WHERE x.task_id=(d->>'task_id')::pg_catalog.uuid
 AND x.kind IN ('TERMINAL_OUTPUT_PREPARE','NONSUCCESS_OUTPUT_PREPARE')))
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 IF fresh THEN
  INSERT INTO public.task_history_projection(history_id,seq,record_id,digest,frame,kind,account_id,account_generation,task_id)
  VALUES(b.history_id,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,
   substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,j->>'kind',
   (d->>'account_id')::pg_catalog.uuid,(d->>'account_generation')::pg_catalog.int8,(d->>'task_id')::pg_catalog.uuid)
  ON CONFLICT DO NOTHING;
  inserted:=FOUND;
 END IF;
 SELECT * INTO old FROM public.task_history_projection p WHERE p.history_id=b.history_id AND p.record_id=(j->>'record_id')::pg_catalog.uuid;
 IF old.history_id IS NULL OR (old.seq,old.digest,old.frame,old.kind,old.account_id,old.account_generation,old.task_id)
 IS DISTINCT FROM ((j->>'seq')::pg_catalog.int8,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,j->>'kind',
 (d->>'account_id')::pg_catalog.uuid,(d->>'account_generation')::pg_catalog.int8,(d->>'task_id')::pg_catalog.uuid)
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 RETURN inserted;
END $$;

-- Preserve 064's existing P-only operation and native predicate. Both kinds
-- of terminal now exclude a later protected catalog output.
CREATE OR REPLACE FUNCTION public.task_history_artifact_project(p_frame pg_catalog.bytea)
RETURNS TABLE(history_id pg_catalog.uuid,seq pg_catalog.int8,record_id pg_catalog.uuid,digest pg_catalog.bytea,frame pg_catalog.bytea,created pg_catalog.bool)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j pg_catalog.jsonb; d pg_catalog.jsonb; b public.task_history_binding; a public.task_history_acceptance;
 identity pg_catalog.jsonb; observed pg_catalog.jsonb; who pg_catalog.oid; readers pg_catalog.oid[];
 writers pg_catalog.oid[]; native_writers pg_catalog.oid[]; top_xid pg_catalog.numeric;
 native_xid pg_catalog.xid; native_rows pg_catalog.int4:=0;
BEGIN
 PERFORM public.resource_output_catalog_principal();
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO writers FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_artifact_project(pg_catalog.bytea)'::pg_catalog.regprocedure AND x.grantee<>f.proowner;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO native_writers FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.resource_output_catalog_snapshot(pg_catalog.jsonb,pg_catalog.uuid)'::pg_catalog.regprocedure AND x.grantee<>f.proowner;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO readers FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_output_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure AND x.grantee<>f.proowner;
 IF cardinality(writers) IS DISTINCT FROM 1 OR writers IS DISTINCT FROM native_writers
 OR who IS DISTINCT FROM writers[1] OR cardinality(readers) IS DISTINCT FROM 2 OR who=ANY(readers)
 OR readers IS DISTINCT FROM (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_acceptance_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure AND x.grantee<>f.proowner)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid IN ('public.task_history_artifact_project(pg_catalog.bytea)'::pg_catalog.regprocedure,
 'public.task_history_output_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)'::pg_catalog.regprocedure)
 AND x.grantee<>f.proowner AND (x.is_grantable OR x.privilege_type<>'EXECUTE'))
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 j:=public.task_history_decode_artifact(p_frame); d:=j->'data'; SELECT * INTO b FROM public.task_history_binding;
 SELECT * INTO a FROM public.task_history_acceptance x WHERE x.history_id=b.history_id AND x.record_id=(d->>'acceptance_record_id')::pg_catalog.uuid;
 IF b.history_id IS DISTINCT FROM (j->>'history_id')::pg_catalog.uuid OR b.version<>1 OR b.key_id<>1
 OR a.task_id IS NULL OR (a.account_id,a.account_generation,a.task_id) IS DISTINCT FROM
 ((d->>'account_id')::pg_catalog.uuid,(d->>'account_generation')::pg_catalog.int8,(d->>'task_id')::pg_catalog.uuid)
 OR a.seq>=(j->>'seq')::pg_catalog.int8
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 identity:=jsonb_build_object('account_id',d->'account_id','generation',d->'account_generation',
 'task_id',d->'task_id','epoch',d->'epoch','lifecycle_id',d->'lifecycle_id',
 'financial_scope_id',d->'financial_scope_id','metadata_scope_id',d->'metadata_scope_id',
 'producer_instance_id',d->'producer_instance_id');
 observed:=public.resource_output_catalog_admit(identity,(d->>'intent_id')::pg_catalog.uuid);
 IF observed->>'source_kind'<>'output' OR observed->'publication'='null'::pg_catalog.jsonb
 OR (observed->'job'->>'job_id')::pg_catalog.uuid<>(d->>'job_id')::pg_catalog.uuid
 OR (observed->'job'->>'operation_id')::pg_catalog.uuid<>(d->>'source_operation_id')::pg_catalog.uuid
 OR (observed->'intent'->>'resource_id')::pg_catalog.uuid<>(d->>'resource_id')::pg_catalog.uuid
 OR (observed->'intent'->>'version_id')::pg_catalog.uuid<>(d->>'version_id')::pg_catalog.uuid
 OR (observed->'intent'->>'output_slot')::pg_catalog.int4<>(d->>'output_slot')::pg_catalog.int4
 OR (observed->'intent'->>'attempt_id')::pg_catalog.uuid<>(d->>'original_attempt_id')::pg_catalog.uuid
 OR (observed->'task'->>'conversation_id')::pg_catalog.uuid<>a.conversation_id
 OR (observed->'task'->>'result_message_id')::pg_catalog.uuid<>a.result_message_id
 OR EXISTS(SELECT 1 FROM public.task_history_projection x WHERE x.task_id=a.task_id
 AND x.kind IN ('STOP_INTENT','TERMINAL_OUTPUT_PREPARE','NONSUCCESS_OUTPUT_PREPARE'))
 OR EXISTS(SELECT 1 FROM public.task_history_projection x WHERE x.history_id=b.history_id AND x.kind='ARTIFACT_PREPARE'
 AND public.task_history_decode_artifact(x.frame)->'data'->>'intent_id'=d->>'intent_id')
 OR (SELECT count(*) FROM public.task_history_projection x WHERE x.history_id=b.history_id)<>(j->>'seq')::pg_catalog.int8-1
 OR NOT EXISTS(SELECT 1 FROM public.task_history_projection x WHERE x.history_id=b.history_id AND x.seq=(j->>'seq')::pg_catalog.int8-1
 AND encode(x.digest,'hex')=j->>'prev_digest')
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 top_xid:=pg_current_xact_id()::pg_catalog.text::pg_catalog.numeric;
 FOR native_xid IN
 SELECT x.xmin FROM public.resource_publications x WHERE x.account_id=a.account_id AND x.intent_id=(d->>'intent_id')::pg_catalog.uuid
 UNION ALL SELECT x.xmin FROM public.resource_versions x WHERE x.account_id=a.account_id AND x.version_id=(d->>'version_id')::pg_catalog.uuid
 UNION ALL SELECT x.xmin FROM public.resource_references x WHERE x.account_id=a.account_id AND x.version_id=(d->>'version_id')::pg_catalog.uuid AND x.role='output'
 LOOP
  IF pg_xact_status((top_xid+mod(native_xid::pg_catalog.text::pg_catalog.numeric-mod(top_xid,4294967296)+4294967296,4294967296))::pg_catalog.text::pg_catalog.xid8)
  IS DISTINCT FROM 'in progress' THEN RAISE EXCEPTION 'task_history_refused'; END IF;
  native_rows:=native_rows+1;
 END LOOP;
 IF native_rows<>3 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 INSERT INTO public.task_history_projection(history_id,seq,record_id,digest,frame,kind,account_id,account_generation,task_id)
 VALUES(b.history_id,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,
 substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,'ARTIFACT_PREPARE',a.account_id,a.account_generation,a.task_id);
 PERFORM public.attempt_lifecycle_assert_open(identity);
 RETURN QUERY SELECT b.history_id,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,
 substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,true;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
END $$;

CREATE FUNCTION public.task_history_decode_nonsuccess(frame pg_catalog.bytea) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE n pg_catalog.int8; j pg_catalog.jsonb; d pg_catalog.jsonb; payload pg_catalog.text;
 data_wire pg_catalog.text; envelope_wire pg_catalog.text; k pg_catalog.text;
 keys pg_catalog.text[]:=ARRAY['account_id','account_generation','task_id','result_message_id',
 'acceptance_record_id','epoch','terminal_schema','status','reason'];
BEGIN
 IF frame IS NULL OR octet_length(frame) NOT BETWEEN 37 AND 8228 THEN RAISE EXCEPTION 'invalid'; END IF;
 n:=get_byte(frame,0)::pg_catalog.int8*16777216+get_byte(frame,1)::pg_catalog.int8*65536+get_byte(frame,2)::pg_catalog.int8*256+get_byte(frame,3);
 IF n NOT BETWEEN 1 AND 8192 OR octet_length(frame)<>n+36
 OR substring(frame FROM (n+5)::pg_catalog.int4 FOR 32) IS DISTINCT FROM
 sha256(convert_to('daemon.restore-history.frame.v4'||chr(10),'UTF8')||substring(frame FROM 1 FOR (n+4)::pg_catalog.int4))
 THEN RAISE EXCEPTION 'invalid'; END IF;
 payload:=convert_from(substring(frame FROM 5 FOR n::pg_catalog.int4),'UTF8'); j:=payload::pg_catalog.jsonb; d:=j->'data';
 IF jsonb_typeof(j) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(j))<>8
 OR NOT j ?& ARRAY['v','history_id','seq','prev_digest','record_id','kind','actor','data']
 OR j->'v' IS DISTINCT FROM '4'::pg_catalog.jsonb OR j->>'v'<>'4'
 OR j->'kind' IS DISTINCT FROM '"NONSUCCESS_OUTPUT_PREPARE"'::pg_catalog.jsonb
 OR j->'actor' IS DISTINCT FROM '"worker"'::pg_catalog.jsonb
 OR jsonb_typeof(d) IS DISTINCT FROM 'object'
 OR (SELECT count(*) FROM jsonb_object_keys(d))<>cardinality(keys) OR NOT d ?& keys
 OR jsonb_typeof(j->'seq') IS DISTINCT FROM 'number' OR j->>'seq' !~ '^[1-9][0-9]*$'
 OR (j->>'seq')::pg_catalog.int8 NOT BETWEEN 1 AND 16384
 OR jsonb_typeof(j->'prev_digest') IS DISTINCT FROM 'string' OR j->>'prev_digest' !~ '^[0-9a-f]{64}$'
 OR d->'terminal_schema' IS DISTINCT FROM '"native-nonsuccess-body-v1"'::pg_catalog.jsonb
 OR jsonb_typeof(d->'reason') IS DISTINCT FROM 'string'
 OR NOT (
  (d->'status'='"failed"'::pg_catalog.jsonb AND d->>'reason' IN
   ('provider_length','provider_content_filter','stream_done_with_pending','budget_exceeded','extended_budget_exceeded',
    'extended_agents_exceeded','trial_exhausted','trial_extended_agents_exhausted','context_limit',
    'tool_price_exceeded','embedding_price_exceeded','rate_limited','concurrency_exceeded','deadline_exceeded'))
  OR (d->'status'='"needs_attention"'::pg_catalog.jsonb AND d->>'reason' IN
   ('finish_evidence_missing','finish_evidence_conflict','uncertain_effect'))
 )
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
 FOREACH k IN ARRAY ARRAY['account_id','task_id','result_message_id','acceptance_record_id'] LOOP
  IF jsonb_typeof(d->k) IS DISTINCT FROM 'string' OR ((d->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM d->>k
  OR (d->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN RAISE EXCEPTION 'invalid'; END IF;
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

CREATE FUNCTION public.task_history_valid_turn_finish(f pg_catalog.jsonb) RETURNS pg_catalog.bool
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE p pg_catalog.jsonb; l pg_catalog.jsonb; binds pg_catalog.bool:=false;
 local_binding pg_catalog.bool:=false; local_kind pg_catalog.text;
BEGIN
 IF jsonb_typeof(f) IS DISTINCT FROM 'object' OR NOT f ?& ARRAY['version','outcome','reason','provider','limits']
 OR (f-ARRAY['version','outcome','reason','provider','limits'])<>'{}'::pg_catalog.jsonb
 OR f->'version' IS DISTINCT FROM '1'::pg_catalog.jsonb OR f->>'version'<>'1'
 OR jsonb_typeof(f->'outcome') IS DISTINCT FROM 'string'
 OR jsonb_typeof(f->'reason') IS DISTINCT FROM 'string'
 OR jsonb_typeof(f->'limits') IS DISTINCT FROM 'array' OR jsonb_array_length(f->'limits')>24
 THEN RETURN false; END IF;
 p:=f->'provider';
 IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR jsonb_typeof(p->'state') IS DISTINCT FROM 'string'
 OR p->>'state' NOT IN ('confirmed','missing','conflicting','not_attempted')
 OR (p-ARRAY['state','reason','provenance','dispatch_id'])<>'{}'::pg_catalog.jsonb
 OR (p ? 'dispatch_id' AND (jsonb_typeof(p->'dispatch_id') IS DISTINCT FROM 'string'
  OR p->>'dispatch_id' !~ '^[A-Za-z0-9_-]{1,64}$' OR p->>'state'='not_attempted'))
 OR (p->>'state'='confirmed' AND (jsonb_typeof(p->'reason') IS DISTINCT FROM 'string'
  OR p->>'reason' NOT IN ('stop','length','content_filter','tool_calls')
  OR p->'provenance' IS DISTINCT FROM '"provider_wire"'::pg_catalog.jsonb))
 OR (p->>'state'<>'confirmed' AND p ?| ARRAY['reason','provenance'])
 THEN RETURN false; END IF;
 local_kind:=CASE f->>'reason'
 WHEN 'budget_exceeded' THEN 'compute_budget'
 WHEN 'extended_budget_exceeded' THEN 'extended_compute_budget'
 WHEN 'extended_agents_exceeded' THEN 'extended_agent_runs'
 WHEN 'trial_exhausted' THEN 'trial_budget'
 WHEN 'trial_extended_agents_exhausted' THEN 'trial_extended_agent_runs'
 WHEN 'context_limit' THEN 'context'
 WHEN 'tool_price_exceeded' THEN 'tool_price'
 WHEN 'embedding_price_exceeded' THEN 'embedding_price'
 WHEN 'rate_limited' THEN 'rate'
 WHEN 'concurrency_exceeded' THEN 'concurrency'
 WHEN 'deadline_exceeded' THEN 'time' END;
 FOR l IN SELECT value FROM jsonb_array_elements(f->'limits') LOOP
  IF jsonb_typeof(l) IS DISTINCT FROM 'object' OR (l-ARRAY['kind','binding','value','unit'])<>'{}'::pg_catalog.jsonb
  OR jsonb_typeof(l->'kind') IS DISTINCT FROM 'string' OR l->>'kind' !~ '^[a-z][a-z0-9_]{0,63}$'
  OR jsonb_typeof(l->'binding') IS DISTINCT FROM 'boolean' OR (l ? 'value')<>(l ? 'unit')
  OR (l ? 'value' AND (jsonb_typeof(l->'value') IS DISTINCT FROM 'number'
   OR (l->>'value')::pg_catalog.numeric NOT BETWEEN 0 AND 1.7976931348623157e308
   OR jsonb_typeof(l->'unit') IS DISTINCT FROM 'string'
   OR l->>'unit' NOT IN ('tokens','microusd','seconds','attempts','rounds','requests')))
  THEN RETURN false; END IF;
  binds:=binds OR l->'binding'='true'::pg_catalog.jsonb;
  IF l->'binding'='true'::pg_catalog.jsonb AND l->>'kind'=local_kind THEN local_binding:=true; END IF;
 END LOOP;
 RETURN coalesce(CASE f->>'reason'
 WHEN 'provider_stop' THEN f->>'outcome'='complete' AND p->>'reason'='stop' AND NOT binds
 WHEN 'provider_length' THEN f->>'outcome'='incomplete' AND p->>'reason'='length' AND NOT binds
 WHEN 'provider_content_filter' THEN f->>'outcome'='failed' AND p->>'reason'='content_filter' AND NOT binds
 WHEN 'finish_evidence_missing' THEN f->>'outcome'='unknown' AND p->>'state'='missing' AND NOT binds
 WHEN 'finish_evidence_conflict' THEN f->>'outcome'='unknown'
  AND (p->>'state'='conflicting' OR p->>'reason'='tool_calls') AND NOT binds
 WHEN 'stream_done_with_pending' THEN f->>'outcome'='failed'
 ELSE f->>'outcome'='incomplete' AND local_binding
 END,false);
EXCEPTION WHEN OTHERS THEN
 RETURN false;
END $$;

-- Same signed-in W stager and existing function ACL. Preserve the complete-body
-- predicate from 065; only its negative terminal exclusion grows.
CREATE OR REPLACE FUNCTION public.task_history_terminal_project(p_frame pg_catalog.bytea)
RETURNS TABLE(history_id pg_catalog.uuid,seq pg_catalog.int8,record_id pg_catalog.uuid,digest pg_catalog.bytea,frame pg_catalog.bytea,created pg_catalog.bool)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j pg_catalog.jsonb; d pg_catalog.jsonb; b public.task_history_binding; a public.task_history_acceptance;
 t public.tasks; account public.entitlement_accounts; who pg_catalog.oid; writer_ids pg_catalog.oid[];
 top_xid pg_catalog.numeric; native_xid pg_catalog.xid; native_rows pg_catalog.int4:=0;
 nonsuccess pg_catalog.bool; reason pg_catalog.text; finish pg_catalog.jsonb;
 m public.messages; attempt public.task_attempts; e public.task_events; earlier pg_catalog.int8;
BEGIN
 PERFORM public.task_history_check_caller(false);
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO writer_ids FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_send_project(bytea)'::regprocedure AND x.grantee<>f.proowner;
 IF cardinality(writer_ids) IS DISTINCT FROM 1 OR who IS DISTINCT FROM writer_ids[1]
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 BEGIN
  j:=public.task_history_decode_terminal(p_frame);
 EXCEPTION WHEN SQLSTATE 'P0001' THEN
  j:=public.task_history_decode_nonsuccess(p_frame);
 END;
 nonsuccess:=j->>'kind'='NONSUCCESS_OUTPUT_PREPARE'; d:=j->'data';
 SELECT * INTO b FROM public.task_history_binding;
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
 OR t.lease_epoch<>(d->>'epoch')::pg_catalog.int8 OR t.finished_at IS NULL
 OR t.lease_owner IS NOT NULL OR t.lease_expires_at IS NOT NULL
 OR EXISTS(SELECT 1 FROM public.task_history_projection x WHERE x.task_id=a.task_id
  AND x.kind IN ('STOP_INTENT','TERMINAL_OUTPUT_PREPARE','NONSUCCESS_OUTPUT_PREPARE'))
 OR (SELECT count(*) FROM public.task_history_projection x WHERE x.history_id=b.history_id)<>(j->>'seq')::pg_catalog.int8-1
 OR NOT EXISTS(SELECT 1 FROM public.task_history_projection x WHERE x.history_id=b.history_id AND x.seq=(j->>'seq')::pg_catalog.int8-1 AND encode(x.digest,'hex')=j->>'prev_digest')
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 IF NOT nonsuccess THEN
  IF t.status<>'completed'
  OR NOT EXISTS(SELECT 1 FROM public.messages cm WHERE cm.id=a.result_message_id AND cm.user_id=a.account_id
   AND cm.conversation_id=a.conversation_id AND cm.role='assistant' AND cm.status='complete' AND cm.metadata->>'terminal_status'='complete')
  OR NOT EXISTS(SELECT 1 FROM public.task_attempts x WHERE x.task_id=t.id AND x.epoch=t.lease_epoch AND x.outcome='completed' AND x.ended_at IS NOT NULL)
  THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 ELSE
  reason:=d->>'reason';
  SELECT * INTO m FROM public.messages x WHERE x.id=a.result_message_id;
  SELECT * INTO attempt FROM public.task_attempts x WHERE x.task_id=t.id AND x.epoch=t.lease_epoch;
  SELECT * INTO e FROM public.task_events x WHERE x.task_id=t.id AND x.seq=t.event_seq;
  SELECT count(*) INTO earlier FROM public.task_attempts x WHERE x.task_id=t.id AND x.epoch<t.lease_epoch
   AND x.execution_started_at IS NOT NULL AND x.outcome IN ('lost','failed_retryable');
  finish:=m.metadata->'turn_finish';
  IF b.request_schema<>'chat-canonical-3715-v1'
  OR NOT EXISTS(SELECT 1 FROM public.task_history_projection p
   WHERE (p.history_id,p.seq,p.record_id,p.digest,p.frame,p.kind,p.account_id,p.account_generation,p.task_id)
    =(a.history_id,a.seq,a.record_id,a.digest,a.frame,'ACCEPT_PREPARE',a.account_id,a.account_generation,a.task_id))
  OR t.status IS DISTINCT FROM d->>'status' OR t.terminal_code IS DISTINCT FROM reason
  OR t.cancel_requested_at IS NOT NULL OR t.attempt_count<1
  OR NOT EXISTS(SELECT 1 FROM public.task_inference_recovery x WHERE x.task_id=t.id
   AND x.account_id=a.account_id AND x.account_generation=a.account_generation AND x.protocol_version=1)
  OR m.id IS NULL OR (m.user_id,m.conversation_id,m.role,m.status) IS DISTINCT FROM
   (a.account_id,a.conversation_id,'assistant','error')
  OR m.metadata->'terminal_status' IS DISTINCT FROM '"error"'::pg_catalog.jsonb
  OR m.metadata->'terminal_reason' IS DISTINCT FROM to_jsonb(reason)
  OR (m.metadata-ARRAY['terminal_status','terminal_reason','turn_finish','usage','uncertain_tools','regenerated_after_interruption'])<>'{}'::pg_catalog.jsonb
  OR (m.metadata ? 'usage' AND jsonb_typeof(m.metadata->'usage') IS DISTINCT FROM 'object')
  OR m.metadata->'regenerated_after_interruption' IS DISTINCT FROM (CASE WHEN earlier>0 THEN to_jsonb(earlier) END)
  OR (t.status='failed' AND m.metadata ? 'uncertain_tools')
  OR (t.status='needs_attention' AND NOT coalesce(
   jsonb_typeof(m.metadata->'uncertain_tools')='array'
   AND m.metadata->'uncertain_tools' @> (SELECT coalesce(jsonb_agg(DISTINCT x.tool_name),'[]'::pg_catalog.jsonb)
    FROM public.task_operations x WHERE x.task_id=t.id)
   AND jsonb_array_length(m.metadata->'uncertain_tools')=(SELECT count(DISTINCT x.tool_name)
    FROM public.task_operations x WHERE x.task_id=t.id),false))
  OR NOT public.task_history_valid_turn_finish(finish)
  OR (t.status='failed' AND (finish->>'outcome' NOT IN ('incomplete','failed') OR finish->>'reason'<>reason))
  OR (t.status='needs_attention' AND (finish->>'outcome' NOT IN ('incomplete','failed','unknown')
   OR (reason<>'uncertain_effect' AND (finish->>'outcome'<>'unknown' OR finish->>'reason'<>reason))))
  OR (reason='uncertain_effect') IS DISTINCT FROM EXISTS(SELECT 1 FROM public.task_operations x WHERE x.task_id=t.id)
  OR attempt.id IS NULL OR attempt.execution_started_at IS NULL OR attempt.ended_at IS DISTINCT FROM t.finished_at
  OR attempt.execution_started_at NOT BETWEEN attempt.started_at AND attempt.ended_at
  OR attempt.terminal_code IS DISTINCT FROM reason OR attempt.partial_ciphertext IS DISTINCT FROM m.content
  OR attempt.outcome IS DISTINCT FROM (CASE t.status WHEN 'failed' THEN 'failed_terminal' ELSE 'needs_attention' END)
  -- The payload is encrypted by the native writer. SQL checks the event's
  -- exact task/sequence/kind/time and presence; the guarded reader opens it.
  OR e.kind IS DISTINCT FROM 'finished' OR e.created_at IS DISTINCT FROM t.finished_at OR e.payload_ciphertext IS NULL
  THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 END IF;
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
 VALUES(b.history_id,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,j->>'kind',a.account_id,a.account_generation,a.task_id);
 RETURN QUERY SELECT b.history_id,(j->>'seq')::pg_catalog.int8,(j->>'record_id')::pg_catalog.uuid,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,true;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
END $$;

CREATE OR REPLACE FUNCTION public.task_history_terminal_page(p_history pg_catalog.uuid,after_seq pg_catalog.int8,expected_key_check pg_catalog.text)
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
 IF EXISTS(SELECT 1 FROM public.task_history_acceptance a WHERE NOT EXISTS(
 SELECT 1 FROM public.task_history_projection p WHERE (p.history_id,p.seq,p.record_id,p.digest,p.frame,p.kind)
 =(a.history_id,a.seq,a.record_id,a.digest,a.frame,'ACCEPT_PREPARE')))
 OR EXISTS(SELECT 1 FROM public.task_history_projection p WHERE p.kind='ACCEPT_PREPARE' AND NOT EXISTS(
 SELECT 1 FROM public.task_history_acceptance a WHERE (p.history_id,p.seq,p.record_id,p.digest,p.frame)
 =(a.history_id,a.seq,a.record_id,a.digest,a.frame))) THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 PERFORM * FROM public.task_history_acceptance_page(p_history,after_seq,expected_key_check);
 FOR r IN SELECT * FROM public.task_history_projection p WHERE p.history_id=p_history AND p.seq>after_seq
 ORDER BY p.seq LIMIT 32 LOOP
  IF r.kind='ACCEPT_PREPARE' THEN j:=public.task_history_decode_acceptance(r.frame);
  ELSIF r.kind='TERMINAL_OUTPUT_PREPARE' THEN j:=public.task_history_decode_terminal(r.frame);
  ELSIF r.kind='NONSUCCESS_OUTPUT_PREPARE' THEN j:=public.task_history_decode_nonsuccess(r.frame);
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

-- New private helpers cannot execute as any pre-existing service principal.
DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
 WHERE n.nspname='public' AND p.proname IN ('task_history_decode_nonsuccess','task_history_valid_turn_finish')
 AND x.grantee<>p.proowner AND NOT(x.grantee=0 AND x.privilege_type='EXECUTE' AND NOT x.is_grantable))
 THEN RAISE EXCEPTION 'task_history_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN ('task_history_decode_nonsuccess','task_history_valid_turn_finish') LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
