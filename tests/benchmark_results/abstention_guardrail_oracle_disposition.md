# Abstention Guardrail Oracle Disposition

**Artifact path**: `tests/benchmark_results/abstention_guardrail_oracle_disposition.md`
**Generated**: 2026-05-27T11:47:00Z
**Task**: 6. Obtain Oracle Disposition
**Plan**: `.sisyphus/plans/abstention-guardrail-wiring-audit.md`
**Audit artifact**: `tests/benchmark_results/abstention_guardrail_wiring_audit.md`

---

## Selected Disposition

**`wire-it`**

---

## Rationale

The Oracle selected `wire-it` based on the following verified facts from the audit:

1. **Production assembly genuinely unwired**: `assemble_system_prompt()` in `orchestrator/memory/injection.py` imports only `DAEMON_SYSTEM_PROMPT`. No reference to `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` by named import, verbatim text, or paraphrased equivalent.

2. **Archived guardrail text only in uncommitted archive**: The only authoritative source of the guardrail constant's text is `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713` — an uncommitted proposed diff, never applied to the working tree.

3. **`assemble_system_prompt()` lacks import/reference/append/paraphrase**: The function assembles the system prompt from `DAEMON_SYSTEM_PROMPT` plus optional memory context plus generic memory-tools disclosure. The archived abstention guardrail is not part of this assembly path.

4. **Generic memory guidance is distinct from abstention instruction**: The generic "do not speculate" guidance in `DAEMON_SYSTEM_PROMPT:63` addresses not guessing whether a memory exists. The archived guardrail addresses answering carefully when memory IS present but insufficient to answer the question. These are semantically distinct instructions serving different purposes.

---

## Operational Consequences

### W1 TODO 4 — Harness Parity Diagnostic

**W1 TODO 4 does NOT need patch before commissioning W1.**

The harness parity diagnostic (`.sisyphus/plans/wave1-prompt-surface-changes.md:242-280`) measures whether the benchmark harness correctly reads guardrail state from prompt artifacts. The current pytest gate (`test_abstention_regression_gate.py`) tests harness-side saved artifacts and does not claim to prove production wiring. No patch to W1 TODO 4 is required.

### Production Wiring/Removal

**Production wiring and removal are deferred to a separately authorized plan or W1 production-restoration task.**

This audit authorizes no production code changes in `orchestrator/memory/**`, `orchestrator/eval/runner.py`, or `orchestrator/prompts.py`. The `wire-it` disposition means production restoration (W1 TODO 9) cannot proceed without a separately commissioned wire-it plan that adds the guardrail constant to `orchestrator/prompts.py` and imports/appends it in `orchestrator/memory/injection.py`'s `assemble_system_prompt()`.

### W1 TODO 9 — Production Restoration

**W1 TODO 9 must wait for a separately authorized wire-it plan.**

W1 TODO 9 (`wave1-prompt-surface-changes.md:439-475`) titled "Restore Abstention Guardrail and Add Confidence Guidance" cannot be completed within this audit plan's scope. A future wire-it plan is required to:
- Add `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` constant to `orchestrator/prompts.py`
- Import and append the guardrail in `orchestrator/memory/injection.py`'s `assemble_system_prompt()`
- Verify production assembly includes the guardrail when memory context is present

---

## Out of Scope

The following are explicitly out of scope for this disposition and this audit plan:

- **No current-plan code edits**: `orchestrator/memory/**`, `orchestrator/eval/runner.py`, `orchestrator/prompts.py` are not modified
- **No broad roadmap changes**: Memory dedup thresholds, retrieval scoring, or other memory pipeline changes are out of scope
- **No prompt rewrites**: `orchestrator/prompts.py` is not rewritten
- **No harness rewrites**: `orchestrator/eval/runner.py` is not modified
- **No full LongMemEval or abstention sweeps**: Benchmark execution is out of scope
- **No bulk stale-doc cleanup**: The 8 benchmark result docs flagged as "needs follow-up cleanup" are deferred to a separate follow-up task

---

## What a Wire-It Plan Should Implement

When a separate wire-it plan is commissioned, it should implement the archived guardrail semantics:

**Archived guardrail text** (from `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713`):
```
When a question depends on retrieved memory or recent context, treat that memory as evidence rather than permission to guess.
If the available memory does not directly answer the question, say that you do not know or that the available memory is insufficient.
Do not fill gaps with nearby but non-answering details, inferred timelines, or best guesses.
Only answer confidently when the memory evidence directly supports the answer.
```

**Implementation path** (deferred, not implemented here):
1. Add constant to `orchestrator/prompts.py`
2. Import/append in `orchestrator/memory/injection.py::assemble_system_prompt()`
3. Verify production `assemble_system_prompt()` includes guardrail with memory context present
4. Update production-facing documentation

---

## Stale Documentation Note

Eight benchmark result docs in `tests/benchmark_results/` contain explicit factual errors claiming the guardrail is defined or appended in production. These are out of scope for this audit plan and should be addressed in a separate stale-doc follow-up task.

---

*Oracle disposition completed 2026-05-27T11:47:00Z. Disposition artifact is `.sisyphus/evidence/task-6-oracle-disposition.md` and `.sisyphus/evidence/task-6-oracle-scope-filter.md`.*
