from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_longmemeval import dev2, holdout
from tests.benchmark_longmemeval.dev2 import (
    ALGORITHM,
    ELIGIBLE_TYPES,
    MANIFEST_FIELDS,
    QuestionAggregate,
    SessionCounts,
    assert_manifest_allowlisted,
    estimate,
    estimate_volumes,
    freeze,
    project_metadata,
    select_dev2,
    selected_row_aggregates,
)

SENTINEL = "SENTINEL-CONTENT-MUST-NOT-BE-EMITTED"


def row(
    qid: str,
    kind: str = "multi-session",
    sessions: list[str] | None = None,
    *,
    turns: int = 2,
) -> dict[str, Any]:
    """Synthetic dataset row; every text field carries the sentinel."""
    answer_sessions = [qid] if sessions is None else sessions
    session_ids = list(dict.fromkeys(answer_sessions))
    return {
        "question_id": qid,
        "question_type": kind,
        "answer_session_ids": answer_sessions,
        "question": SENTINEL,
        "answer": SENTINEL,
        "haystack_session_ids": session_ids,
        "haystack_sessions": [
            [{"role": "user", "content": SENTINEL} for _ in range(turns)] for _ in session_ids
        ],
    }


def select(
    rows: list[dict[str, Any]],
    dev: list[dict[str, Any]] | None = None,
    holdout_ids: set[str] | None = None,
    *,
    size: int = 2,
    seed: int = 42,
) -> dict[str, Any]:
    return select_dev2(
        project_metadata(rows),
        project_metadata([] if dev is None else dev),
        set() if holdout_ids is None else holdout_ids,
        size=size,
        seed=seed,
    )


def test_determinism_and_order_independence() -> None:
    rows = [row(f"q{i}") for i in range(30)]
    first = select(rows, size=12)
    assert first == select(list(reversed(rows)), size=12)
    assert first == select(rows, size=12)
    assert first["question_ids"] == sorted(first["question_ids"])
    assert len(first["question_ids"]) == len(set(first["question_ids"])) == 12
    assert first["question_ids"] != select(rows, size=12, seed=43)["question_ids"]


def test_algorithm_name_differs_from_holdout() -> None:
    manifest = select([row("q")], size=1)
    assert manifest["algorithm"] == ALGORITHM == dev2.ALGORITHM
    assert ALGORITHM != holdout.ALGORITHM


def test_only_eligible_question_types_are_selected() -> None:
    rows = [row(f"ineligible{i}", "single-session-user") for i in range(20)]
    rows += [row(f"pref{i}", "single-session-preference") for i in range(5)]
    rows += [row("multi"), row("temporal", "temporal-reasoning"), row("ku", "knowledge-update")]
    manifest = select(rows, size=3)
    assert set(manifest["question_ids"]) == {"multi", "temporal", "ku"}
    assert manifest["excluded_ineligible_types"] == 25
    assert set(manifest["selected_strata"]) <= {
        f"{kind}:{abstention}" for kind in ELIGIBLE_TYPES for abstention in ("answerable", "abs")
    }


def test_dev_ids_are_independently_excluded() -> None:
    dev_rows = [row("dev1", sessions=["dev_session"])]
    rows = dev_rows + [row(f"eligible{i}") for i in range(3)]
    manifest = select(rows, dev=dev_rows, size=2)
    assert manifest["excluded_dev_ids"] == 1
    assert "dev1" not in manifest["question_ids"]


def test_holdout_ids_are_independently_excluded() -> None:
    holdout_row = row("ho1", sessions=["ho_session"])
    rows = [holdout_row] + [row(f"eligible{i}") for i in range(3)]
    manifest = select(rows, holdout_ids={"ho1"}, size=2)
    assert manifest["excluded_holdout_ids"] == 1
    assert manifest["holdout_count"] == 1
    assert "ho1" not in manifest["question_ids"]


def test_shared_answer_sessions_are_independently_excluded() -> None:
    dev_rows = [row("dev1", sessions=["dev_session"])]
    holdout_row = row("ho1", sessions=["ho_session"])
    rows = dev_rows + [
        holdout_row,
        row("dev_shared", sessions=["dev_session"]),
        row("ho_shared", sessions=["ho_session"]),
        row("eligible1"),
        row("eligible2"),
        row("eligible3"),
    ]
    manifest = select(rows, dev=dev_rows, holdout_ids={"ho1"}, size=2)
    assert manifest["excluded_shared_answer_sessions_only"] == 1
    assert manifest["excluded_holdout_shared_answer_sessions_only"] == 1
    assert "dev_shared" not in manifest["question_ids"]
    assert "ho_shared" not in manifest["question_ids"]
    assert manifest["eligible_count"] == 3
    assert (
        manifest["excluded_dev_ids"]
        + manifest["excluded_holdout_ids"]
        + manifest["excluded_shared_answer_sessions_only"]
        + manifest["excluded_holdout_shared_answer_sessions_only"]
        + manifest["excluded_ineligible_types"]
        + manifest["eligible_count"]
        == manifest["dataset_count"]
    )


def test_proportional_allocation_includes_abstention() -> None:
    rows = [row(f"ms{i}") for i in range(20)]
    rows += [row(f"ms_abs{i}_abs") for i in range(10)]
    rows += [row(f"tr{i}", "temporal-reasoning") for i in range(10)]
    rows += [row(f"ku{i}", "knowledge-update") for i in range(5)]
    manifest = select(rows, size=9)
    assert manifest["eligible_strata"] == {
        "knowledge-update:answerable": 5,
        "multi-session:abs": 10,
        "multi-session:answerable": 20,
        "temporal-reasoning:answerable": 10,
    }
    assert manifest["selected_strata"] == {
        "knowledge-update:answerable": 1,
        "multi-session:abs": 2,
        "multi-session:answerable": 4,
        "temporal-reasoning:answerable": 2,
    }
    assert sum(manifest["selected_strata"].values()) == 9


def test_largest_remainder_ties_use_ascending_stratum_name() -> None:
    rows = [
        row("ms"),
        row("tr", "temporal-reasoning"),
        row("ku", "knowledge-update"),
    ]
    manifest = select(rows, size=2)
    assert manifest["eligible_strata"] == {
        "knowledge-update:answerable": 1,
        "multi-session:answerable": 1,
        "temporal-reasoning:answerable": 1,
    }
    assert manifest["selected_strata"] == {
        "knowledge-update:answerable": 1,
        "multi-session:answerable": 1,
    }


def test_insufficient_candidates_raise_instead_of_shrinking() -> None:
    rows = [row("q1"), row("q2"), row("ineligible", "single-session-assistant")]
    with pytest.raises(ValueError, match="Insufficient eligible candidates"):
        select(rows, size=3)


def test_invalid_inputs_raise() -> None:
    rows = [row("q1")]
    metadata = project_metadata(rows)
    with pytest.raises(ValueError):
        select_dev2(metadata, [], set(), size=0)
    with pytest.raises(ValueError, match="Holdout IDs do not match"):
        select_dev2(metadata, [], {"missing"}, size=1)
    with pytest.raises(ValueError, match="Dev and holdout IDs overlap"):
        select_dev2(metadata, metadata, {"q1"}, size=1)
    with pytest.raises(ValueError, match="Holdout IDs must be unique"):
        select_dev2(metadata, [], ["q1", "q1"], size=1)


def test_estimate_volumes_dedupes_sessions() -> None:
    aggregates = [
        QuestionAggregate("q1", (SessionCounts("s1", 10, 1000), SessionCounts("s2", 10, 1000))),
        QuestionAggregate("q2", (SessionCounts("s1", 10, 1000), SessionCounts("s3", 5, 500))),
    ]
    volumes = estimate_volumes(aggregates)
    assert volumes["estimated_questions"] == 2
    assert volumes["estimated_ingest_sessions"] == 3
    assert volumes["estimated_ingest_turns"] == 25
    assert volumes["estimated_ingest_chars"] == 2500
    assert volumes["estimated_tokens"] == 625
    assert volumes["labelling_spot_check_fraction"] == 0.15
    assert volumes["estimated_labelling_spot_check_questions"] == 1
    assert set(volumes) <= set(dev2.ESTIMATE_FIELDS)


def test_estimate_arithmetic_including_spot_check_and_dedup() -> None:
    aggregates = [
        QuestionAggregate("q1", (SessionCounts("s1", 10, 1000), SessionCounts("s2", 10, 1000))),
        QuestionAggregate("q2", (SessionCounts("s1", 10, 1000), SessionCounts("s3", 5, 500))),
    ]
    costs = {
        "extraction_usd_per_1k_tokens": 0.5,
        "embedding_usd_per_1k_tokens": 0.1,
        "extraction_seconds_per_1k_tokens": 10,
        "embedding_seconds_per_1k_tokens": 2,
        "turns_per_db_restore": 10,
        "db_restore_seconds": 60,
        "labelling_seconds_per_question": 90,
    }
    result = estimate(aggregates, costs)
    assert result["estimated_ingest_sessions"] == 3
    assert result["estimated_ingest_turns"] == 25  # shared s1 counted once
    assert result["estimated_ingest_chars"] == 2500
    assert result["estimated_tokens"] == pytest.approx(625.0)
    assert result["estimated_extraction_usd"] == pytest.approx(0.3125)
    assert result["estimated_embedding_usd"] == pytest.approx(0.0625)
    assert result["estimated_extraction_seconds"] == pytest.approx(6.25)
    assert result["estimated_embedding_seconds"] == pytest.approx(1.25)
    assert result["estimated_db_restores"] == 3  # ceil(25 / 10)
    assert result["estimated_db_restore_seconds"] == pytest.approx(180.0)
    assert result["estimated_wall_time_seconds"] == pytest.approx(187.5)
    assert result["estimated_labelling_spot_check_questions"] == 1  # ceil(15% of 2)
    assert result["estimated_labelling_seconds"] == pytest.approx(90.0)
    assert result["estimated_labelling_minutes"] == pytest.approx(1.5)
    assert result["chars_per_token_approximation"] == 4.0
    assert set(result) <= set(dev2.ESTIMATE_FIELDS)
    assert all(isinstance(value, (int, float)) for value in result.values())


def test_estimate_requires_explicit_unit_costs() -> None:
    aggregates = [QuestionAggregate("q1", (SessionCounts("s1", 1, 4),))]
    costs: dict[str, Any] = {
        "extraction_usd_per_1k_tokens": 0.5,
        "embedding_usd_per_1k_tokens": 0.1,
        "extraction_seconds_per_1k_tokens": 10,
        "embedding_seconds_per_1k_tokens": 2,
        "turns_per_db_restore": 10,
        "db_restore_seconds": 60,
        "labelling_seconds_per_question": 90,
    }
    assert estimate(aggregates, costs)["estimated_ingest_sessions"] == 1
    missing = dict(costs)
    missing.pop("db_restore_seconds")
    with pytest.raises(ValueError, match="exactly the declared fields"):
        estimate(aggregates, missing)
    with pytest.raises(ValueError, match="exactly the declared fields"):
        estimate(aggregates, {**costs, "invented_price": 1.0})
    with pytest.raises(ValueError, match="must be a number"):
        estimate(aggregates, {**costs, "db_restore_seconds": True})
    with pytest.raises(ValueError, match="must be positive"):
        estimate(aggregates, {**costs, "turns_per_db_restore": 0})


def test_estimate_counts_conflicts_and_keeps_conservative_counts() -> None:
    aggregates = [
        QuestionAggregate("q1", (SessionCounts("s1", 2, 40),)),
        QuestionAggregate("q2", (SessionCounts("s1", 3, 60),)),
    ]
    volumes = estimate_volumes(aggregates)
    assert volumes["session_count_conflicts"] == 1
    assert volumes["estimated_ingest_sessions"] == 1
    assert volumes["estimated_ingest_turns"] == 3
    assert volumes["estimated_ingest_chars"] == 60


def test_estimate_never_reads_non_selected_rows() -> None:
    class Exploding(dict):
        def __getitem__(self, key: str) -> Any:
            if key != "question_id":
                raise AssertionError("Non-selected row content must not be read")
            return super().__getitem__(key)

        def get(self, key: str, default: Any = None) -> Any:
            if key != "question_id":
                raise AssertionError("Non-selected row content must not be read")
            return super().get(key, default)

    kept = row("kept")
    held_out = Exploding(row("held_out"))
    aggregates = selected_row_aggregates([kept, held_out], {"kept"})
    assert [aggregate.question_id for aggregate in aggregates] == ["kept"]
    assert estimate_volumes(aggregates)["estimated_ingest_turns"] == 2


def test_manifest_allowlist_guard() -> None:
    manifest = select([row("q")], size=1)
    assert set(manifest) <= MANIFEST_FIELDS
    assert_manifest_allowlisted(manifest)
    with pytest.raises(ValueError, match="outside allowlist"):
        assert_manifest_allowlisted({**manifest, "question_text": "leak"})
    with pytest.raises(ValueError, match="Estimate field outside allowlist"):
        assert_manifest_allowlisted({**manifest, "estimate": {"question_text": "leak"}})
    with pytest.raises(ValueError, match="Estimate values must be numbers"):
        assert_manifest_allowlisted({**manifest, "estimate": {"estimated_tokens": "leak"}})


def test_freeze_is_content_free_allowlisted_and_never_overwritten(tmp_path: Path) -> None:
    dataset, dev, holdout_manifest, output = (
        tmp_path / name for name in ("full.json", "dev.json", "holdout.json", "dev2.json")
    )
    rows = [row("q1"), row("q2"), row("q3", "temporal-reasoning")]
    dataset.write_text(json.dumps(rows))
    dev.write_text("[]")
    holdout_manifest.write_text(json.dumps({"question_ids": []}))
    costs = {
        "extraction_usd_per_1k_tokens": 0.5,
        "embedding_usd_per_1k_tokens": 0.1,
        "extraction_seconds_per_1k_tokens": 10,
        "embedding_seconds_per_1k_tokens": 2,
        "turns_per_db_restore": 10,
        "db_restore_seconds": 60,
        "labelling_seconds_per_question": 90,
    }
    costs_path = tmp_path / "unit_costs.json"
    costs_path.write_text(json.dumps(costs))
    freeze(
        dataset,
        dev,
        holdout_manifest,
        output,
        size=2,
        seed=42,
        unit_costs_path=costs_path,
    )
    raw = output.read_bytes()
    assert SENTINEL.encode() not in raw
    manifest = json.loads(raw)
    assert_manifest_allowlisted(manifest)
    assert set(manifest) <= MANIFEST_FIELDS
    assert manifest["algorithm"] == ALGORITHM
    assert manifest["size"] == 2
    assert manifest["estimate"]["estimated_ingest_sessions"] == 2
    assert manifest["unit_costs_sha256"] == hashlib.sha256(costs_path.read_bytes()).hexdigest()
    assert manifest["dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert manifest["dev_sha256"] == hashlib.sha256(dev.read_bytes()).hexdigest()
    assert manifest["holdout_sha256"] == hashlib.sha256(holdout_manifest.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        freeze(dataset, dev, holdout_manifest, output, size=2, seed=43)
    assert output.read_bytes() == raw


def test_freeze_without_unit_costs_emits_volume_estimate_only(tmp_path: Path) -> None:
    dataset, dev, holdout_manifest, output = (
        tmp_path / name for name in ("full.json", "dev.json", "holdout.json", "dev2.json")
    )
    dataset.write_text(json.dumps([row("q1"), row("q2")]))
    dev.write_text("[]")
    holdout_manifest.write_text(json.dumps({"question_ids": []}))
    freeze(dataset, dev, holdout_manifest, output, size=2, seed=42)
    manifest = json.loads(output.read_bytes())
    assert_manifest_allowlisted(manifest)
    assert "estimated_extraction_usd" not in manifest["estimate"]
    # 2 rows x 1 session x 2 turns x len(SENTINEL) chars, then chars / 4.
    assert manifest["estimate"]["estimated_ingest_chars"] == 4 * len(SENTINEL)
    assert manifest["estimate"]["estimated_tokens"] == 4 * len(SENTINEL) / 4
    assert "unit_costs_sha256" not in manifest


def test_frozen_dev2_manifest_matches_its_pins_and_stays_disjoint() -> None:
    fixtures = Path(__file__).parent / "fixtures"
    manifest = json.loads((fixtures / "dev2_ids.json").read_text())
    holdout = json.loads((fixtures / "holdout_ids.json").read_text())
    dev = json.loads((fixtures / "dev_subset.json").read_text())
    ids = manifest["question_ids"]

    dev2.assert_manifest_allowlisted(manifest)
    assert len(ids) == len(set(ids)) == manifest["size"] == 100
    assert ids == sorted(ids)
    assert not set(ids) & set(holdout["question_ids"])
    assert not set(ids) & {row["question_id"] for row in dev}
    assert sum(manifest["selected_strata"].values()) == 100
    assert {key.split(":")[0] for key in manifest["selected_strata"]} == set(dev2.ELIGIBLE_TYPES)
    encoded = ("\n".join(ids) + "\n").encode()
    assert hashlib.sha256(encoded).hexdigest() == manifest["question_ids_sha256"]
    assert manifest["dataset_sha256"] == holdout["dataset_sha256"]
    assert manifest["dev_sha256"] == holdout["dev_sha256"]
    assert (
        hashlib.sha256((fixtures / "holdout_ids.json").read_bytes()).hexdigest()
        == manifest["holdout_sha256"]
    )
    assert (
        hashlib.sha256((fixtures / "dev2_unit_costs.json").read_bytes()).hexdigest()
        == manifest["unit_costs_sha256"]
    )
