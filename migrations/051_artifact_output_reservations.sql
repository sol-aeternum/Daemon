-- R0: uncalled account-content reservations, NOT storage/publication authority.
-- Caller/migration runner owns the transaction. No runtime roles or grants.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE TABLE public.artifact_output_jobs (
 job_id uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 account_generation bigint NOT NULL CHECK (account_generation >= 0),
 task_id uuid NOT NULL,
 operation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE (account_id,task_id,operation_id),
 UNIQUE (account_id,job_id,account_generation,task_id)
);
CREATE TABLE public.artifact_output_intents (
 intent_id uuid PRIMARY KEY,
 account_id uuid NOT NULL,
 job_id uuid NOT NULL,
 account_generation bigint NOT NULL CHECK (account_generation >= 0),
 task_id uuid NOT NULL,
 output_slot integer NOT NULL CHECK (output_slot >= 0),
 resource_id uuid NOT NULL UNIQUE,
 version_id uuid NOT NULL UNIQUE,
 lifecycle_id uuid NOT NULL,
 attempt_id uuid NOT NULL,
 attempt_epoch bigint NOT NULL CHECK (attempt_epoch > 0),
 representation text NOT NULL CHECK (representation = 'generated_file'),
 mime text NOT NULL CHECK (mime = 'text/csv'),
 measured_bytes bigint NOT NULL CHECK (measured_bytes BETWEEN 0 AND 8388608),
 descriptor_ciphertext text NOT NULL CHECK (octet_length(descriptor_ciphertext) BETWEEN 1 AND 131072),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (account_id,job_id,account_generation,task_id) REFERENCES
 public.artifact_output_jobs(account_id,job_id,account_generation,task_id) ON DELETE CASCADE,
 UNIQUE (account_id,job_id,output_slot)
);
COMMENT ON TABLE public.artifact_output_jobs IS 'Account content; historical task identity survives chat deletion. No live task link, namespace or publication authority.';
COMMENT ON TABLE public.artifact_output_intents IS 'Immutable reservation through narrow API; encrypted metadata. Account/reset inventory includes this table. No bytes, admission, availability or publication receipt.';

-- Closed typed wire objects; creation timestamps are assigned only by the DB.
CREATE FUNCTION public.artifact_output_job_arg(j jsonb) RETURNS public.artifact_output_jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r public.artifact_output_jobs;
BEGIN
 IF j IS NULL OR jsonb_typeof(j)<>'object' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 r:=jsonb_populate_record(NULL::public.artifact_output_jobs,j);
 IF j IS DISTINCT FROM (to_jsonb(r)-'created_at') OR r.job_id IS NULL OR r.account_id IS NULL
 OR r.account_generation IS NULL OR r.account_generation<0 OR r.task_id IS NULL OR r.operation_id IS NULL
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 RETURN r;
EXCEPTION WHEN data_exception THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused';
END $$;

CREATE FUNCTION public.artifact_output_intent_arg(c jsonb) RETURNS public.artifact_output_intents
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r public.artifact_output_intents;
BEGIN
 IF c IS NULL OR jsonb_typeof(c)<>'object' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 r:=jsonb_populate_record(NULL::public.artifact_output_intents,c);
 IF c IS DISTINCT FROM (to_jsonb(r)-'created_at')
 OR EXISTS(SELECT 1 FROM jsonb_each(c) e WHERE e.value='null'::jsonb)
 OR r.account_generation<0 OR r.output_slot<0 OR r.attempt_epoch<1
 OR r.representation<>'generated_file' OR r.mime<>'text/csv'
 OR r.measured_bytes NOT BETWEEN 0 AND 8388608
 OR octet_length(r.descriptor_ciphertext) NOT BETWEEN 1 AND 131072
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 RETURN r;
EXCEPTION WHEN data_exception THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused';
END $$;

-- Readback takes no task/lifecycle locks; history is not live authority.
CREATE FUNCTION public.artifact_output_account_lock(a uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
BEGIN
 IF a IS NULL OR current_setting('transaction_isolation')<>'read committed' THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('daemon:account-control:v1','UTF8')||decode('00','hex')||uuid_send(a)),'hex'),1,16))::bit(64)::bigint);
 PERFORM 1 FROM public.entitlement_accounts WHERE user_id=a FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_unavailable'; END IF;
END $$;

CREATE FUNCTION public.artifact_output_readback(j jsonb,c jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE expected public.artifact_output_jobs; wanted public.artifact_output_intents;
 actual public.artifact_output_jobs; candidate public.artifact_output_intents;
BEGIN
 expected:=public.artifact_output_job_arg(j);
 IF c IS NOT NULL THEN
  wanted:=public.artifact_output_intent_arg(c);
  IF (wanted.account_id,wanted.job_id,wanted.account_generation,wanted.task_id)
  IS DISTINCT FROM (expected.account_id,expected.job_id,expected.account_generation,expected.task_id)
  THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_conflict'; END IF;
 END IF;
 PERFORM public.artifact_output_account_lock(expected.account_id);
 -- Stable key and global ID collisions both refuse; no foreign row is returned.
 FOR actual IN SELECT * FROM public.artifact_output_jobs WHERE job_id=expected.job_id
 OR (account_id=expected.account_id AND task_id=expected.task_id AND operation_id=expected.operation_id)
 ORDER BY job_id FOR UPDATE LOOP
  IF (to_jsonb(actual)-'created_at') IS DISTINCT FROM j THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_conflict'; END IF;
 END LOOP;
 IF c IS NOT NULL THEN
  FOR candidate IN SELECT * FROM public.artifact_output_intents WHERE intent_id=wanted.intent_id
  OR resource_id=wanted.resource_id OR version_id=wanted.version_id
  OR (account_id=wanted.account_id AND job_id=wanted.job_id AND output_slot=wanted.output_slot)
  ORDER BY intent_id FOR UPDATE LOOP
   IF (to_jsonb(candidate)-'created_at') IS DISTINCT FROM c THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_conflict'; END IF;
  END LOOP;
 END IF;
 IF candidate.intent_id IS NOT NULL AND actual.job_id IS NULL THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_unavailable'; END IF;
 RETURN jsonb_build_object('outcome',CASE WHEN actual.job_id IS NULL THEN 'absent'
 WHEN c IS NOT NULL AND candidate.intent_id IS NULL THEN 'job_present_intent_absent' ELSE 'present' END,
 'job',CASE WHEN actual.job_id IS NULL THEN NULL ELSE to_jsonb(actual) END,
 'intent',CASE WHEN candidate.intent_id IS NULL THEN NULL ELSE to_jsonb(candidate) END);
END $$;

CREATE FUNCTION public.artifact_output_register(p jsonb,j jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE expected public.artifact_output_jobs; result jsonb; inserted boolean:=false;
BEGIN
 expected:=public.artifact_output_job_arg(j);
 PERFORM public.attempt_lifecycle_assert_open(p);
 IF (expected.account_id,expected.account_generation,expected.task_id) IS DISTINCT FROM
 ((p->>'account_id')::uuid,(p->>'generation')::bigint,(p->>'task_id')::uuid)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 result:=public.artifact_output_readback(j,NULL);
 -- Final post-wait lease check, not transaction-start now(). Task remains locked.
 IF NOT EXISTS(SELECT 1 FROM public.tasks WHERE id=expected.task_id AND lease_expires_at>clock_timestamp()) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 IF result->>'outcome'='absent' THEN
  INSERT INTO public.artifact_output_jobs(job_id,account_id,account_generation,task_id,operation_id)
  VALUES(expected.job_id,expected.account_id,expected.account_generation,expected.task_id,expected.operation_id);
  inserted:=true;
 END IF;
 -- A global unique key can wait on another account's invisible INSERT, even
 -- though our account/task locks are held. Refuse atomically after that wait.
 IF NOT EXISTS(SELECT 1 FROM public.tasks WHERE id=expected.task_id AND lease_expires_at>clock_timestamp()) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 RETURN public.artifact_output_readback(j,NULL)||jsonb_build_object('outcome','pending_commit','inserted',inserted);
EXCEPTION WHEN integrity_constraint_violation OR data_exception THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_conflict';
END $$;

CREATE FUNCTION public.artifact_output_reserve(p jsonb,j jsonb,c jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE wanted public.artifact_output_intents; result jsonb; inserted boolean:=false;
BEGIN
 wanted:=public.artifact_output_intent_arg(c);
 PERFORM public.attempt_lifecycle_assert_open(p);
 IF (wanted.account_id,wanted.account_generation,wanted.task_id,wanted.lifecycle_id,wanted.attempt_epoch)
 IS DISTINCT FROM ((p->>'account_id')::uuid,(p->>'generation')::bigint,(p->>'task_id')::uuid,
 (p->>'lifecycle_id')::uuid,(p->>'epoch')::bigint)
 OR NOT EXISTS(SELECT 1 FROM public.task_attempts WHERE id=wanted.attempt_id
 AND task_id=wanted.task_id AND epoch=wanted.attempt_epoch)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 result:=public.artifact_output_readback(j,c);
 IF result->>'outcome'='absent' THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.tasks WHERE id=wanted.task_id AND lease_expires_at>clock_timestamp()) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 IF result->>'outcome'='job_present_intent_absent' THEN
  INSERT INTO public.artifact_output_intents(intent_id,account_id,job_id,account_generation,task_id,
  output_slot,resource_id,version_id,lifecycle_id,attempt_id,attempt_epoch,representation,mime,measured_bytes,descriptor_ciphertext)
  VALUES(wanted.intent_id,wanted.account_id,wanted.job_id,wanted.account_generation,wanted.task_id,
  wanted.output_slot,wanted.resource_id,wanted.version_id,wanted.lifecycle_id,wanted.attempt_id,
  wanted.attempt_epoch,wanted.representation,wanted.mime,wanted.measured_bytes,wanted.descriptor_ciphertext);
  inserted:=true;
 END IF;
 -- Includes invisible cross-account intent/resource/version uniqueness waits.
 IF NOT EXISTS(SELECT 1 FROM public.tasks WHERE id=wanted.task_id AND lease_expires_at>clock_timestamp()) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_refused'; END IF;
 RETURN public.artifact_output_readback(j,c)||jsonb_build_object('outcome','pending_commit','inserted',inserted);
EXCEPTION WHEN integrity_constraint_violation OR data_exception THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='reservation_conflict';
END $$;

-- Reject inherited defaults, revoke intrinsic PUBLIC EXECUTE in this transaction.
DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 WHERE c.oid IN ('public.artifact_output_jobs'::regclass,'public.artifact_output_intents'::regclass) AND a.grantee<>c.relowner)
 OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname LIKE 'artifact_output_%' AND a.grantee<>p.proowner
 AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)) THEN
 RAISE EXCEPTION 'reservation_unsafe_default_privileges'; END IF;
 REVOKE ALL ON public.artifact_output_jobs,public.artifact_output_intents FROM PUBLIC;
 FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname LIKE 'artifact_output_%' LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
