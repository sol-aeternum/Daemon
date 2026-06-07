"""Regression tests for admin API key constant-time comparison.

These tests guard against regressions of issue #22, where Python's `==`
operator was used to compare admin API keys. `==` is not constant-time
and leaks the prefix-length match count via response time. The fix uses
`hmac.compare_digest` which is constant-time.
"""

from __future__ import annotations

import hmac
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SELF_FILE = pathlib.Path(__file__).resolve()


@pytest.mark.parametrize(
    "needle",
    [
        "token == settings.daemon_admin_api_key",
        "settings.daemon_admin_api_key == token",
        "== settings.daemon_admin_api_key",
        "==daemon_admin_api_key",
    ],
)
def test_no_eq_comparison_against_admin_key(needle):
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
        if needle in text:
            offenders.append(path)
    assert not offenders, (
        f"Found non-constant-time comparison against admin API key ({needle!r}) in: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in offenders)
    )


def test_hmac_compare_digest_matches():
    secret = "super-secret-admin-key-123"
    presented = "super-secret-admin-key-123"
    assert hmac.compare_digest(presented.encode(), secret.encode())


def test_hmac_compare_digest_rejects_mismatch():
    secret = "super-secret-admin-key-123"
    presented = "super-secret-admin-key-124"
    assert not hmac.compare_digest(presented.encode(), secret.encode())


def test_hmac_compare_digest_rejects_prefix_match():
    secret = "super-secret-admin-key-123"
    presented = "super-secret-admin-key"
    assert not hmac.compare_digest(presented.encode(), secret.encode())
