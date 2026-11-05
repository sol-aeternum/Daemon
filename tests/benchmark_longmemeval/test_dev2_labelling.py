"""Boundary tests for the frozen dev2 labelling protocol."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest

from tests.benchmark_longmemeval import dev2_labelling
from tests.benchmark_longmemeval.dev2_labelling import (
    LABELLER_MODEL,
    LABELLER_ROUTE_ID,
    LABELLER_SEED,
    LABEL_SCHEMA,
    MEMORY_SQL,
    PROMPT_V1,
    LabelInput,
    artifact_hashes,
    build_request,
    load_answer_session_memories,
    parse_label,
    spot_check_sample,
)

# Pinned at freeze time; any change to the prompt, schema or template fails here.
PINNED_HASHES = {
    "prompt": "8287ddf4385a1ce5908b9b007f82bdb9b345e263778dda373c861a7b9fb894b7",
    "label_schema": "ef2a02089d0f41dea5d4f22c1bed57bb85e1b8463b5efcb47b0c7dfa65b3265f",
    "request_template": "cc60e376ba92180ded2c6bb622bb458f0b412a7a4ee4cb89f937892c9c718c9d",
}

SENTINEL = "SENTINEL_MEMORY_TEXT_do_not_leak"

ITEM = LabelInput(
    question_id="q-1",
    question="Where did the owner leave the keys?",
    reference_answer="In the top drawer of the hallway cabinet.",
    memories=(
        ("mem-2", "Keys are kept in the hallway cabinet's top drawer."),
        ("mem-1", f"{SENTINEL} Unrelated note about groceries."),
    ),
)


def test_pinned_artifact_hashes() -> None:
    assert artifact_hashes() == PINNED_HASHES


def test_prompt_avoids_ranking_and_retrieval_language() -> None:
    lowered = PROMPT_V1.lower()
    for word in ("rank", "retriev", "score", "candidate", "recall"):
        assert word not in lowered


def test_schema_is_strict_and_closed() -> None:
    assert LABEL_SCHEMA["additionalProperties"] is False
    assert LABEL_SCHEMA["required"] == ["memory_ids", "answerable_from_store", "note"]
    assert set(LABEL_SCHEMA["properties"]) == {
        "memory_ids",
        "answerable_from_store",
        "note",
    }
    assert LABEL_SCHEMA["properties"]["memory_ids"]["items"] == {"type": "string"}


def test_request_shape_has_no_temperature_and_pins_route() -> None:
    request = build_request(ITEM)
    assert "temperature" not in request
    assert request["model"] == LABELLER_MODEL == "openrouter/openai/gpt-6-luna"
    assert LABELLER_ROUTE_ID == "luna-azure-eu"
    assert request["seed"] == LABELLER_SEED == 42
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["response_format"]["json_schema"]["schema"] == LABEL_SCHEMA
    user = request["messages"][1]["content"]
    assert ITEM.question in user
    assert ITEM.reference_answer in user
    # Both memories are present, in ascending id order.
    assert user.index("mem-1") < user.index("mem-2")


def test_memories_are_sorted_by_id_regardless_of_input_order() -> None:
    forward = LabelInput("q", "question?", "answer.", (("b", "beta"), ("a", "alpha")))
    backward = LabelInput("q", "question?", "answer.", (("a", "alpha"), ("b", "beta")))
    assert forward == backward
    assert forward.memories == (("a", "alpha"), ("b", "beta"))
    assert build_request(forward) == build_request(backward)
    assert (
        build_request(forward)["messages"][1]["content"]
        == build_request(backward)["messages"][1]["content"]
    )


def test_duplicate_input_ids_rejected() -> None:
    with pytest.raises(ValueError):
        LabelInput("q", "question?", "answer.", (("a", "one"), ("a", "two")))


def test_parse_label_accepts_valid_output() -> None:
    text = json.dumps(
        {"memory_ids": ["mem-2", "mem-1"], "answerable_from_store": True, "note": "Both bear."}
    )
    label = parse_label(ITEM, text)
    assert label.memory_ids == ("mem-2", "mem-1")
    assert label.answerable_from_store is True
    assert label.note == "Both bear."


def test_parse_label_accepts_not_answerable_with_empty_ids() -> None:
    text = json.dumps(
        {"memory_ids": [], "answerable_from_store": False, "note": "Memories do not support it."}
    )
    label = parse_label(ITEM, text)
    assert label.memory_ids == ()
    assert label.answerable_from_store is False


BAD_LABELS = {
    "malformed json": '{"memory_ids": [',
    "not an object": "[]",
    "extra field": json.dumps(
        {
            "memory_ids": ["mem-1"],
            "answerable_from_store": True,
            "note": "n",
            "rationale": "x",
        }
    ),
    "missing field": json.dumps({"memory_ids": ["mem-1"], "answerable_from_store": True}),
    "wrong type ids": json.dumps(
        {"memory_ids": "mem-1", "answerable_from_store": True, "note": "n"}
    ),
    "wrong type id element": json.dumps(
        {"memory_ids": [7], "answerable_from_store": True, "note": "n"}
    ),
    "wrong type bool": json.dumps(
        {"memory_ids": ["mem-1"], "answerable_from_store": "true", "note": "n"}
    ),
    "wrong type note": json.dumps(
        {"memory_ids": ["mem-1"], "answerable_from_store": True, "note": 4}
    ),
    "unknown id": json.dumps({"memory_ids": ["mem-9"], "answerable_from_store": True, "note": "n"}),
    "duplicate ids": json.dumps(
        {"memory_ids": ["mem-1", "mem-1"], "answerable_from_store": True, "note": "n"}
    ),
    "empty ids but answerable": json.dumps(
        {"memory_ids": [], "answerable_from_store": True, "note": "n"}
    ),
    "ids but not answerable": json.dumps(
        {"memory_ids": ["mem-1"], "answerable_from_store": False, "note": "n"}
    ),
}


@pytest.mark.parametrize("text", list(BAD_LABELS.values()), ids=list(BAD_LABELS))
def test_parse_label_rejects_bad_output(text: str) -> None:
    with pytest.raises(ValueError):
        parse_label(ITEM, text)


@pytest.mark.parametrize("text", list(BAD_LABELS.values()), ids=list(BAD_LABELS))
def test_error_messages_never_contain_memory_text(text: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_label(ITEM, text)
    message = str(excinfo.value)
    assert SENTINEL not in message
    assert "top drawer" not in message


def test_memory_sql_touches_only_memories() -> None:
    lowered = MEMORY_SQL.lower()
    assert "from memories" in lowered
    assert lowered.count("from ") == 1
    assert "join" not in lowered
    for forbidden in ("embedding", "retrieval", "chunk", "document", "rank"):
        assert forbidden not in lowered
    assert "status = 'active'" in lowered
    assert "user_id = $1" in lowered
    assert "source_conversation_id = any($2::uuid[])" in lowered


def test_loader_uses_injected_reader_and_decrypt() -> None:
    seen: dict[str, Any] = {}

    async def reader(sql: str, args: Any) -> list[tuple[str, str]]:
        seen["sql"] = sql
        seen["args"] = args
        return [("mem-b", "cipher-b"), ("mem-a", "cipher-a")]

    def decrypt(value: str) -> str:
        return value.replace("cipher", "plain")

    result = asyncio.run(
        load_answer_session_memories(reader, decrypt, "user-1", ["conv-1", "conv-2"])
    )
    assert result == (("mem-a", "plain-a"), ("mem-b", "plain-b"))
    assert seen["sql"] == MEMORY_SQL
    assert seen["args"] == ("user-1", ["conv-1", "conv-2"])


def test_loader_without_conversations_reads_nothing() -> None:
    def reader(sql: str, args: Any) -> list[tuple[str, str]]:
        raise AssertionError("reader must not be called")

    assert asyncio.run(load_answer_session_memories(reader, str, "user-1", [])) == ()


def test_spot_check_sample_counts_for_14_and_100() -> None:
    assert len(spot_check_sample([f"id-{i:03d}" for i in range(14)])) == 3
    assert len(spot_check_sample([f"id-{i:03d}" for i in range(100)])) == 15


def test_spot_check_sample_minimum_and_determinism() -> None:
    small = ["a", "b", "c"]
    sample = spot_check_sample(small)
    assert len(sample) == 2  # ceil(0.15 * 3) = 1, raised to the minimum
    assert sample == spot_check_sample(list(reversed(small)))
    assert sample == spot_check_sample(small, seed=42)
    assert set(sample) <= set(small)
    assert len(set(sample)) == len(sample)
    big = [f"id-{i:03d}" for i in range(100)]
    assert spot_check_sample(big) == spot_check_sample(big)


def test_spot_check_sample_rejects_impossible_fraction() -> None:
    with pytest.raises(ValueError):
        spot_check_sample(["a"], fraction=0.0)


# --- ``label`` command --------------------------------------------------------
#
# Fakes only: no provider, database or dataset is touched.

SENTINEL = "SENTINEL-MEMORY-TEXT-4a1f"
LABEL_TEXT = '{"memory_ids": ["m1"], "answerable_from_store": true, "note": "the river is named"}'


def _checkpoint(*pairs: tuple[str, str, str]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for question_id, session_id, conversation_id in pairs:
        results[f"{question_id}:0"] = {
            "status": "complete",
            "session_id": session_id,
            "conversation_id": conversation_id,
        }
    return {"phases": {"ingest": {"results": results, "pending_sessions": {}}}}


def _row(question_id: str = "q1", session_id: str = "s1") -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question": "Where did I park?",
        "answer": "By the river.",
        "answer_session_ids": [session_id],
        "user_id": f"user-{question_id}",
    }


def _receipt(content: str, *, cost: str = "0.01", provider: str = "azure") -> dict[str, Any]:
    return {
        "provider": provider,
        "usage": {"cost": cost},
        "choices": [{"message": {"content": content}}],
    }


def _run(
    tmp_path: Any,
    *,
    rows: list[dict[str, Any]],
    checkpoint: dict[str, Any],
    call: Any,
    check_attestation: Any = None,
    verify_route: Any = None,
    memory_reader: Any = None,
    max_spend: str = "1.00",
    name: str = "labels.json",
    ledger: Any = None,
    ceiling: Any = None,
    clock: Any = None,
) -> tuple[dict[str, Any], Any]:
    output = tmp_path / name
    document = asyncio.run(
        dev2_labelling.label_questions(
            question_rows=rows,
            checkpoint=checkpoint,
            output_path=output,
            max_spend_usd=Decimal(max_spend),
            call=call,
            check_attestation=check_attestation or (lambda: None),
            verify_route=verify_route or (lambda: None),
            memory_reader=memory_reader or (lambda user_id, ids: [("m1", SENTINEL)]),
            decrypt=lambda text: text,
            read_ledger_spend_usd=ledger or (lambda: Decimal(0)),
            next_call_ceiling_usd=ceiling or (lambda payload: Decimal("0.01")),
            **({"clock": clock} if clock is not None else {}),
        )
    )
    return document, output


def test_attestation_refusal_makes_zero_calls(tmp_path: Any) -> None:
    calls: list[Any] = []

    def refuse() -> None:
        raise ValueError("attestation refused")

    with pytest.raises(ValueError, match="attestation refused"):
        _run(
            tmp_path,
            rows=[_row()],
            checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
            call=lambda payload: calls.append(payload),
            check_attestation=refuse,
        )
    assert calls == []


def test_route_refusal_makes_zero_calls(tmp_path: Any) -> None:
    calls: list[Any] = []

    def refuse() -> None:
        raise ValueError("route unapproved")

    with pytest.raises(ValueError, match="route unapproved"):
        _run(
            tmp_path,
            rows=[_row()],
            checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
            call=lambda payload: calls.append(payload),
            verify_route=refuse,
        )
    assert calls == []


def test_parse_failure_records_schema_invalid_without_retry(tmp_path: Any) -> None:
    calls: list[dict[str, Any]] = []

    def call(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        return _receipt("not a label")

    document, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=call,
    )

    assert len(calls) == 1
    assert calls[0]["_route_id"] == LABELLER_ROUTE_ID
    assert calls[0]["_exact_model"] is True
    assert calls[0]["model"] == LABELLER_MODEL
    entry = document["questions"][0]
    assert entry["schema_valid"] is False
    assert entry["reason"] == "parse_failure"
    text = output.read_text(encoding="utf-8")
    assert "not a label" not in text
    assert SENTINEL not in text


def test_spend_cap_stops_before_the_next_call(tmp_path: Any) -> None:
    calls: list[dict[str, Any]] = []
    charged: list[Decimal] = []

    def call(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        charged.append(Decimal("0.05"))
        return _receipt(LABEL_TEXT, cost="0.05")

    document, _ = _run(
        tmp_path,
        rows=[_row("q1"), _row("q2", "s2")],
        checkpoint=_checkpoint(
            ("q1", "s1", "11111111-1111-1111-1111-111111111111"),
            ("q2", "s2", "22222222-2222-2222-2222-222222222222"),
        ),
        call=call,
        max_spend="0.05",
        ledger=lambda: sum(charged, Decimal(0)),
    )

    assert len(calls) == 1
    assert document["stop_reason"] == "spend_cap"
    assert len(document["questions"]) == 1
    assert document["total_cost_usd"] == "0.05"


def test_prior_ledger_spend_blocks_the_first_call(tmp_path: Any) -> None:
    calls: list[Any] = []
    document, _ = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=lambda payload: calls.append(payload),
        max_spend="1.00",
        ledger=lambda: Decimal("0.99"),
        ceiling=lambda payload: Decimal("0.02"),
    )

    assert calls == []
    assert document["stop_reason"] == "spend_cap"
    assert document["calls"] == 0


def test_next_call_ceiling_crossing_the_cap_is_refused(tmp_path: Any) -> None:
    calls: list[Any] = []
    document, _ = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=lambda payload: calls.append(payload),
        max_spend="0.05",
        ledger=lambda: Decimal("0.04"),
        ceiling=lambda payload: Decimal("0.02"),
    )

    assert calls == []
    assert document["stop_reason"] == "spend_cap"
    assert document["total_cost_usd"] == "0"


def test_open_holds_count_in_the_reused_ledger_reader() -> None:
    from tests.benchmark_longmemeval.dev2_pilot import spend_from_row

    breakdown = spend_from_row({"settled_microusd": 1_000_000, "held_microusd": 500_000})
    assert breakdown.settled_usd == 1.0
    assert breakdown.held_usd == 0.5
    assert breakdown.total_usd == 1.5


def test_unreadable_ledger_sends_nothing(tmp_path: Any) -> None:
    calls: list[Any] = []

    def ledger() -> Decimal:
        raise RuntimeError("ledger unavailable")

    document, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=lambda payload: calls.append(payload),
        ledger=ledger,
    )

    assert calls == []
    assert document["stop_reason"] == "spend_unreadable"
    assert output.exists()


def test_unknown_ceiling_sends_nothing(tmp_path: Any) -> None:
    calls: list[Any] = []

    def ceiling(payload: Any) -> Decimal:
        raise ValueError("no price ceiling")

    document, _ = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=lambda payload: calls.append(payload),
        ceiling=ceiling,
    )

    assert calls == []
    assert document["stop_reason"] == "ceiling_unknown"


def test_missing_cost_stops_after_that_call(tmp_path: Any) -> None:
    calls: list[Any] = []

    def call(payload: Any) -> dict[str, Any]:
        calls.append(payload)
        return {"provider": "azure", "choices": [{"message": {"content": LABEL_TEXT}}]}

    document, output = _run(
        tmp_path,
        rows=[_row("q1"), _row("q2", "s2")],
        checkpoint=_checkpoint(
            ("q1", "s1", "11111111-1111-1111-1111-111111111111"),
            ("q2", "s2", "22222222-2222-2222-2222-222222222222"),
        ),
        call=call,
    )

    assert len(calls) == 1
    assert document["stop_reason"] == "cost_unknown"
    entry = document["questions"][0]
    assert entry["cost_usd"] is None
    assert entry["reason"] == "cost_unknown"
    assert document["total_cost_usd"] == "0"
    assert output.exists()


def test_price_ceiling_never_under_the_worst_case() -> None:
    class _Price:
        microusd_per_1m_prompt = 250_000
        microusd_per_1m_completion = 1_000_000

    class _Route:
        price_ceiling = _Price()

    ceiling = dev2_labelling.price_ceiling_reader(_Route())
    payload = build_request(ITEM)
    bound = len(json.dumps(payload["messages"], sort_keys=True, ensure_ascii=False).encode("utf-8"))
    worst = (
        Decimal(bound) * Decimal(250_000) / Decimal(1_000_000)
        + Decimal(4000) * Decimal(1_000_000) / Decimal(1_000_000)
    ) / Decimal(1_000_000)
    assert ceiling(payload) >= worst
    # A bigger input can never read cheaper.
    bigger = dict(payload)
    bigger["messages"] = [{"role": "user", "content": "x" * (bound + 1000)}]
    assert ceiling(bigger) >= ceiling(payload)


def test_memory_text_never_reaches_the_output(tmp_path: Any) -> None:
    document, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=lambda payload: _receipt(LABEL_TEXT),
    )

    entry = document["questions"][0]
    assert entry["schema_valid"] is True
    assert entry["serving_provider"] == "azure"
    assert entry["memory_ids"] == ["m1"]
    text = output.read_text(encoding="utf-8")
    assert SENTINEL not in text


def test_dispatch_failure_is_uncertain_and_leaks_no_memory_text(tmp_path: Any) -> None:
    def call(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"provider echoed {SENTINEL}")

    document, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=call,
    )
    assert document["stop_reason"] == "uncertain_dispatch"
    assert document["questions"] == []
    # A charged-but-unresolved call is journal-only: no final document, no rerun.
    assert not output.exists()
    journal = tmp_path / "labels.json.receipts.jsonl"
    assert journal.exists()
    assert SENTINEL not in journal.read_text(encoding="utf-8")


def test_no_answer_memories_makes_no_call(tmp_path: Any) -> None:
    calls: list[Any] = []

    document, _ = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=lambda payload: calls.append(payload),
        memory_reader=lambda user_id, ids: [],
    )

    assert calls == []
    entry = document["questions"][0]
    assert entry["answerable_from_store"] is False
    assert entry["reason"] == "no_memories"
    assert entry["calls"] == 0


def test_output_is_never_overwritten(tmp_path: Any) -> None:
    (tmp_path / "labels.json").write_text("{}", encoding="utf-8")
    calls: list[Any] = []
    with pytest.raises(FileExistsError):
        _run(
            tmp_path,
            rows=[_row()],
            checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
            call=lambda payload: calls.append(payload),
        )
    assert calls == []
    assert (tmp_path / "labels.json").read_text(encoding="utf-8") == "{}"
    assert not (tmp_path / "labels.json.receipts.jsonl").exists()


def test_journal_records_intent_then_result(tmp_path: Any) -> None:
    _, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=lambda payload: _receipt(LABEL_TEXT),
    )
    journal = tmp_path / f"{output.name}.receipts.jsonl"
    records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [record["kind"] for record in records] == ["intent", "result"]
    assert records[0]["question_id"] == "q1"
    assert records[1]["question_id"] == "q1"
    assert records[1]["memory_ids"] == ["m1"]
    assert SENTINEL not in journal.read_text(encoding="utf-8")


def test_failure_after_one_question_keeps_it_and_rerun_does_not_redispatch(
    tmp_path: Any,
) -> None:
    rows = [_row("q1"), _row("q2", "s2")]
    checkpoint = _checkpoint(
        ("q1", "s1", "11111111-1111-1111-1111-111111111111"),
        ("q2", "s2", "22222222-2222-2222-2222-222222222222"),
    )
    calls: list[Any] = []

    def flaky(payload: Any) -> dict[str, Any]:
        calls.append(payload)
        if len(calls) == 2:
            raise RuntimeError("provider exploded")
        return _receipt(LABEL_TEXT)

    document, output = _run(tmp_path, rows=rows, checkpoint=checkpoint, call=flaky)
    assert len(calls) == 2
    assert document["stop_reason"] == "uncertain_dispatch"
    assert [entry["question_id"] for entry in document["questions"]] == ["q1"]
    assert document["total_cost_usd"] == "0.01"
    assert not output.exists()

    rerun_calls: list[Any] = []
    second, _ = _run(
        tmp_path,
        rows=rows,
        checkpoint=checkpoint,
        call=lambda payload: rerun_calls.append(payload),
    )
    assert rerun_calls == []
    assert second["stop_reason"] == "uncertain_dispatch"
    assert [entry["question_id"] for entry in second["questions"]] == ["q1"]
    assert second["total_cost_usd"] == "0.01"
    assert second["calls"] == 1


def test_intent_without_result_never_redispatches(tmp_path: Any) -> None:
    journal = tmp_path / "labels.json.receipts.jsonl"
    journal.write_text(json.dumps({"kind": "intent", "question_id": "q1"}) + "\n", encoding="utf-8")
    calls: list[Any] = []

    document, _ = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=lambda payload: calls.append(payload),
    )

    assert calls == []
    assert document["stop_reason"] == "uncertain_dispatch"


def test_complete_resume_matches_an_uninterrupted_run(tmp_path: Any) -> None:
    rows = [_row("q1"), _row("q2", "s2")]
    checkpoint = _checkpoint(
        ("q1", "s1", "11111111-1111-1111-1111-111111111111"),
        ("q2", "s2", "22222222-2222-2222-2222-222222222222"),
    )

    class _Clock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            self.value += 1.0
            return self.value

    def call(payload: Any) -> dict[str, Any]:
        return _receipt(LABEL_TEXT)

    # First run only reaches q1, as if the process died before writing the doc.
    first, first_output = _run(
        tmp_path,
        rows=rows[:1],
        checkpoint=checkpoint,
        call=call,
        name="resumed.json",
        clock=_Clock(),
    )
    assert first["stop_reason"] is None
    first_output.unlink()

    resumed, _ = _run(
        tmp_path,
        rows=rows,
        checkpoint=checkpoint,
        call=call,
        name="resumed.json",
        clock=_Clock(),
    )
    uninterrupted, _ = _run(
        tmp_path,
        rows=rows,
        checkpoint=checkpoint,
        call=call,
        name="fresh.json",
        clock=_Clock(),
    )

    assert resumed == uninterrupted
    assert resumed["calls"] == 2


# --- entry point -------------------------------------------------------------

DRY_RUN_UUID = "11111111-1111-1111-1111-111111111111"


def _inputs(tmp_path: Any) -> tuple[Any, Any]:
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([_row()]), encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(_checkpoint(("q1", "s1", DRY_RUN_UUID))), encoding="utf-8")
    return rows, checkpoint


def test_help_exits_zero_and_lists_the_flags(capsys: Any) -> None:
    with pytest.raises(SystemExit) as raised:
        dev2_labelling.main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    for flag in ("--question-rows", "--checkpoint", "--output", "--max-spend-usd", "--dry-run"):
        assert flag in output


def test_dry_run_makes_no_call_and_creates_no_files(tmp_path: Any, capsys: Any) -> None:
    rows, checkpoint = _inputs(tmp_path)
    before = set(tmp_path.iterdir())
    assert (
        dev2_labelling.main(
            [
                "--dry-run",
                "--question-rows",
                str(rows),
                "--checkpoint",
                str(checkpoint),
                "--output",
                str(tmp_path / "labels.json"),
            ]
        )
        == 0
    )
    assert set(tmp_path.iterdir()) == before
    assert "no calls will be made" in capsys.readouterr().out


def test_bad_arguments_do_not_silently_succeed() -> None:
    with pytest.raises(SystemExit) as raised:
        dev2_labelling.main(["label", "--bogus"])
    assert raised.value.code == 2
    with pytest.raises(SystemExit) as raised:
        dev2_labelling.main([])
    assert raised.value.code == 2


def test_label_arguments_require_output_and_cap(tmp_path: Any) -> None:
    rows, checkpoint = _inputs(tmp_path)
    with pytest.raises(SystemExit) as raised:
        dev2_labelling.main(
            ["label", "--question-rows", str(rows), "--checkpoint", str(checkpoint)]
        )
    assert raised.value.code == 2
