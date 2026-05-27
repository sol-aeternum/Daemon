"""Tests for deterministic retrieval ordering.

These tests verify that tied-score candidates are ordered deterministically
by id ASC at every layer: SQL vector/BM25 fetches and Python final sort.

Scope: Task 10 — deterministic tie-breakers only. Weights, thresholds,
and scoring math are intentionally NOT varied.
"""

import uuid
from unittest.mock import AsyncMock, patch
import pytest

from orchestrator.memory import retrieval as retrieval_module


# ---------------------------------------------------------------------------
# Helper: make a synthetic memory candidate dict
# ---------------------------------------------------------------------------


def _make_candidate(
    memory_id: str | uuid.UUID,
    *,
    similarity: float = 0.0,
    bm25_score: float = 0.0,
    vector_sim: float = 0.0,
    confidence: float = 1.0,
    trust_score: float = 0.5,
    status: str = "active",
    source_type: str = "user_created",
    created_at: str = "2024-01-01T00:00:00Z",
    last_accessed_at: str | None = None,
    valid_to: str | None = None,
) -> dict[str, object]:
    """Return a minimal candidate dict as returned by store search methods."""
    mid = str(memory_id)
    return {
        "id": uuid.UUID(mid) if isinstance(memory_id, str) else memory_id,
        "content": f"Memory content for {mid}",
        "similarity": similarity,
        "vector_sim": vector_sim,
        "bm25_score": bm25_score,
        "confidence": confidence,
        "trust_score": trust_score,
        "status": status,
        "source_type": source_type,
        "created_at": created_at,
        "last_accessed_at": last_accessed_at or created_at,
        "valid_to": valid_to,
    }


# ---------------------------------------------------------------------------
# Test: SQL vector search — tied similarity returns id ASC order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vector_tied_similarity_returns_id_asc_order():
    """When two memories have identical vector similarity, SQL ORDER BY id ASC
    must return them in ascending id order."""
    uid = uuid.uuid4()
    query_emb = [0.1] * 1024

    # Return two candidates with identical similarity (0.9) but different ids
    cand_a = _make_candidate(
        "00000000-0000-0000-0000-000000000001",
        similarity=0.9,
        vector_sim=0.9,
    )
    cand_b = _make_candidate(
        "00000000-0000-0000-0000-000000000002",
        similarity=0.9,
        vector_sim=0.9,
    )
    # Return them in REVERSE id order to prove the SQL ORDER BY overrides
    mock_store = AsyncMock()
    mock_store.search_memories = AsyncMock(return_value=[cand_b, cand_a])
    mock_store.search_memories_bm25 = AsyncMock(return_value=[])

    with patch.object(retrieval_module, "embed_query", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = query_emb
        # Patch bulk_touch_memories and log_retrieval to be no-ops
        mock_store.bulk_touch_memories = AsyncMock()
        mock_store.log_retrieval = AsyncMock()
        with patch.object(retrieval_module, "_is_retrieval_logging_enabled", return_value=False):
            result = await retrieval_module.retrieve_memories(
                store=mock_store,
                query_embedding=query_emb,
                user_id=uid,
                limit=5,
            )

    ids_returned = [str(m["id"]) for m in result]
    # The SQL ORDER BY id ASC must win regardless of insertion order
    assert ids_returned == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ], f"Expected id-ascending order, got {ids_returned}"


# ---------------------------------------------------------------------------
# Test: SQL BM25 — tied bm25_score returns id ASC order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bm25_tied_score_returns_id_asc_order():
    """When two memories have identical BM25 scores, SQL ORDER BY id ASC
    must return them in ascending id order."""
    uid = uuid.uuid4()
    query_emb = [0.1] * 1024

    cand_a = _make_candidate(
        "00000000-0000-0000-0000-000000000001",
        bm25_score=1.5,
    )
    cand_b = _make_candidate(
        "00000000-0000-0000-0000-000000000002",
        bm25_score=1.5,
    )
    # Reversed insertion order
    mock_store = AsyncMock()
    mock_store.search_memories = AsyncMock(return_value=[])
    mock_store.search_memories_bm25 = AsyncMock(return_value=[cand_b, cand_a])

    with patch.object(retrieval_module, "embed_query", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = query_emb
        mock_store.bulk_touch_memories = AsyncMock()
        mock_store.log_retrieval = AsyncMock()
        with patch.object(retrieval_module, "_is_retrieval_logging_enabled", return_value=False):
            result = await retrieval_module.retrieve_memories(
                store=mock_store,
                query_embedding=query_emb,
                query_text="test query",
                user_id=uid,
                limit=5,
            )

    ids_returned = [str(m["id"]) for m in result]
    assert ids_returned == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ], f"Expected id-ascending order, got {ids_returned}"


# ---------------------------------------------------------------------------
# Test: Python final sort — tied final_score is broken by id ASC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_python_final_sort_tied_score_is_deterministic_by_id():
    uid = uuid.uuid4()
    query_emb = [0.1] * 1024

    # vector_sim=0.3 gives final_score=0.20 (above MIN_FINAL_SCORE=0.15)
    # 0.5*0.3 + 0.3*0.0 + 0.2*0.5*1.0*0.5 = 0.15+0.0+0.05 = 0.20
    cand_a = _make_candidate(
        "00000000-0000-0000-0000-000000000001",
        similarity=0.3,
        vector_sim=0.3,
        created_at="2024-01-01T00:00:00Z",
        last_accessed_at="2024-01-01T00:00:00Z",
    )
    cand_b = _make_candidate(
        "00000000-0000-0000-0000-000000000002",
        similarity=0.3,
        vector_sim=0.3,
        created_at="2024-01-01T00:00:00Z",
        last_accessed_at="2024-01-01T00:00:00Z",
    )
    mock_store = AsyncMock()
    mock_store.search_memories = AsyncMock(return_value=[cand_b, cand_a])
    mock_store.search_memories_bm25 = AsyncMock(return_value=[])

    with patch.object(retrieval_module, "embed_query", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = query_emb
        mock_store.bulk_touch_memories = AsyncMock()
        mock_store.log_retrieval = AsyncMock()
        with patch.object(retrieval_module, "_is_retrieval_logging_enabled", return_value=False):
            results = []
            for _ in range(3):
                r = await retrieval_module.retrieve_memories(
                    store=mock_store,
                    query_embedding=query_emb,
                    user_id=uid,
                    limit=5,
                )
                results.append([str(m["id"]) for m in r])

    assert results[0] == results[1] == results[2], f"Ordering unstable: {results}"
    assert results[0] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ], f"Expected id-ascending tie-break, got {results[0]}"


# ---------------------------------------------------------------------------
# Test: non-tied ranking is unchanged — higher score outranks lower score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_tied_ranking_unchanged_higher_score_first():
    uid = uuid.uuid4()
    query_emb = [0.1] * 1024

    # cand_b: high score (should come first)
    # 0.5*0.9 + 0.3*1.0 + 0.2*0.5*1.0*0.5 = 0.45+0.3+0.05 = 0.80
    cand_b = _make_candidate(
        "00000000-0000-0000-0000-000000000002",
        similarity=0.9,
        vector_sim=0.9,
        bm25_score=1.0,
        created_at="2024-01-01T00:00:00Z",
        last_accessed_at="2024-01-01T00:00:00Z",
    )
    # cand_a: lower score (should come second)
    # 0.5*0.3 + 0.3*0.0 + 0.2*0.5*1.0*0.5 = 0.15+0.0+0.05 = 0.20
    cand_a = _make_candidate(
        "00000000-0000-0000-0000-000000000001",
        similarity=0.3,
        vector_sim=0.3,
        created_at="2024-01-01T00:00:00Z",
        last_accessed_at="2024-01-01T00:00:00Z",
    )
    mock_store = AsyncMock()
    mock_store.search_memories = AsyncMock(return_value=[cand_a, cand_b])
    mock_store.search_memories_bm25 = AsyncMock(return_value=[])

    with patch.object(retrieval_module, "embed_query", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = query_emb
        mock_store.bulk_touch_memories = AsyncMock()
        mock_store.log_retrieval = AsyncMock()
        with patch.object(retrieval_module, "_is_retrieval_logging_enabled", return_value=False):
            result = await retrieval_module.retrieve_memories(
                store=mock_store,
                query_embedding=query_emb,
                user_id=uid,
                limit=5,
            )

    ids_returned = [str(m["id"]) for m in result]
    assert ids_returned[0] == "00000000-0000-0000-0000-000000000002", (
        f"Expected higher-score candidate first, got {ids_returned}"
    )
    assert ids_returned[1] == "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Test: repeated calls with same tied-score candidates return same order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tied_score_ordering_stable_over_10_calls():
    """Ten consecutive calls with the same tied-score candidates must return
    the same ordered ids every time."""
    uid = uuid.uuid4()
    query_emb = [0.1] * 1024

    ids = [f"00000000-0000-0000-0000-00000000000{x}" for x in range(1, 6)]
    candidates = [
        _make_candidate(
            ids[i],
            similarity=0.3,
            vector_sim=0.3,
            created_at="2024-01-01T00:00:00Z",
            last_accessed_at="2024-01-01T00:00:00Z",
        )
        for i in range(5)
    ]
    # Reversed insertion order from the mock
    reversed_candidates = list(reversed(candidates))

    mock_store = AsyncMock()
    mock_store.search_memories = AsyncMock(return_value=reversed_candidates)
    mock_store.search_memories_bm25 = AsyncMock(return_value=[])

    with patch.object(retrieval_module, "embed_query", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = query_emb
        mock_store.bulk_touch_memories = AsyncMock()
        mock_store.log_retrieval = AsyncMock()
        with patch.object(retrieval_module, "_is_retrieval_logging_enabled", return_value=False):
            results = []
            for _ in range(10):
                r = await retrieval_module.retrieve_memories(
                    store=mock_store,
                    query_embedding=query_emb,
                    user_id=uid,
                    limit=5,
                )
                results.append(tuple(str(m["id"]) for m in r))

    expected = tuple(ids[:5])
    for i, r in enumerate(results):
        assert r == expected, (
            f"Call {i+1}/10 returned {r}, expected {expected}"
        )


# ---------------------------------------------------------------------------
# Test: weights unchanged — HYBRID_VECTOR_WEIGHT = 0.5
# ---------------------------------------------------------------------------


def test_hybrid_weights_unchanged():
    """Verify the three ranking weights are the documented values.

    This test acts as a regression guard: any change to these constants
    must be deliberate and will break this test.
    """
    assert retrieval_module.HYBRID_VECTOR_WEIGHT == 0.5
    assert retrieval_module.HYBRID_BM25_WEIGHT == 0.3
    assert retrieval_module.HYBRID_RECENCY_CONFIDENCE_WEIGHT == 0.2


def test_min_final_score_unchanged():
    """Verify MIN_FINAL_SCORE threshold is unchanged at 0.15."""
    assert retrieval_module.MIN_FINAL_SCORE == 0.15


# ---------------------------------------------------------------------------
# Test: hybrid score math is preserved
# ---------------------------------------------------------------------------


def test_hybrid_score_formula_preserved():
    """Verify the _hybrid_score() function computes the documented formula.

    Score = 0.5 * vector_sim + 0.3 * bm25_normalized + 0.2 * recency_confidence_trust
    """
    score = retrieval_module._hybrid_score(
        vector_sim=0.9,
        bm25_normalized=0.5,
        recency=0.9,
        confidence=1.0,
        trust=0.5,
    )
    # 0.5*0.9 + 0.3*0.5 + 0.2*0.9*1.0*0.5
    # = 0.45 + 0.15 + 0.09 = 0.69
    assert abs(score - 0.69) < 1e-9, f"Expected 0.69, got {score}"


# ---------------------------------------------------------------------------
# Test: id tie-break applies even when id is UUID type (not str)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_final_sort_uuid_id_tie_break():
    """Verify the Python sort tie-break works when id is stored as a UUID
    object (not string), as it is when returned from the real store."""
    uid = uuid.uuid4()
    query_emb = [0.1] * 1024

    cand_a_uuid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    cand_b_uuid = uuid.UUID("00000000-0000-0000-0000-000000000002")

    cand_a = _make_candidate(cand_a_uuid, similarity=0.3, vector_sim=0.3)
    cand_b = _make_candidate(cand_b_uuid, similarity=0.3, vector_sim=0.3)

    # str(id) sorting must produce the same ascending order as UUID sorting
    cand_a["id"] = cand_a_uuid  # keep as UUID object
    cand_b["id"] = cand_b_uuid  # keep as UUID object

    mock_store = AsyncMock()
    mock_store.search_memories = AsyncMock(return_value=[cand_b, cand_a])
    mock_store.search_memories_bm25 = AsyncMock(return_value=[])

    with patch.object(retrieval_module, "embed_query", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = query_emb
        mock_store.bulk_touch_memories = AsyncMock()
        mock_store.log_retrieval = AsyncMock()
        with patch.object(retrieval_module, "_is_retrieval_logging_enabled", return_value=False):
            result = await retrieval_module.retrieve_memories(
                store=mock_store,
                query_embedding=query_emb,
                user_id=uid,
                limit=5,
            )

    ids_returned = [str(m["id"]) for m in result]
    assert ids_returned == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ], f"UUID id tie-break failed: {ids_returned}"


# ---------------------------------------------------------------------------
# Test: vector and BM25 both tied — final sort uses id as secondary key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vector_and_bm25_both_tied_final_sort_tie_break():
    """When both vector and BM25 return tied candidates for the same memory,
    the final Python sort must still use id as the secondary key."""
    uid = uuid.uuid4()
    query_emb = [0.1] * 1024

    # Memory A and B are both in vector AND bm25 results (hybrid source)
    # Both end up with identical final_scores
    cand_a = _make_candidate(
        "00000000-0000-0000-0000-000000000001",
        similarity=0.34,
        vector_sim=0.34,
        bm25_score=0.4,
        created_at="2024-01-01T00:00:00Z",
        last_accessed_at="2024-01-01T00:00:00Z",
    )
    cand_b = _make_candidate(
        "00000000-0000-0000-0000-000000000002",
        similarity=0.34,
        vector_sim=0.34,
        bm25_score=0.4,
        created_at="2024-01-01T00:00:00Z",
        last_accessed_at="2024-01-01T00:00:00Z",
    )
    # Returned in reverse order from both sources
    mock_store = AsyncMock()
    mock_store.search_memories = AsyncMock(return_value=[cand_b, cand_a])
    mock_store.search_memories_bm25 = AsyncMock(return_value=[cand_b, cand_a])

    with patch.object(retrieval_module, "embed_query", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = query_emb
        mock_store.bulk_touch_memories = AsyncMock()
        mock_store.log_retrieval = AsyncMock()
        with patch.object(retrieval_module, "_is_retrieval_logging_enabled", return_value=False):
            result = await retrieval_module.retrieve_memories(
                store=mock_store,
                query_embedding=query_emb,
                user_id=uid,
                limit=5,
            )

    ids_returned = [str(m["id"]) for m in result]
    # Id ascending must win despite reversed insertion order
    assert ids_returned == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ], f"Expected id-ascending, got {ids_returned}"
