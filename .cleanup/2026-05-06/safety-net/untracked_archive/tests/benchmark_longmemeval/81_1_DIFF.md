# 81.1% LongMemEval Artifact Diff

## Scope

- **Artifact landing commit**: `91ab1662` — `test(memory,skills): add coverage, benchmarks, and diagnostics`
- **Forensic commit range**: `c5a2a757^..91ab1662`
- **Lineage inside that range**:
  1. `c5a2a757` (2026-04-10) — introduced legacy `tests/longmemeval/evaluate.py` and `tests/longmemeval/ingest.py`
  2. `3c91be24` (2026-04-17 00:50) — introduced `orchestrator/eval/{longmemeval.py,runner.py,longmemeval_fast.py,diagnostics.py}`
  3. `91ab1662` (2026-04-17 00:55) — committed the `81.1%` raw artifact bundle and rewrote evaluator/ingest semantics

## Affected files in the archaeology range

```text
M  orchestrator/config.py
A  orchestrator/eval/diagnostics.py
A  orchestrator/eval/longmemeval.py
A  orchestrator/eval/longmemeval_fast.py
A  orchestrator/eval/runner.py
A  tests/benchmark_results/longmemeval_tier2_fast.json
A  tests/benchmark_results/longmemeval_tier2_fast.md
A  tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_checkpoint.json
A  tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_results.jsonl
A  tests/longmemeval/evaluate.py
A  tests/longmemeval/ingest.py
```

`tests/benchmark_results/longmemeval_tier2_fast/run.log` exists in the saved fixture and is useful evidence, but it was **not** committed in `91ab1662`, so it cannot be git-diffed directly.

## Proven fixture facts

- The raw checkpoint stores `dataset_path = "/app/tests/benchmark_results/longmemeval_s.json"`.
- The saved run log shows answer and judge calls going through `openai/gpt-4o` on OpenRouter.
- The raw results/checkpoint contain **500 clean rows** and no `error` field.
- The raw result rows carry `memories_used`, `chunk_count`, `session_count`, and `source_type = "import"`, which ties the artifact to the fast harness rather than the older extraction-driven path.

## Material diffs by class

### 1) Runner / harness shape

**Historical progression**

- `c5a2a757` had **no** `orchestrator/eval/*` runner layer. Benchmarking was done via the legacy `tests/longmemeval/evaluate.py` / `ingest.py` scripts.
- `3c91be24` added the canonical runner split:
  - `orchestrator/eval/longmemeval.py` CLI wrapper
  - `orchestrator/eval/runner.py` shared-corpus canonical harness
  - `orchestrator/eval/longmemeval_fast.py` direct-insert fast harness
  - `orchestrator/eval/diagnostics.py` retrieval-failure classifier
- `91ab1662` did **not** change the fast runner itself; it committed the raw `81.1%` bundle on top of the `3c91be24` runner shape.

**Material runner facts tied to the artifact**

- `orchestrator/eval/longmemeval_fast.py` introduced:
  - isolated per-run benchmark users (`build_benchmark_user()` / `ensure_benchmark_user()`)
  - question-scoped cleanup before and after each row
  - direct chunk insertion with `DEFAULT_CHUNK_MAX_CHARS = 4000` and `DEFAULT_OVERLAP_TURNS = 2`
  - `chunk_count`, `session_count`, and `source_type` in result rows
  - `force_retrieval_logging=True`
- The committed summary itself names the harness as `orchestrator.eval.longmemeval_fast`, so the `81.1%` artifact belongs to the fast path, not `orchestrator.eval.runner.LongMemEvalRunner`.

**Exact diffs**

```bash
GIT_MASTER=1 git diff c5a2a757 3c91be24 -- orchestrator/eval/longmemeval.py orchestrator/eval/runner.py orchestrator/eval/longmemeval_fast.py orchestrator/eval/diagnostics.py
```

### 2) Matcher / retrieval logic

**Before `91ab1662` (`c5a2a757` / `3c91be24`)**

- `tests/longmemeval/evaluate.py` retrieved memories via `retrieve_memories(...)`.
- It manually prefixed `get_l0_memories(user_id)` and deduped IDs in Python.
- Retrieval always targeted the shared `TEST_USER_ID` and had **no** `allowed_source_conversation_ids` scoping.

**At `91ab1662`**

- `retrieve_user_memories()` was rewritten to call `retrieve_memories_for_text(...)` with:
  - `include_l0=True`
  - `log_retrieval=...`
  - `allowed_source_conversation_ids=...`
  - `retrieval_triggered_by="longmemeval"`
  - `include_dream_observations=True`
- `evaluate_single()` gained explicit `user_id`, `log_retrieval`, and `allowed_source_conversation_ids` parameters.

**Why this matters**

- The artifact rows are fast-harness rows, so they were judged after **conversation-scoped retrieval** through the fast harness’s `conversation_ids` list, not the older shared-user/global retrieval path.

**Exact diffs**

```bash
GIT_MASTER=1 git diff 3c91be24 91ab1662 -- tests/longmemeval/evaluate.py
```

### 3) Judge prompt / answer matcher semantics

**Before `91ab1662`**

- `judge_answer(hypothesis, reference)` compared only reference vs hypothesis.
- Prompt asked for exactly one word: `correct`, `incorrect`, or `partially_correct`.
- Parser used loose substring heuristics on the whole response.

**At `91ab1662`**

- Signature became `judge_answer(question_text, hypothesis, reference)`.
- Prompt now includes:
  - the question text
  - explicit generous CORRECT/PARTIAL/INCORRECT rules
  - an instruction to be generous with CORRECT for paraphrases / extra detail
- Parser now reads the **first line** as `CORRECT | PARTIAL | INCORRECT` before falling back to looser matching.

**Why this matters**

- The `81.1%` bundle contains `311 correct / 189 partially_correct / 0 incorrect`, so the permissive `91ab1662` judge rewrite is a material part of the artifact context.

**Exact diffs**

```bash
GIT_MASTER=1 git diff 3c91be24 91ab1662 -- tests/longmemeval/evaluate.py
```

### 4) Fixture / ingest semantics

**Legacy ingest at `c5a2a757`**

- `tests/longmemeval/ingest.py` walked every dataset entry and every haystack session directly.
- It used a shared benchmark user (`TEST_USER_ID`).
- It did **not** deduplicate equivalent sessions across questions.

**At `91ab1662`**

- `tests/longmemeval/ingest.py` added corpus hashing / dedup:
  - `normalize_session_messages()`
  - `build_corpus_key()` using SHA-256
  - `CorpusPlan` / `CorpusSession`
  - `raw_session_ids` tracking
- The legacy ingest script shifted from per-entry iteration to deduplicated corpus-session iteration.

**Why this matters**

- The historical fixture’s `chunk_count` / `session_count` fields do **not** come from the legacy extraction ingest path; they come from the fast harness’s direct session-chunk inserts.
- The `91ab1662` evaluator and ingest rewrites were committed in the same change as the raw artifact bundle, so the committed artifact already assumes the newer corpus-aware semantics.

**Exact diffs**

```bash
GIT_MASTER=1 git diff c5a2a757^ c5a2a757 -- tests/longmemeval/ingest.py tests/longmemeval/evaluate.py
GIT_MASTER=1 git diff 3c91be24 91ab1662 -- tests/longmemeval/ingest.py tests/longmemeval/evaluate.py
```

### 5) Config provenance

**Committed config changes in range**

- `c5a2a757` added memory-consolidation knobs and switched tier embedding defaults toward Voyage.
- `14c142c2` (already inside the range) added explicit Voyage embedding settings and dedup thresholds:
  - `embedding_document_model = "voyage-4-large"`
  - `embedding_query_model = "voyage-4-lite"`
  - `embedding_dimensions = 1024`
- `3c91be24` added retrieval-logging config:
  - `retrieval_logging_enabled = False`
  - `retrieval_logging_debug = False`

**What did not change**

- No LongMemEval-specific model slot or hidden benchmark-only env knob was added in `orchestrator/config.py` for top-k or judge weighting.
- Answer/judge model IDs remained hardcoded in `tests/longmemeval/evaluate.py` as `openrouter/openai/gpt-4o`.
- Provider resolution still flowed through `get_settings().get_provider_config("openrouter")` with the general `request_timeout_s = 90.0`.

**Why this matters**

- The fast harness inserted chunk embeddings using `settings.embedding_document_model`, so the artifact is tied to the general Voyage document-embedding config in repo history.
- The artifact is **not** explained by a benchmark-only config switch living in git.

**Exact diffs**

```bash
GIT_MASTER=1 git diff c5a2a757^ 91ab1662 -- orchestrator/config.py
```

### 6) Result artifact / accounting layer

**What `91ab1662` committed**

- `tests/benchmark_results/longmemeval_tier2_fast.json`
- `tests/benchmark_results/longmemeval_tier2_fast.md`
- `tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_checkpoint.json`
- `tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_results.jsonl`

**Material evidence**

- `git show 91ab1662:tests/benchmark_results/longmemeval_tier2_fast.json` records:
  - `overall_accuracy = 0.811`
  - `judgments = {correct: 311, partially_correct: 189, incorrect: 0}`
  - notes claiming the 11 FK failures were rerun cleanly via checkpoint resume
- But the committed scorer still does:

```python
if judgment == "correct":
    category_scores[category]["correct"] += 1
```

- Therefore the committed scorer would report **311 / 500 = 62.2%**, not `81.1%`.

**Conclusion**

- The `81.1%` number was **not** produced by the committed `score_accuracy()` implementation in the archaeology range.
- It is a summary-layer weighted/manual/post-processed value written into the artifact summary files.

**Concrete repaired-row example**

- `run.log:267-295` shows question `21436231` failing with `memories_source_conversation_id_fkey`.
- `longmemeval_fast_results.jsonl:25` stores the same question as a clean `partially_correct` row with `chunk_count = 323` and `session_count = 50`.

**Provenance gap**

- The saved `run.log` shows only one fresh run (`0 already checkpointed`) and no later `skip (checkpoint)` lines.
- So the final clean checkpoint/results and the saved run log do **not** come from one fully preserved, git-traceable execution record.

**Exact diffs**

```bash
GIT_MASTER=1 git diff 3c91be24 91ab1662 -- tests/benchmark_results/longmemeval_tier2_fast.json tests/benchmark_results/longmemeval_tier2_fast.md tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_checkpoint.json tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_results.jsonl
GIT_MASTER=1 git show 91ab1662:tests/benchmark_results/longmemeval_tier2_fast.json
```

## Bottom line

- The historical `81.1%` artifact belongs to the **fast harness** introduced in `3c91be24`, not the older legacy scripts and not the canonical runner path.
- The raw fixture proves a clean final checkpoint/results bundle, but the only saved run log still captures the earlier FK-failure pass.
- The committed judge and retrieval semantics were both materially rewritten in `91ab1662` immediately before the artifact was committed.
- The committed scorer remained **correct-only**, so `81.1%` is not a direct output of the benchmark code preserved in git.

## Reproduction commands used for archaeology

```bash
GIT_MASTER=1 git log --oneline -- tests/benchmark_results/longmemeval_tier2_fast.json tests/benchmark_results/longmemeval_tier2_fast.md
GIT_MASTER=1 git log --oneline -- orchestrator/eval/longmemeval_fast.py orchestrator/eval/runner.py orchestrator/eval/longmemeval.py orchestrator/eval/diagnostics.py tests/longmemeval/evaluate.py tests/longmemeval/ingest.py
GIT_MASTER=1 git diff c5a2a757^ c5a2a757 -- tests/longmemeval/evaluate.py tests/longmemeval/ingest.py
GIT_MASTER=1 git diff c5a2a757 3c91be24 -- orchestrator/eval/longmemeval.py orchestrator/eval/runner.py orchestrator/eval/longmemeval_fast.py orchestrator/eval/diagnostics.py tests/longmemeval/evaluate.py tests/longmemeval/ingest.py
GIT_MASTER=1 git diff 3c91be24 91ab1662 -- tests/longmemeval/evaluate.py tests/longmemeval/ingest.py tests/benchmark_results/longmemeval_tier2_fast.json tests/benchmark_results/longmemeval_tier2_fast.md tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_checkpoint.json tests/benchmark_results/longmemeval_tier2_fast/longmemeval_fast_results.jsonl
GIT_MASTER=1 git diff c5a2a757^ 91ab1662 -- orchestrator/config.py
```
