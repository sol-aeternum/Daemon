# LongMemEval dev-subset acceptance rule

**Status: Ratified by the owner on 2026-10-28.** Rule version:
`2026-10-28-official-v1`. The existing qualification checks and numerical paired-noise,
category and abstention thresholds below are retained without exemptions.

**Primary metric:** official category/abstention-aware strict accuracy, with the
unchanged generic strict series retained as a secondary regression report. This is
the declared Azure/OpenRouter + seed-42 implementation of the pinned official
prompts, not a claim of identical upstream serving. See
[Phase 1 measurement evidence](measurement_phase1_2026-10-28.md).

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
   investigation, not acceptance.

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

## 4. Record

Each accepted or rejected candidate gets a dated file in `tests/benchmark_results/`. It holds both
manifests, the per-question paired table, *F*/*G* and the rule version used. Spend and wall time
come from the manifests.
