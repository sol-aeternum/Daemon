# LongMemEval Benchmark Config Pinning Inventory

Date: 2026-04-18

## Scope

This inventory is sourced from the live harness and memory pipeline files, not from `tests/benchmark_longmemeval/`:

- `orchestrator/config.py`
- `orchestrator/memory/retrieval.py`
- `orchestrator/memory/embedding.py`
- `orchestrator/memory/extraction.py`
- `orchestrator/memory/dedup.py`
- `orchestrator/eval/longmemeval.py`
- `orchestrator/eval/runner.py`
- `orchestrator/eval/longmemeval_fast.py`
- `tests/longmemeval/ingest.py`
- `tests/longmemeval/evaluate.py`

`tests/benchmark_longmemeval/` is an artifact directory only.

## Authority legend

| Authority type | Meaning in this inventory |
| --- | --- |
| Live config | A `get_settings()` / `Settings` field from `orchestrator/config.py`; env-overridable at runtime |
| Env | A provider credential/base-url/runtime setting ultimately sourced from process env / `.env` |
| Hardcoded constant | A constant, function default, or inline literal in benchmark/runtime code |
| Prompt template | A hardcoded prompt string/builder that shapes extraction, answer, or judging |
| Dataset path | A CLI path or default local dataset location |
| Checkpoint fixture | Output/checkpoint filenames or JSON contracts that control resume/scoring behavior |

## Canonical-vs-fast lane summary

| Lane | Intended use | Extra authorities beyond shared eval path | Must not be used to claim |
| --- | --- | --- | --- |
| Canonical (`python -m orchestrator.eval.longmemeval ...`) | Full ingest → extract → dedup → retrieve → answer → judge → score | Canonical corpus plan, extraction prompt/model, dedup thresholds, shared benchmark user, canonical checkpoint/score contract | Nothing outside the live canonical pipeline |
| Fast (`python -m orchestrator.eval.longmemeval_fast ...`) | Retrieval/answer/judge/chunking studies only | Per-run UUID user, direct chunk inserts, `DEFAULT_CHUNK_MAX_CHARS`, `DEFAULT_OVERLAP_TURNS`, fast checkpoint/result filenames | Extraction, dedup, or corpus-ingest claims |

## Inventory

### 1. Dataset, lane, and artifact contract

| Variable / surface | Current source | Canonical lane | Fast lane | Recommended pinning authority | Notes |
| --- | --- | --- | --- | --- | --- |
| Dataset file path (`--dataset`) | Dataset path via CLI in `orchestrator/eval/longmemeval.py:20-60` and `orchestrator/eval/longmemeval_fast.py:537-578` | Required explicit CLI path; `LongMemEvalRunner(dataset_path=...)` refuses to infer it | Required explicit CLI path | Dataset path | Benchmark claims should always name the exact dataset file passed on the command line. |
| Legacy dataset fallback (`DATASET_PATH`, `DATASET_URL`) | Hardcoded dataset path/url in `tests/longmemeval/ingest.py:35-36,150-173` and imported as `DEFAULT_DATASET_PATH` in `tests/longmemeval/evaluate.py:49,578-582` | Not used by canonical CLI unless someone runs legacy adapters directly | Not used | Dataset path | Keep for compatibility/debugging only; benchmark pinning should prefer explicit CLI dataset paths over `/tmp/longmemeval-review/data/longmemeval_s.json`. |
| Subset size (`--limit`) | Hardcoded CLI option in `orchestrator/eval/longmemeval.py:55-60`, `orchestrator/eval/longmemeval_fast.py:557-562`, and legacy adapters | Limits dataset/questions in runner | Limits dataset/questions in fast runner | Dataset path | Treat subset selection as part of the dataset fixture contract; a benchmark claim without the limit value is underspecified. |
| Canonical result/checkpoint/score files (`RESULTS_FILENAME`, `CHECKPOINT_FILENAME`, `SCORE_FILENAME`, `CHECKPOINT_VERSION`) | Checkpoint fixture contract in `tests/longmemeval/evaluate.py:83-85`, `orchestrator/eval/runner.py:39-42,54-62,108-166` | `longmemeval_results.jsonl`, `longmemeval_checkpoint.json`, `longmemeval_score.json`, checkpoint version `2` | N/A | Checkpoint fixture | Canonical replay/resume/scoring authority lives in the runner checkpoint/result contract, not in ad-hoc filenames. |
| Fast result/checkpoint files (`RESULTS_FILENAME`, `CHECKPOINT_FILENAME`) | Checkpoint fixture contract in `orchestrator/eval/longmemeval_fast.py:31-33,71-78` | N/A | `longmemeval_fast_results.jsonl`, `longmemeval_fast_checkpoint.json` | Checkpoint fixture | Keep fast-lane artifacts separate from canonical artifacts; never mix them in the same claim. |
| Benchmark user identity (`TEST_USER_ID`, `TEST_USER_EMAIL`, `build_benchmark_user(run_id)`) | Hardcoded constant in `tests/longmemeval/ingest.py:37-39,176-199` and randomized fast user in `orchestrator/eval/longmemeval_fast.py:105-111,431-433` | Shared fixed benchmark user | Fresh per-run UUID/email | Hardcoded constant (canonical) and hardcoded fast-lane helper (fast) | Isolation strategy affects contamination risk, so it belongs in the benchmark authority list. |

### 2. Canonical ingest / extraction / dedup authorities

| Variable / surface | Current source | Canonical lane | Fast lane | Recommended pinning authority | Notes |
| --- | --- | --- | --- | --- | --- |
| Extraction model (`model = "openrouter/openai/gpt-4o-mini"`) | Hardcoded constant in `orchestrator/memory/extraction.py:394-400,535-550` | Active during `process_extraction(...)` from `tests/longmemeval/ingest.py:298-304` | Bypassed | Hardcoded constant | Treat the extraction model as a fixed benchmark reference, not a swap candidate. |
| `EXTRACTION_PROMPT` | Prompt template in `orchestrator/memory/extraction.py:140-309`, used at `:421-424` | Active | Bypassed | Prompt template | Canonical extraction behavior is prompt-defined; this prompt must be pinned with the harness narrative. |
| Extraction sampling/input limits (`MAX_EXTRACTION_INPUT_CHARS`, `EXTRACTION_TEMPERATURE`, `EXTRACTION_TOP_P`, `EXTRACTION_MAX_TOKENS`) | Hardcoded constants in `orchestrator/memory/extraction.py:48-51,403-429` | Active | Bypassed | Hardcoded constant | These directly change extraction coverage and determinism. |
| Extraction confidence calibration (`DEFAULT_EXTRACTED_CONFIDENCE`, `HEDGE_OVERRIDE_CONFIDENCE`, `STRONG_OVERRIDE_CONFIDENCE`, `CORRECTION_MIN_CONFIDENCE`) | Hardcoded constants in `orchestrator/memory/extraction.py:52-55,321-347,482-489` | Active | Bypassed | Hardcoded constant | Canonical scoring can move if calibration changes even when retrieval code stays constant. |
| Document embedding model (`embedding_document_model`) | Live config via `Settings.embedding_document_model` in `orchestrator/config.py:224-227`; consumed by `orchestrator/memory/embedding.py:213-220`, `orchestrator/memory/dedup.py:36-38`, and `orchestrator/eval/longmemeval_fast.py:376-385` | Used for dedup/searchable stored memories | Used for direct chunk inserts | Live config | This is one of the main reproducibility knobs that must remain config-backed, not re-hardcoded in `dedup.py`. |
| Embedding dimensionality (`embedding_dimensions`) | Live config in `orchestrator/config.py:227`; consumed by `orchestrator/memory/embedding.py:183-195` | Active | Active | Live config | Different dimensions change stored vectors and retrieval comparability. |
| Dedup thresholds (`dedup_merge_threshold`, `dedup_supersede_threshold`, `dedup_supersede_same_slot_threshold`) | Live config in `orchestrator/config.py:229-246`; read through `orchestrator/memory/dedup.py:57-66,282-299,386-431` | Active | Bypassed | Live config | `dedup.py` is an adapter here, not the authority. Deprecated threshold constants must not regain authority. |
| Dedup contradiction check (`background_reasoning_model`, temperature `0.1`) | Live config for model in `orchestrator/config.py:258-260` and hardcoded temperature in `orchestrator/memory/dedup.py:142-166` | Active during supersession paths | Bypassed | Live config for model; hardcoded constant for temperature | Contradiction metadata can influence supersession outcomes and therefore canonical corpus contents. |
| Canonical ingest corpus plan (`build_corpus_plan`) | Hardcoded normalization/dedupe logic in `tests/longmemeval/ingest.py:65-147` | Active | Bypassed | Hardcoded constant | Canonical runs ingest deduped corpus sessions, not raw haystack rows one-for-one. |

### 3. Shared retrieval, answer, and judge authorities

| Variable / surface | Current source | Canonical lane | Fast lane | Recommended pinning authority | Notes |
| --- | --- | --- | --- | --- | --- |
| OpenRouter provider route for extraction/answer/judge (`openrouter_base_url`, `openrouter_api_key`, `openrouter_referer`, `openrouter_title`, `request_timeout_s`) | Live config/env via `orchestrator/config.py:68-69,171-174,363-418`; consumed by `tests/longmemeval/evaluate.py:191-229` and `orchestrator/memory/extraction.py:19-45` | Active | Active | Env | Models are fixed references, but the provider endpoint/timeout/auth path is still env-backed and should be recorded with each run. |
| Answer model and sampling (`ANSWER_MODEL`, `ANSWER_TEMPERATURE`, `ANSWER_MAX_TOKENS`) | Hardcoded constants in `tests/longmemeval/evaluate.py:90-92,319-337` | Active | Active (reused through `evaluate_single`) | Hardcoded constant | Treat `ANSWER_MODEL` as a pinned benchmark reference; do not swap it through tier or env config. |
| Answer prompt (`build_answer_prompt`) | Prompt template in `tests/longmemeval/evaluate.py:251-261` | Active | Active | Prompt template | The benchmark answer contract is currently a concise memory-only prompt. |
| Judge model and sampling (`JUDGE_MODEL`, `JUDGE_TEMPERATURE`, `JUDGE_MAX_TOKENS`) | Hardcoded constants in `tests/longmemeval/evaluate.py:94-96,271-316` | Active | Active | Hardcoded constant | Treat `JUDGE_MODEL` as a pinned benchmark reference; do not swap it during recovery work. |
| Judge rubric prompt (`judge_answer`) | Prompt template in `tests/longmemeval/evaluate.py:271-286` | Active | Active | Prompt template | Judge drift lives here, so the rubric must be pinned alongside the judge model ID. |
| Query embedding model (`embedding_query_model`) | Live config via `orchestrator/config.py:225-226`; consumed by `orchestrator/memory/embedding.py:223-233` and recorded in `orchestrator/memory/retrieval.py:263-270,652-683` | Active | Active | Live config | Answer/judge are fixed, but query embeddings remain a live config authority and must be snapshotted for reproducibility. |
| Retrieval call-site contract (`TOP_K_MEMORIES`, `include_l0=True`, `include_dream_observations=True`, `retrieval_triggered_by="longmemeval"`, `allowed_source_conversation_ids`) | Hardcoded defaults/call-site flags in `tests/longmemeval/evaluate.py:87-88,340-360,363-386` and runner wiring in `orchestrator/eval/runner.py:423-439`, `orchestrator/eval/longmemeval_fast.py:478-487` | Shared retrieval uses question-scoped allowlist over canonical corpus conversations | Shared retrieval uses question-scoped allowlist over fast question conversations | Hardcoded constant | `TOP_K_MEMORIES` is the real top-k authority today; both lanes also hardcode `include_l0=True` and `include_dream_observations=True`. |
| Retrieval logging override (`retrieval_logging_enabled`, `retrieval_logging_debug`, `force_retrieval_logging=True`) | Live config flags in `orchestrator/config.py:267-272`, but benchmark CLIs hardcode `force_retrieval_logging=True` in `orchestrator/eval/longmemeval.py:122-129` and `orchestrator/eval/longmemeval_fast.py:590-598`; retrieval gate is `_is_retrieval_logging_enabled(...)` in `orchestrator/memory/retrieval.py:26-33` | Benchmark runner forces logging on | Fast runner forces logging on | Hardcoded constant | `retrieval_logging_enabled` exists, but benchmark runs do not currently rely on it; the explicit benchmark flag is the effective authority. |
| Retrieval ranking weights and filters (`INITIAL_VECTOR_CANDIDATES`, `MIN_FINAL_SCORE`, `HYBRID_VECTOR_WEIGHT`, `HYBRID_BM25_WEIGHT`, `HYBRID_RECENCY_CONFIDENCE_WEIGHT`, recency/source/access buckets) | Hardcoded constants and helper logic in `orchestrator/memory/retrieval.py:21-23,47-49,97-127,144-156,492-626` | Active | Active | Hardcoded constant | These are the live ranking weights for benchmark retrieval; no env/config override exists today. |
| Retrieval scope defaults (`include_local=False`, `include_historical=False`, `memory_slot=None`) | Hardcoded retrieval defaults in `orchestrator/memory/retrieval.py:236-250,440-455` plus benchmark call sites in `tests/longmemeval/evaluate.py:349-359` | Active | Active | Hardcoded constant | The benchmark path does not retrieve local-only memories, historical memories, or slot-filtered subsets unless code changes. |
| `MAX_RETURNED_MEMORIES` | Hardcoded constant in `orchestrator/memory/retrieval.py:20` | Not used by the benchmark call path; `retrieve_memories(...)` uses caller `limit` / `TOP_K_MEMORIES` instead | Same | No active benchmark authority; if ever wired, pin as a hardcoded constant | Keep this listed because it looks authoritative, but `tests/test_retrieval.py:137-168` explicitly guards against clamping results to 5. |

### 4. Fast-lane-only knobs

| Variable / surface | Current source | Canonical lane | Fast lane | Recommended pinning authority | Notes |
| --- | --- | --- | --- | --- | --- |
| Chunking knobs (`DEFAULT_CHUNK_MAX_CHARS`, `DEFAULT_OVERLAP_TURNS`, `--chunk-max-chars`, `--overlap-turns`) | Hardcoded constants and CLI args in `orchestrator/eval/longmemeval_fast.py:38-39,132-239,563-574` | N/A | Active | Hardcoded constant | These are the main fast-lane ablation knobs and must stay separate from canonical claims. |
| Chunk formatting (`[User]: ...`, `[Assistant]: ...`) | Hardcoded formatter in `orchestrator/eval/longmemeval_fast.py:117-129` | N/A | Active | Hardcoded constant | Fast-lane retrieval quality depends on this serialization of session text into chunks. |
| Direct-insert row shape (`BENCHMARK_SOURCE_TYPE`, `BENCHMARK_CATEGORY`, `confidence=1.0`, `trust_score=0.5`, `tier="l1"`, `local_only=FALSE`, `memory_slot=NULL`) | Hardcoded constants/literals in `orchestrator/eval/longmemeval_fast.py:35-39,289-345` | N/A | Active | Hardcoded constant | These values materially change fast-lane retrieval scoring and should be pinned with any fast-lane artifact. |
| Fast benchmark metadata tags (`BENCHMARK_NAME`, `benchmark_source_tag`, `question_id`, `session_id`, `chunk_index`) | Hardcoded metadata shape in `orchestrator/eval/longmemeval_fast.py:35,311-339` | N/A | Active | Hardcoded constant | Needed for forensic traceability of fast-lane artifacts. |

## Bottom line

- **Canonical lane** benchmark claims are controlled by a mixed authority set: explicit dataset/checkpoint fixtures, hardcoded answer/judge/prompt constants, and live config for embeddings and dedup thresholds.
- **Fast lane** reuses the same answer/judge/retrieval path but adds its own chunking and direct-insert hardcoded constants while bypassing extraction and dedup entirely.
- `dedup_merge_threshold` and its sister thresholds are live-config authorities today; `orchestrator/memory/dedup.py` must stay a reader of that config, not a second source of truth.
- `ANSWER_MODEL` and `JUDGE_MODEL` should be treated as pinned benchmark references, not as swappable environment knobs.
- `retrieval_logging_enabled` is part of the live config surface, but the actual benchmark lanes currently override it by forcing retrieval logging on.
- `MAX_RETURNED_MEMORIES` is currently a misleading inert constant, not an active benchmark limiter.
