# Wave 0 Retrieval Ordering Audit

**Date:** 2026-04-21
**Task:** 3 — Audit retrieval ordering and all tie-breakable ranking layers
**Scope:** Memory retrieval pipeline (`retrieval.py`, `store.py`, `entities.py`)

---

## Executive Summary

The retrieval pipeline has **9 ordering sites** across 3 code layers (SQL, Python dict/list, Python sorted). **4 sites have tie risk.** The final Python `sorted()` at `retrieval.py:920` is the primary target for Wave 0 stabilization, but SQL layers at `store.py:933/963` (vector) and `store.py:1023/1051` (BM25) also lack deterministic secondary keys.

**No weight or threshold changes are made in this wave.** This audit is purely about ordering stabilization.

---

## Layer 1: Vector SQL — `search_memories()`

### File: `orchestrator/memory/store.py`

| Location | SQL | Tie Risk | Secondary Key Needed |
|----------|-----|----------|----------------------|
| Line 933 (with category) | `ORDER BY embedding <=> $2::vector` | **YES** | `id ASC` |
| Line 963 (without category) | `ORDER BY embedding <=> $2::vector` | **YES** | `id ASC` |

**Analysis:** pgvector's `<=>` cosine distance operator returns a `double precision` (float). Two semantically different memory vectors can produce identical distances, especially in:
- Low-dimensional embedding subspaces
- Memories with very similar content but different semantic meaning
- Rounded distance values at high similarity ranges

**Current behavior:** Non-deterministic among tied-distance candidates. PostgreSQL does not guarantee any order for equal keys unless `ORDER BY` explicitly includes a secondary sort column.

**Deterministic fix (Wave 0):** `ORDER BY embedding <=> $2::vector, id ASC`

---

## Layer 2: BM25 SQL — `search_memories_bm25()`

### File: `orchestrator/memory/store.py`

| Location | SQL | Tie Risk | Secondary Key Needed |
|----------|-----|----------|----------------------|
| Line 1023 (with category) | `ORDER BY bm25_score DESC` | **YES** | `id ASC` |
| Line 1051 (without category) | `ORDER BY bm25_score DESC` | **YES** | `id ASC` |

**Analysis:** `ts_rank()` returns a `real` (float). Two documents with identical term frequencies and document frequencies can receive the same BM25 score, particularly for:
- Short memory contents with identical word distributions
- Queries with few unique terms
- Identical content stored as separate memories

**Current behavior:** Non-deterministic among tied-score candidates.

**Deterministic fix (Wave 0):** `ORDER BY bm25_score DESC, id ASC`

---

## Layer 3: Entity Lookup — `get_entity_by_lookup_key()`

### File: `orchestrator/memory/store.py`

| Location | SQL | Tie Risk |
|----------|-----|----------|
| Lines 1732-1738 | `SELECT * FROM entities WHERE user_id = $1 AND lookup_key = $2` | **NO** — returns single row or null |

**Analysis:** Single-row lookup. No ordering issue.

---

## Layer 4: Entity Alias Search — `find_entities_by_alias()`

### File: `orchestrator/memory/store.py`

| Location | SQL | Tie Risk |
|----------|-----|----------|
| Line 1857 | `ORDER BY created_at DESC` | **NO** — timestamp is high-resolution |

**Analysis:** `created_at` is `timestamptz` with microsecond precision. The probability of true ties is negligible, and this is an entity lookup layer, not the final ranking — acceptable.

---

## Layer 5: Candidate Map Dict Iteration

### File: `orchestrator/memory/retrieval.py`

| Location | Code | Tie Risk |
|----------|------|----------|
| Line 839 | `all_candidates = list(candidate_map.values())` | **NO** |

**Analysis:** `candidate_map` is a `dict` keyed by `memory_id` string (from `str(c.get("id", ""))`). Each memory_id appears at most once. Python 3.7+ maintains insertion order. Vector candidates inserted first (lines 814-822), BM25 candidates second (lines 824-837), entity candidates third (lines 854-861) — but since each key is unique, there are no ties.

---

## Layer 6: Entity-Expanded Candidate Iteration

### File: `orchestrator/memory/retrieval.py` (`_get_entity_expanded_candidates`)

| Location | Code | Tie Risk |
|----------|------|----------|
| Lines 652-712 | Entity loop → `seen_memory_ids` deduplication → append | **NO** |

**Analysis:** Memory IDs are appended via `seen_memory_ids` set. Each ID is processed once. Within each entity, `find_entities_by_alias` returns `ORDER BY created_at DESC` (not tied), and `get_entity_by_lookup_key` returns single row. No ties possible here.

---

## Layer 7: Temporal Filter

### File: `orchestrator/memory/retrieval.py`

| Location | Code | Tie Risk |
|----------|------|----------|
| Lines 867-874 | List comprehension filtering | **NO** |

**Analysis:** Order-preserving filter on `all_candidates`. No ties introduced.

---

## Layer 8: BM25 Score Normalization (In-Place)

### File: `orchestrator/memory/retrieval.py`

| Location | Code | Tie Risk |
|----------|------|----------|
| Line 887 | `_normalize_bm25_scores(all_candidates)` | **NO** |

**Analysis:** In-place mutation. Preserves candidate order. Normalization math (dividing by max) does not change ordering.

---

## Layer 9: Final Python Sort — **PRIMARY TIE RISK**

### File: `orchestrator/memory/retrieval.py`

| Location | Code | Tie Risk | Secondary Key Needed |
|----------|------|----------|----------------------|
| Lines 920-924 | `sorted(filtered, key=lambda item: _as_float(item.get("final_score"), 0.0), reverse=True)[:target_limit]` | **YES** | `id ASC` |

**Analysis:** This is the **critical ordering site** identified by prior Oracle review. The hybrid score (`HYBRID_VECTOR_WEIGHT * vector_sim + HYBRID_BM25_WEIGHT * bm25_normalized + HYBRID_RECENCY_CONFIDENCE_WEIGHT * recency_confidence_trust`) is a float. Score ties occur when:

1. Two memories have identical vector similarity AND identical BM25 normalized score AND identical recency/confidence/trust products
2. The weights (0.5 / 0.3 / 0.2) are fixed, but the component floats can align

**Concrete tie scenario:**
- Memory A: `vector_sim=0.9, bm25_norm=0.5, recency=0.9, confidence=1.0, trust=0.5` → `0.5*0.9 + 0.3*0.5 + 0.2*0.45 = 0.45 + 0.15 + 0.09 = 0.69`
- Memory B: `vector_sim=0.85, bm25_norm=0.65, recency=1.0, confidence=1.0, trust=0.5` → `0.5*0.85 + 0.3*0.65 + 0.2*0.5 = 0.425 + 0.195 + 0.10 = 0.72`
- These don't tie, but edge cases with rounded floats and quantized embeddings can produce exact equality

**Current behavior:** Python `sorted()` is stable — equal-key elements maintain their original relative order. However, the original order from `filtered` (which comes from `scored` iteration over `all_candidates`) is not guaranteed to be deterministic if any prior layer had non-deterministic ordering.

**Deterministic fix (Wave 0):** `sorted(filtered, key=lambda item: (_as_float(item.get("final_score"), 0.0), str(item.get("id", ""))), reverse=True)`

---

## Layer 10: L0 Memory Prepend — `_prepend_l0_memories()`

### File: `orchestrator/memory/retrieval.py`

| Location | Code | Tie Risk |
|----------|------|----------|
| Lines 439-468 | L0 injected with `final_score = float("inf")`, then non-L0 | **NO** |

**Analysis:** L0 memories receive explicit `final_score = float("inf")`, ensuring they sort before any non-L0 memory. No tie possible between L0 and non-L0.

---

## Layer 11: L0 SQL Fetch — `get_l0_memories()`

### File: `orchestrator/memory/store.py`

| Location | SQL | Tie Risk |
|----------|-----|----------|
| Line 1186 | `ORDER BY created_at ASC` | **NO** |

**Analysis:** Deterministic ordering on `created_at` timestamp. No tie risk.

---

## Wave 0 Ordering Summary Table

| # | Layer | File:Line | Current Ordering | Tie Risk | Wave 0 Secondary Key |
|---|-------|-----------|-----------------|----------|---------------------|
| 1 | Vector SQL (cat) | store.py:933 | `embedding <=> vector` ASC | **YES** | `id ASC` |
| 2 | Vector SQL (no cat) | store.py:963 | `embedding <=> vector` ASC | **YES** | `id ASC` |
| 3 | BM25 SQL (cat) | store.py:1023 | `bm25_score DESC` | **YES** | `id ASC` |
| 4 | BM25 SQL (no cat) | store.py:1051 | `bm25_score DESC` | **YES** | `id ASC` |
| 5 | Entity lookup | store.py:1732 | single row | NO | — |
| 6 | Entity alias search | store.py:1857 | `created_at DESC` | NO | — |
| 7 | Dict iteration | retrieval.py:839 | insertion order | NO | — |
| 8 | Entity expansion | retrieval.py:652 | seen_ids append | NO | — |
| 9 | Temporal filter | retrieval.py:867 | order preserved | NO | — |
| 10 | BM25 normalization | retrieval.py:887 | in-place | NO | — |
| 11 | **Final Python sort** | retrieval.py:920 | `final_score DESC` | **YES** | `id ASC` |
| 12 | L0 prepend | retrieval.py:439 | `final_score = inf` first | NO | — |
| 13 | L0 SQL fetch | store.py:1186 | `created_at ASC` | NO | — |

**Total sites with tie risk: 4** (Layers 1, 2, 3, 4, 11 — BM25 has 2 variants per category)

---

## Score Math vs. Ordering Stabilization

**This wave does NOT change:**
- `HYBRID_VECTOR_WEIGHT = 0.5`
- `HYBRID_BM25_WEIGHT = 0.3`
- `HYBRID_RECENCY_CONFIDENCE_WEIGHT = 0.2`
- `MIN_FINAL_SCORE = 0.15`
- Any retrieval thresholds or candidate limits

**This wave ONLY adds secondary deterministic keys to prevent order drift when scores are equal.**

The distinction:
- **Score math** (weights, thresholds): Determines which candidates pass filtering and their relative score magnitude — NOT changed
- **Ordering stabilization** (secondary keys): Determines which candidate appears first when two have exactly the same score — IS being fixed

---

## Verification Plan for Task 10

To verify Wave 0 changes don't affect ranking behavior (only stabilize ties):

1. Run retrieval on a dataset with known tied-score candidates
2. Verify the returned `limit=5` order is stable across multiple runs
3. Confirm that scores themselves are unchanged (only tiebreaker order is affected)
4. Confirm that non-tied candidates retain their relative ordering from before the change
