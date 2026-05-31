# Task 21 — PR Creation Evidence

**Task**: 21 — Push branch and open PR
**Date**: 2026-05-31T14:35:00Z
**Branch**: `doc-alignment-regeneration-2026-05-29`

---

## gh pr view Raw Output

```json
{
  "title": "Authoritative Documentation Alignment, Regeneration, and Drift Gating",
  "baseRefName": "main",
  "headRefName": "doc-alignment-regeneration-2026-05-29",
  "state": "OPEN",
  "url": "https://github.com/sol-aeternum/Daemon/pull/6",
  "mergeable": "CONFLICTING",
  "mergeStateStatus": "DIRTY",
  "mergedAt": null,
  "statusCheckRollup": []
}
```

**Checks note**: `statusCheckRollup` is empty (`[]`). No CI checks have been triggered on this PR yet. Final verification wave (F1-F4) is pending/not yet run. Do not claim CI is green.

---

## Field Verification

| Field | Value | Verified |
|-------|-------|----------|
| title | Authoritative Documentation Alignment, Regeneration, and Drift Gating | ✅ |
| baseRefName | main | ✅ |
| headRefName | doc-alignment-regeneration-2026-05-29 | ✅ |
| state | OPEN | ✅ |
| url | https://github.com/sol-aeternum/Daemon/pull/6 | ✅ |
| mergeable | CONFLICTING | ✅ |
| mergedAt | null | ✅ |
| statusCheckRollup | [] | ✅ (empty — no checks run yet) |

---

## PR Title

```
Authoritative Documentation Alignment, Regeneration, and Drift Gating
```

---

## PR Body Summary

The PR body includes all required sections:
- Objective verbatim from plan
- Key architecture decisions (source hierarchy T0>T1>T3>T2, stdlib-only gate, CI wires only doc-freshness)
- Artifact list with paths and descriptions
- F1-F4 signoff state (all pending, F3 proven by e2e_gate_proof.md)
- Gate evidence summary (Task 17 approval, Task 18 parity, Task 19 E2E)
- Manual follow-up items (feature-matrix automation, pre-commit host availability)
- Plan reference link

---

## Push Command

```bash
git push origin doc-alignment-regeneration-2026-05-29
```

Non-force push. Branch exists at `origin/doc-alignment-regeneration-2026-05-29`.
