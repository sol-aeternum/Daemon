"""Offline safety checks for the expressly approved fictional-only screen."""

from decimal import Decimal

import pytest

from scripts.benchmark_memory_embeddings import (
    DIMENSIONS,
    NATIVE_RECEIPT_MODELS,
    inputs_for,
    receipt_diagnostics,
    reserve,
    validate_vectors,
)


def test_caps_include_uncertain_attempts_and_restart():
    ledger = {"attempts": []}
    reserve(ledger, 90_112, "0.22")
    reserve(ledger, 9_888, "0.13")
    with pytest.raises(ValueError, match="token cap"):
        reserve(ledger, 1, "0.02")
    assert all(row["outcome"] == "uncertain" for row in ledger["attempts"])


def test_request_and_cost_caps():
    ledger = {"attempts": []}
    for _ in range(24):
        reserve(ledger, 1, "0.12")
    with pytest.raises(ValueError, match="request/token"):
        reserve(ledger, 1, "0.12")
    with pytest.raises(ValueError, match="cost cap"):
        reserve({"attempts": []}, 100_000, "3")
    assert Decimal(ledger["attempts"][0]["reserved_usd"]) > 0


def test_official_task_formatting():
    _, task = inputs_for("voyageai/voyage-4-lite", "queries")
    assert task == "query"
    docs, task = inputs_for("google/gemini-embedding-2", "documents")
    assert task is None and docs[0].startswith("title: none | text: ")
    queries, _ = inputs_for("google/gemini-embedding-2", "queries")
    assert queries[0].startswith("task: search result | query: ")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "0.1"])
def test_reject_invalid_vector(bad):
    data = {"model": "test", "data": [{"index": 0, "embedding": [bad] * DIMENSIONS}]}
    with pytest.raises(ValueError):
        validate_vectors(data, "test", 1)


def test_reject_model_dimension_and_index_drift():
    good = [0.1] * DIMENSIONS
    with pytest.raises(ValueError, match="model"):
        validate_vectors({"model": "other"}, "test", 1)
    with pytest.raises(ValueError, match="dimensions"):
        validate_vectors(
            {"model": "test", "data": [{"index": 0, "embedding": good[:-1]}]}, "test", 1
        )
    with pytest.raises(ValueError, match="indices"):
        validate_vectors({"model": "test", "data": [{"index": 1, "embedding": good}]}, "test", 1)


@pytest.mark.parametrize("returned", ["openai/text-embedding-3-small", "text-embedding-3-small"])
def test_explicit_native_small_receipt(returned):
    vector = [0.1] * DIMENSIONS
    assert validate_vectors(
        {"model": returned, "data": [{"index": 0, "embedding": vector}]},
        "openai/text-embedding-3-small",
        1,
    ) == [vector]


@pytest.mark.parametrize(
    "returned",
    [
        "text-embedding-3-large",
        "text-embedding-ada-002",
        "text-embedding-3-small/latest",
        "other/text-embedding-3-small",
        "text-embedding-3-small-extra",
        None,
        3,
        [],
    ],
)
def test_native_alias_never_accepts_other_models(returned):
    with pytest.raises(ValueError, match="model"):
        validate_vectors({"model": returned}, "openai/text-embedding-3-small", 1)


@pytest.mark.parametrize(
    "pinned",
    ["openai/text-embedding-3-large", "google/gemini-embedding-2", "voyageai/voyage-4-lite"],
)
def test_native_small_rejected_for_other_pins(pinned):
    with pytest.raises(ValueError, match="model"):
        validate_vectors({"model": "text-embedding-3-small"}, pinned, 1)


def test_receipt_diagnostics_are_safe_and_non_authoritative():
    assert receipt_diagnostics(
        {"provider": "Azure", "usage": {"prompt_tokens": 12, "total_tokens": 12}},
        ("azure", "Azure"),
    ) == {"receipt_provider": "Azure", "receipt_usage": {"prompt_tokens": 12, "total_tokens": 12}}
    diagnostic = receipt_diagnostics(
        {
            "provider": "private unexpected data",
            "usage": {"prompt_tokens": True, "total_tokens": -1},
        },
        ("azure", "Azure"),
    )
    assert diagnostic == {
        "receipt_provider": "unexpected",
        "receipt_usage": {"prompt_tokens": None, "total_tokens": None},
    }
    assert receipt_diagnostics({"usage": []}, ("azure", "Azure"))["receipt_provider"] is None


@pytest.mark.parametrize("pinned,native", list(NATIVE_RECEIPT_MODELS.items()))
def test_each_documented_native_model_is_pin_specific(pinned, native):
    vector = [0.1] * DIMENSIONS
    for returned in (pinned, native):
        assert validate_vectors(
            {"model": returned, "data": [{"index": 0, "embedding": vector}]}, pinned, 1
        ) == [vector]
    for other_pin, other_native in NATIVE_RECEIPT_MODELS.items():
        if other_pin != pinned:
            for returned in (other_pin, other_native):
                with pytest.raises(ValueError, match="model"):
                    validate_vectors({"model": returned}, pinned, 1)


def test_non_object_receipt_is_controlled_failure():
    with pytest.raises(ValueError, match="Non-object"):
        validate_vectors([], "openai/text-embedding-3-small", 1)
