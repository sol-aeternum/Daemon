"""Uncalled, in-memory artifact codec; not a registry, store or access check.

All identity fields are authenticated inside Fernet, not used as authorization.
The caller must supply a trusted expected binding after independent authorization.
No filesystem, settings, telemetry, publication or key-management side effects.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import cast

from cryptography.fernet import Fernet, InvalidToken

MAX_CONTENT_BYTES = 8 * 1024 * 1024
MAX_ENCODED_CONTENT_BYTES = 4 * ((MAX_CONTENT_BYTES + 2) // 3)
# The fixed fields, three 128-character ASCII IDs and generation fit in 1024.
MAX_ENVELOPE_BYTES = MAX_ENCODED_CONTENT_BYTES + 1024
# Fernet: version + timestamp + IV + PKCS7-padded ciphertext + HMAC, base64.
MAX_TOKEN_BYTES = 4 * ((57 + 16 * (MAX_ENVELOPE_BYTES // 16 + 1) + 2) // 3)
_FORMAT = "daemon-artifact-envelope-v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_REPRESENTATIONS = frozenset({"submitted_image", "submitted_text", "generated_file"})
_FIELDS = frozenset(
    {"format", "account_id", "resource_id", "version_id", "generation", "representation", "content"}
)


class ArtifactEnvelopeError(ValueError):
    """Uniform non-content failure; never exposes parser or cipher exceptions."""

    def __init__(self) -> None:
        super().__init__("invalid_artifact_envelope")


@dataclass(frozen=True, repr=False)
class ArtifactBinding:
    account_id: str
    resource_id: str
    version_id: str
    generation: int
    representation: str

    def validate(self) -> None:
        for value in (self.account_id, self.resource_id, self.version_id):
            if type(value) is not str or _ID.fullmatch(value) is None:
                raise ArtifactEnvelopeError()
        if type(self.generation) is not int or not 0 <= self.generation <= 2**63 - 1:
            raise ArtifactEnvelopeError()
        if type(self.representation) is not str or self.representation not in _REPRESENTATIONS:
            raise ArtifactEnvelopeError()


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


class ArtifactEnvelopeCodec:
    """Explicitly inject an initialized Fernet cipher; no implicit key lookup.

    This codec accepts bytes only, and verifies the complete object before return.
    It does not validate media, ensure version uniqueness, or enforce generation
    freshness. A matching caller-supplied binding proves none of those things.
    """

    def __init__(self, cipher: Fernet) -> None:
        self._cipher = cipher

    def encode(self, binding: ArtifactBinding, content: bytes) -> bytes:
        binding.validate()
        if type(content) is not bytes or len(content) > MAX_CONTENT_BYTES:
            raise ArtifactEnvelopeError()
        envelope: dict[str, object] = {
            "format": _FORMAT,
            "account_id": binding.account_id,
            "resource_id": binding.resource_id,
            "version_id": binding.version_id,
            "generation": binding.generation,
            "representation": binding.representation,
            "content": base64.b64encode(content).decode("ascii"),
        }
        return self._cipher.encrypt(_canonical(envelope))

    def decode(self, expected: ArtifactBinding, token: bytes) -> bytes:
        expected.validate()
        if type(token) is not bytes or len(token) > MAX_TOKEN_BYTES:
            raise ArtifactEnvelopeError()
        try:
            # Fernet's decoder is permissive; admit only canonical URL-safe base64.
            raw_token = base64.b64decode(token, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw_token) != token:
                raise ArtifactEnvelopeError()
            plaintext = self._cipher.decrypt(token)
            if len(plaintext) > MAX_ENVELOPE_BYTES:
                raise ArtifactEnvelopeError()
            parsed: object = json.loads(plaintext)
            if type(parsed) is not dict:
                raise ArtifactEnvelopeError()
            envelope = cast(dict[str, object], parsed)
            if envelope.keys() != _FIELDS or envelope.get("format") != _FORMAT:
                raise ArtifactEnvelopeError()
            # Exact serialization rejects duplicate keys, whitespace, escaped IDs,
            # alternate encodings and unknown fields rather than normalizing them.
            if _canonical(envelope) != plaintext:
                raise ArtifactEnvelopeError()
            actual = ArtifactBinding(
                account_id=cast(str, envelope["account_id"]),
                resource_id=cast(str, envelope["resource_id"]),
                version_id=cast(str, envelope["version_id"]),
                generation=cast(int, envelope["generation"]),
                representation=cast(str, envelope["representation"]),
            )
            actual.validate()
            if actual != expected:
                raise ArtifactEnvelopeError()
            encoded = envelope["content"]
            if type(encoded) is not str or len(encoded) > MAX_ENCODED_CONTENT_BYTES:
                raise ArtifactEnvelopeError()
            content = base64.b64decode(encoded, validate=True)
            if (
                len(content) > MAX_CONTENT_BYTES
                or base64.b64encode(content).decode("ascii") != encoded
            ):
                raise ArtifactEnvelopeError()
            return content
        except (InvalidToken, ValueError, TypeError, RecursionError, OverflowError, binascii.Error):
            raise ArtifactEnvelopeError() from None
