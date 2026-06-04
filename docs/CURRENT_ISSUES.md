# Current Issues

> Verified-against-commit: 3155d69fa1eb1939cf5c737018242fc119480d6c
> Last updated: 2026-05-31
> Upstream Sources: TRIAGE.md, docs/SOURCES_OF_TRUTH.md

---

`TRIAGE.md` is the raw append-only log of all encountered anomalies. `docs/CURRENT_ISSUES.md` is the curated active operational rollup of critical and warning-level issues requiring attention.

## Active Critical Issues

### Skills API double-encoding crash
- **Severity**: critical
- **Status**: open
- **Source**: TRIAGE.md (2026-04-16 23:05)
- **Impact**: `/skills` and `/skills/{skill_id}` return 500 because `pending_update` is stored as a double-encoded JSON string instead of an object.
- **Action**: Fix persistence path to stop double-encoding before writes.

### Autonomous-edit toggle 500 error
- **Severity**: critical
- **Status**: open
- **Source**: TRIAGE.md (2026-04-16 23:37)
- **Impact**: Clicking "Allow autonomous edits" fails with a 500 error due to `asyncpg.exceptions.DataError` in `upsert_projection`.
- **Action**: Fix `pending_update` serialization in the autonomous-edit route.

### Summary worker arity mismatch
- **Severity**: critical
- **Status**: open
- **Source**: TRIAGE.md (2026-04-08 20:38)
- **Impact**: Background summary generation crashes because `should_summarize` is called with the wrong number of arguments.
- **Action**: Update `orchestrator/worker/jobs.py` to pass `last_summarized_msg_count`.

### Undefined trust helper in dedup.py
- **Severity**: critical
- **Status**: resolved
- **Source**: TRIAGE.md (2026-04-08 11:12)
- **Impact**: `_lazy_import_trust_signals` is defined at `orchestrator/memory/dedup.py:22` and used at line 512; no undefined variable exists.
- **Action**: No action needed — issue was based on misdiagnosis. Helper exists and is used.

### Video E2E test syntax error
- **Severity**: critical
- **Status**: open
- **Source**: TRIAGE.md (2026-04-08 20:35)
- **Impact**: `tests/test_video_e2e.py` contains an unmatched closing parenthesis, causing pytest collection to fail for the entire suite.
- **Action**: Fix the syntax error at line 596.

### Backend container restart wiped benchmark artifacts
- **Severity**: critical
- **Status**: open
- **Source**: TRIAGE.md (2026-05-27 UTC)
- **Impact**: Ephemeral `/tmp/opencode` directory was cleared during a container restart, losing completed benchmark results before they were copied to host storage.
- **Action**: Implement persistent volume mounts or periodic artifact copy-back for long-running jobs.

## Active Warning Issues

### Repository-wide LSP diagnostic noise
- **Severity**: warning
- **Status**: open
- **Source**: TRIAGE.md (2026-05-27 UTC)
- **Impact**: Pre-existing dirty-tree diagnostics (22 errors, 2169 warnings) obscure new regressions during workspace scans.
- **Action**: Triage advisor/Kling/reminder diagnostics; establish a warning-budget strategy.

### Missing Markdown and Biome LSP servers
- **Severity**: warning
- **Status**: open
- **Source**: TRIAGE.md (2026-05-27, 2026-04-14)
- **Impact**: `lsp_diagnostics` cannot run on `.md` or `.json` files, preventing automated verification of documentation and artifacts.
- **Action**: Install Marksman and Biome in the workspace environment.

### Subagent Task Delegation CreditsError
- **Severity**: warning
- **Status**: open
- **Source**: TRIAGE.md (2026-04-15 12:57)
- **Impact**: Task delegation fails when the selected model route requires workspace credits that are currently unavailable.
- **Action**: Restore workspace billing balance or adjust model routing for delegations.

### Frontend lint and TSC build failures
- **Severity**: warning
- **Status**: open
- **Source**: TRIAGE.md (2026-04-08 20:35)
- **Impact**: `next lint` and `tsc` fail due to Next.js 16 CLI changes and missing generated type artifacts, blocking clean CI runs.
- **Action**: Update lint scripts and ensure generated types are present before type-checking.
