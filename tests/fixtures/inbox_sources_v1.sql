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
 IF (SELECT count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'task_history_%')<>16
 OR (SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.task_history_projection'::regclass AND conname='task_history_projection_kind_check') IS DISTINCT FROM
 'CHECK ((kind = ANY (ARRAY[''ACCEPT_PREPARE''::text, ''SEND_POSSIBLE''::text, ''STOP_INTENT''::text, ''ARTIFACT_PREPARE''::text, ''TERMINAL_OUTPUT_PREPARE''::text])))' THEN RAISE EXCEPTION 'task_history_refused'; END IF;
END $$;

-- Qualify the complete predecessor inventory before altering the kind check.
-- This is the exact 065 fixture catalog, with its already-provisioned A/W ACLs.
DO $$ DECLARE a oid[]; w oid[]; r oid[]; publisher oid[]; owner oid; BEGIN
 SELECT relowner INTO owner FROM pg_class WHERE oid='public.task_history_binding'::regclass;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO a FROM pg_proc f
 CROSS JOIN LATERAL aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_acceptance_project(bytea)'::regprocedure AND x.grantee<>owner;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO r FROM pg_proc f
 CROSS JOIN LATERAL aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_acceptance_page(uuid,bigint,text)'::regprocedure AND x.grantee<>owner;
 SELECT array_agg(x) INTO w FROM unnest(r) x WHERE x<>a[1];
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO publisher FROM pg_proc f
 CROSS JOIN LATERAL aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.resource_output_catalog_snapshot(jsonb,uuid)'::regprocedure AND x.grantee<>f.proowner;
 IF cardinality(a) IS DISTINCT FROM 1 OR cardinality(w) IS DISTINCT FROM 1 OR cardinality(r) IS DISTINCT FROM 2
 OR cardinality(publisher) IS DISTINCT FROM 1 OR publisher[1]=ANY(r) OR publisher[1]=0
 OR NOT a[1]=ANY(r) OR 0=ANY(r)
 OR NOT (SELECT count(*)=2 AND bool_and(rolcanlogin) FROM pg_roles WHERE oid=ANY(r))
 OR NOT (SELECT count(*)=16 AND bool_and(
  p.proowner=owner AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')=expected.sha
  AND p.proconfig=ARRAY['search_path=pg_catalog']::text[] AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
  AND p.prosecdef=(p.proname NOT LIKE 'task_history_decode_%')
  AND p.provolatile=CASE WHEN p.proname LIKE 'task_history_decode_%' THEN 'i'::"char" ELSE 'v'::"char" END
  AND NOT p.proisstrict AND NOT p.proleakproof AND p.proparallel='u' AND p.pronargdefaults=0 AND p.provariadic=0
  AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
  WHERE x.grantee<>owner AND (x.is_grantable OR x.privilege_type<>'EXECUTE'))
  AND (SELECT coalesce(array_agg(x.grantee ORDER BY x.grantee),ARRAY[]::oid[])
  FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x WHERE x.grantee<>owner)
  = CASE expected.role WHEN 'a' THEN a WHEN 'w' THEN w WHEN 'r' THEN r WHEN 'p' THEN publisher ELSE ARRAY[]::oid[] END)
  FROM (VALUES
   ('public.task_history_acceptance_page(uuid,bigint,text)','2672c608f88a1b38b01bca230d129ff733637470818f684f4b63935e5d9b7043','r'),
   ('public.task_history_acceptance_project(bytea)','b7c9cdc777bb83cd8aeda25125abbdb157785a254a4f3f899acbb7895e8ff1ac','a'),
   ('public.task_history_artifact_project(bytea)','3b1879a9bf1ba5a99772fa873a1876682d7bf345b0992e295c80fc4dbbd73074','p'),
   ('public.task_history_check_caller(boolean)','15a428276bc9ffee29e4d2a36ba0b40c9f7ea19eb366db8284ed5be19ca2c609',''),
   ('public.task_history_decode_acceptance(bytea)','f1251134f66530e627d121f11f95aef8e787cd0e10a642b0d6a3f19ac186c84f',''),
   ('public.task_history_decode_artifact(bytea)','cf0855f3b73627f2b5a7365952a050933b3573bd846b0bc09d9e9803d0c34dc0',''),
   ('public.task_history_decode_effect(bytea)','9437102792c2ae1c41c636560068c6eea2bd5f510d884c04ff5be1933bfaf1e1',''),
   ('public.task_history_decode_terminal(bytea)','513a3f56835c3a725132c3dcaa419c8647e7182e6d9bde2591061c80414819c5',''),
   ('public.task_history_immutable()','afff3cc45846f7411c31e5fdf9709c40d4576f1f33d99bed657f5b07ac35a5c1',''),
   ('public.task_history_output_page(uuid,bigint,text)','c998c3980915a226230767021a589fc0c4277062befab864d89f50ad13f6b72e','r'),
   ('public.task_history_page(uuid,bigint,text)','6d31a1bf503ca3d764f896a5d77c3a15d6cde16577348352eca1542ce14684d0','r'),
   ('public.task_history_project_frame(bytea,boolean)','039cc985699f366e5f7600b6be00c91d13fec0e550b7ea62aa7de730f2026479',''),
   ('public.task_history_send_project(bytea)','e06ce6a715602633a76f6c16ec9aa572e98ee7193b6cee2bbf088c885deeb30a','w'),
   ('public.task_history_stop_project(bytea)','e19fd4ed88f9509d9e6316de6f969852b05b1d42a3004dba8a4ec8c465374145','w'),
   ('public.task_history_terminal_page(uuid,bigint,text)','091393ff0c3e691a29ae23e335c20b217b8087d59c64127601fbfb6f5c14e2b8','r'),
   ('public.task_history_terminal_project(bytea)','c35be9008df1bf7f333457b61ac63a1e3fe9060693ce78b8b09001957d09564f','w')
  ) expected(signature,sha,role) JOIN pg_proc p ON p.oid=to_regprocedure(expected.signature))
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
END $$;

ALTER TABLE public.task_history_projection DROP CONSTRAINT task_history_projection_kind_check;
ALTER TABLE public.task_history_projection ADD CONSTRAINT task_history_projection_kind_check
 CHECK(kind IN ('ACCEPT_PREPARE','SEND_POSSIBLE','STOP_INTENT','ARTIFACT_PREPARE','TERMINAL_OUTPUT_PREPARE','SOURCE_READ_POSSIBLE','SOURCE_PIN_PREPARE'));

CREATE FUNCTION public.task_history_decode_source(frame pg_catalog.bytea) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE n pg_catalog.int8; j pg_catalog.jsonb; d pg_catalog.jsonb; payload pg_catalog.text;
 data_wire pg_catalog.text; envelope_wire pg_catalog.text; k pg_catalog.text;
 keys pg_catalog.text[]; common text[]:=ARRAY['account_id','account_generation','task_id','epoch','acceptance_record_id','ordinal']; nums text[]; commitment text;
BEGIN
 IF frame IS NULL OR octet_length(frame) NOT BETWEEN 37 AND 8228 THEN RAISE EXCEPTION 'invalid'; END IF;
 n:=get_byte(frame,0)::pg_catalog.int8*16777216+get_byte(frame,1)::pg_catalog.int8*65536+get_byte(frame,2)::pg_catalog.int8*256+get_byte(frame,3);
 IF n NOT BETWEEN 1 AND 8192 OR octet_length(frame)<>n+36
 OR substring(frame FROM (n+5)::pg_catalog.int4 FOR 32) IS DISTINCT FROM
 sha256(convert_to('daemon.restore-history.frame.v6'||chr(10),'UTF8')||substring(frame FROM 1 FOR (n+4)::pg_catalog.int4))
 THEN RAISE EXCEPTION 'invalid'; END IF;
 payload:=convert_from(substring(frame FROM 5 FOR n::pg_catalog.int4),'UTF8'); j:=payload::pg_catalog.jsonb; d:=j->'data';
 IF j->'kind'='"SOURCE_READ_POSSIBLE"'::jsonb THEN
  keys:=common||ARRAY['count','search_record_id','selection_record_id','selection_commitment'];
  nums:=ARRAY['account_generation','epoch','ordinal','count']; commitment:='selection_commitment';
 ELSIF j->'kind'='"SOURCE_PIN_PREPARE"'::jsonb THEN
  keys:=common||ARRAY['possible_record_id','snapshot_id','event_seq','passage_commitment'];
  nums:=ARRAY['account_generation','epoch','ordinal','event_seq']; commitment:='passage_commitment';
 ELSE RAISE EXCEPTION 'invalid'; END IF;
 IF jsonb_typeof(j) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(j))<>8
 OR NOT j ?& ARRAY['v','history_id','seq','prev_digest','record_id','kind','actor','data']
 OR j->'v' IS DISTINCT FROM '6'::pg_catalog.jsonb OR j->>'v'<>'6'
 OR j->'actor' IS DISTINCT FROM '"worker"'::pg_catalog.jsonb
 OR jsonb_typeof(d) IS DISTINCT FROM 'object'
 OR (SELECT count(*) FROM jsonb_object_keys(d))<>cardinality(keys) OR NOT d ?& keys
 OR jsonb_typeof(j->'seq') IS DISTINCT FROM 'number' OR j->>'seq' !~ '^[1-9][0-9]*$'
 OR (j->>'seq')::pg_catalog.int8 NOT BETWEEN 1 AND 16384
 OR jsonb_typeof(j->'prev_digest') IS DISTINCT FROM 'string' OR j->>'prev_digest' !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(d->commitment) IS DISTINCT FROM 'string' OR d->>commitment !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'invalid'; END IF;
 FOREACH k IN ARRAY nums LOOP
  IF jsonb_typeof(d->k) IS DISTINCT FROM 'number' OR d->>k !~ '^(0|[1-9][0-9]*)$'
  OR (d->>k)::pg_catalog.int8<0 THEN RAISE EXCEPTION 'invalid'; END IF;
 END LOOP;
 IF (d->>'epoch')::pg_catalog.int8<1 OR (d->>'ordinal')::bigint NOT BETWEEN 1 AND 2 OR (d ? 'count' AND (d->>'count')::bigint NOT BETWEEN (d->>'ordinal')::bigint AND 2) OR (d ? 'event_seq' AND (d->>'event_seq')::bigint<1) THEN RAISE EXCEPTION 'invalid'; END IF;
 FOREACH k IN ARRAY ARRAY['history_id','record_id'] LOOP
  IF jsonb_typeof(j->k) IS DISTINCT FROM 'string' OR ((j->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM j->>k
  OR (j->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN RAISE EXCEPTION 'invalid'; END IF;
 END LOOP;
 FOREACH k IN ARRAY keys LOOP
  IF NOT k=ANY(nums) AND k<>commitment THEN
   IF jsonb_typeof(d->k) IS DISTINCT FROM 'string' OR ((d->>k)::pg_catalog.uuid)::pg_catalog.text IS DISTINCT FROM d->>k
   OR (d->>k)::pg_catalog.uuid='00000000-0000-0000-0000-000000000000' THEN RAISE EXCEPTION 'invalid'; END IF;
  END IF;
 END LOOP;
 IF (d ? 'count' AND (SELECT count(DISTINCT v) FROM unnest(ARRAY[j->>'record_id',d->>'acceptance_record_id',d->>'search_record_id',d->>'selection_record_id']) v)<>4) OR (d ? 'event_seq' AND (SELECT count(DISTINCT v) FROM unnest(ARRAY[j->>'record_id',d->>'acceptance_record_id',d->>'possible_record_id']) v)<>3) THEN RAISE EXCEPTION 'invalid'; END IF;
 SELECT '{'||string_agg(to_jsonb(key)::pg_catalog.text||':'||value::pg_catalog.text,',' ORDER BY key COLLATE pg_catalog."C")||'}'
 INTO data_wire FROM jsonb_each(d);
 SELECT '{'||string_agg(to_jsonb(key)::pg_catalog.text||':'||CASE WHEN key='data' THEN data_wire ELSE value::pg_catalog.text END,
 ',' ORDER BY key COLLATE pg_catalog."C")||'}' INTO envelope_wire FROM jsonb_each(j);
 IF convert_to(envelope_wire,'UTF8') IS DISTINCT FROM convert_to(payload,'UTF8') THEN RAISE EXCEPTION 'invalid'; END IF;
 RETURN j;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
END $$;


CREATE FUNCTION public.task_history_source_page(p_history uuid,after_seq bigint,expected_key_check text)
RETURNS TABLE(history_id uuid,seq bigint,record_id uuid,digest bytea,frame bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b public.task_history_binding; r public.task_history_projection; j jsonb; d jsonb;
 a jsonb; a_record text; first_send jsonb; search_record text; selection_record text;
 possible_record text; selected_count bigint; selections text[]; snapshots text[];
 last_event bigint; n bigint:=0; sent int:=0; pinned int:=0; finished bool:=true;
 awaiting_pin bool:=false; stopped bool:=false; originals int:=0; previous bytea:=decode(repeat('00',32),'hex');
 reservations text[]; runtimes text[]; nonces text[];
BEGIN
 PERFORM public.task_history_check_caller(false);
 SELECT * INTO b FROM public.task_history_binding;
 IF p_history IS NULL OR b.history_id IS DISTINCT FROM p_history OR b.version<>1 OR b.key_id<>1
 OR b.request_schema<>'chat-canonical-3715-v1' OR expected_key_check IS NULL
 OR b.key_check IS DISTINCT FROM expected_key_check OR after_seq IS NULL OR after_seq NOT BETWEEN 0 AND 16384
 OR EXISTS(SELECT 1 FROM public.task_history_projection p WHERE p.history_id<>p_history)
 OR EXISTS(SELECT 1 FROM public.task_history_acceptance x WHERE NOT EXISTS(
 SELECT 1 FROM public.task_history_projection p WHERE (p.history_id,p.seq,p.record_id,p.digest,p.frame,p.kind)
 =(x.history_id,x.seq,x.record_id,x.digest,x.frame,'ACCEPT_PREPARE')))
 OR EXISTS(SELECT 1 FROM public.task_history_projection p WHERE p.kind='ACCEPT_PREPARE' AND NOT EXISTS(
 SELECT 1 FROM public.task_history_acceptance x WHERE (p.history_id,p.seq,p.record_id,p.digest,p.frame)
 =(x.history_id,x.seq,x.record_id,x.digest,x.frame)))
 OR (SELECT count(*) FROM public.task_history_projection)>20
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 PERFORM * FROM public.task_history_acceptance_page(p_history,0,expected_key_check);
 FOR r IN SELECT * FROM public.task_history_projection p ORDER BY p.seq LOOP
  n:=n+1;
  IF r.kind='ACCEPT_PREPARE' THEN j:=public.task_history_decode_acceptance(r.frame);
  ELSIF r.kind='TERMINAL_OUTPUT_PREPARE' THEN j:=public.task_history_decode_terminal(r.frame);
  ELSIF r.kind IN ('SOURCE_READ_POSSIBLE','SOURCE_PIN_PREPARE') THEN j:=public.task_history_decode_source(r.frame);
  ELSE j:=public.task_history_decode_effect(r.frame); END IF;
  d:=j->'data';
  IF ((j->>'history_id')::uuid,(j->>'seq')::bigint,(j->>'record_id')::uuid,
   substring(r.frame FROM octet_length(r.frame)-31 FOR 32),j->>'kind',(d->>'account_id')::uuid,
   (d->>'account_generation')::bigint,(d->>'task_id')::uuid)
   IS DISTINCT FROM (r.history_id,r.seq,r.record_id,r.digest,r.kind,r.account_id,r.account_generation,r.task_id)
   OR r.seq<>n OR j->>'prev_digest' IS DISTINCT FROM encode(previous,'hex')
   THEN RAISE EXCEPTION 'task_history_refused'; END IF;
  previous:=r.digest;
  IF r.kind='ACCEPT_PREPARE' THEN
   originals:=originals+1;
   IF NOT finished OR stopped OR originals>2 OR (a IS NOT NULL AND
   (a->>'account_id',a->>'account_generation') IS DISTINCT FROM (d->>'account_id',d->>'account_generation'))
   THEN RAISE EXCEPTION 'task_history_refused'; END IF;
   a:=d; a_record:=j->>'record_id'; first_send:=NULL; sent:=0; pinned:=0;
   selected_count:=NULL; selections:=ARRAY[]::text[]; snapshots:=ARRAY[]::text[];
   reservations:=ARRAY[]::text[]; runtimes:=ARRAY[]::text[]; nonces:=ARRAY[]::text[];
   last_event:=0; finished:=false; awaiting_pin:=false;
  ELSE
   IF a IS NULL OR finished OR stopped OR (a->>'account_id',a->>'account_generation',a->>'task_id')
   IS DISTINCT FROM (d->>'account_id',d->>'account_generation',d->>'task_id')
   THEN RAISE EXCEPTION 'task_history_refused'; END IF;
   IF r.kind='STOP_INTENT' THEN stopped:=true;
   ELSIF r.kind='SEND_POSSIBLE' THEN
    sent:=sent+1;
    IF sent>4 OR d->>'effect_kind' IS DISTINCT FROM (CASE WHEN sent=2 THEN 'web_search' ELSE 'completion' END)
    OR (sent=4 AND (awaiting_pin OR selected_count IS NULL OR pinned<>selected_count))
    OR d->>'reservation_id'=ANY(reservations) OR d->>'runtime_identity'=ANY(runtimes) OR d->>'dispatch_nonce'=ANY(nonces)
    THEN RAISE EXCEPTION 'task_history_refused'; END IF;
    IF sent=1 THEN first_send:=d;
    ELSIF (d->>'epoch',d->>'lifecycle_id',d->>'financial_scope_id',d->>'metadata_scope_id',d->>'producer_instance_id')
    IS DISTINCT FROM (first_send->>'epoch',first_send->>'lifecycle_id',first_send->>'financial_scope_id',first_send->>'metadata_scope_id',first_send->>'producer_instance_id')
    THEN RAISE EXCEPTION 'task_history_refused'; END IF;
    reservations:=array_append(reservations,d->>'reservation_id');
    runtimes:=array_append(runtimes,d->>'runtime_identity');
    nonces:=array_append(nonces,d->>'dispatch_nonce');
    IF sent=2 THEN search_record:=j->>'record_id'; ELSIF sent=3 THEN selection_record:=j->>'record_id'; END IF;
   ELSE
    IF d->>'epoch' IS DISTINCT FROM first_send->>'epoch' OR d->>'acceptance_record_id' IS DISTINCT FROM a_record
    THEN RAISE EXCEPTION 'task_history_refused'; END IF;
    IF r.kind='SOURCE_READ_POSSIBLE' THEN
     IF sent<>3 OR awaiting_pin OR (d->>'ordinal')::bigint<>pinned+1
     OR d->>'search_record_id' IS DISTINCT FROM search_record OR d->>'selection_record_id' IS DISTINCT FROM selection_record
     OR (selected_count IS NOT NULL AND selected_count<>(d->>'count')::bigint)
     OR d->>'selection_commitment'=ANY(selections)
     THEN RAISE EXCEPTION 'task_history_refused'; END IF;
     selected_count:=(d->>'count')::bigint; possible_record:=j->>'record_id';
     selections:=array_append(selections,d->>'selection_commitment'); awaiting_pin:=true;
    ELSIF r.kind='SOURCE_PIN_PREPARE' THEN
     IF sent<>3 OR NOT awaiting_pin OR (d->>'ordinal')::bigint<>pinned+1
     OR d->>'possible_record_id' IS DISTINCT FROM possible_record OR d->>'snapshot_id'=ANY(snapshots)
     OR (d->>'event_seq')::bigint<=last_event THEN RAISE EXCEPTION 'task_history_refused'; END IF;
     last_event:=(d->>'event_seq')::bigint; pinned:=pinned+1;
     snapshots:=array_append(snapshots,d->>'snapshot_id'); awaiting_pin:=false;
    ELSIF r.kind='TERMINAL_OUTPUT_PREPARE' THEN
     IF sent<>4 OR d->>'result_message_id' IS DISTINCT FROM a->>'result_message_id'
     THEN RAISE EXCEPTION 'task_history_refused'; END IF;
     finished:=true;
    ELSE RAISE EXCEPTION 'task_history_refused'; END IF;
   END IF;
  END IF;
  IF r.seq>after_seq AND r.seq<=after_seq+32 THEN
   RETURN QUERY SELECT r.history_id,r.seq,r.record_id,r.digest,r.frame;
  END IF;
 END LOOP;
END $$;

CREATE FUNCTION public.task_history_source_project(p_frame bytea)
RETURNS TABLE(history_id uuid,seq bigint,record_id uuid,digest bytea,frame bytea,created bool)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j jsonb; d jsonb; b public.task_history_binding; a public.task_history_acceptance;
 t public.tasks; account public.entitlement_accounts; s jsonb; identity jsonb; previous public.task_history_projection;
 top_xid numeric; native_xid xid; native_rows int:=0; who oid; writers oid[];
BEGIN
 PERFORM public.task_history_check_caller(false);
 SELECT oid INTO who FROM pg_roles WHERE rolname=SESSION_USER;
 SELECT array_agg(x.grantee ORDER BY x.grantee) INTO writers FROM pg_proc f
 CROSS JOIN LATERAL aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_source_project(bytea)'::regprocedure AND x.grantee<>f.proowner;
 IF cardinality(writers) IS DISTINCT FROM 1 OR who IS DISTINCT FROM writers[1]
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 j:=public.task_history_decode_source(p_frame); d:=j->'data'; SELECT * INTO b FROM public.task_history_binding;
 PERFORM * FROM public.task_history_source_page(b.history_id,0,b.key_check);
 SELECT * INTO a FROM public.task_history_acceptance x WHERE x.history_id=b.history_id AND x.record_id=(d->>'acceptance_record_id')::uuid;
 SELECT * INTO previous FROM public.task_history_projection x WHERE x.history_id=b.history_id AND x.seq=(j->>'seq')::bigint-1;
 IF b.history_id IS DISTINCT FROM (j->>'history_id')::uuid OR a.task_id IS NULL
 OR (a.account_id,a.account_generation,a.task_id) IS DISTINCT FROM ((d->>'account_id')::uuid,(d->>'account_generation')::bigint,(d->>'task_id')::uuid)
 OR previous.digest IS NULL OR encode(previous.digest,'hex') IS DISTINCT FROM j->>'prev_digest'
 OR (SELECT count(*) FROM public.task_history_projection)<>(j->>'seq')::bigint-1
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 SELECT public.task_history_decode_effect(x.frame)->'data' INTO s FROM public.task_history_projection x
 WHERE x.task_id=a.task_id AND x.kind='SEND_POSSIBLE' ORDER BY x.seq DESC LIMIT 1;
 identity:=jsonb_build_object('account_id',s->'account_id','generation',s->'account_generation','task_id',s->'task_id',
 'epoch',s->'epoch','lifecycle_id',s->'lifecycle_id','financial_scope_id',s->'financial_scope_id',
 'metadata_scope_id',s->'metadata_scope_id','producer_instance_id',s->'producer_instance_id');
 PERFORM public.attempt_lifecycle_assert_open(identity);
 SELECT * INTO account FROM public.entitlement_accounts WHERE user_id=a.account_id FOR SHARE;
 SELECT * INTO t FROM public.tasks WHERE id=a.task_id FOR UPDATE;
 IF account.user_id IS NULL OR account.status<>'active' OR account.content_generation<>a.account_generation
 OR (t.user_id,t.conversation_id,t.user_message_id,t.result_message_id,t.lease_epoch) IS DISTINCT FROM
 (a.account_id,a.conversation_id,a.user_message_id,a.result_message_id,(d->>'epoch')::bigint)
 OR t.status<>'running' OR t.cancel_requested_at IS NOT NULL OR t.lease_owner IS NULL OR t.lease_expires_at<=clock_timestamp()
 OR EXISTS(SELECT 1 FROM public.task_history_projection x WHERE x.task_id=a.task_id AND x.kind IN ('STOP_INTENT','TERMINAL_OUTPUT_PREPARE'))
 OR (SELECT count(*) FROM public.task_history_projection x WHERE x.task_id=a.task_id AND x.kind='SEND_POSSIBLE')<>3
 OR EXISTS(SELECT 1 FROM public.task_history_projection x
 LEFT JOIN public.entitlement_reservations z ON z.id=(public.task_history_decode_effect(x.frame)->'data'->>'reservation_id')::uuid
 WHERE x.task_id=a.task_id AND x.kind='SEND_POSSIBLE' AND
 (z.status IS DISTINCT FROM 'settled' OR z.overage_microusd<>0 OR z.scope_id IS DISTINCT FROM (s->>'financial_scope_id')::uuid))
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 IF j->>'kind'='SOURCE_PIN_PREPARE' THEN
  top_xid:=pg_current_xact_id()::text::numeric;
  IF current_setting('server_version_num')::int NOT BETWEEN 160000 AND 169999
  OR current_setting('track_counts')<>'on' OR has_parameter_privilege(SESSION_USER,'track_counts','SET')
  OR top_xid>=4294967296
  OR NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_am m ON m.oid=c.relam
  WHERE c.oid='public.web_snapshots'::regclass AND c.relkind='r' AND c.relpersistence='p' AND m.amname='heap'
  AND NOT pg_has_role(SESSION_USER,c.relowner,'MEMBER') AND NOT has_table_privilege(SESSION_USER,c.oid,'TRUNCATE'))
  OR pg_stat_get_xact_tuples_inserted('public.web_snapshots'::regclass)<1
  OR pg_stat_get_xact_tuples_updated('public.web_snapshots'::regclass)<>0
  OR NOT EXISTS(SELECT 1 FROM ONLY public.web_snapshots x WHERE x.id=(d->>'snapshot_id')::uuid AND x.user_id=a.account_id
  AND x.conversation_id=a.conversation_id AND x.expires_at>clock_timestamp() AND x.xmin::text::numeric=top_xid)
  OR t.event_seq<>(d->>'event_seq')::bigint
  THEN RAISE EXCEPTION 'task_history_refused'; END IF;
  FOR native_xid IN SELECT x.xmin FROM public.tasks x WHERE x.id=t.id
  UNION ALL SELECT x.xmin FROM public.task_events x WHERE x.task_id=t.id AND x.seq=t.event_seq AND x.kind='page_refreshed'
  LOOP
   IF native_xid::text::numeric<>top_xid THEN RAISE EXCEPTION 'task_history_refused'; END IF;
   native_rows:=native_rows+1;
  END LOOP;
  IF native_rows<>2 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 END IF;
 INSERT INTO public.task_history_projection(history_id,seq,record_id,digest,frame,kind,account_id,account_generation,task_id)
 VALUES(b.history_id,(j->>'seq')::bigint,(j->>'record_id')::uuid,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,j->>'kind',a.account_id,a.account_generation,a.task_id);
 PERFORM * FROM public.task_history_source_page(b.history_id,0,b.key_check);
 PERFORM public.attempt_lifecycle_assert_open(identity);
 RETURN QUERY SELECT b.history_id,(j->>'seq')::bigint,(j->>'record_id')::uuid,substring(p_frame FROM octet_length(p_frame)-31 FOR 32),p_frame,true;
EXCEPTION WHEN OTHERS THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='task_history_refused';
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
  '''task_history_terminal_project'',''task_history_terminal_page'',''task_history_source_project'',''task_history_source_page'')');
 source:=replace(source,
  '''public.task_history_terminal_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)''::pg_catalog.regprocedure))) THEN',
  '''public.task_history_terminal_page(pg_catalog.uuid,pg_catalog.int8,pg_catalog.text)''::pg_catalog.regprocedure,
  ''public.task_history_source_project(bytea)''::pg_catalog.regprocedure,
  ''public.task_history_source_page(uuid,bigint,text)''::pg_catalog.regprocedure))) THEN');
 source:=replace(source, chr(10)||'END ', $suffix$
 IF (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_source_project(bytea)'::regprocedure AND x.grantee<>f.proowner)
 IS DISTINCT FROM (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_send_project(bytea)'::regprocedure AND x.grantee<>f.proowner)
 OR (SELECT array_agg(x.grantee ORDER BY x.grantee) FROM pg_catalog.pg_proc f
 CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
 WHERE f.oid='public.task_history_source_page(uuid,bigint,text)'::regprocedure AND x.grantee<>f.proowner)
 IS DISTINCT FROM read_grants
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
END $suffix$);
 IF position('task_history_source_project(bytea)' in source)=0
 OR position('task_history_source_page(uuid,bigint,text)' in source)=0
 OR position('IS DISTINCT FROM read_grants'||chr(10)||' THEN RAISE EXCEPTION ''task_history_refused''; END IF;'||chr(10)||'END ' in source)=0
 THEN RAISE EXCEPTION 'task_history_refused'; END IF;
 EXECUTE format('CREATE OR REPLACE FUNCTION public.task_history_check_caller(writing pg_catalog.bool) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS %L',source);
END $install$;

DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
 WHERE n.nspname='public' AND p.proname IN ('task_history_decode_source','task_history_source_project','task_history_source_page')
 AND x.grantee<>p.proowner AND NOT(x.grantee=0 AND x.privilege_type='EXECUTE' AND NOT x.is_grantable))
 THEN RAISE EXCEPTION 'task_history_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN ('task_history_decode_source','task_history_source_project','task_history_source_page') LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
