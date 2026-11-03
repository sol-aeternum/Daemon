-- Source-only P catalog boundary. No role, grant, storage or runtime activation.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE FUNCTION public.resource_output_catalog_principal() RETURNS pg_catalog.void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE who pg_catalog.oid; signature pg_catalog.text; proc_oid pg_catalog.oid;
BEGIN
 -- Reuse 058's exact clean P/login/table/sequence/lifecycle exclusions. The
 -- same P must hold both presentation entrypoints and these catalog entrypoints.
 PERFORM public.resource_output_presentation_principal();
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER;
 FOREACH signature IN ARRAY ARRAY[
 'public.resource_output_catalog_snapshot(pg_catalog.jsonb,pg_catalog.uuid)',
 'public.resource_output_catalog_admit(pg_catalog.jsonb,pg_catalog.uuid)',
 'public.resource_output_catalog_stage(pg_catalog.jsonb,pg_catalog.uuid,pg_catalog.jsonb)'] LOOP
  proc_oid:=signature::pg_catalog.regprocedure;
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   WHERE p.oid=proc_oid AND a.grantee=who AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
  OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   WHERE p.oid=proc_oid AND a.grantee<>p.proowner AND (a.grantee<>who OR a.privilege_type<>'EXECUTE' OR a.is_grantable))
  OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=proc_oid AND pg_has_role(who,p.proowner,'MEMBER'))
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname IN ('resource_output_catalog_principal','resource_output_catalog_scope')
 AND a.grantee<>p.proowner)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND (p.proname LIKE 'artifact_output_%' OR p.proname LIKE 'task_history_%')
 AND has_function_privilege(who,p.oid,'EXECUTE'))
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
END $$;

CREATE FUNCTION public.resource_output_catalog_scope(p pg_catalog.jsonb,ident pg_catalog.uuid) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE t public.tasks; j public.artifact_output_jobs; ns public.artifact_namespaces;
 intent pg_catalog.jsonb; candidate pg_catalog.jsonb; kind pg_catalog.text; publication public.resource_publications;
 version public.resource_versions; refs pg_catalog.jsonb; attempt pg_catalog.uuid; next_ordinal pg_catalog.int8;
BEGIN
 PERFORM public.resource_output_catalog_principal();
 t:=public.resource_output_presentation_scope(p);
 IF ident IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 SELECT to_jsonb(i) INTO intent FROM public.artifact_output_intents i WHERE i.account_id=t.user_id AND i.intent_id=ident FOR UPDATE;
 SELECT to_jsonb(i) INTO candidate FROM public.resource_version_intents i WHERE i.account_id=t.user_id AND i.intent_id=ident FOR UPDATE;
 IF (intent IS NULL)=(candidate IS NULL) THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 IF intent IS NULL THEN intent:=candidate; kind:='append'; ELSE kind:='output'; END IF;
 SELECT * INTO j FROM public.artifact_output_jobs WHERE account_id=t.user_id AND job_id=(intent->>'job_id')::pg_catalog.uuid FOR UPDATE;
 SELECT id INTO attempt FROM public.task_attempts WHERE id=(intent->>'attempt_id')::pg_catalog.uuid AND task_id=t.id AND epoch=(p->>'epoch')::pg_catalog.int8 FOR UPDATE;
 IF j.job_id IS NULL OR j.task_id<>t.id OR j.account_generation<>(p->>'generation')::pg_catalog.int8
 OR (intent->>'task_id')::pg_catalog.uuid<>t.id OR attempt IS NULL
 OR (intent->>'lifecycle_id')::pg_catalog.uuid<>(p->>'lifecycle_id')::pg_catalog.uuid
 OR (intent->>'attempt_epoch')::pg_catalog.int8<>(p->>'epoch')::pg_catalog.int8
 OR COALESCE(intent->>'account_generation',intent->>'generation')::pg_catalog.int8<>(p->>'generation')::pg_catalog.int8
 OR intent->>'representation'<>'generated_file' OR intent->>'mime'<>'text/csv'
 OR (kind='append' AND (intent->>'operation_id')::pg_catalog.uuid<>j.operation_id)
 OR EXISTS(SELECT 1 FROM public.resource_version_intents WHERE kind='output' AND account_id=t.user_id AND job_id=j.job_id AND output_slot=(intent->>'output_slot')::pg_catalog.int4)
 OR EXISTS(SELECT 1 FROM public.artifact_output_intents WHERE kind='append' AND account_id=t.user_id AND job_id=j.job_id AND output_slot=(intent->>'output_slot')::pg_catalog.int4)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 SELECT * INTO ns FROM public.artifact_namespaces WHERE account_id=t.user_id AND generation=j.account_generation FOR SHARE;
 IF ns.namespace IS NULL OR ns.state<>'ready' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 SELECT * INTO publication FROM public.resource_publications WHERE account_id=t.user_id AND intent_id=ident FOR UPDATE;
 IF publication.version_id IS NOT NULL THEN
  SELECT * INTO version FROM public.resource_versions WHERE account_id=t.user_id AND version_id=publication.version_id FOR UPDATE;
  SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.reference_id),'[]'::pg_catalog.jsonb) INTO refs
  FROM (SELECT * FROM public.resource_references WHERE account_id=t.user_id AND version_id=publication.version_id AND role='output' ORDER BY reference_id LIMIT 2 FOR UPDATE) r;
  IF version.version_id IS NULL OR publication.resource_id<>(intent->>'resource_id')::pg_catalog.uuid
  OR publication.version_id<>(intent->>'version_id')::pg_catalog.uuid OR publication.generation<>j.account_generation
  OR publication.namespace<>ns.namespace OR publication.source_kind<>kind OR publication.representation<>'generated_file'
  OR publication.measured_bytes<>(intent->>'measured_bytes')::pg_catalog.int8
  OR version.representation<>'generated_file' OR version.kind<>'file' OR version.mime<>'text/csv'
  OR jsonb_array_length(refs)<>1 OR (refs->0->>'job_id') IS DISTINCT FROM j.job_id::pg_catalog.text
  OR (refs->0->>'task_id') IS DISTINCT FROM t.id::pg_catalog.text
  OR (refs->0->>'message_id') IS DISTINCT FROM t.result_message_id::pg_catalog.text
  OR (refs->0->>'conversation_id') IS DISTINCT FROM t.conversation_id::pg_catalog.text
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 END IF;
 SELECT COALESCE(max(r.ordinal)::pg_catalog.int8,-1)+1 INTO next_ordinal FROM public.resource_references r
 WHERE r.account_id=t.user_id AND r.task_id=t.id AND r.role='output';
 RETURN jsonb_build_object('next_output_ordinal',next_ordinal,'task',jsonb_build_object('id',t.id,'user_id',t.user_id,'conversation_id',t.conversation_id,
 'result_message_id',t.result_message_id,'input_version',t.input_version,'input_ciphertext',t.input_ciphertext),
 'job',to_jsonb(j),'intent',intent,'source_kind',kind,'namespace',to_jsonb(ns),
 'publication',CASE WHEN publication.version_id IS NULL THEN NULL ELSE to_jsonb(publication) END,
 'version',CASE WHEN version.version_id IS NULL THEN NULL ELSE to_jsonb(version) END,'output_references',COALESCE(refs,'[]'::pg_catalog.jsonb));
END $$;

CREATE FUNCTION public.resource_output_catalog_snapshot(p pg_catalog.jsonb,ident pg_catalog.uuid) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
BEGIN
 -- Bounded original-operation observation, never new effect/dispatch authority.
 RETURN public.resource_output_catalog_scope(p,ident);
END $$;

CREATE FUNCTION public.resource_output_catalog_admit(p pg_catalog.jsonb,ident pg_catalog.uuid) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE observed pg_catalog.jsonb;
BEGIN
 observed:=public.resource_output_catalog_scope(p,ident);
 PERFORM public.attempt_lifecycle_assert_open(p);
 RETURN observed; -- No transferable permit; caller retains this transaction.
END $$;

CREATE FUNCTION public.resource_output_catalog_stage(p pg_catalog.jsonb,ident pg_catalog.uuid,packet pg_catalog.jsonb) RETURNS pg_catalog.jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE observed pg_catalog.jsonb; intent pg_catalog.jsonb; publication pg_catalog.jsonb; ref pg_catalog.jsonb;
 field_name pg_catalog.text; reference pg_catalog.uuid; account pg_catalog.uuid; resource pg_catalog.uuid; version pg_catalog.uuid;
 expected_generation pg_catalog.int8; counter pg_catalog.int8; ordinal pg_catalog.int8; created pg_catalog.timestamptz; existing public.resources;
BEGIN
 IF packet IS NULL OR jsonb_typeof(packet)<>'object' OR octet_length(packet::pg_catalog.text)>400000
 OR packet<>jsonb_build_object('reference_id',packet->'reference_id','metadata_ciphertext',packet->'metadata_ciphertext',
 'binding_ciphertext',packet->'binding_ciphertext','provenance_ciphertext',packet->'provenance_ciphertext')
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 reference:=(packet->>'reference_id')::pg_catalog.uuid;
 IF reference IS NULL OR packet->>'reference_id'<>reference::pg_catalog.text
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 FOREACH field_name IN ARRAY ARRAY['metadata_ciphertext','binding_ciphertext','provenance_ciphertext'] LOOP
  IF jsonb_typeof(packet->field_name)<>'string' OR packet->>field_name IS NULL OR octet_length(packet->>field_name) NOT BETWEEN 1 AND 131072
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 END LOOP;
 observed:=public.resource_output_catalog_scope(p,ident);
 intent:=observed->'intent'; publication:=observed->'publication'; account:=(p->>'account_id')::pg_catalog.uuid;
 resource:=(intent->>'resource_id')::pg_catalog.uuid; version:=(intent->>'version_id')::pg_catalog.uuid;
 expected_generation:=(p->>'generation')::pg_catalog.int8;
 IF publication<>'null'::pg_catalog.jsonb THEN
  ref:=observed->'output_references'->0;
  IF observed->'version'->>'metadata_ciphertext'<>packet->>'metadata_ciphertext'
  OR publication->>'binding_ciphertext'<>packet->>'binding_ciphertext'
  OR ref->>'provenance_ciphertext'<>packet->>'provenance_ciphertext' OR ref->>'reference_id'<>reference::pg_catalog.text
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
  RETURN jsonb_build_object('version_id',version,'reference_id',reference,'created_at',observed->'version'->'created_at','inserted',false,'outcome','pending_commit');
 END IF;
 PERFORM public.attempt_lifecycle_assert_open(p);
 SELECT * INTO existing FROM public.resources WHERE account_id=account AND resource_id=resource FOR UPDATE;
 IF (observed->>'source_kind'='append')<>(existing.resource_id IS NOT NULL)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 INSERT INTO public.resource_catalog_heads(account_id,generation) VALUES(account,expected_generation) ON CONFLICT DO NOTHING;
 SELECT insertion_counter INTO counter FROM public.resource_catalog_heads WHERE account_id=account AND resource_catalog_heads.generation=expected_generation FOR UPDATE;
 IF counter IS NULL OR counter>=9223372036854775807 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 counter:=counter+1;
 ordinal:=(observed->>'next_output_ordinal')::pg_catalog.int8;
 IF ordinal>2147483647 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused'; END IF;
 IF observed->>'source_kind'='output' THEN
  INSERT INTO public.resources(resource_id,account_id,insertion_ordinal) VALUES(resource,account,counter);
 END IF;
 INSERT INTO public.resource_versions(version_id,account_id,resource_id,generation,representation,kind,mime,measured_bytes,metadata_ciphertext,insertion_ordinal)
 VALUES(version,account,resource,expected_generation,'generated_file','file','text/csv',(intent->>'measured_bytes')::pg_catalog.int8,packet->>'metadata_ciphertext',counter) RETURNING created_at INTO created;
 INSERT INTO public.resource_publications(version_id,account_id,resource_id,generation,namespace,intent_id,source_kind,representation,measured_bytes,binding_ciphertext,insertion_ordinal)
 VALUES(version,account,resource,expected_generation,observed->'namespace'->>'namespace',ident,observed->>'source_kind','generated_file',(intent->>'measured_bytes')::pg_catalog.int8,packet->>'binding_ciphertext',counter);
 INSERT INTO public.resource_references(reference_id,account_id,resource_id,version_id,generation,role,ordinal,task_id,conversation_id,message_id,job_id,provenance_ciphertext)
 VALUES(reference,account,resource,version,expected_generation,'output',ordinal,(p->>'task_id')::pg_catalog.uuid,
 (observed->'task'->>'conversation_id')::pg_catalog.uuid,(observed->'task'->>'result_message_id')::pg_catalog.uuid,
 (intent->>'job_id')::pg_catalog.uuid,packet->>'provenance_ciphertext');
 UPDATE public.resource_catalog_heads SET insertion_counter=counter WHERE account_id=account;
 PERFORM public.attempt_lifecycle_assert_open(p); -- Includes all insertion/FK waits.
 RETURN jsonb_build_object('version_id',version,'reference_id',reference,'created_at',created,'inserted',true,'outcome','pending_commit');
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR unique_violation THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='resource_output_refused';
END $$;

DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname LIKE 'resource_output_catalog_%' AND a.grantee<>p.proowner
 AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable))
 THEN RAISE EXCEPTION 'resource_output_unsafe_default_privileges'; END IF;
 FOR f IN SELECT p.oid::pg_catalog.regprocedure AS signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname LIKE 'resource_output_catalog_%' LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
