# Task 4 — CI and Pre-commit State Evidence

**Task**: Determine real CI and pre-commit state
**Branch**: `doc-alignment-regeneration-2026-05-29`
**Date**: 2026-05-31
**Agent**: Atlas (quick, filesystem/config audit)

---

## Commands and Raw Outputs

### 1. `.github/workflows/` existence check

```bash
$ glob pattern: .github/workflows/*
Result: No files found
```

```bash
$ ls -la /home/sol/daemon/.github/
total 4
drwxr-xr-x 1 sol sol   48 May 31 18:49 .
drwxr-xr-x 1 sol sol 1152 May 31 18:49 ..
-rw-r--r-- 1 sol sol  427 May 27 20:58 pull_request_template.md
```

**Conclusion**: `.github/workflows/` directory does not exist. No GitHub Actions workflows are present on this branch.

---

### 2. `.pre-commit-config.yaml` existence check

```bash
$ glob pattern: .pre-commit-config.yaml
Result: No files found
```

**Conclusion**: No `.pre-commit-config.yaml` exists anywhere in the repository.

---

### 3. `.github/pull_request_template.md` contents

```bash
$ cat .github/pull_request_template.md
## Description

<!-- What does this PR change? -->

## Feature Matrix

Does this PR add, remove, or change a user-visible feature?

- [ ] No — this PR does not touch user-visible behavior
- [ ] Yes — and I have updated `docs/FEATURE_MATRIX.md` with the change (link to the modified row(s) in this PR's diff)

## Checklist

- [ ] Tests added or updated where applicable
- [ ] Matrix updated if user-visible behavior changed
```

**Findings**:
- PR template EXISTS at `.github/pull_request_template.md`
- Has a "Feature Matrix" checklist section (lines 5-10) — human-enforced, not CI-automated
- NO "Source-of-Truth" section
- NO doc-freshness section
- No CI automation — purely human checklist

---

### 4. `scripts/lint_feature_matrix.py` existence and wiring check

```bash
$ glob pattern: scripts/lint_feature_matrix.py
Result: Found 1 file — /home/sol/daemon/scripts/lint_feature_matrix.py
```

```bash
$ grep -r "lint_feature_matrix" --include="*.yaml" --include="*.yml" --include="*.txt" --include="*.sh"
Result: No matches found
```

```bash
$ grep -r "lint_feature_matrix" --include="AGENTS.md"
AGENTS.md:147: Run `python scripts/lint_feature_matrix.py` before committing matrix changes. CI integration is a separate follow-up; until then, discipline is human-enforced via PR review.
```

**Findings**:
- `scripts/lint_feature_matrix.py` EXISTS (231 lines)
- `lint_feature_matrix.py` is NOT wired into any workflow (no `.github/workflows/`)
- `lint_feature_matrix.py` is NOT wired into any pre-commit (no `.pre-commit-config.yaml`)
- AGENTS.md line 147 explicitly states: "CI integration is a separate follow-up; until then, discipline is human-enforced via PR review"

---

### 5. Feature Matrix Wiring / Doc Freshness check

```bash
$ grep -r "check_doc_freshness\|doc-freshness\|feature.?matrix" --include="*.yaml" --include="*.yml"
Result: No matches found
```

```bash
$ grep -r "workflow|GITHUB_\|github_actions" --include="*.yaml" --include="*.yml"
Result: No matches found
```

**Findings**:
- No doc-freshness check exists anywhere in the repo
- No workflow YAML files reference feature matrix or doc freshness
- No pre-commit hooks reference feature matrix or doc freshness

---

### 6. Branch and dirty state

```bash
$ GIT_MASTER=1 git status --porcelain
?? tests/benchmark_results/doc-alignment-regeneration/
```

```bash
$ GIT_MASTER=1 git rev-parse --abbrev-ref HEAD
doc-alignment-regeneration-2026-05-29
```

```bash
$ ls -la .github/
total 4
drwxr-xr-x 1 sol sol   48 May 31 18:49 .
drwxr-xr-x 1 sol sol 1152 May 31 18:49 ..
-rw-r--r-- 1 sol sol  427 May 27 20:58 pull_request_template.md
```

---

## Summary for Oracle (Task 5) and TODO 18

| Asset | Exists? | Wired to CI? | Wired to pre-commit? |
|---|---|---|---|
| `.github/workflows/` | **NO** | N/A | N/A |
| `.pre-commit-config.yaml` | **NO** | N/A | N/A |
| `.github/pull_request_template.md` | YES | NO (human checklist only) | NO |
| `scripts/lint_feature_matrix.py` | YES | **NO** | **NO** |
| `scripts/check_doc_freshness.py` | **NO** | N/A | N/A |

**Feature-matrix wiring — Manual Follow-Up**: `lint_feature_matrix.py` exists but is NOT wired to CI or pre-commit. Per plan acceptance criterion line 250, this is recorded as a Manual Follow-Up, not as a TODO 18 requirement.

**No workflow files or pre-commit config were created by this task.** Only read-only filesystem checks were performed.
