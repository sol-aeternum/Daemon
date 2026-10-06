"""Pure durable-task state and retry rules (docs/DURABLE_REQUEST_DESIGN.md §4)."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from orchestrator.tasks.inputs import chat_request_hash
from orchestrator.tasks.states import (
    TERMINAL_STATUSES,
    RetryCause,
    TaskStatus,
    can_transition,
    decide_after_attempt,
    retry_backoff_s,
)


def test_terminal_statuses_have_no_outgoing_transitions():
    for terminal, target in itertools.product(TERMINAL_STATUSES, TaskStatus):
        assert not can_transition(terminal, target)


def test_queued_cannot_complete_without_running():
    assert not can_transition(TaskStatus.QUEUED, TaskStatus.COMPLETED)
    assert can_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)


@pytest.mark.parametrize(
    ("cause", "attempt_count", "max_attempts"),
    list(itertools.product(RetryCause, range(0, 4), range(1, 4))),
)
def test_material_effect_blocks_every_retry_path(cause, attempt_count, max_attempts):
    decision = decide_after_attempt(
        cause=cause,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        material_started=True,
    )
    assert decision.status is TaskStatus.NEEDS_ATTENTION
    assert decision.terminal_code == "uncertain_effect"


@pytest.mark.parametrize(
    ("attempt_count", "max_attempts"),
    [(a, m) for a, m in itertools.product(range(0, 5), range(1, 5)) if a <= m],
)
@pytest.mark.parametrize("cause", [RetryCause.LOST, RetryCause.RETRYABLE_ERROR])
def test_retryable_causes_requeue_only_below_cap(cause, attempt_count, max_attempts):
    decision = decide_after_attempt(
        cause=cause,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        material_started=False,
    )
    if attempt_count < max_attempts:
        assert decision.status is TaskStatus.QUEUED
    else:
        assert decision.status is TaskStatus.FAILED
        assert decision.terminal_code == "interrupted"


@pytest.mark.parametrize("cause", [RetryCause.TERMINAL_ERROR, RetryCause.CAPACITY])
def test_terminal_and_capacity_causes_never_requeue(cause):
    decision = decide_after_attempt(
        cause=cause, attempt_count=0, max_attempts=5, material_started=False, error_code="x_y"
    )
    assert decision.status is TaskStatus.FAILED
    assert decision.terminal_code == "x_y"


def test_capacity_defaults_to_capacity_code():
    decision = decide_after_attempt(
        cause=RetryCause.CAPACITY, attempt_count=1, max_attempts=2, material_started=False
    )
    assert decision.terminal_code == "capacity_unavailable"


def test_invalid_counters_are_rejected():
    with pytest.raises(ValueError):
        decide_after_attempt(
            cause=RetryCause.LOST, attempt_count=-1, max_attempts=2, material_started=False
        )
    with pytest.raises(ValueError):
        decide_after_attempt(
            cause=RetryCause.LOST, attempt_count=0, max_attempts=0, material_started=False
        )


def test_backoff_grows_and_is_capped():
    delays = [retry_backoff_s(n) for n in range(0, 10)]
    assert delays[0] == 0.0
    assert delays == sorted(delays)
    assert max(delays) == 120.0


def _hash(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "conversation_id": None,
        "message": "hello",
        "attachments": None,
        "model": None,
        "provider": None,
        "metadata": None,
        "disable_memory_write": False,
    }
    base.update(overrides)
    return chat_request_hash(**base)


def test_request_hash_is_stable_and_order_independent():
    assert _hash(metadata={"a": 1, "b": 2}) == _hash(metadata={"b": 2, "a": 1})
    assert _hash(model=None) == _hash(model="auto")
    assert _hash(attachments=None) == _hash(attachments=[])


def test_request_hash_distinguishes_what_was_asked():
    base = _hash()
    assert _hash(message="hello!") != base
    assert _hash(model="other") != base
    assert _hash(disable_memory_write=True) != base
    assert _hash(attachments=[{"name": "a.txt", "content": "x"}]) != base
