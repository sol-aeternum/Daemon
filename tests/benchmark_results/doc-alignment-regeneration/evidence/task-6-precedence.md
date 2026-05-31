# Task 6 Precedence Evidence

The precedence and role of `CURRENT_ISSUES.md` are explicitly defined in `docs/SOURCES_OF_TRUTH.md`.

## Precedence Definition
```markdown
**Precedence: T0 > T1 > T3 > T2**
```

## CURRENT_ISSUES.md Role
```markdown
### The Role of CURRENT_ISSUES.md
`docs/CURRENT_ISSUES.md` is classified as a **T3 Operational Rollup**. It is the curated interface for understanding active system anomalies. Its primary input is the raw `TRIAGE.md` log, but it is not a narrative document (T2) or a static spec (T1).
```

## Verification Command
```bash
grep "Precedence: T0 > T1 > T3 > T2" docs/SOURCES_OF_TRUTH.md
grep -A 3 "The Role of CURRENT_ISSUES.md" docs/SOURCES_OF_TRUTH.md
```

## Result
Precedence and `CURRENT_ISSUES.md` role are unambiguous and match the Oracle design review requirements.
