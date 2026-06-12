"""Tests for memory encryption."""

from typing import cast

import pytest
from cryptography.fernet import Fernet

from orchestrator.config import Settings
from orchestrator.db import AppState
from orchestrator.memory.encryption import (
    ContentEncryption,
    EncryptionInitError,
    EncryptionKeyMissing,
    get_encryption_operations_failed_total,
    reset_encryption_metrics_for_tests,
)


@pytest.fixture(autouse=True)
def reset_encryption_metrics():
    reset_encryption_metrics_for_tests()
    yield
    reset_encryption_metrics_for_tests()


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
    with pytest.raises(EncryptionInitError, match="not a valid Fernet key"):
        ContentEncryption(None)
    assert get_encryption_operations_failed_total() == 1


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
    assert get_encryption_operations_failed_total() == 1


def test_decrypt_invalid_ciphertext_raises(encryption):
    bogus = "gAAAAA-this-is-not-real-fernet-ciphertext"
    with pytest.raises(ValueError, match="Invalid ciphertext"):
        encryption.decrypt(bogus)
    assert get_encryption_operations_failed_total() == 1


def test_decrypt_raises_on_cipher_failure(encryption, monkeypatch):
    class Broken:
        def decrypt(self, *_):
            raise OSError("boom")

    monkeypatch.setattr(encryption, "_cipher", Broken())
    with pytest.raises(RuntimeError, match="Decryption failed"):
        encryption.decrypt("x")
    assert get_encryption_operations_failed_total() == 1


def test_no_silent_plaintext_when_key_missing(monkeypatch):
    monkeypatch.delenv("DAEMON_ENCRYPTION_KEY", raising=False)
    with pytest.raises(EncryptionKeyMissing):
        enc = ContentEncryption(None)
        enc.encrypt("Secret")
    assert get_encryption_operations_failed_total() == 1


def test_startup_validation_rejects_configured_bad_key():
    from orchestrator.main import _validate_startup_config

    settings = Settings(
        daemon_environment="development",
        daemon_encryption_key="A" * 50,
    )

    with pytest.raises(EncryptionInitError, match="not a valid Fernet key"):
        _validate_startup_config(settings)
    assert get_encryption_operations_failed_total() == 1


@pytest.mark.asyncio
async def test_status_exposes_encryption_failure_metric():
    from orchestrator.routes.system import get_status
    from orchestrator.auth import AuthenticatedDevice

    try:
        ContentEncryption("A" * 50)
    except EncryptionInitError:
        pass

    result = await get_status(
        app_state=AppState(settings=Settings(daemon_environment="development")),
        auth=cast(AuthenticatedDevice, object()),
    )

    assert result["encryption_operations_failed_total"] == 1
    assert result["encryption_failure_alert"] is True
