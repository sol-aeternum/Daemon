# LongMemEval Extraction Barrier Audit

Date: 2026-04-18T02:36:23Z

## Scope

Audited the extraction wait/barrier behavior in the current LongMemEval code paths:

- Canonical lane: `orchestrator/eval/longmemeval.py` -> `orchestrator/eval/runner.py` -> `tests/longmemeval/ingest.py`
- Fast-only lane: `orchestrator/eval/longmemeval_fast.py`
- Legacy benchmark helper: `tests/benchmark_extraction.py`

## Executive Summary

- The only live fixed-time extraction barrier in the canonical lane is `tests/longmemeval/ingest.py:209-249`, where `poll_extraction_complete()` polls `memory_extraction_log` with a **90s timeout** and **2.0s interval**.
- The canonical ingest path also blocks on `await process_extraction(...)` first (`tests/longmemeval/ingest.py:298-304`). Repo evidence shows `process_extraction()` logs to `memory_extraction_log` before returning (`orchestrator/memory/extraction.py:572-596`, backed by `orchestrator/memory/store.py:1443-1486`), so the follow-up poll is a redundant second barrier.
- The legacy extraction benchmark no longer sleeps or polls. It blocks by awaiting `extract_memories(..., messages_json=...)` inline after each cumulative replayed turn (`tests/benchmark_extraction.py:435-449`, `506-516`).
- The fast harness **bypasses extraction waiting entirely**. `orchestrator/eval/longmemeval_fast.py` directly inserts memories via `insert_chunk_memories()` and never calls `process_extraction()`, `extract_memories()`, or `poll_extraction_complete()`.

## Barrier Inventory

| File path / site | Mechanism | Fixed values / cadence | Lane classification | Current behavior | Replacement recommendation |
| --- | --- | --- | --- | --- | --- |
| `tests/longmemeval/ingest.py:298-304` | Direct async completion barrier on `await process_extraction(...)` inside `ingest_session()` | No local timeout or poll interval in this call site | canonical | Ingest blocks until extraction -> dedup -> `log_extraction()` finishes | **Keep this as the canonical completion fence.** The benchmark harness should rely on the return from `process_extraction()` (or the enclosing `ingest_session()` result) instead of adding a second DB poll. |
| `tests/longmemeval/ingest.py:209-249` (called at `tests/longmemeval/ingest.py:314`) | Polling barrier against `memory_extraction_log` (`SELECT id, extracted_facts ... ORDER BY created_at DESC LIMIT 1`) with `asyncio.sleep()` between checks | `max_wait_seconds=90`, `poll_interval=2.0` | canonical | Re-checks for extraction-log evidence after `process_extraction()` has already been awaited | **Retire for the benchmark harness.** If the harness stays on the canonical lane, use the awaited `process_extraction()` completion contract instead of polling `memory_extraction_log`. |
| `tests/benchmark_extraction.py:435-449` + `tests/benchmark_extraction.py:506-516` | Inline synchronous worker-path barrier: `invoke_benchmark_extraction()` awaits `extract_memories(...)` after each cumulative replayed turn | No explicit timeout or poll interval; executes once per replayed message/turn | legacy | Each turn is persisted, then extraction is awaited immediately before the next turn continues | If worker-shape replay is still needed for a focused legacy comparison, treat this inline await as the only barrier. Do **not** add sleep/poll wrappers around it. For the new benchmark harness, prefer the canonical lane instead. |
| `tests/benchmark_extraction.py:596-597`, `653`, `1151-1154`, `1170-1172`, `1191`, `1240` | Stale wait configuration surface (`wait_seconds`, `inter_message_wait`, CLI `--wait`) | `DEFAULT_WAIT=50`; scenario 3 sets `inter_message_wait=60`; CLI `--wait` rewrites `Scenario.wait_seconds`; runtime prints `Legacy wait setting: {args.wait}s (ignored in replay mode)` | legacy | Wait knobs remain in config/output, but replay mode does not read them to sleep, poll, or gate extraction | Do not port these knobs into `tests/benchmark_longmemeval/`. They are legacy metadata only, not active barriers. |
| `orchestrator/eval/longmemeval_fast.py:348-386`, `467-478` | **No extraction barrier**: `ingest_question_chunks()` calls `insert_chunk_memories()` for direct inserts, then evaluation runs immediately | N/A | fast-only | Fast harness bypasses extraction waiting and extraction-log checks altogether | Not a canonical replacement path for extraction benchmarking. Keep it scoped to retrieval/answer/judge/chunking studies only. |

## Delegation-Only Files (No Independent Barrier)

- `orchestrator/eval/longmemeval.py:131-139` is a CLI wrapper only. It adds no timeout/interval behavior and simply dispatches to `LongMemEvalRunner.ingest()` / `evaluate()` / `run()`.
- `orchestrator/eval/runner.py:322-329` adds no separate extraction wait of its own. The canonical ingest barrier is inherited from `tests.longmemeval.ingest.ingest_session()`.

## Canonical Replacement Path Recommendation

Recommended benchmark-harness replacement path:

1. Use the canonical lane: `python -m orchestrator.eval.longmemeval ingest` / `LongMemEvalRunner.ingest()` / `tests.longmemeval.ingest.ingest_session()`.
2. Treat `await process_extraction(...)` as the extraction completion fence.
3. Remove benchmark-side dependence on `poll_extraction_complete()` and `memory_extraction_log` polling for success detection.
4. Keep `orchestrator/eval/longmemeval_fast.py` out of extraction-barrier validation, because it intentionally bypasses extraction.

Why this is the canonical replacement:

- `process_extraction()` is already synchronous from the harness perspective: it extracts facts, deduplicates, inserts memories, and calls `store.log_extraction(...)` before returning (`orchestrator/memory/extraction.py:519-609`).
- `store.log_extraction()` is the code that writes `memory_extraction_log` (`orchestrator/memory/store.py:1443-1471`), so a post-return poll of that same table adds latency without adding a stronger completion guarantee.
- If message-by-message replay parity is ever needed, `await extract_memories(..., messages_json=...)` in `tests/benchmark_extraction.py` is the closest synchronous worker-shape fallback, but it should remain classified as **legacy**, not the replacement for the canonical harness.

## Bottom Line

- **Canonical live barrier to remove later:** `poll_extraction_complete()` with `90s / 2.0s` polling.
- **Legacy live barrier:** inline `await extract_memories(...)` per replayed turn, with no fixed timeout.
- **Legacy inert wait knobs:** `DEFAULT_WAIT=50`, `inter_message_wait=60`, CLI `--wait` (ignored).
- **Fast harness:** confirmed extraction-wait bypass; not suitable as the canonical benchmark replacement for extraction fidelity.
