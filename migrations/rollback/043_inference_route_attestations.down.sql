-- Rollback: 043_inference_route_attestations
-- Drops the attestation records. Monitored routes then fail closed until the table
-- is restored, so roll the policy back to expiring approvals first.

DROP INDEX IF EXISTS idx_inference_route_attestations_route;
DROP TABLE IF EXISTS inference_route_attestations;
