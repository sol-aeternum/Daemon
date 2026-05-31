# Task 7: Exception Test Evidence

## Test Fixtures

### Valid Exception Fixture
**File**: `/tmp/doc_freshness_test/valid_exception.md`

```markdown
# Test file with VALID exception

This doc mentions 13 migrations (wrong).

<!-- DOC_FRESHNESS_EXCEPTION: migration_count expires=2027-12-31 reason="Intentionally out of sync for testing" -->

The project has 13 migrations.
```

### Expired Exception Fixture
**File**: `/tmp/doc_freshness_test/expired_exception.md`

```markdown
# Test file with EXPIRED exception

This doc mentions 13 migrations (wrong).

<!-- DOC_FRESHNESS_EXCEPTION: migration_count expires=2020-01-01 reason="Expired exception" -->

The project has 13 migrations.
```

### Malformed Exception Fixture
**File**: `/tmp/doc_freshness_test/malformed_exception.md`

```markdown
# Test file with MALFORMED exception (missing reason)

This doc mentions 13 migrations.

<!-- DOC_FRESHNESS_EXCEPTION: migration_count expires=2027-12-31 -->

The project has 13 migrations.
```

---

## Test Results

### Test 1: Valid Exception - Report Mode
**Command**: `python scripts/check_doc_freshness.py --mode report --files /tmp/doc_freshness_test/valid_exception.md --format json`

**Result**: Exit code 0. Finding suppressed, exception visible.

```json
{
  "findings": [],
  "exceptions": [
    {
      "doc": "/tmp/doc_freshness_test/valid_exception.md",
      "line": 5,
      "check_id": "migration_count",
      "expires": "2027-12-31",
      "reason": "Intentionally out of sync for testing",
      "suppressed_finding": true
    }
  ],
  "malformed_exceptions": [],
  "summary": {
    "total_findings": 0,
    "total_exceptions": 1,
    "total_malformed": 0
  }
}
```

---

### Test 2: Valid Exception - Fail Mode
**Command**: `python scripts/check_doc_freshness.py --mode fail --files /tmp/doc_freshness_test/valid_exception.md`

**Result**: Exit code 0. Exception suppresses the finding.

---

### Test 3: Expired Exception - Report Mode
**Command**: `python scripts/check_doc_freshness.py --mode report --files /tmp/doc_freshness_test/expired_exception.md --format json`

**Result**: Exit code 0 (report mode). Expired exception does NOT suppress drift.

```json
{
  "findings": [
    {
      "kind": "expired_exception",
      "check_id": "migration_count",
      "message": "expired DOC_FRESHNESS_EXCEPTION for 'migration_count'"
    },
    {
      "kind": "mismatch",
      "check_id": "migration_count",
      "message": "migration count mismatch: expected 30, found 13"
    }
  ],
  "exceptions": [],
  "malformed_exceptions": [],
  "summary": {
    "total_findings": 2,
    "total_exceptions": 0,
    "total_malformed": 0
  }
}
```

**Key behavior**: Expired exception produces `expired_exception` finding AND the underlying `migration_count` drift finding.

---

### Test 4: Expired Exception - Fail Mode
**Command**: `python scripts/check_doc_freshness.py --mode fail --files /tmp/doc_freshness_test/expired_exception.md`

**Result**: Exit code 1 (non-zero), expired exception + drift produce findings.

---

### Test 5: Malformed Exception - Report Mode
**Command**: `python scripts/check_doc_freshness.py --mode report --files /tmp/doc_freshness_test/malformed_exception.md --format json`

**Result**: Exit code 0 (report mode), malformed exception detected.

```json
{
  "findings": [
    {
      "kind": "mismatch",
      "check_id": "migration_count",
      "message": "migration count mismatch: expected 30, found 13"
    }
  ],
  "exceptions": [],
  "malformed_exceptions": [
    {
      "doc": "malformed_exception.md",
      "line": 5,
      "message": "malformed exception syntax"
    }
  ],
  "summary": {
    "total_findings": 1,
    "total_exceptions": 0,
    "total_malformed": 1
  }
}
```

---

### Test 6: Malformed Exception - Fail Mode
**Command**: `python scripts/check_doc_freshness.py --mode fail --files /tmp/doc_freshness_test/malformed_exception.md`

**Result**: Exit code 1 (non-zero) because malformed exceptions cause fail mode to exit non-zero.

---

## Summary

| Scenario | Mode | Finding Suppressed? | Exit | Behavior |
|---------|------|---------------------|------|----------|
| Valid exception | report | Yes | 0 | Finding suppressed, exception visible |
| Valid exception | fail | Yes | 0 | Finding suppressed, exception visible |
| Expired exception | report | **No** | 0 | Both expiry and drift reported |
| Expired exception | fail | **No** | 1 | Both expiry and drift reported |
| Malformed exception | report | No | 0 | Malformed detected, drift reported |
| Malformed exception | fail | No | 1 | Malformed causes non-zero exit |
