"""Secure local delivery for first-boot setup tokens."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def setup_token_file_path(raw_path: str) -> Path:
    return Path(raw_path).expanduser()


def write_setup_token_file(raw_path: str, token: str) -> Path:
    path = setup_token_file_path(raw_path)
    parent = path.parent
    if parent != Path("."):
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.write("\n")
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
        return path
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def setup_token_file_exists(raw_path: str) -> bool:
    return setup_token_file_path(raw_path).exists()


def delete_setup_token_file(raw_path: str) -> None:
    try:
        setup_token_file_path(raw_path).unlink()
    except FileNotFoundError:
        return
