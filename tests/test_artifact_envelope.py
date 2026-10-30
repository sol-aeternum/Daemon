"""Synthetic codec checks, not storage/auth/publication qualification."""

import base64
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from orchestrator.artifact_envelope import (
    MAX_CONTENT_BYTES,
    MAX_ENVELOPE_BYTES,
    MAX_TOKEN_BYTES,
    ArtifactBinding,
    ArtifactEnvelopeCodec,
    ArtifactEnvelopeError,
)

BINDING = ArtifactBinding("account_a", "resource_a", "version_1", 7, "generated_file")
CONTENT = b"name,count\nsynthetic,2\n"


@pytest.fixture
def cipher() -> Fernet:
    return Fernet(Fernet.generate_key())


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


@pytest.mark.parametrize("representation", ["submitted_image", "submitted_text", "generated_file"])
@pytest.mark.parametrize("content", [b"", CONTENT, bytes(range(256))])
def test_roundtrip(cipher: Fernet, representation: str, content: bytes) -> None:
    codec = ArtifactEnvelopeCodec(cipher)
    binding = replace(BINDING, representation=representation)
    token = codec.encode(binding, content)
    assert codec.decode(binding, token) == content
    assert codec.encode(binding, content) != token
    assert b"account_a" not in token


def test_contract_image_text_and_pinned_csv_versions(cipher: Fernet) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/artifact_continuity/contract.json").read_text()
    )
    codec = ArtifactEnvelopeCodec(cipher)
    for item in fixture["inputs"][:2]:
        binding = ArtifactBinding(
            item["owner_id"], item["resource_id"], item["version_id"], 0, item["representation"]
        )
        content = (
            base64.b64decode(item["content_base64"], validate=True)
            if item["representation"] == "submitted_image"
            else item["submitted_text"].encode()
        )
        assert codec.decode(binding, codec.encode(binding, content)) == content
    output = fixture["output"]
    tokens = []
    bindings = []
    for version in output["versions"]:
        binding = ArtifactBinding(
            output["owner_id"], output["resource_id"], version["version_id"], 0, "generated_file"
        )
        bindings.append(binding)
        tokens.append(codec.encode(binding, version["text"].encode()))
    assert codec.decode(bindings[0], tokens[0]) == output["versions"][0]["text"].encode()
    assert codec.decode(bindings[1], tokens[1]) == output["versions"][1]["text"].encode()
    with pytest.raises(ArtifactEnvelopeError):
        codec.decode(bindings[0], tokens[1])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", "other"),
        ("resource_id", "other"),
        ("version_id", "other"),
        ("generation", 8),
        ("representation", "submitted_text"),
    ],
)
def test_every_binding_mismatch(cipher: Fernet, field: str, value: object) -> None:
    codec = ArtifactEnvelopeCodec(cipher)
    token = codec.encode(BINDING, CONTENT)
    with pytest.raises(ArtifactEnvelopeError, match="^invalid_artifact_envelope$"):
        codec.decode(replace(BINDING, **{field: value}), token)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", "../private"),
        ("resource_id", ""),
        ("version_id", "x" * 129),
        ("account_id", "é"),
        ("generation", True),
        ("generation", -1),
        ("generation", 2**63),
        ("generation", 1.0),
        ("representation", "metadata_only"),
        ("representation", []),
        ("account_id", 1),
    ],
)
def test_invalid_binding(cipher: Fernet, field: str, value: object) -> None:
    codec = ArtifactEnvelopeCodec(cipher)
    invalid = replace(BINDING, **{field: value})
    with pytest.raises(ArtifactEnvelopeError):
        codec.encode(invalid, CONTENT)
    with pytest.raises(ArtifactEnvelopeError):
        codec.decode(invalid, codec.encode(BINDING, CONTENT))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "future"),
        ("generation", True),
        ("generation", 7.0),
        ("account_id", ["account_a"]),
        ("content", 5),
        ("content", "@@@"),
        ("content", "Zh=="),
        ("content", "Zg==\n"),
        ("content", "é"),
        ("representation", "metadata_only"),
        ("extra", "secret"),
    ],
)
def test_authenticated_malformed_fields(cipher: Fernet, field: str, value: object) -> None:
    codec = ArtifactEnvelopeCodec(cipher)
    body = json.loads(cipher.decrypt(codec.encode(BINDING, CONTENT)))
    body[field] = value
    with pytest.raises(ArtifactEnvelopeError, match="^invalid_artifact_envelope$"):
        codec.decode(BINDING, cipher.encrypt(canonical(body)))


@pytest.mark.parametrize(
    "plaintext",
    [b"not json", b"[]", b"null", b"{}", b"\xff", b"[" * 1500 + b"]" * 1500],
)
def test_authenticated_invalid_structure(cipher: Fernet, plaintext: bytes) -> None:
    with pytest.raises(ArtifactEnvelopeError):
        ArtifactEnvelopeCodec(cipher).decode(BINDING, cipher.encrypt(plaintext))


def test_noncanonical_and_duplicate_json(cipher: Fernet) -> None:
    codec = ArtifactEnvelopeCodec(cipher)
    body = cipher.decrypt(codec.encode(BINDING, CONTENT))
    for invalid in (
        b" " + body,
        body.replace(b'"generation":7', b'"generation":6,"generation":7'),
        body.replace(b"account_a", b"account_\\u0061"),
        body.decode().encode("utf-16"),
    ):
        with pytest.raises(ArtifactEnvelopeError):
            codec.decode(BINDING, cipher.encrypt(invalid))


def test_corruption_wrong_key_and_token_encoding(cipher: Fernet) -> None:
    codec = ArtifactEnvelopeCodec(cipher)
    token = codec.encode(BINDING, CONTENT)
    raw = bytearray(base64.urlsafe_b64decode(token))
    raw[30] ^= 1
    for invalid in (b"", token[:-8], token + b"\n", token + b"=", base64.urlsafe_b64encode(raw)):
        with pytest.raises(ArtifactEnvelopeError):
            codec.decode(BINDING, invalid)
    with pytest.raises(ArtifactEnvelopeError):
        ArtifactEnvelopeCodec(Fernet(Fernet.generate_key())).decode(BINDING, token)


def test_maximum_and_oversized_content(cipher: Fernet) -> None:
    codec = ArtifactEnvelopeCodec(cipher)
    binding = ArtifactBinding("a" * 128, "r" * 128, "v" * 128, 2**63 - 1, "submitted_image")
    content = b"x" * MAX_CONTENT_BYTES
    token = codec.encode(binding, content)
    assert len(token) <= MAX_TOKEN_BYTES
    assert codec.decode(binding, token) == content
    with pytest.raises(ArtifactEnvelopeError):
        codec.encode(binding, content + b"x")
    body = json.loads(cipher.decrypt(token))
    # One extra decoded byte can still have the SAME base64 encoded length.
    body["content"] = base64.b64encode(content + b"x").decode("ascii")
    with pytest.raises(ArtifactEnvelopeError):
        codec.decode(binding, cipher.encrypt(canonical(body)))


def test_bounds_before_decrypt_and_parse(cipher: Fernet) -> None:
    spy = Mock(spec=Fernet)
    codec = ArtifactEnvelopeCodec(spy)
    with pytest.raises(ArtifactEnvelopeError):
        codec.decode(BINDING, b"x" * (MAX_TOKEN_BYTES + 1))
    spy.decrypt.assert_not_called()
    with pytest.raises(ArtifactEnvelopeError):
        ArtifactEnvelopeCodec(cipher).decode(
            BINDING, cipher.encrypt(b" " * (MAX_ENVELOPE_BYTES + 1))
        )


def test_bytes_only(cipher: Fernet) -> None:
    codec = ArtifactEnvelopeCodec(cipher)
    for value in ("secret", bytearray(b"secret"), None):
        with pytest.raises(ArtifactEnvelopeError):
            codec.encode(BINDING, value)  # type: ignore[arg-type]
        with pytest.raises(ArtifactEnvelopeError):
            codec.decode(BINDING, value)  # type: ignore[arg-type]


def test_no_runtime_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    for directory in ("orchestrator", "providers"):
        for path in (root / directory).rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            source = path.read_text()
            if relative not in {
                "orchestrator/artifact_envelope.py",
                "orchestrator/artifact_staging.py",
                "orchestrator/resource_storage.py",
                "orchestrator/resource_store.py",
                "orchestrator/resource_input_codec.py",
                "orchestrator/resource_inputs.py",
            }:
                assert "artifact_envelope" not in source, relative
            if relative not in {
                "orchestrator/artifact_staging.py",
                "orchestrator/artifact_namespaces.py",
                "orchestrator/resource_storage.py",
            }:
                assert "artifact_staging" not in source, relative
