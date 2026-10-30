-- C1 non-cascading birth inventory, not physical readiness or a lifecycle fence.
-- Source-only: caller owns the migration transaction. No runtime role grants.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';
CREATE TABLE public.artifact_namespaces (
 namespace text PRIMARY KEY CHECK (namespace ~ '^[0-9a-f]{32}$'),
 account_id uuid NOT NULL,
 generation bigint NOT NULL CHECK (generation >= 0),
 journal_id uuid NOT NULL REFERENCES public.deletion_journal_binding(journal_id) ON DELETE RESTRICT,
 birth_seq bigint NOT NULL CHECK (birth_seq > 0),
 birth_event_id uuid NOT NULL,
 birth_digest bytea NOT NULL CHECK (octet_length(birth_digest) = 32),
 command_digest bytea NOT NULL CHECK (octet_length(command_digest) = 32),
 state text NOT NULL CHECK (state IN ('provisioning','ready','uncertain','retired')),
 UNIQUE(account_id,generation),
 UNIQUE(journal_id,birth_seq),
 UNIQUE(journal_id,birth_event_id)
);
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class c
 CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 WHERE c.oid='public.artifact_namespaces'::regclass AND a.grantee<>c.relowner) THEN
 RAISE EXCEPTION 'namespace_unsafe_default_privileges'; END IF;
 REVOKE ALL ON public.artifact_namespaces FROM PUBLIC;
END $$;
COMMENT ON TABLE public.artifact_namespaces IS 'Non-cascading opaque journal birth inventory. Applied marker proves inventory only. Physical readiness always reverified; no lifecycle completion.';
