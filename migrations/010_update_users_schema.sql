ALTER TABLE users
    ADD COLUMN IF NOT EXISTS username TEXT;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS settings JSONB DEFAULT '{}'::jsonb;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS encryption_key_id TEXT;

UPDATE users
SET settings = COALESCE(settings, preferences, '{}'::jsonb)
WHERE settings IS NULL OR settings = '{}'::jsonb;

UPDATE users
SET username = COALESCE(
    NULLIF(username, ''),
    NULLIF(name, ''),
    NULLIF(split_part(email, '@', 1), ''),
    'user'
)
WHERE username IS NULL OR username = '';

ALTER TABLE users
    ALTER COLUMN username SET NOT NULL;
