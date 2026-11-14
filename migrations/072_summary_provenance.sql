-- #601 direct-source summary foundation. No legacy attribution/backfill.
ALTER TABLE conversations
    ADD COLUMN summary_head_version_id uuid,
    ADD COLUMN summary_state_revision bigint NOT NULL DEFAULT 0 CHECK(summary_state_revision >= 0);

CREATE TABLE conversation_summary_versions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL,
    format integer NOT NULL CHECK(format=1),
    account_generation bigint NOT NULL CHECK(account_generation>=0),
    erasure_epoch bigint NOT NULL CHECK(erasure_epoch>=0),
    parent_version_id uuid,
    source_count integer NOT NULL CHECK(source_count>=0),
    checkpoint_only boolean NOT NULL CHECK(checkpoint_only=(source_count=0)),
    checkpoint_count bigint NOT NULL CHECK(checkpoint_count>=0),
    checkpoint jsonb NOT NULL CHECK(jsonb_typeof(checkpoint)='array'),
    rendered_members jsonb NOT NULL CHECK(jsonb_typeof(rendered_members)='array'),
    render_limit integer CHECK(render_limit>0),
    sealed boolean NOT NULL DEFAULT false,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX summary_versions_owner_conversation ON conversation_summary_versions(user_id,conversation_id);
CREATE TABLE conversation_summary_sources (
    version_id uuid NOT NULL REFERENCES conversation_summary_versions(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL,
    message_id uuid NOT NULL,
    source_revision bigint NOT NULL CHECK(source_revision>0),
    PRIMARY KEY(version_id,message_id)
);
CREATE INDEX summary_sources_message ON conversation_summary_sources(user_id,message_id);
CREATE INDEX summary_sources_conversation ON conversation_summary_sources(user_id,conversation_id);
CREATE TABLE conversation_summary_payloads (
    version_id uuid PRIMARY KEY REFERENCES conversation_summary_versions(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    format text NOT NULL CHECK(format='fernet-v1'),
    ciphertext text NOT NULL
);

CREATE FUNCTION summary_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.sealed OR NEW.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION 'summary_version_unsealed_required' USING ERRCODE='55000';
        END IF;
    ELSE
        IF (to_jsonb(NEW)-ARRAY['sealed','revoked_at']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['sealed','revoked_at']) OR
           (OLD.sealed AND NOT NEW.sealed) OR
           (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
            RAISE EXCEPTION 'summary_version_immutable' USING ERRCODE='55000';
        END IF;
        IF NEW.sealed AND NOT OLD.sealed THEN
            IF NEW.source_count!=(SELECT count(*) FROM conversation_summary_sources
                WHERE version_id=NEW.id AND user_id=NEW.user_id)
               OR (NOT NEW.checkpoint_only AND NOT EXISTS(SELECT 1 FROM conversation_summary_payloads
                   WHERE version_id=NEW.id AND user_id=NEW.user_id))
               OR (NEW.checkpoint_only AND (NEW.rendered_members!='[]'::jsonb OR EXISTS(
                   SELECT 1 FROM conversation_summary_payloads WHERE version_id=NEW.id))) THEN
                RAISE EXCEPTION 'summary_closure_incomplete' USING ERRCODE='55000';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER summary_version_guard BEFORE INSERT OR UPDATE ON conversation_summary_versions
    FOR EACH ROW EXECUTE FUNCTION summary_version_guard();

CREATE FUNCTION summary_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        IF EXISTS(SELECT 1 FROM users WHERE id=OLD.user_id) AND
           EXISTS(SELECT 1 FROM conversation_summary_versions WHERE id=OLD.version_id) THEN
            RAISE EXCEPTION 'summary_sources_immutable' USING ERRCODE='55000';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' OR NOT EXISTS(SELECT 1 FROM conversation_summary_versions v
        WHERE v.id=NEW.version_id AND v.user_id=NEW.user_id AND NOT v.sealed) THEN
        RAISE EXCEPTION 'summary_sources_immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER summary_source_guard BEFORE INSERT OR UPDATE OR DELETE ON conversation_summary_sources
    FOR EACH ROW EXECUTE FUNCTION summary_source_guard();

CREATE FUNCTION summary_payload_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' OR NOT EXISTS(SELECT 1 FROM conversation_summary_versions v
        WHERE v.id=NEW.version_id AND v.user_id=NEW.user_id AND NOT v.sealed) THEN
        RAISE EXCEPTION 'summary_payload_immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER summary_payload_guard BEFORE INSERT OR UPDATE ON conversation_summary_payloads
    FOR EACH ROW EXECUTE FUNCTION summary_payload_guard();

CREATE FUNCTION conversation_summary_head_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.summary_head_version_id IS NOT NULL OR NEW.summary_state_revision!=0 THEN
            RAISE EXCEPTION 'summary_state_managed' USING ERRCODE='55000';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.summary_state_revision IS DISTINCT FROM OLD.summary_state_revision THEN
        RAISE EXCEPTION 'summary_state_managed' USING ERRCODE='55000';
    END IF;
    IF NEW.summary IS DISTINCT FROM OLD.summary OR
       NEW.summary_updated_at IS DISTINCT FROM OLD.summary_updated_at OR
       NEW.summary_head_version_id IS DISTINCT FROM OLD.summary_head_version_id OR
       (NEW.metadata->'last_summarized_msg_count') IS DISTINCT FROM (OLD.metadata->'last_summarized_msg_count') OR
       (NEW.metadata->'summary_continuation_pending') IS DISTINCT FROM (OLD.metadata->'summary_continuation_pending') OR
       (NEW.summary IS NULL AND NEW.summary_updated_at IS NULL AND NEW.summary_head_version_id IS NULL) THEN
        NEW.summary_state_revision := OLD.summary_state_revision+1;
        -- Generic plaintext writes remain legacy/unqualified, never an authority.
        IF NEW.summary IS NOT NULL THEN NEW.summary_head_version_id := NULL; END IF;
        IF NEW.summary_head_version_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM conversation_summary_versions v
            WHERE v.id=NEW.summary_head_version_id AND v.user_id=NEW.user_id AND v.conversation_id=NEW.id
              AND v.sealed AND v.revoked_at IS NULL AND (v.checkpoint_only OR EXISTS(
                  SELECT 1 FROM conversation_summary_payloads p WHERE p.version_id=v.id AND p.user_id=NEW.user_id))) THEN
            RAISE EXCEPTION 'summary_head_unqualified' USING ERRCODE='55000';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER conversation_summary_head_guard BEFORE INSERT OR UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION conversation_summary_head_guard();

COMMENT ON TABLE conversation_summary_versions IS
    'Immutable direct-source provenance only, not transitive assistant prompt attribution. Payloads retain replaceable-cache lifecycle.';
