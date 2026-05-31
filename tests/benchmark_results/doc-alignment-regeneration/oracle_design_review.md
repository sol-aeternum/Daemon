# Oracle Design Review — Hierarchy and Gate Design

**Task**: 5 — Oracle design review of hierarchy and gate design  
**Date**: 2026-05-31T11:37:18Z  
**Reviewed artifacts**: `drift_audit.md`, `truth_set.md`, `ci_state.md`, Task 4 evidence, plan excerpts, accumulated notepads

---

## Executive Verdict

VERDICT: APPROVE

The Task 2, Task 3, and Task 4 artifacts are coherent enough to drive Tasks 6-18, provided downstream tasks treat `truth_set.md` and the recorded correction notes as binding wherever `drift_audit.md` contains known false positives. No blocker amendment is required before Task 6; Task 6 may proceed to author `docs/SOURCES_OF_TRUTH.md`.

---

## Reviewed Inputs

- Plan Task 5 requires this review and an explicit verdict on `CURRENT_ISSUES.md`, precedence, linter v1 scope, exception format, and CI fail-mode activation (`.sisyphus/plans/doc-alignment-regeneration.md:269-306`).
- Plan Task 6 already specifies the canonical hierarchy deliverable, including `T0/T1/T2/T3` definitions, precedence `T0 > T1 > T3 > T2`, `CURRENT_ISSUES.md` as T3 rollup, same-tier conflict policy, source missing/rename behavior, and exception comment syntax (`.sisyphus/plans/doc-alignment-regeneration.md:308-330`).
- Plan Task 7 scopes the linter to structured checks and excludes autofix, semantic claim scoring, hardcoded stale deny-lists, and third-party dependencies (`.sisyphus/plans/doc-alignment-regeneration.md:348-351`).
- Plan Task 17 gates blocking activation on a later false-positive review before Task 18 wires CI/pre-commit (`.sisyphus/plans/doc-alignment-regeneration.md:723-740`).
- `drift_audit.md` provides the broad markdown inventory and proposed classifications, including `CURRENT_ISSUES.md` as T3 operational rollup (`tests/benchmark_results/doc-alignment-regeneration/drift_audit.md:21-41`, `:170-175`, `:277-301`).
- `truth_set.md` provides the source-derived facts and explicitly records corrections to Task 2 around `/providers`, `/health` + `/status`, and xAI/fal video support (`tests/benchmark_results/doc-alignment-regeneration/truth_set.md:1-18`, `:156-179`, `:187-289`, `:495-523`).
- `ci_state.md` confirms there are no workflows or pre-commit config today, and scopes TODO 18 to doc-freshness wiring while leaving feature-matrix automation as Manual Follow-Up (`tests/benchmark_results/doc-alignment-regeneration/ci_state.md:10-27`, `:43-61`, `:73-87`).
- Accumulated notepad corrections confirm Task 2 false positives and Task 4 scope corrections (`.sisyphus/notepads/doc-alignment-regeneration/issues.md:62-80`, `:116-139`; `.sisyphus/notepads/doc-alignment-regeneration/learnings.md:84-111`, `:112-136`).

---

## Findings

### 1. Source hierarchy and precedence are sound

Approved hierarchy: `T0 code/config/migrations/manifests > T1 curated gated docs > T3 operational logs/rollups > T2 narrative docs`.

This is consistent with the plan's explicit Oracle-review resolution (`.sisyphus/plans/doc-alignment-regeneration.md:31-36`) and Task 6 requirements (`.sisyphus/plans/doc-alignment-regeneration.md:308-330`). `truth_set.md` correctly restricts authoritative extraction to T0/T1 and treats T2 narrative docs only as contrast notes (`truth_set.md:7`, `:15-18`). Task 6 should write the precedence as the explicit ordered chain above; do not imply precedence from the visual order of the truth-set table, because that table lists T2 before T3 while the plan requires T3 above T2.

### 2. `CURRENT_ISSUES.md` should be T3 operational rollup, not T2 narrative source

Approve `CURRENT_ISSUES.md` as T3. `drift_audit.md` classifies it as an operational rollup (`drift_audit.md:28`, `:170-175`), and `truth_set.md` names `CURRENT_ISSUES.md`/`TRIAGE.md` as operational rollups rather than authoritative fact sources (`truth_set.md:18`).

Task 13 must regenerate it as a curated active issue rollup with an explicit relationship to raw `TRIAGE.md`, per plan (`.sisyphus/plans/doc-alignment-regeneration.md:574-593`). Its current “No outstanding issues!” status should not be used as a T0/T1 source fact; it is a rollup claim to curate against raw triage/source evidence.

### 3. Task 2 drift audit is useful but has known corrected false positives

`drift_audit.md` is acceptable as inventory, but not as final source truth for routes/providers. The downstream source of truth must be `truth_set.md` for these corrected items:

- `/providers` exists in `main.py`; Task 2's “not implemented” finding is superseded by `truth_set.md` (`truth_set.md:203-208`, `:497-501`; conflicting Task 2 text at `drift_audit.md:73-76`, `:316`).
- Both `/health` and `/status` exist, with different roles; Task 2's “source is `/status`” simplification is superseded by `truth_set.md` (`truth_set.md:203-208`, `:284-289`, `:503-506`; conflicting Task 2 text at `drift_audit.md:68-71`, `:315`).
- Both xAI and fal/Kling implement video; Task 2's “xAI images only / fal video” phrasing is superseded by `truth_set.md` (`truth_set.md:156-173`, `:508-513`; conflicting Task 2 text at `drift_audit.md:99-102`, `:313`).
- Sora references are still stale and should be removed from regenerated docs or env-var commentary where encountered (`truth_set.md:175-179`, `:515-518`; `drift_audit.md:63-66`, `:314`, `:321`).

These are not blockers because `truth_set.md` and the notepad corrections capture the correct facts clearly enough for Tasks 6-18.

### 4. Linter v1 scope is correctly bounded

Approve linter v1 as structured-fact-only. The plan's required checks are high-confidence extractable facts — embedding models/dimensions, dedup thresholds, migration inventory, feature states, provider names, route names, and env-var names where extractable (`.sisyphus/plans/doc-alignment-regeneration.md:350-351`).

Do not add semantic prose validation in Task 7. Claims like “system is healthy,” “fully operational,” or broad roadmap phase status should only be gated if converted into explicit structured checks with source extraction; otherwise they belong to curated docs and review, not automated linter judgment.

### 5. Exception mechanism is adequate if reason and expiry are mandatory

Approve the planned exception mechanism with exact syntax:

```html
<!-- DOC_FRESHNESS_EXCEPTION: <check_id> expires=YYYY-MM-DD reason="..." -->
```

Task 6 documents this exact syntax (`.sisyphus/plans/doc-alignment-regeneration.md:325-330`), and Task 7 requires parsing check id, expiry, and reason while failing on expired exceptions or missing sources in fail mode (`.sisyphus/plans/doc-alignment-regeneration.md:348-369`). Implementation expectation: missing `reason`, empty `reason`, malformed `expires`, or expired `expires` should not silently suppress drift; valid unexpired exceptions may suppress the specific `check_id` only and should still be reported as an intentional exception.

### 6. CI/pre-commit strategy is correctly scoped

Approve the staged gate strategy: create the doc-freshness linter first, review false positives before activation, then wire fail-mode into pre-commit and GitHub Actions.

`ci_state.md` confirms no workflows or pre-commit config exist now (`ci_state.md:10-27`) and no doc-freshness wiring exists (`ci_state.md:55-61`). TODO 18 should therefore create `.pre-commit-config.yaml` and `.github/workflows/docs-freshness.yml`, both running `python scripts/check_doc_freshness.py --mode fail` (`ci_state.md:73-80`; `.sisyphus/plans/doc-alignment-regeneration.md:760-779`). Feature-matrix automation remains Manual Follow-Up, not TODO 18 scope, unless the plan is explicitly amended (`ci_state.md:43-52`, `:82-87`; `.sisyphus/plans/doc-alignment-regeneration.md:762-763`).

---

## Blockers/Required Amendments

No blocking amendments before Task 6.

Required downstream execution constraints:

1. Task 6 must write the precedence explicitly as `T0 > T1 > T3 > T2` and must classify `docs/CURRENT_ISSUES.md` as T3 operational rollup, not as a T2 narrative document.
2. Task 6/7 must document and implement the exception format exactly, with mandatory `check_id`, `expires=YYYY-MM-DD`, and non-empty `reason="..."`.
3. Tasks 8-16 must use `truth_set.md` corrections over `drift_audit.md` false positives for `/providers`, `/health` + `/status`, and xAI/fal video support.
4. Task 18 must wire only the doc-freshness gate unless feature-matrix automation is explicitly re-scoped; feature-matrix CI/pre-commit remains Manual Follow-Up.

---

## Non-Blocking Notes

- `drift_audit.md` still contains corrected false positives in its body and summary (`drift_audit.md:73-76`, `:99-102`, `:313-317`). If later agents cite it directly, they should cite `truth_set.md:495-523` beside it to prevent regression.
- The notepad `learnings.md` contains stale first-pass Task 4 notes saying TODO 18 must wire `lint_feature_matrix.py` and add a PR-template Source-of-Truth section (`learnings.md:132-136`). The corrected authority is `ci_state.md:82-87` plus `issues.md:116-139`.
- `.env.example` Sora comments are a real stale source-adjacent issue (`truth_set.md:175-179`, `:515-518`), but fixing `.env.example` is outside this Task 5 review and should not be pulled into Tasks 6-7 unless later plan scope explicitly covers it.

---

## Proceed/Do-Not-Proceed Decision

Proceed. Task 6 may proceed immediately.

No Atlas routing is required before Task 6. Route follow-up only if the orchestrator wants archival cleanup of `drift_audit.md`; it is not necessary for the source hierarchy or linter design because `truth_set.md` and the notepad corrections already supersede the known false positives.
