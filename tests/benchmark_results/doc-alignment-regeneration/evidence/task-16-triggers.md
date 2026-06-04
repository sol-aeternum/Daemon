# Task 16 — Trigger Path Coverage Evidence

## Search: All Required Triggers Named in New Section

| Trigger Path | Found in New Section? |
|---|---|
| `orchestrator/config.py` | ✅ Yes |
| `migrations/` | ✅ Yes |
| `orchestrator/memory/**` | ✅ Yes |
| provider config | ✅ Yes |
| routes | ✅ Yes |
| feature-status docs | ✅ Yes |
| `scripts/check_doc_freshness.py --mode fail` | ✅ Yes |

## Evidence — Grep Results

```
$ grep -n "orchestrator/config.py" .github/pull_request_template.md
16: - [ ] No — this PR does not touch `orchestrator/config.py`, `migrations/`, `orchestrator/memory/**`, provider config, routes, or feature-status docs

$ grep -n "migrations/" .github/pull_request_template.md
16: - [ ] No — this PR does not touch `orchestrator/config.py`, `migrations/`, `orchestrator/memory/**`, provider config, routes, or feature-status docs

$ grep -n "orchestrator/memory" .github/pull_request_template.md
16: - [ ] No — this PR does not touch `orchestrator/config.py`, `migrations/`, `orchestrator/memory/**`, provider config, routes, or feature-status docs

$ grep -n "provider config" .github/pull_request_template.md
16: - [ ] No — this PR does not touch `orchestrator/config.py`, `migrations/`, `orchestrator/memory/**`, provider config, routes, or feature-status docs

$ grep -n "routes" .github/pull_request_template.md
16: - [ ] No — this PR does not touch `orchestrator/config.py`, `migrations/`, `orchestrator/memory/**`, provider config, routes, or feature-status docs

$ grep -n "feature-status" .github/pull_request_template.md
16: - [ ] No — this PR does not touch `orchestrator/config.py`, `migrations/`, `orchestrator/memory/**`, provider config, routes, or feature-status docs

$ grep -n "check_doc_freshness" .github/pull_request_template.md
17: - [ ] Yes — and I have run `scripts/check_doc_freshness.py --mode fail` and updated all gated docs
```

## Unchanged Text Verification

```
$ git diff -- .github/pull_request_template.md | grep "^+" | grep -v "^+++" | wc -l
7  (only the 7 new lines added — Source of Truth section)

$ git diff -- .github/pull_request_template.md | grep "^-" | grep -v "^---" | wc -l
0   (no lines removed)
```

**Conclusion**: All required trigger paths are named verbatim in the new section. No existing template text was modified.
