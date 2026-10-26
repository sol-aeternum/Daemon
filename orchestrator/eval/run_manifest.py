"""Run manifest for LongMemEval fact-harness runs.

One ``run_manifest.json`` per output directory, written atomically at run start
and after every phase transition. It pins the code (git SHA + dirty flag),
config pin, memory formatter, dataset, model routes, embedding route, the
ingest a run evaluates and every parity exception, so two runs can be compared
without guessing what differed. The code pin includes a content hash of
uncommitted changes, so a dirty checkout edited mid-run is detected too.
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


# Paths whose content a benchmark run executes or reads as configuration. Run
# outputs (tests/benchmark_results, scratch directories) are deliberately outside.
CODE_PATHSPECS: tuple[str, ...] = (
    "orchestrator",
    "providers",
    "config",
    "migrations",
    "tests/longmemeval",
    "tests/benchmark_longmemeval",
    "pyproject.toml",
    "uv.lock",
)


def _git(cwd: str | None, *args: str) -> bytes:
    return subprocess.run(  # nosec B603 B607 - fixed git argv
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout


def git_state(repo_root: Path | None = None) -> dict[str, Any]:
    """HEAD SHA, whether tracked files differ from it, and a code content hash.

    ``code_sha256`` covers HEAD plus every uncommitted change (tracked diff and
    untracked, non-ignored files) under :data:`CODE_PATHSPECS`, so two dirty
    checkouts at the same SHA compare unequal when their code differs.
    """
    cwd = str(repo_root) if repo_root is not None else None
    try:
        sha = _git(cwd, "rev-parse", "HEAD").decode().strip()
        status = _git(cwd, "status", "--porcelain", "--untracked-files=no").decode()
        diff = _git(cwd, "diff", "HEAD", "--binary", "--", *CODE_PATHSPECS)
        untracked = _git(
            cwd, "ls-files", "--others", "--exclude-standard", "-z", "--", *CODE_PATHSPECS
        )
    except (OSError, subprocess.SubprocessError):
        return {"sha": None, "dirty": None, "code_sha256": None}
    digest = hashlib.sha256(sha.encode() + b"\0" + diff)
    root = Path(cwd) if cwd else Path.cwd()
    for name in sorted(item for item in untracked.split(b"\0") if item):
        digest.update(b"\0untracked\0" + name + b"\0")
        path = root / name.decode()
        if path.is_file():
            digest.update(file_sha256(path).encode())
    return {"sha": sha or None, "dirty": bool(status.strip()), "code_sha256": digest.hexdigest()}


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
    if pinned.get("code_sha256") != current.get("code_sha256"):
        changes.append(
            f"code content {str(pinned.get('code_sha256'))[:12]} -> "
            f"{str(current.get('code_sha256'))[:12]}"
        )
    if changes and not allow_change:
        raise ManifestPinError(
            "Refusing to resume: this run is pinned to "
            f"{pinned.get('sha')} (dirty={pinned.get('dirty')}) but the checkout differs: "
            f"{'; '.join(changes)}. Finish or abandon runs before editing or rebasing; "
            "pass --allow-code-change only to deliberately continue (the run is then "
            "not baseline-qualified)."
        )
    return changes


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["schema_version"] = MANIFEST_SCHEMA_VERSION
    atomic_write_json(path, manifest)


def process_identity() -> dict[str, Any]:
    return {"pid": os.getpid()}
