"""Boundary tests for the frozen dev2 labelling protocol."""

from __future__ import annotations

import asyncio
import fcntl
import json
from collections.abc import Sequence
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.benchmark_longmemeval import dev2_labelling
from tests.benchmark_longmemeval.dev2_labelling import (
    LABELLER_MODEL,
    LABELLER_ROUTE_ID,
    LABELLER_SEED,
    LABEL_RECORD_SCHEMA_V2 as LABEL_RECORD_SCHEMA,
    LABEL_RECORD_SCHEMAS,
    LABEL_SCHEMA_V1 as LABEL_SCHEMA,
    MEMORY_SQL,
    MEMORY_SQL_V2,
    PROMPT_V1,
    PROMPT_V2,
    LabelInput,
    LabelInputV2,
    artifact_hashes,
    build_label_input_v2,
    build_request,
    build_spot_check_packet,
    build_spot_check_packet_v2,
    load_answer_session_memories,
    parse_label,
    session_dates_from_row,
    spot_check_sample,
    spot_check_selection,
)
from tests.benchmark_longmemeval.dev2_pilot import answer_conversation_ids

# Pinned at freeze time; any change to the prompt, schema or template fails here.
PINNED_HASHES = {
    "prompt": "8287ddf4385a1ce5908b9b007f82bdb9b345e263778dda373c861a7b9fb894b7",
    "label_schema": "ef2a02089d0f41dea5d4f22c1bed57bb85e1b8463b5efcb47b0c7dfa65b3265f",
    "request_template": "cc60e376ba92180ded2c6bb622bb458f0b412a7a4ee4cb89f937892c9c718c9d",
}

# Pinned when v2 was added: a wording or template change must be deliberate too.
PINNED_V2_HASHES = {
    "prompt_v2": "43e52a1839d7e4e85b8a72aec8ac0d620a663b006d1147bb4d4350137997d677",
    "label_record_schema_v2": "87255b59cda0cb8a6aa896d01f045d3d262a9aef6a1c36ff3ff2ce9a927c2a5e",
    "request_template_v2": "2bb192d242528e3ef43776c213032cb463f9c79067c2645b2ac5bb93dac4cea1",
}

PINNED_V3_HASHES = {
    "prompt_v3": "c1c715a00611ffeaa2f86e9449eb63264e7884fe691859c5d998a98b32125698",
    "label_schema_v3": "4c8e7c091a350d7da93b680a62d5a81502f6c112db84fd0567c1afb8c48cf4b4",
    "label_record_schema_v3": "d66afce175b7794f0d728e72d20b3a3f24623a06f732d54308b5c2aecdcd2f00",
    "request_template_v3": "68c822ff191c4e9721635712ef15a49491492d92235645ea5339c50e594650be",
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
    hashes = artifact_hashes()
    # v1's three pins are unchanged, so v1 stays comparable across the version bump.
    assert {key: hashes[key] for key in PINNED_HASHES} == PINNED_HASHES
    assert set(hashes) == set(PINNED_HASHES) | set(PINNED_V2_HASHES) | set(PINNED_V3_HASHES)
    assert {key: hashes[key] for key in PINNED_V2_HASHES} == PINNED_V2_HASHES
    assert {key: hashes[key] for key in PINNED_V3_HASHES} == PINNED_V3_HASHES
    assert LABEL_RECORD_SCHEMAS == {"dev2-labelling/1", LABEL_RECORD_SCHEMA, "dev2-labelling/3"}


def test_v2_prompt_states_the_date_rules() -> None:
    assert "Resolve relative expressions" in PROMPT_V2
    assert "session date" in PROMPT_V2
    assert "question's date" in PROMPT_V2
    assert PROMPT_V2.startswith(PROMPT_V1)
    # The biased sentence is gone: the prompt must not push toward answerable.
    assert "don't treat it as" not in PROMPT_V2
    assert "memory states no absolute date" not in PROMPT_V2


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


# --- v2: dated, attributed inputs ---------------------------------------------


def test_v2_input_sorts_by_session_date_then_session_then_memory() -> None:
    item = LabelInputV2(
        question_id="q",
        question="question?",
        question_date="2023-05-30 23:18",
        reference_answer="answer.",
        memories=(
            ("m-b", "s2", "2023-05-30 18:00", "later date"),
            ("m-a", "s1", "2023-05-30 10:00", "earlier date"),
            ("m-c", "s1", "2023-05-30 10:00", "same session, later id"),
            ("m-0", "s10", "2023-05-30 10:00", "session ten sorts between s1 and s2"),
        ),
    )
    assert [memory[0] for memory in item.memories] == ["m-a", "m-c", "m-0", "m-b"]
    assert [memory[1] for memory in item.memories] == ["s1", "s1", "s10", "s2"]


def test_v2_tied_session_dates_sort_stably() -> None:
    common = {
        "question_id": "q",
        "question": "question?",
        "question_date": "2023-05-30 23:18",
        "reference_answer": "answer.",
    }
    date = "2023-05-30 10:00"
    forward = LabelInputV2(
        memories=(("m2", "s2", date, "two"), ("m1", "s1", date, "one")), **common
    )
    backward = LabelInputV2(
        memories=(("m1", "s1", date, "one"), ("m2", "s2", date, "two")), **common
    )
    assert forward == backward
    assert [memory[0] for memory in forward.memories] == ["m1", "m2"]


def test_v2_memory_date_comes_from_the_dataset_not_the_database_row() -> None:
    # The SQL selects no timestamp at all, so no row can carry a date into the input.
    lowered = MEMORY_SQL_V2.lower()
    for forbidden in ("created_at", "timestamp", "updated_at", "select *"):
        assert forbidden not in lowered
    # A contradictory timestamp on the database row is never read: the loader
    # returns only (id, conversation, text), and the date comes from the dataset.
    row = {
        "question_id": "q1",
        "question": "Where did I park?",
        "answer": "By the river.",
        "answer_session_ids": ["s1"],
        "user_id": "user-q1",
        "question_date": "2023/05/30 (Tue) 23:18",
        "haystack_session_ids": ["s1"],
        "haystack_dates": ["2023/05/30 (Tue) 09:41"],
    }
    database_row = ("m1", Q1_CONVERSATION, "text", "2030/01/01 (Tue) 00:00")

    async def reader(_sql: str, _args: Any) -> list[tuple[str, str, str, str]]:
        return [database_row]

    loaded = asyncio.run(
        dev2_labelling.load_answer_session_memories_v2(reader, str, "user-q1", [Q1_CONVERSATION])
    )
    assert loaded == (("m1", Q1_CONVERSATION, "text"),)
    conversations = answer_conversation_ids(
        _checkpoint(("q1", "s1", Q1_CONVERSATION)), "q1", ["s1"]
    )
    item = build_label_input_v2(row, conversations, loaded)
    assert item.question_date == "2023-05-30 23:18"
    assert item.memories == (("m1", "s1", "2023-05-30 09:41", "text"),)


def test_v2_session_dates_refuse_mismatched_or_unparsable_datasets() -> None:
    assert session_dates_from_row(
        {"haystack_session_ids": ["s1"], "haystack_dates": ["2023/05/30 (Tue) 09:41"]}
    ) == {"s1": "2023-05-30 09:41"}
    with pytest.raises(ValueError):
        session_dates_from_row({"haystack_session_ids": ["s1", "s2"], "haystack_dates": []})
    with pytest.raises(ValueError):
        session_dates_from_row({"haystack_session_ids": ["s1"], "haystack_dates": ["not a date"]})


def test_v2_refuses_a_database_that_is_not_the_remap() -> None:
    assert (
        dev2_labelling.assert_label_database_v2(
            "postgresql://user:pass@host/daemon_lme_bench_dev2_final_remap"
        )
        == "daemon_lme_bench_dev2_final_remap"
    )
    for dsn in (
        None,
        "",
        "postgresql://user:pass@host/daemon_lme_bench_dev2",
        "postgresql://user:pass@host/daemon",
    ):
        with pytest.raises(ValueError, match="refusing database"):
            dev2_labelling.assert_label_database_v2(dsn)


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
LABEL_TEXT = '{"target_groups": [["m1"]], "answerable_from_store": true, "needs_memory_date": false, "note": "the river is named"}'
STORED_DATE = "2023-05-30T18:00:00+00:00"


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
        # Synthetic dataset dates, in LongMemEval's format. V3 also reads the
        # synthetic stored valid_from supplied by the fake memory reader.
        "question_date": "2023/05/30 (Tue) 23:18",
        "haystack_session_ids": [session_id],
        "haystack_dates": ["2023/05/30 (Tue) 18:00"],
    }


def _receipt(content: str, *, cost: str = "0.01", provider: str = "azure") -> dict[str, Any]:
    return {
        "provider": provider,
        "usage": {"cost": cost},
        "choices": [{"message": {"content": content}}],
    }


_UNSET = object()


def _run(
    tmp_path: Any,
    *,
    rows: list[dict[str, Any]],
    checkpoint: dict[str, Any],
    call: Any,
    check_attestation: Any = None,
    verify_route: Any = None,
    acquire_run_lock: Any = _UNSET,
    memory_reader: Any = None,
    max_spend: str = "1.00",
    name: str = "labels.json",
    ledger: Any = None,
    ceiling: Any = None,
    clock: Any = None,
    check_run_lock: Any = None,
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
            acquire_run_lock=(lambda: True) if acquire_run_lock is _UNSET else acquire_run_lock,
            check_run_lock=check_run_lock or (lambda: True),
            verify_route=verify_route or (lambda: None),
            memory_reader=memory_reader
            or (lambda _sql, params: [("m1", params[1][0], SENTINEL, STORED_DATE)]),
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


def test_v2_unattributable_memory_stops_the_run(tmp_path: Any) -> None:
    """An unattributable memory is a run-level stop, never a not-answerable label."""
    second = ("q2", "s2", "22222222-2222-2222-2222-222222222222")
    cases = {
        "unknown conversation": {
            "rows": [_row("q1", "s1"), _row("q2", "s2")],
            "checkpoint": _checkpoint(("q1", "s1", Q1_CONVERSATION), second),
            "memory_reader": lambda _sql, params: [
                ("m1", "99999999-9999-9999-9999-999999999999", SENTINEL, STORED_DATE)
            ],
        },
        "session without a date": {
            "rows": [
                {
                    **_row("q1", "s1"),
                    "haystack_session_ids": ["another-session"],
                    "haystack_dates": ["2023/05/30 (Tue) 18:00"],
                },
                _row("q2", "s2"),
            ],
            "checkpoint": _checkpoint(("q1", "s1", Q1_CONVERSATION), second),
        },
    }
    for name, overrides in cases.items():
        calls: list[Any] = []
        document, output = _run(
            tmp_path,
            rows=overrides["rows"],
            checkpoint=overrides["checkpoint"],
            call=lambda payload: calls.append(payload),
            memory_reader=overrides.get("memory_reader"),
            name=f"{name.replace(' ', '-')}.json",
        )
        # No dispatch at all, including the question after the refused one.
        assert calls == [], name
        assert document["stop_reason"] == "unattributable", name
        # No entry: the refused question can never read as not-answerable.
        assert document["questions"] == [], name
        assert document["calls"] == 0, name
        # A stopped document: written with a non-null stop_reason, never complete.
        assert output.exists(), name
        saved = json.loads(output.read_text(encoding="utf-8"))
        assert saved["stop_reason"] == "unattributable", name
        assert saved["questions"] == [], name


def test_label_refuses_a_rows_file_without_dataset_dates(tmp_path: Any) -> None:
    """A refusal before any journal or document: the file never becomes a run."""
    valid = _row()
    rows: list[dict[str, Any]] = []
    for field in ("question_date", "haystack_session_ids", "haystack_dates"):
        row = dict(valid)
        del row[field]
        rows.append({**row, "question_id": f"missing-{field}"})
    rows.append({**valid, "question_id": "empty-question-date", "question_date": ""})
    rows.append({**valid, "question_id": "empty-haystack-dates", "haystack_dates": []})
    rows.append(
        {
            **valid,
            "question_id": "length-mismatch",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2023/05/30 (Tue) 18:00"],
        }
    )
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(_q1_checkpoint()), encoding="utf-8")

    for row in rows:
        question_rows = tmp_path / f"{row['question_id']}.json"
        question_rows.write_text(json.dumps([row]), encoding="utf-8")
        output = tmp_path / f"{row['question_id']}-labels.json"
        with pytest.raises(SystemExit) as excinfo:
            dev2_labelling.main(
                [
                    "label",
                    "--question-rows",
                    str(question_rows),
                    "--checkpoint",
                    str(checkpoint),
                    "--output",
                    str(output),
                    "--max-spend-usd",
                    "1.00",
                ]
            )
        assert excinfo.value.code == 2, row["question_id"]
        assert not output.exists(), row["question_id"]
        assert not dev2_labelling._journal_path(output).exists(), row["question_id"]


def test_v2_spend_cap_stops_before_the_first_dispatch(tmp_path: Any) -> None:
    calls: list[Any] = []
    document, _ = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_q1_checkpoint(),
        call=lambda payload: calls.append(payload),
        max_spend="0.05",
        ledger=lambda: Decimal("0.05"),
        ceiling=lambda payload: Decimal("0.01"),
    )
    assert calls == []
    assert document["stop_reason"] == "spend_cap"
    assert document["calls"] == 0
    assert document["artifacts"] == artifact_hashes()


def test_a_v1_journal_refuses_a_v2_resume(tmp_path: Any) -> None:
    rows = [_row()]
    journal = tmp_path / "labels.json.receipts.jsonl"
    v1_manifest = {
        "kind": "batch",
        "version": 1,
        # v1's protocol had only the three v1 artifacts and no dataset dates.
        "protocol": dict(PINNED_HASHES),
        "questions": {"q1": "0" * 64},
    }
    journal.write_text(json.dumps(v1_manifest) + "\n", encoding="utf-8")
    before = journal.read_text(encoding="utf-8")
    calls: list[Any] = []

    document, output = _run(
        tmp_path,
        rows=rows,
        checkpoint=_q1_checkpoint(),
        call=lambda payload: calls.append(payload),
    )

    assert calls == []
    assert document["stop_reason"] == "receipt_mismatch"
    assert not output.exists()
    assert journal.read_text(encoding="utf-8") == before


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
    # A real call with no known cost: the total must not read as complete.
    assert document["total_cost_complete"] is False
    assert document["unknown_cost_questions"] == ["q1"]
    assert output.exists()


def _pinned_price() -> Any:
    from orchestrator.entitlements.policy import PriceCeiling

    return PriceCeiling(microusd_per_1m_prompt=250_000, microusd_per_1m_completion=1_000_000)


def _pinned_route() -> Any:
    """A route with the pinned price ceiling and the production price method."""
    price = _pinned_price()

    class _Route:
        price_ceiling = price

        def estimate_microusd(self, prompt_tokens: int, completion_tokens: int) -> int:
            return price.estimate_microusd(prompt_tokens, completion_tokens)

    return _Route()


def _messages_only_ceiling(item: LabelInput) -> Decimal:
    """The old messages-byte bound, kept as the regression baseline."""
    payload = build_request(item)
    bound = len(json.dumps(payload["messages"], sort_keys=True, ensure_ascii=False).encode("utf-8"))
    micro = (Decimal(bound) * Decimal(250_000) + Decimal(4000) * Decimal(1_000_000)) / Decimal(
        1_000_000
    )
    return micro.to_integral_value(rounding=ROUND_CEILING) / Decimal(1_000_000)


def test_price_ceiling_comes_from_the_production_request_bound() -> None:
    from orchestrator import compute_runtime

    price = _pinned_price()
    ceiling = dev2_labelling.price_ceiling_reader(_pinned_route())
    # The small request from the review: `Where?` / `Here.` / one `Here.` memory.
    small = LabelInput("q-small", "Where?", "Here.", (("m1", "Here."),))

    for item in (ITEM, small):
        payload = {"_route_id": LABELLER_ROUTE_ID, "_exact_model": True, **build_request(item)}
        params = {
            key: value for key, value in payload.items() if key not in {"_route_id", "_exact_model"}
        }
        reserved = price.estimate_microusd(
            compute_runtime._request_bound(params).bound, dev2_labelling.LABELLER_MAX_TOKENS
        )
        # Exactly the production reservation: the schema and the per-message
        # framing count, so it covers what an uncertain dispatch can settle.
        assert ceiling(payload) == Decimal(reserved) / Decimal(1_000_000)
        # Strictly above the messages-only bound that used to price this guard.
        assert ceiling(payload) > _messages_only_ceiling(item)

    bigger = dict(build_request(ITEM))
    bigger["messages"] = [{"role": "user", "content": "x" * 100_000}]
    assert ceiling(bigger) >= ceiling(dict(build_request(ITEM)))


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
    assert entry["target_groups"] == [["m1"]]
    assert entry["needs_memory_date"] is False
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
    # The batch manifest is the first line and is durable before any dispatch.
    assert [record["kind"] for record in records] == ["batch", "intent", "result"]
    assert records[0]["version"] == 1
    assert records[0]["protocol"] == artifact_hashes()
    assert set(records[0]["questions"]) == {"q1"}
    assert records[1]["question_id"] == "q1"
    assert records[2]["question_id"] == "q1"
    assert records[2]["target_groups"] == [["m1"]]
    # The intent and the result are bound to the same inputs.
    assert records[1]["input_sha256"] == records[2]["input_sha256"]
    text = journal.read_text(encoding="utf-8")
    assert SENTINEL not in text
    # The journal stores a digest, never the question or reference-answer text.
    assert "Where did I park?" not in text
    assert "By the river." not in text


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
    rows = [_row()]
    checkpoint = _checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111"))
    journal = tmp_path / "labels.json.receipts.jsonl"
    journal.write_text(
        json.dumps(dev2_labelling._batch_manifest(rows))
        + "\n"
        + json.dumps({"kind": "intent", "question_id": "q1", "input_sha256": "0" * 64})
        + "\n",
        encoding="utf-8",
    )
    calls: list[Any] = []

    document, _ = _run(
        tmp_path,
        rows=rows,
        checkpoint=checkpoint,
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
        return _receipt(LABEL_TEXT, cost="0.05")

    charged: list[Decimal] = []

    def paying_call(payload: Any) -> dict[str, Any]:
        charged.append(Decimal("0.05"))
        return _receipt(LABEL_TEXT, cost="0.05")

    # Interrupted after q1: the cap stops the second dispatch mid-batch.
    first, first_output = _run(
        tmp_path,
        rows=rows,
        checkpoint=checkpoint,
        call=paying_call,
        max_spend="0.05",
        ledger=lambda: sum(charged, Decimal(0)),
        name="resumed.json",
        clock=_Clock(),
    )
    assert first["stop_reason"] == "spend_cap"
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


# --- benchmark run lock -------------------------------------------------------

Q1_CONVERSATION = "11111111-1111-1111-1111-111111111111"


def _q1_checkpoint() -> dict[str, Any]:
    return _checkpoint(("q1", "s1", Q1_CONVERSATION))


def test_busy_run_lock_sends_nothing_and_creates_nothing(tmp_path: Any) -> None:
    calls: list[Any] = []
    document, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_q1_checkpoint(),
        call=lambda payload: calls.append(payload),
        acquire_run_lock=lambda: False,
    )

    assert document["stop_reason"] == "run_lock_busy"
    assert document["questions"] == []
    assert calls == []
    assert not output.exists()
    assert not dev2_labelling._journal_path(output).exists()


def test_raising_run_lock_sends_nothing_and_creates_nothing(tmp_path: Any) -> None:
    calls: list[Any] = []

    def locked() -> bool:
        raise RuntimeError("lock unavailable")

    document, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_q1_checkpoint(),
        call=lambda payload: calls.append(payload),
        acquire_run_lock=locked,
    )

    assert document["stop_reason"] == "run_lock_busy"
    assert calls == []
    assert not output.exists()
    assert not dev2_labelling._journal_path(output).exists()


def test_missing_run_lock_stops_instead_of_dispatching(tmp_path: Any) -> None:
    calls: list[Any] = []
    document, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_q1_checkpoint(),
        call=lambda payload: calls.append(payload),
        acquire_run_lock=None,
    )

    # The lock is required, not optional: no path skips it.
    assert document["stop_reason"] == "run_lock_busy"
    assert calls == []
    assert not output.exists()
    assert not dev2_labelling._journal_path(output).exists()


def test_second_output_is_refused_while_the_run_lock_is_held(tmp_path: Any) -> None:
    held = {"held": False}

    def lock() -> bool:
        if held["held"]:
            return False
        held["held"] = True
        return True

    calls: list[Any] = []

    def call(payload: Any) -> dict[str, Any]:
        calls.append(payload)
        return _receipt(LABEL_TEXT)

    # Spend is close enough to the cap that the second run would pass the
    # pre-dispatch check and dispatch: only the shared lock can refuse it.
    first, _ = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_q1_checkpoint(),
        call=call,
        name="first.json",
        max_spend="0.05",
        ledger=lambda: Decimal("0.04"),
        ceiling=lambda payload: Decimal("0.01"),
        acquire_run_lock=lock,
    )
    assert first["stop_reason"] is None
    assert len(calls) == 1

    second, second_output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_q1_checkpoint(),
        call=call,
        name="second.json",
        max_spend="0.05",
        ledger=lambda: Decimal("0.04"),
        ceiling=lambda payload: Decimal("0.01"),
        acquire_run_lock=lock,
    )

    assert second["stop_reason"] == "run_lock_busy"
    assert len(calls) == 1
    assert not second_output.exists()
    assert not dev2_labelling._journal_path(second_output).exists()


# --- lazy ceiling -------------------------------------------------------------


def test_ceiling_resolves_the_route_only_after_attestation(tmp_path: Any) -> None:
    order: list[str] = []
    state = {"attested": False, "resolved": 0}

    def verify() -> Any:
        order.append("verify")
        if not state["attested"]:
            # What a fresh process sees before the attestation snapshot loads.
            raise ValueError("zdr_attestation_unknown")
        state["resolved"] += 1
        return _pinned_route()

    def check_attestation() -> None:
        order.append("attest")
        state["attested"] = True

    ceiling = dev2_labelling.ceiling_from_route(verify)
    # Building the command's arguments resolves nothing.
    assert state["resolved"] == 0

    calls: list[Any] = []
    document, _ = _run(
        tmp_path,
        rows=[_row("q1"), _row("q2", "s2")],
        checkpoint=_checkpoint(
            ("q1", "s1", Q1_CONVERSATION),
            ("q2", "s2", "22222222-2222-2222-2222-222222222222"),
        ),
        call=lambda payload: calls.append(payload) or _receipt(LABEL_TEXT),
        check_attestation=check_attestation,
        ceiling=ceiling,
    )

    assert len(calls) == 2
    assert document["stop_reason"] is None
    # Attestation first, then the route, and only the once: the reader is cached.
    assert order == ["attest", "verify"]
    assert state["resolved"] == 1


# --- receipts are bound to their inputs --------------------------------------


def _seed_completed_run(
    tmp_path: Any,
    *,
    rows: list[dict[str, Any]],
    checkpoint: dict[str, Any],
    **kwargs: Any,
) -> None:
    document, output = _run(
        tmp_path,
        rows=rows,
        checkpoint=checkpoint,
        call=lambda payload: _receipt(LABEL_TEXT),
        **kwargs,
    )
    assert document["stop_reason"] is None
    assert output.exists()
    # Only the document is removed: the journal, and its manifest, stay.
    output.unlink()


def _assert_mismatch(document: dict[str, Any], calls: list[Any], output: Any, before: str) -> None:
    assert document["stop_reason"] == "receipt_mismatch"
    assert calls == []
    assert not output.exists()
    # The journal is preserved untouched: it is evidence, not a rerun target.
    assert dev2_labelling._journal_path(output).read_text(encoding="utf-8") == before


def test_changed_answer_on_resume_is_a_receipt_mismatch(tmp_path: Any) -> None:
    rows = [_row()]
    checkpoint = _q1_checkpoint()
    _seed_completed_run(tmp_path, rows=rows, checkpoint=checkpoint)
    journal = tmp_path / "labels.json.receipts.jsonl"
    before = journal.read_text(encoding="utf-8")

    calls: list[Any] = []
    document, output = _run(
        tmp_path,
        rows=[dict(rows[0], answer="By the sea.")],
        checkpoint=checkpoint,
        call=lambda payload: calls.append(payload),
    )

    _assert_mismatch(document, calls, output, before)


def test_changed_user_on_resume_is_a_receipt_mismatch(tmp_path: Any) -> None:
    rows = [_row()]
    checkpoint = _q1_checkpoint()
    _seed_completed_run(tmp_path, rows=rows, checkpoint=checkpoint)
    journal = tmp_path / "labels.json.receipts.jsonl"
    before = journal.read_text(encoding="utf-8")

    calls: list[Any] = []
    document, output = _run(
        tmp_path,
        rows=[dict(rows[0], user_id="user-someone-else")],
        checkpoint=checkpoint,
        call=lambda payload: calls.append(payload),
    )

    _assert_mismatch(document, calls, output, before)


def test_changed_checkpoint_mapping_on_resume_is_a_receipt_mismatch(tmp_path: Any) -> None:
    rows = [_row()]
    _seed_completed_run(tmp_path, rows=rows, checkpoint=_q1_checkpoint())
    journal = tmp_path / "labels.json.receipts.jsonl"
    before = journal.read_text(encoding="utf-8")

    calls: list[Any] = []
    document, output = _run(
        tmp_path,
        rows=rows,
        checkpoint=_checkpoint(("q1", "s1", "99999999-9999-9999-9999-999999999999")),
        call=lambda payload: calls.append(payload),
    )

    _assert_mismatch(document, calls, output, before)


def test_changed_memories_on_resume_is_a_receipt_mismatch(tmp_path: Any) -> None:
    rows = [_row()]
    checkpoint = _q1_checkpoint()
    _seed_completed_run(tmp_path, rows=rows, checkpoint=checkpoint)
    journal = tmp_path / "labels.json.receipts.jsonl"
    before = journal.read_text(encoding="utf-8")

    calls: list[Any] = []
    document, output = _run(
        tmp_path,
        rows=rows,
        checkpoint=checkpoint,
        call=lambda payload: calls.append(payload),
        memory_reader=lambda _sql, params: [
            ("m1", params[1][0], "a different memory", STORED_DATE)
        ],
    )

    _assert_mismatch(document, calls, output, before)


def test_changed_protocol_on_resume_is_a_receipt_mismatch(tmp_path: Any, monkeypatch: Any) -> None:
    rows = [_row()]
    checkpoint = _q1_checkpoint()
    _seed_completed_run(tmp_path, rows=rows, checkpoint=checkpoint)
    journal = tmp_path / "labels.json.receipts.jsonl"
    before = journal.read_text(encoding="utf-8")

    monkeypatch.setattr(dev2_labelling, "PROMPT_V1", PROMPT_V1 + "\nAlso be terse.")

    calls: list[Any] = []
    document, output = _run(
        tmp_path,
        rows=rows,
        checkpoint=checkpoint,
        call=lambda payload: calls.append(payload),
    )

    _assert_mismatch(document, calls, output, before)


def test_journal_without_a_manifest_stops(tmp_path: Any) -> None:
    journal = tmp_path / "labels.json.receipts.jsonl"
    journal.write_text(
        json.dumps(
            {
                "kind": "result",
                "question_id": "q1",
                "input_sha256": "0" * 64,
                "schema_valid": True,
                "answerable_from_store": True,
                "calls": 1,
                "cost_usd": "0.01",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    before = journal.read_text(encoding="utf-8")

    calls: list[Any] = []
    document, output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_q1_checkpoint(),
        call=lambda payload: calls.append(payload),
    )

    _assert_mismatch(document, calls, output, before)


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


def test_a_journal_held_by_another_run_sends_nothing(tmp_path: Any) -> None:
    calls: list[Any] = []

    def call(payload: Any) -> dict[str, Any]:
        calls.append(payload)
        return _receipt(LABEL_TEXT)

    journal = dev2_labelling._journal_path(tmp_path / "labels.json")
    journal.write_text("", encoding="utf-8")
    with open(journal, "a", encoding="utf-8") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="held by another run"):
            _run(
                tmp_path,
                rows=[_row()],
                checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
                call=call,
            )
    assert calls == []
    assert not (tmp_path / "labels.json").exists()


@pytest.mark.parametrize("check", [lambda: False, lambda: 1 / 0])
def test_a_lock_lost_before_dispatch_sends_nothing(tmp_path: Any, check: Any) -> None:
    calls: list[Any] = []

    def call(payload: Any) -> dict[str, Any]:
        calls.append(payload)
        return _receipt(LABEL_TEXT)

    document, _output = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_checkpoint(("q1", "s1", "11111111-1111-1111-1111-111111111111")),
        call=call,
        check_run_lock=check,
    )
    assert calls == []
    assert document["stop_reason"] == "run_lock_lost"


# --- spot-check selection and packet -----------------------------------------
#
# Fakes only: the packet command's connector raises if it is ever reached, so
# every refusal below proves that no connection was opened.


def _label_entry(
    question_id: str,
    *,
    schema_valid: bool = True,
    answerable: bool = True,
    reason: str | None = None,
    memory_ids: Sequence[str] = (),
    note: str = "one short sentence",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "question_id": question_id,
        "schema_valid": schema_valid,
        "answerable_from_store": answerable,
        "calls": 1,
        "cost_usd": "0.01",
        "latency_seconds": 0.5,
        "serving_provider": "azure",
    }
    if reason is not None:
        entry["reason"] = reason
    if schema_valid:
        entry["memory_ids"] = list(memory_ids)
        entry["note"] = note
    return entry


def _five_ids() -> list[str]:
    return [f"q{index}" for index in range(5)]


def test_spot_check_seeded_draw_is_unchanged() -> None:
    entries = [_label_entry(question_id) for question_id in _five_ids()]
    selection = spot_check_selection(entries)
    seeded = [question_id for question_id, reasons in selection if "seeded_sample" in reasons]
    assert seeded == list(spot_check_sample(_five_ids()))
    assert {reason for _, reasons in selection for reason in reasons} <= {
        "seeded_sample",
        "answerable_false",
    }


def test_spot_check_seeded_false_label_gets_both_reasons_once() -> None:
    entries = [_label_entry(question_id) for question_id in _five_ids()]
    target = spot_check_sample(_five_ids())[0]
    by_id = {entry["question_id"]: entry for entry in entries}
    by_id[target]["answerable_from_store"] = False
    by_id[target]["memory_ids"] = []

    selection = spot_check_selection(entries)
    assert [question_id for question_id, _ in selection].count(target) == 1
    assert dict(selection)[target] == ("seeded_sample", "answerable_false")


def test_spot_check_adds_not_answerable_labels_sorted_and_counts_no_memories() -> None:
    entries = [_label_entry(question_id) for question_id in _five_ids()]
    seeded = set(spot_check_sample(_five_ids()))
    extra = sorted(set(_five_ids()) - seeded)
    assert len(extra) == 3
    by_id = {entry["question_id"]: entry for entry in entries}

    by_id[extra[0]]["answerable_from_store"] = False
    by_id[extra[0]]["memory_ids"] = []
    by_id[extra[1]].update(
        {"schema_valid": False, "answerable_from_store": False, "reason": "no_memories"}
    )
    by_id[extra[1]].pop("memory_ids", None)
    by_id[extra[1]].pop("note", None)
    by_id[extra[2]].update(
        {"schema_valid": False, "answerable_from_store": False, "reason": "parse_failure"}
    )
    by_id[extra[2]].pop("memory_ids", None)
    by_id[extra[2]].pop("note", None)

    selection = spot_check_selection(entries)
    additional = [
        question_id for question_id, reasons in selection if "answerable_false" in reasons
    ]
    assert additional == sorted([extra[0], extra[1]])
    assert dict(selection)[extra[0]] == ("answerable_false",)
    assert dict(selection)[extra[1]] == ("answerable_false",)
    assert extra[2] not in {question_id for question_id, _ in selection}


PACKET_CONVERSATION = "22222222-2222-2222-2222-222222222222"


def _packet_rows() -> list[dict[str, Any]]:
    return [
        {**_row("q1", "s1"), "question": "Which card did I use?", "answer": "The blue card."},
        {**_row("q2", "s2"), "question": "Which vet do I use?", "answer": "The Oak Street vet."},
    ]


def _packet_checkpoint() -> dict[str, Any]:
    return _checkpoint(("q1", "s1", Q1_CONVERSATION), ("q2", "s2", PACKET_CONVERSATION))


def test_packet_marks_labelled_ids_and_states_the_rate_rule() -> None:
    labels = [
        _label_entry("q1", memory_ids=["m1"], note="Both memories cover it."),
        _label_entry("q2", schema_valid=False, answerable=False, reason="no_memories"),
    ]
    selection = [("q1", ("seeded_sample",)), ("q2", ("answerable_false",))]
    memories = {
        "q1": (("m1", "blue card"), ("m2", "backup card")),
        "q2": (),
    }

    packet = build_spot_check_packet(
        selection, labels, _packet_rows(), _packet_checkpoint(), memories
    )

    assert "seeded sample only" in packet
    assert "flags for owner review" in packet
    assert packet.count("## q1") == 1
    assert packet.count("## q2") == 1
    q1, q2 = packet.split("## q1", 1)[1].split("## q2", 1)
    assert "Seeded 15% sample" in q1
    assert "Every not-answerable label" not in q1
    assert "Which card did I use?" in q1
    assert "The blue card." in q1
    assert "[LABELLED] m1: blue card" in q1
    assert "[LABELLED] m2" not in q1
    assert "Both memories cover it." in q1
    assert "Every not-answerable label" in q2
    assert "Seeded 15% sample" not in q2
    assert "No answer-session memories were recorded for this question." in q2
    assert "answerable_from_store: false" in q2
    assert "labelled memory IDs correct?" in q2


def test_v2_packet_groups_by_session_date_and_shows_version_and_hash() -> None:
    memories = {
        "q1": (
            ("m2", "s2", "2023-05-30 11:00", "later session"),
            ("m1", "s1", "2023-05-30 09:00", "earlier session"),
        )
    }
    packet = build_spot_check_packet_v2(
        [("q1", ("seeded_sample",))],
        [_label_entry("q1", memory_ids=["m1", "m2"])],
        _packet_rows()[:1],
        _packet_checkpoint(),
        memories,
        label_schema=LABEL_RECORD_SCHEMA,
        prompt_sha256=PINNED_V2_HASHES["prompt_v2"],
    )
    assert f"Labeller version: `{LABEL_RECORD_SCHEMA}`" in packet
    assert f"Prompt hash (v2): `{PINNED_V2_HASHES['prompt_v2']}`" in packet
    assert "Question date: 2023-05-30 23:18" in packet
    assert packet.index("### Session s1 (2023-05-30 09:00)") < packet.index(
        "### Session s2 (2023-05-30 11:00)"
    )
    assert "[LABELLED] m1: earlier session" in packet
    assert "[LABELLED] m2: later session" in packet


def test_v2_packet_labels_refuse_a_document_without_artifacts() -> None:
    document = _batch_document("q1", "q2")
    document.pop("artifacts")
    with pytest.raises(ValueError, match="prompt hash"):
        dev2_labelling._packet_labels(document, _batch_rows(), {"q1", "q2"})


class _NullTransaction:
    async def __aenter__(self) -> _NullTransaction:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeConnection:
    """A read-only stand-in: returns canned memory rows and records the SQL."""

    def __init__(self, rows: list[tuple[str, str, str]]) -> None:
        self.rows = rows
        self.closed = False
        self.sql: list[str] = []

    def transaction(self, *, readonly: bool = False) -> _NullTransaction:
        assert readonly is True
        return _NullTransaction()

    async def fetch(self, sql: str, *args: Any) -> list[tuple[str, str, str]]:
        self.sql.append(sql)
        return self.rows

    async def close(self) -> None:
        self.closed = True


def test_v2_packet_dispatch_reads_dated_memories(tmp_path: Any, monkeypatch: Any) -> None:
    connection = _FakeConnection([("m1", Q1_CONVERSATION, "blue card")])
    monkeypatch.setattr(
        "orchestrator.config.get_settings",
        lambda: SimpleNamespace(database_url="postgresql://user@host/bench"),
    )
    labels = _batch_document("q1", "q2")

    async def connect(_url: str) -> _FakeConnection:
        return connection

    packet = asyncio.run(
        dev2_labelling._run_packet(
            labels=labels,
            rows=_packet_rows(),
            checkpoint=_packet_checkpoint(),
            questions=labels["questions"],
            selection=[("q1", ("seeded_sample",))],
            connector=connect,
            decrypt=str,
        )
    )
    assert connection.closed is True
    assert connection.sql == [MEMORY_SQL_V2]
    assert f"Labeller version: `{LABEL_RECORD_SCHEMA}`" in packet
    assert f"Prompt hash (v2): `{artifact_hashes()['prompt_v2']}`" in packet
    assert "### Session s1 (2023-05-30 18:00)" in packet
    assert "[LABELLED] m1: blue card" in packet


def _no_connect(database_url: str) -> Any:
    raise AssertionError(f"no connection may be opened: {database_url}")


def _write_packet_inputs(
    tmp_path: Any,
    *,
    stop_reason: str | None = None,
    questions: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any, Any]:
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps(_packet_rows()), encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(_packet_checkpoint()), encoding="utf-8")
    (tmp_path / "population.json").write_text(json.dumps(["q1", "q2"]), encoding="utf-8")
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "schema": "dev2-labelling/1",
                "stop_reason": stop_reason,
                "questions": questions
                if questions is not None
                else [_label_entry("q1", memory_ids=["m1"]), _label_entry("q2", memory_ids=["m2"])],
            }
        ),
        encoding="utf-8",
    )
    return rows, checkpoint, labels


def test_packet_refuses_an_output_inside_the_repository(tmp_path: Any) -> None:
    rows, checkpoint, labels = _write_packet_inputs(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    output = repo / "packet.md"
    with pytest.raises(ValueError):
        dev2_labelling.packet_main(
            [
                "--question-rows",
                str(rows),
                "--checkpoint",
                str(checkpoint),
                "--labels",
                str(labels),
                "--population",
                str(tmp_path / "population.json"),
                "--output",
                str(output),
            ],
            repo_root=repo,
            connector=_no_connect,
        )
    assert not output.exists()


def test_packet_refuses_an_existing_output_without_connecting(tmp_path: Any) -> None:
    rows, checkpoint, labels = _write_packet_inputs(tmp_path)
    output = tmp_path / "packet.md"
    output.write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        dev2_labelling.packet_main(
            [
                "--question-rows",
                str(rows),
                "--checkpoint",
                str(checkpoint),
                "--labels",
                str(labels),
                "--population",
                str(tmp_path / "population.json"),
                "--output",
                str(output),
            ],
            repo_root=tmp_path / "elsewhere",
            connector=_no_connect,
        )
    assert output.read_text(encoding="utf-8") == "keep me"


def test_packet_refuses_an_unfinished_label_document(tmp_path: Any) -> None:
    rows, checkpoint, labels = _write_packet_inputs(tmp_path, stop_reason="spend_cap")
    output = tmp_path / "packet.md"
    with pytest.raises(ValueError):
        dev2_labelling.packet_main(
            [
                "--question-rows",
                str(rows),
                "--checkpoint",
                str(checkpoint),
                "--labels",
                str(labels),
                "--population",
                str(tmp_path / "population.json"),
                "--output",
                str(output),
            ],
            repo_root=tmp_path / "elsewhere",
            connector=_no_connect,
        )
    assert not output.exists()


def test_packet_refuses_a_label_id_absent_from_the_rows(tmp_path: Any) -> None:
    rows, checkpoint, labels = _write_packet_inputs(
        tmp_path, questions=[_label_entry("q9", memory_ids=["m1"])]
    )
    output = tmp_path / "packet.md"
    with pytest.raises(ValueError):
        dev2_labelling.packet_main(
            [
                "--question-rows",
                str(rows),
                "--checkpoint",
                str(checkpoint),
                "--labels",
                str(labels),
                "--population",
                str(tmp_path / "population.json"),
                "--output",
                str(output),
            ],
            repo_root=tmp_path / "elsewhere",
            connector=_no_connect,
        )
    assert not output.exists()


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _batch_rows() -> list[dict[str, Any]]:
    return [{"question_id": q, "question": "Q", "answer": "A"} for q in ("q1", "q2")]


def _batch_document(*ids: str, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": dev2_labelling.LABEL_RECORD_SCHEMA_V2,
        "artifacts": artifact_hashes(),
        "stop_reason": None,
        "questions": [
            {
                "question_id": q,
                "schema_valid": True,
                "answerable_from_store": True,
                "memory_ids": ["m1"],
                "note": "n",
            }
            for q in ids
        ],
    }
    document.update(overrides)
    return document


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_batch_document("q1"), "missing"),
        (_batch_document("q1", "q2", "q2"), "duplicate"),
        (_batch_document("q1", "q2", "q3"), "absent"),
        (_batch_document("q1", "q2", schema="other"), "schema"),
        (_batch_document("q1", "q2", stop_reason="spend_cap"), "stopped"),
    ],
)
def test_packet_refuses_an_incomplete_or_malformed_batch(document: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        dev2_labelling._packet_labels(document, _batch_rows(), {"q1", "q2"})


def test_packet_accepts_a_complete_batch() -> None:
    assert (
        len(dev2_labelling._packet_labels(_batch_document("q1", "q2"), _batch_rows(), {"q1", "q2"}))
        == 2
    )


@pytest.mark.parametrize(
    ("entry", "expected", "absent"),
    [
        ({"schema_valid": True, "answerable_from_store": True}, ": true", "No valid"),
        ({"schema_valid": True, "answerable_from_store": False}, ": false", "No valid"),
        ({"schema_valid": False, "reason": "no_memories"}, "no stored memories", "No valid"),
        ({"schema_valid": False, "reason": "parse_failure"}, "parse_failure", ": false"),
    ],
)
def test_an_invalid_reply_is_never_shown_as_a_negative_label(
    entry: dict[str, Any], expected: str, absent: str
) -> None:
    line = dev2_labelling._packet_label_line(entry)
    assert expected in line
    assert absent not in line


def test_rows_and_labels_cannot_agree_on_an_incomplete_subset() -> None:
    rows = [{"question_id": "q1", "question": "Q", "answer": "A"}]
    with pytest.raises(ValueError, match="frozen --population"):
        dev2_labelling._packet_labels(_batch_document("q1"), rows, {"q1", "q2"})


def test_duplicate_rows_refuse() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        dev2_labelling._packet_labels(
            _batch_document("q1", "q2"), [*_batch_rows(), _batch_rows()[0]], {"q1", "q2"}
        )


@pytest.mark.parametrize("value", [[], {"question_ids": []}, {"other": ["q1"]}, ["q1", "q1"]])
def test_an_empty_or_malformed_population_refuses(tmp_path: Any, value: Any) -> None:
    path = tmp_path / "population.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="--population"):
        dev2_labelling._read_population(path)


@pytest.mark.parametrize(
    "broken",
    [
        {"answerable_from_store": None},
        {"answerable_from_store": "false"},
        {"memory_ids": None},
        {"note": None},
        {"schema_valid": "true"},
        {"schema_valid": False, "reason": None},
    ],
)
def test_a_malformed_entry_refuses_before_sampling(broken: dict[str, Any]) -> None:
    document = _batch_document("q1", "q2")
    document["questions"][0].update(broken)
    with pytest.raises(ValueError, match="--labels q1"):
        dev2_labelling._packet_labels(document, _batch_rows(), {"q1", "q2"})


def test_the_committed_pilot_population_has_all_fourteen() -> None:
    assert len(dev2_labelling._read_population(FIXTURES / "dev2_pilot_ids.json")) == 14


def test_v2_loader_accepts_asyncpg_uuid_columns() -> None:
    import asyncio
    import uuid as _uuid

    from tests.benchmark_longmemeval.dev2_labelling import load_answer_session_memories_v2

    conversation = _uuid.uuid4()
    memory = _uuid.uuid4()

    def reader(sql, params):
        return [(memory, conversation, "enc")]

    triples = asyncio.run(
        load_answer_session_memories_v2(reader, lambda text: "plain", "u1", [str(conversation)])
    )
    assert triples == ((str(memory), str(conversation), "plain"),)


def _v3_item() -> dev2_labelling.LabelInputV3:
    return dev2_labelling.LabelInputV3(
        "q1",
        "When did I park?",
        "2023-05-30 23:18",
        "May 30",
        (
            ("m2", "s1", "2023-05-30 18:00", "A paraphrase", STORED_DATE),
            ("m1", "s1", "2023-05-30 18:00", "I parked", STORED_DATE),
        ),
    )


def _v3_label(**updates: Any) -> dict[str, Any]:
    return {
        "target_groups": [["m1", "m2"]],
        "answerable_from_store": True,
        "needs_memory_date": True,
        "note": "temporal cue dropped at extraction; stored date supports it",
        **updates,
    }


def test_v3_schema_request_and_stored_date_contract() -> None:
    item = _v3_item()
    request = dev2_labelling.build_request_v3(item)
    schema = request["response_format"]["json_schema"]["schema"]
    assert schema == dev2_labelling.LABEL_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "target_groups",
        "answerable_from_store",
        "needs_memory_date",
        "note",
    }
    assert schema["properties"]["target_groups"]["items"]["minItems"] == 1
    assert schema["properties"]["needs_memory_date"] == {"type": "boolean"}
    assert "temperature" not in request
    assert request["model"] == LABELLER_MODEL
    assert request["seed"] == LABELLER_SEED
    assert request["reasoning_effort"] == "medium"
    assert request["max_tokens"] == 4000
    assert request["messages"][0]["content"] == dev2_labelling.PROMPT_V3
    user = request["messages"][1]["content"]
    assert '"valid_from"' in user and STORED_DATE in user
    assert user.index('"id": "m1"') < user.index('"id": "m2"')
    assert "Dates stored only in valid_from count as answerable" in dev2_labelling.PROMPT_V3
    assert "temporal cue was dropped at extraction" in dev2_labelling.PROMPT_V3
    label = dev2_labelling.parse_label_v3(item, json.dumps(_v3_label()))
    assert label.target_groups == (("m1", "m2"),)
    assert label.needs_memory_date is True


@pytest.mark.parametrize(
    "update",
    [
        {"target_groups": "m1"},
        {"target_groups": ["m1"]},
        {"target_groups": [[]]},
        {"target_groups": [[""]]},
        {"target_groups": [[" "]]},
        {"target_groups": [[1]]},
        {"target_groups": [["unknown"]]},
        {"target_groups": [["m1", "m1"]]},
        {"target_groups": []},
        {"answerable_from_store": False},
        {"answerable_from_store": 1},
        {"needs_memory_date": "true"},
        {"needs_memory_date": 1},
        {"needs_memory_date": None},
        {"note": None},
        {"memory_ids": ["m1"]},
    ],
)
def test_v3_rejects_invalid_groups_unknown_ids_and_date_flags(update: dict[str, Any]) -> None:
    with pytest.raises(ValueError) as error:
        dev2_labelling.parse_label_v3(_v3_item(), json.dumps(_v3_label(**update)))
    assert SENTINEL not in str(error.value)


def test_v3_date_flag_is_required_and_negative_label_has_no_groups() -> None:
    value = _v3_label()
    value.pop("needs_memory_date")
    with pytest.raises(ValueError):
        dev2_labelling.parse_label_v3(_v3_item(), json.dumps(value))
    label = dev2_labelling.parse_label_v3(
        _v3_item(),
        json.dumps(
            _v3_label(
                target_groups=[],
                answerable_from_store=False,
                needs_memory_date=False,
            )
        ),
    )
    assert not label.target_groups and not label.answerable_from_store
    # A memory can support multiple distinct requirements; groups need not be disjoint.
    overlap = dev2_labelling.parse_label_v3(
        _v3_item(), json.dumps(_v3_label(target_groups=[["m1"], ["m1", "m2"]]))
    )
    assert overlap.target_groups == (("m1",), ("m1", "m2"))


def test_v3_reads_actual_valid_from_and_binds_it_to_receipts() -> None:
    import uuid
    from datetime import datetime, UTC

    stored = datetime(2023, 4, 1, tzinfo=UTC)
    conversation = uuid.UUID(Q1_CONVERSATION)
    sqls: list[str] = []

    def reader(sql: str, params: Any) -> list[tuple[Any, ...]]:
        sqls.append(sql)
        return [("m1", conversation, "encrypted", stored)]

    loaded = asyncio.run(
        dev2_labelling.load_answer_session_memories_v3(
            reader,
            lambda _: "plain",
            "u",
            [str(conversation)],
        )
    )
    assert sqls == [dev2_labelling.MEMORY_SQL_V3]
    assert loaded == (("m1", Q1_CONVERSATION, "plain", stored.isoformat()),)
    mapping = {"s1": Q1_CONVERSATION}
    item = dev2_labelling.build_label_input_v3(_row(), mapping, loaded)
    assert item.memories[0][2] == "2023-05-30 18:00"  # dataset session date
    assert item.memories[0][4] == stored.isoformat()  # not substituted with session date
    first = dev2_labelling._result_input_sha256(_row(), mapping, loaded)
    changed = (("m1", Q1_CONVERSATION, "plain", STORED_DATE),)
    assert first != dev2_labelling._result_input_sha256(_row(), mapping, changed)
    for invalid in (None, "not-a-date", "2023-05-30"):
        with pytest.raises(ValueError, match="valid_from"):
            dev2_labelling._stored_date(invalid)


def test_v3_packet_shows_groups_alternatives_stored_date_and_date_flag() -> None:
    entry = {"question_id": "q1", "schema_valid": True, **_v3_label()}
    packet = dev2_labelling.build_spot_check_packet_v2(
        [("q1", ("seeded_sample",))],
        [entry],
        _packet_rows()[:1],
        _packet_checkpoint(),
        {"q1": _v3_item().memories},
        label_schema=dev2_labelling.LABEL_RECORD_SCHEMA,
        prompt_sha256=PINNED_V3_HASHES["prompt_v3"],
    )
    assert "(v3)" in packet and "Prompt hash (v3)" in packet
    assert "Group 1: m1, m2" in packet
    assert "every group required" in packet
    assert "needs_memory_date: true" in packet
    assert "valid_from: " + STORED_DATE in packet
    assert "temporal cue dropped at extraction" in packet
    assert "[LABELLED] m1:" in packet and "[LABELLED] m2:" in packet
    assert "needs_memory_date correct?" in packet


def test_seeded_spot_check_carries_only_per_question_supersets() -> None:
    old = [
        {"question_id": qid, "schema_valid": True, "memory_ids": [mid]}
        for qid, mid in [("q1", "m1"), ("q2", "m2"), ("unseeded", "m3")]
    ]
    new = [
        {"question_id": qid, "schema_valid": True, "target_groups": groups}
        for qid, groups in [("q1", [["m1", "other"]]), ("q2", [["m2"]]), ("unseeded", [])]
    ]
    compare = dev2_labelling.seeded_spot_check_changes
    assert compare(old, new, ["q2", "q1"]) == ()
    new[0]["target_groups"] = [["m2"]]
    # Global union still has m1 in q2, but q1 lost its own checked target.
    new[1]["target_groups"] = [["m1", "m2"]]
    assert compare(old, new, ["q2", "q1"]) == ("q1",)
    with pytest.raises(ValueError, match="missing"):
        compare(old, new[:1], ["q1", "q2"])
    with pytest.raises(ValueError, match="nonempty"):
        compare(old, new, [])


def test_seeded_comparison_distinguishes_no_memories_from_parse_failure() -> None:
    empty = {
        "question_id": "q1",
        "schema_valid": False,
        "reason": "no_memories",
        "answerable_from_store": False,
    }
    compare = dev2_labelling.seeded_spot_check_changes
    assert compare([empty], [empty], ["q1"]) == ()
    before = {"question_id": "q1", "schema_valid": True, "memory_ids": ["m1"]}
    assert compare([before], [empty], ["q1"]) == ("q1",)
    with pytest.raises(ValueError, match="valid labels"):
        compare([empty], [{**empty, "reason": "parse_failure"}], ["q1"])


def test_v3_end_to_end_driver_uses_only_v3_and_preserves_groups(tmp_path: Any) -> None:
    calls: list[Any] = []
    document, _ = _run(
        tmp_path,
        rows=[_row()],
        checkpoint=_q1_checkpoint(),
        call=lambda payload: (
            calls.append(payload) or _receipt(json.dumps(_v3_label(target_groups=[["m1"]])))
        ),
    )
    assert document["schema"] == "dev2-labelling/3"
    assert document["stop_reason"] is None
    assert calls[0]["messages"][0]["content"] == dev2_labelling.PROMPT_V3
    assert calls[0]["response_format"]["json_schema"]["schema"] == dev2_labelling.LABEL_SCHEMA
    entry = document["questions"][0]
    assert "memory_ids" not in entry
    assert entry["target_groups"] == [["m1"]] and entry["needs_memory_date"] is True


@pytest.mark.parametrize("target", ["m1", "unknown"])
def test_v3_packet_dispatch_uses_stored_dates_and_checks_input_ids(
    monkeypatch: Any, target: str
) -> None:
    sqls: list[str] = []
    closed: list[bool] = []

    async def fetch(sql: str, *args: Any) -> list[tuple[str, str, str, str]]:
        sqls.append(sql)
        return [("m1", Q1_CONVERSATION, "plain", STORED_DATE)]

    async def close() -> None:
        closed.append(True)

    async def connect(_url: str) -> Any:
        return SimpleNamespace(
            fetch=fetch, close=close, transaction=lambda **kwargs: _NullTransaction()
        )

    monkeypatch.setattr(
        "orchestrator.config.get_settings",
        lambda: SimpleNamespace(database_url="postgresql://user@host/synthetic"),
    )
    entry = {"question_id": "q1", "schema_valid": True, **_v3_label(target_groups=[[target]])}
    labels = {
        "schema": dev2_labelling.LABEL_RECORD_SCHEMA,
        "artifacts": artifact_hashes(),
        "questions": [entry],
    }
    pending = dev2_labelling._run_packet(
        labels=labels,
        rows=_packet_rows()[:1],
        checkpoint=_packet_checkpoint(),
        questions=[entry],
        selection=[("q1", ("seeded_sample",))],
        connector=connect,
        decrypt=str,
    )
    if target == "unknown":
        with pytest.raises(ValueError, match="outside the input"):
            asyncio.run(pending)
    else:
        packet = asyncio.run(pending)
        assert "Group 1: m1" in packet and "needs_memory_date: true" in packet
        assert "valid_from: " + STORED_DATE in packet
        assert "Prompt hash (v3)" in packet
    assert sqls == [dev2_labelling.MEMORY_SQL_V3]
    assert closed == [True]
