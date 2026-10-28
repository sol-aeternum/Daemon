# LongMemEval Phase D — dev-subset baseline, 2026-10-27

**Status:** complete. Every phase ran on one pinned worktree at `b342f625` (#576, checkpoint v4, one benchmark
user per question). All manifests report `baseline_qualified: true` and no `disqualifications`.

**Headline:** with qualified embeddings, the production memory pipeline answers **30 of 50 (60%)**
dev-subset questions strictly correct. A same-snapshot rerun scored **31 of 50 (62%)**. Arm B is **not** a
lexical-retrieval result: it retrieved no memory for any question, so its 6 of 50 (12%) is a no-memory floor
(see "Arm B").

These numbers are **not comparable** to the historical 10.4% / 67.8% / 81.1% results, or to the v3 smoke in
[`parity_baseline_2026-10-26.md`](../parity_baseline_2026-10-26.md). Any comparison must follow
[`ACCEPTANCE_RULE_PROPOSED.md`](../ACCEPTANCE_RULE_PROPOSED.md) (Proposed — owner approval required), which
never compares v3 and v4 runs.

## Scores (strict, 50 questions)

| Category | A: qualified embeddings | A2: rerun of A | B: no memory retrieved |
|---|---|---|---|
| IE-user | 9/9 | 9/9 | 1/9 |
| IE-assistant | 5/9 | 5/9 | 3/9 |
| IE-preference | 0/3 (3 partial) | 1/3 (2 partial) | 0/3 (3 partial) |
| Multi-session (MR) | 6/10 (1 partial) | 6/10 | 0/10 |
| Knowledge update (KU) | 5/9 | 5/9 | 1/9 |
| Temporal (TR) | 5/10 | 5/10 | 1/10 |
| **Total** | **30/50 (60%)** | **31/50 (62%)** | **6/50 (12%)** |
| of which abstention (`_abs`, 5 questions) | 1/5 | 1/5 | 2/5 |

- **No errored rows.** All three score files have `error_count: 0` (see "Evaluate retries").
- **The scorer prints `ABS: 0.0`.** No row carries an ABS category: the five `_abs` questions are scored
  under their base category (KU, TR, MR). That 0.0 has an empty denominator and is not a result. The
  abstention row above is counted by question id.
- `assert_substrate_match` passed on A and B.

## Evaluate noise (A vs A2)

A2 re-ran evaluate from the same snapshot.

- **Retrieval is deterministic:** `memory_context_sha256` is identical for 50 of 50 questions, so A and A2
  showed the reader exactly the same memories.
- **The variation comes from the reader and judge**, even at temperature 0 with seed 42:
  - 19 of 50 hypotheses differ in wording.
  - Two judgments flipped: `gpt4_372c3eed_abs` (MR) went partially correct → incorrect, and `75f70248`
    (IE-preference) went partially correct → correct.
  - The judge came back on four different system fingerprints in each run, while the reader used
    `fp_6d4f7d9eea` throughout.
- **One rerun is a single sample of F.** It suggests strict accuracy moves by about ±1 question (±2 points)
  on this subset. It is not a measured bound. Judge nondeterminism is tracked in #574.

## Arm B: a no-memory floor, not lexical retrieval

B ran evaluate with `EMBEDDING_ROUTE_ID` unset, which is what the runbook calls the lexical arm.

- **What happened:** every memory was written during ingest with
  `embedding_model = openrouter:azure:openai/text-embedding-3-small:1024`. Without a route, the production
  BM25 fallback only considers memories stored under the no-route models (`voyage-4-large` and configured
  fallbacks). So `memories_used` is 0 and the memory context is empty for all 50 questions.
- **This is production behaviour, not a harness fault.** It is filed as #578. The v3 smoke shows the same thing: its B leg retrieved nothing for 3 of 3 questions.
- **Owner decision:** B is reported as the no-memory floor, and the planned B2 rerun was skipped.
- **What B's 6 correct answers are:**
  - two abstentions, where "I don't have that information" is the right answer
  - three IE-assistant questions that general knowledge answers: Memrise, Veja, and 2–3 eggs for a French omelette
  - one judge false positive: `86f00804`, the same question and failure noted in the v3 smoke
- **What the A-vs-B gap does and doesn't show:** the gap (30 vs 6) measures what retrieved memory adds over no
  memory. It says nothing about embedding versus lexical retrieval.

## Ingest

| | |
|---|---|
| Questions / users | 50 / 50 (`uuid5` per question, namespace `ef9c86b2-…`) |
| Sessions | 2,126, all `complete`; 19 questions had sessions reordered by `haystack_date` |
| Chunks | 8,633, none failed |
| Memories | 25,435 active, all embedded through route `azure-openrouter-small-1024` |
| Extraction model | `openrouter/openai/gpt-6-luna` (route `luna-azure-eu`), 36,984 calls |
| Concurrency | 8 for the first 656 sessions, then 16 (owner request; graceful stop and resume) |
| Active time | 17,360 s (4.82 h); wall 5.03 h (07:23–12:26 UTC) |
| Throughput | about 357 sessions/h at 8 and about 740 sessions/h at 16. Provider latency bound (about 20 s per chunk); no 429s |
| Ledger spend | $19.19 luna + $0.03 embeddings = **$19.22** |

**Six sessions failed, and each became an interruption** (#576 "failure = interruption"):

- Every one was `extraction_failed`: four times on `982b5123_abs` / `sharegpt_xxk9JDz_21` (1 chunk each, no
  error class recorded), once with `EmbeddingRequestError` on `3ba21379`, and once on `gpt4_93159ced`.
- Each resume rolled the session back with `restored_pre_session_state: true`, then retried it.
- No question was rebuilt (`question_rebuilds` is empty).
- In each case the retried session completed. The failure records carry types and counts only.

## Evaluate retries

Evaluate runs one question at a time. The answer and judge calls go to `openrouter/openai/gpt-4o-2024-08-06`
(endpoint `azure`) and repeatedly hit upstream 429s ("Provider returned error", provider Azure), even though
the request rate was low.

The driver re-asked only the errored questions. `results_reconciliations` in each manifest shows nothing was
asked twice once it had succeeded. Invocations needed: A 4, B 7, A2 2. Every final row is complete.

## Spend

Within the approved $30 Phase D cap:

| Item | Spend |
|---|---|
| Attempt 1 at `3dcb4abf` (aborted by a network drop; superseded by this run) | $6.05 (ledger) |
| Ingest, this run | $19.22 (ledger) |
| Evaluate A, B, A2 (gpt-4o answer and judge, off-ledger) | about $1.13 from final row tokens at list price ($2.50 / M in, $10 / M out), plus at most about $0.2 of answers whose judge call then hit a 429 |
| **Total** | **about $26.6** |

## Reproduce and verify

- **Code:** `b342f62596acac14a596577e1caa1ce95f7d82ca`, clean, `code_sha256` `a0a029c9…` (in every manifest).
- **Hash chain:**
  - Each eval manifest pins the ingest manifest (`536436c1…`), the ingest checkpoint (`8c873c17…`) and the
    snapshot (`3fd65416…`).
  - None of those three files is committed byte-for-byte. The checkpoint and snapshot stay local (runbook §9).
    The ingest manifest is committed as `ingest/run_manifest.redacted.json`: identical except that the 12
    `corpus_key` fields in `ingest_failures` / `ingest_rollbacks` are removed, because gitleaks reads those
    SHA-256 session keys as API keys. `question_id` and `session_id` still identify each session.
  - The ingest DB fingerprint is `251dd9cf…`.
- **Paths:** manifests hold absolute local paths, kept so the hashes above still verify.
- **Manifest embedding field:** each manifest's `embedding.configuration` reads
  `unavailable / route_unapproved`. That was a stale pre-attestation snapshot, fixed in `8d034a29`. Route
  approval is recorded under `route_attestations` (9 of 9 attested on each invocation), and A's
  `retrieval_mode` shows the route was used.

## Files

| Path | Content |
|---|---|
| `ingest/run_manifest.redacted.json` | ingest manifest without the 12 failure/rollback `corpus_key` fields: invocations, failures, rollbacks, users, spend, fingerprint |
| `eval_*/run_manifest.json` | evaluate manifests: ingest source hashes, reconciliations, fingerprints |
| `eval_*/longmemeval_score.json` | scorer output |
| `eval_*/longmemeval_results.jsonl` | one row per question: public LongMemEval text, retrieved memory context, reader and judge output, hashes |
