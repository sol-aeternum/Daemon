# LongMemEval Contamination Analysis

Date: 2026-04-18

## Scope

This report scores contamination vectors using the already-completed forensic inputs:

- `tests/benchmark_longmemeval/81_1_DIFF.md`
- `tests/benchmark_longmemeval/ISOLATION_AUDIT.md`
- `tests/benchmark_longmemeval/TEARDOWN_AUDIT.md`
- `tests/benchmark_longmemeval/test_contamination_analysis.py` for reproducible artifact checks on the preserved 81.1% bundle

This is a forensic analysis only. It does **not** change benchmark behavior.

## Evidence base

### Canonical lane facts carried into scoring

1. Canonical LongMemEval reuses one fixed benchmark user (`longmemeval@daemon.test` / `12345678-1234-5678-1234-567812345678`) across runs, while question-level retrieval isolation comes from `allowed_source_conversation_ids` inside the canonical runner (`ISOLATION_AUDIT.md:19-22,31-52`).
2. Canonical runner code does not perform automatic teardown between questions or runs; destructive cleanup lives only in the legacy ingest helper that deletes the entire shared user (`ISOLATION_AUDIT.md:54-60`).
3. Live teardown audit showed canonical rows accumulating across cases instead of returning to zero, including `retrieval_log` rows with `conversation_id IS NULL`, and only a manual user delete returned the tables to zero (`TEARDOWN_AUDIT.md:16-31`).
4. The standalone legacy evaluator path calls `evaluate_single()` without `allowed_source_conversation_ids`, so that path can read against the full shared canonical user instead of the canonical runner's question-scoped allowlist (`ISOLATION_AUDIT.md:68-79`; `tests/longmemeval/evaluate.py:458-527`).

### Fast lane / 81.1% artifact facts carried into scoring

1. The preserved 81.1% artifact belongs to `orchestrator.eval.longmemeval_fast`, not the canonical runner (`81_1_DIFF.md:51-60,227-232`).
2. Fast lane creates a unique per-run user, scopes retrieval to that run user plus current-question conversations, and runs cleanup before and after each question (`ISOLATION_AUDIT.md:81-117`).
3. Live teardown audit showed fast synchronous tables returning to zero after cleanup, but a delayed async `retrieval_log` write could recreate a single post-cleanup row; that row disappears on the next pre-cleanup or final user delete (`TEARDOWN_AUDIT.md:33-69`).
4. The preserved run log and final checkpoint/results bundle do not come from one fully preserved execution trace: `run.log` shows 11 FK failures and `0 already checkpointed`, while the final results bundle contains clean rows for those same QIDs (`81_1_DIFF.md:181-219`).
5. The historical retrieval logs for the 81.1% run were not preserved, so any claim about what specific memories were exposed during that run must carry uncertainty.

## Scoring method

Each vector gets a **risk score** from 0 to 9.

`score = breadth + potency + evidence_strength`

### Inputs

- **Breadth (0-3)**
  - `0`: one-off or contradicted
  - `1`: narrow artifact-only or timing-specific
  - `2`: conditional path or one lane only
  - `3`: routine/default lane behavior
- **Potency (0-3)**
  - `0`: cannot change retrieved benchmark content
  - `1`: contaminates logs/provenance more than retrieval content
  - `2`: can preserve stale benchmark state but some scoping still remains
  - `3`: can expose out-of-scope benchmark content directly to evaluation
- **Evidence strength (0-3)**
  - `0`: contradicted / no support
  - `1`: weak or mostly speculative
  - `2`: plausible but not directly preserved
  - `3`: directly demonstrated by audit/code/artifact

### Verdict labels

- **Confirmed**: mechanism directly demonstrated (`evidence_strength = 3`) and materially relevant.
- **Plausible**: mechanism is supported but not directly preserved end-to-end.
- **Weak**: mechanism exists, but contamination potency is low.
- **Rejected**: audits/code contradict the vector.

## Ranked vector table

| Rank | Lane | Mechanism | Breadth | Potency | Evidence | Score | Verdict | Confidence | Portable to later analysis? |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| 1 (tie) | Canonical | Shared benchmark user persists because canonical lane does not tear down between cases/runs | 3 | 2 | 3 | 8 | Confirmed | High | Yes |
| 1 (tie) | Canonical | Legacy evaluator bypasses `allowed_source_conversation_ids` and can read the full shared user | 2 | 3 | 3 | 8 | Confirmed | High | Yes, but only when that path is used |
| 3 | Fast / artifact provenance | 81.1% bundle has split provenance between failing `run.log` and clean final checkpoint/results | 2 | 1 | 3 | 6 | Confirmed | High | Yes for artifact-trust analysis; no for retrieval-state analysis |
| 4 | Fast | Async `retrieval_log` write can survive post-question cleanup | 1 | 1 | 3 | 5 | Weak | Medium-high | Limited: forensic residue only |
| 5 | Fast | Stable cross-question memory contamination through retained conversations/memories | 0 | 0 | 3 | 0 | Rejected | High | No |
| 6 | Cross-lane inference | Use fast direct-insert behavior to argue canonical extraction/dedup contamination | 0 | 0 | 3 | 0 | Rejected | High | No |

## Vector-by-vector verdicts

### 1. Canonical shared-user persistence without teardown

- **Verdict:** **Confirmed contamination vector**.
- **Why:** canonical ingest/evaluate reuse the fixed benchmark user, and canonical runner teardown is manual rather than automatic (`ISOLATION_AUDIT.md:31-60`). The teardown audit directly showed state accumulation across consecutive cases: conversations `0 -> 1 -> 2`, messages `0 -> 2 -> 4`, memories `0 -> 1 -> 2`, extraction log `0 -> 1 -> 2`, retrieval log `0 -> 1 -> 2` (`TEARDOWN_AUDIT.md:18-31`).
- **What it can contaminate:** later canonical runs or analyses that depend on shared-user state remaining pristine.
- **What limits it:** canonical runner still applies question-specific `allowed_source_conversation_ids`, so persistence alone does **not** prove that off-question memories were returned during a normal canonical run.
- **Residual uncertainty:** because historical retrieval logs were not preserved for the old 81.1% artifact, this vector is confirmed in the lane generally, but not directly tied to the fast 81.1% run.

### 2. Canonical legacy evaluator bypass of the conversation allowlist

- **Verdict:** **Confirmed contamination vector**.
- **Why:** the standalone `run_evaluation()` path invokes `evaluate_single()` without forwarding `allowed_source_conversation_ids` (`tests/longmemeval/evaluate.py:458-527`), while `evaluate_single()` defaults to the shared `TEST_USER_ID` (`tests/longmemeval/evaluate.py:363-386`). Isolation audit already identified this as the path that can read the full shared canonical user instead of the canonical runner's per-question allowlist (`ISOLATION_AUDIT.md:68-79`).
- **What it can contaminate:** any benchmark result produced through the legacy evaluation adapter on a dirty shared benchmark user.
- **Why it ranks with shared-user persistence:** shared-user persistence creates the stale pool; allowlist bypass is the direct mechanism that can actually expose that stale pool to evaluation.
- **Residual uncertainty:** none about the existence of the vector; uncertainty applies only to whether any specific historical artifact actually used this path.

### 3. Split provenance inside the preserved 81.1% fast bundle

- **Verdict:** **Confirmed artifact contamination**, not confirmed retrieval-state contamination.
- **Why:** the saved `run.log` records 11 FK failures and starts from `0 already checkpointed`, but the final checkpoint/results bundle contains 500 clean rows and no error entries for those same QIDs (`81_1_DIFF.md:190-219`). The reproducibility test in `test_contamination_analysis.py` re-checks that those failed QIDs exist as clean rows in the final artifact.
- **What it can contaminate:** forensic confidence in the 81.1% bundle as one self-consistent execution record.
- **What it cannot prove by itself:** that retrieval content was contaminated. It proves a provenance gap, not which memories were exposed.
- **Residual uncertainty:** medium, because the matching repaired run log was not preserved.

### 4. Fast async `retrieval_log` bleed after cleanup

- **Verdict:** **Weak contamination vector**.
- **Why:** teardown audit proved that post-question cleanup returns fast synchronous tables to zero, then a delayed background `retrieval_log` insert can recreate exactly one stray row (`TEARDOWN_AUDIT.md:37-55`).
- **Why it stays weak:** the surviving row is in `retrieval_log`, not in `memories`, `messages`, or `conversations`. The next pre-cleanup removes it, and end-of-run user deletion removes the last one (`TEARDOWN_AUDIT.md:52-69`).
- **What it can contaminate:** forensic residue and teardown accounting.
- **What it does not support:** a claim that fast lane was stably leaking retrieved memory content across questions.

### 5. Fast lane stable cross-question memory contamination

- **Verdict:** **Rejected**.
- **Why:** isolation audit shows fast lane uses a unique run user plus question-local conversations (`ISOLATION_AUDIT.md:89-117`), and teardown audit shows the synchronous benchmark tables return to zero after each question (`TEARDOWN_AUDIT.md:37-55`). The only demonstrated survivor is a late `retrieval_log` row, not retained memories/conversations.
- **Implication:** fast-lane behavior does **not** support a claim that the 81.1% bundle came from ordinary cross-question memory accumulation in the fast harness itself.

### 6. Fast direct-insert behavior as evidence of canonical extraction/dedup contamination

- **Verdict:** **Rejected**.
- **Why:** fast lane bypasses canonical extraction and dedup by inserting chunk memories directly (`ISOLATION_AUDIT.md:124-127`). Any extraction/dedup conclusion drawn from fast behavior would be a methodology error, not evidence.
- **Implication:** keep extraction/dedup claims anchored to canonical ingest evidence only.

## Bottom-line judgments

### What is firmly contaminated

1. **Canonical benchmark state** is vulnerable to contamination unless the shared benchmark user is explicitly destroyed, because canonical rows accumulate across cases.
2. **Legacy standalone canonical evaluation** is contamination-prone on a dirty shared benchmark user because it bypasses the canonical runner's conversation allowlist.
3. **The preserved 81.1% fast artifact bundle** is provenance-contaminated: its saved run log and final checkpoint/results are from different phases or runs.

### What is only weakly contaminated

1. **Fast-lane teardown accounting** can retain a late `retrieval_log` row through async bleed.

### What is not supported

1. **Stable fast-lane memory/conversation leakage** as the cause of 81.1%.
2. **Canonical extraction/dedup contamination claims derived from fast direct-insert behavior**.

## Evidence-backed verdict on the 81.1% artifact

The best-supported explanation is **mixed contamination**:

- **Not a clean canonical result**: the artifact is fast-lane, not canonical (`81_1_DIFF.md:227-232`).
- **Not a clean single-run fast result**: the preserved bundle has split provenance (`81_1_DIFF.md:190-219`).
- **Not explained by stable fast cross-question memory leakage**: fast synchronous state was shown to cleanly reset between questions (`TEARDOWN_AUDIT.md:48-55`).
- **Still compatible with benchmark-process contamination overall**: canonical shared-user persistence and legacy-evaluator allowlist bypass are real contamination vectors elsewhere in the LongMemEval toolchain.

So the 81.1% bundle should be treated as:

> **artifact-trust contaminated, fast-lane retrieval-state contamination unproven, canonical shared-user contamination risk independently confirmed elsewhere in the benchmark stack.**

## Confidence statement

- **High confidence** in the lane-level verdicts, because the isolation and teardown audits directly exercised the live code paths.
- **High confidence** that the preserved 81.1% bundle is provenance-split, because the run log and final results disagree on the same QIDs.
- **Lower confidence** on any claim about exactly which memories were exposed during the historical 81.1% run, because the historical retrieval logs were not retained.
