# Harness Parity Oracle Review — T5 Equivalence Gate

**Task**: `- [ ] 5. Oracle ratifies equivalence definition and adapter strategy`  
**Date**: 2026-05-06  
**Status**: FINAL — ratified, not draft  
**Reviewed inputs**: `harness_parity_inventory.md`, `harness_parity_path_a_reconstruction.md`, `harness_parity_dependency_audit.md`, `harness_parity_category_paths.md`, and T1-T4 evidence files.

---

## Ratified equivalence definition

**Ratified equivalence definition**: each LongMemEval question maps to one deterministic UUID5 synthetic user; the harness ingests only that question's `haystack_sessions` under that synthetic user; the harness runs synchronous production extraction inline for those haystack conversations; L0 remains empty unless explicitly prepopulated through an existing production memory path; the answer-time path calls production `build_memory_context()` and production `assemble_system_prompt()` directly; the prompt returned by production assembly is passed to the answering model unchanged; and parity is accepted only when the harness-sent prompt is byte-identical to a direct production assembly call made against the same synthetic-user state.

The v1 aggregated unscoped retrieval strategy is explicitly retracted and rejected. Aggregating all questions under one shared user and attempting to recover scope with unfiltered retrieval, `allowed_source_conversation_ids`, or cross-question filtering is not production-faithful because production prompt scope is derived from the `conversations.user_id` reached through `conversation_id`, not from an allowlist parameter on `build_memory_context()`.

T5 therefore clears T6 to proceed with the scope-wall check. It does **not** authorize edits to `orchestrator/memory/**`, `orchestrator/eval/runner.py`, config-pin files, or tests; T6 must still halt if repository functionality requires out-of-scope edits.

---

## Normalization/byte-identity ruling

**Normalization/byte-identity ruling**: no verifier-side normalization is permitted. The comparison is exact UTF-8 byte identity of the two final Python `str` prompt values after each value is encoded with `prompt.encode("utf-8")`.

The two compared byte strings are:

1. **Harness call path bytes**: the exact system prompt string that the corrected LongMemEval harness passes as the system prompt to `answer_with_llm()` for a question, after the harness has called `build_memory_context(store, answer_conversation_id, max_tokens=same_value)` and `assemble_system_prompt(memory_context=that_context, preferences_block=same_preferences_block, conversation_id=answer_conversation_id)`, with no string operations between the returned prompt and model invocation except assignment, structured metadata capture, and function-argument passing.
2. **Direct production call path bytes**: the exact system prompt string returned by a direct test-side invocation of the same production calls against the same prepared synthetic-user state, same answer/evaluation `conversation_id`, same `max_tokens` value, and same `preferences_block` produced from that synthetic user's settings via production `format_preferences_block()`.

Allowed operations after production returns the prompt are only assignment, structured payload inclusion, metadata capture, and passing the string as a function argument. Disallowed operations are concatenation, substitution, slicing, formatting, encoding/decoding changes other than the final comparison encode, stripping, whitespace cleanup, regex rewrite, truncation, sorting, or any benchmark-local normalization.

Production's own internal normalization remains production behavior. For example, `_normalize_content()` and `_truncate_to_chars()` inside `orchestrator/memory/injection.py` are allowed because both call paths must invoke the same production code before the compared prompt is returned; they are not verifier-side normalization rules.

---

## Concern rulings

| Concern class | Ruling | Implementation consequence |
|---|---|---|
| synthetic-user isolation | **APPROVED / REQUIRED.** One LongMemEval question equals one deterministic UUID5 synthetic user, and all haystack sessions for that question are ingested under that user. | T7 must create or ensure a deterministic user row per `question_id`; no shared benchmark user for parity prompt assembly. |
| inline extraction contract | **APPROVED / REQUIRED.** The harness may bypass arq scheduling and the 30-second debounce, but it must still call production `process_extraction()` inline so extraction, dedup, embeddings, memory writes, extraction-log writes, and conversation-summary side effects remain production code paths. | No bulk-loaded pre-extracted/oracle memories; extraction runs once per relevant haystack session/conversation through the existing path shown by T3. |
| timestamp variance | **BYTE-IDENTITY STRICT.** Timestamp differences are not normalized away. If timestamps affect message ordering, recency scoring, access boosts, summary ordering, or final prompt ordering, both compared call paths must see the same prepared synthetic-user state. | T7/T10 should preserve deterministic haystack ordering and avoid wall-clock-dependent prompt-visible divergence where possible; any direct-vs-harness prompt mismatch caused by timestamp variance is a parity failure, not a permitted normalization. |
| equal-rank ordering | **BYTE-IDENTITY STRICT.** The harness must not impose a benchmark-local tie-breaker or reorder retrieved memories/summaries. Production ranking/order is the contract. | If equal `final_score`, equal SQL rank, or equal timestamps produce nondeterministic memory order across the same prepared state, T10 must fail/halt and record the divergent memory IDs; do not sort by id in the harness to make the bytes pass. |
| encryption decoding | **APPROVED APPLICATION-STRING COMPARISON.** Parity is judged after production store reads decrypt message and memory content into application strings. | T13 should smoke-test decryption separately; T5 byte identity compares assembled prompt bytes, not encrypted database values. |
| Fernet ciphertext nondeterminism | **EXPECTED / NOT A PARITY INPUT.** Fernet encryption is nondeterministic and ciphertext byte equality is neither required nor meaningful. | Do not compare `messages.content`, `memories.content`, or extraction-log ciphertext across runs; compare decrypted/application prompt strings only. |
| whitespace | **BYTE-IDENTITY STRICT.** No whitespace normalization is permitted after production assembly. | Exact newlines, blank lines, spaces, and trailing characters in the returned prompt bytes must match; harness must not call `.strip()` or equivalent on the returned prompt. |
| empty L0 | **APPROVED DEFAULT.** Empty L0 is production-valid and matches LongMemEval's no-prebaked-profile assumption. | Leave L0 empty unless a later explicit prepopulation case intentionally tests frozen memories through existing memory paths. Non-empty L0 is not required for default parity. |
| retrieval scoping | **APPROVED VIA USER SCOPE ONLY.** Production-faithful retrieval scoping is `conversation_id -> conversations.user_id -> user-scoped memory reads`. | Do not resurrect aggregated shared-user retrieval, unscoped retrieval, or `allowed_source_conversation_ids` as the production prompt-scope mechanism. `allowed_source_conversation_ids` may remain legacy/diagnostic context outside the ratified prompt path, but not as the parity mechanism. |
| preferences/settings | **APPROVED EMPTY DEFAULT WITH PRODUCTION BLOCK WHEN PRESENT.** Empty synthetic-user settings are production-valid and produce an empty preferences block. If settings are non-empty, both call paths must compute the block through production `format_preferences_block(get_user_settings(user_id))` and pass exactly that block to `assemble_system_prompt()`. | Do not ignore non-empty settings if they are prepopulated; do not invent benchmark-local preference formatting. For the default LongMemEval parity path, keep settings empty. |
| summaries | **APPROVED EMPTY DEFAULT.** `process_extraction()` updates `conversations.summary`, but production `build_memory_context()` reads summary memories via `get_recent_summaries()` / `memories.category='summary'`; empty summary-memory state is production-valid. | Do not format summaries locally. If a later run requires non-empty summaries, prepopulate summary memories only through an existing production consolidation/summary-memory path and compare exact bytes. |
| entity expansion | **APPROVED EMPTY DEFAULT.** Empty entity state is production-valid; entity-linked retrieval is optional and separately populated. | Do not require entity rows for default parity. If alias/entity behavior is intentionally tested, populate via existing production entity-resolution paths before both compared calls. |
| token budget trimming | **PRODUCTION-ONLY.** Token trimming must be whatever `build_memory_context()` does with the same `max_tokens`; no harness-side trimming or post-trimming is allowed. | T7/T10 must use the same `max_tokens` value on both paths, defaulting to production default unless explicitly recorded. Any byte mismatch from different token budgets is a harness bug. |
| pre-population paths | **APPROVED ONLY THROUGH EXISTING PATHS.** User rows, conversations, messages, extracted memories, optional settings, optional L0, optional summary memories, and optional entities must be created through existing project/store/production paths. | No production memory-code edits; no direct oracle memory import for default parity; no per-question teardown inside the loop. If a needed state surface requires production changes, halt rather than patching production. |

---

## Adapter strategy ruling

The corrected adapter strategy is approved because T1 proves the current benchmark-local formatter is the measurable parity gap, T2 classifies the gap as a Wave 0 Path A miss rather than later drift, T3 finds zero production-change dependencies, and T4 proves every LongMemEval_S category converges through the same assembly path.

The adapter must be thin: prepare synthetic-user state, call production entry points, pass the returned prompt unchanged, and record enough metadata to prove what happened. It must not become a second prompt-rendering implementation.

The adapter's answer/evaluation conversation must belong to the synthetic user whose haystack was ingested. Because `build_memory_context()` derives `user_id` from `store.get_conversation(conversation_id)`, a wrong conversation owner is a hard parity failure even if retrieved memories appear plausible.

---

## Downstream instructions for T6/T9/T10

1. **T6 scope gate**: before deleting `_format_eval_memory_block()` or changing call sites, confirm whether out-of-scope imports/hashes/tests require edits. If they do, emit the plan's `[DECISION NEEDED: authorize runner.py/test/config-pin scope expansion, or commission a separate plan / alternate harness-native entry point]` halt instead of editing those files.
2. **T6 allowed path**: if a harness-native parity entry point can be implemented entirely under `tests/longmemeval/**`, T6/T7 may preserve out-of-scope legacy consumers while routing the parity run through the new in-scope entry point; document legacy status explicitly.
3. **T9 static check**: inspect the call chain from production prompt return to model invocation. Only assignment, metadata capture, structured payload inclusion, and function-argument passing are allowed; any prompt string operation is a halt.
4. **T10 runtime spot-check**: compare the exact UTF-8 bytes described in the normalization/byte-identity ruling for 20 stratified questions. Each record must include `question_id`, `synthetic_user_id`, answer/evaluation `conversation_id`, retrieved memory IDs, whether settings/L0/summaries/entities were empty or prepopulated, and pass/fail.
5. **T10 mismatch handling**: on any byte mismatch, record both prompt digests, a short redacted divergent excerpt, relevant retrieved memory ID order, and the suspected concern class; then halt before T11.

---

## Final disposition

**Ratified equivalence definition** — APPROVED. The required equivalence is strict byte identity of the harness-sent production-assembled prompt and a direct production assembly result against the same synthetic-user state, with no verifier-side normalization and no resurrection of aggregated unscoped retrieval.
