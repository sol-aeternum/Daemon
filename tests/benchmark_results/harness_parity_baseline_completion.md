# Harness Parity Baseline Completion

**Date**: 2026-05-27  
**Plan**: `.sisyphus/plans/longmemeval-parity-baseline-completion.md`  
**Task**: 12. Write Baseline Completion Artifact  
**Final decision**: `declare-new-w1-anchor`  
**Baseline anchor tag**: `harness-parity-shipped`  
**W1 rollback target**: `pre-wave-1`

---

## 1. Completion Summary

The LongMemEval parity baseline completion plan is closed on the authorized headline-only run2 path. The cleaned LongMemEval_S corpus was validated, the real-corpus smoke trace confirmed the parity prompt-surface path, run1 produced full raw artifacts, and run2 contributed only headline metrics because the raw run2 artifacts were lost before repository copy-back and the user explicitly forbade another rerun.

The stable aggregate pair is:

- Run1 aggregate: `0.1342685370741483`
- Run2 headline aggregate: `0.146`
- Stability delta: `0.011731462925851695`
- Declared W1+ aggregate anchor: `0.14013426853707415`

The gated process therefore declares `declare-new-w1-anchor`. Run2 per-category data does not survive in the repository and is not reconstructed here.

---

## 2. Original HALT Recap

The earlier baseline attempt halted because the historical file URL for `longmemeval_s.json` returned 404:

- Failed historical URL: `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s.json`
- Resolved cleaned corpus URL: `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json`
- Canonical local path used by this plan: `/tmp/longmemeval-review/data/longmemeval_s_cleaned.json`

This completion artifact treats the cleaned corpus as the validated executable corpus for this plan. It does not claim byte-for-byte or distributional equivalence with the missing original `longmemeval_s.json`; the original URL remained unavailable, and comparability beyond the recorded cleaned-corpus validation was not proven.

---

## 3. Corpus Validation Facts

Source artifact: `tests/benchmark_results/harness_parity_corpus_validation.md`

| Fact | Value |
|---|---:|
| SHA256 | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| Records | `500` |
| Byte size | `277383467` |
| Missing required fields | `[]` |
| Non-empty haystack sessions | `500` |
| Schema fit | Same 9-field schema as `tests/benchmark_longmemeval/fixtures/dev_subset.json` |
| Corpus decision | `proceed-real-corpus-valid` |

Shared 9-field schema:

`answer`, `answer_session_ids`, `haystack_dates`, `haystack_session_ids`, `haystack_sessions`, `question`, `question_date`, `question_id`, `question_type`

Category distribution:

| Category | Count |
|---|---:|
| `knowledge-update` | 78 |
| `multi-session` | 133 |
| `single-session-assistant` | 56 |
| `single-session-preference` | 30 |
| `single-session-user` | 70 |
| `temporal-reasoning` | 133 |
| **Total** | **500** |

---

## 4. Entry Contract and Smoke Trace

Source artifacts:

- `tests/benchmark_results/harness_parity_entry_contract.md`
- `tests/benchmark_results/harness_parity_real_corpus_smoke.md`

The allowed pre-W1 measurement path is pinned to:

`tests/longmemeval/parity_harness.py:parity_evaluate_single()`

That path calls production `build_memory_context()` and `assemble_system_prompt()` before answer generation. The baseline token budget for this pre-W1 measurement remained `2500`.

Smoke trace summary:

| Question ID | Category | Preferred mapping | Memories used | Judgment | Correct | Error |
|---|---|---|---:|---|---|---|
| `e47becba` | `single-session-user` | `IE-user` | 5 | `incorrect` | false | `None` |
| `7161e7e2` | `single-session-assistant` | `IE-assistant` | 5 | `incorrect` | false | `None` |
| `8a2466db` | `single-session-preference` | `IE-preference` | 5 | `partially_correct` | false | `None` |
| `0a995998` | `multi-session` | `multi-session` | 5 | `incorrect` | false | `None` |
| `gpt4_59149c77` | `temporal-reasoning` | `temporal-reasoning` | 5 | `incorrect` | false | `None` |

Smoke invariants:

- Decision: `proceed-real-corpus-smoke-pass`
- Rows: `5`
- Unique question IDs: `5`
- Distinct categories: `5`
- `synthetic_user_id` present on every row
- Stored `memory_context` and `system_prompt` present on every row
- All five rows had `memories_used=5`
- Runtime exclusions: `0`
- Correct: `0 / 5`

---

## 5. Full Baseline Runs

### 5.1 Run1 — full raw artifacts available

Source artifacts:

- `tests/benchmark_results/harness_parity_baseline/run1/results.jsonl`
- `tests/benchmark_results/harness_parity_baseline/run1/summary.json`

Run1 headline metrics:

| Metric | Value |
|---|---:|
| Rows | `500` |
| Unique IDs | `500` |
| Duplicates | `0` |
| Runtime exclusions | `1` |
| Correct | `67` |
| Denominator | `499` |
| Aggregate | `0.1342685370741483` |

Run1 category profile:

| Category | Correct | Incorrect | Excluded | Usable denominator | Rate |
|---|---:|---:|---:|---:|---:|
| `single-session-user` | 16 | 53 | 1 | 69 | 23.19% |
| `multi-session` | 16 | 117 | 0 | 133 | 12.03% |
| `single-session-preference` | 4 | 26 | 0 | 30 | 13.33% |
| `temporal-reasoning` | 8 | 125 | 0 | 133 | 6.02% |
| `knowledge-update` | 15 | 63 | 0 | 78 | 19.23% |
| `single-session-assistant` | 8 | 48 | 0 | 56 | 14.29% |

The single runtime exclusion was the invalid-ciphertext runtime path recorded during the run. It was excluded from the denominator rather than silently dropped.

### 5.2 Run2 — headline-only metrics by explicit waiver

Source artifact:

- `tests/benchmark_results/harness_parity_baseline/run2/headline_summary.json`

Run2 headline metrics:

| Metric | Value |
|---|---:|
| Rows | `500` |
| Unique IDs | `500` |
| Duplicates | `0` |
| Correct | `73` |
| Total | `500` |
| Aggregate | `0.146` |
| Runtime exclusions | `0` |
| `raw_artifacts_available` | `false` |
| `rerun_forbidden` | `true` |

Run2 raw artifacts and per-category counts are unavailable. The completed run2 files were lost when the backend container's ephemeral `/tmp/opencode` state disappeared before repo copy-back. The user explicitly waived another run2 rerun because a full rerun would waste roughly 48 hours, so this completion artifact uses only the surviving headline numbers and does not create or imply `run2/results.jsonl` or `run2/summary.json`.

No run2 category counts are invented in this artifact.

---

## 6. Stability Gate and Anchor

Source artifact: `tests/benchmark_results/harness_parity_baseline_stability.md`

Aggregate stability computation:

```text
abs(0.1342685370741483 - 0.146) = 0.011731462925851695
```

The aggregate delta `0.011731462925851695` is within the Task 8 threshold `0.02`, so the aggregate stability gate passed.

Anchor computation:

```text
(0.1342685370741483 + 0.146) / 2 = 0.14013426853707415
```

Declared aggregate baseline anchor: `0.14013426853707415`

Task 8 decision: `declare-new-w1-anchor`

Category deltas are explicitly unavailable on the authorized headline-only run2 path. The only available per-category raw profile is run1, so run1 categories are used for W1 priority interpretation only, not as a per-category stability declaration.

No run3 was started because the user forbade another rerun and aggregate stability passed.

---

## 7. Binding W1 Priority Refresh

Source artifact: `tests/benchmark_results/harness_parity_oracle_wave_priority.md`

Oracle disposition: `binding-priority-refresh`

Binding W1 priority summary:

- `temporal-reasoning` at `8/133` and `multi-session` at `16/133` are the primary evidence-attention categories for W1.
- All categories remain protected. W1 should not optimize only for the weakest categories at the expense of regression coverage.
- `knowledge-update`, `single-session-preference`, and `single-session-assistant` are secondary/protected coverage categories.
- `single-session-user` is the best surviving run1 category and should be treated mainly as a regression guard.
- The oracle review is interpretive priority guidance only; it is not additional measurement evidence and does not authorize implementation outside W1 prompt-surface scope.

Category-specific binding disposition:

| Priority | Categories | W1 emphasis |
|---|---|---|
| P0 | `temporal-reasoning`, `multi-session` | Main evidence-attention focus; do not omit from probes or smoke samples. |
| P1 | `knowledge-update`, `single-session-preference` | Secondary lift focus and non-regression coverage. |
| P1-protected | `single-session-assistant` | Protect and observe; no assistant-extraction policy change under W1. |
| P2-protected | `single-session-user` | Regression guard. |

---

## 8. W1 Plan Patch Summary

Source artifact: `tests/benchmark_results/harness_parity_downstream_patch_check.md`

The W1 plan patch propagated the new baseline without changing implementation scope:

- Measurement path pinned to `tests/longmemeval/parity_harness.py:parity_evaluate_single()`.
- W1 implementation remains confined to `orchestrator/memory/injection.py`.
- Historical `10.4%`, `49/473`, and `10.36%` figures are non-actionable Wave 0 references only.
- `harness-parity-shipped` is the baseline anchor tag for W1+ measurement.
- `pre-wave-1` remains W1's rollback target.
- Run2 is documented as headline-only; per-category run2 values must not be invented.

The patch did not authorize retrieval, extraction, schema, producer, reranker, benchmark-adapter, roadmap, or memory-store work.

---

## 9. Downstream Guardrails

Source artifact: `tests/benchmark_results/harness_parity_downstream_patch_check.md`

Guardrail result: `proceed-downstream-patches-clean`

Forbidden-path status from Task 11:

| Path | Status |
|---|---|
| `orchestrator/memory/**` | Clean |
| `tests/longmemeval/**` | Clean |
| `docs/MEMORY_UPGRADE_ROADMAP.md` | Clean |
| `.sisyphus/plans/feature-matrix-review-fixes.md` | Clean |
| `frontend/next-env.d.ts` | Clean |

No corpus data was staged or committed. No new git tag was created. The run2 directory contains only `headline_summary.json`; no raw run2 `results.jsonl` or full `summary.json` was fabricated.

---

## 10. Failure Defects and Operational Lessons

These issues were observed during the full parity-baseline process and are carried forward as defects or risks, not as blockers to this completion artifact.

1. **Wrong URL footgun**  
   `tests/longmemeval/ingest.py` still references `longmemeval_s.json`, which returned 404 historically. The executable cleaned corpus path is `longmemeval_s_cleaned.json`; the legacy path was not edited in this plan.

2. **Markdown LSP unavailable**  
   Markdown artifact diagnostics cannot run in the current LSP setup. Existing TRIAGE evidence records `Error: No LSP server configured for extension: .md`.

3. **Docker `/tmp/opencode` artifact loss**  
   Completed run2 raw artifacts were lost when the backend container restarted or was recreated before repo copy-back, wiping the container-local `/tmp/opencode` tree.

4. **Invalid ciphertext runtime exclusion**  
   Run1 encountered one invalid-ciphertext runtime error. It was counted as a runtime exclusion, leaving denominator `499` from `500` submitted rows.

5. **Long-run output persistence risk**  
   Multi-day benchmark runs must not rely on container-local temporary output as the sole artifact location. Future runs should stream or periodically copy artifacts to host storage, or use a persistent mounted output path.

6. **Non-blocking infra/backfill issues**  
   The infrastructure preflight recorded non-blocking worker decryption noise and backend skill backfill errors. PostgreSQL, pgvector, Redis, backend health, and arq worker readiness still passed, so these did not block the parity baseline.

---

## 11. Final Scientific Record

This artifact establishes `0.14013426853707415` as the post-parity, pre-W1 aggregate anchor under the parity-fixed harness. The anchor is based on run1 full raw data plus run2 headline data under explicit user waiver. It must not be compared to historical `10.4%` / `49/473` / `10.36%` Wave 0 references as anomaly-vs-threshold logic; those numbers are historical, non-actionable context only.

Downstream W1 work should proceed against the declared anchor, keep implementation confined to `orchestrator/memory/injection.py`, protect all categories, and treat `temporal-reasoning` plus `multi-session` as the primary evidence-attention categories.

**FINAL_ARTIFACT_DECISION**: `declare-new-w1-anchor`
