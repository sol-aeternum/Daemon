"""Bounded outcome normalization; no HTTP, database, or inference calls."""

import json

import pytest

from orchestrator.tasks.fence import _outcome_of


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": 201, "headers": {}, "body": "created"}, "succeeded"),
        ({"status": 400, "headers": {}, "body": "invalid"}, "unknown"),
        ({"status": 503, "headers": {}, "body": "upstream error"}, "unknown"),
        ({"status": 500, "success": True}, "unknown"),
        ({"success": False, "error": "timeout"}, "unknown"),
        ({"performed": False, "status": 500}, "failed"),
        ({"success": True}, "succeeded"),
        ({}, "unknown"),
        ("", "unknown"),
        ("legacy response", "unknown"),
        (None, "unknown"),
    ],
)
def test_normalizes_real_http_and_missing_evidence(result, expected):
    assert _outcome_of(result) == expected
    if isinstance(result, dict):
        assert _outcome_of(json.dumps(result)) == expected
