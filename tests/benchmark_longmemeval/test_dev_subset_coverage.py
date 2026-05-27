from __future__ import annotations

import json
import re
from typing import cast

from tests.benchmark_longmemeval.dev_subset import (
    CELL_FLOOR,
    COVERAGE_REPORT_PATH,
    EXPECTED_QUESTION_IDS,
    FIXTURE_PATH,
    REQUIRED_CELLS,
    TARGET_SIZE,
    build_cell_counts,
    selected_question_ids,
)

BenchmarkCase = dict[str, object]


def _load_fixture() -> list[BenchmarkCase]:
    payload = cast(object, json.loads(FIXTURE_PATH.read_text()))
    assert isinstance(payload, list)
    return cast(list[BenchmarkCase], payload)


def _load_machine_summary() -> dict[str, object]:
    text = COVERAGE_REPORT_PATH.read_text()
    match = re.search(
        r"## Machine-checkable summary\n\n```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "dev_subset_coverage.md is missing its JSON summary"
    summary_payload = cast(object, json.loads(match.group(1)))
    assert isinstance(summary_payload, dict)
    return cast(dict[str, object], summary_payload)


def test_dev_subset_fixture_matches_locked_question_id_order() -> None:
    fixture = _load_fixture()
    assert selected_question_ids(fixture) == list(EXPECTED_QUESTION_IDS)


def test_dev_subset_coverage_report_enforces_size_and_cell_floors() -> None:
    fixture = _load_fixture()
    counts = build_cell_counts(fixture)
    summary = _load_machine_summary()

    assert len(fixture) == TARGET_SIZE
    assert len(set(selected_question_ids(fixture))) == TARGET_SIZE
    for cell in REQUIRED_CELLS:
        assert counts[cell] >= CELL_FLOOR

    assert summary["target_size"] == TARGET_SIZE
    assert summary["cell_floor"] == CELL_FLOOR
    assert summary["required_cells"] == list(REQUIRED_CELLS)
    assert summary["selected_question_ids"] == list(EXPECTED_QUESTION_IDS)
    assert summary["selected_question_ids"] == selected_question_ids(fixture)
    assert summary["required_cell_counts"] == {
        cell: counts[cell] for cell in REQUIRED_CELLS
    }
