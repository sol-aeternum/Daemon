from __future__ import annotations

import logging
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class ContentEncryption:
    def __init__(self, key: str | None = None) -> None:
        self.key = key or os.environ.get("DAEMON_ENCRYPTION_KEY", "")
        self._cipher: Fernet | None = None

        if self.key:
            try:
                self._cipher = Fernet(
                    self.key.encode() if isinstance(self.key, str) else self.key
                )
            except Exception as e:
                logger.warning(
                    f"Failed to initialize encryption cipher: {e}. "
                    "Content will be stored in plaintext."
                )
                self._cipher = None
        else:
            logger.warning(
                "DAEMON_ENCRYPTION_KEY not set. Content will be stored in plaintext."
            )

    def encrypt(self, plaintext: str) -> str:
        if not self._cipher:
            return plaintext

        try:
            encrypted_bytes = self._cipher.encrypt(plaintext.encode())
            return encrypted_bytes.decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}. Returning plaintext.")
            return plaintext

    def decrypt(self, ciphertext: str) -> str:
        if not self._cipher:
            return ciphertext

        try:
            decrypted_bytes = self._cipher.decrypt(ciphertext.encode())
            return decrypted_bytes.decode()
        except InvalidToken:
            raise ValueError(
                "Invalid ciphertext: decryption failed (wrong key or corrupted data)"
            )
        except Exception as e:
            logger.error(f"Decryption failed: {e}. Returning ciphertext as-is.")
            return ciphertext
