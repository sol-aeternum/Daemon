"""Bind already-burned proofs in the successful claim transaction, never by email history."""

from __future__ import annotations

from uuid import UUID

from orchestrator.services.identity.account_service import SupportsIdentityQueries
from orchestrator.services.identity.email_challenge import EmailChallengeInvalid, EmailChallengeRow
from orchestrator.services.identity.google_verifier import GoogleNonceInvalid, GoogleNonceRow


async def lock_consumed_email_challenge(
    conn: SupportsIdentityQueries, proof: EmailChallengeRow
) -> None:
    if proof.consumed_at is None:
        raise EmailChallengeInvalid("challenge is not consumed")
    found = await conn.fetchval(
        """
        SELECT id FROM email_challenges
        WHERE id = $1 AND consumed_at = $2 AND normalized_email = $3
          AND claimed_user_id IS NULL
        FOR UPDATE
        """,
        proof.id,
        proof.consumed_at,
        proof.normalized_email,
    )
    if found is None:
        raise EmailChallengeInvalid("consumed challenge no longer claimable")


async def attribute_email_challenge(
    conn: SupportsIdentityQueries, proof: EmailChallengeRow, user_id: UUID
) -> None:
    found = await conn.fetchval(
        """
        UPDATE email_challenges SET claimed_user_id = $4
        WHERE id = $1 AND consumed_at = $2 AND normalized_email = $3
          AND claimed_user_id IS NULL
        RETURNING id
        """,
        proof.id,
        proof.consumed_at,
        proof.normalized_email,
        user_id,
    )
    if found is None:
        raise EmailChallengeInvalid("consumed challenge no longer claimable")


async def lock_consumed_google_nonce(conn: SupportsIdentityQueries, proof: GoogleNonceRow) -> None:
    if proof.consumed_at is None:
        raise GoogleNonceInvalid("nonce is not consumed")
    found = await conn.fetchval(
        """
        SELECT id FROM google_nonce_challenges
        WHERE id = $1 AND consumed_at = $2 AND nonce_verifier_hash = $3
          AND user_id_proposed IS NOT DISTINCT FROM $4 AND claimed_user_id IS NULL
        FOR UPDATE
        """,
        proof.id,
        proof.consumed_at,
        proof.nonce_verifier_hash,
        proof.user_id_proposed,
    )
    if found is None:
        raise GoogleNonceInvalid("consumed nonce no longer claimable")


async def attribute_google_nonce(
    conn: SupportsIdentityQueries, proof: GoogleNonceRow, user_id: UUID
) -> None:
    found = await conn.fetchval(
        """
        UPDATE google_nonce_challenges SET claimed_user_id = $4
        WHERE id = $1 AND consumed_at = $2 AND nonce_verifier_hash = $3
          AND claimed_user_id IS NULL
        RETURNING id
        """,
        proof.id,
        proof.consumed_at,
        proof.nonce_verifier_hash,
        user_id,
    )
    if found is None:
        raise GoogleNonceInvalid("consumed nonce no longer claimable")
