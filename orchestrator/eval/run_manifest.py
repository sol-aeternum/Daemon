"""Run manifest for LongMemEval fact-harness runs.

One ``run_manifest.json`` per output directory, written atomically at run start
and after every phase transition. It pins the code (git SHA + dirty flag),
config pin, memory formatter, dataset, model routes, embedding route, the
ingest a run evaluates and every parity exception, so two runs can be compared
without guessing what differed.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import subprocess  # nosec B404 - fixed git argv, no shell
from pathlib import Path
from typing import Any

from orchestrator.eval.durable_io import atomic_write_json, file_sha256

MANIFEST_FILENAME = "run_manifest.json"
MANIFEST_SCHEMA_VERSION = 1


class ManifestPinError(RuntimeError):
    """Raised when a resumed run's code no longer matches the manifest's pin."""


def git_state(repo_root: Path | None = None) -> dict[str, Any]:
    """HEAD SHA and whether tracked files differ from it.

    Untracked files are ignored so a run's own artifacts never mark the code dirty.
    """
    cwd = str(repo_root) if repo_root is not None else None
    try:
        sha = subprocess.run(  # nosec B603 B607 - fixed git argv
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
        status = subprocess.run(  # nosec B603 B607 - fixed git argv
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"sha": None, "dirty": None}
    return {"sha": sha or None, "dirty": bool(status.strip())}


def source_sha256(*objects: Any) -> str:
    digest = hashlib.sha256()
    for obj in objects:
        digest.update(inspect.getsource(obj).encode("utf-8"))
    return digest.hexdigest()


def optional_file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return file_sha256(path)


def load_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    import json

    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Run manifest must be a JSON object: {path}")
    return payload


def check_code_pin(
    manifest: dict[str, Any], current: dict[str, Any], *, allow_change: bool
) -> list[str]:
    """Refuse to resume on different code unless explicitly allowed.

    Returns the list of recorded pin changes (empty when the pin holds).
    """
    pinned = manifest.get("git") or {}
    changes: list[str] = []
    if pinned.get("sha") != current.get("sha"):
        changes.append(f"git sha {pinned.get('sha')} -> {current.get('sha')}")
    if pinned.get("dirty") != current.get("dirty"):
        changes.append(f"git dirty {pinned.get('dirty')} -> {current.get('dirty')}")
    if changes and not allow_change:
        raise ManifestPinError(
            "Refusing to resume: this run is pinned to "
            f"{pinned.get('sha')} (dirty={pinned.get('dirty')}) but the checkout is "
            f"{current.get('sha')} (dirty={current.get('dirty')}). Finish or abandon runs "
            "before rebasing; pass --allow-code-change only to deliberately continue."
        )
    return changes


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["schema_version"] = MANIFEST_SCHEMA_VERSION
    atomic_write_json(path, manifest)


def process_identity() -> dict[str, Any]:
    return {"pid": os.getpid()}
