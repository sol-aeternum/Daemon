import json
from uuid import uuid4

import pytest

from orchestrator.tasks.observe import _Observation
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import TaskSnapshot
from orchestrator.turn_finish import answer_finish


def _data(frame):
    return json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))


@pytest.mark.parametrize("legacy", [False, True])
def test_authoritative_terminal_result_replaces_provisional_text(legacy):
    view = _Observation("fixture-conversation", "fixture-request")
    view.displayed = "provisional text from live frames"
    finish = answer_finish(
        {"state": "confirmed", "reason": "length", "provenance": "provider_wire"}
    )
    snapshot = TaskSnapshot(
        task_id=uuid4(),
        conversation_id=uuid4(),
        result_message_id=uuid4(),
        status=TaskStatus.COMPLETED if legacy else TaskStatus.FAILED,
        terminal_code=None if legacy else "provider_length",
        attempt_count=1,
        content_generation=1,
        content_delta_seq=1,
        content="committed text",
        event_seq=2,
        cancel_requested=False,
        turn_finish=None if legacy else finish,
    )

    frames = [_data(frame) for frame in view.terminal(snapshot)]
    by_type = {frame["type"]: frame["data"] for frame in frames}
    assert by_type["task"]["reset"] is True
    assert by_type["task"]["content"] == "committed text"
    assert by_type["final"]["text"] == "committed text"
    assert by_type["final"]["finish_reason"] == (None if legacy else "length")
    assert by_type["done"]["turn_finish"] == by_type["final"]["turn_finish"]
    assert by_type["done"]["turn_finish"]["outcome"] == ("unknown" if legacy else "incomplete")
