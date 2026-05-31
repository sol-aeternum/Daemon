"""Focused tests for retrieval failure-mode diagnostics.

Tests evidence-based classification (extraction_miss, retrieval_miss,
reader_failure) using actual memory existence checks.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from orchestrator.eval.diagnostics import (
    DiagnosticResult,
    FailureMode,
    RetrievalEvidence,
    SupportingMemoryInfo,
    compute_category_breakdown,
    build_machine_readable_summary,
    build_human_readable_report,
    classify_failure,
    find_supporting_memories,
    merge_supporting_memory_into_evidence,
)


class TestClassifyFailure:
    def test_correct_answer_returns_empty(self) -> None:
        result = {"judgment": "correct"}
        evidence = None
        supporting = SupportingMemoryInfo(False, [], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == ""
        assert note == ""

    def test_no_evidence_no_supporting_returns_extraction_miss(self) -> None:
        result = {"judgment": "incorrect"}
        evidence = None
        supporting = SupportingMemoryInfo(False, [], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.EXTRACTION_MISS
        assert "never extracted" in note

    def test_no_evidence_supporting_not_in_candidates_returns_extraction_miss(
        self,
    ) -> None:
        result = {"judgment": "incorrect"}
        evidence = None
        supporting = SupportingMemoryInfo(True, [uuid.uuid4()], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.EXTRACTION_MISS
        assert "not in candidate set" in note

    def test_no_evidence_supporting_in_candidates_unknown(self) -> None:
        result = {"judgment": "incorrect"}
        evidence = None
        supporting = SupportingMemoryInfo(True, [uuid.uuid4()], True, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.UNKNOWN
        assert "cannot determine" in note

    def test_zero_candidates_returns_extraction_miss(self) -> None:
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test",
            candidate_ids=[],
            selected_ids=[],
            candidate_scores={},
            l0_included=False,
            latency_ms=10,
        )
        supporting = SupportingMemoryInfo(False, [], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.EXTRACTION_MISS
        assert "zero candidates" in note

    def test_supporting_memory_not_found_returns_extraction_miss(self) -> None:
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test",
            candidate_ids=[uuid.uuid4(), uuid.uuid4()],
            selected_ids=[uuid.uuid4()],
            candidate_scores={},
            l0_included=False,
            latency_ms=10,
        )
        supporting = SupportingMemoryInfo(False, [], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.EXTRACTION_MISS
        assert "never extracted" in note

    def test_supporting_memory_in_selected_returns_reader_failure(self) -> None:
        mem_id = uuid.uuid4()
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test",
            candidate_ids=[uuid.uuid4(), mem_id],
            selected_ids=[mem_id],
            candidate_scores={},
            l0_included=False,
            latency_ms=10,
        )
        supporting = SupportingMemoryInfo(True, [mem_id], True, True)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.READER_FAILURE
        assert "retrieved correct memory but produced wrong answer" in note

    def test_supporting_memory_in_candidates_not_selected_returns_retrieval_miss(
        self,
    ) -> None:
        mem_id = uuid.uuid4()
        other_id = uuid.uuid4()
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test",
            candidate_ids=[other_id, mem_id],
            selected_ids=[other_id],
            candidate_scores={},
            l0_included=False,
            latency_ms=10,
        )
        supporting = SupportingMemoryInfo(True, [mem_id], True, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.RETRIEVAL_MISS
        assert "not selected" in note

    def test_supporting_memory_exists_not_in_candidates_returns_retrieval_miss(
        self,
    ) -> None:
        mem_id = uuid.uuid4()
        other_id = uuid.uuid4()
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test",
            candidate_ids=[other_id],
            selected_ids=[other_id],
            candidate_scores={},
            l0_included=False,
            latency_ms=10,
        )
        supporting = SupportingMemoryInfo(True, [mem_id], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.RETRIEVAL_MISS
        assert "not in candidates" in note

    def test_classify_unknown_when_evidence_unclear(self) -> None:
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test",
            candidate_ids=[uuid.uuid4(), uuid.uuid4()],
            selected_ids=[uuid.uuid4()],
            candidate_scores={},
            l0_included=False,
            latency_ms=10,
        )
        supporting = SupportingMemoryInfo(False, [], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.EXTRACTION_MISS


class TestMergeSupportingMemoryIntoEvidence:
    def test_none_evidence_returns_supporting_unchanged(self) -> None:
        supporting = SupportingMemoryInfo(True, [uuid.uuid4()], False, False)
        result = merge_supporting_memory_into_evidence(None, supporting)
        assert result.found == supporting.found
        assert result.in_candidates == supporting.in_candidates
        assert result.in_selected == supporting.in_selected

    def test_updates_in_candidates_and_in_selected(self) -> None:
        mem_id = uuid.uuid4()
        candidate_id = uuid.uuid4()
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test",
            candidate_ids=[candidate_id, mem_id],
            selected_ids=[candidate_id],
            candidate_scores={},
            l0_included=False,
            latency_ms=10,
        )
        supporting = SupportingMemoryInfo(True, [mem_id], False, False)
        result = merge_supporting_memory_into_evidence(evidence, supporting)
        assert result.in_candidates is True
        assert result.in_selected is False


class TestComputeCategoryBreakdown:
    def test_empty_results(self) -> None:
        breakdown = compute_category_breakdown([])
        assert breakdown == {}

    def test_correct_answer_not_in_breakdown(self) -> None:
        results = [
            DiagnosticResult(
                question_id="q1",
                question="test?",
                reference="answer",
                hypothesis="answer",
                category="IE-user",
                judgment="correct",
                failure_mode="",
                evidence=None,
                supporting_memory=None,
                memories_used=3,
            )
        ]
        breakdown = compute_category_breakdown(results)
        total = sum(count for counts in breakdown.values() for count in counts.values())
        assert total == 0

    def test_wrong_answers_grouped_by_category(self) -> None:
        results = [
            DiagnosticResult(
                question_id="q1",
                question="test?",
                reference="answer",
                hypothesis="wrong",
                category="IE-user",
                judgment="incorrect",
                failure_mode=FailureMode.EXTRACTION_MISS,
                evidence=None,
                supporting_memory=None,
                memories_used=0,
            ),
            DiagnosticResult(
                question_id="q2",
                question="test2?",
                reference="answer2",
                hypothesis="wrong2",
                category="IE-user",
                judgment="incorrect",
                failure_mode=FailureMode.RETRIEVAL_MISS,
                evidence=None,
                supporting_memory=None,
                memories_used=5,
            ),
            DiagnosticResult(
                question_id="q3",
                question="test3?",
                reference="answer3",
                hypothesis="wrong3",
                category="MR",
                judgment="incorrect",
                failure_mode=FailureMode.READER_FAILURE,
                evidence=None,
                supporting_memory=None,
                memories_used=5,
            ),
        ]
        breakdown = compute_category_breakdown(results)
        assert breakdown["IE-user"][FailureMode.EXTRACTION_MISS] == 1
        assert breakdown["IE-user"][FailureMode.RETRIEVAL_MISS] == 1
        assert breakdown["MR"][FailureMode.READER_FAILURE] == 1


class TestBuildMachineReadableSummary:
    def test_summary_structure(self) -> None:
        results = [
            DiagnosticResult(
                question_id="q1",
                question="test?",
                reference="answer",
                hypothesis="wrong",
                category="IE-user",
                judgment="incorrect",
                failure_mode=FailureMode.EXTRACTION_MISS,
                evidence=None,
                supporting_memory=SupportingMemoryInfo(False, [], False, False),
                memories_used=0,
                note="no supporting memory",
            ),
        ]
        category_breakdown = compute_category_breakdown(results)
        summary = build_machine_readable_summary(results, category_breakdown)

        assert summary["total_questions"] == 1
        assert summary["failure_mode_counts"][FailureMode.EXTRACTION_MISS] == 1
        assert summary["failure_mode_counts"][FailureMode.UNKNOWN] == 0
        assert len(summary["results"]) == 1
        assert summary["results"][0]["question_id"] == "q1"
        assert summary["results"][0]["failure_mode"] == FailureMode.EXTRACTION_MISS

    def test_supporting_memory_in_summary(self) -> None:
        mem_id = uuid.uuid4()
        results = [
            DiagnosticResult(
                question_id="q1",
                question="test?",
                reference="answer",
                hypothesis="wrong",
                category="IE-user",
                judgment="incorrect",
                failure_mode=FailureMode.RETRIEVAL_MISS,
                evidence=RetrievalEvidence(
                    log_id=uuid.uuid4(),
                    query_text="test",
                    candidate_ids=[uuid.uuid4(), mem_id],
                    selected_ids=[uuid.uuid4()],
                    candidate_scores={},
                    l0_included=True,
                    latency_ms=25,
                ),
                supporting_memory=SupportingMemoryInfo(True, [mem_id], True, False),
                memories_used=3,
            )
        ]
        category_breakdown = compute_category_breakdown(results)
        summary = build_machine_readable_summary(results, category_breakdown)

        assert summary["results"][0]["supporting_memory"] is not None
        assert summary["results"][0]["supporting_memory"]["found"] is True
        assert summary["results"][0]["supporting_memory"]["in_candidates"] is True
        assert summary["results"][0]["supporting_memory"]["in_selected"] is False
        assert summary["results"][0]["evidence"]["candidate_count"] == 2


class TestBuildHumanReadableReport:
    def test_report_contains_summary(self) -> None:
        results = [
            DiagnosticResult(
                question_id="q1",
                question="What is my cat's name?",
                reference="Luna",
                hypothesis="Max",
                category="IE-user",
                judgment="incorrect",
                failure_mode=FailureMode.READER_FAILURE,
                evidence=RetrievalEvidence(
                    log_id=uuid.uuid4(),
                    query_text="What is my cat's name?",
                    candidate_ids=[uuid.uuid4() for _ in range(5)],
                    selected_ids=[uuid.uuid4() for _ in range(5)],
                    candidate_scores={},
                    l0_included=False,
                    latency_ms=20,
                ),
                supporting_memory=SupportingMemoryInfo(True, [uuid.uuid4()], True, True),
                memories_used=5,
                note="supporting memory was selected (1 match); LLM retrieved correct memory but produced wrong answer",
            )
        ]
        category_breakdown = compute_category_breakdown(results)
        summary = build_machine_readable_summary(results, category_breakdown)
        report = build_human_readable_report(results, category_breakdown, summary)

        assert "**Total Questions:** 1" in report
        assert FailureMode.READER_FAILURE in report
        assert "Wrong Answer Details" in report
        assert "q1" in report
        assert "LLM retrieved correct memory but produced wrong answer" in report

    def test_report_includes_supporting_memory_info(self) -> None:
        results = [
            DiagnosticResult(
                question_id="q1",
                question="test?",
                reference="answer",
                hypothesis="wrong",
                category="IE-user",
                judgment="incorrect",
                failure_mode=FailureMode.RETRIEVAL_MISS,
                evidence=None,
                supporting_memory=SupportingMemoryInfo(True, [uuid.uuid4()], False, False),
                memories_used=0,
            )
        ]
        category_breakdown = compute_category_breakdown(results)
        summary = build_machine_readable_summary(results, category_breakdown)
        report = build_human_readable_report(results, category_breakdown, summary)

        assert "Per-Category Breakdown" in report


class TestMissingLogFailureCases:
    def test_unknown_mode_when_supporting_in_candidates_but_no_log(self) -> None:
        result = {"judgment": "incorrect"}
        evidence = None
        supporting = SupportingMemoryInfo(True, [uuid.uuid4()], True, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.UNKNOWN
        assert "cannot determine" in note

    def test_extraction_miss_when_no_supporting_memory_and_no_log(self) -> None:
        result = {"judgment": "incorrect"}
        evidence = None
        supporting = SupportingMemoryInfo(False, [], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.EXTRACTION_MISS


class TestEvidenceBasedClassification:
    def test_full_pipeline_extraction_miss(self) -> None:
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test question",
            candidate_ids=[uuid.uuid4()],
            selected_ids=[],
            candidate_scores={},
            l0_included=False,
            latency_ms=10,
        )
        supporting = SupportingMemoryInfo(False, [], False, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.EXTRACTION_MISS
        assert "never extracted" in note

    def test_full_pipeline_retrieval_miss(self) -> None:
        mem_id = uuid.uuid4()
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test question",
            candidate_ids=[uuid.uuid4(), mem_id],
            selected_ids=[uuid.uuid4()],
            candidate_scores={},
            l0_included=False,
            latency_ms=15,
        )
        supporting = SupportingMemoryInfo(True, [mem_id], True, False)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.RETRIEVAL_MISS
        assert "not selected" in note

    def test_full_pipeline_reader_failure(self) -> None:
        mem_id = uuid.uuid4()
        result = {"judgment": "incorrect"}
        evidence = RetrievalEvidence(
            log_id=uuid.uuid4(),
            query_text="test question",
            candidate_ids=[uuid.uuid4(), mem_id],
            selected_ids=[mem_id, uuid.uuid4()],
            candidate_scores={},
            l0_included=True,
            latency_ms=20,
        )
        supporting = SupportingMemoryInfo(True, [mem_id], True, True)
        mode, note = classify_failure(result, evidence, supporting)
        assert mode == FailureMode.READER_FAILURE
        assert "correct memory" in note


class TestReportOutputFiles:
    def test_machine_readable_summary_is_valid_json(self, tmp_path: Path) -> None:
        results = [
            DiagnosticResult(
                question_id="q1",
                question="test?",
                reference="answer",
                hypothesis="wrong",
                category="IE-user",
                judgment="incorrect",
                failure_mode=FailureMode.EXTRACTION_MISS,
                evidence=None,
                supporting_memory=SupportingMemoryInfo(False, [], False, False),
                memories_used=0,
            )
        ]
        category_breakdown = compute_category_breakdown(results)
        summary = build_machine_readable_summary(results, category_breakdown)

        json_str = json.dumps(summary)
        parsed = json.loads(json_str)
        assert parsed["total_questions"] == 1
        assert parsed["failure_mode_counts"][FailureMode.EXTRACTION_MISS] == 1

    def test_human_readable_report_format(self, tmp_path: Path) -> None:
        results = [
            DiagnosticResult(
                question_id="q1",
                question="test question",
                reference="reference answer",
                hypothesis="hypothesis answer",
                category="IE-user",
                judgment="incorrect",
                failure_mode=FailureMode.RETRIEVAL_MISS,
                evidence=RetrievalEvidence(
                    log_id=uuid.uuid4(),
                    query_text="test question",
                    candidate_ids=[uuid.uuid4() for _ in range(8)],
                    selected_ids=[uuid.uuid4() for _ in range(3)],
                    candidate_scores={},
                    l0_included=True,
                    latency_ms=15,
                ),
                supporting_memory=SupportingMemoryInfo(True, [uuid.uuid4()], True, False),
                memories_used=3,
            )
        ]
        category_breakdown = compute_category_breakdown(results)
        summary = build_machine_readable_summary(results, category_breakdown)
        report = build_human_readable_report(results, category_breakdown, summary)

        lines = report.split("\n")
        assert any("Retrieval Diagnostics Report" in l for l in lines)  # noqa: E741
        assert any("Failure Mode Summary" in l for l in lines)  # noqa: E741
        assert any("Per-Category Breakdown" in l for l in lines)  # noqa: E741
        assert any("Wrong Answer Details" in l for l in lines)  # noqa: E741
        assert any("in_candidates=True" in l for l in lines)  # noqa: E741
        assert any("in_selected=False" in l for l in lines)  # noqa: E741


class TestFindSupportingMemories:
    """Regression tests for find_supporting_memories."""

    @pytest.mark.asyncio
    async def test_search_includes_dream_observations(self) -> None:
        """Verify that dream observations are included in supporting memory search.

        This is a regression test - diagnostics must be able to see dream
        observations when searching for supporting memory evidence, even though
        they are excluded from default factual retrieval.
        """
        import uuid
        from unittest.mock import MagicMock, patch

        user_id = uuid.uuid4()
        mock_store = MagicMock()
        mock_store.search_memories = AsyncMock(return_value=[])

        # Mock embed_query to avoid external dependency
        with patch("orchestrator.eval.diagnostics.embed_query") as mock_embed:
            mock_embed.return_value = [0.1] * 1024  # dummy embedding

            await find_supporting_memories(
                store=mock_store,
                reference="the reference fact",
                user_id=user_id,
            )

        # Verify include_dream_observations=True was passed
        mock_store.search_memories.assert_called_once()
        call_kwargs = mock_store.search_memories.call_args.kwargs
        assert call_kwargs.get("include_dream_observations") is True
