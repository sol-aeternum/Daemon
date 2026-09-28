"""Plain mapping responses obey the same completeness rules as SDK objects."""

import pytest

from orchestrator.memory.completion import (
    EMPTY_RESPONSE_REASON,
    TRUNCATED_RESPONSE_REASON,
    read_completeness,
)


@pytest.mark.parametrize(
    ("content", "finish_reason", "expected_reason"),
    [
        ("YES: same entity", "stop", None),
        ("YES", "length", TRUNCATED_RESPONSE_REASON),
        ("", "stop", EMPTY_RESPONSE_REASON),
        ("", "length", TRUNCATED_RESPONSE_REASON),
    ],
)
def test_mapping_response_preserves_content_and_failure_reason(
    content, finish_reason, expected_reason
):
    result = read_completeness(
        {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    )
    assert result.content == content
    assert result.reason == expected_reason
    assert result.complete == (expected_reason is None)
