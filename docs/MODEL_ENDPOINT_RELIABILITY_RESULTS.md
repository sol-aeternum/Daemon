# GLM endpoint reliability — live pilot results

Date: 28 September 2026. **Measured execution evidence; human quality adjudication
pending. No production route or default-model activation.**

Protocol and approval history:
[MODEL_ENDPOINT_RELIABILITY_PROPOSAL.md](MODEL_ENDPOINT_RELIABILITY_PROPOSAL.md).
Original single-endpoint results remain separate in
[MODEL_ROSTER_PILOT_RESULTS.md](MODEL_ROSTER_PILOT_RESULTS.md).

## Main finding

The same-model fallback path recovered one real Inceptron timeout through Together.
All 24 logical attempts produced schema-valid terminal responses. This demonstrates
one successful recovery, not a statistically established availability improvement:
the two single-endpoint control arms also completed all eight attempts each.

Exact model: `z-ai/glm-5.3-flash`. Primary provider: `inceptron/fp8`;
alternate: `together`. Each outbound dispatch was individually pinned, with
provider-managed fallback disabled and independent reservation/settlement.

## Results

Four frozen utility cases (U01/U03/U05/U08), two repeats per arm, interleaved in a
recorded schedule. Latencies include runner/settlement/pacing overhead. With only
eight observations per arm, nearest-rank p95 is the sample maximum.

| Arm | Terminal responses / logical attempts | Provider dispatches | Recovered failures | Schema valid | Median / p95 seconds | Account charge USD |
| --- | --- | --- | --- | --- | --- | --- |
| Inceptron only | 8 / 8 | 8 | 0 | 8 / 8 | 3.10 / 7.95 | 0.000344 |
| Together only | 8 / 8 | 8 | 0 | 8 / 8 | 0.92 / 8.61 | 0.001381 |
| Inceptron then Together | 8 / 8 | 9 | 1 | 8 / 8 | 3.66 / 48.78 | 0.002362 |

There were no cooldown skips, avoided primary dispatches, capacity stops or
logical deadline overruns during the comparison. Original qualification failures
remain in the experiment record and are excluded only from this arm-only table.

### Observed recovery

`rel-U05-primary_then_alternate-r2`:

1. Inceptron timed out at its 45-second endpoint bound (measured 45.04 seconds
   including cleanup). No response/usage was captured. The ledger retained the
   full USD 0.001986 bound.
2. Together completed in 3.72 seconds, with the exact model/provider attribution,
   valid schema and USD 0.000074 ledger charge.
3. The logical result arrived in 48.78 seconds, within the 90-second arm deadline.

The recovered response preserved Lisbon, March 2025, null employer and the explicit
employer-absence field. This is an AI-assisted observation, not a named human verdict.

## Quality limitations

Schema validity did not establish correctness. AI-assisted inspection identified:

- `rel-U05-primary_only-r1`: `since` was null and `absent_fields` was empty,
  violating U05-A1/A2.
- `rel-U05-alternate_only-r1`: omitted `employer` from `absent_fields`, violating
  U05-A2 despite returning a null employer.
- `rel-U08-alternate_only-r2`: returned an empty identifiers array and generic
  summary, losing the required identifiers, amount and status.
- U08 primary-only repeat 2 and primary-then-alternate repeat 2 preserved the
  order ID in the identifiers array but omitted it from the summary, contrary to
  U08-A2.

No semantic pass rate or cost-per-correct-answer ranking is certified. The frozen
rubrics remain unchanged; all machine-readable human verdict fields are pending.
Provider quantization, reasoning behavior and token usage can differ even under
the same model ID. This sample does not establish infrastructure independence,
long-term reliability, or a universally superior endpoint ordering.

## Accounting and qualification history

The original primary smoke exhausted a 64-token allowance. A separately approved
4,096-token qualification recheck returned HTTP 429. Together's smoke completed.
The user then approved using the prior successful exact-pinned Inceptron call
`O01-glm-flash-r2` as compatibility evidence while retaining the new 429 as
availability evidence. The source/call hashes and admission rule are recorded;
neither failed qualification call was replayed or relabelled successful.

| Accounting scope | Settled account charge USD |
| --- | --- |
| Original two smokes plus the distinct recheck | 0.001945 |
| All three comparison arms | 0.004087 |
| Entire reliability experiment | **0.006032** |
| Aggregate roster + reliability pilot | **0.044213** |

The reliability experiment used **28 of at most 35 authorized dispatches**;
USD 0.993968 remains under its USD 1 sub-cap. Aggregate remaining authorization
is USD 24.955787. PostgreSQL reconciliation found **187 reservations total,
zero open holds and zero overage**. The original USD 0.038181 baseline is intact.

Known provider-reported reliability cost totals USD 0.00210487, with two dispatch
costs unknown (the 429 recheck and timeout). This is a partial invoice subtotal,
not a substitute for the conservative ledger total. All account charges are known;
that does not imply all provider invoice costs are known.

## Verification and artifacts

- 309 integrated tests passed, including PostgreSQL, interruption/resume,
  real routing-context lifetime, amendment and evidence-admission regressions.
- Isolated basedpyright: zero errors. Pre-commit and scoped Bandit passed.
- Independent reviews accepted the runtime, runner fixes and amendment. The
  evidence-admission review's sole blocking test-fixture finding was already
  fixed and verified before execution; the reviewer found no admission-logic flaw.
- Existing repository-wide release-gate debt remains separate; this is not a
  whole-project release certification.

Private artifacts under `/tmp/opencode/roster-live-20260927/`:

- `reliability-state.json`: original identity, plan amendment, compatibility
  evidence, smokes, per-dispatch records and fixed schedule outcomes.
- `reliability-final-results.json`: reconciled ledger, arm metrics and provider
  cost completeness; artifact version remains distinct from the original scorer.
- `reliability-human-review.md`: all 24 tasks, frozen rubrics and final answers.
- `reliability-presmoke-*.json`: refreshed public qualification evidence.

The user accepted the recommendation on 28 September 2026 to retain bounded
same-model endpoint redundancy as a reliability mechanism; the approved scope is
recorded in [the proposal's post-experiment decision](MODEL_ENDPOINT_RELIABILITY_PROPOSAL.md#post-experiment-decision--approved-direction).
Production deployment, provider ordering and quality-based
promotion remain separate decisions. No further paid calls are required to finish
this approved experiment.
