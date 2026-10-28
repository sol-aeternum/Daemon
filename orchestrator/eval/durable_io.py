"""Crash-safe file primitives for LongMemEval checkpoints, manifests and results.

Every JSON document is replaced atomically (temp file in the same directory,
``fsync``, ``os.replace``, directory ``fsync``), so an interrupted write leaves
either the previous version or the new one, never a torn file. Results are an
append-only JSONL stream fsynced per line; a torn final line from a killed
process is detected and cut on the next read.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory(path.parent)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def append_jsonl_record(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line and make it durable before returning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl_records(path: Path, *, repair: bool = True) -> list[dict[str, Any]]:
    """Read complete JSONL records, cutting a torn trailing line if present.

    Only the final line may be incomplete (an append interrupted mid-write);
    a malformed line anywhere else is corruption and raises.
    """
    if not path.exists():
        return []
    raw = path.read_bytes()
    if not raw:
        return []
    records: list[dict[str, Any]] = []
    lines = raw.split(b"\n")
    complete_lines, tail = lines[:-1], lines[-1]
    good_length = 0
    for index, line in enumerate(complete_lines):
        if not line.strip():
            good_length += len(line) + 1
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            # append_jsonl_record writes the newline last, so a newline-terminated
            # malformed line is corruption, not an interrupted append.
            raise ValueError(f"Corrupt JSONL record at line {index + 1} of {path}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record at line {index + 1} of {path} is not an object")
        records.append(record)
        good_length += len(line) + 1
    if tail.strip():
        try:
            record = json.loads(tail)
        except json.JSONDecodeError:
            if repair:
                logger.warning(
                    "Cutting torn trailing JSONL record (%s bytes) from %s", len(tail), path
                )
                with path.open("r+b") as handle:
                    handle.truncate(good_length)
                    handle.flush()
                    os.fsync(handle.fileno())
            return records
        if isinstance(record, dict):
            # A complete record missing only its newline: keep it and terminate it.
            records.append(record)
            if repair:
                with path.open("ab") as handle:
                    handle.write(b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
    return records


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
