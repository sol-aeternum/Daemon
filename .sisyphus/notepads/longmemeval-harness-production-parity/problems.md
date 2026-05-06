# LongMemEval Harness-Production Parity — Problems Log

## T6 Issues — 2026-05-06

### Issue 1: T6-GATE Halt — Formatter Deletion Blocked
- **Severity**: critical
- **Scope**: project
- **Encountered during**: T6 scope-wall check
- **Category**: scope-blocker
- **Blocked current task**: T7 (implementation)
- **What happened**: The scope-wall check found that `orchestrator/eval/runner.py` and multiple test files import and depend on `_format_eval_memory_block` and `build_assembled_system_prompt`. Deleting these symbols would break `ImportError`, `NameError`, and config-pin hash contracts in out-of-scope files.
- **Evidence**:
  - `runner.py:212` — imports `_format_eval_memory_block`
  - `runner.py:636` — `active_memory_formatter_sha256 = _sha256_source(_format_eval_memory_block)`
  - `runner.py:633` — `active_system_prompt_builder_sha256 = _sha256_source(build_assembled_system_prompt)`
  - `tests/test_longmemeval_evaluate.py:15-16` — imports both symbols
  - `tests/benchmark_longmemeval/test_teardown_audit.py:19` — imports `evaluate_single`
- **Likely cause**: The benchmark contract was built on the assumption that `_format_eval_memory_block` is the active memory formatter. The runner hashes its source as part of the config-pin mechanism, creating a hard contractual dependency that cannot be broken without explicit scope expansion.
- **Suggested action**: Two paths forward:
  1. **Harness-native entry point** (allowed by T6-GATE): Implement a new parity-specific entry point under `tests/longmemeval/**` that calls production `build_memory_context()` and `assemble_system_prompt()` directly, leaving `_format_eval_memory_block`/`build_assembled_system_prompt`/`evaluate_single` intact for the legacy runner. T14 uses this new entry point.
  2. **Scope expansion**: User explicitly authorizes modifying `runner.py`, `tests/test_longmemeval_evaluate.py`, `tests/benchmark_longmemeval/test_config_pinning.py`, and `tests/benchmark_longmemeval/test_teardown_audit.py` to remove the legacy symbol references and re-pin the config contract against production entry points.

### Issue 2: Dual-Call Anomaly Cannot Be Fixed Without Deletion
- **Severity**: warning
- **Scope**: project
- **Encountered during**: T6 scope-wall check
- **Category**: benchmark-gap
- **Blocked current task**: no (cosmetic issue)
- **What happened**: `_format_eval_memory_block()` is called twice per question — once inside `build_assembled_system_prompt()` at `evaluate.py:487`, and again standalone at `evaluate.py:652` for checkpoint metadata. This is wasteful but cannot be cleaned up without deleting the Path B standalone call site, which would require deleting the symbol that `runner.py` depends on.
- **Evidence**: `evaluate.py:651-652`
- **Likely cause**: The standalone call was added to capture `memory_context` in `answer_prompt_metadata.memory_content` without routing through `build_assembled_system_prompt`. Now that `runner.py` hashes `_format_eval_memory_block`, any change to the standalone call pattern is a scope-expansion decision.
- **Suggested action**: Track as known benchmark inefficiency; resolve when formatter deletion scope is authorized.