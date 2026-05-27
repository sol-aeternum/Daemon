# Abstention Guardrail Wiring Audit

**Artifact path**: `tests/benchmark_results/abstention_guardrail_wiring_audit.md`
**Generated**: 2026-05-27T11:20:00Z
**Audit scope**: `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` — production prompt assembly wiring
**Blocked by**: Tasks 1–4 (complete)
**Blocks**: Task 6 (Oracle disposition), Task 7 (disposition application)

---

## 1. Frame

### Observed

The constant `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is referenced across 13 benchmark result docs in `tests/benchmark_results/` as a production guardrail that is "appended" or "wired" into `assemble_system_prompt()`. The audit was commissioned to determine the actual wiring state.

### Expected

The audit should determine whether `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is:
1. **Referenced under a different name** — exists but under an alias
2. **Genuinely unwired / non-existent** — never wired into production assembly
3. **Content present via other mechanism** — functionally equivalent text appears through other paths
4. **Uncertain / missing evidence** — insufficient data to classify

### Surface Area

| File | Role |
|------|------|
| `orchestrator/prompts.py` | Only exports `DAEMON_PROMPT_VERSION` and `DAEMON_SYSTEM_PROMPT` |
| `orchestrator/memory/injection.py` | `build_memory_context()` + `assemble_system_prompt()` — only imports `DAEMON_SYSTEM_PROMPT` |
| `orchestrator/eval/runner.py` | No reference; 526 lines; no hash/config-pin (historical reference at :640-641 is stale) |
| `tests/benchmark_longmemeval/abstention_sweep.py` | Imports constant (will fail at load time) |
| `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713` | Uncommitted proposed addition to `prompts.py` — the only authoritative source of the constant's text |

### Constraints

- No modifications to `orchestrator/memory/**`, `orchestrator/eval/runner.py`, or `orchestrator/prompts.py`
- No benchmark sweeps or LongMemEval executions
- Oracle decides final disposition; this artifact only classifies

---

## 2. Reference Map

*(Evidence: `.sisyphus/evidence/task-1-reference-map.md`, `.sisyphus/evidence/task-1-import-probe.txt`, `.sisyphus/evidence/task-1-guardrail-text.md`)*

### Constant Definition Status

| File | Status |
|------|--------|
| `orchestrator/prompts.py` | **NOT DEFINED** — only `DAEMON_PROMPT_VERSION` and `DAEMON_SYSTEM_PROMPT` present |
| `.cleanup/.../tracked_modifications.diff:5710-5713` | **Proposed only** — uncommitted diff; would add constant if applied |

### Import Probe

```
$ PYTHONPATH=. python -c "from orchestrator.prompts import MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL"
ImportError: cannot import name 'MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL' from 'orchestrator.prompts'
EXIT_CODE: 1
```

### Live Python References

| File | Line | Import / Reference | Fails? |
|------|------|-------------------|--------|
| `tests/benchmark_longmemeval/abstention_sweep.py` | 15 | `from orchestrator.prompts import MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` | **YES** — constant absent |
| `orchestrator/memory/injection.py` | — | No reference | N/A |
| `orchestrator/eval/runner.py` | — | No reference | N/A |
| `orchestrator/guardrails.py` | — | No reference | N/A |

### Archive Source (Uncommitted)

The only source of the guardrail constant's text is `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713`:

```
+MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL = """When a question depends on retrieved memory or recent context, treat that memory as evidence rather than permission to guess.
+If the available memory does not directly answer the question, say that you do not know or that the available memory is insufficient.
+Do not fill gaps with nearby but non-answering details, inferred timelines, or best guesses.
+Only answer confidently when the memory evidence directly supports the answer."""
```

This is **uncommitted archive** — a proposed diff, never applied to the working tree. The diff's implied `prompts.py` would have 5710+ lines; current `prompts.py` is 138 lines.

**Classification**: The constant does not exist in any committed Python source. The only authoritative text is from uncommitted archive.

---

## 3. Production Assembly

*(Evidence: `.sisyphus/evidence/task-2-production-assembly.md`, `.sisyphus/evidence/task-2-nonempty-prompt.md`, `.sisyphus/evidence/task-2-empty-prompt.md`)*

### Mechanism Inventory

`assemble_system_prompt()` in `orchestrator/memory/injection.py:311-336`:

**Import surface** (`injection.py:30`):
```python
from orchestrator.prompts import DAEMON_SYSTEM_PROMPT
```
Only `DAEMON_SYSTEM_PROMPT` is imported. `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is not imported, not referenced, does not exist.

**Assembly logic** (`injection.py:318-336`):
```python
parts = [DAEMON_SYSTEM_PROMPT.strip()]
prefs = (preferences_block or "").strip()
if prefs:
    parts.append(prefs)
memory_block = memory_context.strip()
if memory_block:
    parts.append(memory_block)
assembled = "\n\n".join(part for part in parts if part)
if "memory tools" not in assembled.lower():
    assembled = (
        assembled
        + "\n\n"
        + "You have access to memory tools for reading and writing durable user and project context."
    )
return assembled
```

### Three-Way Distinction: What IS Present vs. What Is NOT

| Text | Source | Present in Assembly? |
|------|--------|---------------------|
| `"You have access to memory tools for reading and writing durable user and project context."` | `injection.py:333` (appended when `"memory tools"` not in prompt) | **YES** — generic memory-tools disclosure |
| `"Do not speculate about what you do or don't remember."` | `DAEMON_SYSTEM_PROMPT:63` | **YES** — generic guidance (distinct semantics) |
| `"When a question depends on retrieved memory or recent context, treat that memory as evidence rather than permission to guess. If the available memory does not directly answer the question, say that you do not know or that the available memory is insufficient. Do not fill gaps with nearby but non-answering details, inferred timelines, or best guesses. Only answer confidently when the memory evidence directly supports the answer."` | `.cleanup/.../tracked_modifications.diff:5710-5713` (archived, uncommitted) | **NO** — abstention guardrail |

### Semantic Distinction: Generic Guidance vs. Archived Guardrail

The generic `"Do not speculate about what you do or don't remember"` (DAEMON_SYSTEM_PROMPT:63) addresses **not guessing whether a memory exists**. The archived guardrail addresses **answering carefully when memory IS present but insufficient** — specifically instructing the model to say "I don't know" when retrieved memory does not directly answer the question, and not to fill gaps with inferred timelines or best guesses.

These are semantically distinct. Generic guidance does not substitute for the archived guardrail's specific abstention instruction.

### Probe Results

Both probes confirm no guardrail phrases are present regardless of memory context:

| Probe | Result |
|-------|--------|
| `assemble_system_prompt('<memories>[test]</memories>')` | Abstention guardrail phrases: **absent** |
| `assemble_system_prompt('')` | Abstention guardrail phrases: **absent**; generic fallback still appended |

**Classification**: Production assembly is **genuinely unwired**. No named import, verbatim text, or paraphrased content from the archived guardrail appears in `assemble_system_prompt()`.

---

## 4. Git History

*(Evidence: `.sisyphus/evidence/task-3-git-history.md`)*

### Three-Way Distinction: Git Pickaxe Results

**Unrestricted Repository Pickaxe** — `git log -S "MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL" --oneline --all`:

Returns **7 commits** — all docs/test artifacts where the string appears in markdown files only:

| Commit | Message | String appears in |
|--------|---------|------------------|
| `c30e9a83` | chore(feature-matrix): restore PR scope to target artifacts | Deleted `tests/benchmark_results/wave0_closure_memo.md`, `baselines.md`, etc. (19 `.md` files deleted; 0 Python occurrences) |
| `86ad9cf2` | fix(longmemeval): repair shipped parity harness scope | Deleted `.sisyphus/evidence/task-*.txt` files (40 files deleted; 0 Python occurrences) |
| `290b7c02` | docs(memory): restore roadmap baseline scope | `docs/MEMORY_UPGRADE_ROADMAP.md` |
| `e73927a7` | docs(wave1): include harness parity anchor plan in shipped scope | `.sisyphus/plans/wave1-prompt-surface-changes.md` |
| `d5d7bce8` | fix(tests): address F2/F3/F4 final verification rejections | `.sisyphus/plans/`, `.sisyphus/notepads/`, `harness_parity_postmortem.md` |
| `d83af3ba` | test(memory): route LongMemEval through production injection | `tests/benchmark_results/wave0_closure_memo.md`, `baselines.md`, `harness_parity_*.md` (added 46 `.md` files; 0 Python occurrences) |
| `07e9e6e7` | docs(benchmark): capture wave 0 closure artifacts | `tests/benchmark_results/wave0_closure_memo.md`, `baselines.md` |

Sample verification: commit `c30e9a83` modified `tests/longmemeval/evaluate.py` (Python file changed), but `evaluate.py` within that commit contains **0 occurrences** of the string.

**Python-Path-Limited Pickaxe** — `git log -S "MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL" --oneline --all -- '*.py'`:

**Returns ZERO output** — no committed Python file in any branch ever contained this string.

Additional probes confirming the same:

| Command | Output |
|---------|--------|
| `git log -S "MEMORY_EVIDENCE_ABSTENTION" --oneline --all -- '*.py'` | **(empty)** |
| `git log -S "abstention_guardrail" --oneline --all -- '*.py'` | **(empty)** |

**`orchestrator/prompts.py` Full History** — `git log --all -p --follow -- orchestrator/prompts.py | grep "MEMORY_EVIDENCE"`:

**Returns ZERO output** — the string never appeared in any version of `orchestrator/prompts.py` across its entire commit history. `prompts.py` was introduced in commit `938fafe6` and modified by ~15 commits.

### Summary Table

| Question | Answer | Evidence |
|----------|--------|----------|
| Any committed Python file defining the constant? | **NO** | Python-path-limited pickaxe: empty |
| Any committed Python file importing the constant? | **NO** | Python-path-limited pickaxe: empty |
| Any version of `orchestrator/prompts.py` containing it? | **NO** | Full file history grep: empty |
| Any version of `orchestrator/memory/injection.py` appending it? | **NO** | Full file history grep: empty |
| String appears anywhere in committed repo? | **YES — markdown docs only** | 7 unrestricted pickaxe commits, all `.md` files |
| Archive/`.cleanup/` guardrail committed? | **NO** | Uncommitted proposed diff |
| Stash changes committed? | **NO** | Uncommitted archive |

---

## 5. Test Evidence

*(Evidence: `.sisyphus/evidence/task-4-abstention-pytest.txt`, `.sisyphus/evidence/task-4-sweep-import.txt`)*

### Pytest Gate: 2/2 PASSED (EXIT_CODE 0)

```
tests/benchmark_longmemeval/test_abstention_regression_gate.py::test_abstention_regression_gate_is_enforced PASSED
tests/benchmark_longmemeval/test_abstention_regression_gate.py::test_abstention_sweep_changes_prompt_only_between_off_and_on PASSED
```

**What this gate tests**: The gate reads **saved benchmark harness artifacts** (pre-computed prompt strings stored in `tests/benchmark_longmemeval/`) and verifies that the benchmark's abstention-sweep mechanism changes prompt output between `off` and `on` states within the harness.

**What this gate does NOT test**: The gate does NOT call `assemble_system_prompt()` from `orchestrator/memory/injection.py`. A passing gate does NOT prove `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` is present in the production system prompt.

### Import Probe: ImportError (EXIT_CODE 1) — As Expected

```
tests/benchmark_longmemeval/abstention_sweep.py:15: from orchestrator.prompts import MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL
ImportError: cannot import name 'MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL' from 'orchestrator.prompts'
```

This is **audit evidence of the documented absent-constant state**, not an implementation failure. The test file was written against a proposed-but-never-applied modification. The broken import correctly reflects the current committed source state.

---

## 6. Stale Doc Inventory

*(Evidence: `.sisyphus/evidence/task-3-stale-doc-inventory.md`)*

13 benchmark result docs in `tests/benchmark_results/` make factual claims about `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL`. All errors originate from treating the uncommitted archived diff as if it were applied to committed source.

| Classification | Count | Action |
|---------------|-------|--------|
| `needs follow-up cleanup` | 8 | Contain explicit factual errors; cleanup recommended separately |
| `stale historical artifact` | 5 | Accurately document finding or project decision; preserve as-is |

### Needs Follow-Up Cleanup (8)

| File | Key Lines | False Claim |
|------|-----------|-------------|
| `wave0_path_a_implementation.md` | 70-72, 180 | "Guardrail appended at injection.py:330" (line 330 is memory-tools conditional, not guardrail) |
| `wave0_injection_audit.md` | 34 | "Appends MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL" (never appended; constant doesn't exist) |
| `wave0_benchmark_vs_production_injection.md` | 75, 130, 156-167 | "Production appends guardrail" (repeated; factually incorrect) |
| `wave0_benchmark_injection_origin.md` | 34, 81-82 | "Production depends on guardrail from prompts.py" (never existed) |
| `wave0_injection_historical_diff.md` | 63, 78, 139 | "Appended in assemble_system_prompt()" (never present in either version) |
| `wave0_dual_injection_test.md` | 100-106, 116, 157, 193 | "Production includes MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL" (never existed) |
| `wave0_low_score_diagnosis.md` | 50 | "including MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL" (never existed) |
| `wave0_benchmark_alignment_decision.md` | 33 | Table claims production has guardrail (factually incorrect) |

### Stale Historical Artifacts (5)

| File | Key Lines | Why Preserved |
|------|-----------|---------------|
| `wave0_closure_path_a_audit.md` | 107-171, 356 | Correctly identifies non-operational state; explicitly calls out path_a_implementation error |
| `wave0_closure_abs_zero_diagnosis.md` | 12, 102-121, 348 | Correctly identifies non-operational; framing reflects archived diff (incorrect about "defined in prompts.py" but primary finding is sound) |
| `wave0_closure_memo.md` | 324 | Correctly records project deferral decision (not a claim about current source) |
| `wave0_option_a_revised_sanity_assessment.md` | 147 | Same as above — project decision record |
| `wave0_closure_dirty_tree_audit.md` | 120-133 | Accurate audit of stashed changes found in dirty tree |

---

## 7. W1 TODO 4 vs W1 TODO 9: Consumer Impact vs Production Restoration

**This distinction is critical and must be preserved in all downstream work.**

### W1 TODO 4 — Consumer Impact (Harness Parity Diagnostic)

- **What it is**: Task 4 of Wave 1 is a **diagnostic** task that investigates whether the benchmark harness (running saved artifacts/precomputed prompts) reflects production behavior.
- **Current finding**: The benchmark harness has its own independent abstention-sweep mechanism that tests saved prompt strings — it is **not** wired through `assemble_system_prompt()`.
- **Consumer impact**: The benchmark harness is **not** measuring production guardrail behavior. Changing or fixing W1 TODO 4 does not affect whether the guardrail is in the production prompt.
- **The pytest gate passing** (`test_abstention_regression_gate.py` — 2/2) proves the harness-side toggle works on harness-side artifacts. It says nothing about production.

### W1 TODO 9 — Production Restoration

- **What it is**: W1 TODO 9 is the task to **restore** the abstention guardrail to the production prompt assembly path — i.e., actually wire `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` (or equivalent text) into `assemble_system_prompt()`.
- **Current state**: Production assembly is **genuinely unwired** — no named import, no verbatim text, no paraphrasing of the archived guardrail in `assemble_system_prompt()`.
- **Consumer impact**: W1 TODO 9 is what actually puts the guardrail in the production prompt. W1 TODO 4 does not.
- **Dependency**: W1 TODO 9 cannot be completed until Oracle selects a disposition from this audit. Disposition options are documented in Section 8.

**These are not interchangeable. W1 TODO 4 is a diagnostic/consumption question. W1 TODO 9 is a production restoration question.**

---

## 8. Disposition Options

Oracle must select exactly one of the following three dispositions. Each maps specific facts to a recommended action.

### Fact Summary

| # | Fact | Source |
|---|------|--------|
| F1 | `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` does not exist in any committed Python source | Task 1 import probe + Task 3 Python-path-limited pickaxe |
| F2 | The only source of the guardrail's text is uncommitted archive: `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713` | Task 1 guardrail text |
| F3 | `assemble_system_prompt()` imports only `DAEMON_SYSTEM_PROMPT`; does not import, reference, or append the guardrail constant or its text | Task 2 production assembly |
| F4 | Generic memory-tools disclosure (`injection.py:333`) and generic "do not speculate" guidance (`DAEMON_SYSTEM_PROMPT:63`) are semantically distinct from the archived guardrail's abstention instruction | Task 2 semantic distinction |
| F5 | The pytest gate tests harness-side artifacts, not production `assemble_system_prompt()` wiring | Task 4 test evidence |
| F6 | 8 benchmark result docs contain explicit factual errors claiming the guardrail is wired | Task 3 stale doc inventory |
| F7 | 5 benchmark result docs correctly identify non-operational state or project deferral decisions | Task 3 stale doc inventory |

### Decision Table

| Fact combination | => | Disposition |
|-----------------|-----|-------------|
| F1 + F2 + F3 are the operative facts; F4 confirms no substitution | => | **Option A: `wire-it`** — Restore the archived guardrail text to production prompt assembly by adding the constant to `orchestrator/prompts.py` and importing/appending it in `injection.py`. 8 stale docs remain; 5 historical artifacts preserved. |
| F1 + F2 + F3 + F4 — Oracle determines the semantic gap in generic guidance is or is not acceptable for the current product stage | => | **Option B: `defer-with-patch`** — Keep current production assembly as-is; address the semantic gap via separate future-wave prompt revision; do not touch `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` wiring until a holistic prompt revision is commissioned. |
| F1 + F2 + F3 + F7 — No operational guardrail is needed now; the 8 stale docs are documentation debt, not code | => | **Option C: `remove-dead-reference`** — Confirm no wiring exists (already the case); do not wire; treat the 8 stale docs as follow-up cleanup task separate from production code. |

### Option A: `wire-it`

**Action**: Add `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` to `orchestrator/prompts.py` (using the archived text from `.cleanup/.../tracked_modifications.diff:5710-5713`), then import and append it in `orchestrator/memory/injection.py`'s `assemble_system_prompt()`.

**Facts supporting**: F1 (constant absent), F2 (text recoverable from archive), F3 (genuinely unwired), F4 (generic guidance is not equivalent).

**Facts against**: F5 (pytest gate would still test harness-only artifacts, not production), F6 (8 stale docs need separate follow-up).

**Consumer impact for W1 TODO 4**: No change. The harness mechanism is independent of production wiring.

**Consumer impact for W1 TODO 9**: This IS W1 TODO 9 production restoration. It directly addresses the wiring gap.

### Option B: `defer-with-patch`

**Action**: Do not wire `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` at this time. Address the semantic gap between generic guidance and the archived guardrail in a holistic future prompt revision. The 8 stale benchmark docs are out of scope for this disposition.

**Facts supporting**: F4 (generic guidance is present; semantic gap may be acceptable for current product stage), F5 (harness-independent), F7 (5 docs correctly characterize deferral decision).

**Facts against**: F1, F2, F3 (wiring gap is real; generic guidance does not substitute for specific abstention instruction).

**Consumer impact for W1 TODO 4**: No change to harness diagnostic.

**Consumer impact for W1 TODO 9**: Deferred to a future holistic prompt revision wave.

### Option C: `remove-dead-reference`

**Action**: Confirm no wiring exists (already the case); treat the 13 docs as documentation debt to be cleaned up separately. No production code changes. The 8 stale docs with explicit factual errors are documentation fixes, not code fixes.

**Facts supporting**: F1 (no wiring), F6 (8 docs need cleanup), F7 (5 docs are fine).

**Facts against**: F3 (genuinely unwired — but this option says don't wire), F4 (generic guidance is not equivalent abstention instruction — but this option accepts that gap).

**Consumer impact for W1 TODO 4**: No change to harness diagnostic.

**Consumer impact for W1 TODO 9**: Explicitly NOT doing W1 TODO 9 production restoration.

---

## 9. Oracle Questions

Oracle must answer the following. The audit artifact is the sole fact package.

### Primary Question

**Select exactly one disposition** from `wire-it`, `defer-with-patch`, or `remove-dead-reference`. State the chosen disposition and explain the operational consequences for W1 TODO 9 production restoration specifically.

### Secondary Questions

**Q1**: Does the semantic gap between generic "do not speculate" guidance (DAEMON_SYSTEM_PROMPT:63) and the archived abstention guardrail constitute a functional deficit that requires correction in Option A (`wire-it`)? Or is the generic guidance sufficient for the current product stage, supporting Option B (`defer-with-patch`)?

**Q2**: Should the 8 stale benchmark result docs (`needs follow-up cleanup`) be addressed as part of this audit's disposition, or deferred to a separate documentation cleanup task?

**Q3**: For W1 TODO 4 (harness parity diagnostic): The pytest gate passes on harness artifacts. If the disposition is `wire-it` (Option A), should W1 TODO 4 be expanded to verify that `assemble_system_prompt()` also produces the guardrail in the production path? Or is W1 TODO 4 complete as-is (diagnostic confirmed; production wiring is a separate W1 TODO 9 concern)?

---

## 10. Archived Guardrail Text (Historical / Uncommitted)

The following is the **only authoritative source** of the `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` constant text. It is reproduced here for Oracle reference. **It is labeled historical/uncommitted — it was a proposed addition to `orchestrator/prompts.py` via `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713`, never applied to the working tree.**

```
When a question depends on retrieved memory or recent context, treat that memory as evidence rather than permission to guess.
If the available memory does not directly answer the question, say that you do not know or that the available memory is insufficient.
Do not fill gaps with nearby but non-answering details, inferred timelines, or best guesses.
Only answer confidently when the memory evidence directly supports the answer.
```

**Source**: `.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713`
**Classification**: Uncommitted proposed diff — historical candidate text, NOT current production behavior

---

## 11. Contradictory / Inconvenient Evidence Preserved

Per diagnostic-triage protocol, the following contradictory or inconvenient facts are explicitly preserved and must not be rationalized away:

1. **ImportError in `abstention_sweep.py`**: `tests/benchmark_longmemeval/abstention_sweep.py:15` fails at load time because the constant doesn't exist. This is audit evidence, not a bug to silently document.

2. **8 stale docs with false claims**: These are not cosmetic — they make concrete operational claims ("production path appends guardrail") that are factually incorrect. They must not be quietly corrected or deleted; they require explicit follow-up.

3. **Pytest gate passing with no production wiring**: The 2/2 pytest pass creates a false sense of coverage. The gate tests harness artifacts, not production wiring. This must be stated explicitly and not obscured.

4. **Unrestricted pickaxe returns 7 commits**: The prior version of the git history evidence incorrectly stated zero results. The unrestricted pickaxe DOES return 7 commits — all docs/test artifacts. The Python-path-limited pickaxe correctly returns zero. Both facts must be stated.

5. **Generic guidance is not equivalent**: The presence of generic "do not speculate" and generic "memory tools" text does not substitute for the specific abstention instruction in the archived guardrail. The semantic gap is real and must not be papered over with "we have something similar."

---

*End of audit artifact. Oracle disposition to be recorded in `tests/benchmark_results/abstention_guardrail_oracle_disposition.md` per Task 6.*
