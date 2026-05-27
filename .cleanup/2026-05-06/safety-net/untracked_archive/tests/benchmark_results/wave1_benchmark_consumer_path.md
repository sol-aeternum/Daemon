# Wave 1 Benchmark Consumer-Path Viability Gate

decision: halt-harness-parity-required

## Bottom line
LongMemEval_S does not consume the same memory prompt surface that production builds in `orchestrator/memory/injection.py`. The benchmark answer path reuses production `assemble_system_prompt()`, but it constructs `memory_context` with a benchmark-local `_format_eval_memory_block()` first. That means any Wave 1 change confined to `build_memory_context()` — including structured JSON, confidence/provenance/timestamp/source rendering, L0 head rendering, summary inclusion, or token-budget shaping — cannot be measured by the current benchmark consumer path.

## Call chain from benchmark entrypoint to final answer prompt metadata
1. `python -m orchestrator.eval.longmemeval evaluate` enters `main()` in `orchestrator/eval/longmemeval.py:141-173`, which constructs `LongMemEvalRunner(...)` and calls `await runner.evaluate()`.
2. `LongMemEvalRunner.evaluate()` in `orchestrator/eval/runner.py:1593-1789` imports `evaluate_single`, `_format_eval_memory_block`, and `build_assembled_system_prompt` from `tests.longmemeval.evaluate` at `orchestrator/eval/runner.py:196-216`.
3. The runner calls `evaluate_single(...)` at `orchestrator/eval/runner.py:1727-1738`.
4. `evaluate_single()` in `tests/longmemeval/evaluate.py:627-714`:
   - retrieves memories with `retrieve_user_memories(...)` at `:641-649`
   - builds `system_prompt = await build_assembled_system_prompt(memories)` at `:651`
   - separately captures `memory_context = _format_eval_memory_block(memories, [])` at `:652`
   - passes `system_prompt` into `answer_with_llm(...)` at `:653-655`
5. `build_assembled_system_prompt()` in `tests/longmemeval/evaluate.py:477-490` first calls benchmark-local `_format_eval_memory_block(...)` at `:487-488`, then calls production `assemble_system_prompt(memory_context=memory_context)` at `:490`.
6. `answer_with_llm()` in `tests/longmemeval/evaluate.py:579-595` sends a two-message prompt when `system_prompt` is provided: `[{"role": "system", "content": system_prompt}, {"role": "user", "content": question}]`.
7. `answer_prompt_metadata.system_message` is created from that exact `system_prompt` variable at `tests/longmemeval/evaluate.py:703-705` and written out by `write_results_jsonl()` at `tests/longmemeval/evaluate.py:238-242`.
8. The harness contract in `orchestrator/eval/runner.py:621-646` hashes `_format_eval_memory_block` into `active_memory_formatter_sha256`, which is direct evidence that the benchmark-local formatter — not production `build_memory_context()` — is the active consumer-path memory formatter.

## Production-path comparison
### Production chat path
`orchestrator/main.py:1757-1769` imports `build_memory_context`, `assemble_system_prompt`, and `format_preferences_block`, then executes:
1. `preferences_block = format_preferences_block(user_settings)`
2. `memory_context = await build_memory_context(store, conversation_uuid)`
3. `assembled_system_prompt = await assemble_system_prompt(memory_context=memory_context, preferences_block=preferences_block, conversation_id=conversation_uuid)`

### What `build_memory_context()` does in production
`orchestrator/memory/injection.py:168-308` performs several production-only steps before prompt assembly:
- fetches and formats L0 memories through `_format_l0_block()` at `:104-114` and `:183-193`
- derives `query_text` from recent conversation messages at `:194-206`
- retrieves memories from the store at `:213-243`
- fetches recent summaries at `:209-210` and `:244`
- renders memory and summary sections at `:246-276`
- enforces token-budget trimming at `:266-298`
- returns the final production memory-context string at `:300-308`

### What the benchmark path does instead
- `tests/longmemeval/evaluate.py` never calls `build_memory_context()`; its only `build_memory_context()` mention is the docstring comment at `:440`.
- `build_assembled_system_prompt()` accepts pre-retrieved memories and optional summaries, then immediately formats them with `_format_eval_memory_block()` at `:487-488`.
- `evaluate_single()` passes `[]` for summaries at `:652`, so the benchmark prompt surface does not exercise production summary selection.
- `retrieve_user_memories()` sets `include_l0=True` at `tests/longmemeval/evaluate.py:613-624`, but `_format_eval_memory_block()` has no L0-specialized branch. It ignores the production `_format_l0_block()` semantics and renders everything as the same flat `- Label: content` lines.
- The benchmark path does not pass a `preferences_block` into `assemble_system_prompt()`.

## Prompt metadata evidence
- `answer_prompt_metadata.system_message` is captured exactly where the prompt is built, not reconstructed later: `tests/longmemeval/evaluate.py:651` and `:703-705`.
- Representative successful stored row: `tests/benchmark_results/wave0_closure_option_a_rerun/longmemeval_results.jsonl`, row 4 (`question_id=58bf7951`). A read-only inspection script found:
  - `answer_prompt_metadata` keys: `system_message`, `user_message`, `memory_content`, `memories_raw`, `model`, `answer_fingerprint`, `provider_endpoint_slug`, `seed`, `temperature`
  - `memory_content` preview: `About this user:
- Fact: User bought Sushi Go!
- Fact: User has a ceramic vase from the street fair ...`
  - `provenance`, `timestamp`, `confidence`, and `source_type` were all absent from both `memory_content` and `system_message`
- The same sample row's `memories_raw[0]` still carried side-channel fields including `content`, `confidence`, `source_type`, and `created_at`, which proves the flattening happens before the final prompt is assembled.
- Related anomaly logged to `TRIAGE.md`: `orchestrator/eval/runner.py:640-641` hashes `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`, but sampled benchmark `system_message` did not contain that guardrail and production `assemble_system_prompt()` does not append it (`orchestrator/memory/injection.py:318-336`).

## Field-preservation check
| Field | Present in raw benchmark memory sample? | Used by `build_memory_context()` text path today? | Used by benchmark `_format_eval_memory_block()`? | Reaches benchmark `system_message`? | Evidence |
|---|---|---|---|---|---|
| `content` | Yes | Yes (`orchestrator/memory/injection.py:254-258`) | Yes (`tests/longmemeval/evaluate.py:452-456`) | Yes, but only as flattened text | Sample `memory_content` preview shows only label + text lines |
| `provenance` | No exact `provenance` key in sampled row; closest raw provenance-like fields are `source` / `source_conversation_id` | No | No | No | Sample `memories_raw[0]` keys had no `provenance`; formatter ignores `source*` |
| `timestamp` | Yes, but only as raw side-channel fields such as `created_at`, `updated_at`, `last_retrieved_at`, `valid_from`, `valid_to` | No | No | No | Inspection script showed no `timestamp` markers in `memory_content` or `system_message` |
| `confidence` | Yes (`confidence`) | No matches in `injection.py` | No matches in benchmark formatter | No | Present in `memories_raw`, absent from final prompt text |
| `source_type` | Yes (`source_type`) | No matches in `injection.py` | No matches in benchmark formatter | No | Present in `memories_raw`, absent from final prompt text |

## Gate rationale
The hard gate is triggered. The benchmark-local formatter collapses retrieved memory dicts before `assemble_system_prompt()` runs, and `answer_prompt_metadata.system_message` is captured from that already-collapsed string. Because the benchmark never calls production `build_memory_context()`, it cannot measure Wave 1 prompt-surface changes made there.

## Required consequence
- Stop here for Wave 1 implementation.
- Do not start TODOs 1-20 on the assumption that LongMemEval_S measures `orchestrator/memory/injection.py` prompt-surface changes.
- A separate harness-parity task is required before implementation can be benchmarked meaningfully.

## Next recommended plan action
Halt after TODO 0 and commission a separate harness-parity task (or explicitly change scope) before any Wave 1 production-surface implementation begins.
