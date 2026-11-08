# M2 exposure gate pre-registration (#601)

**Status: owner-ratified 2026-11-05; gate code not yet frozen; nothing has run.** The owner's rulings are recorded below. No dev2 retrieval, exposure, answer or judge output informed this document. Running M2 still needs the dependencies below, the frozen gate code and the owner's go-ahead; this document doesn't authorise the run.

## Dependencies

All of these must hold before freeze:

1. **Labels:** dev2 labels owner-spot-checked, with corrections applied.
2. **Route:** the Block D (M1.5) route chosen and attested.
3. **Snapshot:** the dev2 snapshot qualified under the existing `baseline_qualified` rules, with its SHA-256 and fingerprint recorded.

**Owner exception, 2026-11-08:** this campaign may use the completed, historically
remapped corpus as explicitly **disqualified exploratory evidence**. Its manifest
remains `baseline_qualified=false` (`code_changed_mid_run`), with one explicitly
accepted extraction-failed session. This exception does not qualify the snapshot
or change the gates, route controls, fingerprints, USD 0.50 cap or zero restores.

## Arms, on the dev2 snapshot

| Arm | Definition | Role |
|---|---|---|
| Baseline | Production, k=5 | Control |
| Candidate | `multi_query`, additive merge, quota 3, at most 3 sub-queries, Block D route, reasoning none or minimal | Gated |
| Round-robin | The same sub-queries, round-robin merge (evaluation-only) | Reported only; cannot pass or fail the gate |

Sub-queries come from **one** generation pass of `PROMPT_V2` across the 100 dev2 questions, pinned to a file with its hash before any measurement. No regeneration after seeing results. Both merge arms read that same file.

## Metric

**Strict case exposure (final v3):** a question counts as exposed only when **every target group** has at least one rendered member. Group members are interchangeable alternatives, not additional requirements. It's computed over **answerable non-`_abs`** questions only (`answerable_from_store=true`). Questions labelled not answerable are reported separately as the retention rate and are outside the denominator. The render budget is the production default and is pinned at freeze.

## Gates

| # | Gate | Rule |
|---|---|---|
| 1 | Gain | Newly fully-exposed questions (candidate − baseline) ≥ max(5, 10% of answerable questions). **The formula is frozen.** Its value is computed unrounded from the final spot-checked labels and recorded before any retrieval. |
| 2 | No loss, on dev2 | No answerable non-`_abs` question loses an exposed target group. Losing a redundant alternative is not a loss if another member still renders; losing the last member of even a partially exposed group is a loss. Verified on the dev2 run, not assumed. |
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

## Final v3 amendment, 2026-11-08 (owner-approved; supersedes v2 labelling requirement)

- Final M2 requires **only `dev2-labelling/3`** labels across all 100 questions.
  Historical v1/v2 labels remain readable for provenance, but threshold,
  measurement and gates refuse them (including mixed-version sets).
- `target_groups` replaces `memory_ids`: each group is nonempty and contains
  interchangeable supplied IDs (paraphrases or same-session memories providing
  the same needed fact/date via `valid_from`). Any member satisfies a group;
  strict exposure requires every group. The benchmark aggregates production
  per-ID rendered flags; `orchestrator/eval/exposure.py` keeps its flat-ID API.
- `needs_memory_date` is a required boolean. Dates only in stored `valid_from`
  count as answerable; keep the temporal-cue-dropped-at-extraction note. Exposure
  remains a rendered-ID diagnostic, **not** a claim that dates were rendered or
  that an answer is correct. No production renderer or date-display change.
- Gate 1's formula and non-`_abs` denominator are unchanged. Gate 2 compares
  exposed groups, including partial cases, rather than redundant alternatives.
  Measurement records group membership, exposed zero-based group indices and
  the date flag. Threshold/measure/gates use `/2` schemas so old flat-target
  outputs cannot be mistaken for final group-based results.
- Normal independent review and refreeze of labeller and M2 code precede paid
  work. The primary alone later relabels the 14-question batch under a USD 0.05
  incremental cap. The original seeded spot-check carries only if **each seeded
  question's** v3 member union is a superset of its v2 targets; otherwise report
  only changed seeded questions. The pure comparison helper is documented in
  `tests/benchmark_longmemeval/dev2_labelling.md`; no evidence reads here.
- The primary then completes final 100-question labels, computes/records the
  exact threshold before retrieval, and runs M2 only after those prerequisites.
  **Final v3 freeze: no further labeller changes regardless of outcome.**
- All existing fingerprint, lineage, run-lock, route/attestation, cumulative
  ledger/unknown-dispatch, side-effect, USD 0.50 M2 spend and zero-restore
  constraints remain unchanged. This source-only change executes no campaign.

## M2-only lexical amendment (owner-approved, 2026-11-09)

- Measurement may explicitly select `--retrieval-mode lexical` for the existing
  unembedded corpus. The default remains `cached_embeddings`; an empty selector
  never selects lexical mode automatically. This repairs measurement only: no
  generation, labelling, reembedding, route enablement, provider call or restore.
- Before any lexical measurement **or resume**, admit exactly all 100 expected
  question users. Aggregate over all their memories, without status/tier filters:
  every `embedding` and `embedding_model` must be SQL NULL. Include zero-memory
  users via a left join; aggregate memory count must agree with the fingerprint.
  Missing/duplicate/foreign users, non-null vectors/models or unavailable evidence
  refuse before retrieval. Repeat admission on resumed completed journals too.
- The benchmark adapter calls production `retrieve_memories_for_text` with
  `query_embedding=None`, `allow_query_embedding=False`, `touch_results=False`
  and logging disabled. No synthetic vectors or embedding receipts are created.
  Production's internal empty-list representation of absent embeddings remains
  its native lexical path, not a numerical dummy vector.
- Baseline k=5, component depth 10, quota 3, ID de-duplication, scoring, temporal
  filtering/entity expansion, locality filtering, L0/summaries, UUID added-row
  replacement and production rendering stay unchanged. Merge uses production
  `merge_memories`; round-robin remains report-only. No-trigger questions remain
  identical to baseline. Component retrieval retains its shared deadline,
  all-or-nothing fallback and owned cancellation/draining.
- **Historical recency ruling:** both lexical baseline and components inherit
  each question's historical reference date through `RECENCY_REFERENCE_TIME`.
  Reset the context after success, error, timeout or cancellation. This explicitly
  resolves the prior baseline/component clock asymmetry; embedding mode is not
  changed by this amendment.
- Bind retrieval mode, adapter version, corpus admission, fingerprint and ledger
  observation into the lexical measurement journal. Reject cross-mode resumes,
  incompatible adapters, untagged-as-lexical journals and any lexical journal
  containing embedding records. Mode/version propagate to measure, gates and
  sanitized reports. Historical untagged embedding evidence stays embedding-only.
- Preserve the frozen labels/subqueries, generation receipts and original start
  marker plus USD 0.50 cap. No re-anchoring. Lexical measurement requires no open
  holds, compares ledger actuals/holds/counts and a reservation-row digest, and
  checks unchanged fingerprint/retrieval-log/ledger observations after success
  **and failure**, once producers drain. Missing verification prevents success.
- Normal independent review/refreeze precedes live resumption. The final v3
  labeller remains frozen regardless of outcome; this source-only amendment does
  not execute any benchmark or inspect real corpus/evidence.
