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
