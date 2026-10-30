-- Caller owns transaction; no runtime role/grant changes. Source-only C1 registry.
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '5s';

ALTER TABLE public.artifact_namespaces ADD CONSTRAINT artifact_namespace_owned_generation
 UNIQUE (account_id,generation,namespace);

CREATE TABLE public.resource_catalog_heads (
 account_id uuid PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
 generation bigint NOT NULL CHECK (generation>=0),
 insertion_counter bigint NOT NULL DEFAULT 0 CHECK (insertion_counter>=0),
 filter_revision bigint NOT NULL DEFAULT 0 CHECK (filter_revision>=0)
);
CREATE TABLE public.resources (
 resource_id uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 insertion_ordinal bigint NOT NULL CHECK (insertion_ordinal>0),
 UNIQUE(account_id,resource_id), UNIQUE(account_id,insertion_ordinal)
);
CREATE TABLE public.resource_versions (
 version_id uuid PRIMARY KEY,
 account_id uuid NOT NULL,
 resource_id uuid NOT NULL,
 generation bigint NOT NULL CHECK (generation>=0),
 representation text NOT NULL CHECK (representation IN
 ('submitted_image','submitted_text','metadata_only','generated_file')),
 kind text NOT NULL CHECK (kind IN ('image','text','file')),
 mime text NOT NULL,
 measured_bytes bigint,
 metadata_ciphertext text NOT NULL CHECK (octet_length(metadata_ciphertext) BETWEEN 1 AND 131072),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 insertion_ordinal bigint NOT NULL CHECK (insertion_ordinal>0),
 FOREIGN KEY(account_id,resource_id) REFERENCES public.resources(account_id,resource_id) ON DELETE CASCADE,
 UNIQUE(account_id,resource_id,version_id), UNIQUE(account_id,insertion_ordinal),
 UNIQUE(account_id,resource_id,version_id,generation),
 UNIQUE(account_id,resource_id,version_id,generation,representation,measured_bytes,insertion_ordinal),
 CHECK ((representation='metadata_only' AND measured_bytes IS NULL)
 OR (representation<>'metadata_only' AND measured_bytes IS NOT NULL AND measured_bytes BETWEEN 0 AND 8388608)),
 CHECK ((representation='submitted_image' AND kind='image' AND mime IN ('image/png','image/jpeg','image/webp','image/gif'))
 OR (representation='submitted_text' AND kind='text' AND mime='text/plain')
 OR (representation='metadata_only' AND kind='file' AND mime='application/octet-stream')
 OR (representation='generated_file' AND kind='file' AND mime='text/csv'))
);
CREATE TABLE public.resource_publications (
 version_id uuid PRIMARY KEY,
 account_id uuid NOT NULL,
 resource_id uuid NOT NULL,
 generation bigint NOT NULL CHECK (generation>=0),
 namespace text NOT NULL,
 intent_id uuid NOT NULL UNIQUE,
 source_kind text NOT NULL CHECK (source_kind IN ('input','output','append')),
 representation text NOT NULL CHECK (representation IN ('submitted_image','submitted_text','generated_file')),
 measured_bytes bigint NOT NULL CHECK (measured_bytes BETWEEN 0 AND 8388608),
 binding_ciphertext text NOT NULL CHECK (octet_length(binding_ciphertext) BETWEEN 1 AND 131072),
 insertion_ordinal bigint NOT NULL CHECK (insertion_ordinal>0),
 committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(account_id,resource_id,version_id,generation,representation,measured_bytes,insertion_ordinal)
 REFERENCES public.resource_versions(account_id,resource_id,version_id,generation,representation,measured_bytes,insertion_ordinal)
 ON DELETE CASCADE,
 FOREIGN KEY(account_id,generation,namespace)
 REFERENCES public.artifact_namespaces(account_id,generation,namespace) ON DELETE RESTRICT,
 CHECK ((source_kind='input' AND representation IN ('submitted_image','submitted_text'))
 OR (source_kind IN ('output','append') AND representation='generated_file'))
);
CREATE TABLE public.resource_references (
 reference_id uuid PRIMARY KEY,
 account_id uuid NOT NULL,
 resource_id uuid NOT NULL,
 version_id uuid NOT NULL,
 generation bigint NOT NULL CHECK (generation>=0),
 role text NOT NULL CHECK (role IN ('input','output')),
 ordinal integer NOT NULL CHECK (ordinal>=0),
 task_id uuid,
 conversation_id uuid,
 message_id uuid,
 job_id uuid,
 provenance_ciphertext text NOT NULL CHECK (octet_length(provenance_ciphertext) BETWEEN 1 AND 131072),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(account_id,resource_id,version_id,generation)
 REFERENCES public.resource_versions(account_id,resource_id,version_id,generation) ON DELETE CASCADE,
 -- Explicit detach/encrypted-provenance update must precede source deletion.
 -- Deferred NO ACTION permits the account's cascade to remove both at commit.
 FOREIGN KEY(task_id) REFERENCES public.tasks(id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(conversation_id) REFERENCES public.conversations(id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(message_id) REFERENCES public.messages(id) DEFERRABLE INITIALLY DEFERRED,
 UNIQUE(account_id,task_id,role,ordinal)
);
CREATE TABLE public.resource_version_intents (
 intent_id uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 generation bigint NOT NULL CHECK (generation>=0),
 resource_id uuid NOT NULL,
 version_id uuid NOT NULL UNIQUE,
 task_id uuid NOT NULL,
 job_id uuid NOT NULL,
 operation_id uuid NOT NULL,
 output_slot integer NOT NULL CHECK (output_slot>=0),
 lifecycle_id uuid NOT NULL,
 attempt_id uuid NOT NULL,
 attempt_epoch bigint NOT NULL CHECK (attempt_epoch>0),
 representation text NOT NULL CHECK (representation='generated_file'),
 mime text NOT NULL CHECK (mime='text/csv'),
 measured_bytes bigint NOT NULL CHECK (measured_bytes BETWEEN 0 AND 8388608),
 descriptor_ciphertext text NOT NULL CHECK (octet_length(descriptor_ciphertext) BETWEEN 1 AND 131072),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(account_id,resource_id) REFERENCES public.resources(account_id,resource_id) ON DELETE CASCADE,
 FOREIGN KEY(account_id,job_id,generation,task_id)
 REFERENCES public.artifact_output_jobs(account_id,job_id,account_generation,task_id) ON DELETE CASCADE,
 UNIQUE(account_id,operation_id,output_slot), UNIQUE(account_id,job_id,output_slot)
);

CREATE INDEX resource_versions_catalog ON public.resource_versions(account_id,created_at DESC,version_id DESC);
CREATE INDEX resource_versions_kind_catalog ON public.resource_versions(account_id,kind,created_at DESC,version_id DESC);
CREATE INDEX resource_versions_mime_catalog ON public.resource_versions(account_id,mime,created_at DESC,version_id DESC);
CREATE INDEX resource_references_task ON public.resource_references(account_id,task_id,ordinal,reference_id);
CREATE INDEX resource_references_filters ON public.resource_references(account_id,role,task_id,job_id,version_id);

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class c
 CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 WHERE c.oid IN ('public.resource_catalog_heads'::regclass,'public.resources'::regclass,
 'public.resource_versions'::regclass,'public.resource_publications'::regclass,
 'public.resource_references'::regclass,'public.resource_version_intents'::regclass)
 AND a.grantee<>c.relowner) THEN
 RAISE EXCEPTION 'registry_unsafe_default_privileges'; END IF;
 REVOKE ALL ON public.resource_catalog_heads,public.resources,public.resource_versions,
 public.resource_publications,public.resource_references,public.resource_version_intents FROM PUBLIC;
END $$;
COMMENT ON TABLE public.resource_catalog_heads IS 'Account/reset content: committed version watermark and mutable-filter revision; never rewind generation.';
COMMENT ON TABLE public.resources IS 'Account/reset content; source deletion retains resource identity. Not filesystem cleanup.';
COMMENT ON TABLE public.resource_versions IS 'Immutable account/reset content: encrypted metadata/provenance/checksum; availability separately verified.';
COMMENT ON TABLE public.resource_publications IS 'Immutable account/reset content: committed intent binding, not physical availability or a caller-supplied write grant.';
COMMENT ON TABLE public.resource_references IS 'Account/reset content; source detach requires same-transaction encrypted source_deleted update and filter revision increment.';
COMMENT ON TABLE public.resource_version_intents IS 'Account/reset content: immutable append-version identity; registered lifecycle/current lease and original generation required for new effects.';
