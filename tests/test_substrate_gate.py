from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.eval.substrate import (
    Substrate,
    SubstrateMismatchError,
    assert_substrate_match,
    normalize_substrate,
    read_score_substrate,
)


def _write_score(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload))


def test_normalize_substrate_accepts_known_values() -> None:
    assert normalize_substrate("chunk") == "chunk"
    assert normalize_substrate("fact") == "fact"


def test_normalize_substrate_rejects_unknown() -> None:
    with pytest.raises(SubstrateMismatchError, match="Unknown substrate"):
        normalize_substrate("parity")


def test_normalize_substrate_rejects_none() -> None:
    with pytest.raises(SubstrateMismatchError, match="Unknown substrate"):
        normalize_substrate(None)


def test_read_score_substrate_returns_tagged_value(tmp_path: Path) -> None:
    score = tmp_path / "longmemeval_chunk_score.json"
    _write_score(
        score,
        {
            "substrate": "chunk",
            "benchmark_name": "longmemeval_chunk",
            "generated_at": "2026-06-04T00:00:00+00:00",
            "result_count": 10,
            "accuracy": {"TR": 0.5},
        },
    )
    assert read_score_substrate(score) == "chunk"


def test_read_score_substrate_rejects_missing_field(tmp_path: Path) -> None:
    score = tmp_path / "untagged.json"
    _write_score(score, {"generated_at": "2026-06-04", "result_count": 10, "accuracy": {}})
    with pytest.raises(SubstrateMismatchError, match="missing required 'substrate' field"):
        read_score_substrate(score)


def test_read_score_substrate_rejects_legacy_filename(tmp_path: Path) -> None:
    score = tmp_path / "longmemeval_score.json"
    _write_score(score, {"generated_at": "2026-04-22", "result_count": 50, "accuracy": {}})
    with pytest.raises(SubstrateMismatchError, match="missing required 'substrate' field"):
        read_score_substrate(score)


def test_read_score_substrate_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Score JSON not found"):
        read_score_substrate(tmp_path / "nope.json")


def test_assert_substrate_match_passes_same_substrate(tmp_path: Path) -> None:
    score_a = tmp_path / "a.json"
    score_b = tmp_path / "b.json"
    _write_score(score_a, {"substrate": "chunk", "result_count": 10, "accuracy": {}})
    _write_score(score_b, {"substrate": "chunk", "result_count": 12, "accuracy": {}})
    payload_a, payload_b = assert_substrate_match(score_a, score_b)
    assert payload_a["result_count"] == 10
    assert payload_b["result_count"] == 12


def test_assert_substrate_match_rejects_chunk_vs_fact(tmp_path: Path) -> None:
    score_chunk = tmp_path / "chunk.json"
    score_fact = tmp_path / "fact.json"
    _write_score(score_chunk, {"substrate": "chunk", "result_count": 10, "accuracy": {}})
    _write_score(score_fact, {"substrate": "fact", "result_count": 14, "accuracy": {}})
    with pytest.raises(
        SubstrateMismatchError, match="Cannot compare score JSONs across substrates"
    ):
        assert_substrate_match(score_chunk, score_fact)


def test_assert_substrate_match_rejects_legacy_untagged(tmp_path: Path) -> None:
    score_legacy = tmp_path / "legacy.json"
    score_chunk = tmp_path / "chunk.json"
    _write_score(score_legacy, {"result_count": 14, "accuracy": {"TR": 0.3}})
    _write_score(score_chunk, {"substrate": "chunk", "result_count": 10, "accuracy": {}})
    with pytest.raises(SubstrateMismatchError, match="missing required 'substrate' field"):
        assert_substrate_match(score_legacy, score_chunk)


def test_substrate_literal_type_excludes_unknown() -> None:
    assert set(Substrate.__args__) == {"chunk", "fact"}
