# LongMemEval Teardown Audit

Date: 2026-04-21T11:58:58+00:00

## Scope

This audit exercised the live benchmark code paths with deterministic local doubles for extraction, embeddings, answer generation, and judging so the only variable under test was database teardown behavior.

- Canonical lane exercised `tests.longmemeval.ingest.ingest_session()` plus `tests.longmemeval.evaluate.evaluate_single()`, which are the concrete units looped by `orchestrator/eval/runner.py`.
- Fast lane exercised `orchestrator.eval.longmemeval_fast.cleanup_benchmark_state()` plus `ingest_question_chunks()` plus `evaluate_single()`, mirroring the per-question loop in `LongMemEvalFastRunner.run()`.

### Instrumentation note

The fast-lane audit deliberately held the background `store.log_retrieval()` task behind an event before releasing it. That does **not** change which row is written; it only makes the existing asynchronous retrieval-log timing window deterministic so the audit can prove whether late writes survive teardown.

## Canonical lane snapshots

| Snapshot | users | conversations | messages | memories | memory_extraction_log | retrieval_log | entities | dream_log | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | fresh isolated audit user before case 1 |
| after case 1 settled | 1 | 1 | 2 | 1 | 1 | 1 | 0 | 0 | no teardown ran after case 1 |
| after case 2 settled | 1 | 2 | 4 | 2 | 2 | 2 | 0 | 0 | case 2 adds another full row-set on top of case 1 |
| after manual user delete | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | manual cleanup proves FK cascades work when invoked |

### Canonical interpretation

- `conversations`, `messages`, `memories`, `memory_extraction_log`, and `retrieval_log` all grow from case 1 to case 2 instead of returning to zero.
- The canonical retrieval rows were written with `conversation_id IS NULL` in both observed cases (`after case 1 settled = 1`, `after case 2 settled = 2`), so they are not tied to conversation deletion anyway.
- Manually deleting the audit user returns every table to zero, which shows the residual rows come from **missing per-case teardown**, not from broken foreign-key cleanup.

**Canonical verdict:** residual rows survive between benchmark cases because the canonical lane does not run teardown between cases. The only destructive cleanup is whole-user deletion, and `orchestrator/eval/runner.py` does not call it.

## Fast lane snapshots

| Snapshot | users | conversations | messages | memories | memory_extraction_log | retrieval_log | entities | dream_log | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | fresh isolated fast-lane user before case 1 |
| case 1 after pre-case cleanup | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | baseline cleanup before case 1 |
| case 1 after evaluate return | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | retrieval_log task is queued but still blocked behind the audit gate |
| case 1 after post-case cleanup | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | cleanup removed synchronous tables before the retrieval-log task was released |
| case 1 after delayed retrieval flush | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | late retrieval_log insert survives teardown while all other user tables stay at zero |
| case 2 after pre-case cleanup | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | this pre-case cleanup removes any leftover row from the prior case |
| case 2 after evaluate return | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | retrieval_log task is queued but still blocked behind the audit gate |
| case 2 after post-case cleanup | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | cleanup removed synchronous tables before the retrieval-log task was released |
| case 2 after delayed retrieval flush | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | late retrieval_log insert survives teardown while all other user tables stay at zero |
| after end-of-run user delete | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | final user deletion clears the last leaked retrieval row |

### Fast interpretation

- `messages` and `memory_extraction_log` stay at zero for every fast-lane snapshot because `ingest_question_chunks()` direct-inserts `memories` and bypasses canonical message persistence and extraction logging.
- After each fast case returns, the post-case cleanup removes the synchronous tables (`conversations`, `memories`, etc.) back to zero.
- Releasing the delayed retrieval-log task **after** cleanup recreates a single `retrieval_log` row (`conversation_id IS NULL` count after case 1 release = 1; after case 2 release = 1). That row survives the post-case cleanup because it lands after the deletes have already run.
- The next case's pre-cleanup deletes the leftover row from the prior case, and final user deletion returns every table to zero.

**Fast verdict:** the fast lane has no stable leak in its synchronous tables, but `retrieval_log` can survive teardown through **async bleed** from the background persistence task. Any row left behind is finally removed by the next pre-case cleanup or, if it is the last case, by the end-of-run user deletion.

## Root-cause summary

| Lane | Residual rows observed between cases? | Root cause | Evidence |
| --- | --- | --- | --- |
| Canonical | Yes: `conversations`, `messages`, `memories`, `memory_extraction_log`, `retrieval_log` accumulate 1 -> 2 across the two cases | Missing teardown | Counts only reset after the audit manually deletes the whole user |
| Fast | Yes, but only for `retrieval_log` when the delayed background write lands after cleanup | Async bleed | Post-case cleanup reaches zero, then a late `retrieval_log` row reappears with all other tables still at zero |
| Fast end-of-run | No rows remain after `DELETE FROM users ...` | End-of-run user deletion | Final user delete returns the run-scoped user and every user-linked table to zero |

## Bottom line

- The canonical lane leaks benchmark state between cases because it never tears the benchmark user down between cases.
- The fast lane cleans its synchronous benchmark tables, but retrieval evidence is vulnerable to async timing because the retrieval-log write is backgrounded.
- End-of-run user deletion is a separate mechanism from per-case teardown: it is not what causes the leak, but it is what guarantees the last fast-lane stray row disappears.
