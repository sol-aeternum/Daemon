from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.longmemeval.evaluate import judge_answer, score_accuracy


class MockResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def model_dump(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.mark.asyncio
async def test_judge_answer_parses_correct_uppercase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("CORRECT\nThis is a valid answer.")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="Paris",
        reference="Paris",
    )
    assert result == "correct"


@pytest.mark.asyncio
async def test_judge_answer_parses_partial_uppercase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("PARTIAL\nThe answer is missing some facts.")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What are the colors of the French flag?",
        hypothesis="Blue and white",
        reference="Blue, white, and red",
    )
    assert result == "partially_correct"


@pytest.mark.asyncio
async def test_judge_answer_parses_incorrect_uppercase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("INCORRECT\nThe answer contradicts the reference.")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="London",
        reference="Paris",
    )
    assert result == "incorrect"


@pytest.mark.asyncio
async def test_judge_answer_parses_correct_lowercase_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("correct\nThe answer matches.")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="Paris",
        reference="Paris",
    )
    assert result == "correct"


@pytest.mark.asyncio
async def test_judge_answer_parses_partial_lowercase_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("partial\nSome facts are missing.")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What are the colors of the French flag?",
        hypothesis="Blue",
        reference="Blue, white, and red",
    )
    assert result == "partially_correct"


@pytest.mark.asyncio
async def test_judge_answer_parses_incorrect_lowercase_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("incorrect\nThe answer is wrong.")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="Berlin",
        reference="Paris",
    )
    assert result == "incorrect"


@pytest.mark.asyncio
async def test_judge_answer_fallback_on_unexpected_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("maybe\nThis is ambiguous.")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="Paris",
        reference="Paris",
    )
    assert result == "incorrect"


@pytest.mark.asyncio
async def test_judge_answer_fallback_on_garbage_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("garbage response with no clear verdict")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="Paris",
        reference="Paris",
    )
    assert result == "incorrect"


@pytest.mark.asyncio
async def test_judge_answer_fallback_incorrect_substring_not_misclassified_as_correct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("that is incorrect\nThe answer does not match.")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="Berlin",
        reference="Paris",
    )
    assert result == "incorrect"


@pytest.mark.asyncio
async def test_judge_answer_fallback_on_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MockResponse("")
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=mock_response),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="Paris",
        reference="Paris",
    )
    assert result == "incorrect"


@pytest.mark.asyncio
async def test_judge_answer_returns_incorrect_on_none_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tests.longmemeval.evaluate._call_llm_with_provider_config",
        AsyncMock(return_value=None),
    )
    result = await judge_answer(
        question_text="What is the capital of France?",
        hypothesis="Paris",
        reference="Paris",
    )
    assert result == "incorrect"


def test_score_accuracy_maps_correct_judgments() -> None:
    results = [
        {"category": "IE-user", "judgment": "correct"},
        {"category": "IE-user", "judgment": "correct"},
        {"category": "IE-user", "judgment": "incorrect"},
    ]
    accuracy = score_accuracy(results)
    assert accuracy["IE-user"] == 2 / 3


def test_score_accuracy_maps_partial_correct_judgments() -> None:
    results = [
        {"category": "MR", "judgment": "partially_correct"},
        {"category": "MR", "judgment": "correct"},
        {"category": "MR", "judgment": "incorrect"},
    ]
    accuracy = score_accuracy(results)
    assert accuracy["MR"] == 1 / 3


def test_score_accuracy_handles_zero_results() -> None:
    results: list[dict[str, Any]] = []
    accuracy = score_accuracy(results)
    assert accuracy["IE-user"] == 0.0


def test_score_accuracy_handles_all_incorrect() -> None:
    results = [
        {"category": "KU", "judgment": "incorrect"},
        {"category": "KU", "judgment": "incorrect"},
    ]
    accuracy = score_accuracy(results)
    assert accuracy["KU"] == 0.0


def test_score_accuracy_handles_all_correct() -> None:
    results = [
        {"category": "TR", "judgment": "correct"},
        {"category": "TR", "judgment": "correct"},
        {"category": "TR", "judgment": "correct"},
    ]
    accuracy = score_accuracy(results)
    assert accuracy["TR"] == 1.0


def test_score_accuracy_unknown_category_is_ignored() -> None:
    results = [
        {"category": "IE-user", "judgment": "correct"},
        {"category": "UNKNOWN-CAT", "judgment": "correct"},
    ]
    accuracy = score_accuracy(results)
    assert "UNKNOWN-CAT" not in accuracy
    assert accuracy["IE-user"] == 1.0
