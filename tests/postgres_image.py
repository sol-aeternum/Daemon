"""Pinned pgvector qualification for the owned PostgreSQL fixture.

Registry index -> linux/amd64 manifest -> config hashes were verified against
the existing pgvector pg16 pin. See docs/OWNED_TEST_FIXTURES.md.
This module inspects only; it never pulls, substitutes or retries an image.
"""

from __future__ import annotations


INDEX_DIGEST = "sha256:33198da2828a14c30348d2ccb4750833d5ed9a44c88d840a0e523d7417120337"
AMD64_MANIFEST_DIGEST = "sha256:16e62164a405447dca191079a924ee5b8a9dbf04fe53128701ffbea857b37782"
CONFIG_DIGEST = "sha256:4c0c0efbd40e8ff42363074292654a7831cac506fc01a1c89627f1b1040ba341"
IMAGE_REFERENCE = f"pgvector/pgvector@{INDEX_DIGEST}"
PLATFORM = "linux/amd64"


def validate_cached_image(data: object) -> str:
    """Fail closed on unknown identities, metadata shapes or platform selection."""
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("Ambiguous pinned PostgreSQL image inspection")
    item = data[0]
    image = item.get("Id")
    digests = item.get("RepoDigests")
    allowed_references = {IMAGE_REFERENCE, f"docker.io/pgvector/pgvector@{INDEX_DIGEST}"}
    if (
        not isinstance(image, str)
        or image not in {INDEX_DIGEST, CONFIG_DIGEST}
        or item.get("Os") != "linux"
        or item.get("Architecture") != "amd64"
        or not isinstance(digests, list)
        or not digests
        or not all(isinstance(ref, str) and ref in allowed_references for ref in digests)
    ):
        raise ValueError("Pinned PostgreSQL image identity/platform mismatch")
    # Docker's containerd store exposes the index Id; classic stores expose the
    # amd64 config Id. Both are bound by the verified index/manifest/config chain.
    return image
