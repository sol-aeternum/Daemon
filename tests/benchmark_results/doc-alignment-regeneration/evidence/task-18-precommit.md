# Task 18 — Pre-commit Hook Evidence

**Date:** 2026-05-31
**Task:** Wire blocking drift gate into pre-commit and CI
**Branch:** `doc-alignment-regeneration-2026-05-29`

---

## Hook Configuration

File: `.pre-commit-config.yaml`

```yaml
repos:
  - repo: local
    hooks:
      - id: doc-freshness
        name: doc-freshness
        description: Check documentation freshness against source of truth
        entry: python scripts/check_doc_freshness.py --mode fail
        language: system
        pass_filenames: false
        types: []
```

### Hook Details

| Field | Value |
|-------|-------|
| `id` | `doc-freshness` |
| `entry` | `python scripts/check_doc_freshness.py --mode fail` |
| `language` | `system` |
| `pass_filenames` | `false` |

---

## Pre-commit Availability Check

**Command:** `which pre-commit`

**Result:** `pre-commit not found`

**Exit code:** N/A (tool not installed)

**Impact:** pre-commit is not installed in the host environment. The hook configuration is valid but cannot be executed via `pre-commit run` directly.

---

## Consumer Command Attempt

The canonical pre-commit consumer command was attempted:

**Command:**
```bash
pre-commit run doc-freshness --all-files
```

**Raw output:**
```
/usr/bin/bash: line 1: pre-commit: command not found
```

**Exit code:** `127`

**Result:** FAIL — pre-commit is not installed in the host environment.

---

## Fallback Execution

Since `pre-commit` is unavailable, the hook command was executed directly:

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail
```

**Output:**
```
No drift detected.
```

**Exit code:** `0`

**Result:** PASS — direct command parity confirmed.

---

## Conclusion

- Hook configuration is correctly structured and uses `language: system` with `pass_filenames: false`
- Hook entry matches the required fail-mode command exactly
- Consumer command `pre-commit run doc-freshness --all-files` failed with exit 127 due to missing tool
- Missing pre-commit is an environment/tooling blocker documented for Task 18; direct hook command parity still passed (exit 0)
- Installation of `pre-commit` would enable the standard consumer path
