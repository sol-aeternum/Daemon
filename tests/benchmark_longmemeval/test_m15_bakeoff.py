"""Tests for the M1.5 bake-off driver: fakes only, no provider or database.

Every collaborator is injected, so this module opens no connection, calls no
provider and reads only the committed fixture inside the repository.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.compute_runtime import ComputeUnavailable
from orchestrator.memory import multi_query
from orchestrator.memory.multi_query import DECOMPOSITION_ROUTE_ID, ProductionSubqueryProvider
from tests.benchmark_longmemeval import m15_bakeoff as bakeoff
from tests.benchmark_longmemeval.dev2_labelling import _append_journal, _journal_path
from tests.benchmark_longmemeval.m15_bakeoff import (
    ARMS,
    Question,
    call_key,
    dispatch_payload,
    dispatch_plan,
    load_questions,
    manifest,
    run_bakeoff,
)


def _question(index: int, message: str | None = None) -> Question:
    return Question(
        question_id=f"q{index}",
        message=message if message is not None else f"message {index}",
        reference_time=datetime(2023, 5, 30, tzinfo=UTC),
        digest=f"digest-{index}",
    )


def _response(
    content: str = '{"queries": ["alpha"]}',
    *,
    cost: str = "0.0001",
    provider: str = "FakeProvider",
    finish: str = "stop",
    output_tokens: int = 7,
) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "provider": provider,
        "usage": {"cost": cost, "completion_tokens": output_tokens},
    }


def _fake_routes(arms: Any) -> dict[str, Any]:
    return {arm.label: SimpleNamespace(route_id=arm.route_id, model="fake/model") for arm in arms}


async def _true() -> bool:
    return True


async def _false() -> bool:
    return False


async def _zero() -> Decimal:
    return Decimal("0")


async def _nothing() -> None:
    return None


def _output(tmp_path: Any) -> Any:
    return tmp_path / "bakeoff.json"


def _kwargs(
    tmp_path: Any,
    questions: list[Question],
    *,
    call: Any = None,
    max_spend: Any = None,
    spend: Any = None,
    ceiling: Any = None,
    acquire: Any = None,
    held: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    async def default_call(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return _response()

    kwargs: dict[str, Any] = {
        "questions": questions,
        "output_path": _output(tmp_path),
        "max_spend_usd": max_spend if max_spend is not None else Decimal("10"),
        "call": call or default_call,
        "check_attestation": _nothing,
        "verify_routes": _fake_routes,
        "read_ledger_spend_usd": spend or _zero,
        "next_call_ceiling_usd": ceiling or (lambda payload: Decimal("0.001")),
        "acquire_run_lock": acquire or _true,
        "check_run_lock": held or _true,
    }
    return kwargs, calls


# --- request payload -------------------------------------------------------


def test_payload_matches_production(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    route = SimpleNamespace(
        route_id=DECOMPOSITION_ROUTE_ID,
        model="prod/model",
        is_approved=lambda requirements: True,
    )
    policy = SimpleNamespace(route=lambda route_id: route, requirements=object())
    monkeypatch.setattr(multi_query, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(multi_query.attestation, "is_monitored", lambda route: True)

    async def fake_guarded(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": '{"queries": []}'}}]}

    monkeypatch.setattr(multi_query, "guarded_completion", fake_guarded)
    reference = datetime(2023, 5, 30, 23, 18, tzinfo=UTC)
    assert (
        asyncio.run(
            ProductionSubqueryProvider().get_subqueries(
                "hello", reference_time=reference, deadline=10_000_000_000.0
            )
        )
        == []
    )

    question = Question("q1", "hello", reference, "digest")
    payload = dispatch_payload(ARMS[0], SimpleNamespace(model="bakeoff/model"), question)
    production = {key: value for key, value in captured.items() if key != "_dispatch_timeout_s"}
    differing = {"_route_id", "model", "reasoning_effort", "stream"}

    assert set(payload) - differing == set(production) - {"_route_id", "model"}
    for key in set(payload) - differing:
        assert payload[key] == production[key]
    assert payload["model"] != production["model"]
    assert payload["_route_id"] != production["_route_id"]
    assert payload["reasoning_effort"] == "none"
    assert payload["stream"] is False


# --- rotation, counts, labels ---------------------------------------------


def test_rotation_and_call_count() -> None:
    questions = [_question(index) for index in range(4)]
    plan = dispatch_plan(questions, ARMS)
    assert len(plan) == 12
    assert [(arm.label, question.question_id) for arm, question in plan] == [
        ("A", "q0"),
        ("B", "q0"),
        ("C", "q0"),
        ("B", "q1"),
        ("C", "q1"),
        ("A", "q1"),
        ("C", "q2"),
        ("A", "q2"),
        ("B", "q2"),
        ("A", "q3"),
        ("B", "q3"),
        ("C", "q3"),
    ]


def test_fifty_calls_per_arm(tmp_path: Any) -> None:
    questions = load_questions()
    assert len(questions) == 50
    kwargs, calls = _kwargs(tmp_path, questions)
    document = asyncio.run(run_bakeoff(**kwargs))

    assert document["stop_reason"] is None
    assert len(calls) == 150
    for arm in ARMS:
        assert document["summary"][arm.label]["calls"] == 50
        assert document["summary"][arm.label]["failures"] == 0
    assert document["max_calls"] == 150

    written = json.loads(_output(tmp_path).read_text(encoding="utf-8"))
    arms = {entry["label"]: entry for entry in written["arms"]}
    assert arms["B"]["reasoning_effort"] == "low"
    assert arms["C"]["reasoning_effort"] == "low"
    assert written["summary"]["B"]["reasoning_effort"] == "low"


# --- stop reasons ----------------------------------------------------------


def test_spend_cap_sends_nothing(tmp_path: Any) -> None:
    kwargs, calls = _kwargs(tmp_path, [_question(0), _question(1)], max_spend=Decimal("0"))
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "spend_cap"
    assert calls == []


def test_spend_unreadable_sends_nothing(tmp_path: Any) -> None:
    async def boom() -> Decimal:
        raise RuntimeError("ledger")

    kwargs, calls = _kwargs(tmp_path, [_question(0)], spend=boom)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "spend_unreadable"
    assert calls == []


def test_ceiling_unknown_sends_nothing(tmp_path: Any) -> None:
    def boom(payload: Any) -> Decimal:
        raise ValueError("ceiling")

    kwargs, calls = _kwargs(tmp_path, [_question(0)], ceiling=boom)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "ceiling_unknown"
    assert calls == []


def test_run_lock_busy_creates_nothing(tmp_path: Any) -> None:
    kwargs, calls = _kwargs(tmp_path, [_question(0)], acquire=_false)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "run_lock_busy"
    assert calls == []
    assert not _output(tmp_path).exists()
    assert not _journal_path(_output(tmp_path)).exists()


def test_run_lock_lost_sends_nothing(tmp_path: Any) -> None:
    kwargs, calls = _kwargs(tmp_path, [_question(0)], held=_false)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "run_lock_lost"
    assert calls == []


def test_receipt_mismatch_sends_nothing(tmp_path: Any) -> None:
    output = _output(tmp_path)
    with open(_journal_path(output), "x", encoding="utf-8") as handle:
        _append_journal(handle, manifest([_question(0)]))
    kwargs, calls = _kwargs(tmp_path, [_question(0), _question(1)])
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "receipt_mismatch"
    assert calls == []


def test_uncertain_dispatch_stops(tmp_path: Any) -> None:
    attempts: list[Any] = []

    async def boom(payload: Any) -> Any:
        attempts.append(payload)
        raise RuntimeError("unknown failure")

    kwargs, _ = _kwargs(tmp_path, [_question(0)], call=boom)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "uncertain_dispatch"
    assert len(attempts) == 1


def test_consecutive_failures_stop(tmp_path: Any) -> None:
    attempts: list[Any] = []

    async def unavailable(payload: Any) -> Any:
        attempts.append(payload)
        raise ComputeUnavailable("upstream_unavailable", "down", category="upstream_unavailable")

    questions = [_question(index) for index in range(12)]
    kwargs, _ = _kwargs(tmp_path, questions, call=unavailable)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "consecutive_failures"
    # Each arm counts its own, so the first to reach the limit stops the run.
    per_arm = [sum(1 for row in document["questions"] if row["arm"] == arm) for arm in "ABC"]
    assert max(per_arm) == bakeoff.CONSECUTIVE_FAILURE_LIMIT
    assert len(attempts) == sum(per_arm)


def test_accounting_error_stops(tmp_path: Any) -> None:
    attempts: list[Any] = []

    async def settlement(payload: Any) -> Any:
        attempts.append(payload)
        raise ComputeUnavailable("settlement_failed", "settlement", category="failed")

    kwargs, _ = _kwargs(tmp_path, [_question(0), _question(1)], call=settlement)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "accounting_error"
    assert len(attempts) == 1


# --- recorded data ---------------------------------------------------------


def test_known_failure_records_and_continues(tmp_path: Any) -> None:
    attempts: list[Any] = []

    async def flaky(payload: Any) -> Any:
        attempts.append(payload)
        if len(attempts) == 1:
            raise ComputeUnavailable(
                "upstream_unavailable", "down", category="upstream_unavailable"
            )
        return _response()

    kwargs, _ = _kwargs(tmp_path, [_question(0), _question(1)], call=flaky)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] is None
    assert len(attempts) == 6
    first = document["questions"][0]
    assert first["error_category"] == "upstream_unavailable"
    assert first["schema_valid"] is False


def test_invalid_json_is_schema_invalid(tmp_path: Any) -> None:
    async def bad(payload: Any) -> Any:
        return _response("not json at all")

    kwargs, _ = _kwargs(tmp_path, [_question(0)], call=bad)
    document = asyncio.run(run_bakeoff(**kwargs))
    entry = document["questions"][0]
    assert entry["schema_valid"] is False
    assert entry["triggered"] is False
    assert entry["component_count"] == 0
    assert entry["error_category"] is None


def test_truncated_finish_reason_is_counted(tmp_path: Any) -> None:
    async def truncated(payload: Any) -> Any:
        return _response(finish="length")

    kwargs, _ = _kwargs(tmp_path, [_question(0), _question(1)], call=truncated)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["summary"]["A"]["truncated_calls"] == 2
    assert document["questions"][0]["finish_reason"] == "length"


def test_sentinel_never_stored(tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    question_sentinel = "SENTINEL_QUESTION_9f3c"
    reply_sentinel = "SENTINEL_REPLY_7c1a"
    attempts: list[Any] = []

    async def reply(payload: Any) -> Any:
        attempts.append(payload)
        return _response('{"queries": ["' + reply_sentinel + '"]}')

    questions = [_question(0, message=question_sentinel + " what happened?")]
    kwargs, _ = _kwargs(tmp_path, questions, call=reply)
    with caplog.at_level(logging.DEBUG):
        document = asyncio.run(run_bakeoff(**kwargs))
    assert len(attempts) == 3

    journal = _journal_path(_output(tmp_path)).read_text(encoding="utf-8")
    written = _output(tmp_path).read_text(encoding="utf-8")
    rendered = json.dumps(document)
    for text in (journal, written, rendered, caplog.text):
        assert question_sentinel not in text
        assert reply_sentinel not in text


def test_max_calls_holds_across_resume(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bakeoff, "MAX_CALLS", 3)
    questions = [_question(0), _question(1)]
    output = _output(tmp_path)
    plan = dispatch_plan(questions, ARMS)
    with open(_journal_path(output), "x", encoding="utf-8") as handle:
        _append_journal(handle, manifest(questions))
        for arm, question in plan[:3]:
            entry = bakeoff._call_entry(arm, question, 0.1)
            _append_journal(
                handle,
                {
                    "kind": "intent",
                    "call_key": call_key(arm, question),
                    "arm": arm.label,
                    "question_id": question.question_id,
                },
            )
            _append_journal(
                handle,
                {"kind": "result", "call_key": call_key(arm, question), **entry},
            )

    kwargs, calls = _kwargs(tmp_path, questions)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "max_calls"
    assert calls == []


# --- guards ----------------------------------------------------------------


def test_call_ceiling_uses_256_output_tokens() -> None:
    seen: dict[str, int] = {}

    def estimate(bound: int, tokens: int) -> int:
        seen["bound"] = bound
        seen["tokens"] = tokens
        return 1234

    route = SimpleNamespace(price_ceiling=object(), estimate_microusd=estimate)
    payload = {
        "_route_id": "arm-route",
        "_dispatch_timeout_s": 120.0,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 256,
    }
    assert bakeoff.call_ceiling(route, payload) == Decimal("0.001234")
    assert seen["tokens"] == 256
    assert seen["bound"] > 0

    with pytest.raises(ValueError):
        bakeoff.call_ceiling(SimpleNamespace(price_ceiling=None), payload)


def test_wrong_database_name_refuses() -> None:
    assert bakeoff.check_bakeoff_database("postgresql://u:p@localhost/daemon_lme_bench_m15") == (
        "daemon_lme_bench_m15"
    )
    with pytest.raises(ValueError):
        bakeoff.check_bakeoff_database("postgresql://u:p@localhost/daemon_lme_bench")
    with pytest.raises(ValueError):
        bakeoff.check_bakeoff_database("postgresql://u:p@localhost/other")
    with pytest.raises(ValueError):
        bakeoff.check_bakeoff_database(None)


def test_percentiles() -> None:
    assert bakeoff._percentile([], 0.5) is None
    assert bakeoff._percentile([4.0], 0.95) == 4.0
    assert bakeoff._percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert bakeoff._percentile([float(value) for value in range(1, 101)], 0.95) == pytest.approx(
        95.05
    )


def test_dry_run_makes_no_calls_or_files(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(**kwargs: Any) -> Any:
        raise AssertionError("dry run must not run the bake-off")

    monkeypatch.setattr(bakeoff, "_run_bakeoff", fail)
    output = _output(tmp_path)
    code = bakeoff.bakeoff_main(
        ["--dry-run", "--input", str(bakeoff.QUESTION_PATH), "--output", str(output)]
    )
    assert code == 0
    assert not output.exists()
    assert not _journal_path(output).exists()
    printed = capsys.readouterr().out
    assert "50 questions x 3 arms = 150 calls" in printed
    assert "reasoning_effort=low" in printed


def test_the_input_must_be_exactly_fifty_questions(tmp_path: Any) -> None:
    rows = json.loads(bakeoff.QUESTION_PATH.read_text(encoding="utf-8"))
    short = tmp_path / "short.json"
    short.write_text(json.dumps(rows[:49]), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 50"):
        bakeoff.load_questions(short)
    assert len(bakeoff.load_questions()) == 50


@pytest.mark.parametrize(
    "name", ["daemon_lme_bench", "daemon_lme_bench_dev2", "daemon_lme_bench_dev2_final"]
)
def test_the_bakeoff_refuses_every_other_benchmark_database(name: str) -> None:
    with pytest.raises(ValueError):
        bakeoff.check_bakeoff_database(f"postgresql://u:p@localhost/{name}")


def test_output_tokens_come_from_the_provider_usage_field() -> None:
    assert bakeoff._output_tokens({"usage": {"completion_tokens": 42}}) == 42
    assert bakeoff._output_tokens({"usage": {"output_tokens": 42}}) == 0


def test_another_arms_successes_do_not_reset_a_failing_arm(tmp_path: Any) -> None:
    """The live run refused every B and C call while Luna's successes hid it."""

    async def only_luna_works(payload: Any) -> Any:
        if payload["_route_id"] == "luna-azure-eu":
            return _response()
        raise ComputeUnavailable("invalid_request", "refused", category="invalid_request")

    questions = [_question(index) for index in range(30)]
    kwargs, _ = _kwargs(tmp_path, questions, call=only_luna_works)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "consecutive_failures"
    failures = {
        arm: sum(1 for row in document["questions"] if row["arm"] == arm and row["error_category"])
        for arm in ("B", "C")
    }
    assert max(failures.values()) == bakeoff.CONSECUTIVE_FAILURE_LIMIT


def _seed_journal(tmp_path: Any, questions: list[Question], calls: int, failing_arm: str) -> None:
    with open(_journal_path(_output(tmp_path)), "x", encoding="utf-8") as handle:
        _append_journal(handle, manifest(questions))
        for arm, question in dispatch_plan(questions, ARMS)[:calls]:
            key = call_key(arm, question)
            entry = bakeoff._call_entry(arm, question, 0.1)
            if arm.label == failing_arm:
                entry["error_category"] = "invalid_request"
            _append_journal(
                handle,
                {
                    "kind": "intent",
                    "call_key": key,
                    "arm": arm.label,
                    "question_id": question.question_id,
                },
            )
            _append_journal(handle, {"kind": "result", "call_key": key, **entry})


def test_a_resume_keeps_an_arms_failure_streak(tmp_path: Any) -> None:
    attempts: list[Any] = []

    async def fails_on_b(payload: Any) -> Any:
        attempts.append(payload)
        if payload["_route_id"] == ARMS[1].route_id:
            raise ComputeUnavailable("invalid_request", "refused", category="invalid_request")
        return _response()

    questions = [_question(index) for index in range(12)]
    _seed_journal(tmp_path, questions, 27, "B")  # nine rounds: nine B failures, with A and C fine
    kwargs, _ = _kwargs(tmp_path, questions, call=fails_on_b)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "consecutive_failures"
    assert len([a for a in attempts if a["_route_id"] == ARMS[1].route_id]) == 1


def test_a_resume_with_a_full_streak_sends_nothing(tmp_path: Any) -> None:
    questions = [_question(index) for index in range(12)]
    _seed_journal(tmp_path, questions, 30, "B")  # ten rounds: ten B failures
    kwargs, calls = _kwargs(tmp_path, questions)
    document = asyncio.run(run_bakeoff(**kwargs))
    assert document["stop_reason"] == "consecutive_failures"
    assert calls == []
