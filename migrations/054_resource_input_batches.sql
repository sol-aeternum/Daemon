-- Caller owns transaction. Source-only C1 inputs; no runtime grants/activation.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';
CREATE TABLE public.resource_input_batches (
 batch_id uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 generation bigint NOT NULL CHECK (generation>=0),
 key_digest bytea CHECK (key_digest IS NULL OR octet_length(key_digest)=32),
 lookup_key_id bytea NOT NULL CHECK (octet_length(lookup_key_id)=32),
 task_id uuid NOT NULL UNIQUE,
 request_ciphertext text CHECK (request_ciphertext IS NULL OR octet_length(request_ciphertext) BETWEEN 1 AND 134217728),
 state text NOT NULL CHECK (state IN ('preparing','accepted','uncertain','source_deleted')),
 accepted_task_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(account_id,batch_id,generation),
 UNIQUE(account_id,key_digest),
 CHECK (accepted_task_id IS NULL OR accepted_task_id=task_id),
 CHECK ((state='accepted') = (accepted_task_id IS NOT NULL)),
 CHECK ((state='source_deleted') = (request_ciphertext IS NULL)),
 FOREIGN KEY(accepted_task_id) REFERENCES public.tasks(id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE public.resource_input_intents (
 intent_id uuid PRIMARY KEY,
 account_id uuid NOT NULL,
 batch_id uuid NOT NULL,
 generation bigint NOT NULL CHECK (generation>=0),
 ordinal integer NOT NULL CHECK (ordinal>=0),
 resource_id uuid NOT NULL UNIQUE,
 version_id uuid NOT NULL UNIQUE,
 representation text NOT NULL CHECK (representation IN ('submitted_image','submitted_text','metadata_only')),
 kind text NOT NULL CHECK (kind IN ('image','text','file')),
 mime text NOT NULL,
 measured_bytes bigint,
 descriptor_ciphertext text NOT NULL CHECK (octet_length(descriptor_ciphertext) BETWEEN 1 AND 131072),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(account_id,batch_id,generation)
 REFERENCES public.resource_input_batches(account_id,batch_id,generation) ON DELETE CASCADE,
 UNIQUE(account_id,batch_id,ordinal),
 CHECK ((representation='metadata_only' AND measured_bytes IS NULL)
 OR (representation<>'metadata_only' AND measured_bytes IS NOT NULL AND measured_bytes BETWEEN 0 AND 8388608)),
 CHECK ((representation='submitted_image' AND kind='image' AND mime IN ('image/png','image/jpeg','image/webp','image/gif'))
 OR (representation='submitted_text' AND kind='text' AND mime='text/plain')
 OR (representation='metadata_only' AND kind='file' AND mime='application/octet-stream'))
);
CREATE INDEX resource_input_intents_ordered ON public.resource_input_intents(account_id,batch_id,ordinal,intent_id);
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class c
 CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 WHERE c.oid IN ('public.resource_input_batches'::regclass,'public.resource_input_intents'::regclass)
 AND a.grantee<>c.relowner) THEN RAISE EXCEPTION 'resource_input_unsafe_default_privileges'; END IF;
 REVOKE ALL ON public.resource_input_batches,public.resource_input_intents FROM PUBLIC;
END $$;
COMMENT ON TABLE public.resource_input_batches IS 'Account/reset content: stable keyed submission identity, encrypted canonical request, preallocated task identity; no plaintext key, availability grant or automatic uncertain-commit retry.';
COMMENT ON TABLE public.resource_input_intents IS 'Account/reset content: ordered immutable received-representation identities and encrypted descriptor, committed before stage effects; no caller path or publication authority.';
