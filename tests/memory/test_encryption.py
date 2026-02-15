"""Tests for memory encryption."""

import pytest
from cryptography.fernet import Fernet
from orchestrator.memory.encryption import ContentEncryption


@pytest.fixture
def encryption():
    # Generate a valid Fernet key
    key = Fernet.generate_key().decode()
    return ContentEncryption(key)


def test_encrypt_decrypt(encryption):
    plaintext = "Hello, World!"
    encrypted = encryption.encrypt(plaintext)
    decrypted = encryption.decrypt(encrypted)
    assert decrypted == plaintext


def test_empty_string(encryption):
    encrypted = encryption.encrypt("")
    decrypted = encryption.decrypt(encrypted)
    assert decrypted == ""


def test_no_encryption_when_no_key(monkeypatch):
    monkeypatch.delenv("DAEMON_ENCRYPTION_KEY", raising=False)
    encryption = ContentEncryption(None)
    plaintext = "Secret"
    encrypted = encryption.encrypt(plaintext)
    # When no key, encrypt returns plaintext
    assert encrypted == plaintext
