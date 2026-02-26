from __future__ import annotations

import pytest

from orchestrator.memory.extraction import (
    ExtractedFact,
    calibrate_confidence,
    validate_fact,
)


@pytest.mark.parametrize(
    ("content", "confidence", "expected"),
    [
        ("User might move to Japan", 0.80, 0.65),
        ("User is maybe allergic to pollen", 0.76, 0.65),
        ("User is thinking about switching jobs", 0.90, 0.65),
    ],
)
def test_calibrate_confidence_hedges(
    content: str, confidence: float, expected: float
) -> None:
    fact = ExtractedFact(content=content, category="fact", confidence=confidence)
    calibrated = calibrate_confidence(fact)
    assert calibrated.confidence == pytest.approx(expected)


@pytest.mark.parametrize(
    ("content", "confidence", "expected"),
    [
        ("User is definitely allergic to shellfish", 0.80, 0.92),
        ("User has diagnosed celiac disease", 0.82, 0.92),
        ("User always takes coffee black", 0.84, 0.92),
    ],
)
def test_calibrate_confidence_strong_signals(
    content: str, confidence: float, expected: float
) -> None:
    fact = ExtractedFact(content=content, category="fact", confidence=confidence)
    calibrated = calibrate_confidence(fact)
    assert calibrated.confidence == pytest.approx(expected)


def test_calibrate_confidence_correction_minimum() -> None:
    fact = ExtractedFact(
        content="User corrected prior car ownership statement",
        category="correction",
        confidence=0.80,
    )
    calibrated = calibrate_confidence(fact)
    assert calibrated.confidence == pytest.approx(0.90)


def test_calibrate_confidence_mixed_signal_prefers_hedge() -> None:
    fact = ExtractedFact(
        content="User might definitely move next year",
        category="fact",
        confidence=0.80,
    )
    calibrated = calibrate_confidence(fact)
    assert calibrated.confidence == pytest.approx(0.65)


def test_calibrate_confidence_neutral_unchanged() -> None:
    fact = ExtractedFact(
        content="User works remotely", category="fact", confidence=0.81
    )
    calibrated = calibrate_confidence(fact)
    assert calibrated.confidence == pytest.approx(0.81)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Hi", False),
        ("", False),
        ("assistant said the user likes tea", False),
        ("The NVIDIA RTX 5090 can draw up to 600W", False),
        ("user greeted", False),
        ("user thanked", False),
        ("User is heading to bed", False),
        ("User said goodnight", False),
        ("User is going to bed early tonight", False),
        ("User said brb", False),
        ("User may travel to Japan", True),
        ("User works from bed sometimes", True),
        ("User prefers sleeping early", True),
        ("User mentioned brb stands for be right back", True),
        ("The user prefers TOML", True),
        ("The user's birthday is March 15th", True),
    ],
)
def test_validate_fact_rules(content: str, expected: bool) -> None:
    fact = ExtractedFact(content=content, category="fact", confidence=0.8)
    assert validate_fact(fact) is expected
