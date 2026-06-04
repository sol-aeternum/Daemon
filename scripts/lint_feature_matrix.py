#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
from pathlib import Path


FEATURE_MATRIX_PATH = Path("docs/FEATURE_MATRIX.md")
EXPECTED_HEADER = (
    "| Feature | Web | Android PWA | Android native | iOS future | "
    "Backend dependency | Wedge required? |"
)
CLIENT_COLUMN_LABELS = ("Web", "Android PWA", "Android native", "iOS future")
MINIMUM_FEATURE_ROWS = 20


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_matrix(root: Path) -> tuple[list[str], list[tuple[int, str]]]:
    matrix_path = root / FEATURE_MATRIX_PATH
    if not matrix_path.exists():
        return [], [(1, f"{FEATURE_MATRIX_PATH.as_posix()} not found")]

    return matrix_path.read_text(encoding="utf-8").splitlines(), []


def parse_legend_vocabulary(lines: list[str]) -> tuple[set[str], list[tuple[int, str]]]:
    violations: list[tuple[int, str]] = []
    vocabulary: set[str] = set()
    in_legend = False

    for line in lines:
        if line == "## Legend":
            in_legend = True
            continue

        if in_legend and line.startswith("## "):
            break

        if not in_legend:
            continue

        match = re.search(r"`([^`]+)`\s*=", line)
        if match:
            vocabulary.add(match.group(1))

    if not in_legend:
        violations.append((1, "missing '## Legend' section"))
    elif not vocabulary:
        violations.append((1, "Legend does not define any controlled vocabulary terms"))

    return vocabulary, violations


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []

    return [cell.strip() for cell in stripped[1:-1].split("|")]


def find_table(
    lines: list[str],
) -> tuple[tuple[int, str] | None, tuple[int, str] | None, list[tuple[int, str]]]:
    violations: list[tuple[int, str]] = []
    in_feature_matrix = False
    header: tuple[int, str] | None = None
    delimiter: tuple[int, str] | None = None

    for line_number, line in enumerate(lines, start=1):
        if line == "## Feature Matrix":
            in_feature_matrix = True
            continue

        if not in_feature_matrix:
            continue

        if line.startswith("## "):
            break

        if header is None:
            if line.startswith("|"):
                header = (line_number, line)
            continue

        if delimiter is None:
            if line.startswith("|"):
                delimiter = (line_number, line)
                break

    if not in_feature_matrix:
        violations.append((1, "missing '## Feature Matrix' section"))
    elif header is None:
        violations.append((1, "Feature Matrix table header not found"))
    elif delimiter is None:
        violations.append((header[0], "Feature Matrix table delimiter row not found"))

    return header, delimiter, violations


def is_category_row(cells: list[str]) -> bool:
    if len(cells) != 7:
        return False

    feature = cells[0]
    return bool(re.fullmatch(r"\*\*[^*]+\*\*", feature)) and all(cell == "—" for cell in cells[1:])


def looks_like_category_label(feature: str) -> bool:
    return bool(re.fullmatch(r"\*\*[^*]+\*\*", feature))


def validate_header(line_number: int, line: str) -> list[tuple[int, str]]:
    if line == EXPECTED_HEADER:
        return []
    return [(line_number, f"expected exact header '{EXPECTED_HEADER}'")]


def validate_delimiter(line_number: int, line: str) -> list[tuple[int, str]]:
    cells = split_markdown_row(line)
    if len(cells) != 7:
        return [(line_number, f"expected 7 delimiter cells, found {len(cells)}")]
    violations: list[tuple[int, str]] = []
    if any(not re.fullmatch(r"-+", cell) for cell in cells):
        violations.append((line_number, "delimiter row must contain only hyphen groups"))
    return violations


def validate_rows(
    lines: list[str],
    start_line: int,
    allowed_vocabulary: set[str],
) -> tuple[int, list[tuple[int, str]]]:
    violations: list[tuple[int, str]] = []
    feature_rows = 0

    for line_number in range(start_line, len(lines) + 1):
        raw_line = lines[line_number - 1]
        if not raw_line.startswith("|"):
            if feature_rows or violations:
                break
            continue

        cells = split_markdown_row(raw_line)
        if len(cells) != 7:
            violations.append((line_number, f"expected 7 cells, found {len(cells)}"))
            continue

        if looks_like_category_label(cells[0]) and not all(cell == "—" for cell in cells[1:]):
            violations.append(
                (
                    line_number,
                    "category separator rows must use a bold Feature cell and '—' in all remaining six cells",
                )
            )
            continue

        if is_category_row(cells):
            continue

        feature_rows += 1

        for column_index, column_label in enumerate(CLIENT_COLUMN_LABELS, start=1):
            value = cells[column_index]
            if value not in allowed_vocabulary:
                violations.append(
                    (line_number, f"{column_label} must use Legend vocabulary, found '{value}'")
                )

        backend_dependency = cells[5]
        if not backend_dependency:
            violations.append((line_number, "Backend dependency must be non-empty"))

        wedge_value = cells[6]
        if wedge_value not in {"Yes", "No"}:
            violations.append(
                (line_number, f"Wedge required? must be 'Yes' or 'No', found '{wedge_value}'")
            )

    if feature_rows < MINIMUM_FEATURE_ROWS:
        violations.append(
            (
                start_line,
                f"Feature Matrix table must contain at least {MINIMUM_FEATURE_ROWS} feature rows, found {feature_rows}",
            )
        )

    return feature_rows, violations


def collect_violations(argv: list[str]) -> tuple[int, list[tuple[int, str]]]:
    violations: list[tuple[int, str]] = []
    if len(argv) != 1:
        violations.append((1, "script accepts no arguments"))
        return 0, violations

    lines, read_violations = read_matrix(repo_root())
    if read_violations:
        return 0, read_violations

    vocabulary, legend_violations = parse_legend_vocabulary(lines)
    violations.extend(legend_violations)

    header, delimiter, table_violations = find_table(lines)
    violations.extend(table_violations)
    if header is None or delimiter is None:
        return 0, violations

    violations.extend(validate_header(*header))
    violations.extend(validate_delimiter(*delimiter))

    row_count, row_violations = validate_rows(lines, delimiter[0] + 1, vocabulary)
    violations.extend(row_violations)
    return row_count, violations


def main() -> int:
    row_count, violations = collect_violations(sys.argv)
    if violations:
        for line_number, message in violations:
            print(f"LINE {line_number}: {message}")
        print(f"FAIL: {len(violations)} violation(s)")
        return 1

    print(f"OK: {row_count} feature rows validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
