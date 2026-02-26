-- Add assistant reasoning persistence fields to messages

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS reasoning_text TEXT, -- Reasoning/thinking text (nullable; persisted by app layer)
    ADD COLUMN IF NOT EXISTS reasoning_duration_secs INTEGER; -- Duration in seconds (nullable)
