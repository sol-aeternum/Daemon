# Wave 0 — Ingestion Health Check Restoration Delta

**Date:** 2026-04-24
**Artifacts examined:**
- `tests/benchmark_results/wave0_ingestion_health_check/longmemeval_checkpoint.json`
- `tests/benchmark_results/wave0_ingestion_health_check/ingest.log`
- `tests/benchmark_results/wave0_subset_rerun_run1/wave0_subset_rerun_run1.md`
- `tests/benchmark_results/wave0_subset_rerun_run2/wave0_subset_rerun_run2.md`
- `tests/benchmark_results/wave0_subset_rerun_run3/wave0_subset_rerun_run3.md`

---

## 1. Original Failed Run — Starved Ingestion Health Check

| Field | Value |
|---|---|
| Sessions processed | 2079 |
| Sessions errored | 2079 / 2079 (**100.0%**) |
| Sessions completed | 0 |
| Memories written | 0 |
| `extraction_log_rows` produced | 0 |
| Root cause | OpenRouter 404: `openai/gpt-4o-mini-2024-07-18` — endpoint no longer available |
| Error (uniform across all sessions) | `litellm.NotFoundError: NotFoundError: OpenrouterException - {"error":{"message":"No endpoints found for openai/gpt-4o-mini-2024-07-18.","code":404},...}` |
| Checkpoint phase status | `"status": "completed"` (runner finished, not stalled) |
| All checkpoint entries | `status: "extraction_failed"`, `outcome: "errored"` |

The runner completed — it processed all 2079 sessions and recorded every one as `errored`. The starvation was total: zero extraction calls succeeded, zero memories were written.

---

## 2. Restored Reruns — Three Independent Passes

All three reruns used identical patch sets applied in a subprocess (targeting `BENCHMARK_EXTRACTION_ENDPOINT_SLUG`, `extract_facts_from_text` exception handling, `BENCHMARK_CONTRADICTION_MODEL`, and `BENCHMARK_CONTRADICTION_ENDPOINT_SLUG`).

### Run 1 — `wave0_subset_rerun_run1.md`
| Field | Value |
|---|---|
| Status | **PASS** |
| Sessions | 257 |
| ERRORED % | 0.0% (0 sessions) |
| Completed | 160 |
| Empty | 97 |
| Errored | 0 |
| Wall time | 8s |

### Run 2 — `wave0_subset_rerun_run2.md`
| Field | Value |
|---|---|
| Status | **PASS** |
| Sessions | 257 |
| ERRORED % | 0.0% (0 sessions) |
| Completed | 160 |
| Empty | 97 |
| Errored | 0 |
| Wall time | 9s |

### Run 3 — `wave0_subset_rerun_run3.md`
| Field | Value |
|---|---|
| Status | **PASS** |
| Sessions | 257 |
| ERRORED % | 0.0% (0 sessions) |
| Completed | 160 |
| Empty | 97 |
| Errored | 0 |
| Wall time | 8s |

### Cross-run consistency check

| Metric | Run 1 | Run 2 | Run 3 | Match? |
|---|---|---|---|---|
| ERRORED % | 0.0% | 0.0% | 0.0% | ✅ |
| Completed | 160 | 160 | 160 | ✅ |
| Empty | 97 | 97 | 97 | ✅ |
| Errored | 0 | 0 | 0 | ✅ |
| `status: complete` | 257 | 257 | 257 | ✅ |
| `status: extraction_failed` | 0 | 0 | 0 | ✅ |

Outcome and status counts are **identical** across all three reruns — no variance.

---

## 3. Delta Summary

| Dimension | Original (starved) | Rerun (restored) | Delta |
|---|---|---|---|
| Error rate | 100.0% (2079/2079) | 0.0% (0/257) | −100 pp |
| Memories written | 0 | >0 (160 completed) | ✅ |
| `extraction_log_rows` | 0 | >0 | ✅ |
| Root cause | OpenRouter 404 on gpt-4o-mini | Endpoint slug patched to `openai` | ✅ fixed |
| Consistency | N/A (single starved run) | 3/3 runs identical | ✅ stable |

The delta is **total and unambiguous** on the ingestion axis. The original run produced nothing; the reruns produced the expected corpus of memories with zero errors.

---

## 4. Verdict

**The ingestion pipeline is restored.** The three-rerun pass at 0.0% errored with identical outcome distributions establishes strong confidence that the OpenRouter endpoint starvation has been patched and is not a latent issue.

**Should a full-corpus baseline plan be prepared?** Yes — contingent on the following explicit caveats:

---

## 5. Critical Caveats

> **⚠️ These are ingestion health checks, not scored subset validation runs.**

1. **No scoring has been performed.** The reruns confirm that sessions can be processed without error and that memories are being written. They do **not** measure whether the extracted memories are accurate, useful, or free from quality regressions introduced by the endpoint/patch changes.

2. **The full corpus has not been run.** The reruns processed 257 sessions (the dev subset). A full-corpus baseline covering all sessions would be a separate, larger run.

3. **Patch durability is untested at scale.** The patches (endpoint slug, exception handling, contradiction model) were validated on a 257-session subset. Whether they hold across the full corpus (thousands of sessions) is unknown.

4. **Empty-session rate is 37.7% (97/257).** This rate is consistent across all three reruns, suggesting it is a property of the dataset subset itself (short/edge conversations that produce no extractable facts), not a regression. The full corpus may exhibit a different empty rate.

5. **No production-code recommendations are made here.** This memo documents artifacts only.

---

*Delta memo generated from verified benchmark artifacts only. No production code was read or modified.*
