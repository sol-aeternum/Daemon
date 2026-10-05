"""Offline-only checks; synthetic ledgers never touch the operator's receipts."""

from decimal import Decimal
import json
from typing import Any, cast

import pytest

from scripts import embedding_qualification_support as qualification


def frozen():
    return {
        "parent_reservations": [{"input": 75_711, "usd": "0.01804121"}]
        + [{"input": 0, "usd": "0"} for _ in range(8)],
        "fixture": qualification.digest(qualification.fixtures()[0]),
    }


def test_frozen_corpus_excludes_metadata_before_send_or_ranking():
    fixture, documents = qualification.fixtures()
    assert len(fixture["scenarios"]) == 16
    assert len(documents) == 64
    assert len({row["id"] for row in documents}) == 64
    excluded = {
        "tea-old",
        "commute-old",
        "project-old",
        "address-old",
        "diet-local",
        "notes-foreign",
        "music-oldspace",
    }
    assert not excluded & {row["id"] for row in documents}
    assert qualification.CRITERIA == {"queries": 16, "minimum_top1": 12, "minimum_top3": 15}


@pytest.mark.parametrize("value", [True, None, "false", 0, 1])
def test_unknown_or_non_boolean_locality_is_not_eligible(value):
    fixture, _ = qualification.fixtures()
    assert not qualification.eligible({"local_only": value}, fixture)


def test_all_parent_reservations_count_towards_token_cap():
    ledger = {"frozen": frozen(), "attempts": []}
    qualification.reserve(ledger, "docs", 24_289)
    with pytest.raises(ValueError, match="cap"):
        qualification.reserve(ledger, "queries", 1)
    assert ledger["attempts"][0]["outcome"] == "uncertain"


def test_parent_request_and_cost_reservations_count():
    ledger = {"frozen": frozen(), "attempts": []}
    for index in range(15):
        qualification.reserve(ledger, str(index), 1)
    with pytest.raises(ValueError, match="cap"):
        qualification.reserve(ledger, "extra", 1)
    ledger = {"frozen": {"parent_reservations": [{"input": 1, "usd": "0.25"}]}, "attempts": []}
    with pytest.raises(ValueError, match="cap"):
        qualification.reserve(ledger, "extra", 1)


def test_no_duplicate_batch_and_no_bad_input():
    ledger = {"frozen": frozen(), "attempts": []}
    qualification.reserve(ledger, "docs", 1)
    with pytest.raises(ValueError, match="replay"):
        qualification.reserve(ledger, "docs", 1)
    for tokens in (0, -1, True, 1.0):
        with pytest.raises(ValueError, match="Invalid"):
            qualification.reservation_cost(cast(Any, tokens))


def test_input_price_is_per_million_with_fee_allowance():
    assert qualification.reservation_cost(1_000_000) == Decimal("0.022")


@pytest.mark.parametrize("outcome", ["uncertain", "invalid", "http_error", None])
def test_incomplete_or_failed_run_cannot_resume(tmp_path, outcome):
    identity = frozen()
    ledger = {"frozen": identity, "attempts": []}
    attempt = qualification.reserve(ledger, "docs", 100)
    attempt["outcome"] = outcome
    path = tmp_path / "child.json"
    qualification.write_ledger(path, ledger)
    with pytest.raises(ValueError, match="cannot resume"):
        qualification.open_followup(path, identity)


def test_valid_resume_requires_same_frozen_identity_and_reservation(tmp_path):
    identity = frozen()
    ledger = {"frozen": identity, "attempts": []}
    attempt = qualification.reserve(ledger, "docs", 100)
    attempt.update(outcome="valid", receipt={"model": "fictional"})
    path = tmp_path / "child.json"
    qualification.write_ledger(path, ledger)
    assert qualification.open_followup(path, identity) == ledger
    with pytest.raises(ValueError, match="identity"):
        qualification.open_followup(path, {**identity, "fixture": "changed"})
    attempt["usd"] = "0"
    qualification.write_ledger(path, ledger)
    with pytest.raises(ValueError, match="reservation"):
        qualification.open_followup(path, identity)


def test_original_ledger_hash_prevents_new_allowance(tmp_path):
    parent = tmp_path / "parent.json"
    parent.write_text(json.dumps({"attempts": []}))
    with pytest.raises(ValueError, match="changed"):
        qualification.parent_reservations(parent)


def test_durable_reservation_exists_before_send_is_permitted(tmp_path):
    ledger = {"frozen": frozen(), "attempts": []}
    path = tmp_path / "child.json"
    attempt = qualification.reserve_durable(path, ledger, "docs", 100)
    persisted = json.loads(path.read_text())
    assert persisted["attempts"] == [attempt]
    assert attempt["outcome"] == "uncertain"


def test_failed_reservation_write_never_returns_permission(tmp_path, monkeypatch):
    ledger = {"frozen": frozen(), "attempts": []}

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(qualification, "write_ledger", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        qualification.reserve_durable(tmp_path / "child.json", ledger, "docs", 100)
    assert ledger["attempts"][0]["outcome"] == "uncertain"


def test_dual_locks_and_fixed_child_path(tmp_path, monkeypatch):
    parent = tmp_path / "original.json"
    parent.write_text("immutable original")
    monkeypatch.setattr(qualification, "parent_reservations", lambda path: [])
    with qualification.cumulative_ledger(parent) as (child, reservations):
        assert child == tmp_path / "original.json.azure-small.json"
        assert reservations == []
        with pytest.raises(BlockingIOError), qualification.cumulative_ledger(parent):
            pytest.fail("Original-runner lock was not exclusive")
    with qualification.cumulative_ledger(parent):
        pass
    assert parent.read_text() == "immutable original"


def test_orthogonal_relevant_vectors_pass_and_wrong_vectors_fail():
    fixture, documents = qualification.fixtures()
    vectors = [
        [float(index == column) for column in range(len(documents))]
        for index in range(len(documents))
    ]
    by_id = {row["id"]: vector for row, vector in zip(documents, vectors, strict=True)}
    queries = [by_id[scenario["relevant"]] for scenario in fixture["scenarios"]]
    texts = [scenario["query"] for scenario in fixture["scenarios"]]
    result = qualification.score(fixture, documents, vectors, queries, query_texts=texts)
    assert result["top1"] == result["top3"] == 16 and result["retrieval_pass"]
    wrong = qualification.score(fixture, documents, vectors, [vectors[0]] * 16, query_texts=texts)
    assert not wrong["retrieval_pass"]
    with pytest.raises(ValueError, match="corpus"):
        qualification.score(fixture, documents[::-1], vectors, queries, query_texts=texts)
    with pytest.raises(ValueError, match="manifest"):
        qualification.score(fixture, documents, vectors, queries, query_texts=texts[::-1])


def test_cosine_is_scale_safe_and_rejects_bad_vectors():
    assert qualification.cosine([1e200, 0.0], [1e200, 0.0]) == 1
    for vector in ([float("nan")], [True], [0.0], []):
        with pytest.raises(ValueError):
            qualification.cosine(cast(Any, vector), cast(Any, vector))
