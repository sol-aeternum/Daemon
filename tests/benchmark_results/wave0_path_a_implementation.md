# Wave 0 Path A Implementation Memo

## Production Code Status

**`orchestrator/memory/` was NOT modified for Path A / PA1.**

The Path A harness adapter is implemented entirely in `tests/longmemeval/evaluate.py`. The production memory pipeline (`orchestrator/memory/injection.py`, `orchestrator/memory/retrieval.py`, `orchestrator/memory/store.py`, etc.) remains unchanged. The harness calls production functions `assemble_system_prompt()` and `retrieve_memories_for_text()` but does not modify them.

> **Note on pre-existing working-tree diffs**: `git diff -- orchestrator/memory/` shows modifications to `dedup.py`, `extraction.py`, `injection.py`, `retrieval.py`, and `store.py`. These are **pre-existing** changes from a prior session and are **not** attributable to Path A work. Path A edits are confined to `tests/longmemeval/evaluate.py` and `tests/test_longmemeval_evaluate.py`.

---

## Production-Input Gap Defaults

The following documents every production-input gap/default used by the Path A harness. For each gap, the memo states whether it uses production behavior or a benchmark default.

### Conversation History

- **Gap**: LongMemEval has no production conversation/message state — there are no `messages` rows, no `conversation` rows, and no live query context.
- **Resolution**: Harness bypasses `build_memory_context()` entirely. Instead, `_format_eval_memory_block()` (evaluate.py:416–456) formats pre-retrieved memories directly into the production-style `About this user:` block format.
- **Default**: `memories` and `summaries` are passed by the caller (`evaluate_single`); summaries default to `[]`.
- **Classification**: **Benchmark default** — production would derive these from a live conversation context. LongMemEval has no such context.

### User Preferences

- **Gap**: LongMemEval does not have a user preferences context separate from memory.
- **Resolution**: User preferences are not explicitly injected. Preferences stored as memories are retrieved as part of the normal memory retrieval step.
- **Classification**: **Production behavior** (no special handling — preferences flow through normal memory retrieval).

### Recent Session Summaries

- **Gap**: LongMemEval has no session summaries in the production sense (no `memory_extraction_log` summary rows for the test user).
- **Resolution**: `build_assembled_system_prompt(memories, summaries=None)` is called with `summaries=[]` in `evaluate_single` (evaluate.py:633).
- **Classification**: **Benchmark default** — empty summaries list.

### Current Timestamp

- **Gap**: No live conversation context means no `get_time` tool call is made during answer generation.
- **Resolution**: `assemble_system_prompt()` in `injection.py` calls `datetime.now().isoformat()` when building the system prompt. This is production behavior.
- **Classification**: **Production behavior** — the timestamp comes from the production `assemble_system_prompt()` call, not a synthetic override.

### Trust-Signal Recording

- **Gap**: No trust-signal recording mechanism in the harness.
- **Resolution**: Not applicable to the retrieval-only LongMemEval benchmark.
- **Classification**: **Not applicable** — trust-signal recording is a production chat concern, not a benchmark retrieval concern.

### `retrieval_triggered_by`

- **Value**: `"longmemeval"` (evaluate.py:604)
- **Classification**: **Benchmark default** — explicitly set to tag retrieval log entries for the LongMemEval benchmark run. Production uses values like `"memory_read"`, `"memory_reflect"`, etc.

### `include_dream_observations`

- **Value**: `True` (evaluate.py:605)
- **Classification**: **Benchmark default** — `include_dream_observations=True` is set in `retrieve_user_memories()`. Production retrieval defaults vary by call site; memory_reflect uses `True`, memory_read uses `False`.

### `include_local`

- **Value**: `False` (runner.py:475, scope_defaults)
- **Classification**: **Benchmark default** — explicitly set to `False` in the retrieval scope defaults. Production `memory_read` defaults to `True` (memory/tools.py:84, 100).

### L0 / Frozen Memory Handling

- **Value**: `include_l0=True` (evaluate.py:601)
- **Classification**: **Benchmark default** — L0 (frozen/immutable) memories are included in retrieval. Production `memory_reflect` uses `include_l0=True`; `memory_read` defaults to `False`.

### Abstention Guardrail Handling

- **Implementation**: `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` from `orchestrator/prompts.py` is appended to the assembled system prompt when `memory_context` is non-empty (injection.py:330). This is the **production behavior**.
- **Benchmark behavior**: The harness calls `assemble_system_prompt(memory_context=...)` which internally applies the guardrail. `_format_eval_memory_block()` returns a non-empty string when any memories are present, so the guardrail is included.
- **Classification**: **Production behavior** — the abstention guardrail is part of `assemble_system_prompt()` and is not modified or disabled in the benchmark harness.

---

## Answer Prompt Metadata (Benchmark Mode)

When `benchmark_mode=True`, each result row in `longmemeval_results.jsonl` includes an `answer_prompt_metadata` object (evaluate.py:668–678) capturing the full audit trail:

| Field | Source | Description |
|-------|--------|-------------|
| `system_message` | `build_assembled_system_prompt()` | Full assembled system prompt string (DAEMON_SYSTEM_PROMPT + memory block + guardrail) |
| `user_message` | `question_text` | Raw question text from dataset |
| `memory_content` | `_format_eval_memory_block()` | Formatted `About this user:` block string |
| `memories_raw` | `retrieve_user_memories()` | Raw memory dicts list from retrieval |
| `model` | `answer_tracking.get("model")` | Provider-returned model string |
| `answer_fingerprint` | `answer_tracking.get("fingerprint")` | Provider-returned system fingerprint |
| `provider_endpoint_slug` | `BENCHMARK_ANSWER_ENDPOINT_SLUG` | `"openrouter/openai/gpt-4o-2024-08-06"` |
| `seed` | `BENCHMARK_SEED` | `42` |
| `temperature` | `0.0` | Always `0.0` in benchmark mode |

---

## Deterministic Provider Controls

### Model

| Call | Production Model | Benchmark Model |
|------|----------------|-----------------|
| Answer | `openrouter/openai/gpt-4o` | `openrouter/openai/gpt-4o-2024-08-06` |
| Judge | `openrouter/openai/gpt-4o` | `openrouter/openai/gpt-4o-2024-08-06` |

- **Benchmark model ID** (evaluate.py:106–107): `openrouter/openai/gpt-4o-2024-08-06`
- **Classification**: **Benchmark default** — dated snapshot model ID for reproducibility. Production uses the alias `gpt-4o`.

### Endpoint/Provider Order

- **Value** (evaluate.py:111–112, 310–316): `["openrouter/openai/gpt-4o-2024-08-06"]`
- **Implementation**: Passed via `extra_body={"provider": {"order": [endpoint_slug], "allow_fallbacks": false}}` in `_call_llm_with_provider_config()`.
- **Classification**: **Benchmark default** — forces routing to the specific endpoint.
- **Known Issue**: `BENCHMARK_ANSWER_ENDPOINT_SLUG` is a full model slug (`openrouter/openai/gpt-4o-2024-08-06`) where OpenRouter provider routing expects a provider slug such as `openai`. The PA3 full-corpus aligned run produced 0/500 successful answer calls with `No endpoints found for openai/gpt-4o-2024-08-06`. This is a known/reopened blocker (R1 diagnosis in progress). The provider pinning tests pass because they mock the LLM call — the live routing failure only manifests in actual benchmark runs.

### Seed

- **Value**: `42` (evaluate.py:115, 308)
- **Classification**: **Benchmark default** — `seed=BENCHMARK_SEED` is added to every benchmark-mode LLM call.

### Temperature

- **Production**: `ANSWER_TEMPERATURE=0.7`, `JUDGE_TEMPERATURE=0.0`
- **Benchmark**: `0.0` (evaluate.py:286–290, 569)
- **Classification**: **Benchmark default** — temperature is pinned to `0.0` for all benchmark-mode calls. Raises `BenchmarkSamplingError` if non-zero temperature is passed.

### Fallback Policy

- **Value**: `allow_fallbacks=False` (evaluate.py:314)
- **Classification**: **Benchmark default** — prevents silent provider fallback. Provider/transport errors raise `BenchmarkProviderError` in benchmark mode (evaluate.py:328–331).

### Fingerprint Capture / Drift Checks

- **Implementation** (evaluate.py:335–368): After each benchmark-mode LLM call, `system_fingerprint` is extracted from the response and stored in `_BM_METADATA`. On subsequent calls, the fingerprint is compared — drift raises `BenchmarkSamplingError`.
- **Classification**: **Benchmark default** — production has no fingerprint tracking.

---

## Verification

### Provider Pinning Test Results

```
$ BENCHMARK_MODE=1 PYTHONPATH=. pytest tests/benchmark/test_provider_pinning.py -q
....................                                                     [100%]
20 passed, 15 warnings in 2.47s
```

**Result**: PASS — all 20 provider pinning/fail-fast tests pass.

### Acceptance Script

```bash
python - <<'PY'
from pathlib import Path
p=Path('tests/benchmark_results/wave0_path_a_implementation.md')
s=p.read_text()
required_terms = [
    'conversation history','user preferences','recent session',
    'trust-signal','retrieval_triggered_by','include_dream_observations',
    'include_local','L0','abstention','provider',
    # answer_prompt_metadata fields
    'system_message','user_message','memory_content','memories_raw',
    'provider_endpoint_slug','seed','temperature'
]
for term in required_terms:
    assert term.lower() in s.lower(), f"MISSING: {term}"
PY
```

**Result**: All terms present. Acceptance script passes.

---

## Key Files

| File | Role |
|------|------|
| `tests/longmemeval/evaluate.py` | Path A harness adapter — `_format_eval_memory_block`, `build_assembled_system_prompt`, `_call_llm_with_provider_config`, answer/judge prompts |
| `orchestrator/eval/runner.py` | Canonical runner — `build_longmemeval_pinned_config`, phase ordering, retrieval defaults |
| `tests/benchmark_longmemeval/longmemeval_config_pin.json` | Frozen snapshot of all pinned deterministic settings |
| `tests/benchmark/test_provider_pinning.py` | Provider pinning / fail-fast test suite (20 tests) |
| `orchestrator/prompts.py` | `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`, `DAEMON_SYSTEM_PROMPT` |
| `orchestrator/memory/injection.py` | `assemble_system_prompt()` — called by harness, not modified |
| `orchestrator/memory/retrieval.py` | `retrieve_memories_for_text()` — called by harness, not modified |

---

## Summary

Path A does not modify `orchestrator/memory/`. All changes are confined to the test harness in `tests/longmemeval/evaluate.py`. The harness preserves production memory formatting semantics (`About this user:`, `Recent context:`) and calls production `assemble_system_prompt()`. The abstention guardrail is production behavior. All other gaps (conversation history, user preferences, session summaries, timestamp context) are handled as benchmark defaults appropriate to LongMemEval's retrieval-only benchmark scope.