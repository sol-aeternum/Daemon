# Baselines

## Wave 0 production-memory baseline status

This document records the current state of the first production-aligned LongMemEval artifact after Path A.

Path A successfully aligned the benchmark harness with the production-style memory prompt path. The benchmark no longer measures the old thin bullet-list user prompt; it now assembles a production-style system prompt and sends a `[system, user]` pair. That means historical benchmark-harness scores are no longer the right reference for production-memory evaluation.

### Production-aligned artifact output — INVALID / NOT A BASELINE

The artifact written at `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_score.json` reports these exact values:

- `aggregate=0.0`
- `IE-user=0.0`
- `IE-assistant=0.0`
- `IE-preference=0.0`
- `MR=0.0`
- `KU=0.0`
- `TR=0.0`
- `ABS=0.0`

These numbers must be read only as **production-aligned artifact output — INVALID / NOT A BASELINE**.

### Why the artifact is invalid

- `longmemeval_results.jsonl` contains 500 rows.
- 482 rows failed with `Benchmark fingerprint drift`.
- 18 rows have no `error` field, but they still do not establish a valid quality signal: 17 are `incorrect` and 1 is `partially_correct`.
- Raw `status="complete"` rate is `0/500` because the emitted rows omit the `status` field entirely.
- The answer/judge model is reachable, but its live fingerprints differ from the pinned benchmark fingerprints.

Therefore the 0.0 aggregate and all 0.0 category values are not model-quality results. They are blocked-run outputs.

### Variance and blocker note

No valid baseline means there is nothing sound to compare against historical score envelopes or use for pass/fail gating. Earlier bounded-variance work and older scores still describe the old benchmark path, not this production-aligned path. Until the aligned answer/judge fingerprint contract is stable enough to yield an accepted artifact, baseline and variance claims remain deferred.

### Superseded historical scores

The historical 67.8, 81.1, 28-34, and 22.4 values are superseded for production-memory evaluation. They may remain in the record as benchmark-harness history only.

### Non-regression note

Extraction non-regression remains PASS and independent of the aligned baseline blocker:

- Precision: 1.00
- Recall: 1.00
- Adversarial FP: 0

### Authoritative baseline state

As of 2026-05-04, the project has a **valid production-aligned LongMemEval_S baseline** under Wave 0 Closure Option A.

---

## Wave 0 Option A — Authoritative Baseline (2026-05-04)

**Status:** Accepted production-aligned baseline
**Task:** C1-D (locked) + E5 (structural pass) + E7 (ledger update)
**Supersedes:** all prior benchmark-harness scores and the 2026-04-30 invalid artifact

### Raw Artifact Score (Preserved)

| Field | Value |
|---|---|
| Attempted | 500 |
| Success count | 473 |
| Error count | 27 |
| Correct judgments | 49 |
| **Raw artifact score** | **49 / 500 = 0.098** |

### Option A Disposition-Adjusted Baseline

| Field | Value |
|---|---|
| Excluded rows | 27 invalid-ciphertext (bounded error-class exclusion from C1-A) |
| Adjusted denominator | 473 |
| Correct (unchanged) | 49 |
| **Disposition-adjusted baseline** | **49 / 473 = 0.10359408033826638** |

### Per-Category Artifact Scores

| Category | Correct | Total | Accuracy |
|---|---|---|---|
| ABS | 16 | 30 | 0.5333 |
| IE-assistant | 7 | 56 | 0.1250 |
| IE-preference | 5 | 30 | 0.1667 |
| IE-user | 7 | 64 | 0.1094 |
| KU | 10 | 72 | 0.1389 |
| MR | 3 | 121 | 0.0248 |
| TR | 1 | 127 | 0.0079 |

### Variance Contract

Single-run point estimate with **bounded variance** — not zero-variance, not triple-run-locked.

**Variance sources:**
- OpenAI token/fingerprint drift despite `seed=42` and `temperature=0.0` where fingerprints are stable
- Voyage AI retrieval/embedding nondeterminism
- arq/background job timing

### Legacy Gate Policy — Superseded

The following old gates are **NOT final pass/fail criteria** under Option A. They were pre-data diagnostic sanity bounds:

| Old Gate | Raw Value | Status |
|---|---|---|
| `aggregate > 0.15` | 49/500 = 0.098 | **SUPERSEDED** — Option A defines new structural baseline |
| `success_count >= 495` | 473 | **SUPERSEDED** — bounded exclusions apply; 27 error-class rows excluded analytically |
| Per-category floor gates | Multiple fail | **SUPERSEDED** — Option A has no per-category floor requirement |

### 27 Invalid-Ciphertext Rows — W1+ Storage Anomaly

27 rows errored with `Invalid ciphertext: decryption failed (wrong key or corrupted data)` during retrieval decryption. These rows are analytically excluded under Option A and are **not Wave 0 blockers**. Per-question attribution is structurally impossible — the exception fires at `store.py:903` before the async retrieval log write (`retrieval.py:696`) is scheduled. W1+ follow-up required for storage-anomaly resolution.

### First Accepted Baseline

This is the **first accepted production-aligned LongMemEval_S baseline** under User Option A. Historical benchmark-harness scores and the earlier invalid 0.0 artifact (`wave0_full_corpus_aligned/longmemeval_score.json`) are superseded for W1 comparisons.
