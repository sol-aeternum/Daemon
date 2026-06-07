"""Tests for memory encryption."""

import pytest
from cryptography.fernet import Fernet

from orchestrator.memory.encryption import ContentEncryption, EncryptionKeyMissing


@pytest.fixture
def valid_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def encryption(valid_key):
    return ContentEncryption(valid_key)


def test_encrypt_decrypt(encryption):
    plaintext = "Hello, World!"
    encrypted = encryption.encrypt(plaintext)
    assert encrypted != plaintext
    decrypted = encryption.decrypt(encrypted)
    assert decrypted == plaintext


def test_empty_string(encryption):
    encrypted = encryption.encrypt("")
    decrypted = encryption.decrypt(encrypted)
    assert decrypted == ""


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("DAEMON_ENCRYPTION_KEY", raising=False)
    with pytest.raises(EncryptionKeyMissing, match="DAEMON_ENCRYPTION_KEY is required"):
        ContentEncryption(None)


def test_empty_key_raises(monkeypatch):
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", "")
    with pytest.raises(EncryptionKeyMissing, match="DAEMON_ENCRYPTION_KEY is required"):
        ContentEncryption(None)


def test_short_key_raises(monkeypatch):
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", "tooshort")
    with pytest.raises(EncryptionKeyMissing, match="too short"):
        ContentEncryption(None)


def test_invalid_fernet_key_raises(monkeypatch):
    monkeypatch.setenv("DAEMON_ENCRYPTION_KEY", "A" * 50)
    with pytest.raises(EncryptionKeyMissing, match="not a valid Fernet key"):
        ContentEncryption(None)


def test_explicit_key_takes_precedence(monkeypatch, valid_key):
    monkeypatch.delenv("DAEMON_ENCRYPTION_KEY", raising=False)
    enc = ContentEncryption(valid_key)
    assert enc.encrypt("x") != "x"


def test_encrypt_raises_on_failure(encryption, monkeypatch):
    class Broken:
        def encrypt(self, *_):
            raise OSError("boom")

    monkeypatch.setattr(encryption, "_cipher", Broken())
    with pytest.raises(RuntimeError, match="Encryption failed"):
        encryption.encrypt("x")


def test_decrypt_invalid_ciphertext_raises(encryption):
    bogus = "gAAAAA-this-is-not-real-fernet-ciphertext"
    with pytest.raises(ValueError, match="Invalid ciphertext"):
        encryption.decrypt(bogus)


def test_no_silent_plaintext_when_key_missing(monkeypatch):
    monkeypatch.delenv("DAEMON_ENCRYPTION_KEY", raising=False)
    with pytest.raises(EncryptionKeyMissing):
        enc = ContentEncryption(None)
        enc.encrypt("Secret")
