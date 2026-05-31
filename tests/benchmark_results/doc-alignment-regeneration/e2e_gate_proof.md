# Task 19 — E2E Gate Proof

**Date:** 2026-05-31
**Task:** Prove drift gate through consumer path
**Branch:** `doc-alignment-regeneration-2026-05-29`

---

## Consumer Command Definition

The consumer command is derived from Task 18 evidence which proved:
- `pre-commit run doc-freshness --all-files` fails with exit 127 (pre-commit not installed)
- Both `.pre-commit-config.yaml` and `.github/workflows/docs-freshness.yml` use identical entry: `python scripts/check_doc_freshness.py --mode fail`

**Consumer command used for all proofs:**
```bash
python scripts/check_doc_freshness.py --mode fail
```

**Rationale:** Task 18 established command parity and documented pre-commit unavailability as a tooling issue. This task proves behavior through the CI-equivalent command.

---

## Scenario 1: Aligned Tree Passes

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail
```

**Raw output:**
```
No drift detected.
```

**Exit code:** `0`

**Result:** PASS — aligned tree exits 0 with no findings.

---

## Scenario 2: Injected Stale Fact Fails

**Method:** Temporarily modified `docs/TECHNICAL_SPECS.md` line 91 to inject stale migration_latest fact.

**Original (correct):**
```
Latest migration: `030_add_advisor_traces.sql`.
```

**Injected (stale):**
```
Latest migration: `029_skill_consolidation_nudge.sql`.
```

**Backup location:** `/tmp/opencode/doc-freshness-task19/TECHNICAL_SPECS.md.backup` (outside repo)

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail
```

**Raw output:**
```
/home/sol/daemon/docs/TECHNICAL_SPECS.md:91 [CheckId.MIGRATION_LATEST] expected='030_add_advisor_traces.sql' observed='029_skill_consolidation_nudge.sql'  latest migration mismatch: expected 030_add_advisor_traces.sql, found 029_skill_consolidation_nudge.sql
```

**Exit code:** `1`

**Result:** PASS — consumer command exits non-zero and names the injected contradiction with file:line and check id.

**Restoration:** File restored from backup before task completion. Verified with `diff` (exit 0 = no difference).

---

## Scenario 3: Aligned Tree After Restoration

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail
```

**Raw output:**
```
No drift detected.
```

**Exit code:** `0`

**Result:** PASS — aligned tree returns to passing state after restoration.

---

## Scenario 4: Exception Behaviors

All exception tests were run using `--files` flag with temporary fixtures in `/tmp/opencode/doc-freshness-task19/fixtures/` (outside repo). Fixtures were deleted after testing.

### 4a: Valid Exception (Future Expiry)

**Fixture:** `/tmp/opencode/doc-freshness-task19/fixtures/valid_exception.md`
```markdown
# Valid Exception Test

<!-- DOC_FRESHNESS_EXCEPTION: migration_count expires=2027-12-31 reason="testing valid exception" -->

Migration count in this doc is intentionally wrong: 999 migrations.
```

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-task19/fixtures/valid_exception.md
```

**Raw output:**
```
No drift detected.
```

**Exit code:** `0`

**Result:** PASS — valid exception with future expiry date suppresses the finding and exits 0.

### 4b: Expired Exception

**Fixture:** `/tmp/opencode/doc-freshness-task19/fixtures/expired_exception.md`
```markdown
# Expired Exception Test

<!-- DOC_FRESHNESS_EXCEPTION: migration_count expires=2020-01-01 reason="testing expired exception" -->

Migration count in this doc is intentionally wrong: 999 migrations.
```

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-task19/fixtures/expired_exception.md
```

**Raw output:**
```
/tmp/opencode/doc-freshness-task19/fixtures/expired_exception.md:5 [migration_count] expected='expires >= 2026-05-31' observed='expired on 2020-01-01'  expired DOC_FRESHNESS_EXCEPTION for 'migration_count'
/tmp/opencode/doc-freshness-task19/fixtures/expired_exception.md:1 [CheckId.MIGRATION_COUNT] expected='30' observed='999'  migration count mismatch: expected 30, found 999
```

**Exit code:** `1`

**Result:** PASS — expired exception is reported as `expired DOC_FRESHNESS_EXCEPTION` finding, and the underlying migration_count mismatch is also flagged.

### 4c: Malformed Exception

**Fixture:** `/tmp/opencode/doc-freshness-task19/fixtures/malformed_exception.md`
```markdown
# Malformed Exception Test

<!-- DOC_FRESHNESS_EXCEPTION: migration_count expires=not-a-date reason="testing malformed" -->

Migration count in this doc is intentionally wrong: 999 migrations.
```

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-task19/fixtures/malformed_exception.md
```

**Raw output:**
```
/tmp/opencode/doc-freshness-task19/fixtures/malformed_exception.md:1 [CheckId.MIGRATION_COUNT] expected='30' observed='999'  migration count mismatch: expected 30, found 999
MALFORMED_EXCEPTION malformed_exception.md:5: malformed exception syntax
```

**Exit code:** `1`

**Result:** PASS — malformed exception syntax is reported as `MALFORMED_EXCEPTION` finding, and the underlying migration_count mismatch is also flagged.

### 4d: Missing Source File

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail --files /tmp/nonexistent/doc.md
```

**Raw output:**
```
MALFORMED_EXCEPTION /tmp/nonexistent/doc.md:0: source file not found: /tmp/nonexistent/doc.md
```

**Exit code:** `1`

**Result:** PASS — missing source file is reported as `MALFORMED_EXCEPTION` with "source file not found" message.

### 4e: Video Provider Mismatch

**Fixture:** `/tmp/opencode/doc-freshness-task19/fixtures/video_provider_mismatch.md`
```markdown
# Video Provider Mismatch Test

video providers: xai, unknown_provider
```

**Command:**
```bash
python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-task19/fixtures/video_provider_mismatch.md
```

**Raw output:**
```
/tmp/opencode/doc-freshness-task19/fixtures/video_provider_mismatch.md:1 [CheckId.VIDEO_PROVIDERS] expected='providers in fal, xai' observed='claimed unknown, xai (unsupported: unknown; missing: fal)'  video provider set mismatch
```

**Exit code:** `1`

**Result:** PASS — video provider mismatch is detected with detailed unsupported/missing provider information.

---

## Cleanup Verification

**Command:**
```bash
ls -la /tmp/opencode/doc-freshness-task19 2>&1
```

**Result:** Directory does not exist (removed).

**Git status check:** All temporary files are in `/tmp/opencode/doc-freshness-task19/` which is outside the repo. No `*_drift_fixture_tmp*` or scratch files remain in the repository.

---

## Summary

| Scenario | Command | Exit Code | Result |
|----------|---------|-----------|--------|
| Aligned tree | `python scripts/check_doc_freshness.py --mode fail` | 0 | PASS |
| Injected stale fact | Same consumer command | 1 | PASS — names contradiction |
| After restoration | Same consumer command | 0 | PASS — back to aligned |
| Valid exception | `--files` fixture | 0 | PASS — suppressed |
| Expired exception | `--files` fixture | 1 | PASS — flagged |
| Malformed exception | `--files` fixture | 1 | PASS — flagged |
| Missing source | `--files` fixture | 1 | PASS — flagged |
| Provider mismatch | `--files` fixture | 1 | PASS — flagged |

---

## Source Facts at Test Time

- **Migrations:** count=30, latest=`030_add_advisor_traces.sql`
- **Embeddings:** document_model=`voyage-4-large`, dedup_merge=0.9, dedup_supersede_generic=0.82, dedup_supersede_same_slot=0.65
- **Video providers:** `['fal', 'xai']`

---

## Notes

- pre-commit is not installed in the host environment (exit 127). Consumer proof uses CI-equivalent command per Task 18 evidence.
- All exception fixture files were created in `/tmp/opencode/doc-freshness-task19/` outside the repo and deleted after testing.
- TECHNICAL_SPECS.md was temporarily modified, tested, and restored from backup. Verified with `diff` (exit 0).
- No permanent changes were made to any repo files.
