# Task 18 — Command Parity Evidence

**Date:** 2026-05-31
**Task:** Wire blocking drift gate into pre-commit and CI
**Branch:** `doc-alignment-regeneration-2026-05-29`

---

## Command Parity Verification

### Pre-commit Hook Entry

File: `.pre-commit-config.yaml` (line 7)

```yaml
entry: python scripts/check_doc_freshness.py --mode fail
```

### GitHub Workflow Step

File: `.github/workflows/docs-freshness.yml` (line 41)

```yaml
- run: python scripts/check_doc_freshness.py --mode fail
```

### Parity Comparison

| Component | Command |
|-----------|---------|
| Pre-commit hook `entry` | `python scripts/check_doc_freshness.py --mode fail` |
| GitHub Actions `run` | `python scripts/check_doc_freshness.py --mode fail` |
| **Parity** | **EXACT MATCH** |

Both invoke the identical fail-mode script command: `python scripts/check_doc_freshness.py --mode fail`.

---

## Feature Matrix Automation — NOT Wired

### Plan Decision

Task 18 scope (per plan line 762) explicitly states: "Do not add feature-matrix hook unless scope amended."

The plan references "only if explicitly approved by TODO 4/17" for running `python scripts/lint_feature_matrix.py` in CI.

Task 17 Oracle re-review approval (`task-17-amendment-rereview.md:12`) states:
> "Task 18 may wire the blocking fail-mode doc-freshness gate."

This approval covers doc-freshness only. No explicit approval exists for wiring `lint_feature_matrix.py`.

### Current Status

- `scripts/lint_feature_matrix.py` is NOT wired into `.pre-commit-config.yaml`
- `scripts/lint_feature_matrix.py` is NOT wired into `.github/workflows/docs-freshness.yml`
- Feature-matrix automation is recorded as **Manual Follow-Up** per inherited decision

### Manual Follow-Up Items

| Item | Status | Reference |
|------|--------|-----------|
| Feature-matrix CI wiring | Manual Follow-Up | AGENTS.md line 147; ci_state.md:50-51 |
| Feature-matrix pre-commit wiring | Manual Follow-Up | ci_state.md:51 |

---

## Workflow Path Triggers

The workflow correctly triggers on:
- `docs/**`
- `*.md`
- `orchestrator/config.py`
- `orchestrator/routes/**`
- `orchestrator/main.py`
- `orchestrator/memory/**`
- `migrations/**`
- `scripts/check_doc_freshness.py`
- `.github/workflows/docs-freshness.yml`
- `.pre-commit-config.yaml`

This matches the required source-of-truth inputs per the plan (line 762).

---

## Conclusion

- **Command parity:** CONFIRMED — both pre-commit hook and workflow use identical fail-mode command
- **Feature matrix:** NOT wired — Manual Follow-Up per inherited decision and lack of explicit approval
- **Scope:** Task 18 remains limited to doc-freshness gate wiring only
