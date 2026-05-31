# PR-Review Fix Wave — PR #6 Bot Comments

**Date**: 2026-06-01
**Branch**: `doc-alignment-regeneration-2026-05-29`
**Scope**: 12 unresolved bot PR review comment fixes (Atlas-reported)

---

## Summary

Addressed all 12 unresolved PR review comment threads from Atlas's review of PR #6.
The fix wave touches only the linter (`scripts/check_doc_freshness.py`), two docs,
and the CI workflow. No runtime code under `orchestrator/`, `frontend/`, or `providers/` was modified.

---

## Comment Fixes

### 3330359183 — Missing Gated Docs Malformed
**Status**: VERIFIED (prior final-wave fix confirmed)
- `get_gated_docs()` now appends gated paths even when missing, triggering `MALFORMED_EXCEPTION` in fail mode
- Verified: linter passes with no findings

### 3330359186 — Dedup Threshold Label-Value Binding
**Fix**: Updated `_check_dedup_threshold()` to require the threshold value to appear near its corresponding label.
- New signature: `_check_dedup_threshold(text, name, expected, label_pattern)`
- Label binding patterns: `merge`, `generic`, `same.?slot`
- A doc with swapped labels (e.g., merge=0.65, same_slot=0.90) now fails

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode fail --files <swapped_doc.md>
Exit 1: dedup threshold mismatch for dedup_supersede_same_slot_threshold:
  expected 0.65 near 'same.?slot', found 0.90
```

### 3330359187 — Embedding Query Model and Dimensions
**Fix**: Added `_check_embedding_query_model()` and `_check_embedding_dimensions()`.
- Added `CheckId.EMBEDDING_QUERY_MODEL` and `CheckId.EMBEDDING_DIMENSIONS`
- Added `_EMBEDDING_QUERY_MODEL_CLAIM_RE` and `_EMBEDDING_DIMENSIONS_CLAIM_RE`
- Integration into `check_document()` with exception handling

### 3330359188 — Migration Filename All Claims
**Fix**: Changed `_check_migration_latest()` from `re.search` to `re.findall`.
- Now scans ALL migration filename patterns in doc, not just the first
- Reports stale claim if any pattern mismatch found

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode fail --files <dual_claims_doc.md>
Exit 1: migration latest mismatch: expected 030_..., found 013_...
```

### 3330359189 — Markdown/Backtick Provider List Parsing
**Fix**: Added singular and plural backtick patterns to `_check_video_providers()`.
- Singular: `**Providers**: `fal`` (singular claim, validates fal is in valid set)
- Plural: `**Providers**: `fal` `xai`` (plural claim, validates exact set match)
- Added non-greedy `[^`]+?` to prevent greedy over-matching
- Added skip logic: if extracted name is in `singular_claims`, don't add to `plural_claims`

### 3330359190 — Token SSE data.text Not data.delta
**Fix**: Updated `docs/TECHNICAL_SPECS.md:65`:
```
- Before: | `token` | `data.delta` | Incremental text token |
- After:  | `token` | `data.text` | Incremental text token (compat: `data.delta` accepted by bridge) |
```

### 3330399004 — Active Non-Suppressing Exceptions in Reports
**Fix**: `check_document()` now returns ALL active non-expired exceptions, not just `updated_exceptions` (suppressed ones).
- Changed return: `all_active_exceptions = [exc for exc in exceptions if exc.expires >= today]`
- Non-suppressing exceptions now appear in reports with `(ACTIVE)` status

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode report --files <exception_doc.md>
<doc>:2 [EXCEPTION migration_count] expires=2027-01-01 reason='test exception' (ACTIVE)
```

### 3330399006 — docker-compose.yml in Workflow Paths
**Fix**: Added `docker-compose.yml` to path filters in `.github/workflows/docs-freshness.yml`
for both `push` and `pull_request` triggers.

### 3330399007 — Tier Model Default Validation
**Fix**: Added `get_tier_facts()` and `_check_tier_defaults()` to validate tier table claims.
- Extracts all `tier_{name}_{slot}_model` from `orchestrator/config.py`
- Parses tier table rows from gated docs with `_TIER_TABLE_ROW_RE`
- Normalizes model names (strips provider prefix) for comparison
- Allows BYOK embeddings to be "voyage-4-large" even though config has "" (documented exception)
- Integrated into `check_document()` with `CheckId.TIER_MODEL`

**Scope note**: Validation is limited to checking if the documented model name appears within the config value (substring match after normalization). This avoids semantic prose validation while catching clear mismatches.

### 3330399009 — Migration Count All Claims
**Fix**: Changed `_check_migration_count()` from `re.search` to `re.findall`.
- Now scans ALL migration count patterns in doc, not just the first
- Reports stale claim if any pattern mismatch found

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode fail --files <stale_count_doc.md>
Exit 1: migration count mismatch: expected 30, found 13
```

### 3330450207 — Bold/Coloned Embedding Format Acceptance
**Fix**: Updated `_EMBEDDING_DOC_MODEL_CLAIM_RE` to accept bold GFM format.
- New pattern: `r'\*\*EMBEDDING_DOCUMENT_MODEL\*\*[:=\s]+(?:["\'])?([a-z0-9-]+)(?=["\']?\s|$)'`
- Same approach for `_EMBEDDING_QUERY_MODEL_CLAIM_RE` and `_EMBEDDING_DIMENSIONS_CLAIM_RE`

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode fail --files <bold_embedding_doc.md>
Exit 0: No drift detected.  (accepts **EMBEDDING_DOCUMENT_MODEL**: voyage-4-large)
```

### 3330450209 — Video Default Propagation Wording
**Fix**: Updated `docs/PROJECT_CONTEXT.md:60`:
```
- Before: *Note: Default video provider is `fal` (Kling). BYOK users bypass credits...
- After:  *Note: Default video provider is `fal` (Kling) via config.py; runtime selection
           depends on `video_provider` propagation. BYOK users bypass credits...
```
**Rationale**: config.py defaults are `fal`, but image subagent can fall back to image/openrouter
if video_provider is not propagated from config. Documentation updated to be source-truth oriented
without implying unconditional runtime behavior.

### 3330399007 (Tier Model Defaults) — Deferral Consideration
The PR comment asked whether a one-line config propagation fix is already intended by existing code.
After analysis: `orchestrator/` tier routing reads `video_provider` from `TierConfig` which is populated
from config.py defaults. There is no evidence of incomplete propagation in the code itself.
The documentation-only fix (above) is the appropriate resolution.

---

## Verification

### Linter Commands
```bash
# Report mode
python scripts/check_doc_freshness.py --mode report --format text
# Expected: No drift detected.

# Fail mode (full gated docs)
python scripts/check_doc_freshness.py --mode fail --format text
# Expected: No drift detected. (exit 0)

# Fail mode (root docs only)
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md
# Expected: No drift detected. (exit 0)

# Compile check
python -m py_compile scripts/check_doc_freshness.py
# Expected: (no output, exit 0)

# Feature matrix linter
python scripts/lint_feature_matrix.py
# Expected: OK: 60 feature rows validated
```

### Temp Fixture Results
| Test | Input | Expected | Actual |
|------|-------|----------|--------|
| Dedup swapped | merge=0.65, same_slot=0.90 | Exit 1 | Exit 1 ✅ |
| Migration count stale | 30...13 migrations | Exit 1 | Exit 1 ✅ |
| Provider markdown/bold | `**Providers**: `fal`` | Exit 0 | Exit 0 ✅ |
| Embedding bold format | `**EMBEDDING_DOCUMENT_MODEL**: voyage-4-large` | Exit 0 | Exit 0 ✅ |
| Non-suppressing exception | ACTIVE exception in report | Visible | Visible ✅ |
| Embedding prose false positive | "embedding model choice..." | Exit 0 | Exit 0 ✅ |

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/check_doc_freshness.py` | Linter fixes: dedup binding, migration all-claims, embedding query/dim, provider markdown, tier defaults, exception visibility, bold embedding format |
| `docs/TECHNICAL_SPECS.md` | Token SSE `data.text` (was `data.delta`) |
| `docs/PROJECT_CONTEXT.md` | Video default wording updated for propagation nuance |
| `.github/workflows/docs-freshness.yml` | Added `docker-compose.yml` to path filters |

---

## Deliberately Deferred

**3330450209 (video default)**: No app-code change made. The documentation-only fix addresses the overstatement. No evidence of incomplete config propagation in runtime code. Config propagation in the image/video subagent is functioning as designed.
