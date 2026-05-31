# CI State — Documentation Gate Wiring

**Task**: 4 — Determine real CI and pre-commit state
**Branch**: `doc-alignment-regeneration-2026-05-29`
**Date**: 2026-05-31
**Verification**: `evidence/task-4-ci-precommit.md`

---

## 1. Workflows

**Status**: NO workflows exist.

- `.github/workflows/` directory does not exist on this branch.
- No GitHub Actions YAML files are present anywhere in the repository.
- No workflow currently runs documentation freshness checks.
- No workflow currently runs feature matrix linting.

---

## 2. Pre-commit

**Status**: NO pre-commit config exists.

- `.pre-commit-config.yaml` does not exist anywhere in the repository.
- No local pre-commit hook runs documentation freshness or feature matrix checks.

---

## 3. PR Template

**Status**: EXISTS — human-enforced only.

- `.github/pull_request_template.md` is present.
- Contains a "Feature Matrix" checklist section asking whether the PR touches user-visible behavior.
- Contains a "Matrix updated if user-visible behavior changed" checklist item.
- **No Source-of-Truth section** is present in the PR template.
- **No automated CI gate** — purely human self-review checklist.
- **No doc-freshness section** in the PR template.

---

## 4. Feature Matrix Wiring

**Status**: NOT wired to CI or pre-commit.

- `scripts/lint_feature_matrix.py` EXISTS (231 lines, validated structure only — no execution proof in this audit).
- `lint_feature_matrix.py` is NOT referenced in any workflow YAML file (grep across all YAML/yml returned zero matches).
- `lint_feature_matrix.py` is NOT referenced in any pre-commit config (no pre-commit config exists).
- AGENTS.md line 147 explicitly states: *"CI integration is a separate follow-up; until then, discipline is human-enforced via PR review."*
- **Manual Follow-Up required**: Feature-matrix CI/pre-commit wiring is not in scope for TODO 18; it must be recorded as a Manual Follow-Up unless the plan scope is amended.

---

## 5. Doc Freshness Wiring

**Status**: NOT wired to CI or pre-commit.

- `scripts/check_doc_freshness.py` does NOT exist.
- No doc-freshness check is implemented anywhere in the repository.
- No workflow, pre-commit hook, or CI gate currently enforces documentation freshness.

---

## 6. Branch and Dirty State

- Current branch: `doc-alignment-regeneration-2026-05-29`
- Dirty state (pre-task): Only `tests/benchmark_results/doc-alignment-regeneration/` untracked — consistent with prior Task 1-3 artifacts.
- Post-task: Only Task 4 artifacts added (this file + evidence).

---

## 7. Conclusions for TODO 18

TODO 18 (Wire documentation gates) must:

1. **Create `.pre-commit-config.yaml`** — no pre-commit config exists; TODO 18 must create it and add a `doc-freshness` local hook running `python scripts/check_doc_freshness.py --mode fail`.
2. **Create `.github/workflows/docs-freshness.yml`** — no workflow directory exists; TODO 18 must create it and invoke `python scripts/check_doc_freshness.py --mode fail`.
3. **Wire the doc-freshness script** into both pre-commit and workflow after Task 7 creates it.
4. **Feature-matrix CI wiring** remains a Manual Follow-Up unless TODO 4/17 scope is amended (per plan acceptance criterion line 250).

**What is NOT TODO 18's scope**:
- **PR template Source-of-Truth section** → Task 16 edits `.github/pull_request_template.md`.
- **`scripts/check_doc_freshness.py` creation** → Task 7 implements it before TODO 18 wires it.
- **Feature-matrix automation** → recorded as Manual Follow-Up (not TODO 18 scope).

**No existing CI gate will break** by adding documentation checks — there are no existing CI gates to conflict with.
