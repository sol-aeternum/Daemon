"""Bounded descriptor and import-contract checks; database authority tested separately."""

import ast
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

from orchestrator import artifact_reservations as ar


def packet():
    job = ar.JobBinding(uuid4(), uuid4(), 4, uuid4(), uuid4())
    binding = ar.IntentBinding(uuid4(), 0, uuid4(), uuid4(), uuid4(), uuid4(), 2, 20)
    cipher = Fernet(Fernet.generate_key())
    codec = ar.DescriptorCodec(cipher)
    command = codec.encode(job, binding, filename="private-fixture.csv", sha256="a" * 64)
    return cipher, codec, command


def test_descriptor_roundtrip_binding_and_private_representation():
    cipher, codec, command = packet()
    codec.validate(command)
    assert "private-fixture" not in repr(command)
    assert "private-fixture" not in json.dumps(command.wire())
    assert "a" * 64 not in json.dumps(command.wire())
    parsed = json.loads(cipher.decrypt(command.descriptor_ciphertext.encode()))
    assert parsed["provenance"]["original_task_id"] == str(command.job.task_id)
    for binding in (
        replace(command.binding, measured_bytes=21),
        replace(command.binding, version_id=uuid4()),
    ):
        with pytest.raises(ar.ReservationError):
            codec.validate(replace(command, binding=binding))
    with pytest.raises(ar.ReservationError):
        codec.validate(replace(command, job=replace(command.job, account_id=uuid4())))
    with pytest.raises(ar.ReservationError):
        ar.DescriptorCodec(Fernet(Fernet.generate_key())).validate(command)


@pytest.mark.parametrize("filename", ["x" * 1025, "é" * 513, "\ud800", 1])
def test_filename_bounds_and_unicode_scalars(filename):
    _, codec, command = packet()
    with pytest.raises(ar.ReservationError):
        codec.encode(command.job, command.binding, filename=filename, sha256="a" * 64)


def test_no_normalization_and_exact_size_boundaries():
    cipher, codec, command = packet()
    for filename in ("é" * 512, "é", "e\u0301", ""):
        c = codec.encode(command.job, command.binding, filename=filename, sha256="a" * 64)
        codec.validate(c)
        assert json.loads(cipher.decrypt(c.descriptor_ciphertext.encode()))["filename"] == filename
    for size in (0, 8388608):
        codec.validate(
            codec.encode(
                command.job,
                replace(command.binding, measured_bytes=size),
                filename="x",
                sha256="a" * 64,
            )
        )
    for size in (-1, True, 8388609):
        with pytest.raises(ar.ReservationError):
            codec.encode(
                command.job,
                replace(command.binding, measured_bytes=size),
                filename="x",
                sha256="a" * 64,
            )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "nested_duplicate",
        "whitespace",
        "unknown",
        "bool",
        "float",
        "nan",
        "surrogate",
        "binding",
        "large",
        "deep",
    ],
)
def test_authenticated_noncanonical_descriptors_refuse(mutation):
    cipher, codec, command = packet()
    original = cipher.decrypt(command.descriptor_ciphertext.encode())
    obj = json.loads(original)
    if mutation == "duplicate":
        data = original[:-1] + b',"schema_version":1}'
    elif mutation == "nested_duplicate":
        data = original.replace(
            b'"kind":"deterministic_csv"', b'"kind":"deterministic_csv","kind":"deterministic_csv"'
        )
    elif mutation == "whitespace":
        data = json.dumps(obj).encode()
    elif mutation == "large":
        data = b" " * (ar.MAX_DESCRIPTOR_BYTES + 1)
    elif mutation == "deep":
        data = b"[" * 1000 + b"]" * 1000
    else:
        if mutation == "unknown":
            obj["unknown"] = "private"
        elif mutation == "bool":
            obj["schema_version"] = True
        elif mutation == "float":
            obj["schema_version"] = 1.0
        elif mutation == "nan":
            obj["measured_bytes"] = float("nan")
        elif mutation == "surrogate":
            obj["filename"] = "\ud800"
        else:
            obj["account_id"] = str(uuid4())
        data = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    token = cipher.encrypt(data).decode()
    with pytest.raises(ar.ReservationError, match="^invalid_reservation$"):
        codec.validate(replace(command, descriptor_ciphertext=token))


@pytest.mark.parametrize("token", ["", "?", "x" * 131073, "é", "eA=="])
def test_invalid_tokens(token):
    _, codec, command = packet()
    with pytest.raises(ar.ReservationError):
        codec.validate(replace(command, descriptor_ciphertext=token))


def test_no_runtime_callers_or_import_time_io(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    for base in ("orchestrator", "providers", "scripts"):
        for path in (root / base).rglob("*.py"):
            if path == root / "orchestrator/artifact_reservations.py":
                continue
            assert "artifact_reservations" not in path.read_text(), str(path)
    tree = ast.parse((root / "orchestrator/artifact_reservations.py").read_text())
    assert not any(
        isinstance(n, (ast.Import, ast.ImportFrom)) and "artifact_staging" in ast.unparse(n)
        for n in ast.walk(tree)
    )

    def forbidden(*args, **kwargs):
        pytest.fail("construction caused database IO")

    monkeypatch.setattr(ar.asyncpg, "connect", forbidden)
    monkeypatch.setattr(ar.asyncpg, "create_pool", forbidden)
    ar.OutputReservations(ar.DescriptorCodec(Fernet(Fernet.generate_key())))


def test_result_decode_refuses_lies_and_private_errors():
    _, _, command = packet()
    row = command.job.wire() | {"created_at": "2026-10-28T00:00:00+00:00"}
    value = {"outcome": "pending_commit", "job": row, "intent": None, "inserted": True}
    assert ar._decode(json.dumps(value), command.job, None, write=True).inserted
    for field, bad in (("outcome", "present"), ("inserted", 1), ("job", None)):
        with pytest.raises(ar.ReservationError, match="^result_unavailable$"):
            ar._decode(json.dumps(value | {field: bad}), command.job, None, write=True)
