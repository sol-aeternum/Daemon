-- Add reasoning_model field to messages

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS reasoning_model TEXT; -- Model ID used for reasoning (nullable)
