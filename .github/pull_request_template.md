## Description

<!-- What does this PR change? -->

## Feature Matrix

Does this PR add, remove, or change a user-visible feature?

- [ ] No — this PR does not touch user-visible behavior
- [ ] Yes — and I have updated `docs/FEATURE_MATRIX.md` with the change (link to the modified row(s) in this PR's diff)

## Source of Truth

Does this PR touch a source-of-truth or governance file?

- [ ] No — this PR does not touch `orchestrator/config.py`, `migrations/`, `orchestrator/memory/**`, provider config, routes, or feature-status docs
- [ ] Yes — and I have run `python scripts/check_doc_freshness.py --mode fail` and updated all gated docs

## Local CI

Did you run the local gate runner before opening this PR?

- [ ] Yes — I ran `scripts/local_ci.sh` and every blocking gate passed (or I used `scripts/pr_create.sh`, which refuses to call `gh pr create` until they do)
- [ ] Not applicable — explain why (e.g. docs-only change with no script impact)

## Checklist

- [ ] Tests added or updated where applicable
- [ ] Matrix updated if user-visible behavior changed
