# Memory prompt-surface exposure sweep — 2026-11-03

Diagnostic only; no production candidate, answer generation or judging.
Historical clock: question_as_of plus RECENCY_REFERENCE_TIME; remapped corpus.
Database reads use one read-only repeatable-read snapshot; fingerprints do not detect concurrent commits outside that snapshot.
Git: `99bc5e0bb534151146a237b9e90f882fc33d9fee`. MAX_MEMORY_ITEMS: 5 → 5.
Fingerprint before: `5cb6ab14f2eb6bf11bb5e4c5ed164a08b70d0fb92155918d006be4f630b874f3`; after: `5cb6ab14f2eb6bf11bb5e4c5ed164a08b70d0fb92155918d006be4f630b874f3`.
Raw SHA256: `691f7d77d72a32d0e81d00a81ecebacb6e3689ff56b25d07187d657a52c1f959`.

## Input pins

- cases: `c54798d04a3136921b86841043eb22c4d3fb27c897e8eff68325ec173bbb11f2`
- dataset: `6612e3a03c79b92bab2f1750d4661908e3dc37359715b7fb6b62c6f758bddb34`
- evidence_index: `3fe6e32efc329719728ade67fc677dfe7195688f8afdb3b147b3cc31ebac3043`
- ingest_checkpoint: `fbf6910d16f7a533c78b06c49f723ab579ce1b87f57eb01730975e0957470478`
- ingest_manifest: `536436c12bfb2fcb40c9c128389060f3a4f65ad4d3eb3dfa013a9478a97c94b6`
- phase3b_manifest: `616f767ff1432442e425e49c2542a56dc8c499744399836e12cb149ccb03bda5`
- pilot_manifest: `348f1970eea4c49ed8f0c61b8dbba95563d6d0949347c695aadf507c46284704`
- pilot_results: `1136c9af6011b767b9a1b9eda8e92e3c06e5e93ff35906e5c77ced0e7cf68105`

## Calls and cost

Provider calls: 0; embedding: 0; chat: 0; cost: USD 0 (zero calls, not a token-price estimate).
Query vectors reused from retrieval_log; exact normalized-question/model match and exactly one row in the inclusive pilot A1 window.
Pinned vector window: [2026-11-02T09:55:58+00:00, 2026-11-02T10:34:15+00:00]. Questions with differing out-of-window rows: 3/50.
Model: `openai/text-embedding-3-small`; storage: `openrouter:azure:openai/text-embedding-3-small:1024`; dimensions: 1024.

## Prior evidence

2026-10-31 zero-call diagnostic: Run on the original corpus before the historical clock (control 7eba3df3, logged vectors, 0 provider calls). Width 10/20/30 fully covered 1/5 omission cases (1192316e). Needed-fact final ranks at pool 60 were 12-34. Width-5 replay was byte-identical on 50/50. Conclusion: the gap is query formulation, not the width cutoff. Evidence: coverage-preflight-findings-20261031.md (not committed).
v3 pilot: Ran 2026-11-02 at fd8b62a0 and FAILED: majority flips 4/50 vs matched-sample flips 10/150. Candidate mode is blocked under v3. All 13 approved restores are consumed. Result: #601 issuecomment-6586631920.
All approved restores are consumed (13/13). The failed v3 pilot is not proposed again.

## Exposure by width

Gold-session coverage is a proxy, not proof of answer-fact exposure. Empty gold is None.
| k | Strict /5 | Any /5 | Pre-budget /5 | Coverage mean (proxy) | Changed questions | Coverage losses | Budget drops (target) | Tokens | Effective items | Duplicates exact / similar |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 0 | 4 | 0 | 0.8953333333333333 | 0 | 0 | 0 (0) | 7430 | 250 | 0 / 5 |
| 10 | 1 | 4 | 1 | 0.9493333333333334 | 7 | 0 | 0 (0) | 13422 | 500 | 0 / 9 |
| 15 | 1 | 5 | 1 | 0.9533333333333333 | 7 | 0 | 0 (0) | 19602 | 750 | 0 / 14 |
| 20 | 3 | 5 | 3 | 0.9533333333333333 | 7 | 0 | 0 (0) | 25787 | 1000 | 0 / 17 |

Duplicate rules: `casefold_whitespace_collapse_equality` and `casefold_alphanumeric_token_set_jaccard_ge_0.8`; occupancy counts slots matching an earlier line, not pairs. Similarity includes exact duplicates.

## Per-target ranks

| Question | Memory ID | Rank (limit 50; None if absent) | Rendered widths | Budget-dropped widths |
|---|---|---|---|---|
| 28dc39ac | 06729686-3261-40ed-8ff6-df507bf435c3 | 1 | 5, 10, 15, 20 | none |
| 28dc39ac | 0807f4f5-3e15-43bf-b776-857b726ad7e2 | 15 | 20 | none |
| 28dc39ac | 11672c8b-34a5-4b99-9fc5-b92c5fc17bc5 | 12 | 15, 20 | none |
| 28dc39ac | 8925ddbd-0184-4573-b2c5-a7f91bf5e677 | 2 | 5, 10, 15, 20 | none |
| 28dc39ac | 99db9c59-462a-4f38-bc8b-7c4261a8437a | 7 | 10, 15, 20 | none |
| 28dc39ac | f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f | 6 | 10, 15, 20 | none |
| 1192316e | 2b34b3b2-589a-43eb-908e-3e2bedf033e5 | 1 | 5, 10, 15, 20 | none |
| 1192316e | b2239692-8040-4005-abaa-79588eedf2b1 | 7 | 10, 15, 20 | none |
| 6613b389 | 21ac11f9-9689-4571-af6f-3c5c7c858260 | 1 | 5, 10, 15, 20 | none |
| 6613b389 | 55eab6cb-17c0-4201-be69-9b3b70700029 | 14 | 15, 20 | none |
| 6613b389 | 94c365d4-d4cb-47c1-8661-9b9ff73a09f6 | 17 | 20 | none |
| 0bb5a684 | 0770505d-0b96-4908-af07-2e692c034b9b | 12 | 20 | none |
| 0bb5a684 | 3c418fde-f8c6-4383-8513-9df463894cd9 | 34 | none | none |
| 0bb5a684 | e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8 | 1 | 5, 10, 15, 20 | none |
| 0a34ad58 | 07c6d26d-8bd3-438b-aecc-6d8451f0ec5d | 21 | 20 | none |
| 0a34ad58 | 3bb5fed7-48de-4aa3-9cbc-5ec9d1fb9d42 | 13 | 15, 20 | none |
| 0a34ad58 | ddabe83c-c72c-4e28-b6b2-287102a9c442 | 37 | none | none |

## Prefix stability and pilot self-check

k=5 pilot block hash matches: 50/50; recoverable: 50/50. Mismatches are findings, not stops.
Pilot comparison assumptions: width=5 (unverified); historical clock=True (verified); model=`openai/text-embedding-3-small` (unverified); route=`azure-openrouter-small-1024` (verified). Unverified means the pilot manifest does not expose the fact; hash equality alone is not provenance verification.

| k | Stable top-5 /50 | Unstable IDs |
|---|---|---|
| 5 | 50 | none |
| 10 | 50 | none |
| 15 | 49 | e8a79c70 |
| 20 | 48 | e8a79c70, 852ce960 |

## Abstention distractor early warning

Rule: `first_capitalized_content_token_else_first_content_token_overlap`. First capitalized non-stopword alphanumeric token, else first non-stopword token; an added rendered memory sharing it is plausible support, not a verdict.
Overreach source: `tests/benchmark_results/failure_triage_phase2_2026-10-28.md:132`. Dev _abs and overreach membership may overlap.

| Question | Dev _abs | Overreach | k | Added items | Plausible support |
|---|---|---|---|---|---|
| f685340e_abs | True | True | 5 | 0 | 0 |
| f685340e_abs | True | True | 10 | 5 | 0 |
| f685340e_abs | True | True | 15 | 10 | 0 |
| f685340e_abs | True | True | 20 | 15 | 0 |
| 982b5123_abs | True | False | 5 | 0 | 0 |
| 982b5123_abs | True | False | 10 | 5 | 2 |
| 982b5123_abs | True | False | 15 | 10 | 2 |
| 982b5123_abs | True | False | 20 | 15 | 3 |
| gpt4_93159ced_abs | True | True | 5 | 0 | 0 |
| gpt4_93159ced_abs | True | True | 10 | 5 | 0 |
| gpt4_93159ced_abs | True | True | 15 | 10 | 0 |
| gpt4_93159ced_abs | True | True | 20 | 15 | 2 |
| 80ec1f4f_abs | True | False | 5 | 0 | 0 |
| 80ec1f4f_abs | True | False | 10 | 5 | 0 |
| 80ec1f4f_abs | True | False | 15 | 10 | 0 |
| 80ec1f4f_abs | True | False | 20 | 15 | 0 |
| gpt4_372c3eed_abs | True | True | 5 | 0 | 0 |
| gpt4_372c3eed_abs | True | True | 10 | 5 | 0 |
| gpt4_372c3eed_abs | True | True | 15 | 10 | 0 |
| gpt4_372c3eed_abs | True | True | 20 | 15 | 0 |

## Decision

`INVESTIGATE_RETRIEVAL`; chosen k: None.
Reasons: strict_threshold_not_met.
Investigate retrieval; every candidate width has fewer than four strictly rendered cases. No campaign.

## Coverage changes

Loss attribution is prefix_instability when any baseline memory from a lost gold session is absent from the new pre-budget set; otherwise it is non_target_budget_drop. The former takes precedence if both mechanisms occur.
- k=10: changed=['28dc39ac', '1192316e', '6613b389', '80ec1f4f_abs', 'gpt4_372c3eed_abs', '0bb5a684', 'gpt4_65aabe59']; losses=[]; causes={}.
- k=15: changed=['28dc39ac', '1192316e', '6613b389', '80ec1f4f_abs', 'gpt4_372c3eed_abs', '0bb5a684', 'gpt4_65aabe59']; losses=[]; causes={}.
- k=20: changed=['28dc39ac', '1192316e', '6613b389', '80ec1f4f_abs', 'gpt4_372c3eed_abs', '0bb5a684', 'gpt4_65aabe59']; losses=[]; causes={}.

## Primary notes (added after the run; the generated sections above are unedited)

- **Pre-registration.** Before any sweep code or results existed, the owner fixed
  the decision chain, the strict "all named memories rendered" definition and the
  zero-call vector source. The text is in
  `exposure-sweep-preregistration-20261103.md` in the task evidence area. The
  chain and thresholds are code constants in `orchestrator/eval/exposure.py`, and
  have not changed since commit `711741a6`, the first commit of this work.
- **Vector window.** This was a method choice, not a threshold. It was recorded
  before the successful run, after the first run stopped on differing logged
  duplicates. Each question has two logged vectors: one from 2026-10-30 and one
  from the v3 pilot A1 run. 47 of the 50 pairs are byte-identical; 3 differ only by
  provider float jitter (cosine ≥ 0.9999995). The A1 vector is the one behind the
  A1 prompts, which the k=5 self-check reproduced 50/50.
- **Units.** The `Tokens` and `Effective items` columns are totals across the 50
  questions. Per question, k=5 averages about 149 estimated tokens and k=20 about
  516.
- **Ranks.** The rank column comes from the separate limit-50
  `retrieve_memories_for_text` pass, not from positions in the gather path. The
  two can disagree: `0770505d` ranks 12th but is rendered only at k=20, and
  `07c6d26d` ranks 21st but is rendered at k=20. The cause is not investigated
  here. The decision doesn't depend on it, because two cases need facts that
  aren't rendered at any width ≤ 20 (`3c418fde`, rank 34; `ddabe83c`, rank 37).
- **Comparison with 2026-10-31.** That diagnostic, on the original corpus before
  the historical clock, fully covered 1/5 cases at width 10. This sweep
  reproduces 1/5 at k=10 and adds 3/5 at k=20, still below the threshold of 4.
  At k=20, the rendered memory block costs about 3.5× the tokens of k=5. It also
  adds lexical false-premise candidates for two abstention questions:
  `982b5123_abs` (3) and `gpt4_93159ced_abs` (2).
- **Outcome.** No width campaign is proposed. Any follow-up should look at query
  formulation for multi-part questions (for example, decomposition or
  multi-query retrieval). That would be a design change, needing its own approval
  and budget.
