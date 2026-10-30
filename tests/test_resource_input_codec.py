"""Synthetic key identities, exact representations and bounded passive PNG checks."""

import base64
import json
import struct
import zlib
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

from orchestrator.resource_input_codec import (
    InputLimits,
    InputLookup,
    received_inputs,
    validate_png,
)
from orchestrator.resource_store import ResourceError
from orchestrator.tasks.inputs import canonical_json

LIMITS = InputLimits(65536, 8, 32768, 16384, 32768, 4096, 4, 2)
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/artifact_continuity/contract.json").read_text()
)
PNG = base64.b64decode(FIXTURE["inputs"][0]["content_base64"])


def attachments():
    return [
        {
            "kind": "image",
            "data_url": "data:image/png;base64," + base64.b64encode(PNG).decode(),
            "name": "tiny.png",
        },
        {
            "kind": "text",
            "text_content": "  exact\n... (truncated)\n",
            "name": "part.txt",
            "size": 999999,
        },
        {"kind": "binary", "name": "not-received.bin", "size": 999999},
    ]


def test_lookup_domain_account_key_separation_and_strict_key_encoding():
    key, a, b = Fernet.generate_key(), uuid4(), uuid4()
    first, same, different = InputLookup(key), InputLookup(key), InputLookup(Fernet.generate_key())
    assert first.identity(a) == same.identity(a)
    assert first.digest(a, "key") == same.digest(a, "key")
    assert (
        len(
            {
                first.identity(a),
                first.identity(b),
                first.digest(a, "key"),
                first.digest(a, "key2"),
                first.digest(b, "key"),
                different.identity(a),
            }
        )
        == 6
    )
    assert first.digest(a, None) is None
    for invalid in (b"bad", key + b"=", key.rstrip(b"="), b"!" * 44):
        with pytest.raises(ResourceError):
            InputLookup(invalid)
    for invalid in ("", "a" * 129, "private key", "\u00e9"):
        with pytest.raises(ResourceError):
            first.digest(a, invalid)


def test_received_exact_text_metadata_only_and_complete_png():
    values = received_inputs(canonical_json({"attachments": attachments()}), LIMITS)
    assert values[0].content == PNG
    assert values[1].content == b"  exact\n... (truncated)\n"
    assert values[1].measured_bytes != values[1].metadata["size"]
    assert values[2].content is values[2].measured_bytes is values[2].sha256 is None
    assert "not-received" not in repr(values)


def chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def image(*, width=1, height=1, depth=8, color=4, interlace=0, rows=b"\x00\x00\xff", extra=b""):
    return (
        PNG[:8]
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))
        + chunk(b"IDAT", zlib.compress(rows))
        + extra
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    "bad",
    [
        PNG[:8],
        PNG[:-1],
        PNG + b"trailing",
        PNG[:25] + b"bad" + PNG[28:],
        image(width=0),
        image(width=2**31),
        image(depth=16),
        image(color=3),
        image(interlace=1),
        image(rows=b"\x05\x00\xff"),
        image(rows=b"\x00\x00"),
        image(rows=b"x" * 100000),
        image(extra=chunk(b"acTL", b"animation")),
        image(extra=chunk(b"IDAT", b"trailing-stream")),
    ],
)
def test_png_profile_rejects_corruption_bombs_and_unsupported_before_render(bad):
    with pytest.raises(ResourceError, match="invalid_image"):
        validate_png(bad, LIMITS)


def test_png_dimensions_fail_before_zlib_output_allocation(monkeypatch):
    class NoDecompress:
        def decompress(self, *args):
            pytest.fail("decompression attempted past dimension limit")

    monkeypatch.setattr(zlib, "decompressobj", NoDecompress)
    with pytest.raises(ResourceError):
        validate_png(image(width=1024, height=1024), LIMITS)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a[0].update(data_url="https://example.invalid/private.png"),
        lambda a: a[0].update(data_url="data:image/jpeg;base64,AA=="),
        lambda a: a[0].update(data_url="data:image/png;base64,invalid"),
        lambda a: a[1].update(text_content="x" * 17000),
        lambda a: a[2].update(data_url="received bytes must not disappear"),
        lambda a: a.extend([a[2]] * 10),
    ],
)
def test_invalid_or_unbounded_request_refused(mutate):
    values = attachments()
    mutate(values)
    with pytest.raises(ResourceError):
        received_inputs(canonical_json({"attachments": values}), LIMITS)


def test_limits_are_required_finite_and_no_bool_or_oversized_request():
    for field, value in (("items", True), ("pixels", 0), ("concurrent", -1)):
        with pytest.raises(ResourceError):
            replace(LIMITS, **{field: value})
    with pytest.raises(ResourceError):
        received_inputs("x" * (LIMITS.request_bytes + 1), LIMITS)
    with pytest.raises(ResourceError):
        received_inputs('{"attachments":[],"attachments":[]}', LIMITS)
