from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Final, cast

from tests.benchmark_longmemeval.failure_dataset import (
    DEV_SUBSET_BASELINE_DIR,
    FAILURES_PATH,
)

JSONDict = dict[str, object]

STAGE_ORDER: Final[tuple[str, ...]] = (
    "extraction-miss",
    "retrieval-miss",
    "generation-error",
)
CATEGORY_ORDER: Final[tuple[str, ...]] = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "abstention",
)
TAXONOMY_PATH: Final[Path] = DEV_SUBSET_BASELINE_DIR / "taxonomy.md"
DENSE_CELL_MIN_COUNT: Final[int] = 3
REPRESENTATIVE_ID_LIMIT: Final[int] = 3

EXTRACTION_MISS_IDS: Final[tuple[str, ...]] = (
    "e8a79c70",
    "71a3fd6b",
    "28dc39ac",
    "gpt4_372c3eed_abs",
    "2bf43736",
    "0977f2af",
    "c5e8278d",
    "09d032c9",
    "59524333",
    "7a8d0b71",
)
RETRIEVAL_MISS_IDS: Final[tuple[str, ...]] = (
    "8550ddae",
    "86f00804",
    "19b5f2b3",
    "4388e9dd",
    "ba358f49",
    "6cb6f249",
    "2318644b",
    "1192316e",
    "8c18457d",
    "6613b389",
    "gpt4_af6db32f",
    "gpt4_b0863698",
    "5831f84d",
    "f685340e",
    "ad7109d1",
    "0bb5a684",
    "92a0aa75",
    "545bd2b5",
    "cc06de0d",
    "25e5aa4f",
)
GENERATION_ERROR_IDS: Final[tuple[str, ...]] = (
    "gpt4_4edbafa2",
    "852ce960",
    "gpt4_93159ced_abs",
    "75f70248",
    "c4f10528",
    "0a34ad58",
    "gpt4_65aabe59",
    "3ba21379",
    "gpt4_93159ced",
)

EXPECTED_STAGE_COUNTS: Final[dict[str, int]] = {
    "extraction-miss": len(EXTRACTION_MISS_IDS),
    "retrieval-miss": len(RETRIEVAL_MISS_IDS),
    "generation-error": len(GENERATION_ERROR_IDS),
}


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    sequence = cast(list[object], value)
    return [str(item) for item in sequence]


def _load_jsonl_rows(path: Path) -> list[JSONDict]:
    rows: list[JSONDict] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload_obj = cast(object, json.loads(line))
        if not isinstance(payload_obj, dict):
            raise ValueError(f"Expected JSON object rows in {path}")
        rows.append(cast(JSONDict, payload_obj))
    return rows


def load_failure_rows(path: Path = FAILURES_PATH) -> list[JSONDict]:
    if not path.exists():
        raise FileNotFoundError(f"Failure dataset missing: {path}")
    return _load_jsonl_rows(path)


def _build_stage_assignment_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for stage, question_ids in (
        ("extraction-miss", EXTRACTION_MISS_IDS),
        ("retrieval-miss", RETRIEVAL_MISS_IDS),
        ("generation-error", GENERATION_ERROR_IDS),
    ):
        for question_id in question_ids:
            if question_id in mapping:
                raise ValueError(f"Duplicate taxonomy stage assignment: {question_id}")
            mapping[question_id] = stage
    return mapping


STAGE_BY_QUESTION_ID: Final[dict[str, str]] = _build_stage_assignment_map()


def _question_metadata(row: JSONDict) -> JSONDict:
    metadata = row.get("question_metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"Missing question_metadata for {row.get('question_id')}")
    return cast(JSONDict, metadata)


def taxonomy_category(row: JSONDict) -> str:
    metadata = _question_metadata(row)
    if bool(metadata.get("is_abstention_variant")):
        return "abstention"
    return str(metadata.get("question_type", ""))


def _answer_support_sessions(
    row: JSONDict,
    occurrence: JSONDict,
) -> list[JSONDict]:
    metadata = _question_metadata(row)
    answer_session_ids = set(_normalize_string_list(metadata.get("answer_session_ids")))
    extraction_evidence = cast(JSONDict, occurrence["extraction_evidence"])
    scoped_sessions = cast(list[JSONDict], extraction_evidence.get("scoped_sessions", []))
    support_sessions: list[JSONDict] = []
    for scoped_session in scoped_sessions:
        raw_session_ids = set(_normalize_string_list(scoped_session.get("raw_session_ids")))
        if answer_session_ids & raw_session_ids:
            support_sessions.append(scoped_session)
    return support_sessions


def _answer_session_statuses(row: JSONDict, occurrence: JSONDict) -> dict[str, list[str]]:
    metadata = _question_metadata(row)
    answer_session_ids = _normalize_string_list(metadata.get("answer_session_ids"))
    statuses_by_session_id: dict[str, list[str]] = {
        session_id: [] for session_id in answer_session_ids
    }
    for scoped_session in _answer_support_sessions(row, occurrence):
        status = str(scoped_session.get("status", "missing"))
        raw_session_ids = set(_normalize_string_list(scoped_session.get("raw_session_ids")))
        for answer_session_id in answer_session_ids:
            if answer_session_id in raw_session_ids:
                statuses_by_session_id[answer_session_id].append(status)
    return statuses_by_session_id


def occurrence_has_complete_answer_support(
    row: JSONDict,
    occurrence: JSONDict,
) -> bool:
    statuses_by_session_id = _answer_session_statuses(row, occurrence)
    return bool(statuses_by_session_id) and all(
        statuses and all(status == "complete" for status in statuses)
        for statuses in statuses_by_session_id.values()
    )


def _complete_support_occurrences(row: JSONDict) -> list[JSONDict]:
    occurrences = cast(list[JSONDict], row.get("failure_occurrences", []))
    return [
        occurrence
        for occurrence in occurrences
        if occurrence_has_complete_answer_support(row, occurrence)
    ]


def _incomplete_answer_statuses(row: JSONDict) -> dict[str, list[str]]:
    incomplete: dict[str, list[str]] = {}
    occurrences = cast(list[JSONDict], row.get("failure_occurrences", []))
    for occurrence in occurrences:
        statuses = sorted(
            {
                str(session.get("status", "missing"))
                for session in _answer_support_sessions(row, occurrence)
                if str(session.get("status", "")) != "complete"
            }
        )
        if statuses:
            incomplete[str(occurrence.get("run_id", ""))] = statuses
    return incomplete


def _percent(count: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{(count / total) * 100:.1f}%"


def _answer_excerpt(answer: object, *, limit: int = 120) -> str:
    text = " ".join(str(answer).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _markdown_escape(text: str) -> str:
    return text.replace("|", "\\|")


def _evidence_note(row: JSONDict, stage: str) -> str:
    question_id = str(row.get("question_id", ""))
    complete_support_occurrences = _complete_support_occurrences(row)

    if stage == "extraction-miss":
        incomplete = _incomplete_answer_statuses(row)
        run_bits = ", ".join(
            f"{run_id}:{'/'.join(statuses)}" for run_id, statuses in incomplete.items()
        )
        return (
            "Every failed occurrence left at least one answer session unextracted "
            f"({run_bits}), so no failed run preserved fully extracted support for {question_id}."
        )

    occurrence = complete_support_occurrences[0]
    run_id = str(occurrence.get("run_id", ""))
    answer = _answer_excerpt(occurrence.get("model_answer", ""))

    if stage == "retrieval-miss":
        return (
            f"{run_id} fully extracted the answer sessions, but the hypothesis still treated the "
            f"needed fact as missing or insufficient: “{answer}”."
        )

    return (
        f"{run_id} fully extracted the answer sessions, yet the hypothesis still committed to "
        f"a wrong or preference-blind answer: “{answer}”."
    )


def build_taxonomy_entries(
    failure_rows: list[JSONDict] | None = None,
) -> list[JSONDict]:
    rows = failure_rows or load_failure_rows()
    entries: list[JSONDict] = []
    for row in rows:
        question_id = str(row.get("question_id", ""))
        if question_id not in STAGE_BY_QUESTION_ID:
            raise ValueError(f"Missing stage assignment for {question_id}")
        category = taxonomy_category(row)
        if category not in CATEGORY_ORDER:
            raise ValueError(f"Unsupported category assignment for {question_id}: {category}")
        stage = STAGE_BY_QUESTION_ID[question_id]
        metadata = _question_metadata(row)
        entries.append(
            {
                "question_id": question_id,
                "stage": stage,
                "category": category,
                "raw_question_type": str(metadata.get("question_type", "")),
                "observed_failure_runs": _normalize_string_list(
                    row.get("observed_failure_runs", [])
                ),
                "evidence_note": _evidence_note(row, stage),
            }
        )
    return entries


def _ids_for_stage(entries: list[JSONDict], stage: str) -> list[str]:
    return [str(entry["question_id"]) for entry in entries if entry["stage"] == stage]


def _ids_for_category(entries: list[JSONDict], category: str) -> list[str]:
    return [str(entry["question_id"]) for entry in entries if entry["category"] == category]


def _cell_ids(
    entries: list[JSONDict],
) -> dict[str, dict[str, list[str]]]:
    cells: dict[str, dict[str, list[str]]] = {
        stage: {category: [] for category in CATEGORY_ORDER} for stage in STAGE_ORDER
    }
    for entry in entries:
        stage = str(entry["stage"])
        category = str(entry["category"])
        cells[stage][category].append(str(entry["question_id"]))
    return cells


def _matrix_cell_markdown(ids: list[str], total: int) -> str:
    if not ids:
        return f"0 ({_percent(0, total)})"
    cell = f"{len(ids)} ({_percent(len(ids), total)})"
    if len(ids) >= DENSE_CELL_MIN_COUNT:
        representative_ids = ", ".join(
            f"`{question_id}`" for question_id in ids[:REPRESENTATIVE_ID_LIMIT]
        )
        cell += f"<br>{representative_ids}"
    return cell


def render_taxonomy_markdown(
    failure_rows: list[JSONDict] | None = None,
) -> str:
    rows = failure_rows or load_failure_rows()
    entries = build_taxonomy_entries(failure_rows=rows)
    total = len(entries)
    cells = _cell_ids(entries)
    stage_counts = Counter(str(entry["stage"]) for entry in entries)
    category_counts = Counter(str(entry["category"]) for entry in entries)

    lines = [
        "# Dev subset baseline failure taxonomy",
        "",
        "Built from `tests/benchmark_results/dev_subset_baseline/failures.jsonl` and the committed locked canonical runs `run1` + `run2` only.",
        "Phase 0 remains reopened because those two scored runs landed at `32.0%` and `22.0%`, but this taxonomy still uses their locked 39-row union as the baseline failure corpus.",
        "",
        f"Total classified failures: **{total}**.",
        "",
        "## Classification contract",
        "",
        "1. Stage names follow the repo-native intent in `orchestrator/eval/diagnostics.py` (`extraction_miss`, `retrieval_miss`, `reader_failure`), but the plan-requested hyphenated labels are authoritative here: `extraction-miss`, `retrieval-miss`, and `generation-error`.",
        "2. `tests/benchmark_longmemeval/dev_subset.py` treats abstention as an overlay, not a primary `question_type`. To keep a single unique category assignment per failure, every `_abs` row is categorized as `abstention` instead of its raw primary cell.",
        "3. Because the locked artifacts do **not** preserve retrieval-log rows or exact selected-memory snapshots, retrieval vs generation is inferred only after the answer sessions are known to be fully extracted:",
        "   - `extraction-miss`: every failed occurrence still leaves at least one answer session in `extraction_timeout` or `extraction_failed`.",
        "   - `retrieval-miss`: at least one failed occurrence fully extracts the answer sessions, but the hypothesis still says the needed fact is missing, unavailable, or insufficient.",
        "   - `generation-error`: at least one failed occurrence fully extracts the answer sessions, and the hypothesis instead commits to a wrong value/entity/order or a preference-blind partial answer.",
        "",
        "## Stage × category matrix",
        "",
        f"Dense cell threshold: **{DENSE_CELL_MIN_COUNT}+** failures. Percentages are of the full **{total}**-row corpus; dense cells show up to {REPRESENTATIVE_ID_LIMIT} representative IDs in fixture order.",
        "",
        "| Stage \\ Category | single-session-user | single-session-assistant | single-session-preference | multi-session | temporal-reasoning | knowledge-update | abstention | Total |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for stage in STAGE_ORDER:
        row_cells = [f"`{stage}`"]
        for category in CATEGORY_ORDER:
            row_cells.append(_matrix_cell_markdown(cells[stage][category], total))
        row_cells.append(f"{stage_counts[stage]} ({_percent(stage_counts[stage], total)})")
        lines.append("| " + " | ".join(row_cells) + " |")

    lines.extend(
        [
            "",
            "## Stage totals",
            "",
            "| Stage | Count | Share |",
            "| --- | --- | --- |",
        ]
    )
    for stage in STAGE_ORDER:
        lines.append(
            f"| `{stage}` | {stage_counts[stage]} | {_percent(stage_counts[stage], total)} |"
        )

    lines.extend(
        [
            "",
            "## Category totals",
            "",
            "| Category | Count | Share |",
            "| --- | --- | --- |",
        ]
    )
    for category in CATEGORY_ORDER:
        lines.append(
            f"| `{category}` | {category_counts[category]} | {_percent(category_counts[category], total)} |"
        )

    lines.extend(["", "## Stage assignment buckets", ""])
    for stage in STAGE_ORDER:
        stage_ids = _ids_for_stage(entries, stage)
        joined_ids = ", ".join(f"`{question_id}`" for question_id in stage_ids)
        lines.extend(
            [
                f"### `{stage}` — {len(stage_ids)} / {total} ({_percent(len(stage_ids), total)})",
                "",
                joined_ids,
                "",
            ]
        )

    lines.extend(["## Category assignment buckets", ""])
    for category in CATEGORY_ORDER:
        category_ids = _ids_for_category(entries, category)
        joined_ids = ", ".join(f"`{question_id}`" for question_id in category_ids)
        lines.extend(
            [
                f"### `{category}` — {len(category_ids)} / {total} ({_percent(len(category_ids), total)})",
                "",
                joined_ids,
                "",
            ]
        )

    lines.extend(
        [
            "## Complete per-question assignments",
            "",
            "| Question ID | Stage | Category | Failure runs | Evidence note |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for entry in entries:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{entry['question_id']}`",
                    f"`{entry['stage']}`",
                    f"`{entry['category']}`",
                    ", ".join(
                        f"`{run_id}`"
                        for run_id in cast(list[object], entry["observed_failure_runs"])
                    ),
                    _markdown_escape(str(entry["evidence_note"])),
                ]
            )
            + " |"
        )

    lines.append("")
    return "\n".join(lines)


def write_taxonomy_report(path: Path = TAXONOMY_PATH) -> str:
    markdown = render_taxonomy_markdown()
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(markdown)
    return markdown


def main() -> None:
    _ = write_taxonomy_report()
    print(f"Wrote taxonomy report to {TAXONOMY_PATH}")


if __name__ == "__main__":
    main()
