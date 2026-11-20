"""Synthetic final-answer evidence for fixtures that replace the provider loop."""

from orchestrator.turn_finish import answer_finish


def confirmed_stop():
    return {
        "type": "done",
        "returned_answer": True,
        "turn_finish": answer_finish(
            {"state": "confirmed", "reason": "stop", "provenance": "provider_wire"}
        ).model_dump(exclude_none=True),
    }
