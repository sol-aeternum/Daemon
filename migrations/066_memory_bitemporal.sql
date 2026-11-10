-- Source-only #601. Requires the matching binary. Old writers fail closed.
-- NOW()/clock_timestamp() below are NEVER represented as commit timestamps.
CREATE TABLE memory_knowledge_clock (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    account_generation bigint NOT NULL CHECK (account_generation >= 0),
    sequence bigint NOT NULL DEFAULT 0 CHECK (sequence >= 0),
    erasure_epoch bigint NOT NULL DEFAULT 0 CHECK (erasure_epoch >= 0),
    last_clock_at timestamptz NOT NULL,
    clock_regressed boolean NOT NULL DEFAULT false
);

CREATE TABLE memory_mutations (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sequence bigint NOT NULL,
    account_generation bigint NOT NULL,
    transaction_id xid8 NOT NULL,
    write_lower_at timestamptz NOT NULL,
    observed_committed_by timestamptz,
    PRIMARY KEY(user_id, sequence),
    UNIQUE(user_id, transaction_id),
    CHECK (observed_committed_by IS NULL OR observed_committed_by >= write_lower_at)
);
CREATE INDEX memory_mutations_unsealed ON memory_mutations(user_id,sequence)
    WHERE observed_committed_by IS NULL;

ALTER TABLE memories ADD COLUMN head_revision_id uuid;
ALTER TABLE memories ADD COLUMN temporal_spec jsonb NOT NULL DEFAULT '{}';
ALTER TABLE memories ADD COLUMN temporal_scope_hash text NOT NULL DEFAULT 'untimed';
ALTER TABLE memories ADD COLUMN temporal_expression_ciphertext text;
ALTER TABLE memories ADD COLUMN revision_kind text NOT NULL DEFAULT 'assertion'
    CHECK(revision_kind IN ('assertion','correction','world_change','metadata','baseline'));
ALTER TABLE memories ADD COLUMN provenance_unknown boolean NOT NULL DEFAULT true;

CREATE TABLE memory_revisions (
    id uuid PRIMARY KEY,
    memory_id uuid NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sequence bigint NOT NULL,
    ordinal integer NOT NULL,
    previous_revision_id uuid,
    relationship text NOT NULL,
    source_origin_kind text NOT NULL,
    source_origin_id uuid,
    provenance_unknown boolean NOT NULL,
    state jsonb NOT NULL,
    FOREIGN KEY(user_id,sequence) REFERENCES memory_mutations(user_id,sequence),
    UNIQUE(memory_id,sequence,ordinal)
);
CREATE INDEX memory_revisions_cutoff ON memory_revisions(user_id,memory_id,sequence DESC,ordinal DESC);

CREATE TABLE memory_revision_segments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    revision_id uuid NOT NULL REFERENCES memory_revisions(id) ON DELETE CASCADE,
    content text NOT NULL,
    content_hash text,
    content_tsv tsvector,
    embedding vector(1024),
    embedding_model text,
    event_start timestamptz,
    event_end timestamptz,
    start_kind text NOT NULL CHECK(start_kind IN ('exact','unbounded','unknown')),
    end_kind text NOT NULL CHECK(end_kind IN ('exact','unbounded','unknown')),
    precision text NOT NULL CHECK(precision IN ('exact','date','month','year','approximate','unknown')),
    time_expression_ciphertext text,
    timezone text,
    CHECK ((event_start IS NOT NULL) = (start_kind='exact')),
    CHECK ((event_end IS NOT NULL) = (end_kind='exact')),
    CHECK (event_start IS NULL OR event_end IS NULL OR event_start < event_end),
    CHECK (precision='exact' OR start_kind!='exact' OR end_kind!='exact')
);
CREATE INDEX memory_segments_revision ON memory_revision_segments(revision_id);
CREATE INDEX memory_segments_lexical ON memory_revision_segments USING gin(content_tsv);
-- Separate historical index: the existing current-projection HNSW is untouched.
CREATE INDEX memory_segments_vector ON memory_revision_segments USING hnsw(embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

CREATE TABLE memory_dependencies (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_memory_id uuid NOT NULL,
    parent_revision_id uuid NOT NULL,
    dependent_kind text NOT NULL,
    dependent_id uuid NOT NULL,
    PRIMARY KEY(user_id,parent_memory_id,parent_revision_id,dependent_kind,dependent_id)
);
CREATE INDEX memory_dependencies_child ON memory_dependencies(user_id,dependent_kind,dependent_id);

CREATE TABLE memory_sources (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_id uuid NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    source_kind text NOT NULL,
    source_id uuid NOT NULL,
    PRIMARY KEY(user_id,memory_id,source_kind,source_id)
);

CREATE TABLE memory_tombstones (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_id uuid NOT NULL,
    erasure_epoch bigint NOT NULL,
    erased_at timestamptz NOT NULL,
    purge_after timestamptz,
    hard boolean NOT NULL,
    PRIMARY KEY(user_id,memory_id)
);
-- Span suppression is not a content ban: new messages have different source IDs.
CREATE TABLE memory_erased_sources (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_kind text NOT NULL,
    source_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    PRIMARY KEY(user_id,source_kind,source_id,memory_id)
);

DROP INDEX idx_memories_active_content_hash_unique;
CREATE UNIQUE INDEX idx_memories_active_content_hash_unique
    ON memories(user_id,content_hash,local_only,temporal_scope_hash)
    WHERE content_hash IS NOT NULL AND status='active' AND valid_to IS NULL
      AND NOT provenance_unknown;

-- Baseline is migration knowledge, not the row's historical creation time.
INSERT INTO memory_knowledge_clock(user_id,account_generation,sequence,last_clock_at)
SELECT owners.user_id,owners.account_generation,1,clock_timestamp()
FROM (SELECT DISTINCT m.user_id,COALESCE(a.content_generation,0) AS account_generation
      FROM memories m LEFT JOIN entitlement_accounts a ON a.user_id=m.user_id) owners;
INSERT INTO memory_mutations(user_id,sequence,account_generation,transaction_id,write_lower_at)
SELECT user_id,1,account_generation,pg_current_xact_id(),last_clock_at FROM memory_knowledge_clock;

CREATE FUNCTION memory_capture_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    mutation memory_mutations%ROWTYPE;
    revision uuid := gen_random_uuid();
    prior uuid;
    spec jsonb;
    boundary timestamptz;
    n integer;
BEGIN
    IF TG_OP='DELETE' THEN
        -- Owner cascade purges history; do not manufacture a revision for no owner.
        IF NOT EXISTS(SELECT 1 FROM users WHERE id=OLD.user_id) THEN RETURN OLD; END IF;
        IF NOT EXISTS(SELECT 1 FROM memory_mutations
                      WHERE user_id=OLD.user_id AND transaction_id=pg_current_xact_id()) THEN
            RAISE EXCEPTION 'memory_mutation_required' USING ERRCODE='55000';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' AND NEW.user_id IS DISTINCT FROM OLD.user_id THEN
        RAISE EXCEPTION 'memory_owner_immutable' USING ERRCODE='55000';
    END IF;
    IF TG_OP='UPDATE' AND OLD.source_conversation_id IS NOT NULL AND NEW.source_conversation_id IS NULL THEN
        NEW.provenance_unknown := true;
    END IF;
    IF TG_OP='UPDATE' AND
       (to_jsonb(NEW)-ARRAY['embedding','embedding_model','content_tsv','content_hash',
          'updated_at','last_accessed_at','access_count','last_retrieved_at','trust_score'])
       IS NOT DISTINCT FROM
       (to_jsonb(OLD)-ARRAY['embedding','embedding_model','content_tsv','content_hash',
          'updated_at','last_accessed_at','access_count','last_retrieved_at','trust_score']) THEN
        RETURN NEW;
    END IF;
    SELECT * INTO mutation FROM memory_mutations
    WHERE user_id=NEW.user_id AND transaction_id=pg_current_xact_id();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'memory_mutation_required' USING ERRCODE='55000';
    END IF;
    IF EXISTS(SELECT 1 FROM memory_tombstones WHERE user_id=NEW.user_id AND memory_id=NEW.id)
       AND NEW.status != 'deleted' THEN
        RAISE EXCEPTION 'memory_erased' USING ERRCODE='55000';
    END IF;
    prior := CASE WHEN TG_OP='UPDATE' THEN OLD.head_revision_id ELSE NULL END;
    NEW.head_revision_id := revision;
    RETURN NEW;
END $$;

CREATE FUNCTION memory_save_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    mutation memory_mutations%ROWTYPE;
    prior uuid;
    spec jsonb := NEW.temporal_spec;
    boundary timestamptz;
    n integer;
BEGIN
    IF TG_OP='UPDATE' AND NEW.head_revision_id IS NOT DISTINCT FROM OLD.head_revision_id THEN
        RETURN NEW;
    END IF;
    SELECT * INTO STRICT mutation FROM memory_mutations
    WHERE user_id=NEW.user_id AND transaction_id=pg_current_xact_id();
    prior := CASE WHEN TG_OP='UPDATE' THEN OLD.head_revision_id ELSE NULL END;
    SELECT COALESCE(max(ordinal),0)+1 INTO n FROM memory_revisions
    WHERE memory_id=NEW.id AND sequence=mutation.sequence;
    INSERT INTO memory_revisions(id,memory_id,user_id,sequence,ordinal,previous_revision_id,
        relationship,source_origin_kind,source_origin_id,provenance_unknown,state)
    VALUES(NEW.head_revision_id,NEW.id,NEW.user_id,mutation.sequence,n,prior,NEW.revision_kind,
        CASE WHEN prior IS NOT NULL THEN (SELECT source_origin_kind FROM memory_revisions WHERE id=prior)
             WHEN NEW.source_conversation_id IS NOT NULL THEN 'conversation' ELSE NEW.source_type END,
        COALESCE((SELECT source_origin_id FROM memory_revisions WHERE id=prior),NEW.source_conversation_id),
        NEW.provenance_unknown,
        to_jsonb(NEW)-ARRAY['content','content_hash','content_tsv','embedding','embedding_model',
            'temporal_expression_ciphertext']);
    IF prior IS NOT NULL AND TG_OP='UPDATE' AND NEW.content=OLD.content
       AND NEW.temporal_spec=OLD.temporal_spec THEN
        INSERT INTO memory_revision_segments(revision_id,content,content_hash,content_tsv,
            embedding,embedding_model,event_start,event_end,start_kind,end_kind,precision,
            time_expression_ciphertext,timezone)
        SELECT NEW.head_revision_id,content,content_hash,content_tsv,embedding,embedding_model,
            event_start,event_end,start_kind,end_kind,precision,time_expression_ciphertext,timezone
        FROM memory_revision_segments WHERE revision_id=prior;
    ELSE
        IF NEW.revision_kind='correction' AND prior IS NOT NULL THEN
            INSERT INTO memory_revision_segments(revision_id,content,content_hash,content_tsv,
                embedding,embedding_model,event_start,event_end,start_kind,end_kind,precision,
                time_expression_ciphertext,timezone)
            SELECT NEW.head_revision_id,content,content_hash,content_tsv,embedding,embedding_model,
                event_start,LEAST(COALESCE(event_end,(spec->>'start')::timestamptz),(spec->>'start')::timestamptz),
                start_kind,CASE WHEN event_end IS NOT NULL AND event_end<=(spec->>'start')::timestamptz THEN end_kind ELSE 'exact' END,
                precision,time_expression_ciphertext,timezone
            FROM memory_revision_segments WHERE revision_id=prior
              AND spec->>'start' IS NOT NULL AND (event_start IS NULL OR event_start<(spec->>'start')::timestamptz);
            INSERT INTO memory_revision_segments(revision_id,content,content_hash,content_tsv,
                embedding,embedding_model,event_start,event_end,start_kind,end_kind,precision,
                time_expression_ciphertext,timezone)
            SELECT NEW.head_revision_id,content,content_hash,content_tsv,embedding,embedding_model,
                GREATEST(COALESCE(event_start,(spec->>'end')::timestamptz),(spec->>'end')::timestamptz),event_end,
                CASE WHEN event_start IS NOT NULL AND event_start>=(spec->>'end')::timestamptz THEN start_kind ELSE 'exact' END,
                end_kind,precision,time_expression_ciphertext,timezone
            FROM memory_revision_segments WHERE revision_id=prior
              AND spec->>'end' IS NOT NULL AND (event_end IS NULL OR event_end>(spec->>'end')::timestamptz);
        END IF;
        IF NEW.revision_kind='world_change' THEN
            boundary := (spec->>'start')::timestamptz;
            IF prior IS NULL OR boundary IS NULL OR spec->>'precision' != 'exact' THEN
                RAISE EXCEPTION 'world_change_requires_exact_boundary' USING ERRCODE='22023';
            END IF;
            INSERT INTO memory_revision_segments(revision_id,content,content_hash,content_tsv,
                embedding,embedding_model,event_start,event_end,start_kind,end_kind,precision,
                time_expression_ciphertext,timezone)
            SELECT NEW.head_revision_id,content,content_hash,content_tsv,embedding,embedding_model,
                event_start,LEAST(COALESCE(event_end,boundary),boundary),start_kind,'exact',precision,
                time_expression_ciphertext,timezone
            FROM memory_revision_segments WHERE revision_id=prior
              AND (event_start IS NULL OR event_start<boundary);
        END IF;
        INSERT INTO memory_revision_segments(revision_id,content,content_hash,content_tsv,
            embedding,embedding_model,event_start,event_end,start_kind,end_kind,precision,
            time_expression_ciphertext,timezone)
        VALUES(NEW.head_revision_id,NEW.content,NEW.content_hash,NEW.content_tsv,NEW.embedding,
            NEW.embedding_model,(spec->>'start')::timestamptz,(spec->>'end')::timestamptz,
            COALESCE(spec->>'start_kind','unknown'),COALESCE(spec->>'end_kind','unknown'),
            COALESCE(spec->>'precision','unknown'),NEW.temporal_expression_ciphertext,spec->>'timezone');
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER memory_revision_capture BEFORE INSERT OR UPDATE OR DELETE ON memories
    FOR EACH ROW EXECUTE FUNCTION memory_capture_revision();
CREATE TRIGGER memory_revision_save AFTER INSERT OR UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION memory_save_revision();

-- Seed ciphertext without decrypting it. Existing searchable/vector material is copied.
UPDATE memories SET revision_kind='baseline',
    provenance_unknown=(source_conversation_id IS NULL AND source_type NOT IN ('manual','import','user_created'));

-- Existing explicit dependency metadata is evidence of derivation, not an
-- invented historical assertion. Ignore malformed/foreign IDs conservatively.
INSERT INTO memory_dependencies(user_id,parent_memory_id,parent_revision_id,dependent_kind,dependent_id)
SELECT child.user_id,parent.id,parent.head_revision_id,'memory',child.id
FROM memories child
CROSS JOIN LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(child.metadata->'source_memory_ids')='array'
    THEN child.metadata->'source_memory_ids' ELSE '[]'::jsonb END) source(value)
JOIN memories parent ON parent.id::text=source.value AND parent.user_id=child.user_id
ON CONFLICT DO NOTHING;

COMMENT ON TABLE memory_mutations IS
    'Committed sequence watermarks; timestamp bounds are not commit timestamps. Missing/intersecting bounds mean indeterminate.';
COMMENT ON TABLE memory_tombstones IS
    'Current revocation at every knowledge cutoff. Memory-only erasure is not protected against backup restoration.';

-- Untimed ordinary retrieval retains direct projection/index access. Explicit
-- event timelines use the head bundle (including scheduled future changes).
CREATE VIEW memory_current_assertions AS
SELECT m.* FROM memories m WHERE m.temporal_spec='{}'::jsonb
UNION ALL
SELECT (jsonb_populate_record(NULL::memories,to_jsonb(m)||jsonb_build_object(
    'content',s.content,'content_hash',s.content_hash,'content_tsv',s.content_tsv,
    'embedding',s.embedding,'embedding_model',s.embedding_model))).*
FROM memories m JOIN memory_revision_segments s ON s.revision_id=m.head_revision_id
WHERE m.temporal_spec!='{}'::jsonb
  AND (s.event_start IS NULL OR s.event_start<=clock_timestamp())
  AND (s.event_end IS NULL OR s.event_end>clock_timestamp());
