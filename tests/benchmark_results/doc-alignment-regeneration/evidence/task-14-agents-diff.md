# Task 14: AGENTS.md Surgical Diff

## Diff Output
```diff
diff --git a/AGENTS.md b/AGENTS.md
index b2946917..6d5bf2b3 100644
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -5,9 +5,10 @@ Personal multi-agent AI assistant. FastAPI backend orchestrates LLM calls via Op
 
 ## Before You Touch Anything
 1. Read `docs/CURRENT_ISSUES.md` — know what's broken before changing things
-2. Read `docs/PROJECT_CONTEXT.md` — understand what's implemented vs planned
-3. If the task touches memory: read `orchestrator/memory/` and `docs/TECHNICAL_SPECS.md`
-4. Check recent commits and code comments for context on current state
+2. Read `docs/FEATURE_MATRIX.md` (implemented/planned status) and `docs/PROJECT_CONTEXT.md` (regenerated context)
+3. If the task touches memory: read `MEMORY_LAYER.md` and `docs/TECHNICAL_SPECS.md`
+4. Read `docs/SOURCES_OF_TRUTH.md` — documentation authority map
+5. Check recent commits and code comments for context on current state
 
 ## Rules of Engagement
 - **Ask before making design decisions.** If a task has multiple valid approaches, present options with tradeoffs. Do not pick one autonomously.
@@ -144,6 +145,6 @@ Daemon maintains a feature matrix at `docs/FEATURE_MATRIX.md` capturing every us
 - Promoting a feature's state on any surface (e.g., `Not started` → `Mobile eligible`) → update the relevant cell
 - Retiring or platform-restricting a feature → update cells or remove the row with justification in the PR
 
-**Validation:** Run `python scripts/lint_feature_matrix.py` before committing matrix changes. CI integration is a separate follow-up; until then, discipline is human-enforced via PR review.
+**Validation:** Run `python scripts/lint_feature_matrix.py` and `python scripts/check_doc_freshness.py --mode fail` before committing changes. CI integration is a separate follow-up; until then, discipline is human-enforced via PR review.
 
 **Internal infrastructure is out of scope.** The matrix tracks user-visible capabilities only. Memory dedup thresholds, embedding model choice, retrieval scoring — none of these are matrix entries.
```

## Conclusion
Only the requested sections in `AGENTS.md` were modified. The "Before You Touch Anything" section now points to canonical sources, and the "Validation" section includes the new documentation freshness gate. The Diagnostic Triage Protocol section remains completely unchanged.
