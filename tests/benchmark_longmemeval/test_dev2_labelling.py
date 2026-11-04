"""Boundary tests for the frozen dev2 labelling protocol."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

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
