# Wave 0 — E5 Revised Sanity Gate Structural Assessment (Option A)

**Date:** 2026-05-04
**Task:** E5
**Status:** COMPLETE
**Assessment:** PASS — All Option A structural conditions satisfied

---

## Source of Truth

Primary sources for this assessment:
- `tests/benchmark_results/wave0_option_a_production_aligned_baseline.md` (C1-D)
- `.sisyphus/evidence/c1-d-production-aligned-baseline-lock.json` (C1-D evidence)
- `.sisyphus/evidence/c1-c-option-a-rerun.json` (C1-C)
- `.sisyphus/evidence/c1-a-28-error-disposition.json` (C1-A)
- `.sisyphus/evidence/c1-b-abs-disposition.json` (C1-B)

Numeric values are verified by direct computation from source artifacts, not copied blindly.

---

## Revised Sanity Outcome

### Structural Conditions — All Satisfied

| Structural Condition | Source | Status | Value |
|---|---|---|---|
| Production-aligned full-corpus artifacts exist and are parseable | C1-D | ✅ SATISFIED | 500-row results + score JSON |
| Raw artifact score preserved | C1-D | ✅ SATISFIED | 49/500 = 0.098 |
| Option A disposition-adjusted baseline locked | C1-D | ✅ SATISFIED | 49/473 = 0.10359408033826638 |
| 27 invalid-ciphertext rows are bounded error-class exclusions from C1-A | C1-A, C1-C | ✅ SATISFIED | 0/27 have retrieval_log entries; per-question attribution structurally impossible |
| `7401057b` null-content harness failure fixed and verified | C1-A, C1-C | ✅ SATISFIED | error=null, hypothesis non-empty, memories_used=5 |
| ABS category wiring fixed and verified | C1-B, C1-C | ✅ SATISFIED | 30 ABS rows, 16/30 = 0.5333 |
| No re-ingestion in C1-C/C1-D/E5 | C1-C, C1-D, E5 | ✅ SATISFIED | checkpoint reused; no new ingest calls |
| `orchestrator/memory/**` unchanged | C1-A–C1-E | ✅ SATISFIED | `git diff -- orchestrator/memory/` = clean throughout |

---

## Raw Score Preservation

The raw official artifact score is **49/500 = 0.098** and is **not hidden, not rounded, not inflated**.

```
verified: 49 / 500 = 0.098
```

This value appears in:
- `c1-c-option-a-rerun.json` → `official_raw_aggregate_correct_over_500: 0.098`
- `c1-d-production-aligned-baseline-lock.json` → `official_raw_score: 0.098`
- `wave0_option_a_production_aligned_baseline.md` → official artifact score table

---

## Option A Disposition-Adjusted Baseline

Under **User Option A** (supersedes old 15% aggregate and per-category floor gates):

| Field | Value |
|---|---|
| Excluded rows | 27 invalid-ciphertext (bounded error-class exclusion, C1-A) |
| Adjusted denominator | 473 |
| Correct judgments (unchanged) | 49 |
| **Disposition-adjusted baseline** | **49/473 = 0.10359408033826638** |

```
verified: 49 / 473 = 0.10359408033826638
```

The numerator is identical to the raw artifact score. The denominator shrinks because only the 27 error-class rows are excluded — this is an analytical carry-forward, not a relitigation.

---

## Old Gates — Explicitly Superseded

The following gates were pre-data diagnostic sanity bounds. Under **User Option A**, they are **superseded — not failed blockers**:

| Old Gate | Raw Value | Would Pass Old Gate? | Option A Status |
|---|---|---|---|
| `aggregate > 0.15` | 49/500 = 0.098 | No | **SUPERSEDED** — Option A defines new structural baseline |
| `success_count >= 495` | 473 | No | **SUPERSEDED** — bounded exclusions apply; 27 error-class rows excluded analytically |
| Per-category floor gates | Multiple below floor | No | **SUPERSEDED** — Option A has no per-category floor requirement |

These gates are recorded for traceability. They do not block Wave 0 Option A closure.

---

## C1 Disposition Chain

```
C1-A (bounded exclusions + null guard)
  └─▶ C1-B (ABS category wiring fix)
        └─▶ C1-C (canonical rerun — verify A + B end-to-end)
              └─▶ C1-D (baseline locked)
                    └─▶ E5 (revised sanity gate structural assessment)
```

**C1-A:** 27 invalid-ciphertext rows → bounded error-class exclusion (analytical, per-question attribution structurally impossible); `7401057b` → null guard fixed in `evaluate.py:405-407`.

**C1-B:** `_abs` suffix detection added to `evaluate.py` and `runner.py` → ABS bucket correctly populated: 30 rows, 16/30 = 0.5333.

**C1-C:** Canonical evaluate resumed from checkpoint (484 completed), finished 16, scored full corpus. Verified A and B end-to-end.

**C1-D:** Baseline locked in `wave0_option_a_production_aligned_baseline.md` and `c1-d-production-aligned-baseline-lock.json`. Accepted for E5–E9.

---

## Invalid-Ciphertext Exclusion — Bounded Error-Class (C1-A)

27 rows errored with `Invalid ciphertext: decryption failed (wrong key or corrupted data)`.

**Key structural fact:** 0/27 rows have a `retrieval_log` entry. The exception fires at `store.py:903` before the async log write at `retrieval.py:696` is scheduled. Per-question candidate attribution is structurally impossible.

**Key recovery fact:** Key/config recovery requires `orchestrator/memory/` changes, which are prohibited under N1. The 27-row exclusion is analytical.

**No new rows added in C1-C:** IDs are identical to C1-A.

---

## 7401057b Null Guard Fix — Verified in C1-C Rerun

| Field | C1-A State | C1-C Rerun State |
|---|---|---|
| question_id | 7401057b | 7401057b |
| error | `AttributeError: 'NoneType'...` | `null` |
| hypothesis | (empty) | non-empty |
| memories_used | 0 | 5 |
| judgment | N/A | incorrect |
| none_type_strip_present | true | false |

Fix: `evaluate.py:405-407` — `content = message.get('content', '')` then `return content if content is not None else ''`.

---

## ABS Category Wiring Fix — Verified (C1-B + C1-C)

| Metric | Before Fix (C3) | After Fix (C1-C) |
|---|---|---|
| ABS bucket rows | 0 | 30 |
| ABS accuracy | N/A | 0.5333 (16/30) |
| Official judgment: correct | N/A | 16 |
| Official judgment: incorrect | N/A | 13 |
| Official judgment: partially_correct | N/A | 1 |

Fix: `evaluate.py:829-833` + `runner.py:1716-1720` — `if question_id.endswith('_abs'): category = 'ABS'`.

Note: `MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL` production injection remains deferred under N1 (not in scope for Wave 0).

---

## Remaining Concerns — Non-Blocking for Wave 0 Option A

The following are **W1+ follow-up items**, not Wave 0 Option A blockers:

| Concern | Classification | Action |
|---|---|---|
| Production guardrail not wired | **W1+** — N1 deferral | Separate planning required |
| Invalid-ciphertext storage anomaly | **W1+** — requires memory code changes | Cannot be fixed without N1 changes; bounded analytically |
| Future W1 gate redesign | **W1+** — structural | Option A defines new structural baseline; future gates need redesign under new semantics |

Wave 0 Option A structural assessment is **complete and passes** based on the Option A contract.

---

## Notepad Append Reference

E5 findings appended to: `.sisyphus/notepads/wave0-closure-fresh/learnings.md`

---

## Verification Summary

| Check | Method | Result |
|---|---|---|
| JSON parses | `python3 -c "import json"` on all evidence files | ✅ PASS |
| No secrets in markdown | Grep scan for `sk-`, `Bearer `, `api_key`, `password`, `secret` | ✅ CLEAN |
| No placeholders in markdown | Grep scan for `<placeholder>`, `REDACTED` (inappropriate context) | ✅ CLEAN |
| No stale final-gate language | Grep for `final gate`, `pass/fail`, `gate.*pass` | ✅ CLEAN |
| `orchestrator/memory/` diff | `git diff -- orchestrator/memory/` | ✅ CLEAN (no changes) |
| Raw score not inflated | Direct computation from source JSON | ✅ 49/500 = 0.098 preserved |
| Numerator unchanged | 49 correct judgments identical in raw and adjusted | ✅ CONFIRMED |