# Phase 3A — offline selection/diversity candidate

Status: **implemented and verified offline; no benchmark execution or candidate
acceptance**. The owner approved this bounded implementation after
Phase 2 #602 merged as `ae50bb29`. This branch starts at that latest main commit.

## Hypothesis and single variable

Phase 2 found five failed dev questions whose stored answer facts were absent from
the saved memory prompt; one prompt spent two slots on paraphrases of one fact.
That evidence does not establish why candidates were omitted or prove diversity
will recover them. There are no historical query-matched ranking traces to replay.

The sole intervention is `DIVERSITY_PENALTY = 0.10`, compared with the unchanged
relevance-only selector (penalty zero). No sweep or alternate candidate is selected.
In `retrieve_memories`, immediately before its existing per-space cutoff:

1. Start with the unchanged score-qualified candidate list and its existing
   relevance-descending / ID-ascending order.
2. Select the highest relevance row first. For subsequent slots, maximize
   `original final_score - 0.10 * maximum positive cosine overlap with selected rows`.
   Ties use original relevance then ID. Cosine overlap is clamped to [0, 1].
3. Use only existing finite, nonzero stored vectors with explicit identical
   embedding identities and matching dimensions. Missing, malformed, nonfinite,
   zero or incomparable vectors contribute zero redundancy; no route is inferred
   and no embedding request is added.
4. Return the selected original rows in their original relevance/ID order. The
   stored/returned scores, content, quantities and provenance are not rewritten.

This is **soft prompt selection, not semantic fact equivalence or storage dedup**.
Distinct quantities and contradictory updates remain separate eligible rows; they
are not guaranteed a slot within a five-row capacity. Positive cosine can reflect
related-but-distinct facts, so this candidate can regress answer coverage. A
relevance gap greater than 0.10 cannot be reversed solely by the bounded penalty.

## Fixed boundaries

- Apply only when that retrieval pass has a query vector; lexical-only passes
  retain the existing relevance-only result even if stored rows contain vectors.
- Existing vector/BM25 fetch limits, candidate union, entity expansion, temporal
  filtering, minimum score 0.15, score weights and requested output limit stay fixed.
  Prompt injection still requests five facts. Explicit tool callers' larger limits
  remain supported; nothing is newly clamped to five.
- The outer multi-space merge still sorts only the already selected per-space
  rows. It does not admit unselected rows from `scored_candidates_out`. Diversity
  is per-space, not a claim of globally diverse multi-space output.
- Production and evaluation share `retrieve_memories_for_text` / `retrieve_memories`;
  no harness-only selector or question-ID special case exists.
- `build_memory_context()` and all public call signatures, formatter, reader cap,
  L0/summaries, local/source/user/model filters, trust/access side-effect controls,
  routes, API/schema/environment surface and dependencies are unchanged.
- No changes to task, durable-chat, fencing, account-generation, quarantine or
  memory-fence code. No deployment or runtime setting change.

The design review identified expanding the outer candidate pool as a possible
second variable. That proposal was rejected: the candidate replaces only the
existing per-space selection over its already eligible/scored pool. The outer
candidate union is untouched.

## Offline acceptance checks

`tests/memory/test_retrieval_diversity.py` uses synthetic facts/vectors and mocked
store/embedding IO only. It checks zero-penalty equivalence (IDs, order and rendered
context), slot refill within the existing pool, output ordering, deterministic ties,
bounded score displacement, negative-cosine handling, stored-vector formats and
invalid/unknown/mismatched-space cases. It also checks no fact/value merging and
exercises both actual production prompt construction and evaluation gather/render.

Integration checks compare query/store call counts, unchanged candidate limits and
scope arguments, lexical-only equality, unchanged score filtering and read-only
evaluation side effects. A multi-space regression ensures the outer merge cannot
reintroduce a higher-scoring row excluded by the per-space diversity selection.
Existing retrieval, ordering, logging and prompt/parity regressions remain required.

These tests prove bounded behavior, **not improved LongMemEval accuracy or semantic
equivalence**. Final-state lint/types/SAST/audit/tests, required project gates and a
fresh enforced read-only source review are required independently of benchmark
acceptance.

The first full gate run found one compatibility failure: the conservative writer
catalogue classifies any `.remove()` call as a possible filesystem write, including
the selector's local index-list removal. Replacing that operation with equivalent
pure list filtering avoids the ambiguity without changing the catalogue, manifest,
gate or selection algorithm. All other blocking gates passed in that run; final
verification of the repaired state then passed.

### Final offline verification

- 39 new synthetic regression cases; 295 affected selection/retrieval/parity/catalogue
  tests pass. The catalogue remains unchanged.
- All blocking managed project gates passed in 423 seconds: backend 7,371 passed /
  1,265 skipped / 14 warnings / 16 subtests; frontend 1,473 tests; cache-browser 30;
  types clean. Non-blocking inventories are unchanged; no gate was loosened.
- Fresh enforced read-only source review found no defects. It used the disclosed
  OpenAI-family fallback for OpenAI-authored work, not a cross-family review; the
  reviewer did not execute tests or inspect a live database. The subsequent local
  list-filter compatibility repair preserves the same unique-index behavior and
  was checked by the affected tests and full gates.
- Separate candidate branch/draft PR only. Current-head hosted checks and code
  review still govern any future merge, as do benchmark acceptance and owner
  clearance. No merge or deployment is part of this offline step.

## Benchmark execution is a separate next step

No inference, DB inspection/mutation/restore, ingest or holdout access is performed
for this implementation. Before any paid benchmark work:

- Agree the exact baseline/noise/candidate run envelope and calculate a conservative
  bound from frozen inputs, query embedding work and both judges' input/output caps.
  Carry all prior unknown-request reservations. Current cumulative returned-usage
  quote is $0.3030775 (not verified billing); reservation is $1.7019050. The cumulative
  evaluate-only authorization stays $10; it is not a spending target. Ingest and
  spending beyond that authorization need a new estimate and owner approval.
- Obtain explicit authority for the canonical benchmark snapshot restores; Phase 2's
  exact-container authorization covered read-only inspection, not restores.
- Use the qualified immutable Phase D snapshot, fresh run directories and fixed clean
  execution SHAs. Verify the marked benchmark DB and before/after fingerprints.
  Do not rebase/edit code mid-run or use state/code-pin overrides.
- Establish canonical reader-level noise on the unchanged control; saved-hypothesis
  judge repetitions alone do not supply it. Predeclare the paired comparator/noise
  calculation and reports before running, rather than selecting a favorable pair.
- Validate exactly the frozen 50 dev IDs with one binary official judgment per ID,
  matching complete evaluate/score summaries. Official strict remains primary,
  generic scoring secondary, under ratified `2026-10-28-official-v1`.
- Apply every qualification/comparability, gain/noise, category and abstention gate.
  Stop for approximately >20% judge disagreement, excessive category regression,
  implausible results, missing required evidence or scope/spend violations.
- This is embedding-mode-specific; explain that limitation under the rule's lexical
  clause. Historical B is summary-bearing/no-facts, not a qualified lexical baseline.
  Existing baseline category zeros and missing qualification evidence still block
  acceptance; this candidate cannot waive them or fix source-retention by selection.

No source expansion, historical-date adaptation, ingest, holdout validation, reader
cap change or production improvement claim is bundled into this candidate. Do not
merge/deploy it as an accepted memory gain solely because offline tests pass.
