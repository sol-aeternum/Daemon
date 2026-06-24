from __future__ import annotations

import logging
import os
import asyncio
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


ENCRYPTION_OPERATIONS_FAILED_TOTAL_KEY = "daemon:metrics:encryption_operations_failed_total"


class SharedEncryptionFailureCounter(Protocol):
    async def incr(self, key: str) -> object: ...


_encryption_operations_failed_total = 0
_shared_encryption_failure_counter: SharedEncryptionFailureCounter | None = None


async def _record_shared_encryption_failure(
    counter: SharedEncryptionFailureCounter,
) -> None:
    try:
        await counter.incr(ENCRYPTION_OPERATIONS_FAILED_TOTAL_KEY)
    except Exception:
        logger.warning("Failed to record shared encryption failure metric", exc_info=True)


def set_shared_encryption_failure_counter(
    counter: SharedEncryptionFailureCounter | None,
) -> None:
    global _shared_encryption_failure_counter
    _shared_encryption_failure_counter = counter


def _record_encryption_failure() -> None:
    global _encryption_operations_failed_total
    _encryption_operations_failed_total += 1
    if _shared_encryption_failure_counter is None:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_record_shared_encryption_failure(_shared_encryption_failure_counter))


def get_encryption_operations_failed_total() -> int:
    return _encryption_operations_failed_total


def reset_encryption_metrics_for_tests() -> None:
    global _encryption_operations_failed_total
    _encryption_operations_failed_total = 0
    set_shared_encryption_failure_counter(None)


class EncryptionInitError(ValueError):
    pass


class EncryptionKeyMissing(EncryptionInitError):
    pass


class ContentEncryption:
    def __init__(self, key: str | None = None) -> None:
        resolved = key if key is not None else os.environ.get("DAEMON_ENCRYPTION_KEY")
        if not resolved:
            _record_encryption_failure()
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
            _record_encryption_failure()
            raise EncryptionKeyMissing(
                "DAEMON_ENCRYPTION_KEY is too short. Fernet requires a "
                "32-byte url-safe base64-encoded key (~43 characters)."
            )

        try:
            self._cipher: Fernet = Fernet(resolved_bytes)
        except Exception as e:
            _record_encryption_failure()
            raise EncryptionKeyMissing(
                f"DAEMON_ENCRYPTION_KEY is not a valid Fernet key: {e}"
            ) from e

    def encrypt(self, plaintext: str) -> str:
        try:
            encrypted_bytes = self._cipher.encrypt(plaintext.encode())
        except Exception as e:
            _record_encryption_failure()
            raise RuntimeError(f"Encryption failed: {e}") from e
        return encrypted_bytes.decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            decrypted_bytes = self._cipher.decrypt(ciphertext.encode())
        except InvalidToken as e:
            _record_encryption_failure()
            raise ValueError(
                "Invalid ciphertext: decryption failed (wrong key or corrupted data)"
            ) from e
        except Exception as e:
            _record_encryption_failure()
            raise RuntimeError(f"Decryption failed: {e}") from e
        return decrypted_bytes.decode()
