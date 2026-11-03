# Memory retrieval exactness and candidate-pool audit — compact evidence

Diagnostic only; no candidate, campaign or deployment authorization. Offline summary; no DB/provider access.
The original Primary notes below are preserved verbatim, including their historical size description.
Git: `495314bea94033cf0f6c1b61ab058a764f7c33ef`.
Fingerprint before: `5cb6ab14f2eb6bf11bb5e4c5ed164a08b70d0fb92155918d006be4f630b874f3`; after: `5cb6ab14f2eb6bf11bb5e4c5ed164a08b70d0fb92155918d006be4f630b874f3`.
Provider calls: 0; embedding: 0; chat: 0; cost USD 0.
Window: `2026-11-02T09:55:58+00:00` through `2026-11-02T10:34:15+00:00` (inclusive).
Clock: `question_as_of_and_RECENCY_REFERENCE_TIME`; transaction: `read_only_repeatable_read`.
Fingerprint equality within a snapshot does not detect concurrent commits outside it.
Pilot rendered-block matches: 50/50.
Full sanitized evidence (outside Git): `/home/sol/.local/share/opencode-work/evidence/memory-tuning-20261028/retrieval-exactness-20261103-495314be.sanitized.json`
Full evidence SHA256: `2d21668776e2f5d908f4dfaef09183642a4a8c0ec03a50ee38f2b467fe6eb3b2`
Raw output (outside Git; hashed only): `/home/sol/.local/share/opencode-work/evidence/memory-tuning-20261028/retrieval-exactness-20261103-495314be.raw.json`
Raw SHA256: `da94df3b9584d8982667daa399df3b5e59bcaf70f14af022611f221627b64d85`

## Input pins

- cases: `c54798d04a3136921b86841043eb22c4d3fb27c897e8eff68325ec173bbb11f2`
- dataset: `6612e3a03c79b92bab2f1750d4661908e3dc37359715b7fb6b62c6f758bddb34`
- evidence_index: `3fe6e32efc329719728ade67fc677dfe7195688f8afdb3b147b3cc31ebac3043`
- ingest_checkpoint: `fbf6910d16f7a533c78b06c49f723ab579ce1b87f57eb01730975e0957470478`
- ingest_manifest: `536436c12bfb2fcb40c9c128389060f3a4f65ad4d3eb3dfa013a9478a97c94b6`
- phase3b_manifest: `616f767ff1432442e425e49c2542a56dc8c499744399836e12cb149ccb03bda5`
- pilot_manifest: `348f1970eea4c49ed8f0c61b8dbba95563d6d0949347c695aadf507c46284704`
- pilot_results: `1136c9af6011b767b9a1b9eda8e92e3c06e5e93ff35906e5c77ced0e7cf68105`

## T0 — execution facts

- connection_probes: `{"campaign": 1, "harness": 1, "sweep": 1}`
- connection_probe_details: `{"campaign": {"probes": 1, "probes_before_load": null}, "harness": {"probes": 1, "probes_before_load": null}, "sweep": {"probes": 1, "probes_before_load": null}}`
- connection_recipes: `{"campaign": {"command_timeout": 30, "init": null, "max_inactive_connection_lifetime": 0, "max_queries": 1000000, "max_size": 10, "min_size": 10, "recipe_only": true, "timeout": 10}, "harness": {"concurrency": 1, "init": null, "max_size": 10, "min_size": 2}, "sweep": {"default_transaction_read_only": "on", "statement_timeout": 30000, "timeout": 10}}`
- execution_facts: `{"db_role_settings": [], "dev_user_counts": {"count": 50, "max": 617, "median": 510.0, "min": 393}, "indexes": [{"definition_sha256": "111c5b671c140fe0df4a85359831b70e40e3cff111bf6d85c3f8b9f6835de490", "name": "idx_memories_active"}, {"definition_sha256": "6191be63b1ce9c10b9663122a309f31964347871fa8d3e0973acdeeae4d4dea7", "name": "idx_memories_active_content_hash_unique"}, {"definition_sha256": "c17bec623ee108c12ecb17be93450a56a969d2858dccc003cea738ba4b4cebbf", "name": "idx_memories_embedding_ivfflat"}, {"definition_sha256": "6d8cc83523106eaa59f3cb6fbab9a8a3bff6c595efdb2d3d5e14e1f70e7b3792", "name": "idx_memories_slot_history"}, {"definition_sha256": "ebd090e0fa539b22d3764e9749dfa6e3e4b2dc34d046ed513089576bde46896e", "name": "idx_memories_user_id"}, {"definition_sha256": "3d0ac2d426cc0437c43b18e1d7e4e623671381c5b22473d1a7bb1f64c52bd8f2", "name": "idx_memories_user_status"}], "pg_settings": [{"reset": 1, "setting": 1, "source": "default"}], "pgvector_version": "0.8.1", "rows_by_embedding_model": [{"count": 25435, "model": "openrouter:azure:openai/text-embedding-3-small:1024"}]}`

Pre-load null means the setting was not registered/set before backend-local pgvector load; effective probes drive decisions.
Per-user counts above are summary only. No per-user list or plan-node dump is committed.

| Question | Kind | Mode | Index | IVFFlat used | Shape SHA prefix |
|---|---|---|---|---|---|
| 0bb5a684 | vector | harness | idx_memories_user_id | False | 41d7719af45c |
| 0bb5a684 | vector | probes10 | idx_memories_user_id | False | 41d7719af45c |
| 0bb5a684 | bm25 | harness | idx_memories_content_tsv | False | 06455d6e7823 |
| 0bb5a684 | bm25 | probes10 | idx_memories_content_tsv | False | 06455d6e7823 |
| 0a34ad58 | vector | harness | idx_memories_user_id | False | 41d7719af45c |
| 0a34ad58 | vector | probes10 | idx_memories_user_id | False | 41d7719af45c |
| 0a34ad58 | bm25 | harness | idx_memories_content_tsv | False | 06455d6e7823 |
| 0a34ad58 | bm25 | probes10 | idx_memories_content_tsv | False | 06455d6e7823 |
| b86304ba | vector | harness | idx_memories_user_id | False | 41d7719af45c |
| b86304ba | vector | probes10 | idx_memories_user_id | False | 41d7719af45c |
| b86304ba | bm25 | harness | idx_memories_content_tsv | False | 06455d6e7823 |
| b86304ba | bm25 | probes10 | idx_memories_content_tsv | False | 06455d6e7823 |

Plan-shape differences: `[]`.

## T1 — exact versus approximate ranking

Exact reference retains captured production eligibility filters; index scans are disabled locally.
Exact/probes100 verifications: 5.
Recall denominator is min(k, exact eligible rows); empty-reference recall is unity. A shorter LIMIT bounds recall at a larger k.

| Mode / LIMIT | Recall@10 | Recall@50 |
|---|---|---|
| harness_10 | 1.0 | 0.2 |
| harness_100 | 1.0 | 1.0 |
| probes100_10 | 1.0 | 0.2 |
| probes100_100 | 1.0 | 1.0 |
| probes10_10 | 1.0 | 0.2 |
| probes10_100 | 1.0 | 1.0 |

Every per-question recall equals its aggregate: yes.

### Named target ranks

| Question | Memory | Exact cosine rank | BM25 rank |
|---|---|---|---|
| 28dc39ac | 06729686-3261-40ed-8ff6-df507bf435c3 | 1 | None |
| 28dc39ac | 0807f4f5-3e15-43bf-b776-857b726ad7e2 | 18 | None |
| 28dc39ac | 11672c8b-34a5-4b99-9fc5-b92c5fc17bc5 | 14 | None |
| 28dc39ac | 8925ddbd-0184-4573-b2c5-a7f91bf5e677 | 2 | None |
| 28dc39ac | 99db9c59-462a-4f38-bc8b-7c4261a8437a | 9 | None |
| 28dc39ac | f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f | 6 | None |
| 1192316e | 2b34b3b2-589a-43eb-908e-3e2bedf033e5 | 1 | None |
| 1192316e | b2239692-8040-4005-abaa-79588eedf2b1 | 6 | None |
| 6613b389 | 21ac11f9-9689-4571-af6f-3c5c7c858260 | 1 | None |
| 6613b389 | 55eab6cb-17c0-4201-be69-9b3b70700029 | 15 | None |
| 6613b389 | 94c365d4-d4cb-47c1-8661-9b9ff73a09f6 | 17 | None |
| 0bb5a684 | 0770505d-0b96-4908-af07-2e692c034b9b | 16 | None |
| 0bb5a684 | 3c418fde-f8c6-4383-8513-9df463894cd9 | 24 | None |
| 0bb5a684 | e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8 | 2 | None |
| 0a34ad58 | 07c6d26d-8bd3-438b-aecc-6d8451f0ec5d | 19 | None |
| 0a34ad58 | 3bb5fed7-48de-4aa3-9cbc-5ec9d1fb9d42 | 14 | None |
| 0a34ad58 | ddabe83c-c72c-4e28-b6b2-287102a9c442 | 33 | None |

## T2 — production gather/render grid

Configurations: 24; duplicate probes10 collapsed: False.
Pool override is INITIAL_VECTOR_CANDIDATES, shared by vector and BM25; no separate BM25 constant.
Coverage is a gold-session proxy, not answer-fact exposure. Distractors are warnings, not decision vetoes.
Distractor aggregates use only the fixed measured cohort; unmeasured values remain null, not zero. Baseline added rows/support are relative to itself; own plausible support is independent.

| Configuration | Strict /5 | Pre-budget /5 | Coverage losses | Target drops | Total drops | Tokens | Empty retrievals | Measured questions | Added distractors | Added plausible support | Own plausible support |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pool100_exact_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool100_exact_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool100_harness_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool100_harness_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool100_probes10_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool100_probes10_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool10_exact_k10 | 1 | 1 | 0 | 0 | 0 | 13422 | 0 | 5 | 25 | 2 | 5 |
| pool10_exact_k5 | 0 | 0 | 0 | 0 | 0 | 7430 | 0 | 5 | 0 | 0 | 3 |
| pool10_harness_k10 | 1 | 1 | 0 | 0 | 0 | 13422 | 0 | 5 | 25 | 2 | 5 |
| pool10_harness_k5 | 0 | 0 | 0 | 0 | 0 | 7430 | 0 | 5 | 0 | 0 | 3 |
| pool10_probes10_k10 | 1 | 1 | 0 | 0 | 0 | 13422 | 0 | 5 | 25 | 2 | 5 |
| pool10_probes10_k5 | 0 | 0 | 0 | 0 | 0 | 7430 | 0 | 5 | 0 | 0 | 3 |
| pool25_exact_k10 | 1 | 1 | 0 | 0 | 0 | 13474 | 0 | 5 | 25 | 2 | 5 |
| pool25_exact_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool25_harness_k10 | 1 | 1 | 0 | 0 | 0 | 13474 | 0 | 5 | 25 | 2 | 5 |
| pool25_harness_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool25_probes10_k10 | 1 | 1 | 0 | 0 | 0 | 13474 | 0 | 5 | 25 | 2 | 5 |
| pool25_probes10_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool50_exact_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool50_exact_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool50_harness_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool50_harness_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool50_probes10_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool50_probes10_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |

### Coverage-loss summary

- pool100_exact_k10: losses `[]`; causes `{}`.
- pool100_exact_k5: losses `[]`; causes `{}`.
- pool100_harness_k10: losses `[]`; causes `{}`.
- pool100_harness_k5: losses `[]`; causes `{}`.
- pool100_probes10_k10: losses `[]`; causes `{}`.
- pool100_probes10_k5: losses `[]`; causes `{}`.
- pool10_exact_k10: losses `[]`; causes `{}`.
- pool10_exact_k5: losses `[]`; causes `{}`.
- pool10_harness_k10: losses `[]`; causes `{}`.
- pool10_harness_k5: losses `[]`; causes `{}`.
- pool10_probes10_k10: losses `[]`; causes `{}`.
- pool10_probes10_k5: losses `[]`; causes `{}`.
- pool25_exact_k10: losses `[]`; causes `{}`.
- pool25_exact_k5: losses `[]`; causes `{}`.
- pool25_harness_k10: losses `[]`; causes `{}`.
- pool25_harness_k5: losses `[]`; causes `{}`.
- pool25_probes10_k10: losses `[]`; causes `{}`.
- pool25_probes10_k5: losses `[]`; causes `{}`.
- pool50_exact_k10: losses `[]`; causes `{}`.
- pool50_exact_k5: losses `[]`; causes `{}`.
- pool50_harness_k10: losses `[]`; causes `{}`.
- pool50_harness_k5: losses `[]`; causes `{}`.
- pool50_probes10_k10: losses `[]`; causes `{}`.
- pool50_probes10_k5: losses `[]`; causes `{}`.

## T3 — exact-search pool stability summary

Order changes compare common-set order for every observed pool pair, not imputed full-union order. Missing union members make full-union order unknown. Component changes alone are not causal proof.

| Question | Order changed for any pool pair |
|---|---|
| b86304ba | False |
| 8550ddae | False |
| 86f00804 | False |
| 19b5f2b3 | False |
| caf9ead2 | False |
| fca762bc | False |
| 4388e9dd | False |
| e8a79c70 | False |
| d596882b | False |
| 71a3fd6b | False |
| 28dc39ac | False |
| ba358f49 | False |
| 6cb6f249 | False |
| 2318644b | False |
| 1192316e | False |
| 8c18457d | False |
| gpt4_4edbafa2 | False |
| 6613b389 | False |
| gpt4_af6db32f | False |
| gpt4_b0863698 | False |
| 852ce960 | False |
| 6a1eabeb | False |
| 5831f84d | False |
| f685340e | False |
| 184da446 | False |
| f685340e_abs | False |
| 982b5123_abs | False |
| gpt4_93159ced_abs | False |
| 80ec1f4f_abs | False |
| gpt4_372c3eed_abs | False |
| ad7109d1 | False |
| 2bf43736 | False |
| 75f70248 | False |
| a3332713 | False |
| 0bb5a684 | False |
| 0977f2af | False |
| c5e8278d | False |
| c4f10528 | False |
| 0a34ad58 | False |
| 92a0aa75 | False |
| gpt4_65aabe59 | False |
| 3ba21379 | False |
| 545bd2b5 | False |
| 1de5cff2 | False |
| 09d032c9 | False |
| cc06de0d | False |
| gpt4_93159ced | False |
| 59524333 | False |
| 25e5aa4f | False |
| 7a8d0b71 | False |

Questions with any non-zero bm25_normalized: 1; IDs: `["c5e8278d"]`.

## Gather20 versus rank50 mismatches

Explanation is copied, not inferred from correlated ranks or scores.

| Question | Memory | Mode | Gather20 rank | Rank50 rank | Gather vector limit | Rank vector limit | Gather20 rendered | Explanation |
|---|---|---|---|---|---|---|---|---|
| 0bb5a684 | 0770505d-0b96-4908-af07-2e692c034b9b | harness | 12 | 12 | 20 | 50 | True | unexplained |
| 0bb5a684 | 0770505d-0b96-4908-af07-2e692c034b9b | exact | 12 | 12 | 20 | 50 | True | unexplained |
| 0a34ad58 | 07c6d26d-8bd3-438b-aecc-6d8451f0ec5d | harness | 19 | 21 | 20 | 50 | True | unexplained |
| 0a34ad58 | 07c6d26d-8bd3-438b-aecc-6d8451f0ec5d | exact | 19 | 21 | 20 | 50 | True | unexplained |

## Decision — decide_exactness()

```json
{
  "candidates": [],
  "flags": [
    "PARITY_GAP",
    "QUERY_FORMULATION_CONFIRMED"
  ],
  "minimal": null,
  "missing_target_ranks": [
    {
      "bm25_rank": null,
      "exact_cosine_rank": 18,
      "memory_id": "0807f4f5-3e15-43bf-b776-857b726ad7e2",
      "question_id": "28dc39ac"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 14,
      "memory_id": "11672c8b-34a5-4b99-9fc5-b92c5fc17bc5",
      "question_id": "28dc39ac"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 15,
      "memory_id": "55eab6cb-17c0-4201-be69-9b3b70700029",
      "question_id": "6613b389"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 17,
      "memory_id": "94c365d4-d4cb-47c1-8661-9b9ff73a09f6",
      "question_id": "6613b389"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 16,
      "memory_id": "0770505d-0b96-4908-af07-2e692c034b9b",
      "question_id": "0bb5a684"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 24,
      "memory_id": "3c418fde-f8c6-4383-8513-9df463894cd9",
      "question_id": "0bb5a684"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 19,
      "memory_id": "07c6d26d-8bd3-438b-aecc-6d8451f0ec5d",
      "question_id": "0a34ad58"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 14,
      "memory_id": "3bb5fed7-48de-4aa3-9cbc-5ec9d1fb9d42",
      "question_id": "0a34ad58"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 33,
      "memory_id": "ddabe83c-c72c-4e28-b6b2-287102a9c442",
      "question_id": "0a34ad58"
    }
  ]
}
```

## Primary notes

Written by the primary (Opus) after the live run. The generated sections above are unedited.

- **Pre-registration and rulings.** The decision rules were recorded before any
  code or results existed, in
  `retrieval-exactness-preregistration-20261103.md` in the task evidence area.
  - Owner rulings: BUDGET_LIMITED, NONE_OF_THE_ABOVE, and the lexicographic
    minimal-change rule.
  - Primary clarification: tie-breaking across all changed variables.
  - The flags above are exactly what `decide_exactness()` returned.
- **PARITY_GAP: real, but inert for this corpus.**
  - All three benchmark connection types (the fact_harness pool, the campaign
    evaluation recipe and the sweep connection) run at `ivfflat.probes = 1`. That
    is pgvector's default: nothing set it before load, and there is no server,
    database or role setting. The production pool runs at 10.
  - In every T0 plan (3 questions, vector and BM25, probes 1 and 10), the planner
    chose an exact `idx_memories_user_id` bitmap scan plus sort, **never
    IVFFlat**. Per-user memory counts are 393–617.
  - T1 recall against the brute-force reference, for all 50 questions at every
    probes setting: recall@10 is 1.0, and recall@50 at LIMIT 100 is 1.0. Recall@50
    at LIMIT 10 is 0.2, which is the definitional ceiling of 10/50, not a
    shortfall.
  - In T2, the harness, probes10 and exact rows are identical at every pool and
    width.
  - So prior campaigns' retrieval was effectively exact, and the probes gap did
    not affect their results. It remains a latent risk if larger per-user tables
    make the planner choose IVFFlat, and it should be fixed (or explicitly
    pinned) in a separate change. Plan shapes were checked for 3 questions; the
    50-question recall equality is the broader evidence.
- **QUERY_FORMULATION_CONFIRMED.**
  - Pool size and exactness change nothing: every config exposes 0/5 cases at
    k=5 and 1/5 at k=10. Exact search with pool 100 and k=10 exposes 1/5.
  - The 9 missing target memories have exact cosine ranks 14–33, and **none has
    any BM25 match**. All-terms BM25 barely participates: in T3,
    `bm25_normalized` is non-zero only for one item in each of the 4 pool sizes
    for one question (`c5e8278d`) out of 50, and is 0 everywhere else.
  - Common-set composite order did not change with pool size (T3), so mechanism
    2 (pool-dependent BM25 normalisation) is inert on this corpus.
- **The sweep's rank/render mismatches.** The generated section reports
  "unexplained", because the generator does not infer causes. The primary's
  reading of this evidence, offered as an interpretation, not a tested causal
  claim:
  - Candidates are cut by **raw cosine** at `vector_limit = max(10, k)` *before*
    the composite rescoring (vector similarity, confidence, recency and source
    boosts).
  - `0770505d` has exact cosine rank 16. Under that cut it would fall outside
    the limit-15 pool at k=15. This audit did not test k=15, but it is consistent
    with the sweep rendering it only at k=20. At k=20 it is a candidate and
    rescores to 12th (gather20 rank 12 = rank50 rank 12).
  - `07c6d26d` has cosine rank 19. At k=20 it rescores to 19th and is rendered;
    at limit 50 it is 21st. The likely cause is that two candidates from beyond
    cosine rank 20 outscore it on the composite. That is inferred from the rank
    shift; the evidence does not record which candidates they are.
  - Both are consistent with the T1 cosine ranks and the T2 grid. Widening the
    pool at k ≤ 10 still exposes nothing new.
- **Proposed next step (not built, not run).** This is a query-decomposition
  design with an offline exposure pre-gate. It needs separate approval:
  1. For each multi-part question, produce saved sub-queries. The method
     (hand-written fixtures versus an LLM decomposer) is an owner decision.
  2. Embed the sub-queries once, under explicit approval. Embedding goes through
     the metered path, which writes account-ledger rows on the benchmark DB, so
     that write needs the owner's approval.
  3. Rerun this harness's exposure measurement on the union of per-sub-query
     retrievals, rendered at k ≤ 10.
  4. The pre-gate before any paid answer run: ≥ 4/5 omission cases strictly
     exposed, no COVERAGE_REGRESSION, no target budget drops, and abstention
     distractors reported.
- **Size.** The sanitized evidence (~8 MB) and this report are large, mostly
  because of the per-question T3 score components (IDs and numbers only). The
  raw output and its sha are retained outside Git.
- **Unchanged.** There is no diff under `orchestrator/memory/**`,
  `orchestrator/db.py`, the campaign modules or `fact_harness.py`.
  `INITIAL_VECTOR_CANDIDATES` = 10 and `MAX_MEMORY_ITEMS` = 5. Provider calls:
  0. Fingerprint `5cb6ab14…` before and after. No restore and no persistent
  setting.

