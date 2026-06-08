from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class EncryptionKeyMissing(ValueError):
    pass


class ContentEncryption:
    def __init__(self, key: str | None = None) -> None:
        resolved = key if key is not None else os.environ.get("DAEMON_ENCRYPTION_KEY")
        if not resolved:
            raise EncryptionKeyMissing(
                "DAEMON_ENCRYPTION_KEY is required. Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )

        if isinstance(resolved, str):
            resolved_bytes = resolved.encode()
        else:
            resolved_bytes = resolved

        if len(resolved_bytes) < 43:
            raise EncryptionKeyMissing(
                "DAEMON_ENCRYPTION_KEY is too short. Fernet requires a "
                "32-byte url-safe base64-encoded key (~43 characters)."
            )

        try:
            self._cipher: Fernet = Fernet(resolved_bytes)
        except Exception as e:
            raise EncryptionKeyMissing(
                f"DAEMON_ENCRYPTION_KEY is not a valid Fernet key: {e}"
            ) from e

    def encrypt(self, plaintext: str) -> str:
        try:
            encrypted_bytes = self._cipher.encrypt(plaintext.encode())
        except Exception as e:
            raise RuntimeError(f"Encryption failed: {e}") from e
        return encrypted_bytes.decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            decrypted_bytes = self._cipher.decrypt(ciphertext.encode())
        except InvalidToken as e:
            raise ValueError(
                "Invalid ciphertext: decryption failed (wrong key or corrupted data)"
            ) from e
        return decrypted_bytes.decode()
