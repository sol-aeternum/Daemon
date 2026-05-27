from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Final, cast

from tests.longmemeval.ingest import DATASET_PATH as CANONICAL_DATASET_PATH
from tests.longmemeval.ingest import build_corpus_plan

BenchmarkCase = dict[str, object]

FIXTURES_DIR = Path(__file__).with_name("fixtures")
FIXTURE_PATH = FIXTURES_DIR / "dev_subset.json"
COVERAGE_REPORT_PATH = FIXTURES_DIR / "dev_subset_coverage.md"

TARGET_SIZE = 50
CELL_FLOOR = 5
ABSTENTION_SUFFIX = "_abs"

REQUIRED_CELLS: Final[tuple[str, ...]] = (
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "abstention",
)
PRIMARY_FILL_ORDER: Final[tuple[str, ...]] = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
)
REQUIRED_PRIMARY_CELLS: Final[tuple[str, ...]] = (
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
)

EXPECTED_QUESTION_IDS: Final[tuple[str, ...]] = (
    'b86304ba',
    '8550ddae',
    '86f00804',
    '19b5f2b3',
    'caf9ead2',
    'fca762bc',
    '4388e9dd',
    'e8a79c70',
    'd596882b',
    '71a3fd6b',
    '28dc39ac',
    'ba358f49',
    '6cb6f249',
    '2318644b',
    '1192316e',
    '8c18457d',
    'gpt4_4edbafa2',
    '6613b389',
    'gpt4_af6db32f',
    'gpt4_b0863698',
    '852ce960',
    '6a1eabeb',
    '5831f84d',
    'f685340e',
    '184da446',
    'f685340e_abs',
    '982b5123_abs',
    'gpt4_93159ced_abs',
    '80ec1f4f_abs',
    'gpt4_372c3eed_abs',
    'ad7109d1',
    '2bf43736',
    '75f70248',
    'a3332713',
    '0bb5a684',
    '0977f2af',
    'c5e8278d',
    'c4f10528',
    '0a34ad58',
    '92a0aa75',
    'gpt4_65aabe59',
    '3ba21379',
    '545bd2b5',
    '1de5cff2',
    '09d032c9',
    'cc06de0d',
    'gpt4_93159ced',
    '59524333',
    '25e5aa4f',
    '7a8d0b71'
)


def _normalize_case(entry: object, *, source: Path) -> BenchmarkCase:
    if not isinstance(entry, dict):
        raise ValueError(f"Expected object entries in {source}, got {type(entry).__name__}")
    raw_entry = cast(dict[object, object], entry)
    normalized: BenchmarkCase = {}
    for key, value in raw_entry.items():
        normalized[str(key)] = value
    return normalized


def _load_case_list(path: Path) -> list[BenchmarkCase]:
    payload = cast(object, json.loads(path.read_text()))
    if not isinstance(payload, list):
        raise ValueError(f"JSON list required: {path}")
    return [_normalize_case(entry, source=path) for entry in cast(list[object], payload)]


def _list_length(value: object) -> int:
    if not isinstance(value, list):
        return 0
    sequence = cast(list[object], value)
    return len(sequence)


def question_id(entry: BenchmarkCase) -> str:
    value = entry.get("question_id", "")
    return value if isinstance(value, str) else str(value)


def primary_cell(entry: BenchmarkCase) -> str:
    value = entry.get("question_type", "single-session-user")
    return value if isinstance(value, str) else str(value)


def cell_memberships(entry: BenchmarkCase) -> tuple[str, ...]:
    memberships = [primary_cell(entry)]
    if question_id(entry).endswith(ABSTENTION_SUFFIX):
        memberships.append("abstention")
    return tuple(memberships)


def entry_sort_key(entry: BenchmarkCase) -> tuple[int, int, str, str]:
    question_date = entry.get("question_date", "")
    return (
        _list_length(entry.get("haystack_sessions", [])),
        _list_length(entry.get("answer_session_ids", [])),
        question_date if isinstance(question_date, str) else str(question_date),
        question_id(entry),
    )


def load_source_dataset(dataset_path: Path = CANONICAL_DATASET_PATH) -> list[BenchmarkCase]:
    return _load_case_list(dataset_path)


def load_fixture() -> list[BenchmarkCase]:
    return _load_case_list(FIXTURE_PATH)


def build_cell_counts(dataset: list[BenchmarkCase]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in dataset:
        for cell in cell_memberships(entry):
            counts[cell] += 1
    return counts


def selected_question_ids(dataset: list[BenchmarkCase]) -> list[str]:
    return [question_id(entry) for entry in dataset]


def _add_entry(
    entry: BenchmarkCase,
    *,
    selected: list[BenchmarkCase],
    selected_ids: set[str],
) -> bool:
    entry_question_id = question_id(entry)
    if entry_question_id in selected_ids:
        return False
    selected.append(entry)
    selected_ids.add(entry_question_id)
    return True


def select_dev_subset(dataset: list[BenchmarkCase]) -> list[BenchmarkCase]:
    by_primary: dict[str, list[BenchmarkCase]] = defaultdict(list)
    abstention_entries: list[BenchmarkCase] = []
    for entry in dataset:
        by_primary[primary_cell(entry)].append(entry)
        if question_id(entry).endswith(ABSTENTION_SUFFIX):
            abstention_entries.append(entry)

    for entries in by_primary.values():
        entries.sort(key=entry_sort_key)
    abstention_entries.sort(key=entry_sort_key)

    selected: list[BenchmarkCase] = []
    selected_ids: set[str] = set()

    for cell in REQUIRED_PRIMARY_CELLS:
        remaining = CELL_FLOOR
        for entry in by_primary.get(cell, []):
            if _add_entry(entry, selected=selected, selected_ids=selected_ids):
                remaining -= 1
            if remaining == 0:
                break
        if remaining != 0:
            raise ValueError(f"Unable to satisfy required cell {cell!r}")

    abstention_remaining = CELL_FLOOR
    for entry in abstention_entries:
        if _add_entry(entry, selected=selected, selected_ids=selected_ids):
            abstention_remaining -= 1
        if abstention_remaining == 0:
            break
    if abstention_remaining != 0:
        raise ValueError("Unable to satisfy required cell 'abstention'")

    fill_indexes = {cell: 0 for cell in PRIMARY_FILL_ORDER}
    while len(selected) < TARGET_SIZE:
        progressed = False
        for cell in PRIMARY_FILL_ORDER:
            entries = by_primary.get(cell, [])
            while fill_indexes[cell] < len(entries):
                entry = entries[fill_indexes[cell]]
                fill_indexes[cell] += 1
                if _add_entry(entry, selected=selected, selected_ids=selected_ids):
                    progressed = True
                    break
            if len(selected) == TARGET_SIZE:
                break
        if not progressed:
            raise ValueError("Unable to fill dev subset to target size")

    return selected


def selection_summary(dataset: list[BenchmarkCase]) -> dict[str, object]:
    counts = build_cell_counts(dataset)
    corpus_plan = build_corpus_plan(dataset)
    primary_counts = {cell: 0 for cell in PRIMARY_FILL_ORDER}
    for entry in dataset:
        primary_counts[primary_cell(entry)] += 1
    return {
        "target_size": TARGET_SIZE,
        "cell_floor": CELL_FLOOR,
        "required_cells": list(REQUIRED_CELLS),
        "primary_fill_order": list(PRIMARY_FILL_ORDER),
        "selected_question_ids": selected_question_ids(dataset),
        "required_cell_counts": {cell: counts[cell] for cell in REQUIRED_CELLS},
        "primary_counts": primary_counts,
        "corpus_plan": {
            "total_haystack_refs": corpus_plan.total_haystack_refs,
            "unique_session_ids": corpus_plan.unique_session_ids,
            "unique_normalized_contents": corpus_plan.unique_normalized_contents,
        },
        "selection_rules": [
            "Seed each required primary Phase 1 cell with its 5 smallest cases by (haystack_sessions, answer_session_ids, question_date, question_id).",
            "Seed the abstention overlay with its 5 smallest remaining _abs cases using the same deterministic ordering.",
            "Fill the remaining 20 slots round-robin across all primary question types, including single-session-preference, to stay stratified instead of letting the lightest MR/TR pool dominate the tail.",
            "Credit overlap cases to every cell they belong to; abstention is an overlay, not a separate primary question_type.",
        ],
    }


def render_coverage_report(dataset: list[BenchmarkCase]) -> str:
    summary = selection_summary(dataset)
    counts = build_cell_counts(dataset)
    corpus_plan = build_corpus_plan(dataset)
    corpus_refs = {
        qid: len(corpus_plan.question_corpus_refs.get(qid, ()))
        for qid in selected_question_ids(dataset)
    }

    lines = [
        "# Canonical LongMemEval Dev Subset Coverage",
        "",
        "This fixture locks an exact 50-case canonical iteration subset for `orchestrator.eval.longmemeval`.",
        "It is derived from the canonical LongMemEval source dataset and keeps the Phase 1 cell floors explicit instead of relying on informal sampling.",
        "",
        "## Source and intent",
        "",
        f"- Source dataset: `{CANONICAL_DATASET_PATH}`",
        "- Harness lane: canonical only (`orchestrator/eval/longmemeval.py` + `orchestrator/eval/runner.py` + `tests/longmemeval/ingest.py`)",
        "- Why the subset exists: the preserved full-corpus baseline evidence showed the canonical lane remained too slow for tight iteration even after barrier fixes, so this subset keeps canonical experimentation tractable without switching to the fast lane.",
        "",
        "## Deterministic selection rules",
        "",
        "1. Partition cases by primary `question_type`; treat `_abs` question IDs as overlapping `abstention` members in addition to their primary cell.",
        "2. For each required primary cell (`single-session-user`, `single-session-assistant`, `multi-session`, `temporal-reasoning`, `knowledge-update`), take the 5 smallest cases ordered by `(len(haystack_sessions), len(answer_session_ids), question_date, question_id)`.",
        "3. Take the 5 smallest remaining abstention cases using the same ordering.",
        "4. Fill the remaining 20 slots by round-robin over all primary question types (`single-session-user`, `single-session-assistant`, `single-session-preference`, `multi-session`, `temporal-reasoning`, `knowledge-update`), always taking the next smallest unselected case for that type.",
        "5. Round-robin fill is the tie-break that keeps the subset stratified at exactly 50 instead of letting the globally lightest MR/TR pool consume almost all tail slots.",
        "",
        "## Coverage summary",
        "",
        "| Cell | Locked cases | Floor | Status |",
        "| --- | ---: | ---: | --- |",
    ]
    for cell in REQUIRED_CELLS:
        status = "meets floor" if counts[cell] >= CELL_FLOOR else "below floor"
        lines.append(f"| {cell} | {counts[cell]} | {CELL_FLOOR} | {status} |")

    lines.extend(
        [
            "",
            "## Corpus-plan tractability snapshot",
            "",
            f"- Questions locked: {len(dataset)}",
            f"- Haystack refs inside locked subset: {corpus_plan.total_haystack_refs}",
            f"- Unique session IDs inside locked subset: {corpus_plan.unique_session_ids}",
            f"- Unique normalized corpus sessions inside locked subset: {corpus_plan.unique_normalized_contents}",
            "- Reference full-corpus canonical scale from preserved evidence: 500 questions and 18,464 unique normalized corpus sessions.",
            "",
            "## Locked case map",
            "",
            "| # | question_id | primary cell | overlap cells | haystack refs | canonical corpus refs | answer session ids |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: |",
        ]
    )

    for index, entry in enumerate(dataset, start=1):
        qid = question_id(entry)
        memberships = cell_memberships(entry)
        overlap_cells = [cell for cell in memberships if cell != primary_cell(entry)]
        overlap_label = ", ".join(overlap_cells) if overlap_cells else "-"
        lines.append(
            f"| {index} | `{qid}` | `{primary_cell(entry)}` | `{overlap_label}` | {_list_length(entry.get('haystack_sessions', []))} | {corpus_refs[qid]} | {_list_length(entry.get('answer_session_ids', []))} |"
        )

    lines.extend(
        [
            "",
            "## Overlap notes",
            "",
            "- Abstention is the only overlap cell in this subset; every `_abs` case still keeps its primary `question_type` membership.",
            "- The locked abstention quintet overlaps three primary cells: 1 `knowledge-update`, 2 `multi-session`, and 2 `temporal-reasoning`.",
            "- `single-session-preference` is not a required Phase 1 floor, but the round-robin fill still locks 3 preference cases so the dev subset does not erase that benchmark slice entirely.",
            "",
            "## Machine-checkable summary",
            "",
            "```json",
            json.dumps(summary, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_fixture_and_report(dataset_path: Path = CANONICAL_DATASET_PATH) -> list[BenchmarkCase]:
    dataset = load_source_dataset(dataset_path)
    subset = select_dev_subset(dataset)
    selected_ids = tuple(selected_question_ids(subset))
    if selected_ids != EXPECTED_QUESTION_IDS:
        raise ValueError(
            "Deterministic selection drifted from the committed canonical question-id lock"
        )
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    _ = FIXTURE_PATH.write_text(json.dumps(subset, indent=2) + "\n")
    _ = COVERAGE_REPORT_PATH.write_text(render_coverage_report(subset))
    return subset


if __name__ == "__main__":
    _ = write_fixture_and_report()
