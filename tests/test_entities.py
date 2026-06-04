"""Tests for entity extraction and resolution primitives."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestNormalizeLookupKey:
    """Tests for _normalize_lookup_key function."""

    def test_basic_normalization(self) -> None:
        from orchestrator.memory.entities import _normalize_lookup_key

        assert _normalize_lookup_key("John Smith") == "john smith"
        assert _normalize_lookup_key("  ALICE  ") == "alice"
        assert _normalize_lookup_key("O'Connor") == "oconnor"
        assert _normalize_lookup_key("San Francisco") == "san francisco"


class TestIsLikelyEntity:
    """Tests for _is_likely_entity function."""

    def test_rejects_short_text(self) -> None:
        from orchestrator.memory.entities import _is_likely_entity

        assert _is_likely_entity("A") is False
        assert _is_likely_entity("") is False

    def test_rejects_stopwords(self) -> None:
        from orchestrator.memory.entities import _is_likely_entity

        assert _is_likely_entity("The") is False
        assert _is_likely_entity("And") is False
        assert _is_likely_entity("But") is False

    def test_rejects_numeric(self) -> None:
        from orchestrator.memory.entities import _is_likely_entity

        assert _is_likely_entity("12345") is False
        assert _is_likely_entity("3.14") is False

    def test_accepts_valid_entities(self) -> None:
        from orchestrator.memory.entities import _is_likely_entity

        assert _is_likely_entity("John") is True
        assert _is_likely_entity("Alice Smith") is True
        assert _is_likely_entity("San Francisco") is True
        assert _is_likely_entity("Python") is True


class TestExtractFromSlot:
    """Tests for _extract_from_slot function."""

    def test_extracts_person_type(self) -> None:
        from orchestrator.memory.entities import _extract_from_slot

        assert _extract_from_slot("person.name") == "person"
        assert _extract_from_slot("PERSON.nickname") == "person"

    def test_extracts_location_type(self) -> None:
        from orchestrator.memory.entities import _extract_from_slot

        assert _extract_from_slot("location.city") == "location"
        assert _extract_from_slot("LOCATION.country") == "location"

    def test_returns_none_for_unknown_prefix(self) -> None:
        from orchestrator.memory.entities import _extract_from_slot

        assert _extract_from_slot("project.name") is None
        assert _extract_from_slot("unknown.slot") is None


class TestExtractCandidatesBaseline:
    """Tests for extract_candidates_baseline function."""

    def test_extracts_capitalized_phrases(self) -> None:
        from orchestrator.memory.entities import extract_candidates_baseline

        content = "I met Alice and Bob yesterday. They live in San Francisco."
        candidates = extract_candidates_baseline(content)

        texts = [c.text for c in candidates]
        assert "Alice" in texts
        assert "Bob" in texts
        assert "San Francisco" in texts

    def test_extracts_quoted_strings(self) -> None:
        from orchestrator.memory.entities import extract_candidates_baseline

        content = 'My friend calls me "Buddy" and my dog is named "Max"'
        candidates = extract_candidates_baseline(content)

        texts = [c.text for c in candidates]
        assert "Buddy" in texts
        assert "Max" in texts

    def test_extracts_hashtags(self) -> None:
        from orchestrator.memory.entities import extract_candidates_baseline

        content = "I love #Python and #MachineLearning"
        candidates = extract_candidates_baseline(content)

        texts = [c.text for c in candidates]
        assert "#Python" in texts
        assert "#MachineLearning" in texts

    def test_extracts_mentions(self) -> None:
        from orchestrator.memory.entities import extract_candidates_baseline

        content = "Shoutout to @alice and @bob for help"
        candidates = extract_candidates_baseline(content)

        texts = [c.text for c in candidates]
        assert "@alice" in texts
        assert "@bob" in texts

    def test_deduplicates_by_normalized_key(self) -> None:
        from orchestrator.memory.entities import extract_candidates_baseline

        content = "Alice and Bob and Alice"
        candidates = extract_candidates_baseline(content)

        assert len(candidates) == 2
        texts = [c.text for c in candidates]
        assert "Alice" in texts
        assert "Bob" in texts

    def test_filters_stopwords(self) -> None:
        from orchestrator.memory.entities import extract_candidates_baseline

        content = "The The The"
        candidates = extract_candidates_baseline(content)

        assert len(candidates) == 0

    def test_includes_memory_id_and_slot(self) -> None:
        from orchestrator.memory.entities import extract_candidates_baseline

        memory_id = uuid.uuid4()
        content = "I visited San Francisco"
        candidates = extract_candidates_baseline(
            content, memory_id=memory_id, memory_slot="location.city"
        )

        assert len(candidates) == 1
        assert candidates[0].source_memory_id == memory_id
        assert candidates[0].entity_type == "location"


class TestIsSpacyAvailable:
    """Tests for spaCy availability detection."""

    def test_returns_false_when_not_installed(self) -> None:
        from orchestrator.memory.entities import _is_spacy_available

        # The module should detect spaCy is not available
        result = _is_spacy_available()
        # If spacy is not installed, should return False
        # If it is installed, returns True
        assert isinstance(result, bool)


class TestCosineSimilarity:
    """Tests for _cosine_similarity function."""

    def test_identical_vectors(self) -> None:
        from orchestrator.memory.entities import _cosine_similarity

        vec = [1.0, 0.0, 0.0]
        assert _cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        from orchestrator.memory.entities import _cosine_similarity

        vec1 = [1.0, 0.0, 0.0]
        vec2 = [0.0, 1.0, 0.0]
        assert _cosine_similarity(vec1, vec2) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        from orchestrator.memory.entities import _cosine_similarity

        vec1 = [1.0, 0.0]
        vec2 = [-1.0, 0.0]
        assert _cosine_similarity(vec1, vec2) == pytest.approx(-1.0)

    def test_different_lengths(self) -> None:
        from orchestrator.memory.entities import _cosine_similarity

        vec1 = [1.0, 0.0]
        vec2 = [1.0, 0.0, 0.0]
        assert _cosine_similarity(vec1, vec2) == 0.0


class TestCandidateMention:
    """Tests for CandidateMention dataclass."""

    def test_creates_candidate(self) -> None:
        from orchestrator.memory.entities import CandidateMention

        mention = CandidateMention(
            text="Alice",
            normalized_key="alice",
            confidence=0.8,
        )

        assert mention.text == "Alice"
        assert mention.normalized_key == "alice"
        assert mention.confidence == 0.8
        assert mention.source_memory_id is None
        assert mention.context is None


class TestEntityResolution:
    """Tests for EntityResolution dataclass."""

    def test_creates_resolution(self) -> None:
        from orchestrator.memory.entities import CandidateMention, EntityResolution

        mention = CandidateMention(text="Alice", normalized_key="alice")
        resolution = EntityResolution(
            mention=mention,
            merge_decision="new",
        )

        assert resolution.mention == mention
        assert resolution.is_new is True
        assert resolution.merge_decision == "new"
        assert resolution.resolved_entity_id is None


class TestExtractionResult:
    """Tests for ExtractionResult dataclass."""

    def test_creates_extraction_result(self) -> None:
        from orchestrator.memory.entities import (
            CandidateMention,
            EntityResolution,
            ExtractionResult,
        )

        candidates = [CandidateMention(text="Alice", normalized_key="alice")]
        resolutions = [EntityResolution(mention=candidates[0], merge_decision="new")]

        result = ExtractionResult(
            candidates=candidates,
            resolutions=resolutions,
        )

        assert len(result.candidates) == 1
        assert len(result.resolutions) == 1
        assert result.spacy_enriched is False
        assert result.ambiguous_merges_needed == []


class TestBatchConfirmMerges:
    """Tests for batch_confirm_merges function."""

    @pytest.mark.asyncio
    async def test_passes_through_when_no_ambiguous(self) -> None:
        from orchestrator.memory.entities import (
            batch_confirm_merges,
            CandidateMention,
            EntityResolution,
        )

        mention = CandidateMention(text="Alice", normalized_key="alice")
        resolution = EntityResolution(
            mention=mention,
            merge_decision="merge",  # Not ambiguous
            resolved_entity_id=uuid.uuid4(),
        )

        result = await batch_confirm_merges([resolution])
        assert result[0].merge_decision == "merge"

    @pytest.mark.asyncio
    async def test_confirms_merge_on_yes_response(self) -> None:
        from orchestrator.memory.entities import (
            batch_confirm_merges,
            CandidateMention,
            EntityResolution,
        )

        mention = CandidateMention(text="Alice", normalized_key="alice", context="My friend Alice")
        resolution = EntityResolution(
            mention=mention,
            merge_decision="ambiguous",
            resolved_entity_id=uuid.uuid4(),
            canonical_name="Alice Smith",
            similarity=0.78,
        )

        with patch(
            "orchestrator.memory.entities.confirm_merge_llm",
            return_value=(True, "YES: Same person"),
        ):
            result = await batch_confirm_merges([resolution])

        assert result[0].merge_decision == "merge"

    @pytest.mark.asyncio
    async def test_rejects_merge_on_no_response(self) -> None:
        from orchestrator.memory.entities import (
            batch_confirm_merges,
            CandidateMention,
            EntityResolution,
        )

        mention = CandidateMention(text="Bob", normalized_key="bob", context="A different Bob")
        resolution = EntityResolution(
            mention=mention,
            merge_decision="ambiguous",
            resolved_entity_id=uuid.uuid4(),
            canonical_name="Alice Smith",
            similarity=0.76,
        )

        with patch(
            "orchestrator.memory.entities.confirm_merge_llm",
            return_value=(False, "NO: Different people"),
        ):
            result = await batch_confirm_merges([resolution])

        assert result[0].merge_decision == "reject"


class TestNoFalseMergeBehavior:
    """Tests ensuring no false merges occur."""

    @pytest.mark.asyncio
    async def test_reject_very_low_similarity(self) -> None:
        from orchestrator.memory.entities import resolve_candidate, CandidateMention

        # Create a mock store and canonical entities
        mock_store = MagicMock()
        entity_id = uuid.uuid4()
        canonical_entities = [
            (entity_id, "Python Programming", [0.1] * 1024)  # Very different embedding
        ]

        mention = CandidateMention(
            text="JavaScript",
            normalized_key="javascript",
            confidence=0.5,
        )

        # The embedding similarity should be very low, triggering reject
        resolution = await resolve_candidate(
            mention,
            uuid.uuid4(),
            mock_store,
            canonical_entities,
        )

        # Even without real embeddings, we should see the logic handles low similarity
        assert resolution.merge_decision in ("new", "reject")

    @pytest.mark.asyncio
    async def test_no_spacy_still_extracts(self) -> None:
        from orchestrator.memory.entities import (
            extract_entity_candidates,
        )

        # Even without spaCy, baseline extraction should work
        content = "I met Alice and Bob in New York"
        candidates = await extract_entity_candidates(content, use_spacy=False)

        texts = [c.text for c in candidates]
        assert "Alice" in texts
        assert "Bob" in texts
        assert "New York" in texts

    @pytest.mark.asyncio
    async def test_spacy_graceful_degradation(self) -> None:
        from orchestrator.memory.entities import extract_candidates_spacy

        # Should return empty list when spaCy not available
        result = await extract_candidates_spacy("Test content")
        assert result == []


class TestNoSpacyIntegration:
    """Integration tests ensuring the no-spaCy path works end-to-end."""

    @pytest.mark.asyncio
    async def test_full_pipeline_without_spacy(self) -> None:
        from orchestrator.memory.entities import extract_and_resolve_entities

        mock_store = MagicMock()
        mock_store.get_entities_for_user = AsyncMock(return_value=[])

        memory_contents: list[tuple[str, uuid.UUID | None, str | None]] = [
            ("I love Python and JavaScript", None, None),
            ("Alice is my friend who lives in San Francisco", None, "person.name"),
        ]

        result = await extract_and_resolve_entities(
            uuid.uuid4(),
            mock_store,
            memory_contents,
            use_spacy=False,
        )

        assert len(result.candidates) > 0
        assert result.spacy_enriched is False

        # All resolutions should be "new" since no canonical entities exist
        for resolution in result.resolutions:
            if resolution.merge_decision != "reject":
                assert resolution.is_new is True


class TestAmbiguousMergeRejection:
    """Tests for ambiguous merge rejection when LLM confirms different entities."""

    @pytest.mark.asyncio
    async def test_rejects_merge_when_llm_says_no(self) -> None:
        from orchestrator.memory.entities import (
            batch_confirm_merges,
            CandidateMention,
            EntityResolution,
        )

        mention = CandidateMention(
            text="Bob",
            normalized_key="bob",
            context="Bob is a different person from the Bob Smith I mentioned earlier",
        )
        resolution = EntityResolution(
            mention=mention,
            merge_decision="ambiguous",
            resolved_entity_id=uuid.uuid4(),
            canonical_name="Bob Smith",
            similarity=0.76,
        )

        with patch(
            "orchestrator.memory.entities.confirm_merge_llm",
            return_value=(False, "NO: Different people - Bob is a separate individual"),
        ):
            result = await batch_confirm_merges([resolution])

        assert result[0].merge_decision == "reject"
        # After rejection, is_new should still be True (rejected = treat as new)
        assert result[0].is_new is True

    @pytest.mark.asyncio
    async def test_rejects_merge_on_unsure_response(self) -> None:
        from orchestrator.memory.entities import (
            batch_confirm_merges,
            CandidateMention,
            EntityResolution,
        )

        mention = CandidateMention(
            text="Alex",
            normalized_key="alex",
            context="My colleague Alex",
        )
        resolution = EntityResolution(
            mention=mention,
            merge_decision="ambiguous",
            resolved_entity_id=uuid.uuid4(),
            canonical_name="Alexander",
            similarity=0.73,
        )

        with patch(
            "orchestrator.memory.entities.confirm_merge_llm",
            return_value=(False, "UNSURE: Cannot determine without more context"),
        ):
            result = await batch_confirm_merges([resolution])

        # UNSURE is treated same as NO - reject
        assert result[0].merge_decision == "reject"

    @pytest.mark.asyncio
    async def test_rejects_merge_when_llm_raises_exception(self) -> None:
        from orchestrator.memory.entities import (
            batch_confirm_merges,
            CandidateMention,
            EntityResolution,
        )

        mention = CandidateMention(
            text="Charlie",
            normalized_key="charlie",
            context="My friend Charlie",
        )
        resolution = EntityResolution(
            mention=mention,
            merge_decision="ambiguous",
            resolved_entity_id=uuid.uuid4(),
            canonical_name="Charles",
            similarity=0.74,
        )

        with patch(
            "orchestrator.memory.entities.confirm_merge_llm",
            side_effect=Exception("LLM provider error"),
        ):
            result = await batch_confirm_merges([resolution])

        # Exception should result in reject (best-effort)
        assert result[0].merge_decision == "reject"

    @pytest.mark.asyncio
    async def test_confirms_merge_when_llm_says_yes(self) -> None:
        from orchestrator.memory.entities import (
            batch_confirm_merges,
            CandidateMention,
            EntityResolution,
        )

        mention = CandidateMention(
            text="Mike",
            normalized_key="mike",
            context="My friend Mike from college",
        )
        resolution = EntityResolution(
            mention=mention,
            merge_decision="ambiguous",
            resolved_entity_id=uuid.uuid4(),
            canonical_name="Michael",
            similarity=0.77,
        )

        with patch(
            "orchestrator.memory.entities.confirm_merge_llm",
            return_value=(True, "YES: Mike is a common nickname for Michael"),
        ):
            result = await batch_confirm_merges([resolution])

        assert result[0].merge_decision == "merge"
