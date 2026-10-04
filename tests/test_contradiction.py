from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from orchestrator.memory.dedup import (
    # The supersession/contradiction tests retain dated benchmark behavior only;
    # production never auto-supersedes on similarity (test_memory_equivalence.py).
    AUTOMATIC_CONTRADICTION_MAX_TOKENS,
    CONTRADICTION_PROFILE,
    check_contradiction,
    _deduplicate_facts_benchmark,
    deduplicate_facts,
)
from orchestrator.memory.embedding import EmbeddingBatchResult, EmbeddingConfigurationError
from orchestrator.memory.extraction import ExtractedFact
from orchestrator.model_routing import routing_context


class ProfileRecorder:
    """Record the workload profile a helper binds, delegating to the real CM."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, profile: str, **kwargs: object):
        self.calls.append((profile, kwargs))
        return routing_context(profile, **kwargs)  # type: ignore[arg-type]

    @property
    def profiles(self) -> list[str]:
        return [profile for profile, _ in self.calls]


def _new_fact(content: str, slot: str | None = None) -> ExtractedFact:
    return ExtractedFact(content=content, category="fact", confidence=0.9, slot=slot)


def _embedding_result(vector: list[float]) -> EmbeddingBatchResult:
    return EmbeddingBatchResult(
        embeddings=[vector],
        provider="voyage",
        model="voyage-4-large",
        storage_model="voyage-4-large",
    )


class MockLitellmResponse:
    def __init__(self, content: str):
        self._content = content

    def model_dump(self):
        return {"choices": [{"message": {"content": self._content}}]}

    def dict(self):
        return self.model_dump()


@pytest.fixture(autouse=True)
def _mock_trust_signal():
    """Dedup tests do not exercise database-backed trust signaling."""
    with patch(
        "orchestrator.memory.trust_signals.apply_explicit_negative_signal",
        new_callable=AsyncMock,
        return_value=False,
    ):
        yield


@pytest.mark.asyncio
async def test_check_contradiction_yes() -> None:
    with patch("orchestrator.memory.dedup.guarded_completion") as mock:
        mock.return_value = MockLitellmResponse("YES. Fact B states the opposite of Fact A.")
        contradiction, explanation = await check_contradiction(
            "User drives a Tesla",
            "User does not drive a Tesla",
        )
        assert contradiction is True
        assert "opposite" in explanation.lower()


@pytest.mark.asyncio
async def test_check_contradiction_no() -> None:
    with patch("orchestrator.memory.dedup.guarded_completion") as mock:
        mock.return_value = MockLitellmResponse("NO. Both facts can be true simultaneously.")
        contradiction, explanation = await check_contradiction(
            "User drives a Tesla",
            "User owns a Tesla",
        )
        assert contradiction is False
        assert explanation == ""


@pytest.mark.asyncio
async def test_check_contradiction_llm_failure() -> None:
    with patch("orchestrator.memory.dedup.guarded_completion") as mock:
        mock.side_effect = Exception("LLM unavailable")
        contradiction, explanation = await check_contradiction(
            "User drives a Tesla",
            "User flies a plane",
        )
        assert contradiction is False
        assert explanation == ""


@pytest.mark.asyncio
async def test_dedup_supersession_with_contradiction() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.80,
            "content": "User drives a Tesla",
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    store.supersede_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User does not drive a Tesla",
        "memory_slot": "vehicle",
        "valid_to": None,
        "metadata": {"contradiction_detected": True},
    }

    with (
        patch(
            "orchestrator.memory.dedup.embed_documents_with_metadata",
            new_callable=AsyncMock,
        ) as embed,
        patch("orchestrator.memory.dedup.guarded_completion") as litellm_mock,
    ):
        embed.return_value = _embedding_result([0.01, 0.02])
        litellm_mock.return_value = MockLitellmResponse("YES. Fact B directly contradicts Fact A.")
        result = await _deduplicate_facts_benchmark(
            store,
            uuid.uuid4(),
            [_new_fact("User does not drive a Tesla", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    store.supersede_memory.assert_awaited_once()
    call_kwargs = store.supersede_memory.await_args.kwargs
    assert call_kwargs["metadata"] is not None
    assert call_kwargs["metadata"]["contradiction_detected"] is True
    assert "contradicts" in call_kwargs["metadata"]["contradiction_explanation"].lower()


@pytest.mark.asyncio
async def test_check_contradiction_binds_reasoning_profile() -> None:
    """Contradiction detection routes on the reasoning profile, pinning no model."""
    recorder = ProfileRecorder()

    with (
        patch("orchestrator.memory.dedup.routing_context", new=recorder),
        patch("orchestrator.memory.dedup.guarded_completion") as litellm_mock,
    ):
        litellm_mock.return_value = MockLitellmResponse("NO. The facts are consistent.")
        await check_contradiction("Fact A", "Fact B")

        litellm_mock.assert_awaited_once()
        assert litellm_mock.await_args is not None
        call_kwargs = litellm_mock.await_args.kwargs
        # No model hint at all, so the compute guard is free to select.
        assert "model" not in call_kwargs
        assert recorder.profiles == [CONTRADICTION_PROFILE]
        # No stale exclusion/preference is injected by the workload.
        assert recorder.calls[0][1] == {}


@pytest.mark.asyncio
async def test_check_contradiction_keeps_meaningful_output_bound() -> None:
    """The verdict bound is a real output contract, not a routing hint.

    The automatic call uses the approved helper output budget so a
    reasoning-preset verdict is never truncated into a false "NO"; the
    historic 50-token cap stays on the pinned benchmark call.
    """
    with patch("orchestrator.memory.dedup.guarded_completion") as litellm_mock:
        litellm_mock.return_value = MockLitellmResponse("NO. The facts are consistent.")
        await check_contradiction("Fact A", "Fact B")

        assert litellm_mock.await_args is not None
        call_kwargs = litellm_mock.await_args.kwargs
        assert call_kwargs["max_tokens"] == AUTOMATIC_CONTRADICTION_MAX_TOKENS
        assert call_kwargs["temperature"] == pytest.approx(0.1)
        # No benchmark seed leaks into a deployment call.
        assert "seed" not in call_kwargs


@pytest.mark.asyncio
async def test_check_contradiction_binds_profile_even_when_call_fails() -> None:
    """A provider failure is still advisory, and the profile was still bound."""
    recorder = ProfileRecorder()

    with (
        patch("orchestrator.memory.dedup.routing_context", new=recorder),
        patch("orchestrator.memory.dedup.guarded_completion") as litellm_mock,
    ):
        litellm_mock.side_effect = Exception("Approved inference route unavailable")
        contradiction, explanation = await check_contradiction("Fact A", "Fact B")

        litellm_mock.assert_awaited_once()
        assert contradiction is False
        assert explanation == ""
        assert recorder.profiles == [CONTRADICTION_PROFILE]


@pytest.mark.asyncio
async def test_dedup_supersession_retries_without_metadata_column() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.80,
            "content": "User drives a Tesla",
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    fallback_memory = {
        "id": uuid.uuid4(),
        "content": "User does not drive a Tesla",
        "memory_slot": "vehicle",
        "valid_to": None,
    }
    store.supersede_memory.side_effect = [
        asyncpg.UndefinedColumnError('column "metadata" does not exist'),
        fallback_memory,
    ]

    with (
        patch(
            "orchestrator.memory.dedup.embed_documents_with_metadata",
            new_callable=AsyncMock,
        ) as embed,
        patch("orchestrator.memory.dedup.guarded_completion") as litellm_mock,
    ):
        embed.return_value = _embedding_result([0.01, 0.02])
        litellm_mock.return_value = MockLitellmResponse("YES. Fact B directly contradicts Fact A.")
        result = await _deduplicate_facts_benchmark(
            store,
            uuid.uuid4(),
            [_new_fact("User does not drive a Tesla", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    assert result.superseded[0] == fallback_memory
    assert store.supersede_memory.await_count == 2
    first_call = store.supersede_memory.await_args_list[0].kwargs
    second_call = store.supersede_memory.await_args_list[1].kwargs
    assert first_call["metadata"] is not None
    assert second_call["metadata"] is None


@pytest.mark.asyncio
async def test_dedup_supersession_proceeds_on_llm_failure() -> None:
    store = AsyncMock()
    existing_id = uuid.uuid4()
    store.search_memories.return_value = [
        {
            "id": existing_id,
            "similarity": 0.80,
            "content": "User drives a Tesla",
            "memory_slot": "vehicle",
            "valid_to": None,
        }
    ]
    store.supersede_memory.return_value = {
        "id": uuid.uuid4(),
        "content": "User does not drive a Tesla",
        "memory_slot": "vehicle",
        "valid_to": None,
    }

    with (
        patch(
            "orchestrator.memory.dedup.embed_documents_with_metadata",
            new_callable=AsyncMock,
        ) as embed,
        patch("orchestrator.memory.dedup.guarded_completion") as litellm_mock,
    ):
        embed.return_value = _embedding_result([0.01, 0.02])
        litellm_mock.side_effect = Exception("LLM unavailable")
        result = await _deduplicate_facts_benchmark(
            store,
            uuid.uuid4(),
            [_new_fact("User does not drive a Tesla", "vehicle")],
            conversation_id=uuid.uuid4(),
        )

    assert len(result.superseded) == 1
    store.supersede_memory.assert_awaited_once()
    call_kwargs = store.supersede_memory.await_args.kwargs
    assert call_kwargs["metadata"] is None


@pytest.mark.asyncio
async def test_denied_embeddings_merge_exact_fact_on_cap_lock_connection() -> None:
    store = AsyncMock()
    user_id = uuid.uuid4()
    lock_conn = AsyncMock()
    existing = {
        "id": uuid.uuid4(),
        "content": "User plays guitar",
        "memory_slot": "hobby.current",
        "source_type": "extracted",
        "category": "fact",
        "status": "active",
        "valid_to": None,
    }
    store.search_memories_bm25.return_value = [existing]
    store._insert_memory_with_outcome.return_value = (existing, False)
    with patch(
        "orchestrator.memory.dedup.embed_documents_with_metadata",
        new=AsyncMock(side_effect=EmbeddingConfigurationError("route unavailable")),
    ):
        result = await deduplicate_facts(
            store,
            user_id,
            [_new_fact(existing["content"], existing["memory_slot"])],
            conversation_id=None,
            lock_conn=lock_conn,
        )

    assert result.merged == [existing]
    store.search_memories.assert_not_awaited()
    store.insert_memory.assert_not_awaited()
    store.search_memories_bm25.assert_not_awaited()
    assert store._insert_memory_with_outcome.await_args.kwargs["conn"] is lock_conn
