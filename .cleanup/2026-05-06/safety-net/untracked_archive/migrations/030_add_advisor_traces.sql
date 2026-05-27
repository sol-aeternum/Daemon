-- Migration: 030_add_advisor_traces
-- Adds encrypted advisor_traces TEXT column to messages for replay-only
-- advisor session data (text_parts, reasoning_parts, tool_calls, tool_results,
-- errors, usage, trace_key, parent_trace_key, event_tags).

-- Add advisor_traces column as encrypted TEXT
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS advisor_traces TEXT;

COMMENT ON COLUMN messages.advisor_traces IS
    'JSON-serialized advisor trace data, encrypted at rest. Contains advisor_id, '
    'text_parts, reasoning_parts, tool_calls, tool_results, errors, usage, '
    'trace_key, parent_trace_key, and event_tags. Replay-only: not fed back '
    'into prompt/history assembly.';
