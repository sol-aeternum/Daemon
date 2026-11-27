-- Disposable fixture only, installed after 065 on its existing owner.
-- Caller owns the transaction and provisions the exact A/W EXECUTE grants.
SET LOCAL lock_timeout='1s';
SET LOCAL statement_timeout='5s';
DO $$ BEGIN
 IF CURRENT_USER<>(SELECT pg_get_userbyid(relowner) FROM pg_catalog.pg_class
 WHERE oid='public.task_history_binding'::regclass) THEN
  RAISE EXCEPTION 'task_history_owner_required'; END IF;
 -- No conversion or backfill of existing history to this profile. A fresh
 -- fixture may provision the empty binding before installing this schema.
 IF EXISTS(SELECT 1 FROM public.task_history_projection)
 OR EXISTS(SELECT 1 FROM public.task_history_acceptance) THEN
  RAISE EXCEPTION 'task_history_refused'; END IF;
END $$;

ALTER TABLE public.task_history_projection DROP CONSTRAINT task_history_projection_kind_check;
ALTER TABLE public.task_history_projection ADD CONSTRAINT task_history_projection_kind_check
 CHECK(kind IN ('ACCEPT_PREPARE','SEND_POSSIBLE','STOP_INTENT','ARTIFACT_PREPARE','TERMINAL_OUTPUT_PREPARE','INBOX_ORIGINAL_RETIRED'));

CREATE FUNCTION public.task_history_decode_retired(frame pg_catalog.bytea) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE n pg_catalog.int8; j pg_catalog.jsonb; d pg_catalog.jsonb; payload pg_catalog.text;
 data_wire pg_catalog.text; envelope_wire pg_catalog.text; k pg_catalog.text;
BEGIN
 IF frame IS NULL OR octet_length(frame) NOT BETWEEN 37 AND 8228 THEN RAISE EXCEPTION 'invalid'; END IF;
 n:=get_byte(frame,0)::pg_catalog.int8*16777216+get_byte(frame,1)::pg_catalog.int8*65536+get_byte(frame,2)::pg_catalog.int8*256+get_byte(frame,3);
 IF n NOT BETWEEN 1 AND 8192 OR octet_length(frame)<>n+36
 OR substring(frame FROM (n+5)::pg_catalog.int4 FOR 32) IS DISTINCT FROM
 sha256(convert_to('daemon.restore-history.frame.v5'||chr(10),'UTF8')||substring(frame FROM 1 FOR (n+4)::pg_catalog.int4))
 THEN RAISE EXCEPTION 'invalid'; END IF;
 payload:=convert_from(substring(frame FROM 5 FOR n::pg_catalog.int4),'UTF8'); j:=payload::pg_catalog.jsonb; d:=j->'data';
 IF jsonb_typeof(j) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(j))<>8
 OR NOT j ?& ARRAY['v','history_id','seq','prev_digest','record_id','kind','actor','data']
 OR j->'v' IS DISTINCT FROM '5'::pg_catalog.jsonb OR j->>'v'<>'5'
 OR j->'kind' IS DISTINCT FROM '"INBOX_ORIGINAL_RETIRED"'::pg_catalog.jsonb
 OR j->'actor' IS DISTINCT FROM '"worker"'::pg_catalog.jsonb
 OR jsonb_typeof(d) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(d))<>2
 OR NOT d ?& ARRAY['terminal_record_id','send_record_id']
 OR jsonb_typeof(j->'seq') IS DISTINCT FROM 'number' OR j->>'seq' !~ '^[1-9][0-9]*$'
 OR (j->>'seq')::pg_catalog.int8 NOT BETWEEN 1 AND 16384
 OR jsonb_typeof(j->'prev_digest') IS DISTINCT FROM 'string' OR j->>'prev_digest' !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'invalid'; END IF;
 FOREACH k IN ARRAY ARRAY['history_id','record_id'] LOOP
  IF jsonb_typeof(j->k) IS DISTINCT FROM 'string' OR ((j->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM j->>k
  OR (j->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN RAISE EXCEPTION 'invalid'; END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['terminal_record_id','send_record_id'] LOOP
  IF jsonb_typeof(d->k) IS DISTINCT FROM 'string' OR ((d->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM d->>k
  OR (d->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN RAISE EXCEPTION 'invalid'; END IF;
 END LOOP;
 IF d->>'terminal_record_id'=d->>'send_record_id'
 OR j->>'record_id' IN (d->>'terminal_record_id',d->>'send_record_id')
 THEN RAISE EXCEPTION 'invalid'; END IF;
 SELECT '{'||string_agg(to_jsonb(key)::pg_catalog.text||':'||value::pg_catalog.text,',' ORDER BY key COLLATE pg_catalog."C")||'}'
 INTO data_wire FROM jsonb_each(d);
 SELECT '{'||string_agg(to_jsonb(key)::pg_catalog.text||':'||CASE WHEN key='data' THEN data_wire ELSE value::pg_catalog.text END,
 ',' ORDER BY key COLLATE pg_catalog."C")||'}' INTO envelope_wire FROM jsonb_each(j);
 IF convert_to(envelope_wire,'UTF8') IS DISTINCT FROM convert_to(payload,'UTF8') THEN RAISE EXCEPTION 'invalid'; END IF;
 RETURN j;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
END $$;

CREATE FUNCTION public.task_history_retired_project(p_frame pg_catalog.bytea)
RETURNS TABLE(history_id pg_catalog.uuid,seq pg_catalog.int8,record_id pg_catalog.uuid,digest pg_catalog.bytea,frame pg_catalog.bytea,created pg_catalog.bool)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j pg_catalog.jsonb; d pg_catalog.jsonb; b public.task_history_binding;
 a public.task_history_acceptance; terminal public.task_history_projection; send public.task_history_projection;
 tj pg_catalog.jsonb; sj pg_catalog.jsonb; td pg_catalog.jsonb; sd pg_catalog.jsonb;
 t public.tasks; account public.entitlement_accounts; result public.messages;
 attempt public.task_attempts; finished public.task_events;
 who pg_catalog.oid; writer_ids pg_catalog.oid[];
BEGIN
 PERFORM public.task_history_check_caller(false);
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO writer_ids FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_send_project(bytea)'::regprocedure AND x.grantee<>f.proowner;
 IF cardinality(writer_ids) IS DISTINCT FROM 1 OR who IS DISTINCT FROM writer_ids[1]
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 j:=public.task_history_decode_retired(p_frame); d:=j->'data'; SELECT * INTO b FROM public.task_history_binding;
 -- Exactly one accepted original, one Send, one terminal; this operation is
 -- never a reconstruction, subsequent retirement, or idempotent readback.
 SELECT * INTO terminal FROM public.task_history_projection p WHERE p.history_id=b.history_id
 AND p.record_id=(d->>'terminal_record_id')::pg_catalog.uuid AND p.kind='TERMINAL_OUTPUT_PREPARE';
 SELECT * INTO send FROM public.task_history_projection p WHERE p.history_id=b.history_id
 AND p.record_id=(d->>'send_record_id')::pg_catalog.uuid AND p.kind='SEND_POSSIBLE';
 tj:=public.task_history_decode_terminal(terminal.frame); td:=tj->'data';
 sj:=public.task_history_decode_effect(send.frame); sd:=sj->'data';
 SELECT * INTO a FROM public.task_history_acceptance x WHERE x.history_id=b.history_id AND x.record_id=(td->>'acceptance_record_id')::pg_catalog.uuid;
 IF b.history_id IS DISTINCT FROM (j->>'history_id')::pg_catalog.uuid OR b.version<>1 OR b.key_id<>1
 OR b.request_schema<>'chat-canonical-3715-v1' OR a.task_id IS NULL
 OR (a.seq,send.seq,terminal.seq,(j->>'seq')::pg_catalog.int8) IS DISTINCT FROM (1::bigint,2::bigint,3::bigint,4::bigint)
 OR encode(terminal.digest,'hex') IS DISTINCT FROM j->>'prev_digest'
 OR encode(send.digest,'hex') IS DISTINCT FROM tj->>'prev_digest'
 OR encode(a.digest,'hex') IS DISTINCT FROM sj->>'prev_digest'
 OR public.task_history_decode_acceptance(a.frame)->>'prev_digest' IS DISTINCT FROM repeat('0',64)
 OR (SELECT count(*) FROM public.task_history_projection p WHERE p.history_id=b.history_id)<>3
 OR (SELECT count(*) FROM public.task_history_acceptance x WHERE x.history_id=b.history_id)<>1
 OR (a.account_id,a.account_generation,a.task_id,a.result_message_id) IS DISTINCT FROM
 ((td->>'account_id')::uuid,(td->>'account_generation')::bigint,(td->>'task_id')::uuid,(td->>'result_message_id')::uuid)
 OR (a.account_id,a.account_generation,a.task_id,(td->>'epoch')::bigint) IS DISTINCT FROM
 ((sd->>'account_id')::uuid,(sd->>'account_generation')::bigint,(sd->>'task_id')::uuid,(sd->>'epoch')::bigint)
 OR sd->>'effect_kind' IS DISTINCT FROM 'completion'
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 -- Retain the existing mixed projection's column and acceptance-index checks
 -- before staging. The caller independently checks complete H/SQL parity.
 PERFORM * FROM public.task_history_terminal_page(b.history_id,0,b.key_check);
 SELECT * INTO account FROM public.entitlement_accounts WHERE user_id=a.account_id FOR SHARE;
 SELECT * INTO t FROM public.tasks WHERE id=a.task_id FOR SHARE;
 SELECT * INTO result FROM public.messages WHERE id=a.result_message_id FOR SHARE;
 SELECT * INTO attempt FROM public.task_attempts x WHERE x.task_id=t.id AND x.epoch=t.lease_epoch FOR SHARE;
 SELECT * INTO finished FROM public.task_events x WHERE x.task_id=t.id AND x.seq=t.event_seq FOR SHARE;
 IF account.user_id IS NULL OR account.status<>'active' OR account.content_generation<>a.account_generation
 OR account.trial_reserved_microusd<>0 OR account.trial_extended_agents_reserved<>0
 OR EXISTS(SELECT 1 FROM public.account_deletions WHERE account_id=a.account_id AND closed_at IS NULL)
 OR EXISTS(SELECT 1 FROM public.account_suspension_causes WHERE account_id=a.account_id)
 OR t.id IS NULL OR (t.user_id,t.conversation_id,t.user_message_id,t.result_message_id,t.lease_epoch) IS DISTINCT FROM
 (a.account_id,a.conversation_id,a.user_message_id,a.result_message_id,(td->>'epoch')::bigint)
 OR t.status<>'completed' OR t.finished_at IS NULL OR t.terminal_code IS NOT NULL OR t.cancel_requested_at IS NOT NULL
 OR t.lease_owner IS NOT NULL OR t.lease_expires_at IS NOT NULL OR t.attempt_count<>1
 OR (result.id,result.user_id,result.conversation_id,result.role,result.status,result.metadata->>'terminal_status') IS DISTINCT FROM
 (a.result_message_id,a.account_id,a.conversation_id,'assistant'::text,'complete'::text,'complete'::text)
 OR result.content IS NULL OR result.content=''
 OR (SELECT count(*) FROM public.task_attempts x WHERE x.task_id=t.id)<>1
 OR attempt.id IS NULL OR attempt.outcome<>'completed' OR attempt.terminal_code IS NOT NULL
 OR attempt.execution_started_at IS NULL OR attempt.execution_started_at<attempt.started_at
 OR attempt.ended_at IS DISTINCT FROM t.finished_at OR attempt.ended_at<attempt.execution_started_at
 OR attempt.partial_ciphertext IS DISTINCT FROM result.content
 OR attempt.compute_scope_id IS DISTINCT FROM (sd->>'financial_scope_id')::uuid
 OR (finished.task_id,finished.seq,finished.kind,finished.created_at) IS DISTINCT FROM
 (t.id,t.event_seq,'finished'::text,t.finished_at)
 OR finished.payload_ciphertext IS NULL OR finished.payload_ciphertext=''
 OR (SELECT count(*) FROM public.task_events x WHERE x.task_id=t.id AND x.kind='finished')<>1
 OR EXISTS(SELECT 1 FROM public.task_events x WHERE x.task_id=t.id AND x.seq>t.event_seq)
 OR NOT EXISTS(SELECT 1 FROM public.attempt_lifecycle_control x WHERE x.id=(sd->>'lifecycle_id')::uuid
 AND x.account_id=a.account_id AND x.task_id=t.id AND x.epoch=t.lease_epoch AND x.closed_at IS NOT NULL AND x.revision=2
 AND x.financial_scope_id=(sd->>'financial_scope_id')::uuid AND x.metadata_scope_id=(sd->>'metadata_scope_id')::uuid
 AND x.attempt_started_at=attempt.started_at
 AND x.identity=jsonb_build_object('lifecycle_id',sd->>'lifecycle_id','account_id',sd->>'account_id',
 'generation',(sd->>'account_generation')::bigint,'task_id',sd->>'task_id','epoch',(sd->>'epoch')::bigint,
 'financial_scope_id',sd->>'financial_scope_id','metadata_scope_id',sd->>'metadata_scope_id',
 'producer_instance_id',sd->>'producer_instance_id') FOR SHARE)
 OR (SELECT count(*) FROM public.attempt_lifecycle_control x WHERE x.task_id=t.id)<>1
 OR NOT EXISTS(SELECT 1 FROM public.entitlement_reservations x
 JOIN public.compute_reservation_metadata m ON m.reservation_id=x.id
 JOIN public.compute_scope_metadata s ON s.id=m.metadata_scope_id
 JOIN public.entitlement_usage_periods p ON (p.user_id,p.period_key)=(x.user_id,x.period_key)
 WHERE x.id=(sd->>'reservation_id')::uuid AND x.user_id=a.account_id AND x.scope_id=(sd->>'financial_scope_id')::uuid
 AND x.operation='chat' AND x.status='settled' AND x.actual_microusd BETWEEN 0 AND x.reserved_microusd
 AND x.overage_microusd=0 AND x.settled_at>=x.created_at
 AND x.period_key=to_char(x.created_at AT TIME ZONE 'UTC','YYYY-MM')
 AND ((x.charge_kind='plan' AND p.spent_microusd>=x.actual_microusd)
 OR (x.charge_kind='trial' AND account.trial_consumed_microusd>=x.actual_microusd))
 AND p.reserved_microusd=0 AND p.extended_reserved_microusd=0 AND p.extended_agents_reserved=0
 AND s.account_id=a.account_id
 AND m.metadata_scope_id=(sd->>'metadata_scope_id')::uuid AND m.runtime_identity=(sd->>'runtime_identity')::uuid
 AND m.dispatch_nonce=(sd->>'dispatch_nonce')::uuid FOR SHARE OF x,m,s,p)
 OR EXISTS(SELECT 1 FROM public.entitlement_reservations x WHERE x.user_id=a.account_id AND x.status='open')
 OR EXISTS(SELECT 1 FROM public.entitlement_usage_periods p WHERE p.user_id=a.account_id
 AND (p.reserved_microusd<>0 OR p.extended_reserved_microusd<>0 OR p.extended_agents_reserved<>0))
 OR (SELECT count(*) FROM public.entitlement_reservations x WHERE x.scope_id=(sd->>'financial_scope_id')::uuid)<>1
 OR (SELECT count(*) FROM public.compute_reservation_metadata x WHERE x.metadata_scope_id=(sd->>'metadata_scope_id')::uuid)<>1
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 INSERT INTO public.task_history_projection(history_id,seq,record_id,digest,frame,kind,account_id,account_generation,task_id)
 VALUES(b.history_id,(j->>'seq')::bigint,(j->>'record_id')::uuid,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,'INBOX_ORIGINAL_RETIRED',a.account_id,a.account_generation,a.task_id);
 RETURN QUERY SELECT b.history_id,(j->>'seq')::bigint,(j->>'record_id')::uuid,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,true;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
END $$;

CREATE FUNCTION public.task_history_retired_page(p_history pg_catalog.uuid,after_seq pg_catalog.int8,expected_key_check pg_catalog.text)
RETURNS TABLE(history_id pg_catalog.uuid,seq pg_catalog.int8,record_id pg_catalog.uuid,digest pg_catalog.bytea,frame pg_catalog.bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b public.task_history_binding; r public.task_history_projection; j pg_catalog.jsonb; d pg_catalog.jsonb;
 terminal public.task_history_projection; send public.task_history_projection; accepted public.task_history_acceptance;
 tj pg_catalog.jsonb; sj pg_catalog.jsonb; td pg_catalog.jsonb; sd pg_catalog.jsonb;
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
 =(a.history_id,a.seq,a.record_id,a.digest,a.frame)))
 OR (SELECT count(*) FROM public.task_history_projection p WHERE p.history_id=p_history AND p.kind='INBOX_ORIGINAL_RETIRED')>1
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 PERFORM * FROM public.task_history_acceptance_page(p_history,after_seq,expected_key_check);
 FOR r IN SELECT * FROM public.task_history_projection p WHERE p.history_id=p_history AND p.seq>after_seq
 ORDER BY p.seq LIMIT 32 LOOP
  IF r.kind='INBOX_ORIGINAL_RETIRED' THEN
   j:=public.task_history_decode_retired(r.frame); d:=j->'data';
   SELECT * INTO terminal FROM public.task_history_projection p WHERE p.history_id=p_history
   AND p.record_id=(d->>'terminal_record_id')::uuid AND p.kind='TERMINAL_OUTPUT_PREPARE';
   SELECT * INTO send FROM public.task_history_projection p WHERE p.history_id=p_history
   AND p.record_id=(d->>'send_record_id')::uuid AND p.kind='SEND_POSSIBLE';
   tj:=public.task_history_decode_terminal(terminal.frame); td:=tj->'data';
   sj:=public.task_history_decode_effect(send.frame); sd:=sj->'data';
   SELECT * INTO accepted FROM public.task_history_acceptance x WHERE x.history_id=p_history AND x.record_id=(td->>'acceptance_record_id')::uuid;
   IF (accepted.seq,send.seq,terminal.seq,r.seq) IS DISTINCT FROM (1::bigint,2::bigint,3::bigint,4::bigint)
   OR encode(terminal.digest,'hex') IS DISTINCT FROM j->>'prev_digest'
   OR encode(send.digest,'hex') IS DISTINCT FROM tj->>'prev_digest'
   OR encode(accepted.digest,'hex') IS DISTINCT FROM sj->>'prev_digest'
   OR public.task_history_decode_acceptance(accepted.frame)->>'prev_digest' IS DISTINCT FROM repeat('0',64)
   OR (accepted.account_id,accepted.account_generation,accepted.task_id,accepted.result_message_id) IS DISTINCT FROM
   ((td->>'account_id')::uuid,(td->>'account_generation')::bigint,(td->>'task_id')::uuid,(td->>'result_message_id')::uuid)
   OR (td->>'account_id',td->>'account_generation',td->>'task_id',td->>'epoch') IS DISTINCT FROM
   (sd->>'account_id',sd->>'account_generation',sd->>'task_id',sd->>'epoch')
   OR sd->>'effect_kind' IS DISTINCT FROM 'completion'
   THEN RAISE EXCEPTION 'task_history_refused'; END IF;
   -- The retirement fact has no duplicate identity fields; its projection
   -- columns derive from the validated original terminal ancestor.
   d:=td;
  ELSE
   IF r.kind='ACCEPT_PREPARE' THEN j:=public.task_history_decode_acceptance(r.frame);
   ELSIF r.kind='TERMINAL_OUTPUT_PREPARE' THEN j:=public.task_history_decode_terminal(r.frame);
   ELSIF r.kind='ARTIFACT_PREPARE' THEN j:=public.task_history_decode_artifact(r.frame);
   ELSE j:=public.task_history_decode_effect(r.frame); END IF;
   d:=j->'data';
  END IF;
  IF ((j->>'history_id')::uuid,(j->>'seq')::bigint,(j->>'record_id')::uuid,
   substring(r.frame FROM octet_length(r.frame)-31 FOR 32),j->>'kind',(d->>'account_id')::uuid,
   (d->>'account_generation')::bigint,(d->>'task_id')::uuid)
   IS DISTINCT FROM (r.history_id,r.seq,r.record_id,r.digest,r.kind,r.account_id,r.account_generation,r.task_id)
  THEN RAISE EXCEPTION 'task_history_refused'; END IF;
  RETURN QUERY SELECT r.history_id,r.seq,r.record_id,r.digest,r.frame;
 END LOOP;
END $$;

-- Extend only the exact 065 caller guard. All inherited native principal,
-- role membership, protected table/column and function ACL checks remain.
DO $install$ DECLARE source text; BEGIN
 SELECT p.prosrc INTO source FROM pg_catalog.pg_proc p
 WHERE p.oid='public.task_history_check_caller(boolean)'::regprocedure;
 IF encode(sha256(convert_to(source,'UTF8')),'hex') IS DISTINCT FROM
 '15a428276bc9ffee29e4d2a36ba0b40c9f7ea19eb366db8284ed5be19ca2c609'
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 source:=replace(source,
  '''task_history_terminal_project'',''task_history_terminal_page'')',
  '''task_history_terminal_project'',''task_history_terminal_page'',''task_history_retired_project'',''task_history_retired_page'')');
 source:=replace(source,
  '''public.task_history_terminal_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)''::pg_catalog.regprocedure))) THEN',
  '''public.task_history_terminal_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)''::pg_catalog.regprocedure,
  ''public.task_history_retired_project(bytea)''::pg_catalog.regprocedure,
  ''public.task_history_retired_page(uuid,bigint,text)''::pg_catalog.regprocedure))) THEN');
 source:=replace(source, chr(10)||'END ', $suffix$
 IF (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_retired_project(bytea)'::regprocedure AND x.grantee<>f.proowner)
 IS DISTINCT FROM (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_send_project(bytea)'::regprocedure AND x.grantee<>f.proowner)
 OR (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_retired_page(uuid,bigint,text)'::regprocedure AND x.grantee<>f.proowner)
 IS DISTINCT FROM read_grants
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
END $suffix$);
 IF position('task_history_retired_project(bytea)' in source)=0
 OR position('task_history_retired_page(uuid,bigint,text)' in source)=0
 OR position('IS DISTINCT FROM read_grants'||chr(10)||' THEN RAISE EXCEPTION ''task_history_refused''; END IF;'||chr(10)||'END ' in source)=0
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 EXECUTE format('CREATE OR REPLACE FUNCTION public.task_history_check_caller(writing pg_catalog.bool) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS %L',source);
END $install$;

DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
 WHERE n.nspname='public' AND p.proname IN ('task_history_decode_retired','task_history_retired_project','task_history_retired_page')
 AND x.grantee<>p.proowner AND NOT(x.grantee=0 AND x.privilege_type='EXECUTE' AND NOT x.is_grantable))
 THEN RAISE EXCEPTION 'task_history_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN ('task_history_decode_retired','task_history_retired_project','task_history_retired_page') LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
