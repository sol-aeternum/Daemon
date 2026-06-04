# Oracle Gate Review — Documentation Freshness Blocking Gate

**Task**: 17 — Oracle checkpoint before blocking gate activation  
**Date**: 2026-05-31  
**Reviewed artifacts**: `scripts/check_doc_freshness.py`, `docs/SOURCES_OF_TRUTH.md`, regenerated docs, Task 7-16 evidence, `truth_set.md`, prior `oracle_design_review.md`  
**Report-mode command reviewed**: `python scripts/check_doc_freshness.py --mode report`

---

## Verdict

VERDICT: REJECT

Task 18 must not wire the blocking fail-mode gate yet. The current report-mode output has no live false positives, but the implemented linter does not fully satisfy the Task 7/Task 17 gate contract: one active check is a hardcoded stale-provider sentinel, several declared source-derived check classes are extracted but not enforced, and report-mode text can hide malformed exception/source problems when there are no drift findings.

---

## Report-Mode Output Reviewed

```text
$ python scripts/check_doc_freshness.py --mode report
No drift detected.
```

The command returned successfully with exit code 0. A second capture with explicit shell continuation produced:

```text
No drift detected.
EXIT_CODE=0
```

This output is believable only in the narrow sense that the currently active checks found no unsuppressed drift in the default gated-doc set. It is not strong enough to justify a blocking gate because important advertised checks are currently false negatives rather than confirmed passes.

---

## Concrete Linter Checks Reviewed

### Active check IDs

`CheckId` currently defines only seven active IDs in `scripts/check_doc_freshness.py:184-192`:

- `migration_count`
- `migration_latest`
- `embedding_document_model`
- `dedup_merge_threshold`
- `dedup_supersede_generic_threshold`
- `dedup_supersede_same_slot_threshold`
- `video_providers`

`check_document()` only invokes migration checks, document embedding model, dedup threshold checks, and the video provider sentinel (`scripts/check_doc_freshness.py:356-418`). It does not enforce route names, feature states, env-var names, embedding query model, or embedding dimensions.

### Source extraction

The script does extract source facts at runtime for several categories:

- migrations from `migrations/*.sql` (`scripts/check_doc_freshness.py:41-46`), confirmed as 30 files with latest `030_add_advisor_traces.sql`;
- embedding and dedup defaults from `orchestrator/config.py` (`scripts/check_doc_freshness.py:57-75`), confirmed from `config.py:225-246` as `voyage-4-large`, `voyage-4-lite`, 1024 dimensions, and thresholds 0.90/0.82/0.65;
- video providers from `orchestrator/routes/video_credits.py` (`scripts/check_doc_freshness.py:82-103`), confirmed as `VALID_VIDEO_PROVIDERS = {"xai", "fal"}` at `video_credits.py:157-158`;
- routes, feature states, and env vars (`scripts/check_doc_freshness.py:109-168`).

However, extraction is not the same as enforcement. The route, feature-state, and env-var facts are present in JSON reports but are not used by `check_document()`.

### Source-derived vs hardcoded stale claims

This is the blocker. The migration and embedding/dedup checks are source-derived. The `video_providers` check is not source-derived: `_KNOWN_REMOVED_PROVIDERS = frozenset(["sora"])` at `scripts/check_doc_freshness.py:314`, and `_check_video_providers()` fails only if that literal appears (`scripts/check_doc_freshness.py:317-322`). It does not compare documented providers to the extracted source set `{"xai", "fal"}`.

That violates the Task 7 design constraint of no hardcoded stale-value denylist and fails Task 17's requirement to verify that linter checks are high-confidence and source-derived rather than hardcoded stale claims. It also creates false negatives: a gated doc could omit `xai`, claim only `fal`, or invent another non-Sora provider without this active check catching it.

---

## Exception Handling Safety

Exception handling is mostly safe in fail mode:

- The parser recognizes only `<!-- DOC_FRESHNESS_EXCEPTION: <check_id> expires=YYYY-MM-DD reason="..." -->` and records malformed comments when the marker appears without the full syntax (`scripts/check_doc_freshness.py:217-257`).
- Missing `reason` is handled: no full regex match becomes malformed; an empty quoted reason is explicitly rejected (`scripts/check_doc_freshness.py:244-246`).
- Expired exceptions become findings and do not suppress underlying drift (`scripts/check_doc_freshness.py:535-545`; suppression paths require `exc.expires >= today` at `:359-383` and `:399-413`).
- Suppression is per document and per check ID via `_match_exception()` (`scripts/check_doc_freshness.py:334-338`), not a broad suppress-all mechanism.
- Fail mode exits non-zero if malformed exceptions exist (`scripts/check_doc_freshness.py:561-564`).

The remaining safety issue is report-mode text output. In text mode, malformed entries are only formatted when `all_findings` is non-empty; otherwise the script prints `No drift detected.` (`scripts/check_doc_freshness.py:551-557`). This means a malformed exception or missing explicitly requested file can be hidden in report mode if no drift finding exists. JSON output would expose it, and fail mode would exit non-zero, but the default report-mode text is misleading and should be fixed before activation.

---

## Regenerated Docs Reviewed

### `docs/PROJECT_CONTEXT.md`

This doc passes the active linter checks for the right narrow reasons:

- It states current tier/video/provider facts, including `fal` as default and xAI/fal provider context (`docs/PROJECT_CONTEXT.md:52-60`, `:75-78`).
- It uses the current 30-migration inventory and Voyage embedding facts (`docs/PROJECT_CONTEXT.md:69-72`, `:104-109`).
- It correctly includes `/health`, `/status`, and `/providers` as distinct current endpoints (`docs/PROJECT_CONTEXT.md:122-129`).

Task 8 evidence confirms file-specific fail mode returned `No drift detected.` with exit 0 (`evidence/task-8-context-freshness.md`).

### `docs/TECHNICAL_SPECS.md`

This doc also passes for the right active-check reasons:

- It states `30 migrations in /migrations/` and latest migration `030_add_advisor_traces.sql` (`docs/TECHNICAL_SPECS.md:76-92`).
- It states the current Voyage embedding models/dimensions and dedup thresholds 0.90/0.82/0.65 (`docs/TECHNICAL_SPECS.md:99-106`).
- It lists `/health`, `/status`, `/providers`, and current route groups (`docs/TECHNICAL_SPECS.md:116-127`).

Task 9 evidence confirms file-specific fail mode returned `No drift detected.` with exit 0 (`evidence/task-9-specs-freshness.md`), and stale-token search found no known old values (`evidence/task-9-stale-absence.md`).

### `docs/OPEN_QUESTIONS.md`

This doc passes because it mostly avoids volatile structured facts and keeps unresolved product questions source-bounded. It records `/local` as parsed but local inference unimplemented, leaves fallback chains open due to insufficient source evidence, and treats multi-user as architected but not user-facing resolved (`docs/OPEN_QUESTIONS.md:9-19`, `:35-47`, `:66-70`). Task 11 evidence confirms file-specific fail mode returned `No drift detected.` with exit 0 (`evidence/task-11-freshness.md`).

### Additional docs inspected

- `docs/ROADMAP.md` is correctly shaped as a T2 pointer/index rather than a volatile ledger (`docs/ROADMAP.md:7-16`, `:41-42`), and Task 10 evidence shows no stale thresholds or prohibited legacy tokens.
- `docs/CURRENT_ISSUES.md` correctly states the T3 rollup relationship to raw `TRIAGE.md` (`docs/CURRENT_ISSUES.md:5-9`), and Task 13 evidence shows active issues are traceable.
- `docs/PROJECT_BRIEF.md` is durable narrative and avoids inline volatile model/price/migration tables; Task 12 evidence confirms the file-specific linter pass and no volatile tables.

---

## False Positive / False Negative Assessment

### Current false positives

No current false positives were observed in report mode: the default command output is exactly `No drift detected.` with exit 0. The current regenerated docs have been shaped to avoid known linter-sensitive patterns.

### False-positive risks if wired now

- `migration_latest` flags the first migration filename it sees, which can treat a legitimate historical migration reference as a latest-migration claim. Task 11 already encountered this behavior and avoided explicit older migration filenames.
- `embedding_document_model` uses broad prose regex matching; Task 9 evidence notes formatting sensitivity.
- The `video_providers` finding line can point at a valid provider line while the actual trigger is a removed-provider token elsewhere, as recorded in Task 8 learnings.

These are manageable but should be documented as linter limitations if the gate is narrowed honestly.

### False negatives that block approval

- `route_names`, `feature_states`, and `env_var_names` are extracted but not checked.
- `embedding_query_model` and `embedding_dimensions` are extracted but not checked.
- `video_providers` does not validate documented providers against the source-derived set; it only blocks one hardcoded removed provider token.
- Dedup threshold checks are presence-based and do not strongly associate each threshold with its named scenario, so swapped/misattributed threshold prose could pass if all expected values appear somewhere.

Because Task 18 would make this a blocking gate, the gate must either enforce the claimed source-derived checks or narrow its documented scope before activation.

---

## Decision for Task 18

Task 18 may **not** wire the blocking fail-mode gate now.

Required amendments before Task 18:

1. Replace the hardcoded `Sora`/`_KNOWN_REMOVED_PROVIDERS` video-provider sentinel with a source-derived provider check against `VALID_VIDEO_PROVIDERS`, or explicitly rename/re-scope that check and amend the Task 7/Task 17 gate contract. Preferred fix: source-derived validation.
2. Align declared linter scope with actual active checks before making it blocking. Either implement high-confidence checks for the currently advertised but unenforced categories (`embedding_query_model`, `embedding_dimensions`, route names, feature states, env-var names where reliable), or remove those claims from the script/documented gate scope so Task 18 wires an honest subset.
3. Fix text report-mode formatting so malformed exceptions and missing explicitly requested files are printed even when there are zero drift findings. Add a clean-doc malformed-exception fixture to prove report mode no longer says only `No drift detected.` in that case.
4. Re-run and record: `python scripts/check_doc_freshness.py --mode report`, `python scripts/check_doc_freshness.py --mode fail`, and focused stale/provider/exception fixtures after the amendments.

After these amendments, a short re-review can approve Task 18 if the gate is source-derived, honest about scope, and still reports zero drift on regenerated docs.
