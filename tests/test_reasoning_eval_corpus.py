"""The frozen reasoning-routing evaluation corpus (docs/REASONING_EVAL_PROTOCOL.md).

Any edit to corpus_v1.json fails the hash check: a change is a new corpus version,
never an in-place edit, so held-out cases cannot drift after results exist.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).parent / "fixtures" / "reasoning_eval"
MANIFEST = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
RAW = (HERE / MANIFEST["corpus"]).read_bytes()
CORPUS = json.loads(RAW)
CASES: list[dict[str, Any]] = CORPUS["cases"]

SLICES = {
    "everyday",
    "coding",
    "planning",
    "synthesis",
    "followup",
    "topic_shift",
    "quoted_injection",
    "revealed_by_tools",
    "missing_info",
    "non_english",
}
SPLITS = {"dev", "val", "test"}
STRATA = {"easy", "hard", "deceptively_hard", "long_easy"}
LATENCY = {"utility", "orchestration", "synthesis"}
FIELDS = {
    "case_id",
    "split",
    "slice",
    "stratum",
    "latency_class",
    "history",
    "prompt",
    "tools",
    "tool_responses",
    "rubric",
}


def test_corpus_matches_its_frozen_hash() -> None:
    assert hashlib.sha256(RAW).hexdigest() == MANIFEST["sha256"]
    assert CORPUS["artifact_version"] == MANIFEST["artifact_version"]
    assert len(CASES) == MANIFEST["cases"]


def test_every_slice_has_two_cases_per_split() -> None:
    counts = Counter((case["slice"], case["split"]) for case in CASES)
    assert {slice_ for slice_, _ in counts} == SLICES
    assert all(counts[(slice_, split)] == 2 for slice_ in SLICES for split in SPLITS)


def test_case_ids_are_unique() -> None:
    ids = [case["case_id"] for case in CASES]
    assert len(ids) == len(set(ids))


def test_each_stratum_is_present_in_the_held_out_split() -> None:
    held_out = {case["stratum"] for case in CASES if case["split"] == "test"}
    assert STRATA <= held_out


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_case_schema(case: dict[str, Any]) -> None:
    assert set(case) == FIELDS
    assert case["split"] in SPLITS
    assert case["slice"] in SLICES
    assert case["stratum"] in STRATA
    assert case["latency_class"] in LATENCY
    assert isinstance(case["prompt"], str) and case["prompt"].strip()
    for message in case["history"]:
        assert set(message) == {"role", "content"}
        assert message["role"] in {"user", "assistant"}
    rubric = case["rubric"]
    assert set(rubric) == {"acceptable", "hard_violations", "notes"}
    assert rubric["acceptable"] and all(isinstance(item, str) for item in rubric["acceptable"])
    assert rubric["hard_violations"]
    if case["slice"] == "followup":
        assert case["history"], "follow-up cases carry the turn they follow"


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["tools"]],
    ids=[case["case_id"] for case in CASES if case["tools"]],
)
def test_tool_fixtures_are_argument_sensitive_and_named(case: dict[str, Any]) -> None:
    names = {tool["function"]["name"] for tool in case["tools"]}
    assert set(case["tool_responses"]) <= names
    for entries in case["tool_responses"].values():
        matches = [json.dumps(entry["match"], sort_keys=True) for entry in entries]
        assert len(matches) == len(set(matches)), "each argument set has one response"


def test_tool_slice_cases_all_declare_tools() -> None:
    assert all(case["tools"] for case in CASES if case["slice"] == "revealed_by_tools")
