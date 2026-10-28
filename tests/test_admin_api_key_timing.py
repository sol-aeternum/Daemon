"""Regression tests for admin API key constant-time comparison.

These tests guard against regressions of issue #22, where Python's `==`
operator was used to compare admin API keys. `==` is not constant-time
and leaks the prefix-length match count via response time. The fix uses
`hmac.compare_digest` which is constant-time.
"""

from __future__ import annotations

import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SELF_FILE = pathlib.Path(__file__).resolve()


def test_no_eq_comparison_against_admin_key():
    needles = (
        "token == settings.daemon_admin_api_key",
        "settings.daemon_admin_api_key == token",
        "== settings.daemon_admin_api_key",
        "==daemon_admin_api_key",
    )
    offenders = []
    for path in REPO_ROOT.rglob("*.py"):
        if path.resolve() == SELF_FILE:
            continue
        if any(
            part in path.parts
            for part in (".venv", "node_modules", ".git", "__pycache__", "data", "agent-output")
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        offenders.extend((path, needle) for needle in needles if needle in text)
    assert not offenders, (
        "Found non-constant-time comparison against admin API key in: "
        + ", ".join(f"{p.relative_to(REPO_ROOT)} ({needle!r})" for p, needle in offenders)
    )
