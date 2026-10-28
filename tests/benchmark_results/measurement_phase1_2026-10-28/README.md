# Phase 1 partial judge-only evidence

See [the measurement record](../measurement_phase1_2026-10-28.md) for interpretation,
category/paired/repeated-judge tables, cost accounting and the stopped failure.

- Execution code SHA: `52774035201964da27f8a322252627eaa95467d3`.
- Frozen dev only; no new reader, retrieval, database or holdout calls.
- Five complete 50-row official runs, two complete 50-row generic runs, and one
  incomplete 36-row generic-B run: **386 returned responses**.
- `generic_b_1/run_manifest.json` preserves the uncertain pending call `c5e8278d`.
  No fabricated score file is supplied for the incomplete batch. The 14 missing
  outcomes include that uncertain request plus 13 unattempted rows.
- `artifact_inventory.json` hashes the copied original evidence; do not edit raw
  result/manifests to hide the failure or change the execution pin.
- `phase1-execution-ledger.json` preserves **$1.5595300** reserved, including the
  entire failed batch. **$0.2636950** is a returned-usage quoted-rate estimate,
  not verified billing; the failed call's billed status is unknown.
- Recovery paths in immutable manifests are original absolute local paths.
- Reader-cap diagnostic: **not run**. No acceptance decision or production gain.
