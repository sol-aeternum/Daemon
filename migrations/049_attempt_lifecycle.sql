-- Uncalled source-only prerequisite. Migration runner supplies the transaction.
-- No production roles/grants, runtime callers, retention policy or restore proof.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';
CREATE TABLE public.attempt_lifecycle_control (
 id uuid PRIMARY KEY,
 identity jsonb NOT NULL,
 account_id uuid NOT NULL,
 task_id uuid NOT NULL,
 epoch bigint NOT NULL CHECK (epoch > 0),
 financial_scope_id uuid NOT NULL UNIQUE,
 metadata_scope_id uuid NOT NULL UNIQUE,
 attempt_started_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 revision bigint NOT NULL DEFAULT 1 CHECK (revision IN (1,2)),
 closed_at timestamptz,
 CHECK ((closed_at IS NULL) = (revision = 1)),
 UNIQUE (task_id,epoch)
);
CREATE TABLE public.attempt_lifecycle_commands (
 command_id uuid PRIMARY KEY,
 lifecycle_id uuid NOT NULL REFERENCES public.attempt_lifecycle_control(id),
 opcode text NOT NULL CHECK (opcode IN ('register','close')),
 reason text NOT NULL CHECK (reason IN ('','requested','cancelled','stale')),
 revision bigint NOT NULL CHECK (revision IN (1,2)),
 committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK ((opcode='register' AND reason='' AND revision=1) OR (opcode='close' AND reason<>'' AND revision=2))
);
COMMENT ON TABLE public.attempt_lifecycle_control IS 'Retained account-linked control, not stop/settlement/erasure proof; runtime use blocked on retention/restore qualification.';
COMMENT ON TABLE public.attempt_lifecycle_commands IS 'Immutable command history, never current execution authority.';

CREATE FUNCTION public.attempt_lifecycle_identity(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v jsonb;
BEGIN
 IF p IS NULL OR jsonb_typeof(p)<>'object' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 v:=jsonb_build_object('lifecycle_id',(p->>'lifecycle_id')::uuid,'account_id',(p->>'account_id')::uuid,
 'generation',(p->>'generation')::bigint,'task_id',(p->>'task_id')::uuid,'epoch',(p->>'epoch')::bigint,
 'financial_scope_id',(p->>'financial_scope_id')::uuid,'metadata_scope_id',(p->>'metadata_scope_id')::uuid,
 'producer_instance_id',(p->>'producer_instance_id')::uuid);
 IF p<>v OR v @> '{"lifecycle_id":null}' OR v @> '{"account_id":null}' OR v @> '{"task_id":null}'
 OR v @> '{"financial_scope_id":null}' OR v @> '{"metadata_scope_id":null}' OR v @> '{"producer_instance_id":null}'
 OR (p->>'generation')::bigint IS NULL OR (p->>'generation')::bigint<0
 OR (p->>'epoch')::bigint IS NULL OR (p->>'epoch')::bigint<1
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 RETURN v;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused';
END $$;

-- Shared serialization survives missing account rows. Exactly the Python
-- account_control.store.account_lock_key SHA256 domain and signed bigint.
CREATE FUNCTION public.attempt_lifecycle_lock(p jsonb) RETURNS public.attempt_lifecycle_control
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE c public.attempt_lifecycle_control; a uuid;
BEGIN
 PERFORM public.attempt_lifecycle_identity(p);
 IF current_setting('transaction_isolation')<>'read committed' THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 a:=(p->>'account_id')::uuid;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('daemon:account-control:v1','UTF8')||decode('00','hex')||uuid_send(a)),'hex'),1,16))::bit(64)::bigint);
 PERFORM 1 FROM public.entitlement_accounts WHERE user_id=a FOR UPDATE;
 PERFORM 1 FROM public.account_deletions WHERE account_id=a AND closed_at IS NULL ORDER BY id FOR UPDATE;
 SELECT * INTO c FROM public.attempt_lifecycle_control WHERE id=(p->>'lifecycle_id')::uuid FOR UPDATE;
 IF c.id IS NOT NULL AND c.identity<>p THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 RETURN c;
END $$;

-- Always called AFTER account/operation/control locks. Separate statements after
-- waits are essential at READ COMMITTED. No locks on conversations or financial rows.
CREATE FUNCTION public.attempt_lifecycle_live(p jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE t public.tasks; a public.task_attempts; s public.compute_scope_metadata;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.entitlement_accounts WHERE user_id=(p->>'account_id')::uuid
 AND status='active' AND content_generation=(p->>'generation')::bigint)
 OR EXISTS(SELECT 1 FROM public.account_deletions WHERE account_id=(p->>'account_id')::uuid AND closed_at IS NULL)
 OR EXISTS(SELECT 1 FROM public.account_suspension_causes WHERE account_id=(p->>'account_id')::uuid)
 THEN RETURN false; END IF;
 SELECT * INTO t FROM public.tasks WHERE id=(p->>'task_id')::uuid FOR UPDATE;
 IF t.id IS NULL OR t.user_id<>(p->>'account_id')::uuid OR t.status<>'running'
 OR t.lease_epoch<>(p->>'epoch')::bigint OR t.cancel_requested_at IS NOT NULL THEN RETURN false; END IF;
 -- Recheck lease AFTER acquiring the row lock, never against a pre-wait snapshot.
 IF NOT EXISTS(SELECT 1 FROM public.tasks WHERE id=t.id AND lease_expires_at>clock_timestamp()) THEN RETURN false; END IF;
 SELECT * INTO a FROM public.task_attempts WHERE task_id=t.id AND epoch=t.lease_epoch FOR UPDATE;
 IF a.id IS NULL OR a.outcome<>'running' OR a.ended_at IS NOT NULL OR a.worker_id<>t.lease_owner
 OR a.compute_scope_id IS DISTINCT FROM (p->>'financial_scope_id')::uuid THEN RETURN false; END IF;
 SELECT * INTO s FROM public.compute_scope_metadata WHERE id=(p->>'metadata_scope_id')::uuid FOR UPDATE;
 -- Metadata/attempt waits can also consume the lease; recheck at the end.
 RETURN s.id IS NOT NULL AND s.account_id=(p->>'account_id')::uuid
 AND EXISTS(SELECT 1 FROM public.tasks WHERE id=t.id AND lease_expires_at>clock_timestamp());
END $$;

CREATE FUNCTION public.attempt_lifecycle_readback(p jsonb,cmd uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE c public.attempt_lifecycle_control; r public.attempt_lifecycle_commands; present boolean; live boolean;
BEGIN
 IF cmd IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 c:=public.attempt_lifecycle_lock(p);
 present:=EXISTS(SELECT 1 FROM public.entitlement_accounts WHERE user_id=(p->>'account_id')::uuid);
 -- Missing account + missing binding refuses: do not report command absence.
 -- account_present is an explicit observation for the trusted internal principal,
 -- NOT a privacy/authenticated-ownership boundary or an account-facing lookup.
 IF c.id IS NULL AND NOT present THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 live:=public.attempt_lifecycle_live(p);
 SELECT * INTO r FROM public.attempt_lifecycle_commands WHERE command_id=cmd;
 IF r.command_id IS NOT NULL AND (c.id IS NULL OR r.lifecycle_id<>c.id) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 RETURN jsonb_build_object('control',CASE WHEN c.id IS NULL THEN NULL ELSE to_jsonb(c) END,
 'receipt',CASE WHEN r.command_id IS NULL THEN NULL ELSE to_jsonb(r) END,
 'account_present',present,'references_current',live);
END $$;

CREATE FUNCTION public.attempt_lifecycle_register(p jsonb,cmd uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE c public.attempt_lifecycle_control; r public.attempt_lifecycle_commands; anchor timestamptz;
BEGIN
 IF cmd IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 c:=public.attempt_lifecycle_lock(p);
 -- Receipt replay is HISTORY, including after close or reference removal.
 SELECT * INTO r FROM public.attempt_lifecycle_commands WHERE command_id=cmd;
 IF r.command_id IS NOT NULL THEN
 IF c.id IS NULL OR r.lifecycle_id<>c.id OR r.opcode<>'register' THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 RETURN public.attempt_lifecycle_readback(p,cmd);
 END IF;
 IF c.closed_at IS NOT NULL OR NOT public.attempt_lifecycle_live(p) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 SELECT started_at INTO anchor FROM public.task_attempts WHERE task_id=(p->>'task_id')::uuid AND epoch=(p->>'epoch')::bigint;
 IF c.id IS NULL THEN
 INSERT INTO public.attempt_lifecycle_control(id,identity,account_id,task_id,epoch,financial_scope_id,metadata_scope_id,attempt_started_at)
 VALUES((p->>'lifecycle_id')::uuid,p,(p->>'account_id')::uuid,(p->>'task_id')::uuid,(p->>'epoch')::bigint,
 (p->>'financial_scope_id')::uuid,(p->>'metadata_scope_id')::uuid,anchor);
 END IF;
 INSERT INTO public.attempt_lifecycle_commands(command_id,lifecycle_id,opcode,reason,revision)
 VALUES(cmd,(p->>'lifecycle_id')::uuid,'register','',1);
 RETURN public.attempt_lifecycle_readback(p,cmd);
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused';
END $$;

CREATE FUNCTION public.attempt_lifecycle_assert_open(p jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE c public.attempt_lifecycle_control;
BEGIN
 c:=public.attempt_lifecycle_lock(p);
 IF c.id IS NULL OR c.closed_at IS NOT NULL OR NOT public.attempt_lifecycle_live(p) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 -- No token. Locks last only through this caller transaction/savepoint lifetime.
END $$;

CREATE FUNCTION public.attempt_lifecycle_close(p jsonb,cmd uuid,why text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE c public.attempt_lifecycle_control; r public.attempt_lifecycle_commands;
BEGIN
 IF cmd IS NULL OR why IS NULL OR why NOT IN ('requested','cancelled','stale') THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 c:=public.attempt_lifecycle_lock(p);
 IF c.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 -- Match original command arguments before returning any history.
 SELECT * INTO r FROM public.attempt_lifecycle_commands WHERE command_id=cmd;
 IF r.command_id IS NOT NULL THEN
 IF r.lifecycle_id<>c.id OR r.opcode<>'close' OR r.reason<>why THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused'; END IF;
 ELSE
 UPDATE public.attempt_lifecycle_control SET closed_at=clock_timestamp(),revision=2 WHERE id=c.id AND closed_at IS NULL;
 INSERT INTO public.attempt_lifecycle_commands(command_id,lifecycle_id,opcode,reason,revision) VALUES(cmd,c.id,'close',why,2);
 END IF;
 RETURN public.attempt_lifecycle_readback(p,cmd);
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='lifecycle_refused';
END $$;

-- Reject inherited named-role defaults; revoke intrinsic PUBLIC EXECUTE atomically.
DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 WHERE c.oid IN ('public.attempt_lifecycle_control'::regclass,'public.attempt_lifecycle_commands'::regclass) AND a.grantee<>c.relowner)
 OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname LIKE 'attempt_lifecycle_%' AND a.grantee<>p.proowner
 AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)) THEN
 RAISE EXCEPTION 'lifecycle_unsafe_default_privileges'; END IF;
 REVOKE ALL ON public.attempt_lifecycle_control,public.attempt_lifecycle_commands FROM PUBLIC;
 FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname LIKE 'attempt_lifecycle_%' LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
