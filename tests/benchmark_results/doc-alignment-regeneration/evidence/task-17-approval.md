# Task 17 Evidence — Gate Approval Decision

## Proceed / Stop Decision

STOP — Task 18 must not proceed yet.

## Approval Phrase

No approval phrase is granted. The Oracle gate review records a rejection and an amendment list, not a proceed phrase.

## Search Evidence from `oracle_gate_review.md`

Search pattern: `VERDICT:|Task 18 may|Required amendments|APPROVE`

```text
oracle_gate_review.md:12: VERDICT: REJECT
oracle_gate_review.md:146: Task 18 may **not** wire the blocking fail-mode gate now.
oracle_gate_review.md:148: Required amendments before Task 18:
```

Interpretation: no approval line was found; the review contains a stop decision and amendment list.

## Required Amendments Before Task 18

1. Replace the hardcoded removed-provider sentinel in `video_providers` with source-derived validation against the provider set extracted from `orchestrator/routes/video_credits.py`, or formally re-scope/rename that check and amend the gate contract. Preferred amendment: source-derived provider validation.
2. Align the linter's declared scope with active enforcement before it becomes blocking. Implement the advertised high-confidence checks (`embedding_query_model`, `embedding_dimensions`, route names, feature states, env-var names where reliable) or remove those claims from the script/documented gate scope and evidence.
3. Fix report-mode text output so malformed exceptions and missing explicitly requested files are printed even when no drift findings exist.
4. Add or refresh evidence for the amended behavior, including report mode, fail mode, a provider mismatch fixture, and malformed/expired exception fixtures.

## Re-review Requirement

After the amendments, rerun Task 17 or a focused Oracle re-review. Task 18 may proceed only if that review records an explicit approval phrase.
