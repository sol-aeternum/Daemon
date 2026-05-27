from __future__ import annotations

from collections import Counter
from typing import cast

from tests.benchmark_longmemeval.taxonomy import (
    CATEGORY_ORDER,
    EXPECTED_STAGE_COUNTS,
    STAGE_ORDER,
    STAGE_BY_QUESTION_ID,
    TAXONOMY_PATH,
    build_taxonomy_entries,
    load_failure_rows,
    occurrence_has_complete_answer_support,
    render_taxonomy_markdown,
)


def test_taxonomy_complete_and_unique_assignments() -> None:
    failure_rows = load_failure_rows()
    entries = build_taxonomy_entries(failure_rows=failure_rows)

    failure_ids = [str(row["question_id"]) for row in failure_rows]
    failure_id_set = set(failure_ids)
    assigned_ids = [str(entry["question_id"]) for entry in entries]

    assert assigned_ids == failure_ids
    assert len(set(assigned_ids)) == len(failure_ids)
    assert len(entries) == len(failure_rows)
    assert set(STAGE_BY_QUESTION_ID) == failure_id_set

    assert {str(entry["stage"]) for entry in entries} <= set(STAGE_ORDER)
    assert {str(entry["category"]) for entry in entries} <= set(CATEGORY_ORDER)

    stage_counts = Counter(str(entry["stage"]) for entry in entries)
    category_counts = Counter(str(entry["category"]) for entry in entries)
    assert stage_counts == Counter(EXPECTED_STAGE_COUNTS)
    assert sum(stage_counts.values()) == len(failure_rows)
    assert sum(category_counts.values()) == len(failure_rows)


def test_taxonomy_non_extraction_rows_have_complete_answer_support() -> None:
    failure_rows = load_failure_rows()
    entries_by_id = {
        str(entry["question_id"]): entry for entry in build_taxonomy_entries(failure_rows=failure_rows)
    }

    for row in failure_rows:
        question_id = str(row["question_id"])
        stage = str(entries_by_id[question_id]["stage"])
        occurrences = cast(list[dict[str, object]], row.get("failure_occurrences", []))
        if stage == "extraction-miss":
            assert not any(
                occurrence_has_complete_answer_support(row, occurrence)
                for occurrence in occurrences
            )
        else:
            assert any(
                occurrence_has_complete_answer_support(row, occurrence)
                for occurrence in occurrences
            ), question_id


def test_taxonomy_complete_markdown_file_is_in_sync() -> None:
    assert TAXONOMY_PATH.exists(), f"Missing taxonomy report: {TAXONOMY_PATH}"
    assert TAXONOMY_PATH.read_text() == render_taxonomy_markdown()
