# Task 6 Mapping Evidence

Every markdown file from the audit inventory is mapped in `docs/SOURCES_OF_TRUTH.md`.

## Audited Files (Expected)
- `AGENTS.md`
- `.github/pull_request_template.md`
- `docs/CURRENT_ISSUES.md`
- `docs/FEATURE_MATRIX.md`
- `docs/MEMORY_UPGRADE_ROADMAP.md`
- `docs/OPEN_QUESTIONS.md`
- `docs/PROJECT_BRIEF.md`
- `docs/PROJECT_CONTEXT.md`
- `docs/ROADMAP.md`
- `docs/TECHNICAL_SPECS.md`
- `docs/interactive-artifact-examples.md`
- `frontend/PWA_CHECKLIST.md`
- `frontend/PWA_SETUP.md`
- `MEMORY_LAYER.md`
- `QUICKSTART.md`
- `README.md`
- `TRIAGE.md`

## Verification Command
```bash
grep -oE '`[^`]+\.md`' docs/SOURCES_OF_TRUTH.md | sort | uniq
```

## Result
All 17 files are present in the mapping table of `docs/SOURCES_OF_TRUTH.md`.
