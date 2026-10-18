-- Source/fixture-only. No production composition, role creation or runtime grants.
-- The existing migration runner owns this transaction.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

CREATE TABLE public.task_inference_recovery (
 task_id uuid PRIMARY KEY REFERENCES public.tasks(id) ON DELETE CASCADE,
 account_id uuid NOT NULL,
 account_generation bigint NOT NULL CHECK (account_generation >= 0),
 protocol_version smallint NOT NULL DEFAULT 1 CHECK (protocol_version = 1),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 may_have_dispatched boolean NOT NULL DEFAULT false,
 first_lifecycle_id uuid,
 first_reservation_id uuid,
 first_runtime_identity uuid,
 first_dispatch_nonce uuid,
 first_marked_at timestamptz,
 CHECK ((NOT may_have_dispatched AND first_lifecycle_id IS NULL
 AND first_reservation_id IS NULL AND first_runtime_identity IS NULL
 AND first_dispatch_nonce IS NULL AND first_marked_at IS NULL)
 OR (may_have_dispatched AND first_lifecycle_id IS NOT NULL
 AND first_reservation_id IS NOT NULL AND first_runtime_identity IS NOT NULL
 AND first_dispatch_nonce IS NOT NULL AND first_marked_at IS NOT NULL))
);
COMMENT ON TABLE public.task_inference_recovery IS
 'Possible dispatch, never remote stop or billing cessation. NEW acceptance only; no legacy backfill or release.';
CREATE INDEX attempt_lifecycle_open_page ON public.attempt_lifecycle_control(id)
 WHERE closed_at IS NULL;

CREATE FUNCTION public.task_inference_recovery_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF (NEW.task_id,NEW.account_id,NEW.account_generation,NEW.protocol_version,NEW.created_at)
 IS DISTINCT FROM (OLD.task_id,OLD.account_id,OLD.account_generation,OLD.protocol_version,OLD.created_at)
 OR (OLD.may_have_dispatched AND NEW IS DISTINCT FROM OLD)
 OR (NOT OLD.may_have_dispatched AND NOT NEW.may_have_dispatched AND NEW IS DISTINCT FROM OLD)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task_inference_recovery_immutable BEFORE UPDATE ON public.task_inference_recovery
 FOR EACH ROW EXECUTE FUNCTION public.task_inference_recovery_immutable();

CREATE FUNCTION public.task_inference_dispatch_begin(p jsonb,rid uuid,runtime uuid,nonce uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE c public.task_inference_recovery; r public.entitlement_reservations;
BEGIN
 IF rid IS NULL OR runtime IS NULL OR nonce IS NULL THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 -- 049: account -> operation -> lifecycle -> task/attempt -> metadata scope.
 PERFORM public.attempt_lifecycle_assert_open(p);
 SELECT * INTO c FROM public.task_inference_recovery WHERE task_id=(p->>'task_id')::uuid FOR UPDATE;
 IF c.task_id IS NULL OR c.account_id<>(p->>'account_id')::uuid
 OR c.account_generation<>(p->>'generation')::bigint OR c.protocol_version<>1
 OR (c.may_have_dispatched AND c.first_lifecycle_id<>(p->>'lifecycle_id')::uuid)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 SELECT * INTO r FROM public.entitlement_reservations WHERE id=rid FOR UPDATE;
 IF r.id IS NULL OR r.user_id<>(p->>'account_id')::uuid OR r.status<>'open'
 OR r.scope_id IS DISTINCT FROM (p->>'financial_scope_id')::uuid
 OR EXISTS(SELECT 1 FROM public.compute_reservation_metadata WHERE reservation_id=rid)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 PERFORM public.compute_metadata_attach((p->>'account_id')::uuid,(p->>'metadata_scope_id')::uuid,rid,runtime,nonce);
 UPDATE public.task_inference_recovery SET may_have_dispatched=true,
 first_lifecycle_id=(p->>'lifecycle_id')::uuid,first_reservation_id=rid,
 first_runtime_identity=runtime,first_dispatch_nonce=nonce,first_marked_at=clock_timestamp()
 WHERE task_id=c.task_id AND NOT may_have_dispatched;
 -- Every wait above may consume the lease. Caller repeats immediately pre-COMMIT.
 PERFORM public.attempt_lifecycle_assert_open(p);
 RETURN jsonb_build_object('identity',p,'reservation_id',rid,'runtime_identity',runtime,
 'dispatch_nonce',nonce,'created',true);
END $$;

CREATE FUNCTION public.task_inference_dispatch_readback(p jsonb,rid uuid,runtime uuid,nonce uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE l public.attempt_lifecycle_control; c public.task_inference_recovery; b public.compute_reservation_metadata;
 r public.entitlement_reservations;
BEGIN
 IF rid IS NULL OR runtime IS NULL OR nonce IS NULL THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 l:=public.attempt_lifecycle_lock(p);
 IF l.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 SELECT * INTO c FROM public.task_inference_recovery WHERE task_id=l.task_id;
  SELECT * INTO b FROM public.compute_reservation_metadata WHERE reservation_id=rid;
 SELECT * INTO r FROM public.entitlement_reservations WHERE id=rid;
 IF c.task_id IS NULL OR c.account_id<>l.account_id OR c.account_generation<>(p->>'generation')::bigint
 OR NOT c.may_have_dispatched OR c.first_lifecycle_id<>l.id
 OR b.reservation_id IS NULL OR b.metadata_scope_id<>l.metadata_scope_id
 OR b.runtime_identity<>runtime OR b.dispatch_nonce<>nonce
 OR r.id IS NULL OR r.user_id<>l.account_id OR r.scope_id IS DISTINCT FROM l.financial_scope_id
 OR NOT EXISTS(SELECT 1 FROM public.compute_scope_metadata WHERE id=l.metadata_scope_id AND account_id=l.account_id)
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 -- This observation has no created flag/send permit and changes nothing.
 RETURN jsonb_build_object('identity',p,'reservation_id',rid,'runtime_identity',runtime,
 'dispatch_nonce',nonce,'may_have_dispatched',true);
END $$;

CREATE FUNCTION public.task_inference_recovery_lock(a uuid,g bigint,t uuid,e bigint,cmd uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE l public.attempt_lifecycle_control; c public.task_inference_recovery; task public.tasks;
BEGIN
 IF a IS NULL OR g IS NULL OR g<0 OR t IS NULL OR e IS NULL OR e<0 OR cmd IS NULL
 OR current_setting('transaction_isolation')<>'read committed'
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('daemon:account-control:v1','UTF8')||decode('00','hex')||uuid_send(a)),'hex'),1,16))::bit(64)::bigint);
 PERFORM 1 FROM public.entitlement_accounts WHERE user_id=a FOR UPDATE;
 PERFORM 1 FROM public.account_deletions WHERE account_id=a AND closed_at IS NULL ORDER BY id FOR UPDATE;
 SELECT * INTO l FROM public.attempt_lifecycle_control WHERE task_id=t AND epoch=e FOR UPDATE;
 IF l.id IS NOT NULL AND (l.account_id<>a OR (l.identity->>'generation')::bigint<>g) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 IF l.id IS NOT NULL THEN PERFORM public.attempt_lifecycle_identity(l.identity); END IF;
 SELECT * INTO task FROM public.tasks WHERE id=t FOR UPDATE;
 IF task.id IS NULL OR task.user_id<>a OR task.lease_epoch<>e THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 PERFORM 1 FROM public.task_attempts WHERE task_id=t AND epoch=e FOR UPDATE;
 IF l.id IS NOT NULL THEN
 PERFORM 1 FROM public.compute_scope_metadata WHERE id=l.metadata_scope_id FOR UPDATE;
 END IF;
 SELECT * INTO c FROM public.task_inference_recovery WHERE task_id=t FOR UPDATE;
 IF c.task_id IS NULL OR c.account_id<>a OR c.account_generation<>g OR c.protocol_version<>1
 OR (c.may_have_dispatched AND (l.id IS NULL OR c.first_lifecycle_id<>l.id))
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 IF c.may_have_dispatched AND NOT EXISTS(
 SELECT 1 FROM public.compute_reservation_metadata b
 JOIN public.entitlement_reservations r ON r.id=b.reservation_id
 JOIN public.compute_scope_metadata s ON s.id=b.metadata_scope_id
 WHERE b.reservation_id=c.first_reservation_id AND b.runtime_identity=c.first_runtime_identity
 AND b.dispatch_nonce=c.first_dispatch_nonce AND b.metadata_scope_id=l.metadata_scope_id
 AND s.account_id=a AND r.user_id=a AND r.scope_id=l.financial_scope_id) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 -- Observation/locks ONLY. Python must authenticate/revalidate original encrypted
 -- origin/status/lease/refusal before staging any close on this SAME connection.
 RETURN jsonb_build_object('may_have_dispatched',c.may_have_dispatched,
 'identity',CASE WHEN l.id IS NULL THEN NULL ELSE l.identity END,
 'closed',CASE WHEN l.id IS NULL THEN NULL ELSE l.closed_at IS NOT NULL END);
END $$;

CREATE FUNCTION public.attempt_lifecycle_abandoned_page(after_id uuid) RETURNS SETOF jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
 SELECT identity FROM public.attempt_lifecycle_control
 WHERE closed_at IS NULL AND (after_id IS NULL OR id>after_id) ORDER BY id LIMIT 32
$$;

CREATE FUNCTION public.attempt_lifecycle_close_abandoned(p jsonb,cmd uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='250ms' AS $$
DECLARE l public.attempt_lifecycle_control; task public.tasks; attempt public.task_attempts;
BEGIN
 l:=public.attempt_lifecycle_lock(p);
 IF cmd IS NULL OR l.id IS NULL THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 IF l.closed_at IS NOT NULL THEN RETURN jsonb_build_object('identity',p,'closed',true,'changed',false); END IF;
 SELECT * INTO task FROM public.tasks WHERE id=l.task_id FOR UPDATE;
 SELECT * INTO attempt FROM public.task_attempts WHERE task_id=l.task_id AND epoch=l.epoch FOR UPDATE;
 IF task.id IS NOT NULL AND task.user_id<>l.account_id THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 IF task.id IS NOT NULL AND task.status='running' AND task.lease_epoch=l.epoch
 AND EXISTS(SELECT 1 FROM public.tasks WHERE id=l.task_id AND lease_expires_at>clock_timestamp())
 THEN RETURN jsonb_build_object('identity',p,'closed',false,'changed',false); END IF;
 IF task.id IS NOT NULL AND task.status IN ('queued','running') AND task.lease_epoch=l.epoch
 AND task.status<>'running' AND (attempt.id IS NULL OR attempt.ended_at IS NULL) THEN
 RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='inference_recovery_refused'; END IF;
 PERFORM public.attempt_lifecycle_close(p,cmd,'stale');
 RETURN jsonb_build_object('identity',p,'closed',true,'changed',true);
END $$;

DO $$ DECLARE f record; BEGIN
 IF EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 WHERE c.oid='public.task_inference_recovery'::regclass AND a.grantee<>c.relowner)
 OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND (p.proname LIKE 'task_inference_%'
 OR p.proname IN ('attempt_lifecycle_abandoned_page','attempt_lifecycle_close_abandoned'))
 AND a.grantee<>p.proowner AND NOT(a.grantee=0 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable))
 THEN RAISE EXCEPTION 'inference_recovery_unsafe_default_privileges'; END IF;
 REVOKE ALL ON public.task_inference_recovery FROM PUBLIC;
 FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND (p.proname LIKE 'task_inference_%'
 OR p.proname IN ('attempt_lifecycle_abandoned_page','attempt_lifecycle_close_abandoned')) LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END $$;
