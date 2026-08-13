"""Static convention test for #83: no `os.environ.get(...)` outside Settings.

All env-var access in the backend must go through the `Settings` class
(`orchestrator/config.py`). Direct `os.environ.get(...)` reads bypass the
type validation, defaults, and env-var prefix convention that Settings
provides.

This test scans the backend tree (excluding `config.py` itself, which is
where the canonical field definitions live) and fails the suite if any new
direct env reads are introduced.

The allowlist covers:
  * `orchestrator/config.py` — Settings is the canonical binding site.
  * `orchestrator/database_url.py` — accepts `environ` as a parameter
    (dependency injection used by tests).
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCAN_DIRS = (
    REPO_ROOT / "orchestrator",
    REPO_ROOT / "providers",
    REPO_ROOT / "scripts",
)
ALLOWLIST = frozenset(
    {
        "orchestrator/config.py",
        "orchestrator/database_url.py",
    }
)


def _scan_for_os_environ_get() -> list[tuple[pathlib.Path, int, str]]:
    hits: list[tuple[pathlib.Path, int, str]] = []
    for scan_dir in SCAN_DIRS:
        for path in scan_dir.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    # Skip comment lines entirely.
                    continue
                # Look for the runtime call form `os.environ.get(` —
                # comments that mention the phrase in prose are fine.
                if "os.environ.get(" in line:
                    hits.append((path, lineno, line.strip()))
    return hits


def test_no_os_environ_get_outside_config() -> None:
    hits = _scan_for_os_environ_get()
    assert not hits, (
        "Direct `os.environ.get(...)` calls detected outside "
        "`orchestrator/config.py`. Read env vars through the `Settings` "
        "class instead so type validation, defaults, and env-var prefixing "
        "apply uniformly.\n\nFound:\n"
        + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}: {snippet}" for p, ln, snippet in hits)
    )
