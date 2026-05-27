# Wave 0 — Variance Attribution Design

**Date:** 2026-04-21
**Scope:** Minimum credible run matrix, decision thresholds, residual-review trigger, and attribution protocol for LongMemEval dev-subset reproducibility
**Status:** Design document; requires Oracle review before validation

---

## 1. Context and Problem Statement

**Observed variance (from `VARIANCE.md`):**
- `run1 = 32.0%` strict accuracy (16/50 correct)
- `run2 = 22.0%` strict accuracy (11/50 correct)
- **Spread = 10.0 percentage points** — far outside the **≤3pp gate**

The variance gate failure indicates that at least one component in the pipeline is producing non-reproducible outputs across clean runs. The three candidates are:

| Suspect | Model | Seed Available | Fingerprint Available | Temperature |
|---|---|---|---|---|
| **Judge** | `gpt-4o` via LiteLLM/OpenRouter | Beta, best-effort | Yes, but not captured | `0.0` |
| **Extraction** | `gpt-4o-mini` via LiteLLM/OpenRouter | Beta, best-effort | Yes, but not captured | `0.0` |
| **Embeddings** | `voyage-4-large` / `voyage-4-lite` | **No** | **No** | N/A |

From the reference packs:
- **Judge/Extraction:** `seed` is beta best-effort; `system_fingerprint` must be captured to validate reproducibility
- **Embeddings:** No seed, no fingerprint — conditional nondeterminism confirmed in community evidence; treated as a **measured risk**

---

## 2. Guiding Principles

1. **Minimum matrix:** Do not propose a full factorial design. A sequential isolation strategy with 2–3 runs is sufficient to rank suspects.
2. **Cost-awareness:** Canonical dev-subset ingest projects to ~3.79h per run. Evaluation/scoring adds ~minutes. Run count must be justified by information gain.
3. **Seed is best-effort:** Even when seed is passed, reproducibility requires fingerprint matching and is not guaranteed. Do not assume seed = deterministic.
4. **Embeddings are primary suspect:** The absence of any seed/fingerprint mechanism makes embeddings the highest-risk contributor to cascading variance.
5. **Oracle review trigger:** If residual variance after attribution exceeds the gate, Oracle review is mandatory before claiming reproducibility.

---

## 3. Attribution Protocol

### 3.1 Phase 0 — Instrumented Baseline (2 runs minimum)

**Run A (Instrumented Full Pipeline):**
Same canonical lane with three additions:

1. **Capture `system_fingerprint` from every judge call** — log `system_fingerprint` per question judgment in the results JSONL
2. **Capture `system_fingerprint` from every extraction call** — log per session in the checkpoint
3. **Log embedding model version + timestamp** — Voyage does not expose fingerprint, but logging the model ID and call timestamp provides a rough alignment signal

**Run B (Re-run with seed on Judge + Extraction):**
Same canonical lane with:

1. Add `seed=<fixed_integer>` to judge calls
2. Add `seed=<fixed_integer>` to extraction calls
3. Continue capturing `system_fingerprint`
4. Use **identical** seed value across both judge and extraction (can be different seeds if needed, but record both)

**Decision after Run B:**

| Outcome | Interpretation | Next Step |
|---|---|---|
| Spread ≤ 3pp | Pipeline is reproducible as-is | Gate passed; no further attribution needed |
| Spread > 3pp, fingerprints match across runs | Seed working; variance is from embeddings | → Proceed to Phase 1 |
| Spread > 3pp, fingerprints differ | Provider-side model update occurred | → Log as upstream; Oracle review required |

### 3.2 Phase 1 — Embedding Isolation (1 run)

**Run C (Cache + Swap):**
1. Run canonical pipeline BUT use **cached embedding vectors** from Run A/B instead of live embedding calls
2. Store a copy of all document and query embeddings from Run A
3. Replace live embedding calls with cached vectors from Run A's artifact directory
4. Judge, extraction, and retrieval all use the cached embeddings

**Decision after Run C:**

| Outcome | Interpretation | Next Step |
|---|---|---|
| Spread ≤ 3pp with cached embeddings | **Embeddings are the primary suspect** | Attribute to embedding nondeterminism; report residual variance |
| Spread > 3pp with cached embeddings | Variance is in judge + extraction | → Proceed to Phase 2 |

### 3.3 Phase 2 — Judge Isolation (1 run)

**Run D (Judge Only with seed + fingerprint):**
1. Use cached embeddings from Phase 1
2. Use live extraction
3. Pass `seed=<fixed>` + capture fingerprint on extraction
4. Pass `seed=<fixed>` + capture fingerprint on judge

This isolates whether the residual variance (if any remains after Phase 1) comes from extraction or judge.

### 3.4 Run Count and Cost Summary

| Phase | Run | Duration Estimate | Information Gained |
|---|---|---|---|
| Phase 0 | Run A (instrumented) | ~3.79h ingest + eval/scoring | Fingerprint baseline; embedding model version log |
| Phase 0 | Run B (seeded) | ~3.79h ingest + eval/scoring | Determines if seed+fingerprint resolves variance |
| Phase 1 | Run C (cached embeddings) | ~3.79h ingest + eval/scoring | Isolates embedding contribution |
| Phase 2 | Run D (judge isolated) | ~3.79h ingest + eval/scoring | Isolates judge vs extraction contribution |

**Minimum before attribution is possible:** 2 runs (Phase 0)
**Full attribution matrix:** 4 runs maximum (if Phase 1 and 2 are needed)
**Worst case cost:** ~15.2h of ingest time for full attribution

---

## 4. Decision Thresholds

### 4.1 Gate Check

After each run pair:

```
spread = max(strict_accuracy across runs) - min(strict_accuracy across runs)
gate = 0.03  # 3pp
gate_passed = spread <= gate
```

### 4.2 Fingerprint Match Criteria

For a fingerprint match to be considered **credible evidence** of reproducibility:
- All captured `system_fingerprint` values must be **identical** across the compared runs for the same model
- Any fingerprint change → the run pair is **not** comparable for reproducibility
- Fingerprint comparison is per-call AND aggregated (any change = invalidate that call's reproducibility claim)

### 4.3 Embedding Stability Criteria

Voyage embeddings have no fingerprint. Stability is assessed by:
- Cosine similarity of cached vs live embedding for the same input (if Run C is executed)
- Model + API version logging provides indirect alignment signal
- If cosine similarity of same-text embeddings varies by >0.005 (distance >0.005), treat embeddings as unstable

---

## 5. Residual Variance Threshold — Oracle Review Trigger

### 5.1 Definition

**Residual variance** = spread that remains after Phase 1 (embedding isolation) or Phase 2 (full isolation), after accounting for identified sources.

### 5.2 Oracle Review Trigger

Oracle review is **mandatory** before validation if:

1. **Residual spread > 3pp** after full attribution (all four runs completed and all suspects evaluated)
2. **Any `system_fingerprint` change** occurs during a run pair that was intended to be comparable
3. **Embedding instability > 0.005 cosine distance** is detected in Run C cached-vs-live comparison
4. **Any run produces `system_fingerprint = null`** — indicates provider-level change mid-run

### 5.3 Oracle Review Scope

Oracle must evaluate:
1. Whether the residual variance is attributable to a known upstream source (provider model update, API change)
2. Whether the variance gate should be relaxed for this phase (with documented justification)
3. Whether full-corpus reproducibility is achievable or if the benchmark needs redesign
4. Whether additional runs would provide meaningful information or merely consume resources

---

## 6. Fingerprint and Seed Caveats

### 6.1 Seed Best-Effort Statement

> **Seed support in OpenAI's API (and therefore LiteLLM passthrough via OpenRouter) is beta and best-effort. Determinism is not guaranteed even with seed set and fingerprints matching. Any attribution protocol that relies on seed-based reproducibility must explicitly state this caveat and must not claim guaranteed reproducibility — only "best-effort reproducibility with fingerprint validation."**

### 6.2 Fingerprint Limitations

- `system_fingerprint` is only available for OpenAI chat completions via LiteLLM — Voyage embeddings have no equivalent
- A matching fingerprint is **necessary but not sufficient** for reproducibility
- A changed fingerprint **invalidates** reproducibility claims for that run pair

### 6.3 Embedding Reproducibility Statement

> **Voyage AI embeddings do not support a seed parameter and do not expose a system fingerprint. The attribution protocol cannot validate embedding reproducibility through fingerprint comparison. Embedding stability must be assessed through cached-vs-live vector comparison (cosine similarity), which is an indirect and imperfect proxy.**

### 6.4 Fingerprint Recording Requirements

For any run that is intended to be part of a reproducibility claim:

```python
# Judge response
{
    "question_id": "...",
    "system_fingerprint": "fp_xxxx",  # MUST be captured
    "seed_used": null,               # null if seed not used
    "model": "openrouter/openai/gpt-4o"
}

# Extraction response (from LiteLLM response extra)
{
    "session_id": "...",
    "system_fingerprint": "fp_yyyy",  # MUST be captured
    "seed_used": null,                # null if seed not used
    "model": "openrouter/openai/gpt-4o-mini"
}
```

---

## 7. Artifact Requirements

Each run directory must contain:

| File | Required Fields |
|---|---|
| `longmemeval_results.jsonl` | `system_fingerprint` per judgment, `seed_used` per call |
| `longmemeval_checkpoint.json` | `benchmark_fingerprints` block: `{judge_fingerprints: [...], extraction_fingerprints: [...], embedding_model_versions: {...}}` |
| `longmemeval_score.json` | (existing) strict_accuracy, category_accuracies |
| `fingerprint_audit.json` | (new) per-run fingerprint stability summary, fingerprint_change_count per model |

---

## 8. Attribution Matrix Summary

```
              ┌─────────────────────────────────────────────────────────┐
              │                  ATTRIBUTION MATRIX                    │
              ├─────────────────┬─────────────────┬─────────────────────┤
              │   Judge         │  Extraction     │    Embeddings       │
              │   gpt-4o        │  gpt-4o-mini    │    voyage-4-large   │
              │                 │                 │    voyage-4-lite    │
  ────────────┼─────────────────┼─────────────────┼─────────────────────┤
  Seed?       │  Beta (pass     │  Beta (pass     │  NO SEED SUPPORT    │
  Available   │  through)        │  through)       │                    │
  ────────────┼─────────────────┼─────────────────┼─────────────────────┤
  Fingerprint │  YES (capture   │  YES (capture   │  NO FINGERPRINT    │
  Available  │  per call)       │  per call)      │  SUPPORT           │
  ────────────┼─────────────────┼─────────────────┼─────────────────────┤
  Isolation   │  Phase 2         │  Phase 2        │  Phase 1            │
  Phase       │  (with cached   │  (with cached   │  (cached vectors    │
  Possible    │   embeddings)   │   embeddings)   │   swap)            │
  ────────────┼─────────────────┼─────────────────┼─────────────────────┤
  Primary     │  SECONDARY      │  SECONDARY      │  PRIMARY SUSPECT   │
  Suspect?    │  (if seed+fp     │  (if seed+fp    │  (no seed, no fp,   │
  Ranking     │   doesn't fix)  │   doesn't fix)  │   community ev)    │
  ────────────┼─────────────────┼─────────────────┼─────────────────────┤
  Temperature │  0.0 (fixed)     │  0.0 (fixed)    │  N/A               │
  Setting     │                 │                 │                    │
  ────────────┼─────────────────┼─────────────────┼─────────────────────┤
  Variance    │  MEDIUM         │  MEDIUM         │  HIGH              │
  Risk        │  (seed beta,    │  (seed beta,    │  (no seed/fp,       │
  Rating      │   but 0.0 temp) │   but 0.0 temp) │   confirmed nd)     │
  ────────────┼─────────────────┴─────────────────┴─────────────────────┤
  Oracle      │  REQUIRED if: fingerprint changes, or residual spread   │
  Review      │  > 3pp after full attribution, or fingerprint=null      │
  Trigger     │  detected                                                 │
  ────────────┴─────────────────────────────────────────────────────────┘
```

---

## 9. Oracle Review Output Contract

When Oracle review is triggered, the Oracle must produce:

1. **Variance attribution statement:** Which component(s) are responsible for the observed spread, with confidence level (high/medium/low)
2. **Reproducibility verdict:** Whether the benchmark is reproducible under the current protocol, or whether changes are required before validation
3. **Recommended action:** Specific code/config changes, or explicit waiver with documented rationale if variance is deemed acceptable
4. **Full-corpus feasibility:** Assessment of whether full-corpus Phase 0 is achievable given the attribution findings

---

## 10. External Citations

1. OpenAI. "Advanced Usage — Reproducible outputs." https://platform.openai.com/docs/guides/advanced-usage
2. LiteLLM. "Completion Input Params." https://docs.litellm.ai/docs/completion/input
3. Voyage AI. "Text Embeddings Documentation." https://docs.voyageai.com/docs/embeddings
4. Voyage AI Community. "I'm getting different embeddings for the SAME encoding." https://docs.voyageai.com/discuss/6854e9cdbbba01001836c09f (Feb 2024)
5. He and Thinking Machines Lab. "Behavioral Fingerprints for LLM Endpoint Stability and Identity." arXiv:2603.19022v1. https://arxiv.org/html/2603.19022v1
