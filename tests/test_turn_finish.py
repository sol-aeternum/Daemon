import pytest

from orchestrator.turn_finish import (
    LocalLimit,
    ProviderFinish,
    TurnFinish,
    answer_finish,
    parse_turn_finish,
    refused_finish,
)


@pytest.mark.parametrize(
    ("reason", "outcome", "standard"),
    [
        ("stop", "complete", "stop"),
        ("length", "incomplete", "length"),
        ("content_filter", "failed", "content_filter"),
        ("tool_calls", "unknown", None),
    ],
)
def test_only_confirmed_final_answer_evidence_selects_standard_reason(reason, outcome, standard):
    finish = answer_finish({"state": "confirmed", "reason": reason, "provenance": "provider_wire"})
    assert finish.outcome == outcome
    assert finish.finish_reason == standard
    assert finish.provider.reason == reason
    assert parse_turn_finish(finish.model_dump(exclude_none=True)) == finish


def test_unknown_and_unproven_stop_are_not_completion():
    assert answer_finish({"state": "missing", "reason": "stop"}).finish_reason is None
    assert answer_finish({"state": "confirmed", "reason": "stop"}).finish_reason is None
    assert answer_finish({"state": "conflicting"}).reason == "finish_evidence_conflict"
    assert parse_turn_finish({"finish_reason": "stop"}) is None
    finish = answer_finish({"state": "confirmed", "reason": "stop", "provenance": "provider_wire"})
    data = finish.model_dump(exclude_none=True)
    assert parse_turn_finish({**data, "version": True}) is None
    assert parse_turn_finish({**data, "limits": [{"kind": "output", "binding": True}]}) is None


def test_local_ties_do_not_rewrite_provider_evidence_or_imply_spend():
    finish = TurnFinish(
        outcome="incomplete",
        reason="provider_length",
        provider=ProviderFinish(state="confirmed", reason="length", provenance="provider_wire"),
        limits=[
            LocalLimit(kind="caller_output", value=4096, unit="tokens", binding=True),
            LocalLimit(kind="account_output", value=4096, unit="tokens", binding=True),
        ],
    )
    assert finish.finish_reason == "length"
    assert parse_turn_finish(finish.model_dump(exclude_none=True)) == finish
    assert refused_finish("budget_exceeded").limits[0].kind == "compute_budget"
    assert refused_finish("capacity_unavailable").limits == []
    assert refused_finish("capacity_unavailable", category="deadline_exceeded").reason == (
        "deadline_exceeded"
    )


def test_compat_finish_does_not_let_stop_override_failure_or_unexplained_eof():
    from orchestrator.main import _compat_finish
    from orchestrator.turn_finish import unknown_finish

    done = answer_finish({"state": "confirmed", "reason": "stop", "provenance": "provider_wire"})
    data = {"status": "completed", "turn_finish": done.model_dump()}
    assert _compat_finish("final", data, unknown_finish()).finish_reason is None
    assert (
        _compat_finish("done", {**data, "status": "error"}, unknown_finish()).finish_reason is None
    )
    failed = _compat_finish("error", {}, unknown_finish())
    assert _compat_finish("done", data, failed) is failed
