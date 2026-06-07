# LongMemEval Harness Taxonomy

The LongMemEval evaluation suite has **two distinct memory substrates**. They
produce non-comparable accuracy numbers and serve different purposes. This
document is the authoritative reference for which harness to use and when.

## Substrate taxonomy

### chunk

| Aspect | Value |
|---|---|
| Module | `orchestrator.eval.chunk_harness` |
| CLI | `python -m orchestrator.eval.chunk_harness` |
| Main class | `LongMemEvalChunkRunner` |
| Filename prefix | `longmemeval_chunk_*` (results.jsonl, checkpoint.json, score.json) |
| User pattern | `longmemeval+chunk-{run_id}@daemon.test` |
| Memory substrate | Raw 4000-char overlapping window chunks inserted directly into `memories` |
| LLM extraction | **No** — direct INSERT, no `process_extraction()` call |
| Ingest cost | Cheap (~1-2h for full corpus; only Voyage `embed_documents` calls) |
| Re-ingest cost | Cheap; auto-cleans per question |
| Cleanup | Auto (per question, in `finally:`) |
| User scope | Isolated per-run user, auto-deleted at run end |
| Substrate tag | `substrate: "chunk"` in `longmemeval_chunk_score.json` |

### fact

| Aspect | Value |
|---|---|
| Module | `orchestrator.eval.fact_harness` |
| CLI | `python -m orchestrator.eval.longmemeval` |
| Main class | `LongMemEvalFactRunner` |
| Filename prefix | `longmemeval_fact_*` (results.jsonl, checkpoint.json, score.json) |
| User pattern | `longmemeval@daemon.test` (canonical single user) |
| Memory substrate | LLM-extracted facts via `process_extraction()` then `store.insert_memory()` |
| LLM extraction | **Yes** — full production extraction pipeline (gpt-4o-mini) |
| Ingest cost | Expensive (~22k extraction calls, 48h class) |
| Re-ingest cost | Expensive; manual `--cleanup` flag only |
| Cleanup | Manual (CLI flag) |
| User scope | Single canonical user, persists across runs |
| Substrate tag | `substrate: "fact"` in `longmemeval_fact_score.json` |

## When to use which

| Use case | Recommended harness | Why |
|---|---|---|
| Smoke-test retrieval mechanism (reranking, dedup, weight sweeps) | **chunk** | Cheap re-ingest lets you iterate on config without re-running extraction |
| Wave-gate evaluation (W4, W7, W9 final gate) | **fact** | Mirrors production memory substrate; the only substrate that tells you what users actually experience |
| LongMemEval leaderboard comparison | **fact** | Other systems on the leaderboard measure extracted-fact retrieval, not chunk retrieval |
| Time-anchored retrieval research (W4 bitemporal, W5 temporal expressions) | **fact** | Bitemporal `valid_from` and `event_time` are properties of the extraction pipeline |
| Quick reproduction of an MR question's answer path | **chunk** | Cached `task15_mr_comparison.json` answers are chunk-substrate |
| Cross-wave comparison of accuracy over time | **fact** | Chunk-substrate accuracy is sensitive to chunk-size config changes (W2 had this issue); fact-substrate is stable across such changes |

## Wave applicability matrix

| Wave | Subject | Evaluable with chunk? | Evaluable with fact? |
|---|---|:---:|:---:|
| W1 | Wave-0 preservation | Partial (no extraction test) | **Yes** |
| W2 | Chunk-size ablation | **Yes** (this is chunk's purpose) | No |
| W3 | Extraction-dedup | **No** (no extraction in chunk) | **Yes** |
| W4 | Bitemporal filtering | **No** (no `valid_from` plumbing in chunk) | **Yes** |
| W5 | Temporal-expression → event_time | **No** (no extraction in chunk) | **Yes** |
| W6 | Cross-source contradiction | **No** (chunks are not deduped) | **Yes** |
| W7 | Wave-gate evaluation | Partial (smoke only) | **Yes** |
| W8 | Memory consolidation | **No** (chunks are not consolidated) | **Yes** |
| W9 | Final gate | **No** | **Yes** |

## Historical context

- **Pre-2026-06-04**: Both harnesses existed but used the legacy names
  `orchestrator.eval.longmemeval_fast` (chunk) and `orchestrator.eval.runner`
  (fact). The chunk harness's `BENCHMARK_CATEGORY` constant was mis-set to
  `"fact"` even though it inserted chunks — a **silent substrate-tag bug**
  that polluted 14.0% of historical scores with the wrong substrate label.
- **2026-06-04 refactor** (this doc): Renamed to `chunk_harness` and
  `fact_harness`; fixed `BENCHMARK_CATEGORY = "chunk"`; added a substrate
  field to every score JSON; added the `assert_substrate_match` gate-guard
  in `orchestrator.eval.substrate` to prevent cross-substrate comparison.

## Cross-substrate score comparison is BANNED

The two substrates measure different memory pools. Comparing a chunk-substrate
accuracy against a fact-substrate accuracy (e.g., in a wave-comparison report,
a regression gate, or a leadership summary) is a **silent data-corruption
class** that has historically produced misleading narratives. Any code site
that compares two score JSONs MUST pass through
`orchestrator.eval.substrate.assert_substrate_match`, which hard-fails with
`SubstrateMismatchError` on any substrate disagreement or missing tag.

Pre-substrate-tag score files (anything without the `substrate` field) will
also hard-fail. This is intentional: comparing pre-tag scores against
post-tag scores is the same corruption class.

## See also

- `tests/benchmark_results/HARNESS_NAMING_LEGEND.md` — one-liner old→new
- `tests/benchmark_results/harness_deconfliction.md` — rename report
- `orchestrator/eval/substrate.py` — gate-guard implementation
- `tests/test_substrate_gate.py` — gate-guard unit tests
