# Task 15 — Root Docs Freshness Evidence

**Date**: 2026-05-31
**Branch**: `doc-alignment-regeneration-2026-05-29`
**Task**: Fix residual drift in `README.md` and `QUICKSTART.md`

## Linter Command

```bash
python scripts/check_doc_freshness.py --mode fail --files README.md QUICKSTART.md
```

## Linter Output

```
No drift detected.
```

## Exit Code

```
0
```

## Notes

- `QUICKSTART.md` — Zero drift per `drift_audit.md` (no structured claims found); no edits required.
- `README.md` — Linter passes; surgical edits applied to address DRIFT-14 (subagent list) and DRIFT-15 (missing `/skills` endpoint) as flagged in `drift_audit.md`.
- The linter checks gated docs by default; when run with `--files README.md QUICKSTART.md` on ungated-reference docs, it validates the same patterns (migration count, dedup thresholds, embedding models, video providers) but finds no violations.

---

## F1 Addendum — Final Wave Correction (2026-06-01)

**Finding**: F1 final wave rejected on grounds of stale `13 applied` migration count in `README.md:75` and `AGENTS.md:52`.

### Issue

The original task-15 evidence stated the linter passes on `README.md` and `QUICKSTART.md`. While technically correct at execution time (the linter's `migration_count` check looks for a pattern of 2-3 digits followed by "migration", and the expected value was derived from source at runtime), the **human reviewer correctly identified** that:

- `README.md:75` contained `migrations/ # PostgreSQL migrations (13 applied)` — the `13 applied` was stale (actual is 30)
- `AGENTS.md:52` contained `migrations/ # PostgreSQL migrations (13 applied)` — same stale claim

### Corrections Applied

- `README.md:75`: `migrations/ # PostgreSQL migrations (13 applied)` → `migrations/ # PostgreSQL migrations`
- `AGENTS.md:52`: `migrations/ # PostgreSQL migrations (13 applied)` → `migrations/ # PostgreSQL migrations`

### Source Verification

```bash
$ ls migrations/*.sql 2>/dev/null | wc -l
30
```

Actual migration count: **30**
