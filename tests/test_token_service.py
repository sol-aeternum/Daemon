from __future__ import annotations

import pytest

from orchestrator.auth_tokens import (
    generate_enrollment_code,
    generate_setup_token,
    generate_token,
    hash_enrollment_code,
    hash_token,
    normalize_enrollment_code,
    verify_enrollment_code,
    verify_token,
)


class TestGenerateToken:
    def test_token_is_43_chars(self):
        token = generate_token()
        assert len(token) == 43

    def test_token_is_base64url_safe(self):
        token = generate_token()
        assert all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token
        )

    def test_consecutive_tokens_differ(self):
        tokens = [generate_token() for _ in range(10)]
        assert len(set(tokens)) == len(tokens)


class TestHashToken:
    def test_hash_is_64_hex_chars(self):
        token = "test-token-abc123"
        h = hash_token(token)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_is_deterministic(self):
        token = "consistent-token-xyz"
        h1 = hash_token(token)
        h2 = hash_token(token)
        assert h1 == h2

    def test_different_tokens_different_hashes(self):
        h1 = hash_token("token-one")
        h2 = hash_token("token-two")
        assert h1 != h2


class TestVerifyToken:
    def test_verify_returns_true_for_correct_token(self):
        token = "my-secret-token"
        expected_hash = hash_token(token)
        assert verify_token(token, expected_hash) is True

    def test_verify_returns_false_for_wrong_token(self):
        token = "my-secret-token"
        expected_hash = hash_token(token)
        assert verify_token("wrong-token", expected_hash) is False

    def test_verify_returns_false_for_tampered_hash(self):
        token = "my-secret-token"
        tampered_hash = hash_token(token)[:-4] + "0000"
        assert verify_token(token, tampered_hash) is False


class TestGenerateSetupToken:
    def test_setup_token_is_43_chars(self):
        token = generate_setup_token()
        assert len(token) == 43

    def test_setup_token_is_base64url_safe(self):
        token = generate_setup_token()
        assert all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token
        )

    def test_setup_token_differs_from_access_token(self):
        access = generate_token()
        setup = generate_setup_token()
        assert access != setup


class TestGenerateEnrollmentCode:
    def test_enrollment_code_format(self):
        code = generate_enrollment_code()
        assert len(code) == 9
        assert code[4] == "-"
        assert code[:4].isdigit()
        assert code[5:].isdigit()

    def test_consecutive_codes_differ(self):
        codes = [generate_enrollment_code() for _ in range(20)]
        assert len(set(codes)) == len(codes)


class TestNormalizeEnrollmentCode:
    def test_plain_8_digits(self):
        assert normalize_enrollment_code("12345678") == "12345678"

    def test_hyphen_separated(self):
        assert normalize_enrollment_code("1234-5678") == "12345678"

    def test_space_separated(self):
        assert normalize_enrollment_code("1234 5678") == "12345678"

    def test_with_whitespace(self):
        assert normalize_enrollment_code("  1234-5678  ") == "12345678"

    def test_raises_on_too_short(self):
        with pytest.raises(ValueError, match="exactly 8 digits"):
            _: str = normalize_enrollment_code("1234567")

    def test_raises_on_too_long(self):
        with pytest.raises(ValueError, match="exactly 8 digits"):
            _: str = normalize_enrollment_code("123456789")

    def test_raises_on_non_digits(self):
        with pytest.raises(ValueError, match="only digits"):
            _: str = normalize_enrollment_code("1234-ABCD")


class TestHashEnrollmentCode:
    def test_hash_is_64_hex_chars(self):
        v = hash_enrollment_code("12345678", "test-pepper")
        assert len(v) == 64
        assert all(c in "0123456789abcdef" for c in v)

    def test_same_code_and_pepper_produces_same_hash(self):
        h1 = hash_enrollment_code("12345678", "pepper-one")
        h2 = hash_enrollment_code("12345678", "pepper-one")
        assert h1 == h2

    def test_different_pepper_changes_hash(self):
        h1 = hash_enrollment_code("12345678", "pepper-one")
        h2 = hash_enrollment_code("12345678", "pepper-two")
        assert h1 != h2

    def test_different_code_changes_hash(self):
        h1 = hash_enrollment_code("12345678", "pepper")
        h2 = hash_enrollment_code("87654321", "pepper")
        assert h1 != h2

    def test_accepts_bytes_pepper(self):
        v1 = hash_enrollment_code("12345678", "pepper")
        v2 = hash_enrollment_code("12345678", b"pepper")
        assert v1 == v2

    def test_normalizes_before_hashing(self):
        h1 = hash_enrollment_code("1234-5678", "pepper")
        h2 = hash_enrollment_code("12345678", "pepper")
        assert h1 == h2


class TestVerifyEnrollmentCode:
    def test_verify_returns_true_for_correct_code(self):
        code = "12345678"
        pepper = "my-secret-pepper"
        verifier = hash_enrollment_code(code, pepper)
        assert verify_enrollment_code(code, pepper, verifier) is True

    def test_verify_returns_false_for_wrong_code(self):
        pepper = "my-secret-pepper"
        verifier = hash_enrollment_code("12345678", pepper)
        assert verify_enrollment_code("87654321", pepper, verifier) is False

    def test_verify_returns_false_for_wrong_pepper(self):
        verifier = hash_enrollment_code("12345678", "correct-pepper")
        assert verify_enrollment_code("12345678", "wrong-pepper", verifier) is False

    def test_verify_accepts_bytes_pepper(self):
        code = "12345678"
        pepper_bytes = b"my-pepper"
        verifier = hash_enrollment_code(code, pepper_bytes)
        assert verify_enrollment_code(code, pepper_bytes, verifier) is True

    def test_verify_normalizes_before_comparing(self):
        pepper = "pepper"
        verifier = hash_enrollment_code("1234-5678", pepper)
        assert verify_enrollment_code("12345678", pepper, verifier) is True
