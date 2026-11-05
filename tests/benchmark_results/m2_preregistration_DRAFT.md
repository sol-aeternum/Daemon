# M2 exposure gate pre-registration: DRAFT (#601)

**Status: draft, not ratified, authorises nothing.** The owner ratifies it and the gate code is frozen before any dev2 retrieval exists. No dev2 retrieval, exposure, answer or judge output informed this draft. Gate values are proposals for the owner to accept or change.

## Dependencies

All of these must hold before freeze:

1. **Labels:** dev2 labels owner-spot-checked, with corrections applied.
2. **Route:** the Block D (M1.5) route chosen and attested.
3. **Snapshot:** the dev2 snapshot qualified under the existing `baseline_qualified` rules, with its SHA-256 and fingerprint recorded.

## Arms, on the dev2 snapshot

| Arm | Definition | Role |
|---|---|---|
| Baseline | Production, k=5 | Control |
| Candidate | `multi_query`, additive merge, quota 3, at most 3 sub-queries, Block D route, reasoning none or minimal | Gated |
| Round-robin | The same sub-queries, round-robin merge (evaluation-only) | Reported only; cannot pass or fail the gate |

Sub-queries come from **one** generation pass of `PROMPT_V2` across the 100 dev2 questions, pinned to a file with its hash before any measurement. No regeneration after seeing results. Both merge arms read that same file.

## Metric

**Strict case exposure:** a question counts as exposed only when **all** of its labelled target memories are rendered. It's computed over **answerable** questions only (`answerable_from_store=true`). Questions labelled not answerable are reported separately as the retention rate and are outside the denominator. The render budget is the production default and is pinned at freeze.

## Proposed gates

| # | Gate | Proposed rule |
|---|---|---|
| 1 | Gain | Newly fully-exposed questions (candidate − baseline) ≥ max(5, 10% of answerable questions) |
| 2 | No loss | No answerable question loses any exposed target. This should hold by construction for additive merge; verify it rather than assume it. |
| 3 | dev-50 | No coverage regression on the dev-50 snapshot, using pinned sub-queries from the same route |
| 4 | Abstention | Distractor counts reported for all 6 dev2 and 5 dev-50 abstention questions. A rendered plausible false-premise item in more than 2 of them is **flagged for owner review, not auto-rejected.** |
| 5 | Latency | Trigger rate and added p50/p95 latency reported from the Block D route. Report only. |

## Open questions for the owner

Each of these needs an answer before freeze.

1. **Gate 1's size depends on the retention rate.** The pilot's provisional labels marked 4 of 14 questions not answerable. If that held across dev2, about 71 questions would be answerable and the threshold would be 8. If the spot-check clears those labels, the threshold is 10. Pilot labels are provisional and come from 14 questions, so this is illustrative only. Should the threshold be frozen as the formula, or as a number once final labels exist?
2. **Gate 3 has no authorisation yet.** It needs retrieval on `daemon_lme_bench`, whose fingerprint is protected and whose restores are all consumed; retrieval may write `retrieval_log`. It also needs dev-50 target labels, which the frozen protocol hasn't produced for dev-50, and a generation pass on dev-50. Options: label dev-50 and run it on a scratch copy of the snapshot, or drop gate 3 and rely on gate 2's construction. No approval currently covers either.
3. **Generation spend has no cap.** The current plan approves ingest, labelling and the bake-off, not the 100-question generation pass or the retrieval run. M2 approval must set one. The Block D cost per call will inform it.
4. **Questions that trigger no decomposition.** Proposed: they count as baseline, unchanged, and stay in the denominator. This keeps the trigger rate honest.
5. **Spot-check error rate.** Labelling stops above 20%. Should a rate at or below that widen the gate margin, or be reported only? Proposed: reported only.

## Outputs

**If the gates pass:** a proposal for M3 (an answer campaign on dev2 under v2, with restore and budget counts) is drafted, not run.

**If they fail:** the report says what limited exposure, per question:
- **Decomposition:** generated sub-queries miss targets the labels show exist.
- **Retention:** targets are absent from the store.

## Boundaries

- Small sample of 100 from one dataset; a pass is indicative and does not show general capability.
- Exposure is not answer quality. Only M3 would measure answers.
- The dev2 extraction cap (4,000) differs from the Phase D baseline (2,000); see `extraction_truncation_audit_2026-11-05.md`.
- This draft enables nothing: no route approval, no flag change, no production effect.
