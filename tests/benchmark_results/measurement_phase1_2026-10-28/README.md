# Phase 1 measurement evidence (initial failure and authorized recovery)

See [the measurement record](../measurement_phase1_2026-10-28.md) for interpretation,
category/paired/repeated-judge tables, cost accounting and the stopped failure.

- Initial execution SHA: `52774035201964da27f8a322252627eaa95467d3`;
  recovery SHA: `cf62a34d9eeaee53bc6eb7d9f43303ae639a79e2`.
- Frozen dev only; no retrieval, database, ingest or holdout calls. Two new reader
  calls are confined to the owner-approved saved-context cap diagnostic.
- Five complete 50-row official runs, two complete 50-row generic runs, and one
  incomplete 36-row generic-B run: **386 returned judge responses** initially.
  The fresh authorized residual adds 14 disjoint B judgments, with exactly one
  explicitly marked new replacement. The cap diagnostic adds two reader and four
  judge responses: **406 responses returned; 407 requests attempted** overall.
- `generic_b_1/run_manifest.json` preserves the uncertain pending call `c5e8278d`.
  No fabricated score file is supplied for the incomplete batch. The 14 missing
  outcomes include that uncertain request plus 13 unattempted rows.
- `artifact_inventory.json` hashes the copied original evidence; do not edit raw
  result/manifests to hide the failure or change the execution pin.
- `phase1-execution-ledger.json` preserves the original stopped sequence and
  **$1.5595300** reservation. `phase1-recovery-ledger.json` carries it forward to
  **$1.7019050**, including the entire failed batch plus new calls. Final returned-
  usage quoted-rate estimate: **$0.3030775**, not verified billing. The original
  failed call's outcome/charge is unknown; its pending manifest remains unchanged.
- Recovery paths in immutable manifests are original absolute local paths.
- `reader_cap_a_1/` retains both new answers and all six call receipts. Raising
  the cap removed truncation but did not improve either strict judgment. No
  production cap change or memory improvement is accepted.
- `phase1-final-summary.json` provides complete paired/category/coverage results;
  the earlier partial summary is preserved as historical evidence, not overwritten.
