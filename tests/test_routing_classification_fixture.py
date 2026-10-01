"""Characterization of message classification against a frozen fixture.

Every row records what the classifier returns, including documented defects and
limitations, so any change in routing behaviour shows up as a reviewed fixture diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.model_router import classify_message, select_model_tier

FIXTURE = Path(__file__).parent / "fixtures" / "routing_classification.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_fixture_case_ids_are_unique() -> None:
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_classification_matches_fixture(case: dict[str, Any]) -> None:
    assert classify_message(case["message"]) == case["classification"]
    decision = select_model_tier(case["message"])
    assert decision.profile == case["profile"]
    assert decision.model == ""
