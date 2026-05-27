# Wave 0 Nondeterminism Audit — Canonical Benchmark Path

**Date:** 2026-04-21
**Task:** 1 — Audit live nondeterminism sources in the canonical benchmark path
**Audit scope:** `orchestrator/eval/longmemeval.py` → `orchestrator/eval/runner.py` → `tests/longmemeval/{ingest,evaluate}.py` (canonical lane only)
**Benchmark user:** `longmemeval@daemon.test` (`12345678-1234-5678-1234-567812345678`)
**Reference alignment:** `wave0_retrieval_ordering_audit.md` (Task 3), `wave0_judge_determinism_refs.md`, `wave0_extraction_determinism_refs.md`, `wave0_embedding_determinism_refs.md`, `wave0_provider_routing_audit.md`

---

## Summary

The canonical benchmark path contains **22 distinct nondeterminism operators** across 5 phases. Of these, **3 are uncontrolled** (require Wave 0 implementation before any reproducibility claim is credible), **9 are conditional** (deterministic in current production settings but require benchmark-mode hardening — seed, fingerprint, or deterministic secondary keys — before they can be treated as reproducible), and **10 are controlled** (deterministic by design or effectively masked).

The dominant variance source is the **answer model stochasticity** (`ANSWER_TEMPERATURE = 0.7` at `evaluate.py:91`), confirmed by the existing `dev_subset_baseline/VARIANCE.md` which records a **10pp spread** between two back-to-back runs on the same dataset.

### Status Distribution

| Status | Count | Operators |
|---|---|---|
| **uncontrolled** | 3 | #1 (answer temp), #5 (dedup contradiction), #13 (provider config at call time) |
| **conditional** | 9 | #2 (judge — best-effort only), #3 (extraction — best-effort only), #4 (extraction retry), #6 (Python sort tie), #15 (BM25 SQL tie), #16 (vector SQL tie), #18 (bulk_touch cross-question), #20 (temporal anchor), #22 (Voyage — no seed/fingerprint) |
| **controlled** | 10 | #7, #8, #9, #10, #11, #12, #14, #17, #19, #21 |

---

## Nondeterminism Operator Table

| # | Operator | File:Line | Phase | Type | Status | Benchmark Impact | Wave 0 Action / Skip Rationale |
|---|---|---|---|---|---|---|---|
| 1 | **Answer model stochasticity** — `ANSWER_TEMPERATURE = 0.7` | `tests/longmemeval/evaluate.py:91` | evaluate | stochastic-LLM | **uncontrolled** | **HIGH** — Direct driver of `hypothesis` variance. 10pp spread observed in `dev_subset_baseline`. Affects judgment input. | **Pin `ANSWER_TEMPERATURE = 0.0`** (Task 8). Also requires seed + fingerprint capture for attribution. |
| 2 | **Judge model — temperature-only deterministic** — `JUDGE_TEMPERATURE = 0.0` | `tests/longmemeval/evaluate.py:95` | evaluate | stochastic-LLM | **conditional** | MEDIUM — `temperature=0.0` reduces logit-selection variance but does **not** eliminate GPU-level/infrastructure nondeterminism or guarantee determinism. OpenAI docs say seed+fp are "best effort, not guaranteed." No seed or fingerprint currently captured. | **Add `seed` + `system_fingerprint` capture** in benchmark mode (Task 8). Judge is `conditional` in benchmark mode; fingerprint monitoring is required to attribute residual variance. |
| 3 | **Extraction model — temperature-only deterministic** — `EXTRACTION_TEMPERATURE = 0.0` | `orchestrator/memory/extraction.py:50` | ingest | stochastic-LLM | **conditional** | MEDIUM — `temperature=0.0` + `top_p=1.0` are most restrictive settings but no seed/fingerprint capture. Extraction prompt varies per session, so variance in fact output cascades to embeddings → dedup → retrieval → answer. | **Add `seed` + fingerprint capture** for benchmark mode (Task 9). Extraction output is `conditional` until seed+fp are instrumented. |
| 4 | **Extraction retry logic** — `should_retry` condition gates second LLM call | `orchestrator/memory/extraction.py:540-560` | ingest | conditional | **conditional** | MEDIUM — Retry fires when `len(text.strip()) >= 80` AND (no facts extracted OR rejection ratio ≥ calibrated count). Long sessions will retry, producing variable fact counts and potentially different memory sets per ingestion run. | **Disable retry (`should_retry = False`)** for benchmark cleanliness, or pin retry outcome via checkpoint and document non-reproducibility on long sessions. |
| 5 | **Dedup contradiction check** — `CONTRADICTION_TEMPERATURE = 0.1` | `orchestrator/memory/dedup.py:55` | ingest | stochastic-LLM | **uncontrolled** | MEDIUM — `check_contradiction()` at `dedup.py:143-195` is an LLM call that determines supersede vs merge. Even `temperature=0.1` admits variance. Wrong path affects active memory identity post-ingest. | **Pin `CONTRADICTION_TEMPERATURE = 0.0`** or make contradiction check a no-op for benchmark runs (Task 9). |
| 6 | **Retrieval Python final-sort tied-score ordering** — `sorted(..., key=final_score, reverse=True)` | `orchestrator/memory/retrieval.py:920-924` | evaluate | ordering | **conditional** | MEDIUM — Python `sorted()` is stable. But tied `final_score` (identical hybrid score) relies on dict insertion order as tie-break, which traces back to non-deterministic SQL layer outputs. Concrete tie scenario demonstrated in Task 3 audit. | **Add deterministic secondary key**: `sorted(..., key=lambda item: (_as_float(item.get("final_score"), 0.0), str(item.get("id", ""))), reverse=True)` (Task 10). |
| 7 | **Async `bulk_touch_memories()` fire-and-forget** — fire-after-return touch task | `orchestrator/memory/retrieval.py:942-948` | evaluate | async-side-effect | controlled | LOW — Touch updates `last_accessed_at` and increments `access_count` **after** ranked results are returned. Affects future recency scoring, not the current question's retrieval ranking. | None for current run reproducibility. Flagged for cross-run awareness. |
| 8 | **Async `log_retrieval()` fire-and-forget** — fire-after-return log task | `orchestrator/memory/retrieval.py:975-994` | evaluate | async-side-effect | controlled | LOW — `log_retrieval` writes candidate scores after results are returned. No ranking impact. `latency_ms` is accurate at log time. | None for benchmark reproducibility. Diagnostic only. |
| 9 | **`_days_since_accessed` wall-clock recency** — `dt.datetime.now()` at scoring time | `orchestrator/memory/retrieval.py:348-362` | evaluate | wall-clock | controlled | LOW — Recency scores decay continuously. For a single run (hours), relative recency is stable. Across separate runs on different days, `recency_score()` breakpoints (7/30/90 days) shift. | Document that day-spanning runs require a fixed wall-clock anchor. |
| 10 | **Temporal window detection anchor** — `dt.datetime.now(dt.timezone.utc)` when no `query_reference_time` provided | `orchestrator/memory/retrieval.py:213` | evaluate | wall-clock | **conditional** | MEDIUM — Temporal query windows for relative dates ("last week", "yesterday") depend on anchor time. Anchor drift across days changes window boundaries. | **Pin `query_reference_time`** to a fixed timestamp (e.g., dataset publication date) for all benchmark queries (Tasks 6-7). |
| 11 | **Corpus plan ordering via dict iteration** — `build_corpus_plan` iterates `corpus_sessions_by_key` dict | `tests/longmemeval/ingest.py:173-239` | ingest | ordering | controlled | MEDIUM — Python 3.7+ dicts preserve insertion order. Dataset is a `list` from JSON, so `enumerate(dataset)` order is deterministic. First session to claim a `corpus_key` becomes canonical — deterministic but dataset-order-dependent. | None for reproducibility of a fixed dataset. |
| 12 | **Corpus key uniqueness / canonical session selection** — first-encountered normalized session wins | `tests/longmemeval/ingest.py:200-212` | ingest | ordering | controlled | LOW — Same as #11. Deterministic given a fixed dataset. | None given a fixed dataset. |
| 13 | **Provider config routing at call time** — `get_settings()` resolved per LLM call | `tests/longmemeval/evaluate.py:201-202`, `orchestrator/memory/extraction.py:24-25` | evaluate/ingest | config | **uncontrolled** | HIGH — `get_settings()` is called inside `_call_llm_with_provider_config` and `_get_provider_call_params` at each LLM call. If env vars or config reload mid-run, provider routing (base_url, api_key, model normalization) differs mid-run. No `system_fingerprint` captured at any call site. | **Snapshot provider_config once before the run** and pass explicitly (Tasks 8, 9, 11). Add `BenchmarkProviderDriftError` on mismatched fingerprint. |
| 14 | **BM25 score normalization via max-in-candidate-set** — `max_bm25` from current batch | `orchestrator/memory/retrieval.py:430-433` | evaluate | scoring | controlled | LOW — BM25 normalization is relative to current query's candidate set. Consistent behavior, not random variance. | None; by-design for hybrid scoring. |
| 15 | **BM25 SQL tied-score ordering** — `ORDER BY bm25_score DESC` without secondary key | `orchestrator/memory/store.py:1023`, `1051` | evaluate | ordering | **conditional** | MEDIUM — `ts_rank()` returns `real`; equal BM25 scores across different memories produce ties. PostgreSQL does not guarantee order for equal keys. Tie-break undefined without `id ASC`. | **Add `, id ASC`** to both BM25 `ORDER BY` clauses (Task 10). |
| 16 | **Vector SQL tied-score ordering** — `ORDER BY embedding <=> $2::vector` without secondary key | `orchestrator/memory/store.py:933`, `963` | evaluate | ordering | **conditional** | MEDIUM — Cosine distance returns `double precision`; very high similarity or rounding can produce equal distances. pgvector does not guarantee ordering for equal distances. | **Add `, id ASC`** to both vector `ORDER BY` clauses (Task 10). |
| 17 | **Entity expansion ordering** — `find_entities_by_alias` and `get_entity_by_lookup_key` list merge order | `orchestrator/memory/retrieval.py:652-714` | evaluate | ordering | controlled | LOW — Entity candidates from `created_at DESC` SQL order; single-row lookups; `seen_ids` set prevents duplicates. No ties possible. | None. |
| 18 | **`bulk_touch_memories` access count cross-question contamination** — `access_count` incremented post-retrieval | `orchestrator/memory/retrieval.py:942-948`, `store.py:838-849` | evaluate | stateful | **conditional** | MEDIUM — `bulk_touch_memories` increments `access_count` after each retrieval. `access_boost()` (1.0/1.05/1.1/1.15) for question N+1 is influenced by memories retrieved for question N. | **Disable `bulk_touch_memories`** during benchmark evaluation via flag, or reset `access_count` between questions (Tasks 6-7). |
| 19 | **`log_retrieval` persistence timing** — `latency_ms` computed at log time vs retrieval start | `orchestrator/memory/retrieval.py:951` vs `528` | evaluate | timing | controlled | LOW — `latency_ms = time.monotonic() - start_time` is accurate. Fire-and-forget log is correct for latency. | None. |
| 20 | **Dataset JSON load ordering** — `json.load()` preserves list order | `orchestrator/eval/runner.py:134-135`, `tests/longmemeval/evaluate.py:114-115` | ingest/evaluate | ordering | controlled | LOW — Python 3.7+ `json.load()` preserves list order. Dataset is `list`, so iteration order deterministic. | None. |
| 21 | **Evaluate question iteration order** — `enumerate(dataset)` at `runner.py:780` | `orchestrator/eval/runner.py:780` | evaluate | ordering | controlled | LOW — Dataset list order is preserved from JSON load. Checkpoint restart uses `question_order` list to maintain ordering. | None. |
| 22 | **Voyage embedding conditional nondeterminism** — API call with no seed/fingerprint | `orchestrator/memory/embedding.py:111-169` | evaluate | external | **conditional** | HIGH — Voyage AI has **no `seed` parameter, no `system_fingerprint` equivalent**. Community evidence confirms conditional nondeterminism (cosine similarity ~0.992-0.993 on repeated calls). Embedding variance cascades into retrieval rank changes. | **Pin embedding model versions explicitly** in config pin. Document as highest-risk structural constraint. Use fingerprint-style comparison (rerun embedding and compare vectors) to detect drift (Tasks 5, 12). |

---

## Critical Path: Answer Stochasticity (Operator #1)

This is the **root cause** of the 10pp variance observed in `dev_subset_baseline/VARIANCE.md` between `run1` (32.0%) and `run2` (22.0%).

**Evidence:**
- `evaluate.py:91`: `ANSWER_TEMPERATURE = 0.7`
- `evaluate.py:323-341`: `answer_with_llm()` calls the model with this temperature to generate `hypothesis`
- `evaluate.py:289-320`: `judge_answer()` receives `hypothesis` and `reference`; different `hypothesis` → different `judgment`
- `dev_subset_baseline/VARIANCE.md`: identical datasets, checkpoint state, configuration — only answer model stochasticity differs

**Wave 0 required action:** Pin `ANSWER_TEMPERATURE = 0.0` + add `seed` + capture `system_fingerprint` for attribution (Task 8).

---

## Critical Path: Dedup Contradiction (Operator #5)

Even at `CONTRADICTION_TEMPERATURE = 0.1`, the `check_contradiction()` LLM call (`dedup.py:143-195`) can produce non-deterministic results. The path taken (supersede vs merge) changes which memory ID is active post-ingest, cascading to downstream retrieval.

**Evidence:**
- `dedup.py:463`: `contradiction_detected, explanation = await check_contradiction(existing_content, fact.content)`
- `dedup.py:487-503`: `supersede_memory` vs `touch_memory + merged` path chosen based on LLM output
- `dedup.py:55`: `CONTRADICTION_TEMPERATURE = 0.1`
- `wave0_provider_routing_audit.md`: no fingerprint capture at this call site

**Wave 0 required action:** Pin `CONTRADICTION_TEMPERATURE = 0.0` or make contradiction check a no-op returning `(False, "")` for benchmark runs (Task 9).

---

## Retrieval Ordering — Corrected Treatment (Operators #6, #15, #16)

**This section supersedes any prior "no action needed" conclusions for retrieval ordering.**

The plan explicitly locked deterministic ordering across **all** retrieval ranking layers (vector SQL, BM25 SQL, Python sort). Task 3's `wave0_retrieval_ordering_audit.md` identified **4 tied-risk sites** requiring Wave 0 deterministic secondary keys:

| # | Layer | File:Line | Current Ordering | Tie Risk | Wave 0 Secondary Key Required |
|---|---|---|---|---|---|
| 6 | **Python sort** | `retrieval.py:920` | `sorted(..., key=final_score, reverse=True)` | **YES** | `, id ASC` as tiebreaker |
| 15 | **BM25 SQL (cat)** | `store.py:1023` | `ORDER BY bm25_score DESC` | **YES** | `, id ASC` |
| 16 | **BM25 SQL (no cat)** | `store.py:1051` | `ORDER BY bm25_score DESC` | **YES** | `, id ASC` |
| — | **Vector SQL (cat)** | `store.py:933` | `ORDER BY embedding <=> vector` | YES (Task 3, not in this table — covered) | `, id ASC` |
| — | **Vector SQL (no cat)** | `store.py:963` | `ORDER BY embedding <=> vector` | YES (Task 3, not in this table — covered) | `, id ASC` |

**Retrieval call chain (corrected):**
```
evaluate_single (evaluate.py:367)
  └─ embed_query (embedding.py:223)
       └─ _embed_texts → _embed_with_retry → _post_embeddings
          [Voyage — conditional nondeterminism, Operator #22]
  └─ retrieve_user_memories (evaluate.py:344)
       └─ retrieve_memories_for_text (retrieval.py:504)
            ├─ embed_query (if query_embedding not pre-computed)
            ├─ retrieve_memories (retrieval.py:717)
            │    ├─ store.search_memories (vector, store.py:897)
            │    │    └─ ORDER BY embedding <=> $2::vector, id ASC  [TASK 10 — id ASC required]
            │    ├─ store.search_memories_bm25 (store.py:984)
            │    │    └─ ORDER BY bm25_score DESC, id ASC  [TASK 10 — id ASC required]
            │    ├─ candidate_map iteration (retrieval.py:812-837) [dict insertion order — deterministic]
            │    ├─ _get_entity_expanded_candidates (retrieval.py:623) [ORDER BY created_at DESC — deterministic]
            │    ├─ _normalize_bm25_scores (retrieval.py:427) [max-in-set — by design]
            │    ├─ _hybrid_score per candidate (retrieval.py:900)
            │    ├─ sorted(..., key=(final_score, str(id)), reverse=True) (retrieval.py:920)
            │    │    [TASK 10 — deterministic secondary key required]
            │    ├─ [filter MIN_FINAL_SCORE]
            │    └─ [:target_limit] (retrieval.py:924)
            ├─ bulk_touch_memories async fire-after (retrieval.py:942) [Operator #18 — conditional]
            └─ log_retrieval async fire-after (retrieval.py:975) [diagnostic only]
```

The comment "deterministic within pgvector" in prior version was incorrect — pgvector does not guarantee row ordering for equal distance/score keys. Wave 0 deterministic secondary keys are required for all 4 tied-risk sites.

---

## Judge and Extraction: Corrected Benchmark-Mode Status (Operators #2, #3)

**Prior version marked these as "deterministic — no action required." This is incorrect.**

Per `wave0_judge_determinism_refs.md` and `wave0_extraction_determinism_refs.md`:

> "Chat Completions are non-deterministic by default... Determinism is not guaranteed, and you should refer to the `system_fingerprint` response parameter to monitor changes in the backend."
> — OpenAI Advanced Usage — Reproducible outputs

- **`temperature=0.0`** eliminates logit-selection sampling randomness but does **not** eliminate GPU kernel variance, batch scheduling nondeterminism, or backend config changes
- **`seed` parameter** is best-effort only — "determinism is not guaranteed"
- **`system_fingerprint`** must be captured and validated for reproducibility to be credible

**Current state:**
- Judge: `temperature=0.0` ✓, no `seed` ✗, no `system_fingerprint` capture ✗
- Extraction: `temperature=0.0, top_p=1.0` ✓, no `seed` ✗, no `system_fingerprint` capture ✗

**Wave 0 required action (Tasks 8, 9):** Both call sites must add `seed` parameter and `system_fingerprint` capture in benchmark mode. Reproducibility is `conditional` on fingerprint consistency matching across runs.

---

## Ingest Ordering: Corrected Extraction Treatment (Operator #3)

The ingestion call chain note "[temperature=0.0 deterministic]" is **oversimplified**. The correct statement is:

- `EXTRACTION_TEMPERATURE = 0.0` + `EXTRACTION_TOP_P = 1.0` are the most restrictive sampling settings ✓
- But without `seed` and `system_fingerprint` capture, extraction output is **conditionally deterministic** — variance is reduced but not eliminated
- Extraction retry (Operator #4) further complicates reproducibility for long sessions

**Wave 0 action (Task 9):** Instrument extraction with seed + fingerprint for benchmark mode. The reference pack explicitly states that fingerprint recording is the required verification mechanism, not assumed strict determinism.

---

## Async Side-Effects: Corrected Treatment (Operators #7, #8, #18)

| Operator | Prior Treatment | Corrected Treatment | Rationale |
|---|---|---|---|
| `bulk_touch_memories` (#7) | "controlled — no ranking impact" | **controlled** | Correct — it fires after the ranking is returned; no ranking effect within the same question |
| `log_retrieval` (#8) | "controlled" | **controlled** | Correct — diagnostic only |
| `bulk_touch_memories` access-count effect (#18) | "conditional — cross-question contamination" | **conditional** | Correctly identified in prior version — same classification retained |

The distinction: `#7` is the fire-and-forget mechanism (timing nondeterminism), while `#18` is the **state mutation** that affects subsequent retrievals via `access_boost()`. Both need Wave 0 action: `#18` requires disabling or resetting, `#7` is informational only.

---

## Verified Per-Benchmark-Stage Coverage

| Benchmark Stage | Operators | All Required Operators Present? |
|---|---|---|
| Ingest | #3, #4, #5, #11, #12 | ✓ |
| Extraction | #3 (extraction model), #4 (retry), #5 (contradiction) | ✓ |
| Retrieval | #6, #7, #8, #9, #10, #14, #15, #16, #17, #18, #22 | ✓ |
| Evaluate | #1, #2, #6, #15, #16, #18, #20, #21 | ✓ |
| Provider routing | #2, #3, #5, #13 | ✓ |
| Reset / checkpoint | Covered in `wave0_state_reset_audit.md` | ✓ |

---

## Verification Checklist (for artifact authoring)

- [ ] **`ANSWER_TEMPERATURE`** is pinned to `0.0` in the benchmark-mode config (Task 8)
- [ ] **`seed` parameter** added to answer and judge calls in benchmark mode (Task 8)
- [ ] **`system_fingerprint` capture** added at all 4 LLM call sites in benchmark mode (Tasks 8, 9)
- [ ] **`JUDGE_TEMPERATURE = 0.0`** confirmed (already correct)
- [ ] **`CONTRADICTION_TEMPERATURE`** pinned to `0.0` or contradiction made a no-op for benchmark runs (Task 9)
- [ ] **`bulk_touch_memories`** disabled or `access_count` reset between questions (Tasks 6-7)
- [ ] **Deterministic secondary keys** added to all 4 retrieval tied-risk sites: vector SQL (store.py:933, 963), BM25 SQL (store.py:1023, 1051), Python sort (retrieval.py:920) (Task 10)
- [ ] **`embedding_query_model` and `embedding_document_model`** pinned to specific versioned model names in config pin
- [ ] **Provider config** snapshotted before the run, not resolved per-call (Tasks 8, 9, 11)
- [ ] **Dataset path** locked to a specific file with known SHA256
- [ ] **Checkpoint version** is `2` (current, runner.py:88)
- [ ] **Extraction retry** disabled or its non-reproducibility documented (Task 7)
- [ ] **Temporal query anchor** pinned to a fixed `query_reference_time` for all benchmark queries (Tasks 6-7)
- [ ] **`bulk_touch_memories` flag** added to `retrieve_memories` to allow benchmark-mode disabling (Tasks 6-7)

---

## Files in Scope

| File | Role |
|---|---|
| `orchestrator/eval/longmemeval.py` | CLI entrypoint, phase routing |
| `orchestrator/eval/runner.py` | Canonical runner, checkpoint management, config pinning |
| `tests/longmemeval/ingest.py` | Dataset loading, corpus plan, session ingestion, extraction polling |
| `tests/longmemeval/evaluate.py` | Question evaluation, answer/judge LLM calls |
| `orchestrator/memory/retrieval.py` | Hybrid retrieval, ranking, async side-effects, Python final sort |
| `orchestrator/memory/extraction.py` | Fact extraction, confidence calibration, retry logic |
| `orchestrator/memory/dedup.py` | Deduplication, contradiction checking |
| `orchestrator/memory/embedding.py` | Voyage AI embedding calls (no seed/fingerprint) |
| `orchestrator/memory/store.py` | DB queries, vector search, BM25 search |
| `orchestrator/config.py` | Settings resolution (provider config, thresholds) |

---

## Alignment with Wave 0 Plan Locked Decisions

| Locked Decision (from plan) | Reflected in This Audit | Status |
|---|---|---|
| Answer-path determinism must be included | Operator #1 = uncontrolled; Operator #2 = conditional | ✓ Aligned |
| Dedup contradiction checks must be pinned | Operator #5 = uncontrolled | ✓ Aligned |
| Deterministic ordering across all ranking layers | Operators #6, #15, #16 = conditional; retrieval section updated | ✓ Aligned |
| Judge/extraction are best-effort, require fingerprint capture | Operators #2, #3 = conditional; judge/extraction sections corrected | ✓ Aligned |
| Provider pinning must fail loudly | Operator #13 = uncontrolled; `wave0_provider_routing_audit.md` referenced | ✓ Aligned |
| No `seed` guarantee — fingerprint monitoring required | Operators #2, #3, #22 = conditional; reference packs cited | ✓ Aligned |
| Wave 0 must stabilize retrieval ordering (SQL + Python) | Operators #6, #15, #16 updated; retrieval section rewritten | ✓ Aligned |
| Extraction retry must be addressed | Operator #4 = conditional | ✓ Aligned |
