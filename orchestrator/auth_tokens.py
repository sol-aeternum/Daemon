from __future__ import annotations

import hashlib
import hmac
import secrets
import string

_ENROLLMENT_DIGITS = string.digits


def generate_token() -> str:
    """Generate a high-entropy opaque token using CSPRNG.

    Uses secrets.token_urlsafe(32) which produces 256 bits of entropy
    encoded as 43 base64url characters.
    """
    return secrets.token_urlsafe(32)


def generate_setup_token() -> str:
    """Generate a setup token identical to generate_token().

    Setup tokens use the same 256-bit CSPRNG construction as access/refresh
    tokens. The different name documents intent only.
    """
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Compute deterministic SHA-256 hash of a plaintext token.

    Returns a 64-character hex-encoded hash. The same token always produces
    the same hash, enabling constant-time lookup in the database.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, expected_hash: str) -> bool:
    """Verify a token against its stored hash using constant-time comparison.

    Computes the SHA-256 hash of the presented token and compares it against
    the expected hash using hmac.compare_digest to prevent timing attacks.
    """
    return hmac.compare_digest(hash_token(token), expected_hash)


def generate_enrollment_code() -> str:
    """Generate a human-readable 8-digit enrollment code formatted as NNNN-NNNN.

    Returns an 8-digit decimal code with a hyphen separator for readability.
    The code provides ~26.6 bits of entropy (8 random decimal digits).
    Consecutive codes differ with extremely high probability.
    """
    digits = "".join(secrets.choice(_ENROLLMENT_DIGITS) for _ in range(8))
    return f"{digits[:4]}-{digits[4:]}"


def normalize_enrollment_code(code: str) -> str:
    """Normalize an enrollment code to exactly 8 digits.

    Accepts common pasted forms:
    - '12345678'
    - '1234-5678'
    - '1234 5678'
    - '  1234-5678  '

    Returns exactly 8 decimal digits.
    Raises ValueError if the input cannot be normalized.
    """
    stripped = code.strip()
    digits_only = stripped.replace("-", "").replace(" ", "")
    if len(digits_only) != 8:
        raise ValueError(f"Enrollment code must contain exactly 8 digits, got: {code!r}")
    if not digits_only.isdecimal():
        raise ValueError(f"Enrollment code must contain only digits, got: {code!r}")
    return digits_only


def hash_enrollment_code(code: str, pepper: str | bytes) -> str:
    """Compute an HMAC-SHA256 verifier for an enrollment code with a pepper key.

    The code is normalized to 8 digits before HMAC computation.
    The verifier changes if either the code or the pepper changes.

    Args:
        code: Enrollment code in any common format.
        pepper: Shared secret key (str or bytes). Must not be stored in the DB.
    """
    normalized = normalize_enrollment_code(code)
    if isinstance(pepper, str):
        pepper_bytes = pepper.encode("utf-8")
    else:
        pepper_bytes = pepper
    return hmac.new(pepper_bytes, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_enrollment_code(code: str, pepper: str | bytes, expected_verifier: str) -> bool:
    """Verify an enrollment code against a stored HMAC-SHA256 verifier.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        code: Presented enrollment code.
        pepper: Shared secret key used when computing the original verifier.
        expected_verifier: Stored HMAC-SHA256 hex digest.
    """
    return hmac.compare_digest(hash_enrollment_code(code, pepper), expected_verifier)
