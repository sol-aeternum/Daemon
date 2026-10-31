# LongMemEval dev-subset acceptance rule

**Status: Ratified by the owner on 2026-10-28.** Rule version:
`2026-10-28-official-v1`. The existing qualification checks and numerical paired-noise,
category and abstention thresholds below are retained without exemptions.
One owner-approved amendment, `2026-10-31-official-v2`, applies only to plans that
explicitly bind it (see [Amendment](#amendment-2026-10-31-official-v2)); every other
use keeps v1.

**Primary metric:** official category/abstention-aware strict accuracy, with the
unchanged generic strict series retained as a secondary regression report. This is
the declared Azure/OpenRouter + seed-42 implementation of the pinned official
prompts, not a claim of identical upstream serving. See
[Phase 1 measurement evidence](measurement_phase1_2026-10-28.md).

The primary series is per-row `official_judgment`, summarized by
`phases.score.dual_judgments.official` (including `official_by_category` and the
official `abstention_subset` series alongside it). Legacy top-level strict scores
and `judgment` remain **generic**, not aliases for the primary metric. All strict
outcomes, paired gains, *F*/*G*, category and abstention checks below use the official
series; the generic series is reported separately, never substituted.

Ratification permits Phase 2 DB-backed failure classification, not candidate
acceptance without the required evidence. Known baseline zeros, the historical
empty-bin ABS placeholder, and B's summary-bearing/no-retrieved-facts status remain
explicit limitations; they are not silently waived or treated as a qualified
lexical comparison. Saved-hypothesis judge repetitions do not replace the full
canonical evaluate-only noise checks below. Missing qualification/noise evidence
continues to block acceptance. No ingest, holdout inference, source-expansion
design, or production cap change is authorized by this rule.

Scope: deciding whether a memory change (Waves 1–9 and later) improves the production memory prompt
surface, measured with the production-parity fact harness on the 50-question dev subset
(`tests/benchmark_longmemeval/fixtures/dev_subset.json`). This does not replace a full
LongMemEval_S run for any public claim.

## 1. A run is valid only if its manifest shows all of the following

1. `phases.ingest.status == "completed"`, and fewer than 1% of sessions ended
   `extraction_failed` (dev subset: at most 20 of 2,079).
2. `phases.evaluate.status == "completed"` and `phases.score.error_count == 0`.
3. `state_matches_ingest_at_start` and `state_unchanged_by_evaluate` are both `true`.
   `--allow-state-mismatch` was not used.
4. `baseline_qualified == true` and `disqualifications` is empty in every phase's manifest: no
   `code_pin_changes` (SHA or code content), no `--allow-state-mismatch`, no
   `--retry-failed-sessions`. Every `ingest_rollbacks[]` entry has
   `restored_pre_session_state: true`. An evaluate-only run also needs
   `ingest_source.baseline_qualified == true`.
5. `phases.evaluate.retrieval_modes` has a single key, and it matches the declared mode
   (`embedding:<model>` or `lexical`).
6. No category scores 0. Overall accuracy is not below 5% or above 95%. Either result triggers
   investigation, not acceptance. (Under the v2 amendment only, the zero-category
   condition applies to categories of at least 5 questions.)
7. Every baseline, canonical noise and candidate run covers **exactly the frozen
   50 dev question IDs**, once each, with one binary `official_judgment` (`correct`
   or `incorrect`) per ID. No duplicates, missing/extra IDs or invalid verdicts:
   `phases.evaluate.official_judgments` and
   `phases.score.dual_judgments.official` must both report
   `missing == 0` and `judged == total == 50`, consistent with the saved rows.
   Use the offline `require_complete_official_coverage` check in
   `tests/longmemeval/official_judge.py` with IDs from the frozen dev fixture to
   validate the rows; also check both manifest summaries against its result.
   A completed evaluate phase and zero **generic** error count do not establish
   official coverage: the harness intentionally preserves successful reader/generic
   evidence when the official judge fails, and official accuracy otherwise uses
   only observed labels. Incomplete coverage **blocks acceptance**, including
   noise/paired comparisons. Preserve completed calls and reconcile missing
   judgments from the saved hypotheses under existing spend authorization (ask
   first if not covered); do not replay readers or silently treat missing labels
   as incorrect, drop IDs, or waive coverage. Reconciled results must retain source
   hypothesis/context identity and complete coverage before use; saved-hypothesis
   rejudging does not replace required canonical noise runs.

## 2. Two runs are comparable only if

- `assert_substrate_match` passes (both are `fact`);
- these are identical: dataset `sha256`, `config_pin.sha256` (or the PR explains the pin change),
  answer and judge routes (model, temperature, seed, provider), the embedding mode, and the
  `parity_exceptions` ids;
- both report `phases.evaluate.provider_fingerprints`. The pinned Azure endpoint rotates
  `system_fingerprint` across serving nodes, so identical fingerprints are not required. The
  distribution is recorded so that a shift in serving backend shows up next to the scores, and the
  *F* noise measurement below already includes this variance;
- `memory_formatter.output_sha256` is identical, *unless* the change under test is the formatter
  itself, in which case that is the declared difference.

Runs from checkpoint v3 (one shared user, `haystack_scope`) and v4 (per-question users,
`benchmark_users`) differ in their `parity_exceptions` ids and are never compared.

Never compare against the historical 10.4%, 67.8% or 81.1% figures. They came from a harness that
did not build the production prompt.

## 3. Decision procedure

The sampling noise is large. With n = 50, one question is 2 points, and the standard error near
p = 0.5 is about 7 points. So comparisons are **paired by question** and thresholds are in
questions, not percentages.

**Retrieval, ranking or rendering changes.** These are changes that don't alter what ingest stores.
Run evaluate-only on the baseline ingest snapshot (`--from-ingest`, `bench_db.sh restore`). Measure
evaluate noise once per baseline: two evaluate-only reruns of the *unchanged* code on the same
snapshot. Let *F* be the number of questions whose strict outcome flipped.

**Ingest changes.** These cover extraction, dedup and storage, and need a new ingest. Ingest uses
the unseeded production extraction route, so measure ingest noise once per baseline: a repeat
baseline ingest plus evaluate, with the owner approving that spend. Let *G* be the strict-outcome
flips. If no repeat ingest exists, the change can't be accepted on the dev subset alone.

**Accept** a candidate when all of these hold:

1. Net strict gain ≥ max(3, 2 × *F*) questions for evaluate-only changes, or
   ≥ max(4, 2 × *G*) questions for ingest changes.
2. No category loses more than 2 questions net.
3. Abstention questions (`_abs`) do not lose any net. Wrongly answering an unanswerable question is
   a product-trust regression.
4. Both qualified-embedding and lexical runs move in the same direction, or the PR explains why the
   change targets only one mode.

**Reject or investigate** otherwise. A result inside the noise band is "no measured effect". That
is a valid finding to record, not a failure to rerun until it passes. Reruns of the candidate alone
until it clears the bar are not allowed.

## Amendment 2026-10-31-official-v2

Owner-approved on 2026-10-31 for the fresh historical noted-dates campaign (#601).
It is identical to v1 except §1.6: a category must score above 0 only if it has at
least 5 questions (`MIN_SANITY_CATEGORY`). Smaller categories may score 0. They are
still fully covered, because §1.7 requires one binary official verdict per question.
All other conditions are unchanged: overall accuracy bounds, comparability, the
decision procedure, the noise threshold and the category and abstention limits.

Reason: the first fresh campaign's A0 completed every call but scored 0/3 on the
IE-preference category, whose questions are rubric-judged. One of the three answers
had byte-identical memories and a near-identical answer to the qualified original
A0, yet the official judge flipped Yes to No. That is judge noise, not broken
scoring.

Scope: only plans whose fixed approval binds `acceptance_rule: 2026-10-31-official-v2`
(`orchestrator/eval/campaign_fresh.py` approval `2026-10-31-noted-dates-historical-v2`).
Those plans report this version in `paired-result.json`. The stopped v1 fresh plan
and all earlier campaigns remain under v1.

## 4. Record

Each accepted or rejected candidate gets a dated file in `tests/benchmark_results/`. It holds both
manifests, the per-question paired table, *F*/*G* and the rule version used. Spend and wall time
come from the manifests.
