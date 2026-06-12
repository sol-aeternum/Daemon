-- Add device-creation events to the existing identity audit log.
--
-- The table already stores hashed request metadata for hosted identity
-- events. Device creation is part of the same abuse-triage surface.

ALTER TABLE identity_audit_log
    DROP CONSTRAINT IF EXISTS identity_audit_log_event_type_check;

ALTER TABLE identity_audit_log
    ADD CONSTRAINT identity_audit_log_event_type_check
    CHECK (event_type IN (
        'email_challenge_issued',
        'email_challenge_consumed',
        'email_challenge_locked',
        'google_nonce_issued',
        'google_nonce_consumed',
        'provider_linked',
        'provider_unlinked',
        'invite_created',
        'invite_consumed',
        'invite_disabled',
        'tenant_created',
        'tenant_member_added',
        'device_created'
    ));
