# M2 exposure gate pre-registration (#601)

**Status: owner-ratified 2026-11-05; gate code not yet frozen; nothing has run.** The owner's rulings are recorded below. No dev2 retrieval, exposure, answer or judge output informed this document. Running M2 still needs the dependencies below, the frozen gate code and the owner's go-ahead; this document doesn't authorise the run.

## Dependencies

All of these must hold before freeze:

1. **Labels:** dev2 labels owner-spot-checked, with corrections applied.
2. **Route:** the Block D (M1.5) route chosen and attested.
3. **Snapshot:** the dev2 snapshot qualified under the existing `baseline_qualified` rules, with its SHA-256 and fingerprint recorded.

## Arms, on the dev2 snapshot

| Arm | Definition | Role |
|---|---|---|
| Baseline | Production, k=5 | Control |
| Candidate | `multi_query`, additive merge, quota 3, at most 3 sub-queries, Block D route, reasoning none or minimal | Gated |
| Round-robin | The same sub-queries, round-robin merge (evaluation-only) | Reported only; cannot pass or fail the gate |

Sub-queries come from **one** generation pass of `PROMPT_V2` across the 100 dev2 questions, pinned to a file with its hash before any measurement. No regeneration after seeing results. Both merge arms read that same file.

## Metric

**Strict case exposure:** a question counts as exposed only when **all** of its labelled target memories are rendered. It's computed over **answerable** questions only (`answerable_from_store=true`). Questions labelled not answerable are reported separately as the retention rate and are outside the denominator. The render budget is the production default and is pinned at freeze.

## Gates

| # | Gate | Rule |
|---|---|---|
| 1 | Gain | Newly fully-exposed questions (candidate − baseline) ≥ max(5, 10% of answerable questions). **The formula is frozen.** Its value is computed unrounded from the final spot-checked labels and recorded before any retrieval. |
| 2 | No loss, on dev2 | No answerable question loses any exposed target. Verified on the dev2 run, not assumed. |
| 3 | Baseline subset, unit test | Under the render budget, the baseline's rendered set is a subset of the candidate's (additive merge, end-first trimming). Replaces the dev-50 coverage gate. This is a code test and uses no database. |
| 4 | Abstention | Over the 6 dev2 abstention questions, count only plausible false-premise items rendered by the candidate and not by the baseline. **2 or more of the 6 flags for owner review, not auto-reject.** Both arms' absolute counts are reported too. |
| 5 | Latency | Trigger rate and added p50/p95 latency reported from the Block D route. Report only. |

## Owner rulings, 2026-11-05 and 2026-11-06

| Question | Ruling |
|---|---|
| Gate 1 threshold | Freeze the formula; compute it from final spot-checked labels before any retrieval. |
| dev-50 gate | Dropped. Replaced by the unit test in gate 3; gate 2 stays on dev2. |
| Run limits | See below. |
| Questions with no decomposition trigger | Count as baseline, unchanged, and stay in the denominator. |
| Spot-check error rate | Reported only; it doesn't change the gate margin. Labelling still stops above 20%. |
| Gate 4 threshold | Count candidate-only items; 2 or more of 6 flags for owner review. Both arms' absolute counts reported. |
| Spot-check packet | Every `answerable_from_store=false` label is included in addition to the 15% seeded sample, marked as such. |
| Spot-check stop | The 20% stop is computed over the seeded sample only. The not-answerable additions get their own correction rate; above 20% flags for owner review. |
| Database | `daemon_lme_bench_dev2_final_remap` for M2. The original `daemon_lme_bench_dev2_final` holds Blocks C and E as untouched evidence, and the pilot's `daemon_lme_bench_dev2` stays untouched as evidence. |

## Run limits

- **Spend:** USD 0.50 for generation and retrieval embeddings together. Because the labelling and ingest caps are cumulative ledger ceilings, this one is enforced as the ledger total at M2's start plus 0.50, checked before each dispatch.
- **Restores:** 0.
- **Database:** `daemon_lme_bench_dev2_final_remap` for M2 only; `daemon_lme_bench_dev2_final` holds Blocks C and E as untouched evidence.
- **Side effects off:** no touches, no trust updates and no `retrieval_log` rows. The spend guard's own ledger rows are the one expected write.
- **Fingerprint before and after,** over all 100 dev2 users. `FINGERPRINT_TABLES` doesn't include `retrieval_log`, so its row count is checked separately before and after and must not change.

## Outputs

**If the gates pass:** a proposal for M3 (an answer campaign on dev2 under v2, with restore and budget counts) is drafted, not run.

**If they fail:** the report says what limited exposure, per question:
- **Decomposition:** generated sub-queries miss targets the labels show exist.
- **Retention:** targets are absent from the store.

## Retention diagnosis, in every case

Whether the gates pass or fail, the report gives a per-question diagnosis for every `answerable_from_store=false` label. It uses **extraction logs only**, with no spend, and is judged by reading the logs for that question's answer sessions.

| Category | Evidence in the logs |
|---|---|
| Assistant-turn content | The detail appears only in an assistant turn in the logged input, and no extracted fact carries it |
| Detail lost in atomic decomposition | A related fact was extracted, without the detail the question needs |
| Dedup or supersession | A fact carrying the detail was extracted, yet the label found no such memory |
| Other | Anything else, including detail not visible in the logged input |

**What the logs can and can't show:**
- `input_snippet` holds only the first 1,000 characters of each chunk. Detail past that point isn't visible and is reported as other, with that reason, instead of being assigned a cause.
- `dedup_results` holds per-chunk counts, not per-fact outcomes. Dedup or supersession is therefore an inference from a fact being extracted and then missing, not a recorded event.
- A failed chunk writes no log row.
- Benchmark text stays outside Git. The committed report carries question IDs, categories and counts.

## Boundaries

- Small sample of 100 from one dataset; a pass is indicative and does not show general capability.
- Exposure is not answer quality. Only M3 would measure answers.
- The dev2 extraction cap (4,000) differs from the Phase D baseline (2,000); see `extraction_truncation_audit_2026-11-05.md`.
- This document enables nothing: no route approval, no flag change, no production effect.

## Amendment, 2026-11-07 (owner ruling)

- Block C's stored times were found at ingest wall-clock on all 6,959 memories, not session dates.
- M2 runs on a remapped copy so temporal windows and recency see session time, comparable with the 2026-11-03 sweep's historical clock.
- The original stays untouched as evidence. No re-extraction: relative-date facts stay unanchored (#601), and dedup decisions stand as made at ingest.
- `_abs` questions are excluded from the exposure denominator by ID; gate 4 covers them.
- The labeller is v2, frozen separately, and the gate 1 value is computed from v2's final spot-checked labels. The gate 1 formula, `max(5, 10% of answerable)`, is unchanged; its input is the answerable non-`_abs` questions, and the computed value is recorded before retrieval. The USD 0.50 cap and zero restores are unchanged.
