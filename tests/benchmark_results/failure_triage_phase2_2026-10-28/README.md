# Phase 2 bounded DB evidence

See [the triage report](../failure_triage_phase2_2026-10-28.md).

- `cases.json`: all 16 failed first-pass official questions, each with A/A2
  observations (32 total), adjudicated evidence label/confidence/falsification,
  relevant DB facts and literal source excerpts. No labels are automatically changed.
- `db_evidence_manifest.json`: hashes/counts for full scoped DB exports retained
  in the registered task evidence directory, verified against qualified Phase D.
- `db_preflight.json`: benchmark marker, enforced read-only transaction, and exact
  expected/observed ingest fingerprint. No restore or mutation occurred.
- `db_post_gate_check.json`: a further read-only fingerprint after the completed
  full local gates still equals the qualified ingest state.
- `embedding_summary.json`: all 7,954 scoped memory rows have stored qualified-
  identity vectors and explicitly nonlocal locality; no embedding inference ran.

Original full exports remain outside the checkout at the task evidence location
`phase2-db-evidence/`. They contain public dev benchmark data, never production
data or encryption keys. The committed bundle minimizes copies to cited evidence.
The immutable Phase D dump plus frozen dev/checkpoint artifacts are the recovery
source; do not infer ranking traces from these records—matching retrieval logs
are absent in the ingest state and excluded from its canonical fingerprint.

The offline tests validate coverage, hashes/source references, excerpts and state
metadata without DB/credentials/provider/holdout access. They do not establish a
causal fix, semantic absence from literal searches, or independent judge validity.
The primary-series coverage regressions additionally exercise
`require_complete_official_coverage` against incomplete/invalid synthetic series
and the complete saved official A/A2/B series. That helper validates coverage only;
the rule still requires matching manifest summaries and all other acceptance gates.
