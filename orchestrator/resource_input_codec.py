"""Bounded C1 received representations and owner-approved stable lookup identity.

Explicit key/limits only, no settings, files, extraction or request rewriting.
PNG support is deliberately a strict passive qualification profile, not all media.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from orchestrator.artifact_envelope import MAX_CONTENT_BYTES
from orchestrator.resource_store import ResourceError, _pairs
from orchestrator.tasks.inputs import canonical_json


@dataclass(frozen=True, slots=True)
class InputLimits:
    """Required finite qualification budgets; never production quota defaults."""

    request_bytes: int
    items: int
    total_bytes: int
    item_bytes: int
    raster_bytes: int
    pixels: int
    concurrent: int
    per_account: int

    def __post_init__(self) -> None:
        if (
            any(
                type(v) is not int or v <= 0
                for v in (
                    self.request_bytes,
                    self.items,
                    self.total_bytes,
                    self.item_bytes,
                    self.raster_bytes,
                    self.pixels,
                    self.concurrent,
                    self.per_account,
                )
            )
            or self.request_bytes > 32 * 1024 * 1024
            or self.item_bytes > MAX_CONTENT_BYTES
            or self.total_bytes > self.request_bytes
            or self.items > 100
            or self.per_account > self.concurrent
        ):
            raise ResourceError("invalid_limits")


class InputLookup:
    """No secret values in repr/logs; encryption-key rotation remains unsupported."""

    def __init__(self, encryption_key: bytes) -> None:
        try:
            if type(encryption_key) is not bytes or len(encryption_key) != 44:
                raise ValueError
            raw = base64.b64decode(encryption_key, altchars=b"-_", validate=True)
            if len(raw) != 32 or base64.urlsafe_b64encode(raw) != encryption_key:
                raise ValueError
        except (ValueError, TypeError):
            raise ResourceError("invalid_key") from None
        self._key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"daemon.resource-input.lookup.hkdf.v1",
            info=b"daemon.resource-input.lookup.key.v1",
        ).derive(raw)

    def identity(self, account: UUID) -> bytes:
        if not isinstance(account, UUID):
            raise ResourceError("invalid")
        return hmac.digest(
            self._key, b"daemon.resource-input.lookup.identity.v1\x00" + account.bytes, "sha256"
        )

    def digest(self, account: UUID, key: str | None) -> bytes | None:
        self.identity(account)
        if key is None:
            return None
        if type(key) is not str or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key) is None:
            raise ResourceError("invalid_key")
        raw = key.encode("utf-8")
        return hmac.digest(
            self._key,
            b"daemon.resource-input.lookup.request.v1\x00"
            + account.bytes
            + len(raw).to_bytes(8, "big")
            + raw,
            "sha256",
        )


@dataclass(frozen=True, slots=True, repr=False)
class ReceivedInput:
    representation: str
    kind: str
    mime: str
    metadata: dict[str, Any] = field(repr=False)
    content: bytes | None = field(repr=False)

    @property
    def measured_bytes(self) -> int | None:
        return len(self.content) if self.content is not None else None

    @property
    def sha256(self) -> str | None:
        return hashlib.sha256(self.content).hexdigest() if self.content is not None else None


def validate_png(content: bytes, limits: InputLimits) -> None:
    """Complete bounded noninterlaced 8-bit grayscale/RGB(+alpha) PNG profile.

    Only IHDR/contiguous IDAT/IEND are admitted. No palette, metadata, APNG or
    interlace claim. Exact bounded decoded scanlines are checked, never rendered.
    """
    if len(content) > limits.item_bytes or content[:8] != b"\x89PNG\r\n\x1a\n":
        raise ResourceError("invalid_image")
    offset, phase, expected, row_bytes = 8, 0, 0, 0
    decoder = zlib.decompressobj()
    decoded = bytearray()
    try:
        while offset < len(content):
            if len(content) - offset < 12:
                raise ValueError
            size = struct.unpack_from(">I", content, offset)[0]
            if size > len(content) - offset - 12:
                raise ValueError
            kind = content[offset + 4 : offset + 8]
            end = offset + 8 + size
            payload = content[offset + 8 : end]
            crc = zlib.crc32(payload, zlib.crc32(kind))
            if crc != struct.unpack_from(">I", content, end)[0]:
                raise ValueError
            if kind == b"IHDR" and phase == 0 and size == 13:
                width, height, depth, color, compression, filtering, interlace = struct.unpack(
                    ">IIBBBBB", payload
                )
                channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
                if (
                    not width
                    or not height
                    or width * height > limits.pixels
                    or depth != 8
                    or channels is None
                    or compression
                    or filtering
                    or interlace
                ):
                    raise ValueError
                row_bytes = 1 + width * channels
                expected = row_bytes * height
                if expected > limits.raster_bytes:
                    raise ValueError  # BEFORE any decompressed allocation.
                phase = 1
            elif kind == b"IDAT" and phase in (1, 2):
                if decoder.eof and size:
                    raise ValueError
                decoded.extend(decoder.decompress(payload, expected - len(decoded) + 1))
                if len(decoded) > expected or decoder.unconsumed_tail or decoder.unused_data:
                    raise ValueError
                phase = 2
            elif kind == b"IEND" and phase == 2 and size == 0:
                if (
                    not decoder.eof
                    or len(decoded) != expected
                    or end + 4 != len(content)
                    or any(decoded[i] > 4 for i in range(0, expected, row_bytes))
                ):
                    raise ValueError
                return
            else:
                raise ValueError
            offset = end + 4
    except (ValueError, struct.error, zlib.error):
        raise ResourceError("invalid_image") from None
    raise ResourceError("invalid_image")


def received_inputs(canonical: str, limits: InputLimits) -> tuple[ReceivedInput, ...]:
    """Read the native canonical request unchanged; never reconstruct its identity."""
    if type(canonical) is not str or len(canonical) > limits.request_bytes:
        raise ResourceError("too_large")
    try:
        if len(canonical.encode("utf-8")) > limits.request_bytes:
            raise ResourceError("too_large")
        request = json.loads(canonical, object_pairs_hook=_pairs)
        if type(request) is not dict or canonical_json(request) != canonical:
            raise ResourceError("invalid")
        attachments = request.get("attachments", [])
        if type(attachments) is not list or len(attachments) > limits.items:
            raise ResourceError("too_large")
        result, total = [], 0
        for item in attachments:
            if type(item) is not dict:
                raise ResourceError("invalid")
            metadata = {k: item[k] for k in ("name", "mime_type", "size") if k in item}
            if len(canonical_json(metadata).encode("utf-8")) > 16384:
                raise ResourceError("too_large")
            kind, content = item.get("kind"), None
            if kind == "image":
                url = item.get("data_url")
                prefix = "data:image/png;base64,"
                if type(url) is not str or not url.startswith(prefix):
                    raise ResourceError("unsupported_image")
                raw = url[len(prefix) :]
                if len(raw) > 4 * ((limits.item_bytes + 2) // 3):
                    raise ResourceError("too_large")
                padding = len(raw) - len(raw.rstrip("="))
                if padding > 2 or len(raw) % 4 or (len(raw) // 4 * 3 - padding) > limits.item_bytes:
                    raise ResourceError("too_large")
                content = base64.b64decode(raw, validate=True)
                if base64.b64encode(content).decode("ascii") != raw:
                    raise ResourceError("invalid_image")
                validate_png(content, limits)
                representation, stored_kind, mime = "submitted_image", "image", "image/png"
            elif kind == "text":
                text = item.get("text_content")
                if type(text) is not str or len(text) > limits.item_bytes:
                    raise ResourceError("too_large")
                content = text.encode("utf-8")
                representation, stored_kind, mime = "submitted_text", "text", "text/plain"
            elif kind == "binary" and not any(k in item for k in ("data_url", "text_content")):
                representation, stored_kind, mime = (
                    "metadata_only",
                    "file",
                    "application/octet-stream",
                )
            else:
                raise ResourceError("invalid")
            size = len(content) if content is not None else 0
            total += size
            if size > limits.item_bytes or total > limits.total_bytes:
                raise ResourceError("too_large")
            result.append(ReceivedInput(representation, stored_kind, mime, metadata, content))
        return tuple(result)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ResourceError):
            raise
        raise ResourceError("invalid") from None
