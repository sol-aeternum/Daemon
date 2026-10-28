# Memory tuning pass — 2026-10-28

Status: **Phase 0 merged; Phase 1 partially measured, stopped on an unclassified provider failure.**
No production memory change, ingest, reader inference, holdout evaluation, metric
ratification or acceptance decision has occurred. Judge-only inference has run.
Interim interpretation remains **under proposed rule**.

## Phase 0 — holdout

The owner approved a 100-row proportional question-type × abstention holdout,
seed 42, before selection. See the
[freeze record](../benchmark_longmemeval/fixtures/holdout_freeze.md) and IDs-only
[manifest](../benchmark_longmemeval/fixtures/holdout_ids.json).
Selection excludes dev IDs and shared supporting-session IDs, and accesses only
selection metadata. No holdout content has been inspected or evaluated during tuning.

The qualified Phase D `ingest.dump` exists locally and its SHA-256 matches
`3fd65416fdc24e845a184d7b8d3e0828811e048cd8341a39fe285fc14dc1f5a1`.
The snapshot was only hashed, not restored or modified.

### Phase 0 verification and limits

- Twelve selector/manifest tests pass. An independent rerun against the full local
  input produced a byte-identical manifest, SHA-256
  `25ac70bd5184f582f7b8a153394582384d29f5a0584ea2753f169107a2862460`.
- `scripts/local_ci.sh` completed in 626 seconds with **zero blocking failures**:
  backend lint/format/types, blocking Bandit, dependency audit, pytest; frontend
  locked install, braces integrity, types/lint/format/audit, Vitest, fixture ownership,
  cache-browser qualification and build; feature matrix and all-file pre-commit.
  Backend: 7,141 passed / 1,264 skipped / 14 warnings. Frontend: 109 files / 1,464
  tests passed. Cache-browser qualification: 30 passed. Skips are not coverage claims.
- Required enforced read-only review completed with no material findings. Go worker
  and reviewer routes were unavailable; an OpenAI reviewer was the authorized
  **same-family fallback** for OpenAI-authored changes, not cross-family independence.
  The reviewer did not inspect the full dataset or execute checks; the independent
  full-input regeneration above is primary-supplied evidence, not a CI dependency.
- Existing non-blocking inventory: Bandit 15,889 low / 79 medium / 0 high
  ([#313](https://github.com/sol-aeternum/Daemon/issues/313)); deprecations tracked in
  [#192](https://github.com/sol-aeternum/Daemon/issues/192) and
  [#228](https://github.com/sol-aeternum/Daemon/issues/228). No gates weakened.
- The stale dataset download URL was filed as
  [#586](https://github.com/sol-aeternum/Daemon/issues/586), not silently changed in
  this selection-only phase. Three host/tooling findings are retained locally.
- No production code, API, dependency, environment-variable surface or feature state
  changed. No merge or deployment authorization is implied; human review remains required.
- The separate optional `npm run test:browser` inventory did not execute: after
  installs/builds, the managed runner refused an incomplete bounded resource scan.
  The duplicate task-created Python environments plus frontend dependencies exceed
  the helper's per-tree entry bound. The owner subsequently approved retaining only
  the redundant `.venv` in the same task's registered resource area. Its inode and
  bytes were preserved after ownership/stopped-producer checks; `.uv-venv` stays in
  the checkout, and both environments still count against the task budget. No guard
  change, deletion or symlink was used. The separate browser inventory then completed:
  **21 failed, 2 flaky, 30 passed** (3.8 minutes), tracked in
  [#490](https://github.com/sol-aeternum/Daemon/issues/490). Persistent failures include
  Next devtools inline-style CSP errors in design-token diagnostics; two UI cases
  passed only on the runner's configured retry. No frontend source, assertions,
  snapshots, timeouts or security policy changed. The final gated PR run also passed.
  The owner authorized merging #587; merge commit `26bccd15` is verified. This does
  not authorize Phase 1 merge, tuning, or holdout inference.

## Phase 1 — partial measurement evidence

The separate measurement branch implements dual judging and saved-context
diagnostics; see [the Phase 1 record](measurement_phase1_2026-10-28.md). The owner
approved the paired 256/1024-token diagnostic before implementation. That diagnostic
has not run. Planned reservation for all 406 calls was $1.6086950, not actual spend.
The repaired pending-call marker passes the 137-test targeted suite. Full blocking
local gates passed (677 seconds): backend 7,180 passed / 1,264 skipped, frontend
1,464 passed, cache-browser 30 passed, with no new gate exceptions.
After rebase onto main (including the owner-provided named-key wrapper), all blocking
gates passed again in 404 seconds: backend 7,209 passed / 1,265 skipped; frontend
1,473 passed; cache-browser 30 passed. Code stayed at `52774035` for all paid calls.
There are 386 returned judge responses and one uncertain failed request; generic B
is incomplete at 36/50, and the sequence halted before any reader-cap calls.
Official A=34/50, A2=34/50, B=5/50; official A repeats=34/50 and 33/50.
Fresh generic A=30/50 and A2=31/50. These are metric/noise measurements, not memory gains.

Official source inspected:
[`src/evaluation/evaluate_qa.py` at d6dc8b50](https://github.com/xiaowu0162/LongMemEval/blob/d6dc8b50a2d9ac0c99485ea28fa5755c62414c34/src/evaluation/evaluate_qa.py).
It requires an explicit metric-model CLI argument, not a default judge model.
Its `gpt-4o` option selects `gpt-4o-2024-08-06` (matching the harness snapshot),
with temperature 0, one completion, max tokens 10, and no seed argument.
The harness instead pins Azure via OpenRouter, seed 42 and max judge tokens 256;
these route/sampling differences must be declared, not described as identical.

Official prompts differ for temporal reasoning (off-by-one tolerance), knowledge
updates (old information may accompany the updated answer), preferences (not all
rubric points required), and abstention (`"_abs" in question_id` takes precedence).
The official parser uses `"yes" in eval_response.lower()`; retain raw output and
finish reason so invalid or truncated responses cannot be hidden by aggregate scores.
The generic judge must remain unchanged alongside this second metric.

Still required: reconcile the unknown generic-B failure without replaying its
uncertain call; finish remaining measurements only through an admitted residual plan;
run the approved reader-cap diagnostic; finalize gates and the separate Phase 1 PR.
Then pause for owner decisions on primary metric and `ACCEPTANCE_RULE_PROPOSED.md`.
No Phase 2/3 tuning has started. Failure diagnostic loss is tracked in #595.

## Cumulative benchmark cost ledger

| Activity | Returned judge calls | Quoted-rate usage estimate USD | Cumulative estimate USD |
|---|---:|---:|---:|
| Phase 0 selection and snapshot hash check | 0 | 0.00 | 0.00 |
| Official A/A2/B and two additional official A passes | 250 | 0.1198225 | 0.1198225 |
| Fresh generic A/A2 | 100 | 0.1059200 | 0.2257425 |
| Partial fresh generic B | 36 | 0.0379525 | 0.2636950 |

**Actual billed cost is unknown.** The failed call may have been billed and is not
included in the returned-usage estimate. The full **$1.5595300** reservation remains
held, including the entire failed generic-B batch. No reader/ingest calls occurred.

Evaluate-only authorization is cumulative $10; all ingest and any spend beyond that
limit require an estimate and owner approval. Agent-worker failures are not benchmark
calls. No holdout inference is authorized by the selection approval.
