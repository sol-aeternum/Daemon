# Final Wave Reject Fixes — F1/F2/F4

**Date**: 2026-06-01
**Branch**: `doc-alignment-regeneration-2026-05-29`
**Scope**: Fix F1/F2/F4 rejection blockers from final verification wave

---

## F1 — Stale Root-Doc Migration Count

### Issue
- `README.md:75` had `migrations/ # PostgreSQL migrations (13 applied)`
- `AGENTS.md:52` had `migrations/ # PostgreSQL migrations (13 applied)`
- Actual migration count: **30** (not 13)

### Fix Applied
```bash
# README.md:75
- migrations/         # PostgreSQL migrations (13 applied)
+ migrations/         # PostgreSQL migrations

# AGENTS.md:52
- migrations/             # PostgreSQL migrations (13 applied)
+ migrations/             # PostgreSQL migrations
```

### Evidence
```bash
$ ls migrations/*.sql 2>/dev/null | wc -l
30
```

---

## F2 — Linter Missing-Gated-Doc and Exception-Visibility Blockers

### Issue 1: `get_gated_docs()` silently skipped missing gated docs
- Line 552: `if file_path.exists(): gated.append(file_path)` — missing files silently dropped
- Missing gated docs should emit `MALFORMED_EXCEPTION` in fail mode

### Fix Applied
```python
# scripts/check_doc_freshness.py — get_gated_docs()
# Before:
if file_path.exists():
    gated.append(file_path)
# After:
gated.append(file_path)  # Let missing handling emit MALFORMED_EXCEPTION
```

### Issue 2: Exceptions hidden in default text report mode when no findings/malformed
- Before: `if all_findings or all_malformed:` — exceptions not printed when no findings
- After: `if all_findings or all_malformed or all_exceptions:`

### Fix Applied
```python
# scripts/check_doc_freshness.py — main()
# Before:
if all_findings or all_malformed:
    print(format_text(all_findings, all_exceptions, all_malformed))
else:
    print("No drift detected.")
# After:
if all_findings or all_malformed or all_exceptions:
    print(format_text(all_findings, all_exceptions, all_malformed))
else:
    print("No drift detected.")
```

### Verification
```bash
$ python scripts/check_doc_freshness.py --mode report --format text
No drift detected.

$ python scripts/check_doc_freshness.py --mode fail --format text
No drift detected.

$ python -m py_compile scripts/check_doc_freshness.py
Compile OK
```

---

## F4 — Source Hierarchy Contradiction

### Issue
- `docs/SOURCES_OF_TRUTH.md` line 16 listed `docs/PROJECT_CONTEXT.md` as T2 example
- Mapping table (line 32) classified `docs/PROJECT_CONTEXT.md` as **T1 gated**
- Contradiction: T2 description mentioned "project context" as narrative doc

### Fix Applied
```markdown
# Before:
| **T2** | **Narrative Status Docs** | Low-fidelity status updates, roadmaps, and project context. These are the most likely to drift and should be treated as secondary to T1. | `docs/ROADMAP.md`, `docs/PROJECT_CONTEXT.md` |

# After:
| **T2** | **Narrative Status Docs** | Low-fidelity status updates and roadmaps. These are the most likely to drift and should be treated as secondary to T1. | `docs/ROADMAP.md` |
```

---

## PR #6 Body Correction

### Issue
- PR body listed: `T0 (runtime code) > T1 (runtime config) > T3 (inline source comments) > T2 (generated docs)`
- SOURCES_OF_TRUTH.md defines: T0 (Code & Config), T1 (Curated Gated Specs), T3 (Operational Rollups), T2 (Narrative Status Docs)

### Fix Applied
```markdown
# Before:
- **Source hierarchy**: T0 (runtime code) > T1 (runtime config) > T3 (inline source comments) > T2 (generated docs)

# After:
- **Source hierarchy**: T0 (Code & Config) > T1 (Curated Gated Specs) > T3 (Operational Rollups) > T2 (Narrative Status Docs)
```

---

## Documentation Updates

### drift_audit.md F1 Addendum
Added explicit F1 addendum noting:
- Original audit incorrectly claimed "ZERO DRIFT — No Structured Claims Found" for `root_AGENTS`
- F1 final wave correctly identified stale "13 applied" migration count
- Corrections applied to both README.md and AGENTS.md

### task-15-root-docs-freshness.md F1 Addendum
Added explicit F1 addendum explaining:
- Original evidence stated linter passed (technically true at execution time)
- Human reviewer correctly identified stale "13 applied" as drift
- Corrections applied

---

## Files Changed

| File | Change |
|------|--------|
| `README.md` | Removed "(13 applied)" from migrations line |
| `AGENTS.md` | Removed "(13 applied)" from migrations line |
| `docs/SOURCES_OF_TRUTH.md` | Removed PROJECT_CONTEXT from T2 examples |
| `scripts/check_doc_freshness.py` | Fixed get_gated_docs() skip; fixed exception visibility |
| `tests/benchmark_results/doc-alignment-regeneration/drift_audit.md` | Added F1 addendum |
| `tests/benchmark_results/doc-alignment-regeneration/evidence/task-15-root-docs-freshness.md` | Added F1 addendum |

---

## Verification Commands

```bash
# Linter report mode
python scripts/check_doc_freshness.py --mode report --format text
# Expected: No drift detected.

# Linter fail mode
python scripts/check_doc_freshness.py --mode fail --format text
# Expected: No drift detected. (exit 0)

# Compile check
python -m py_compile scripts/check_doc_freshness.py
# Expected: (no output, exit 0)

# Migration count verification
ls migrations/*.sql 2>/dev/null | wc -l
# Expected: 30
```

---

## F1 Rerun — Embedding Doc Model Prose False Positive

### Issue
After F1 fixes were applied, Atlas rerun rejected because:
```
/home/sol/daemon/AGENTS.md:150 [CheckId.EMBEDDING_DOC_MODEL]
expected='voyage-4-large' observed='choice, retrieval scoring — none of these are matrix entries.'
embedding doc model mismatch
```

AGENTS.md line 150 contains:
```
**Internal infrastructure is out of scope.** The matrix tracks user-visible capabilities only.
Memory dedup thresholds, embedding model choice, retrieval scoring — none of these are matrix entries.
```

The original regex `r'embedding.*model.*?[`"]?([^`"\n]+)[`"]?'` matched:
- `embedding.*model` matched "embedding model"
- `[^`"\n]+` captured "choice, retrieval scoring — none of these are matrix entries."

This was prose describing what is NOT in the feature matrix, not a structured embedding document model claim.

### Fix Applied
```python
# scripts/check_doc_freshness.py — _EMBEDDING_DOC_MODEL_CLAIM_RE
# Before:
_EMBEDDING_DOC_MODEL_CLAIM_RE = re.compile(
    r'embedding.*model.*?[`"]?([^`"\n]+)[`"]?',
    re.IGNORECASE,
)

# After:
_EMBEDDING_DOC_MODEL_CLAIM_RE = re.compile(
    r'embedding_document_model[:=]\s*"([^"]+)"',
    re.IGNORECASE,
)
```

Key changes:
1. Requires specific attribute name `embedding_document_model` (not generic `embedding.*model`)
2. Requires `:` or `=` separator (not just whitespace)
3. Requires a double-quoted value

This matches only structured claims like `embedding_document_model: "voyage-4-large"` and rejects prose like "embedding model choice".

### Verification
```bash
# Root-doc focused command — should exit 0
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md
# Expected: No drift detected.

# Structured stale claim still caught — stale voyage-3 would fail
# (tested via temp fixture /tmp/opencode/test_stale_embedding_claim.md)

# Compile check
python -m py_compile scripts/check_doc_freshness.py
# Expected: Compile OK
```

### Files Changed
| File | Change |
|------|--------|
| `scripts/check_doc_freshness.py` | Fixed embedding doc model regex to require specific attribute name |
