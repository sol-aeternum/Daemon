# Task 14: AGENTS.md Reference Discoverability

## Search Command
`grep -E "SOURCES_OF_TRUTH|FEATURE_MATRIX|MEMORY_LAYER|check_doc_freshness" AGENTS.md`

## Search Output
```
2. Read `docs/FEATURE_MATRIX.md` (implemented/planned status) and `docs/PROJECT_CONTEXT.md` (regenerated context)
3. If the task touches memory: read `MEMORY_LAYER.md` and `docs/TECHNICAL_SPECS.md`
4. Read `docs/SOURCES_OF_TRUTH.md` — documentation authority map
Daemon maintains a feature matrix at `docs/FEATURE_MATRIX.md` capturing every user-visible feature's state across each client surface. This is scope control, not documentation.
**Validation:** Run `python scripts/lint_feature_matrix.py` and `python scripts/check_doc_freshness.py --mode fail` before committing changes. CI integration is a separate follow-up; until then, discipline is human-enforced via PR review.
```

## Conclusion
All new references (`SOURCES_OF_TRUTH`, `FEATURE_MATRIX`, `MEMORY_LAYER`) and the new quality gate (`check_doc_freshness`) are present in their intended contexts within `AGENTS.md`.
