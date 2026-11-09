from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orchestrator.memory.temporal import EventScope, TemporalReadScope


def test_exact_bounds_require_offsets():
    with pytest.raises(ValidationError):
        EventScope(start=datetime(2026, 1, 1), start_kind="exact", precision="exact")


def test_partial_is_not_an_invented_exact_instant():
    scope = EventScope(expression="around March 2024", precision="approximate")
    assert scope.start is None and scope.start_kind == "unknown"
    assert "expression" not in scope.storage()
    with pytest.raises(ValidationError):
        EventScope(start=datetime(2024, 3, 1, tzinfo=UTC), start_kind="exact", precision="month")


def test_unknown_is_not_unbounded():
    assert (
        EventScope().fingerprint()
        != EventScope(start_kind="unbounded", end_kind="unbounded").fingerprint()
    )


def test_strict_cutoff_rejects_naive_time():
    with pytest.raises(ValidationError):
        TemporalReadScope(known_at=datetime(2026, 1, 1))


def test_extractor_cannot_promote_partial_or_invented_offsets_to_exact_bounds():
    from orchestrator.memory.extraction import extracted_event_scope

    proposed = {
        "start": "2020-01-01T00:00:00Z",
        "start_kind": "exact",
        "end_kind": "unknown",
        "precision": "exact",
        "expression": "January 2020",
    }
    scope = extracted_event_scope(proposed, "A fictional event happened in January 2020.")
    assert scope is not None and scope.start is None and scope.start_kind == "unknown"
    assert scope.expression == "January 2020" and scope.precision == "unknown"
    qualified = extracted_event_scope(proposed, "Fictional event at 2020-01-01T00:00:00Z.")
    assert qualified is not None and qualified.start == datetime(2020, 1, 1, tzinfo=UTC)


def test_extractor_retains_approximate_expression_without_resolved_bounds():
    from orchestrator.memory.extraction import extracted_event_scope

    scope = extracted_event_scope(
        {"precision": "approximate", "expression": "around last spring"},
        "A fictional event happened around last spring.",
    )
    assert scope is not None and scope.precision == "approximate"
    assert scope.start is None and scope.end is None
    assert scope.expression == "around last spring"
