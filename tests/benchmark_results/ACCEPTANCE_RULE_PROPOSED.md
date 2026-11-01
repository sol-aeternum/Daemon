# LongMemEval dev-subset acceptance rule

**Status: Ratified by the owner on 2026-10-28.** Rule version:
`2026-10-28-official-v1`. The existing qualification checks and numerical paired-noise,
category and abstention thresholds below are retained without exemptions.
One owner-approved amendment, `2026-10-31-official-v2`, applies only to plans that
explicitly bind it (see [Amendment](#amendment-2026-10-31-official-v2)); every other
use keeps v1. A second, `2026-11-01-paired-sign-v3`, has owner-approved parameters
but no implementation yet, so no plan can bind it (see
[Amendment v3](#amendment-2026-11-01-paired-sign-v3)).

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

## Amendment 2026-11-01-paired-sign-v3

Status: **parameters owner-approved on 2026-11-01; not yet bindable.** v3 has two
plan modes:

- **Pilot mode** may be bound once the k-sample harness and the paired test
  below are implemented, reviewed and merged, and the pilot run itself is
  separately approved. It produces a pilot report, never an acceptance
  verdict.
- **Candidate mode** may be bound only after that, and only when the plan pins
  a passed pilot report (see Pilot requirement).

Until then every campaign stays on v1 or v2.

B2a implements test-injected k = 3 harness sampling and guard receipt identity,
not a production selector. `Campaign(..., layout=...)` is outside the persisted
plan; all existing production plan modes still default to LEGACY. Synthetic
tests cover completed samples, majority rows and stop-without-replay failures.
B2b still owns v3 approval/pins, payload and hold arithmetic, retry ledgers,
launch, bridge registration and finalisation. In particular the existing payload
reserves only one slice per stage, not three sampled LLM calls; B2a must not be
used to launch a v3 campaign.

**Scope.** Evaluate-only campaigns only. v3 builds on v2 and keeps v2's §1.6
change. Ingest-change campaigns are not covered: they stay on v1 or v2 in full,
with one answer sample per question and `max(4, 2G)`.

**Samples.** For each question, every arm makes k = 3 answer calls. All three use
the identical request and route as v1: the pinned config (temperature 0, seed 42)
and §2's identical model and provider, so comparability is unchanged. Samples can differ only through serving
nondeterminism. Each sample is judged separately by the official judge, with the
generic judge kept as the secondary series. Separate judging means majority
scoring also averages over judge variability on near-identical answers. The
sample index is ledger metadata only; it never enters the prompt or the provider
request.

The generic series maps each sample to binary strict (`correct` = 1;
`partially_correct` and `incorrect` = 0) and reports the majority of those. It never sets an accuracy outcome or replaces the
official series. The existing generic/official disagreement validity gate still
applies, per sample (see Coverage).

**Coverage (replaces §1.7's single-verdict wording for v3 plans).** A run is
valid only if every frozen ID has exactly 3 completed answer samples, each with
one binary official verdict: 150 sample verdicts and 50 majority labels. Summaries
report both levels (sample `judged == total == 150`, majority
`judged == total == 50`, `missing == 0`). Results stay one row per question, each
with exactly 3 sample records. The implementation extends the offline coverage
check to validate the sample records. Question-level invariants (result count,
paired totals, accuracy bounds, §1.6) use the 50 majority rows. The per-sample
gate below uses the 150 sample records.

- A failed answer call may be retried only as the plan's retry policy allows.
  Today that is bounded HTTP 429 retries, where a plan pins them. If any sample
  has no completed answer after that, the run is invalid.
- A missing judge verdict on a saved answer is reconciled from that saved
  answer, as §1.7 already requires. Acceptance stays blocked until then.
- A majority is never taken from fewer than 3 labels.
- The code's generic/official strict-disagreement gate (currently ≤ 20%) is
  computed per sample.

**Outcome.** A question's strict outcome is the majority of its 3 official
labels, which cannot tie. F, category and abstention checks, including v2's
§1.6, use majority labels.

**§3 criterion 1, replaced.** Compare the candidate with the paired comparator
(control A0) on majority labels. Let *w* be the questions the candidate gets right
and the comparator wrong, and *l* the reverse. Criterion 1 holds only if both:

- net gain *w* − *l* ≥ 3;
- the one-sided exact sign test P(X ≥ *w*), X ~ Binomial(*w* + *l*, 0.5), is
  ≤ 0.05.

Criteria 2–4 (category loss at most 2, no net abstention loss, both modes) are
unchanged. v3 is stricter than v1 for some outcomes: with *l* = 0 it needs
*w* ≥ 5, while v1 with F = 0 accepts a net gain of 3.

**Candidate shape.** A candidate-mode plan has exactly three control arms, A0,
A1 and A2, at the control source, plus one candidate arm, C0. A0 is the
comparator.

**Calibration.** For every control pair, run the test with each arm as the
candidate in turn (6 ordered comparisons for 3 controls). If any meets
criterion 1, the campaign result is "investigate", not accept. F is still
computed and reported, but no longer sets the threshold.

**Record (adds to §4 for v3 plans).** Every v3 result file records its plan mode,
and per-sample and majority official labels per question.

- **Pilot mode** also records the four pilot rates and the pass or fail result.
- **Candidate mode** also records *w*, *l*, the sign-test p-value, every
  control-pair calibration result, and the identity and hash of the passed pilot
  report its plan pins.

**Reason.** With F ≈ p·n, the `max(3, 2F)` bar stays at a net gain rate of about
2p at any sample size. An effect below that rate fails however large the dev set
is. In Phase 3B ([report](memory_phase3b_2026-11-01.md)), C0's 8% net gain
faced a bar of about 12% (2p, with p ≈ 6%). The sign test's required margin grows
with √(*w* + *l*) rather than with n, so consistent effects become decidable as the
dev set grows. In Phase 3B, retrieved
context was identical across controls. An offline pass over the retained raw
answers (not in the committed evidence index) found that byte-identical answers
never received different official labels across control runs (0 of 74
question-pairs), and every control flip came with a different answer. That is
consistent with answer sampling being a major source of noise. It does not
separate answer sampling from judge variability on differing or near-identical
answers, which separate per-sample judging also averages over.

**Pilot requirement.** Before any candidate-mode plan binds v3, a separately
approved pilot-mode run must complete with exactly two control arms, A0 and A1,
at the control source with k = 3. It reports, on official labels:

- **matched-sample flip rate** (the gate's per-sample rate): the share of the
  150 (question, sample i) pairs where A0's sample i and A1's sample i differ;
- **majority-label flip rate:** the share of the 50 questions whose majority
  labels differ;
- **all-cross-pairs flip rate,** report-only: the share of the 450 pairs
  (A0 sample i, A1 sample j) that differ;
- how often a question's 3 samples within an arm are byte-identical.

The pilot passes if and only if the majority-label flip rate is strictly below
the matched-sample flip rate. The other reported figures have no role in the
pass decision. Equal rates fail, because they show no reduction. For
example, one question with A0 = [0,0,0] against A1 = [1,1,1], one with both arms
at [0,0,1] and 48 at [0,0,0] give 1/50 = 2% against 3/150 = 2%, which fails.
Otherwise v3's candidate mode is not bound without revisiting k or the sample
design.

**Limitations.** Record these with any v3 result:

- The sign test treats questions as independent paired units. Questions that
  share haystack sessions, and repeated candidate selection on the same dev set,
  can make its nominal 0.05 optimistic.
- Majority-of-3 correctness is a different estimand from single-answer
  production quality. A v3 acceptance is evidence about the candidate's effect
  under majority scoring, not a measured production accuracy.
- Earlier results are not rescored. The v2 rejection of the noted-dates
  candidate stands.

This amendment authorizes no run. Each v3 run needs its own approval, which states
its budget and every restore, including each arm's standard baseline restore.
Dev-set expansion and holdout use also need their own approval.

## 4. Record

Each accepted or rejected candidate gets a dated file in `tests/benchmark_results/`. It holds both
manifests, the per-question paired table, *F*/*G* and the rule version used. Spend and wall time
come from the manifests.
