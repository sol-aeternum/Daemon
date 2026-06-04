from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from orchestrator.memory.entities import (
    HASHTAG_PATTERN,
    MENTION_PATTERN,
    QUOTED_STRING,
    extract_candidates_baseline,
)
from tests.benchmark_longmemeval.taxonomy import build_taxonomy_entries, load_failure_rows


AUDIT_DIR = Path("tests/benchmark_results/dev_sweep_alias")
AUDIT_PATH = AUDIT_DIR / "AUDIT.md"
SKIPPED_PATH = AUDIT_DIR / "SKIPPED.md"
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "dev_subset.json"

EXPECTED_TARGET_QIDS = (
    "19b5f2b3",
    "25e5aa4f",
    "545bd2b5",
    "8550ddae",
    "86f00804",
    "ad7109d1",
)


def _load_fixture() -> dict[str, dict[str, object]]:
    data = cast(list[dict[str, object]], json.loads(FIXTURE_PATH.read_text()))
    return {str(item["question_id"]): item for item in data}


def _target_cell_qids() -> tuple[str, ...]:
    entries = build_taxonomy_entries(failure_rows=load_failure_rows())
    qids = sorted(
        str(entry["question_id"])
        for entry in entries
        if str(entry["stage"]) == "retrieval-miss"
        and str(entry["category"]) == "single-session-user"
    )
    return tuple(qids)


def _has_alias_like_surface(text: str) -> bool:
    return any(
        (
            QUOTED_STRING.search(text),
            HASHTAG_PATTERN.search(text),
            MENTION_PATTERN.search(text),
            re.search(r"\b[A-Z]{2,}\b", text),
        )
    )


def test_alias_audit_scope_artifacts_exist_and_record_skip_reason() -> None:
    assert AUDIT_PATH.is_file()
    assert SKIPPED_PATH.is_file()

    audit_text = AUDIT_PATH.read_text()
    skipped_text = SKIPPED_PATH.read_text()

    assert "_get_entity_expanded_candidates()" in audit_text
    assert "retrieval-miss × single-session-user" in audit_text
    assert "Result: skip implementation." in audit_text
    assert "Implementation was intentionally skipped after the required audit." in skipped_text


def test_alias_audit_scope_target_cell_has_no_alias_like_query_surface() -> None:
    fixture = _load_fixture()

    assert _target_cell_qids() == EXPECTED_TARGET_QIDS

    for qid in EXPECTED_TARGET_QIDS:
        question = str(fixture[qid]["question"])
        assert not _has_alias_like_surface(question), qid


def test_alias_audit_scope_existing_extractor_already_covers_plain_entity_tokens() -> None:
    fixture = _load_fixture()

    japan_candidates = {
        candidate.normalized_key
        for candidate in extract_candidates_baseline(str(fixture["19b5f2b3"]["question"]))
    }
    instagram_candidates = {
        candidate.normalized_key
        for candidate in extract_candidates_baseline(str(fixture["545bd2b5"]["question"]))
    }

    assert "japan" in japan_candidates
    assert "instagram" in instagram_candidates


def test_alias_audit_scope_full_fixture_alias_surfaces_stay_off_target() -> None:
    fixture = _load_fixture()

    surface_qids = {
        qid for qid, item in fixture.items() if _has_alias_like_surface(str(item["question"]))
    }

    assert surface_qids == {
        "0bb5a684",
        "184da446",
        "71a3fd6b",
        "7a8d0b71",
        "gpt4_4edbafa2",
    }
    assert surface_qids.isdisjoint(EXPECTED_TARGET_QIDS)
