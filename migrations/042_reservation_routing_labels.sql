-- Migration: 042_reservation_routing_labels
-- Routing labels on spend reservations (optional work O7, authorized 2 October 2026).
--
-- Scope and safety anchors:
--   - Additive only: two nullable columns on entitlement_reservations. No data is
--     rewritten, no existing column, constraint or index changes, and existing rows
--     keep NULL (the labels were never recorded for them).
--   - Unit-economics labels like provider/model/route_id: never interpreted by
--     admission, settlement or recovery, so they cannot change what is charged.
--   - workload_profile is the routing profile the reservation was dispatched under
--     (routine, research, reasoning, background, council); reasoning_effort is the
--     effort actually sent to the provider, if any. Both are short identifiers,
--     never user content.

ALTER TABLE entitlement_reservations
    ADD COLUMN IF NOT EXISTS workload_profile TEXT
        CHECK (workload_profile IS NULL OR char_length(workload_profile) BETWEEN 1 AND 32),
    ADD COLUMN IF NOT EXISTS reasoning_effort TEXT
        CHECK (reasoning_effort IS NULL OR char_length(reasoning_effort) BETWEEN 1 AND 16);
