# Task 17 Amendment Re-review — Oracle Verdict

**Date:** 2026-05-31  
**Reviewer:** Oracle focused re-review  
**Scope:** Decide whether Task 18 may wire the blocking doc-freshness gate after Task 17 amendments.  
**Temporary fixtures:** `/tmp/opencode/doc-freshness-rereview/` only.

## Verdict

**VERDICT: APPROVE**

Task 18 may wire the blocking fail-mode doc-freshness gate.

The three blocking amendments from the original rejection are resolved enough for Task 18. The linter remains intentionally narrow, but its declared scope is now aligned with the active checks, provider validation is source-derived from `VALID_VIDEO_PROVIDERS = {"xai", "fal"}`, and report-mode text output no longer hides malformed exceptions or missing explicit files when there are no drift findings.

## Required Inputs Reviewed

- Original rejection: `tests/benchmark_results/doc-alignment-regeneration/oracle_gate_review.md:12-15` rejected Task 18, with required amendments at `:148-153`.
- Stop/re-review requirement: `tests/benchmark_results/doc-alignment-regeneration/evidence/task-17-approval.md:23-32` requires source-derived provider validation, honest scope, report-mode malformed/missing visibility, refreshed evidence, and an explicit approval phrase before Task 18.
- Amendment record: `tests/benchmark_results/doc-alignment-regeneration/evidence/task-17-amendments.md:7-138` records the provider, scope, and report-mode changes.
- Provider evidence: `tests/benchmark_results/doc-alignment-regeneration/evidence/task-17-provider-validation.md:1-23` records the source set and singular/plural contract; `:157-169` summarizes the fixture outcomes.
- Report-mode evidence: `tests/benchmark_results/doc-alignment-regeneration/evidence/task-17-report-mode.md:3-8` records the visibility fix; `:22-60` records malformed and missing-file report/fail behavior.
- Implementation: `scripts/check_doc_freshness.py` was independently inspected, especially `:5-10`, `:75-99`, `:311-383`, `:469`, and `:615-625`.
- Source provider set: `orchestrator/routes/video_credits.py:157-158` defines `VALID_VIDEO_PROVIDERS = {"xai", "fal"}`.

## Blocking Amendment Review

### 1. Source-derived video-provider validation — satisfied

`get_provider_facts()` extracts `VALID_VIDEO_PROVIDERS` from `orchestrator/routes/video_credits.py` using `_VIDEO_PROVIDERS_RE` (`scripts/check_doc_freshness.py:75-88`) and returns the sorted provider set (`:97-99`). The source currently defines `VALID_VIDEO_PROVIDERS = {"xai", "fal"}` at `orchestrator/routes/video_credits.py:157-158`; an import-level probe returned `['fal', 'xai']`.

`_check_video_providers(doc_content, valid_providers)` now receives `frozenset(facts["providers"]["video_providers"])` at `scripts/check_doc_freshness.py:469`. The check compares structured claims against that source-derived set (`:311-383`) and the script contains no `_KNOWN_REMOVED_PROVIDERS` or hardcoded `sora` denylist in the current provider logic.

The required singular/plural distinction is implemented and independently verified:

- Singular `provider: xai` validates as a single known provider and exits 0.
- Singular `provider: kling` fails as unsupported.
- Plural/list `video providers: xai` fails because it omits `fal`.
- Plural/list `video providers: xai, kling` fails because it includes unsupported `kling` and omits `fal`.
- Set literal `VALID_VIDEO_PROVIDERS = {"xai", "kling"}` fails for unsupported `kling` and missing `fal`.

### 2. Declared linter scope aligned with active checks — satisfied

The script docstring now declares only the enforced checks: migration count/latest, `embedding_document_model`, dedup thresholds, and `video_providers` (`scripts/check_doc_freshness.py:5-10`). The previous rejected categories (`embedding_query_model`, `embedding_dimensions`, route names, feature states, env-var names) are not advertised as active linter checks in the implementation docstring.

Some extraction helpers for query model, dimensions, routes, feature states, and env vars remain in `extract_all_facts()` (`scripts/check_doc_freshness.py:46-51`, `:103-176`), but the gate no longer claims that those facts are enforced. That remaining limitation is acceptable for Task 18 because the blocking gate will be narrow but honest, and the active checks are the high-confidence structured checks recorded in the amended script scope.

### 3. Report-mode malformed/missing visibility — satisfied

Missing explicit files are accumulated in `all_malformed` (`scripts/check_doc_freshness.py:585-588`), malformed exception comments are also accumulated (`:593-595`), and `format_text()` prints malformed entries (`:485-494`). The report-mode text branch now prints formatted output when `all_findings or all_malformed` (`:615-618`), so it no longer prints only `No drift detected.` for a clean document that has malformed exception syntax or for a missing explicitly requested file. Fail mode returns non-zero for malformed entries at `:620-625`.

Independent fixtures confirmed this behavior: report mode prints `MALFORMED_EXCEPTION ...` with exit 0, while fail mode prints the same diagnostic with exit 1.

## Commands Run

```text
$ python scripts/check_doc_freshness.py --mode report
No drift detected.
EXIT_CODE=0

$ python scripts/check_doc_freshness.py --mode fail
No drift detected.
EXIT_CODE=0

$ python -m py_compile scripts/check_doc_freshness.py
(no output)
EXIT_CODE=0

$ python -c 'from scripts.check_doc_freshness import get_provider_facts, repo_root; print(get_provider_facts(repo_root())["video_providers"])'
['fal', 'xai']
EXIT_CODE=0
```

## Focused Fixture Verification

Fixtures were created only under `/tmp/opencode/doc-freshness-rereview/`.

```text
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-rereview/provider-list-invalid.md
/tmp/opencode/doc-freshness-rereview/provider-list-invalid.md:1 [CheckId.VIDEO_PROVIDERS] expected='providers in fal, xai' observed='claimed kling, xai (unsupported: kling; missing: fal)'  video provider set mismatch
EXIT_CODE=1

$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-rereview/provider-list-omission.md
/tmp/opencode/doc-freshness-rereview/provider-list-omission.md:1 [CheckId.VIDEO_PROVIDERS] expected='providers in fal, xai' observed='claimed xai (missing: fal)'  video provider set mismatch
EXIT_CODE=1

$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-rereview/provider-set-invalid.md
/tmp/opencode/doc-freshness-rereview/provider-set-invalid.md:1 [CheckId.VIDEO_PROVIDERS] expected='providers in fal, xai' observed='claimed kling, xai (unsupported: kling; missing: fal)'  video provider set mismatch
EXIT_CODE=1

$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-rereview/provider-singular-valid.md
No drift detected.
EXIT_CODE=0

$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-rereview/provider-singular-invalid.md
/tmp/opencode/doc-freshness-rereview/provider-singular-invalid.md:1 [CheckId.VIDEO_PROVIDERS] expected='valid providers: fal, xai' observed='invalid: kling'  video provider 'kling' is not in the valid provider set
EXIT_CODE=1

$ python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-rereview/malformed-exception.md
MALFORMED_EXCEPTION malformed-exception.md:5: malformed exception syntax
EXIT_CODE=0

$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-rereview/malformed-exception.md
MALFORMED_EXCEPTION malformed-exception.md:5: malformed exception syntax
EXIT_CODE=1

$ python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-rereview/missing.md
MALFORMED_EXCEPTION /tmp/opencode/doc-freshness-rereview/missing.md:0: source file not found: /tmp/opencode/doc-freshness-rereview/missing.md
EXIT_CODE=0

$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-rereview/missing.md
MALFORMED_EXCEPTION /tmp/opencode/doc-freshness-rereview/missing.md:0: source file not found: /tmp/opencode/doc-freshness-rereview/missing.md
EXIT_CODE=1
```

## Remaining Limitations Accepted for Task 18

- The gate does not semantically validate all volatile documentation facts. This is acceptable because `scripts/check_doc_freshness.py:5-10` now honestly declares the enforced subset.
- The provider parser is deliberately structured-pattern based rather than prose-semantic. This is acceptable for a blocking gate because the matching forms are high-confidence (`provider:`, `video provider:`, `providers:`, `video providers:`, and `VALID_VIDEO_PROVIDERS = {...}`).
- The finding output renders enum values as `CheckId.VIDEO_PROVIDERS`; this is cosmetic and does not affect fail-mode correctness or the Task 17 blocker criteria.

## Final Decision

**APPROVE.** Task 18 may wire the blocking fail-mode doc-freshness gate.
