CREATE UNIQUE INDEX IF NOT EXISTS uq_video_credit_refund_reference
ON video_credit_transactions(reference_id)
WHERE type = 'refund' AND reference_id IS NOT NULL;
