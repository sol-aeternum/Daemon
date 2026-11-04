# Query-decomposition exposure gate — 2026-11-03

Diagnostic only; no production candidate activated or campaign authorized.

## Frozen design and hashes
Frozen design SHA256: `ab002ad69c69e9f2398e215eab6e93c35b32c6f72587c8a6f5d27015367e0d96`
Raw SHA256: `4a1f9a8bfed8ab0a4c4f3360b7db7ef6dee979907df05499d983db0eb4aff9a8`
Oracle SHA256: `204dbaf8625298bb23c4db874c2559bca31e8e65efb33bfa8f17c2b6a13d07a0`
Git: `c297f628b315a26d9d127c5182c500c00f634bcd`
Design hashes: `{"decomposition_sha256": "d38b079bc4d47d2e0f315c5b11cfb5d32a9a52ea748f17867a8d9d8c578c0fb4", "generation_sha256": "2415a49f4208e3472e43d0d5632b7f2c0226f6123f6be46f0dec490eb79bfa79", "prompt_sha256": "79f69f54c7fc8aeed13392a845f020659125ae216a4a867eb08c8f97f2de0df0"}`

## Accounting
Chat uses seed=42 and JSON schema; no temperature or reasoning override. Azure EU/ZDR; no fallback.
USD cap: reported costs plus conservative unknown-attempt ceilings, checked before each send.
Chat ceiling: ceil((production_input_bound * prompt_rate + max_tokens * output_rate) / 1e6) micro-USD; embedding uses UTF-8 input_bound and pinned input rate.
Fingerprint before/after: `5cb6ab14f2eb6bf11bb5e4c5ed164a08b70d0fb92155918d006be4f630b874f3` / `5cb6ab14f2eb6bf11bb5e4c5ed164a08b70d0fb92155918d006be4f630b874f3`
All pg_stat_user_tables counters inspected; only explicitly source-derived ledger tables, retrieval_log and inference_route_attestations allowed. Denial encounters are not allowlisted.
Startup uses one production attestation.check_once on the writable benchmark pool per phase (two free public listing GETs); counts are recorded in accounting. No attestation.start or background loop.
One writer backend forces its own next statistics flush; concurrent unrelated writes can conservatively fail the audit. Statistics are not a transactional write-provenance proof.
Read-only repeatable-read retrieval; historical question clock; original vectors reused, never embedded.
No answer or judge transport constructed; construction/dispatch guards active.
chat_attempts: `50`
embedding_calls: `60`
provider_cost_usd: `0.00296158`
unknown_cost_attempts: `0`
charged_bound_usd: `0.00296158`
generation_latency_ms_p50: `1838.4784719964955`
generation_latency_ms_p95: `2714.018265988851`
attestation_counts: `{'routes': 9, 'attested': 9, 'check_failed': 0, 'revoked': 0}`
Table changes: `{"public.entitlement_reservations": [60, 60, 0], "public.entitlement_usage_periods": [0, 120, 0], "public.inference_route_attestations": [9, 0, 0]}`

## Configuration table
| Configuration | R | P | D | Coverage losses | Tokens |
|---|---|---|---|---|---|
| original_k5 | 0 | 0 | 0 | 0 | 7430 |
| generated_rrf_k5 | 1 | 1 | 0 | 0 | 7427 |
| generated_rrf_k10 | 1 | 1 | 0 | 0 | 13366 |
| generated_round_robin_k5 | 1 | 1 | 0 | 0 | 7357 |
| generated_round_robin_k10 | 3 | 3 | 0 | 0 | 13348 |
| generated_union_rescore_k5 | 1 | 1 | 0 | 0 | 7405 |
| generated_union_rescore_k10 | 3 | 3 | 0 | 0 | 13335 |
| oracle_rrf_k5 | 1 | 1 | 0 | 0 | 7408 |
| oracle_rrf_k10 | 2 | 2 | 0 | 0 | 13415 |
| oracle_round_robin_k5 | 1 | 1 | 0 | 0 | 7392 |
| oracle_round_robin_k10 | 5 | 5 | 0 | 0 | 13402 |
| oracle_union_rescore_k5 | 2 | 2 | 0 | 0 | 7393 |
| oracle_union_rescore_k10 | 4 | 4 | 0 | 0 | 13376 |

## Per-target attribution
Ranks are 1-based production depth-10 results; null means absent within that depth. Index 0 is original, others are sub-query indices.
| Case | Memory | Source | Query indices | Ranks |
|---|---|---|---|---|
| 28dc39ac | 06729686-3261-40ed-8ff6-df507bf435c3 | generated | [0, 1] | [1, 1] |
| 28dc39ac | 8925ddbd-0184-4573-b2c5-a7f91bf5e677 | generated | [0, 1] | [2, 2] |
| 28dc39ac | 0807f4f5-3e15-43bf-b776-857b726ad7e2 | generated | [0, 1] | [None, None] |
| 28dc39ac | 11672c8b-34a5-4b99-9fc5-b92c5fc17bc5 | generated | [0, 1] | [None, None] |
| 28dc39ac | 99db9c59-462a-4f38-bc8b-7c4261a8437a | generated | [0, 1] | [7, None] |
| 28dc39ac | f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f | generated | [0, 1] | [6, 10] |
| 28dc39ac | 06729686-3261-40ed-8ff6-df507bf435c3 | oracle | [0, 1, 2, 3] | [1, 1, None, 9] |
| 28dc39ac | 8925ddbd-0184-4573-b2c5-a7f91bf5e677 | oracle | [0, 1, 2, 3] | [2, 2, None, None] |
| 28dc39ac | 0807f4f5-3e15-43bf-b776-857b726ad7e2 | oracle | [0, 1, 2, 3] | [None, 6, 2, 5] |
| 28dc39ac | 11672c8b-34a5-4b99-9fc5-b92c5fc17bc5 | oracle | [0, 1, 2, 3] | [None, 5, 1, 3] |
| 28dc39ac | 99db9c59-462a-4f38-bc8b-7c4261a8437a | oracle | [0, 1, 2, 3] | [7, 9, 6, 2] |
| 28dc39ac | f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f | oracle | [0, 1, 2, 3] | [6, 10, 9, 1] |
| 1192316e | b2239692-8040-4005-abaa-79588eedf2b1 | generated | [0, 1, 2] | [7, 1, 6] |
| 1192316e | 2b34b3b2-589a-43eb-908e-3e2bedf033e5 | generated | [0, 1, 2] | [1, 2, 1] |
| 1192316e | b2239692-8040-4005-abaa-79588eedf2b1 | oracle | [0, 1, 2] | [7, 1, 7] |
| 1192316e | 2b34b3b2-589a-43eb-908e-3e2bedf033e5 | oracle | [0, 1, 2] | [1, 9, 1] |
| 6613b389 | 21ac11f9-9689-4571-af6f-3c5c7c858260 | generated | [0, 1, 2] | [1, 8, 1] |
| 6613b389 | 55eab6cb-17c0-4201-be69-9b3b70700029 | generated | [0, 1, 2] | [None, 1, None] |
| 6613b389 | 94c365d4-d4cb-47c1-8661-9b9ff73a09f6 | generated | [0, 1, 2] | [None, 3, None] |
| 6613b389 | 21ac11f9-9689-4571-af6f-3c5c7c858260 | oracle | [0, 1, 2] | [1, 1, 10] |
| 6613b389 | 55eab6cb-17c0-4201-be69-9b3b70700029 | oracle | [0, 1, 2] | [None, None, 1] |
| 6613b389 | 94c365d4-d4cb-47c1-8661-9b9ff73a09f6 | oracle | [0, 1, 2] | [None, None, 3] |
| 0bb5a684 | e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8 | generated | [0, 1, 2] | [1, 1, None] |
| 0bb5a684 | 0770505d-0b96-4908-af07-2e692c034b9b | generated | [0, 1, 2] | [None, None, 1] |
| 0bb5a684 | 3c418fde-f8c6-4383-8513-9df463894cd9 | generated | [0, 1, 2] | [None, None, 4] |
| 0bb5a684 | e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8 | oracle | [0, 1, 2] | [1, 1, None] |
| 0bb5a684 | 0770505d-0b96-4908-af07-2e692c034b9b | oracle | [0, 1, 2] | [None, None, 1] |
| 0bb5a684 | 3c418fde-f8c6-4383-8513-9df463894cd9 | oracle | [0, 1, 2] | [None, None, 4] |
| 0a34ad58 | 07c6d26d-8bd3-438b-aecc-6d8451f0ec5d | generated | [0, 1] | [None, None] |
| 0a34ad58 | 3bb5fed7-48de-4aa3-9cbc-5ec9d1fb9d42 | generated | [0, 1] | [None, 9] |
| 0a34ad58 | ddabe83c-c72c-4e28-b6b2-287102a9c442 | generated | [0, 1] | [None, None] |
| 0a34ad58 | 07c6d26d-8bd3-438b-aecc-6d8451f0ec5d | oracle | [0, 1, 2, 3] | [None, 3, None, None] |
| 0a34ad58 | 3bb5fed7-48de-4aa3-9cbc-5ec9d1fb9d42 | oracle | [0, 1, 2, 3] | [None, 1, None, None] |
| 0a34ad58 | ddabe83c-c72c-4e28-b6b2-287102a9c442 | oracle | [0, 1, 2, 3] | [None, None, 1, None] |

## Statistics
Sub-query count distribution: `{"1": 43, "2": 7}`
Share with any added query: 0.82
Per-question costs, embedding counts, latency and attempt records are in sanitized evidence.

## Production-cost estimate
One extra decomposition LLM call plus at most three query embeddings per turn (original query embedding unchanged).
Measured mean USD per chat attempt: 0.0000589204; completed receipt count: 50.
Measured mean USD per embedding attempt: 2.593333333333333333333333333E-7; completed receipt count: 60.

## Decision as returned
```json
{"candidate": null, "distractor_deltas": null, "flags": ["CEILING_FAILS"], "merge_leads": [], "oracle_coverage_losses": {"oracle_round_robin_k10": [], "oracle_round_robin_k5": [], "oracle_rrf_k10": [], "oracle_rrf_k5": [], "oracle_union_rescore_k10": [], "oracle_union_rescore_k5": []}, "proposal": null, "token_delta": null, "vetoed": false}
```

## Primary notes
Written by the primary (Opus) after the run. The generated sections above are unedited.

**Outcome, applied mechanically: `CEILING_FAILS`.**
- The oracle under RRF exposes R = 1/5 cases at k=5 and 2/5 at k=10, below 4 at
  both widths. Per the pre-registration, decomposition as designed (RRF fusion)
  is rejected.
- The primary configuration (model-generated, RRF) exposes 1/5 at both widths.
- MODEL_GAP requires the oracle to pass, so it doesn't fire.
- MERGE_LEAD doesn't fire: the best model-generated secondary merge reaches 3/5.
- No configuration has budget drops (D = 0 everywhere) or coverage losses.
- No campaign or production proposal is made.

**Merge policy, not retrieval, binds the oracle.** This is an observation, not
a decision: oracle sub-queries are contaminated and can never promote.
- Oracle round-robin at k=10 exposes **5/5**, and oracle union-rescore at k=10
  exposes 4/5. Oracle RRF at k=10 exposes 2/5.
- With the RRF constant 60, an item ranked in several lists (usually the
  original query's own top hits) accumulates more score than a fact that only
  one sub-query finds at rank 1. The latter scores 1/61, while an item ranked
  3rd and 8th scores 1/63 + 1/68. Facts that only a sub-query surfaces are
  therefore pushed below the top 10.
- This mechanism is the primary's interpretation. The per-case table below
  shows the effect directly: the same sub-queries, with only the merge changed.
  The arithmetic is not otherwise tested.

**Model-generated sub-queries.**
- 43 of 50 questions got exactly one sub-query, and 7 got two. 82% added a
  query that differs from the original.
- For `1192316e`, `6613b389` and `0bb5a684`, the generated sub-queries
  retrieve every target within depth 10, at ranks 1–8 (e.g. `0bb5a684`: the
  meeting facts at ranks 1 and 4 under sub-query 2).
- **Per-case strict exposure** (named targets rendered out of named targets).
  The primary computed this from the raw output (sha `4a1f9a8b…`, outside Git)
  and `cases.json`; rendered equals retrieved here, because D = 0 everywhere.

  | Configuration | 28dc39ac | 1192316e | 6613b389 | 0bb5a684 | 0a34ad58 |
  |---|---|---|---|---|---|
  | original_k5 | 2/6 | 1/2 | 1/3 | 1/3 | 0/3 |
  | generated_rrf_k10 | 4/6 | 2/2 | 1/3 | 1/3 | 0/3 |
  | generated_round_robin_k10 | 4/6 | 2/2 | 3/3 | 3/3 | 0/3 |
  | generated_union_rescore_k10 | 3/6 | 2/2 | 3/3 | 3/3 | 1/3 |
  | oracle_rrf_k10 | 6/6 | 2/2 | 2/3 | 1/3 | 0/3 |
  | oracle_round_robin_k10 | 6/6 | 2/2 | 3/3 | 3/3 | 3/3 |

  With the **same** generated sub-queries, RRF fully exposes 1 case and
  round-robin fully exposes 3: `6613b389` and `0bb5a684` go from 1/3 to 3/3.
  So for those two cases the merge, not the sub-queries, is the binding
  constraint.
- For `28dc39ac`, the model produced one query that misses both Last of Us
  facts.
- For `0a34ad58` (Tokyo preferences), it finds only `3bb5fed7`, at rank 9.
- Generated round-robin and union-rescore reach 3/5 at k=10.

**Coverage, distractors and tokens.** These come from the sanitized evidence;
the generated table doesn't show them.

| Configuration | Coverage mean (proxy) | Distractors: added / plausible (5 measured questions) |
|---|---|---|
| original_k5 baseline | 0.8953 | — |
| generated_rrf_k5 / k10 | 0.9053 / 0.9493 | 3/0 · 25/2 |
| generated_round_robin_k5 / k10 | 0.9153 / 0.9493 | 1/0 · 25/2 |
| generated_union_rescore_k5 / k10 | 0.9220 / 0.9493 | 3/0 · 25/2 |
| oracle_rrf_k5 / k10 | 0.9093 / 0.9533 | 0/0 · 25/2 |

- No configuration loses coverage on any of the 50 questions.
- At k=10, the rendered tokens are ≈ 1.8× the baseline (13.3k vs 7.4k in
  total).

**Production cost, as measured.** One extra Luna call costs about
USD 0.000059, with p50 latency 1.84 s and p95 2.71 s. A query embedding costs
about USD 2.6e-7. If decomposition ran synchronously before retrieval, the
latency would dominate; the cost is negligible.

**Accounting and process.** Every ruling is attributed in
`decomposition-preregistration-20261103.md` (task evidence area).
- **Generation:** one pass, 50/50 calls on the first attempt; no 429 retries
  and no parse re-requests. Served by `openai/gpt-6-luna` on Azure. Cost
  USD 0.00294602.
- **Two generate attempts stopped before any provider call:**
  - the ZDR attestation was missing; the owner approved running it;
  - a `max_price` pinning-check bug, fixed by the primary.
- **The first `measure` crashed after its 60 embeddings**, while serializing
  output. No result was seen. The owner approved one rerun after an offline
  fix. That brings oracle embeddings to 24 in total, against the original cap
  of 15.
- **Totals:** the generated Accounting block above covers **this (rerun)
  `measure` only**: 60 embeddings, plus the generation chat cost carried in
  (USD 0.00296158). Across both measure runs there were 120 embedding calls,
  and total spend was ≈ USD 0.00298 (0.00296158 + 60 × ≈ 2.59e-7 for the
  crashed run), against the cap of USD 0.10.
- **The oracle-embedding overrun** (24 against 15) is an owner ruling recorded
  in the pre-registration file.
- **`vetoed: false`** in the decision JSON means there was no qualifying primary
  to veto. The veto only applies when the primary would otherwise qualify.
- **The RRF constant 60** and the tie-breaks are in the frozen design file
  (`ab002ad6…`).
- **Fingerprint:** `5cb6ab14…` before and after every phase.
- **Tables written:** only the ledger and attestation tables, all allowlisted.
- **Self-checks:** the baseline matched the pilot blocks 50/50, and the
  single-query fused path matched the baseline 50/50.
- **Log labels:** the routing log shows profile, route and model as
  "unrecognized", because the script doesn't initialise the routing-log
  vocabulary as the harness does. The receipts confirm the route.

**If pursued.** The data points to two separate variables:
1. the merge policy (round-robin or union instead of RRF);
2. decomposition coverage for multi-fact questions (the model usually emitted
   a single query).

Each would need its own pre-registration and a fresh, un-contaminated
evaluation. This result already used the dev set's 5 omission cases to inspect
merges, so a new pass shouldn't be judged on the same cases alone.

