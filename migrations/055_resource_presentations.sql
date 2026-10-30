-- Caller owns transaction; source-only C1 presentations, no runtime grants.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';
CREATE TABLE public.resource_presentations (
 presentation_id uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 generation bigint NOT NULL CHECK (generation>=0),
 source_message_id uuid,
 envelope_ciphertext text NOT NULL CHECK (octet_length(envelope_ciphertext) BETWEEN 1 AND 131072),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(account_id,presentation_id),
 FOREIGN KEY(source_message_id) REFERENCES public.messages(id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX resource_presentations_source ON public.resource_presentations(account_id,source_message_id,created_at DESC,presentation_id DESC);
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class c
 CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 WHERE c.oid='public.resource_presentations'::regclass AND a.grantee<>c.relowner)
 THEN RAISE EXCEPTION 'resource_presentation_unsafe_default_privileges'; END IF;
 REVOKE ALL ON public.resource_presentations FROM PUBLIC;
END $$;
COMMENT ON TABLE public.resource_presentations IS 'Account/reset content: encrypted versioned pinned envelope and minimal opaque original provenance; trusted writer validates same-account versions and source message. Source deletion detaches transactionally, not a resource cascade or executable action.';
