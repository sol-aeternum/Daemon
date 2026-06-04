# Task 17 — Report Mode Visibility Evidence

## Amendment 3 Fix Summary

Report mode text output now shows malformed exceptions and missing explicitly requested files even when there are zero drift findings.

**Change:** `if all_findings:` → `if all_findings or all_malformed:`

---

## Fixture: Malformed Exception (no drift findings)

**File:** `/tmp/opencode/doc-freshness-amendments/fixture-malformed-exception.md`
```
# Example with Malformed Exception

Some content here.

<!-- DOC_FRESHNESS_EXCEPTION: migration_count expires=2027-12-31 reason= -->
```

### Report Mode (Malformed + No Drift):
```
$ python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-amendments/fixture-malformed-exception.md
MALFORMED_EXCEPTION fixture-malformed-exception.md:5: malformed exception syntax
EXIT_CODE: 0
```

**Observation:** The malformed exception is shown in report mode even though there are no drift findings. The malformed syntax (empty `reason=""`) is visible.

### Fail Mode (Malformed → Non-Zero Exit):
```
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-amendments/fixture-malformed-exception.md
MALFORMED_EXCEPTION fixture-malformed-exception.md:5: malformed exception syntax
EXIT_CODE: 1
```

**Observation:** Fail mode correctly returns non-zero exit code for malformed exception.

---

## Fixture: Missing Explicitly Requested File (no drift findings)

**File:** `/tmp/opencode/doc-freshness-amendments/fixture-nonexistent.md` (does not exist on disk)

### Report Mode (Missing File + No Drift):
```
$ python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-amendments/fixture-nonexistent.md
MALFORMED_EXCEPTION /tmp/opencode/doc-freshness-amendments/fixture-nonexistent.md:0: source file not found: /tmp/opencode/doc-freshness-amendments/fixture-nonexistent.md
EXIT_CODE: 0
```

**Observation:** The missing file is shown in report mode even though there are no drift findings. Report mode exits 0.

### Fail Mode (Missing File → Non-Zero Exit):
```
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-amendments/fixture-nonexistent.md
MALFORMED_EXCEPTION /tmp/opencode/doc-freshness-amendments/fixture-nonexistent.md:0: source file not found: /tmp/opencode/doc-freshness-amendments/fixture-nonexistent.md
EXIT_CODE: 1
```

**Observation:** Fail mode correctly returns non-zero exit code for missing file.

---

## Fixture: Clean File (no issues)

**File:** `/tmp/opencode/doc-freshness-amendments/fixture-no-issues.md`
```
# Example with Missing Explicit File

Some content here.

The linter should report that this file does not exist.
```

### Report Mode (Clean):
```
$ python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-amendments/fixture-no-issues.md
No drift detected.
EXIT_CODE: 0
```

**Observation:** Clean file without malformed exceptions or missing files correctly prints "No drift detected."

---

## Summary: Report Mode Visibility

| Fixture | Drift Findings | Malformed | Missing File | Report Output | Report Exit | Fail Exit |
|---------|---------------|-----------|--------------|---------------|-------------|-----------|
| malformed-exception | ❌ | ✅ (empty reason) | ❌ | MALFORMED_EXCEPTION... | 0 | 1 |
| nonexistent | ❌ | ❌ | ✅ | MALFORMED_EXCEPTION... | 0 | 1 |
| no-issues | ❌ | ❌ | ❌ | No drift detected. | 0 | 0 |

**Conclusion:** Malformed exceptions and missing files are now visible in report mode even when there are zero drift findings. Fail mode correctly returns non-zero for both malformed exceptions and missing files.
