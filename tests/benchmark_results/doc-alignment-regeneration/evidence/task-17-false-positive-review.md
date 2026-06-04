# Task 17 Evidence — False-Positive / False-Negative Review

## Command Reviewed

```bash
python scripts/check_doc_freshness.py --mode report
```

## Output

```text
No drift detected.
```

The command returned exit code 0. A capture with explicit shell continuation produced:

```text
No drift detected.
EXIT_CODE=0
```

## Assessment

There are no live false positives in the current default report-mode run. The regenerated gated docs currently avoid the active stale patterns for migration count/latest, document embedding model, dedup thresholds, and the hardcoded removed-provider token.

The output is believable only for the active checks. It is not a full no-drift proof because `scripts/check_doc_freshness.py` extracts but does not enforce route names, feature states, env-var names, embedding query model, or embedding dimensions. The provider check is also not source-derived; it only fails on the hardcoded removed-provider token `sora` rather than validating documented providers against `VALID_VIDEO_PROVIDERS = {"xai", "fal"}`.

## False-Positive Risk

- `migration_latest` can flag a legitimate historical migration filename because it treats the first migration filename in a doc as the latest-migration claim.
- `embedding_document_model` is regex-sensitive to prose formatting.
- `video_providers` can report a line containing valid provider text even if the trigger is the hardcoded removed-provider token elsewhere.

These risks are not currently firing, but they should be documented if the gate is narrowed to the implemented subset.

## False-Negative Risk

The current false-negative risk is blocking for Task 18:

1. Route, feature-state, and env-var facts are present in JSON `checked_sources` but not checked by `check_document()`.
2. Embedding query model and dimensions are extracted but not checked.
3. Provider validation is a hardcoded removed-provider sentinel, not a source-derived provider-set check.
4. Dedup checks can pass when the expected threshold value appears somewhere, even if a threshold is misattributed.

## Conclusion

Report mode is clean today, but the gate should not be made blocking until the linter's active checks are source-derived and its declared scope matches what it enforces.
