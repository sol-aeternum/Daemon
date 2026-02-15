-- Seed default user with fixed UUID for development/testing
-- This is idempotent - will not fail if user already exists
INSERT INTO users (id, email, name, preferences, created_at, updated_at)
VALUES (
    '00000000-0000-0000-0000-000000000001'::uuid,
    'default@daemon.local',
    'Default User',
    '{}'::jsonb,
    NOW(),
    NOW()
)
ON CONFLICT (id) DO NOTHING;
