# Abstention Guardrail PR Summary

**Artifact path**: `tests/benchmark_results/abstention_guardrail_pr_summary.md`
**Generated**: 2026-05-27T12:18:00Z
**Plan**: `.sisyphus/plans/abstention-guardrail-wiring-audit.md` — Task 8

---

## Findings Summary

The `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` constant is **genuinely unwired** from production prompt assembly:

- **Constant absent**: `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` does not exist in any committed Python source. Import probe returns `ImportError`.
- **Assembly unwired**: `assemble_system_prompt()` in `orchestrator/memory/injection.py` imports only `DAEMON_SYSTEM_PROMPT`. No reference to the guardrail constant, its verbatim text, or paraphrased equivalent.
- **Production unwired**: Python-path-limited pickaxe confirms the string is absent from production source (`orchestrator/prompts.py`). Test and archive references may exist; the critical gap is that `orchestrator/prompts.py` does not define/export the constant and `assemble_system_prompt()` does not import/append it.
- **Generic guidance is semantically distinct**: Generic "do not speculate" (DAEMON_SYSTEM_PROMPT:63) addresses not guessing whether a memory exists; it does not substitute for the archived guardrail's instruction to say "I don't know" when retrieved memory is insufficient.

Full audit: `tests/benchmark_results/abstention_guardrail_wiring_audit.md`

---

## Oracle Disposition

**Selected**: `wire-it`

The archived guardrail text is recoverable from uncommitted archive (`.cleanup/2026-05-06/safety-net/tracked_modifications.diff:5710-5713`). Production assembly is genuinely unwired. Generic memory guidance is semantically distinct. The `wire-it` disposition means: restore the guardrail to production prompt assembly, but via a separately authorized plan — not in this audit plan's scope.

---

## Changed Artifacts

### Intended (Committed in This PR)

| File | Change |
|------|--------|
| `tests/benchmark_results/abstention_guardrail_pr_summary.md` | New — this file |

### Already Committed (Prior Sessions, Part of Audit Chain)

| File | Commit | Description |
|------|--------|-------------|
| `tests/benchmark_results/abstention_guardrail_wiring_audit.md` | `3155d69f` | Main audit artifact (395 lines) |
| `abstention_guardrail_oracle_disposition.md` | Committed in this fix | Oracle selected `wire-it`; disposition with rationale and consequences |
| `abstention_guardrail_wire_it_plan_stub.md` | Committed in this fix | Non-production plan stub for separate wire-it authorization |

---

## Tests Run

**Final narrow pytest** (`tests/benchmark_longmemeval/test_abstention_regression_gate.py`):

```
tests/benchmark_longmemeval/test_abstention_regression_gate.py::test_abstention_regression_gate_is_enforced PASSED [ 50%]
tests/benchmark_longmemeval/test_abstention_regression_gate.py::test_abstention_sweep_changes_prompt_only_between_off_and_on PASSED [100%]

============================== 2 passed in 0.05s ===============================
EXIT_CODE: 0
```

**Critical caveat**: This gate tests harness-side saved prompt artifacts, NOT production `assemble_system_prompt()` wiring. A passing gate does NOT prove the guardrail is present in production.

---

## Forbidden Path Verification

All four forbidden paths verified absent from committed diff:

```
git diff --name-only HEAD -- orchestrator/memory/ orchestrator/eval/runner.py orchestrator/prompts.py .sisyphus/plans/wave1-prompt-surface-changes.md
# Returns: zero output (no modifications)
```

**Result**: No forbidden paths modified. ✅

---

## W1 Impact

### W1 TODO 4 — Harness Parity Diagnostic

- **Does NOT need patching** before W1 commissioning.
- The pytest gate passing confirms harness-side saved artifacts/checkpoints differ between guardrail-on and guardrail-off states. It does NOT prove the live harness toggle still runs or that production wiring is correct.
- W1 TODO 4 remains a harness parity diagnostic, not production restoration.

### W1 TODO 9 — Production Restoration

- **Must wait** for a separately authorized wire-it plan/task.
- This audit authorizes no production code changes. W1 TODO 9 production restoration cannot be completed in this plan's scope.
- The `wire-it` oracle disposition authorizes a future plan to add the guardrail constant to `orchestrator/prompts.py` and import/append it in `orchestrator/memory/injection.py`'s `assemble_system_prompt()`.

### Summary

| W1 Item | Status |
|---------|--------|
| TODO 4 | Complete as-is; does NOT need patch before W1 commissioning |
| TODO 9 | Deferred to separately authorized wire-it plan |

---

## Lifecycle

**Commit**: Made — `docs(memory): summarize abstention guardrail audit`
**Branch pushed**: `auth-device-model-2026-05-27` → `origin/auth-device-model-2026-05-27`
**PR created**: See URL below

---

## PR URL

https://github.com/sol-aeternum/Daemon/pull/3

---

*This summary is the final deliverable of the abstention guardrail wiring audit (Tasks 1–8). It does not implement production wiring. Production restoration (W1 TODO 9) requires a separately authorized plan.*
