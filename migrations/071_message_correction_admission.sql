-- Source-only #601 admission/version foundation, not correction execution.
-- Existing/imported/generic rows stay ineligible. No roles, grants or GUC authority.
ALTER TABLE messages
    ADD COLUMN source_revision bigint NOT NULL DEFAULT 1 CHECK (source_revision > 0),
    ADD COLUMN correction_origin_generation bigint CHECK (correction_origin_generation >= 0),
    ADD COLUMN correction_source_revision bigint CHECK (correction_source_revision > 0),
    ADD CONSTRAINT messages_correction_source_consistent CHECK (
        correction_source_revision IS NULL OR
        (correction_origin_generation IS NOT NULL AND correction_source_revision <= source_revision)
    );

CREATE FUNCTION memory_message_revision_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.source_revision IS DISTINCT FROM 1::bigint OR NEW.correction_source_revision IS NOT NULL THEN
            RAISE EXCEPTION 'message_source_revision_managed' USING ERRCODE='55000';
        END IF;
        IF NEW.correction_origin_generation IS NOT NULL THEN
            IF NEW.role IS DISTINCT FROM 'user' OR NOT EXISTS (
                SELECT 1 FROM conversations WHERE id=NEW.conversation_id AND user_id=NEW.user_id
            ) THEN
                RAISE EXCEPTION 'message_admission_source_invalid' USING ERRCODE='55000';
            END IF;
        END IF;
    ELSE
        IF NEW.source_revision IS DISTINCT FROM OLD.source_revision OR
           NEW.correction_source_revision IS DISTINCT FROM OLD.correction_source_revision THEN
            RAISE EXCEPTION 'message_source_revision_managed' USING ERRCODE='55000';
        END IF;
        IF NEW.correction_origin_generation IS DISTINCT FROM OLD.correction_origin_generation THEN
            RAISE EXCEPTION 'message_admission_immutable' USING ERRCODE='55000';
        END IF;
        -- Do not add new identity/movement restrictions to unadmitted legacy rows.
        IF OLD.correction_origin_generation IS NOT NULL AND
           (NEW.id,NEW.user_id,NEW.conversation_id,NEW.role) IS DISTINCT FROM
           (OLD.id,OLD.user_id,OLD.conversation_id,OLD.role) THEN
            RAISE EXCEPTION 'message_admission_identity_immutable' USING ERRCODE='55000';
        END IF;
        -- Closed nonsemantic allowlist: token counters only. New columns and all
        -- content/status/metadata/trace changes increment by default, including ABA.
        IF (to_jsonb(NEW)-ARRAY['source_revision','correction_origin_generation',
                'correction_source_revision','tokens_in','tokens_out']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['source_revision','correction_origin_generation',
                'correction_source_revision','tokens_in','tokens_out']) THEN
            NEW.source_revision := OLD.source_revision + 1;
        END IF;
    END IF;
    -- Eligibility is fixed at birth; only the first exact complete version freezes.
    -- Edits/status cycling never refresh that version or authorize another operation.
    IF NEW.correction_origin_generation IS NOT NULL AND
       NEW.correction_source_revision IS NULL AND NEW.status='complete' THEN
        NEW.correction_source_revision := NEW.source_revision;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER message_source_revision_guard BEFORE INSERT OR UPDATE ON messages
    FOR EACH ROW EXECUTE FUNCTION memory_message_revision_guard();

COMMENT ON COLUMN messages.correction_origin_generation IS
    'Immutable prospective trusted-server admission, NULL for legacy/import/generic rows. Not authenticated SQL-callsite provenance.';
COMMENT ON COLUMN messages.correction_source_revision IS
    'First complete admitted version, never refreshed after edits. No correction operation or segment dependency is implemented here.';
