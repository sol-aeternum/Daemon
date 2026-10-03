-- Migration: 043_inference_route_attestations
-- ZDR attestation records for monitored inference route approvals (operator
-- decision, 3 October 2026).
--
-- Scope and safety anchors:
--   - Additive only: one new append-only table. Nothing existing changes.
--   - One row per monitored route per check. A route is admitted only while its
--     approved baseline (baseline_sha256) has a recent 'attested' row and has never
--     been 'revoked'; 'check_failed' records an unreadable check and revokes nothing.
--   - Rows hold route identifiers, outcomes, short reason codes, observed public
--     policy values and a hash of the fetched public metadata. No user content,
--     credentials or request data.

CREATE TABLE IF NOT EXISTS inference_route_attestations (
    id BIGSERIAL PRIMARY KEY,
    route_id TEXT NOT NULL CHECK (char_length(route_id) BETWEEN 1 AND 128),
    baseline_sha256 TEXT NOT NULL CHECK (baseline_sha256 ~ '^[0-9a-f]{64}$'),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    outcome TEXT NOT NULL CHECK (outcome IN ('attested', 'revoked', 'check_failed')),
    reasons TEXT[] NOT NULL DEFAULT '{}',
    observed JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_sha256 TEXT CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_inference_route_attestations_route
    ON inference_route_attestations (route_id, baseline_sha256, checked_at DESC);
