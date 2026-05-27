# Abstention Guardrail Wire-It Plan Stub

**Artifact path**: `tests/benchmark_results/abstention_guardrail_wire_it_plan_stub.md`
**Generated**: 2026-05-27T12:02:00Z
**Task**: 7. Execute Chosen Non-Production Disposition Path
**Selected disposition**: `wire-it`
**Plan**: `.sisyphus/plans/abstention-guardrail-wiring-audit.md`

> **Purpose**: This is a documentation-only plan stub. No production code, no patches to `orchestrator/memory/**`, `orchestrator/eval/runner.py`, `orchestrator/prompts.py`, or the W1 plan file. This stub describes the production-wiring plan that must be commissioned separately before W1 TODO 9 can be completed.

---

## Citation

This stub exists because Oracle selected the `wire-it` disposition. The selection was based on audit findings:

- **Audit**: `tests/benchmark_results/abstention_guardrail_wiring_audit.md`
- **Disposition**: `tests/benchmark_results/abstention_guardrail_oracle_disposition.md`
- **Evidence**: `.sisyphus/evidence/task-6-oracle-disposition.md`, `.sisyphus/evidence/task-6-oracle-scope-filter.md`

---

## What This Stub Documents

The Oracle confirmed that `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is genuinely unwired from production prompt assembly:

| Finding | Evidence |
|---------|----------|
| Constant absent from committed Python | `orchestrator/prompts.py` only has `DAEMON_PROMPT_VERSION` and `DAEMON_SYSTEM_PROMPT` |
| Assembly unwired | `assemble_system_prompt()` imports only `DAEMON_SYSTEM_PROMPT`; no guardrail reference |
| Archived text recoverable | `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713` |
| Generic guidance distinct | Generic "do not speculate" ≠ abstention guardrail (semantically different) |

---

## Future Plan Scope (When Commissioned)

A future wire-it plan would implement the following production changes:

### 1. Add Constant to `orchestrator/prompts.py`

Add the `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` constant using the archived text from `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713`:

```python
MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL = """When a question depends on retrieved memory or recent context, treat that memory as evidence rather than permission to guess.
If the available memory does not directly answer the question, say that you do not know or that the available memory is insufficient.
Do not fill gaps with nearby but non-answering details, inferred timelines, or best guesses.
Only answer confidently when the memory evidence directly supports the answer."""
```

### 2. Import and Append in `orchestrator/memory/injection.py`

In `assemble_system_prompt()`, import the constant and append it when memory context is present:

```python
from orchestrator.prompts import DAEMON_SYSTEM_PROMPT, MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL

def assemble_system_prompt(memory_context: str) -> str:
    # ... existing assembly logic ...
    # Append abstention guardrail when memory is present
    if memory_context:
        parts.append(MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL)
```

### 3. Verify Production Assembly

Verify that `assemble_system_prompt(memory_context='<test>')` includes the guardrail text when memory context is non-empty and excludes it when memory context is empty.

### 4. Update Production-Facing Documentation

Update any production-facing docs that incorrectly claim the guardrail is already wired. This is distinct from benchmark result docs (handled separately).

---

## What Is NOT In Scope Here

The following are explicitly out of scope for this stub and the future wire-it plan:

- **Memory/eval/prompts code edits** in this audit plan — future wire-it plan will handle
- **W1 TODO 4 patch** — not needed before W1 commissioning (per Oracle disposition)
- **W1 TODO 9 implementation** — must wait for wire-it plan to land first
- **Full LongMemEval or abstention sweeps** — out of scope
- **Stale benchmark result doc bulk cleanup** — deferred to separate follow-up
- **Broad roadmap/prompt rewrites/harness rewrites** — out of scope

---

## W1 Relationship

### W1 TODO 4 — Harness Parity Diagnostic

**No patch required before W1 commissioning.**

W1 TODO 4 (`.sisyphus/plans/wave1-prompt-surface-changes.md:242-280`) measures harness-side artifact state, not production wiring. The current pytest gate passing does not prove production guardrail presence, but this is by design — W1 TODO 4 is a harness parity diagnostic, not a production restoration task.

### W1 TODO 9 — Production Restoration

**Deferred. Cannot proceed until wire-it plan is separately authorized.**

W1 TODO 9 (`wave1-prompt-surface-changes.md:439-475`) requires the guardrail constant to exist in `orchestrator/prompts.py` and be wired into `assemble_system_prompt()`. This cannot happen until a wire-it plan is commissioned and executed.

---

## Disposition Notes

- **`defer-with-patch` was NOT selected** — no patch to W1 TODO 4 is applied because the harness diagnostic is correctly scoped and does not require the production guardrail to exist
- **`remove-dead-reference` was NOT selected** — no dead reference cleanup is applied; the broken `abstention_sweep.py` import is an artifact of the harness, not a production code issue
- **W1 TODO 9 cannot move forward** until wire-it plan lands

---

*Plan stub generated 2026-05-27T12:02:00Z. This is a documentation artifact only. No production code is modified.*
