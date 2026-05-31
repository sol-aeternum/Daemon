# Task 15 — Surgical Edit Mapping

**Date**: 2026-05-31
**Branch**: `doc-alignment-regeneration-2026-05-29`
**Task**: Fix residual drift in `README.md` and `QUICKSTART.md`

## Drift Audit Findings Addressed

### DRIFT-14: Subagent list (README.md)

**Audit Finding**: README:67-68 subagent list omits `@document`; claims `@code` and `@reader` as implemented when they are "NOT IMPLEMENTED" (Web experimental per FEATURE_MATRIX).

**Edit 1 — Architecture diagram** (line 32):
- **Changed**: `│  │                 │  │ @code @reader            │  │`
- **To**: `│  │                 │  │ @document @code @reader   │  │`
- **Rationale**: Added `@document` to the subagent orchestrator box in the ASCII diagram.

**Edit 2 — Subagents section** (lines 120-125):
- **Changed**: `@image` description from "Image and video generation (xAI Imagine API)" to "Image generation (xAI) and video generation (xAI, fal.ai/Kling)"
- **Changed**: Added `@document — Document file generation` after `@audio`
- **Changed**: `@code` description from "Code generation and analysis" to "Code generation (experimental, not fully implemented)"
- **Changed**: `@reader` description from "Document reading" to "Document reading (experimental, not fully implemented)"
- **Rationale**: @document is Cross-client stable per FEATURE_MATRIX; @code and @reader are Web experimental/NOT IMPLEMENTED; @image video uses fal.ai/Kling in addition to xAI.

---

### DRIFT-15: Missing `/skills` endpoint (README.md)

**Audit Finding**: README:102-115 API table does not list `/skills` route; source confirms route exists at `main.py:1963`.

**Edit 3 — API Routes table** (after `/memories` row):
- **Changed**: Added `| `/skills` | GET/POST | Skills management (list, create, upload, update, delete) |`
- **Rationale**: `/skills` route is registered in main.py and documented in truth_set.md routes table.

---

## Changes Not Applied

| Finding | File | Reason |
|---------|------|--------|
| QUICKSTART.md drift | — | `drift_audit.md` classifies QUICKSTART as "ZERO DRIFT — No Structured Claims Found" |

## Git Diff Summary

```
README.md:
  + @document added to architecture diagram
  + @document added to Subagents list
  ~ @image description corrected (xAI for images, xAI+fal.ai/Kling for video)
  ~ @code/@reader marked as "experimental, not fully implemented"
  + /skills endpoint added to API Routes table

QUICKSTART.md:
  (no changes — zero drift per audit)
```

## Verification

All three edits are surgical and confined to the specific drift items flagged by the audit:
1. DRIFT-14 subagent list → corrected with @document addition and @code/@reader status fix
2. DRIFT-15 missing /skills endpoint → added to API Routes table
