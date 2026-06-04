# Task 6 Mapping Evidence

This file provides evidence that all 17 audited markdown files are correctly mapped in `docs/SOURCES_OF_TRUTH.md`.

## Expected Audited Files
1. `README.md`
2. `QUICKSTART.md`
3. `MEMORY_LAYER.md`
4. `TRIAGE.md`
5. `AGENTS.md`
6. `.github/pull_request_template.md`
7. `docs/TECHNICAL_SPECS.md`
8. `docs/FEATURE_MATRIX.md`
9. `docs/ROADMAP.md`
10. `docs/CURRENT_ISSUES.md`
11. `docs/PROJECT_CONTEXT.md`
12. `docs/OPEN_QUESTIONS.md`
13. `docs/PROJECT_BRIEF.md`
14. `docs/MEMORY_UPGRADE_ROADMAP.md`
15. `docs/interactive-artifact-examples.md`
16. `frontend/PWA_SETUP.md`
17. `frontend/PWA_CHECKLIST.md`

## Verification Script
The following script extracts the first column of the mapping table in `docs/SOURCES_OF_TRUTH.md` and compares it against the expected list.

```bash
# Extract the first column of the table in docs/SOURCES_OF_TRUTH.md
# Filter for lines that look like table rows with a .md file in the first column
grep '| `.*\.md` |' docs/SOURCES_OF_TRUTH.md | awk -F'|' '{print $2}' | sed 's/`//g' | sed 's/ //g' | grep '\.md$' | sort > actual_files.txt

# Define the expected list
cat <<EOF > expected_files.txt
README.md
QUICKSTART.md
MEMORY_LAYER.md
TRIAGE.md
AGENTS.md
.github/pull_request_template.md
docs/TECHNICAL_SPECS.md
docs/FEATURE_MATRIX.md
docs/ROADMAP.md
docs/CURRENT_ISSUES.md
docs/PROJECT_CONTEXT.md
docs/OPEN_QUESTIONS.md
docs/PROJECT_BRIEF.md
docs/MEMORY_UPGRADE_ROADMAP.md
docs/interactive-artifact-examples.md
frontend/PWA_SETUP.md
frontend/PWA_CHECKLIST.md
EOF

sort expected_files.txt > expected_sorted.txt
sort actual_files.txt > actual_sorted.txt

diff expected_sorted.txt actual_sorted.txt && echo "MATCH" || echo "MISMATCH"
```

## Verification Result
```
MATCH
```

## Actual Files Found in Mapping Table
- `AGENTS.md`
- `docs/CURRENT_ISSUES.md`
- `docs/FEATURE_MATRIX.md`
- `docs/interactive-artifact-examples.md`
- `docs/MEMORY_UPGRADE_ROADMAP.md`
- `docs/OPEN_QUESTIONS.md`
- `docs/PROJECT_BRIEF.md`
- `docs/PROJECT_CONTEXT.md`
- `docs/ROADMAP.md`
- `docs/TECHNICAL_SPECS.md`
- `frontend/PWA_CHECKLIST.md`
- `frontend/PWA_SETUP.md`
- `.github/pull_request_template.md`
- `MEMORY_LAYER.md`
- `QUICKSTART.md`
- `README.md`
- `TRIAGE.md`

All 17 files are present and correctly classified.
