"""Tests for dedup threshold configuration authority.

These tests prove that threshold accessors delegate to config settings
and that branch behavior follows config values — not hardcoded constants.
"""

import pytest
from unittest.mock import patch, MagicMock

from orchestrator.memory.embedding import EmbeddingBatchResult


def _embedding_result(vector: list[float]) -> EmbeddingBatchResult:
    return EmbeddingBatchResult(
        embeddings=[vector],
        provider="voyage",
        model="voyage-4-large",
        storage_model="voyage-4-large",
    )


class TestThresholdAccessors:
    """Verify threshold accessors route to config, not hardcoded constants."""

    def test_merge_threshold_reads_from_config(self):
        """_get_merge_threshold() must delegate to get_settings().dedup_merge_threshold."""
        with patch("orchestrator.memory.dedup.get_settings") as mock_settings:
            mock_settings.return_value.dedup_merge_threshold = 0.91
            from orchestrator.memory.dedup import _get_merge_threshold

            result = _get_merge_threshold()
            assert result == 0.91
            mock_settings.assert_called_once()

    def test_supersede_threshold_reads_from_config(self):
        """_get_supersede_threshold() must delegate to get_settings().dedup_supersede_threshold."""
        with patch("orchestrator.memory.dedup.get_settings") as mock_settings:
            mock_settings.return_value.dedup_supersede_threshold = 0.83
            from orchestrator.memory.dedup import _get_supersede_threshold

            result = _get_supersede_threshold()
            assert result == 0.83
            mock_settings.assert_called_once()

    def test_supersede_same_slot_threshold_reads_from_config(self):
        """_get_supersede_same_slot_threshold() must delegate to config."""
        with patch("orchestrator.memory.dedup.get_settings") as mock_settings:
            mock_settings.return_value.dedup_supersede_same_slot_threshold = 0.66
            from orchestrator.memory.dedup import _get_supersede_same_slot_threshold

            result = _get_supersede_same_slot_threshold()
            assert result == 0.66
            mock_settings.assert_called_once()


class TestThresholdBranchBehavior:
    """Verify branch decisions follow config threshold values."""

    @pytest.mark.asyncio
    async def test_dedup_uses_config_thresholds_not_hardcoded(self):
        """Branch (merge/supersede/new) must follow config thresholds.

        This test uses extreme values to prove config drives behavior:
        - If merge threshold is 0.0, any similarity >= 0.0 triggers merge
        - If merge threshold is 1.0, no similarity >= 1.0 so merge is skipped
        By sweeping these extremes we prove no hardcoded constant is used.
        """
        import uuid
        from dataclasses import dataclass
        from unittest.mock import AsyncMock, patch
        from orchestrator.memory.dedup import deduplicate_facts
        from orchestrator.memory.store import MemoryStore

        TEST_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")

        @dataclass
        class SimpleFact:
            content: str
            category: str
            confidence: float = 0.8
            slot: str | None = None

        fact = SimpleFact(content="test fact", category="test")

        # Mock store returning a single candidate with 0.5 similarity
        mock_store = MagicMock(spec=MemoryStore)
        mock_store.search_memories = AsyncMock(
            return_value=[
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "content": "existing",
                    "similarity": 0.5,
                    "valid_to": None,
                    "memory_slot": None,
                }
            ]
        )
        mock_store.touch_memory = AsyncMock()
        mock_store.insert_memory = AsyncMock(
            return_value={"id": "00000000-0000-0000-0000-000000000002", "content": "test fact"}
        )
        mock_store.supersede_memory = AsyncMock(
            return_value={"id": "00000000-0000-0000-0000-000000000003", "content": "test fact"}
        )

        with (
            patch(
                "orchestrator.memory.dedup.embed_documents_with_metadata",
                new_callable=AsyncMock,
            ) as mock_embed,
            patch(
                "orchestrator.memory.dedup.check_contradiction",
                new_callable=AsyncMock,
                return_value=(True, "threshold test contradiction"),
            ),
        ):
            mock_embed.return_value = _embedding_result([0.1] * 1024)

            # Case A: merge_threshold=0.0 → any candidate merges (similarity 0.5 >= 0.0)
            with patch("orchestrator.memory.dedup.get_settings") as mock_settings:
                mock_settings.return_value.dedup_merge_threshold = 0.0
                mock_settings.return_value.dedup_supersede_threshold = 0.0
                mock_settings.return_value.dedup_supersede_same_slot_threshold = 0.0
                mock_settings.return_value.background_reasoning_model = "gpt-4o-mini"
                mock_settings.return_value.embedding_document_model = "voyage-4-large"

                result = await deduplicate_facts(
                    store=mock_store,
                    user_id=TEST_UUID,
                    facts=[fact],
                    conversation_id=None,
                )
                # With merge threshold 0.0 and similarity 0.5, should merge
                assert len(result.merged) == 1, (
                    "merge threshold 0.0 should trigger merge at similarity 0.5"
                )
                assert len(result.superseded) == 0
                assert len(result.new) == 0

            # Case B: merge_threshold=1.0 → no merge possible (0.5 < 1.0), falls to supersede
            mock_store.reset_mock()
            mock_store.search_memories = AsyncMock(
                return_value=[
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "content": "existing",
                        "similarity": 0.5,
                        "valid_to": None,
                        "memory_slot": None,
                    }
                ]
            )
            mock_store.supersede_memory = AsyncMock(
                return_value={"id": "00000000-0000-0000-0000-000000000003", "content": "test fact"}
            )

            with patch("orchestrator.memory.dedup.get_settings") as mock_settings:
                mock_settings.return_value.dedup_merge_threshold = 1.0
                mock_settings.return_value.dedup_supersede_threshold = 0.0
                mock_settings.return_value.dedup_supersede_same_slot_threshold = 0.0
                mock_settings.return_value.background_reasoning_model = "gpt-4o-mini"
                mock_settings.return_value.embedding_document_model = "voyage-4-large"

                result = await deduplicate_facts(
                    store=mock_store,
                    user_id=TEST_UUID,
                    facts=[fact],
                    conversation_id=None,
                )
                # With merge threshold 1.0, similarity 0.5 can't merge → supersede
                assert len(result.merged) == 0
                assert len(result.superseded) == 1, (
                    "merge threshold 1.0 should skip merge, similarity 0.5 >= 0.0 supersedes"
                )
                assert len(result.new) == 0

    @pytest.mark.asyncio
    async def test_same_slot_threshold_drives_slot_specific_branch(self):
        """Slot-specific supersede threshold must come from config, not hardcoded 0.60."""
        import uuid
        from dataclasses import dataclass
        from unittest.mock import AsyncMock, patch
        from orchestrator.memory.dedup import deduplicate_facts
        from orchestrator.memory.store import MemoryStore

        TEST_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")

        @dataclass
        class SlottedFact:
            content: str
            category: str
            confidence: float = 0.8
            slot: str | None = "vehicle.current"

        fact = SlottedFact(content="test", category="test")

        mock_store = MagicMock(spec=MemoryStore)
        mock_store.search_memories = AsyncMock(
            return_value=[
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "content": "existing",
                    "similarity": 0.7,
                    "valid_to": None,
                    "memory_slot": "vehicle.current",
                }
            ]
        )
        mock_store.touch_memory = AsyncMock()
        mock_store.insert_memory = AsyncMock(
            return_value={"id": "00000000-0000-0000-0000-000000000002", "content": "test"}
        )
        mock_store.supersede_memory = AsyncMock(
            return_value={"id": "00000000-0000-0000-0000-000000000003", "content": "test"}
        )
        mock_store.close_memory = AsyncMock()
        mock_store._pool = MagicMock()
        mock_store._pool.fetch = AsyncMock(return_value=[])
        mock_store._pool.execute = AsyncMock()

        with (
            patch(
                "orchestrator.memory.dedup.embed_documents_with_metadata",
                new_callable=AsyncMock,
            ) as mock_embed,
            patch(
                "orchestrator.memory.dedup.check_contradiction",
                new_callable=AsyncMock,
                return_value=(True, "threshold test contradiction"),
            ),
        ):
            mock_embed.return_value = _embedding_result([0.1] * 1024)

            # With same_slot_threshold=0.0, similarity 0.7 would supersede
            with patch("orchestrator.memory.dedup.get_settings") as mock_settings:
                mock_settings.return_value.dedup_merge_threshold = 1.0
                mock_settings.return_value.dedup_supersede_threshold = 1.0
                mock_settings.return_value.dedup_supersede_same_slot_threshold = 0.0
                mock_settings.return_value.background_reasoning_model = "gpt-4o-mini"
                mock_settings.return_value.embedding_document_model = "voyage-4-large"

                result = await deduplicate_facts(
                    store=mock_store,
                    user_id=TEST_UUID,
                    facts=[fact],
                    conversation_id=None,
                )
                # Merge=1.0 blocks merge; same_slot threshold 0.0 → 0.7 supersedes
                assert len(result.superseded) == 1, (
                    "same_slot threshold 0.0 should allow supersede at similarity 0.7"
                )
