# Task 6 Precedence Evidence

This file provides evidence that the documentation hierarchy, precedence, and specific roles are correctly defined in `docs/SOURCES_OF_TRUTH.md`.

## 1. Precedence Chain
The plan requires the exact precedence chain: `T0 > T1 > T3 > T2`.

### Verification Command
```bash
grep "Precedence: T0 > T1 > T3 > T2" docs/SOURCES_OF_TRUTH.md
```

### Result
```
**Precedence: T0 > T1 > T3 > T2**
```

## 2. CURRENT_ISSUES.md Role
The plan requires `docs/CURRENT_ISSUES.md` to be classified as a T3 operational rollup with `TRIAGE.md` as raw-log input.

### Verification Command
```bash
grep -A 3 "The Role of CURRENT_ISSUES.md" docs/SOURCES_OF_TRUTH.md
```

### Result
```
### The Role of CURRENT_ISSUES.md
`docs/CURRENT_ISSUES.md` is classified as a **T3 Operational Rollup**. It is the curated interface for understanding active system anomalies. Its primary input is the raw `TRIAGE.md` log, but it is not a narrative document (T2) or a static spec (T1).
```

## 3. Exception Syntax
The plan requires the exact exception syntax: `<!-- DOC_FRESHNESS_EXCEPTION: <check_id> expires=YYYY-MM-DD reason="..." -->`.

### Verification Command
```bash
grep "DOC_FRESHNESS_EXCEPTION" docs/SOURCES_OF_TRUTH.md
```

### Result
```
`<!-- DOC_FRESHNESS_EXCEPTION: <check_id> expires=YYYY-MM-DD reason="..." -->`
```

## 4. Linter Scope
The plan requires clarification that the linter gates structured facts only and does not semantically validate all prose.

### Verification Command
```bash
grep -A 2 "## 4. Drift Gating and Exceptions" docs/SOURCES_OF_TRUTH.md
```

### Result
```
## 4. Drift Gating and Exceptions

The `scripts/check_doc_freshness.py` linter enforces alignment between T0/T1 sources and gated documentation.

### Linter Scope
The linter gates **high-confidence structured facts only** (e.g., version numbers, counts, specific config values) and does **NOT** semantically validate prose or general narrative claims.
```

## 5. Missing Source Behavior
The plan requires documenting missing source behavior: error in fail mode, warning in report mode.

### Verification Command
```bash
grep -A 3 "### Missing or Renamed Sources" docs/SOURCES_OF_TRUTH.md
```

### Result
```
### Missing or Renamed Sources
- Fail Mode: If a source file or line cited in a doc is missing or renamed, the linter will exit with an error.
- Report Mode: The linter will issue a warning but exit with code 0.
```

All requirements for precedence, roles, and linter behavior are correctly documented.
