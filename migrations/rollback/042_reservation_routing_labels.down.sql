-- Rollback for 042_reservation_routing_labels: drops the two label columns and the
-- labels recorded in them. Reservation amounts, statuses and settlements are untouched.
ALTER TABLE entitlement_reservations
    DROP COLUMN IF EXISTS reasoning_effort,
    DROP COLUMN IF EXISTS workload_profile;
