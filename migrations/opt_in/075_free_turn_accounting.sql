-- SOURCE ONLY, explicitly selected isolated free-turn accounting qualification.
-- Caller owns the migration transaction. The default migration glob excludes
-- this directory. No service grants, logins, bindings, backfill or activation.
--
-- This schema is an opt-in fixture boundary for accounting-only Free turns. It
-- does not change any production admission/settlement path: every existing
-- entitlement_reservations row has NULL lineage and is left untouched.
SET LOCAL lock_timeout='1s';
SET LOCAL statement_timeout='5s';
DO $$ BEGIN
 IF pg_catalog.to_regclass('public.entitlement_accounts') IS NULL
 OR pg_catalog.to_regclass('public.entitlement_usage_periods') IS NULL
 OR pg_catalog.to_regclass('public.entitlement_reservations') IS NULL
 THEN RAISE EXCEPTION 'free_turn_accounting_base_schema_missing'; END IF;
END $$;

-- One Free-turn root. bound_microusd (B) is permission, not money;
-- funded_microusd (F) is the unused escrow already held in the same UTC usage
-- period's reserved_microusd. scope_id is the single stable foreground scope
-- shared by every call under this root.
CREATE TABLE public.turn_budget_allocations (
    user_id uuid NOT NULL
        REFERENCES public.entitlement_accounts(user_id) ON DELETE CASCADE,
    turn_root_id uuid NOT NULL DEFAULT pg_catalog.gen_random_uuid(),
    period_key text NOT NULL CHECK (period_key ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    scope_id uuid NOT NULL DEFAULT pg_catalog.gen_random_uuid(),
    bound_microusd bigint NOT NULL CHECK (bound_microusd >= 0),
    funded_microusd bigint NOT NULL CHECK (funded_microusd BETWEEN 0 AND bound_microusd),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'sealed')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
    sealed_at timestamptz,
    PRIMARY KEY (user_id, turn_root_id),
    -- Owner + UTC period integrity against the real usage-period row. This is
    -- deliberately not an FK to tasks or conversations.
    FOREIGN KEY (user_id, period_key)
        REFERENCES public.entitlement_usage_periods(user_id, period_key)
        ON DELETE CASCADE,
    CONSTRAINT turn_budget_allocations_scope_unique UNIQUE (user_id, scope_id),
    CONSTRAINT turn_budget_allocations_seal_consistent
        CHECK ((status = 'sealed') = (sealed_at IS NOT NULL)
               AND (status <> 'sealed' OR funded_microusd = 0))
);

CREATE INDEX turn_budget_allocations_period
    ON public.turn_budget_allocations(user_id, period_key, status);

COMMENT ON TABLE public.turn_budget_allocations IS
    'Opt-in fixture accounting root. bound_microusd is permission; '
    'funded_microusd is the unused Free escrow held in the owning period.';
COMMENT ON COLUMN public.turn_budget_allocations.funded_microusd IS
    'Unused F. It is included in entitlement_usage_periods.reserved_microusd '
    'and never takes a rate or concurrency slot.';
COMMENT ON COLUMN public.turn_budget_allocations.revision IS
    'Positive immutable generation for bounded, idempotent seal/readback.';

-- Lineage is attached after the real EntitlementService._reserve_body inserts
-- its row in the same uncommitted transaction. No production API is added.
ALTER TABLE public.entitlement_reservations
    ADD COLUMN turn_root_id uuid,
    ADD COLUMN turn_op_id uuid,
    ADD COLUMN turn_batch_id uuid,
    ADD COLUMN turn_final boolean;

ALTER TABLE public.entitlement_reservations
    ADD CONSTRAINT entitlement_reservations_turn_lineage_all_or_none CHECK (
        (turn_root_id IS NULL AND turn_op_id IS NULL
            AND turn_batch_id IS NULL AND turn_final IS NULL)
        OR (turn_root_id IS NOT NULL AND turn_op_id IS NOT NULL
             AND turn_batch_id IS NOT NULL AND turn_final IS NOT NULL)
    ),
    ADD CONSTRAINT entitlement_reservations_turn_root_fk
        FOREIGN KEY (user_id, turn_root_id)
        REFERENCES public.turn_budget_allocations(user_id, turn_root_id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED;

-- One operation id per owner/root, and an owner/root/batch lookup. NULL
-- lineage rows are ordinary legacy reservations and are not indexed here.
CREATE UNIQUE INDEX entitlement_reservations_turn_op_unique
    ON public.entitlement_reservations(user_id, turn_root_id, turn_op_id)
    WHERE turn_root_id IS NOT NULL;
CREATE INDEX entitlement_reservations_turn_batch_lookup
    ON public.entitlement_reservations(user_id, turn_root_id, turn_batch_id)
    WHERE turn_root_id IS NOT NULL;

-- Enforce lineage only for eligible stored Free PLAN, nonpremium, foreground,
-- nonextended rows with a valid root scope/period. NULL lineage is a no-op.
CREATE FUNCTION public.free_turn_lineage_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE a public.turn_budget_allocations;
BEGIN
 IF TG_OP='UPDATE' AND OLD.turn_root_id IS NOT NULL THEN
   IF (NEW.user_id,NEW.turn_root_id,NEW.turn_op_id,NEW.turn_batch_id,NEW.turn_final,
       NEW.period_key,NEW.scope_id,NEW.operation,NEW.reserved_microusd,NEW.workload_profile)
      IS DISTINCT FROM
      (OLD.user_id,OLD.turn_root_id,OLD.turn_op_id,OLD.turn_batch_id,OLD.turn_final,
       OLD.period_key,OLD.scope_id,OLD.operation,OLD.reserved_microusd,OLD.workload_profile)
   THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='free_turn_lineage_immutable'; END IF;
 END IF;
 IF NEW.turn_root_id IS NULL AND NEW.turn_op_id IS NULL
 AND NEW.turn_batch_id IS NULL AND NEW.turn_final IS NULL THEN
  RETURN NEW;
 END IF;
  IF NEW.turn_root_id IS NULL OR NEW.turn_op_id IS NULL OR NEW.turn_batch_id IS NULL
 OR NEW.turn_final IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='free_turn_lineage_refused';
 END IF;
 IF NEW.plan<>'free' OR NEW.charge_kind<>'plan'
  OR NEW.premium OR NEW.background OR NEW.extended OR NEW.extended_run
  OR NEW.scope_id IS NULL
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='free_turn_lineage_ineligible'; END IF;
 SELECT * INTO a FROM public.turn_budget_allocations
  WHERE user_id=NEW.user_id AND turn_root_id=NEW.turn_root_id FOR SHARE;
 IF a.turn_root_id IS NULL OR a.status<>'open'
 OR a.scope_id<>NEW.scope_id OR a.period_key<>NEW.period_key
 THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='free_turn_lineage_refused'; END IF;
 RETURN NEW;
END $$;

CREATE TRIGGER entitlement_reservations_turn_lineage
    BEFORE INSERT OR UPDATE OF user_id, turn_root_id, turn_op_id, turn_batch_id,
        turn_final, plan, charge_kind, premium, background, extended, extended_run, scope_id,
        period_key, operation, reserved_microusd, workload_profile
    ON public.entitlement_reservations
    FOR EACH ROW EXECUTE FUNCTION public.free_turn_lineage_guard();

-- No table or function grants. The setup owner installs any fixture-only
-- marker/authorization independently, and this migration only narrows PUBLIC.
DO $$ BEGIN
 IF EXISTS (
  SELECT 1 FROM pg_catalog.pg_class c
  CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) x
  WHERE c.oid='public.turn_budget_allocations'::regclass AND x.grantee<>c.relowner
 ) OR EXISTS (
  SELECT 1 FROM pg_catalog.pg_proc f
  JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(f.proacl,pg_catalog.acldefault('f',f.proowner))) x
  WHERE n.nspname='public' AND f.proname='free_turn_lineage_guard'
    AND x.grantee<>f.proowner
    AND NOT (x.grantee=0 AND x.privilege_type='EXECUTE' AND NOT x.is_grantable)
 ) THEN RAISE EXCEPTION 'free_turn_accounting_unsafe_default_privileges'; END IF;
END $$;
REVOKE ALL ON TABLE public.turn_budget_allocations FROM PUBLIC;
REVOKE ALL ON FUNCTION public.free_turn_lineage_guard() FROM PUBLIC;
