# Task 11 Reconciliation: OPEN_QUESTIONS.md

| Old Topic | Final Disposition | Source/Reason |
|-----------|-------------------|---------------|
| Memory Promotion Strategy | RESOLVED | Extraction pipeline now writes `status="active"`. Source: `truth_set.md` (Task 2 conflict note), `docs/CURRENT_ISSUES.md`. |
| Local Pipeline Complexity | OPEN/BLOCKED | Blocked on hardware (RTX 5090). `/local` flag is parsed but all local inference code is unimplemented. Source: `FEATURE_MATRIX.md`. |
| Model Identity | RESOLVED | Abstracted as "Daemon". Underlying models are implementation details managed by the tier system. Source: `orchestrator/prompts.py`, `orchestrator/config.py`. |
| Always-On vs Wake-on-LAN | OPEN | Start always-on. Consider WoL if local usage stays <1%. No source evidence resolves this yet. |
| Fallback Chains | OPEN | No concrete source evidence of general quota/provider fallback behavior. |
| Cost Tracking | OPEN | Per-conversation cost display, budget alerts, usage dashboard. `/video-credits` exists but general cost tracking is open. |
| Multi-User | OPEN / ARCHITECTED | Schema support for user scoping exists in `migrations/`, but user-facing multi-user implementation and workflow decisions remain open. |
| Frontend Polish | OPEN | Markdown rendering, theme unification, memory management UI, file attachment support. |

## Verification
- All eight old topics preserved or reconciled.
- No stale terms from the Task 11 prohibited-pattern grep are present in the new document.
- Header metadata added with correct commit hash.
