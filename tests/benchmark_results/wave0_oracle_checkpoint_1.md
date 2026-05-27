# Wave 0 — Oracle Checkpoint 1

**Date:** 2026-04-23
**Task:** 14. Run Oracle checkpoint on attribution residual before full validation
**Verdict:** **no-go**
**Task 15 allowed to proceed:** **No**
**Status:** **UPDATED** — stale blocked-state checkpoint replaced with current residual-based decision

---

## Inputs Reviewed

- `tests/benchmark_results/wave0_variance_attribution_results.md`
- `tests/benchmark_results/wave0_variance_attribution_design.md`
- `tests/benchmark_results/wave0_attribution/abl1_deterministic/longmemeval_checkpoint.json`
- `tests/benchmark_results/wave0_attribution/abl1_deterministic/longmemeval_score.json`
- `tests/benchmark_results/wave0_attribution/abl2_residual/longmemeval_checkpoint.json`
- `tests/benchmark_results/wave0_attribution/abl2_residual/longmemeval_score.json`
- `.sisyphus/plans/wave-0-baseline-reproducibility-lock.md` (Task 14 only)

## Decision

Oracle checkpoint returns **no-go**. The current attribution evidence is now sufficient to make the gate decision, and that decision is still to block Task 15 because the measured residual remains above the Wave 0 validation bar.

## Rationale

1. The earlier blocked-state rationale is stale. The current attribution report now marks Oracle checkpoint as **READY**, and both `ABL-1` and `ABL-2` have completed with score artifacts.
2. The key current measurement is the post-wave0 residual spread between the two deterministic runs: **6.0pp** (`ABL-1` **28.0%** vs `ABL-2` **34.0%**). That is directly measured from completed artifacts, not inferred.
3. Wave 0 validation later requires aggregate spread **<=3pp**. A directly measured **6pp** swing between two back-to-back deterministic runs is already above that gate, so Oracle cannot justify advancing to Task 15.
4. The current attribution report identifies the remaining residual as **embedding nondeterminism** and describes it as **irreducible without embedding provider changes**. That unresolved source would carry forward into validation.
5. The report's remaining caveat is narrower than before: `ABL-3` and `ABL-4` were not executed, so the **~4pp** answer-temperature contribution is still **inferred** from the pre-wave0/post-wave0 delta rather than isolated experimentally. That caveat does **not** change the core gate result because the blocking **6pp** residual is directly measured.

## Remaining Caveats

1. **Directly measured:** pre-wave0 spread **10pp**, post-wave0 residual **6pp**, embedding nondeterminism **6pp**.
2. **Inferred, not directly measured:** answer-temperature contribution **~4pp**. `ABL-3` and `ABL-4` remain unexecuted.
3. The attribution is therefore strong enough for a gate decision, but not a full four-run confirmation of every contributing source.

## Exact Next Action

1. Do **not** start Task 15 validation.
2. Feed the measured blocker back into the Wave 0 implementation/strategy loop: the remaining unresolved source is embedding nondeterminism that still yields a **6pp** residual.
3. If stronger attribution confidence is still desired, run `ABL-3` and `ABL-4` later to confirm the inferred answer-temperature contribution — but that confirmation is secondary to the current no-go.

## Oracle Gate Result

**No-go.** Task 15 must not proceed because the current measured residual (**6pp**) exceeds the later Wave 0 validation gate (**<=3pp**).
