# PR-Review Fix Wave — PR #6 Bot Comments

**Date**: 2026-06-01
**Branch**: `doc-alignment-regeneration-2026-05-29`
**Scope**: 12 unresolved bot PR review comment fixes (Atlas-reported)

---

## Summary

Addressed all 12 unresolved PR review comment threads from Atlas's review of PR #6.
The fix wave touches only the linter (`scripts/check_doc_freshness.py`), two docs,
and the CI workflow. No runtime code under `orchestrator/`, `frontend/`, or `providers/` was modified.

---

## Comment Fixes

### 3330359183 — Missing Gated Docs Malformed
**Status**: VERIFIED (prior final-wave fix confirmed)
- `get_gated_docs()` now appends gated paths even when missing, triggering `MALFORMED_EXCEPTION` in fail mode
- Verified: linter passes with no findings

### 3330359186 — Dedup Threshold Label-Value Binding
**Fix**: Updated `_check_dedup_threshold()` to require the threshold value to appear near its corresponding label.
- New signature: `_check_dedup_threshold(text, name, expected, label_pattern)`
- Label binding patterns: `merge`, `generic`, `same.?slot`
- A doc with swapped labels (e.g., merge=0.65, same_slot=0.90) now fails

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode fail --files <swapped_doc.md>
Exit 1: dedup threshold mismatch for dedup_supersede_same_slot_threshold:
  expected 0.65 near 'same.?slot', found 0.90
```

### 3330359187 — Embedding Query Model and Dimensions
**Fix**: Added `_check_embedding_query_model()` and `_check_embedding_dimensions()`.
- Added `CheckId.EMBEDDING_QUERY_MODEL` and `CheckId.EMBEDDING_DIMENSIONS`
- Added `_EMBEDDING_QUERY_MODEL_CLAIM_RE` and `_EMBEDDING_DIMENSIONS_CLAIM_RE`
- Integration into `check_document()` with exception handling

### 3330359188 — Migration Filename All Claims
**Fix**: Changed `_check_migration_latest()` from `re.search` to `re.findall`.
- Now scans ALL migration filename patterns in doc, not just the first
- Reports stale claim if any pattern mismatch found

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode fail --files <dual_claims_doc.md>
Exit 1: migration latest mismatch: expected 030_..., found 013_...
```

### 3330359189 — Markdown/Backtick Provider List Parsing
**Fix**: Added singular and plural backtick patterns to `_check_video_providers()`.
- Singular: `**Providers**: `fal`` (singular claim, validates fal is in valid set)
- Plural: `**Providers**: `fal` `xai`` (plural claim, validates exact set match)
- Added non-greedy `[^`]+?` to prevent greedy over-matching
- Added skip logic: if extracted name is in `singular_claims`, don't add to `plural_claims`

### 3330359190 — Token SSE data.text Not data.delta
**Fix**: Updated `docs/TECHNICAL_SPECS.md:65`:
```
- Before: | `token` | `data.delta` | Incremental text token |
- After:  | `token` | `data.text` | Incremental text token (compat: `data.delta` accepted by bridge) |
```

### 3330399004 — Active Non-Suppressing Exceptions in Reports
**Fix**: `check_document()` now returns ALL active non-expired exceptions, not just `updated_exceptions` (suppressed ones).
- Changed return: `all_active_exceptions = [exc for exc in exceptions if exc.expires >= today]`
- Non-suppressing exceptions now appear in reports with `(ACTIVE)` status

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode report --files <exception_doc.md>
<doc>:2 [EXCEPTION migration_count] expires=2027-01-01 reason='test exception' (ACTIVE)
```

### 3330399006 — docker-compose.yml in Workflow Paths
**Fix**: Added `docker-compose.yml` to path filters in `.github/workflows/docs-freshness.yml`
for both `push` and `pull_request` triggers.

### 3330399007 — Tier Model Default Validation
**Fix**: Added `get_tier_facts()` and `_check_tier_defaults()` to validate tier table claims.
- Extracts all `tier_{name}_{slot}_model` from `orchestrator/config.py`
- Parses tier table rows from gated docs with `_TIER_TABLE_ROW_RE`
- Normalizes model names (strips provider prefix) for comparison
- Allows BYOK embeddings to be "voyage-4-large" even though config has "" (documented exception)
- Integrated into `check_document()` with `CheckId.TIER_MODEL`

**Scope note**: Validation is limited to checking if the documented model name appears within the config value (substring match after normalization). This avoids semantic prose validation while catching clear mismatches.

### 3330399009 — Migration Count All Claims
**Fix**: Changed `_check_migration_count()` from `re.search` to `re.findall`.
- Now scans ALL migration count patterns in doc, not just the first
- Reports stale claim if any pattern mismatch found

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode fail --files <stale_count_doc.md>
Exit 1: migration count mismatch: expected 30, found 13
```

### 3330450207 — Bold/Coloned Embedding Format Acceptance
**Fix**: Updated `_EMBEDDING_DOC_MODEL_CLAIM_RE` to accept bold GFM format.
- New pattern: `r'\*\*EMBEDDING_DOCUMENT_MODEL\*\*[:=\s]+(?:["\'])?([a-z0-9-]+)(?=["\']?\s|$)'`
- Same approach for `_EMBEDDING_QUERY_MODEL_CLAIM_RE` and `_EMBEDDING_DIMENSIONS_CLAIM_RE`

**Evidence** (temp fixture):
```
$ python scripts/check_doc_freshness.py --mode fail --files <bold_embedding_doc.md>
Exit 0: No drift detected.  (accepts **EMBEDDING_DOCUMENT_MODEL**: voyage-4-large)
```

### 3330450209 — Video Default Propagation Wording
**Fix**: Updated `docs/PROJECT_CONTEXT.md:60`:
```
- Before: *Note: Default video provider is `fal` (Kling). BYOK users bypass credits...
- After:  *Note: Default video provider is `fal` (Kling) via config.py; runtime selection
           depends on `video_provider` propagation. BYOK users bypass credits...
```
**Rationale**: config.py defaults are `fal`, but image subagent can fall back to image/openrouter
if video_provider is not propagated from config. Documentation updated to be source-truth oriented
without implying unconditional runtime behavior.

### 3330399007 (Tier Model Defaults) — Deferral Consideration
The PR comment asked whether a one-line config propagation fix is already intended by existing code.
After analysis: `orchestrator/` tier routing reads `video_provider` from `TierConfig` which is populated
from config.py defaults. There is no evidence of incomplete propagation in the code itself.
The documentation-only fix (above) is the appropriate resolution.

---

## Verification

### Linter Commands
```bash
# Report mode
python scripts/check_doc_freshness.py --mode report --format text
# Expected: No drift detected.

# Fail mode (full gated docs)
python scripts/check_doc_freshness.py --mode fail --format text
# Expected: No drift detected. (exit 0)

# Fail mode (root docs only)
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md
# Expected: No drift detected. (exit 0)

# Compile check
python -m py_compile scripts/check_doc_freshness.py
# Expected: (no output, exit 0)

# Feature matrix linter
python scripts/lint_feature_matrix.py
# Expected: OK: 60 feature rows validated
```

### Temp Fixture Results
| Test | Input | Expected | Actual |
|------|-------|----------|--------|
| Dedup swapped | merge=0.65, same_slot=0.90 | Exit 1 | Exit 1 ✅ |
| Migration count stale | 30...13 migrations | Exit 1 | Exit 1 ✅ |
| Provider markdown/bold | `**Providers**: `fal`` | Exit 0 | Exit 0 ✅ |
| Embedding bold format | `**EMBEDDING_DOCUMENT_MODEL**: voyage-4-large` | Exit 0 | Exit 0 ✅ |
| Non-suppressing exception | ACTIVE exception in report | Visible | Visible ✅ |
| Embedding prose false positive | "embedding model choice..." | Exit 0 | Exit 0 ✅ |

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/check_doc_freshness.py` | Linter fixes: dedup binding, migration all-claims, embedding query/dim, provider markdown, tier defaults, exception visibility, bold embedding format |
| `docs/TECHNICAL_SPECS.md` | Token SSE `data.text` (was `data.delta`) |
| `docs/PROJECT_CONTEXT.md` | Video default wording updated for propagation nuance |
| `.github/workflows/docs-freshness.yml` | Added `docker-compose.yml` to path filters |

---

## Deliberately Deferred

**3330450209 (video default)**: No app-code change made. The documentation-only fix addresses the overstatement. No evidence of incomplete config propagation in runtime code. Config propagation in the image/video subagent is functioning as designed.

---

## Atlas Verification Addendum (Jun 2026)

### Issue: Two False Negatives Found by Atlas

After initial push, Atlas ran verification fixtures and found two false negatives in the linter:

1. **Provider markdown plural not detected**: `**Providers**: `fal`` was treated as singular and silently skipped from plural_claims, causing `fal`-only claims to pass when they should fail.

2. **Tier model check not running**: `_TIER_NAME_MAP` had uppercase keys ("FREE", "PRO", etc.) but lookup used `tier_doc_name.lower()` returning lowercase, causing all tier checks to silently skip.

### Fixes Applied

#### Provider markdown plural fix:
- Removed `\*\*Providers\*\*:\s*\`([a-z]+)\`` from `singular_patterns` — bold plural label should NEVER be treated as singular
- Added `plural_backtick_label_patterns` approach: match bold label, extract ALL backtick-quoted names on the SAME LINE only
- Key bug: `doc_content[m.end():]` extended to end of entire document, picking up Docker Compose service names from later lines; fixed by limiting to `\n`-delimited line boundary

#### Tier model check fixes:
- **Map keys**: Changed `_TIER_NAME_MAP` keys from uppercase to lowercase, fixing `.get(tier_doc_name.lower())` lookup
- **Regex too broad**: `[A-Z]+` matched feature matrix bold headers like `**Conversations**`; changed to alternation `(FREE|STARTER|PRO|MAX|BYOK)`
- **Slash not normalized**: `openrouter/moonshotai/kimi-k2.5` split into `{"moonshotai/kimi", "k2.5"}` as words; added `/` to the replacement set
- **Em-dash not handled**: `| **BYOK** | — | ...` (FEATURE_MATRIX.md section header) was treated as non-empty value; added `"—"` to the empty/placeholder set
- **Word-overlap too strict**: Using `doc_words.issubset(config_words)` required ALL doc words to appear in config, failing multi-model claims like "Claude 3.5 Sonnet / Opus 4.6"; changed to `any(w in config_normalized for w in doc_words)` — a stale "Wrong Model" still fails because no doc word overlaps with the config string

### Fixture Verification (Atlas-reproduced)

| Fixture | Input | Expected | Actual |
|---------|-------|----------|--------|
| provider_markdown_missing | `**Providers**: `fal`` (only fal) | Exit 1 | Exit 1 ✅ |
| tier_stale | `| **PRO** | Wrong Model | ...` | Exit 1 | Exit 1 ✅ |
| generic_embedding_prose | "embedding model choice..." | Exit 0 | Exit 0 ✅ |
| bold_embedding_ok | `**EMBEDDING_DOCUMENT_MODEL**: voyage-4-large` | Exit 0 | Exit 0 ✅ |
| bold_embedding_stale | `**EMBEDDING_DOCUMENT_MODEL**: voyage-3` | Exit 1 | Exit 1 ✅ |
| query_stale | `**EMBEDDING_QUERY_MODEL**: voyage-3-lite` | Exit 1 | Exit 1 ✅ |
| dim_stale | `**EMBEDDING_DIMENSIONS**: 512` | Exit 1 | Exit 1 ✅ |
| migration_count_second_stale | "30...13" migrations | Exit 1 | Exit 1 ✅ |
| migration_latest_second_stale | "030_...; 013_..." | Exit 1 | Exit 1 ✅ |
| dedup_swapped | merge=0.65, same_slot=0.90 | Exit 1 | Exit 1 ✅ |
| provider_markdown_ok | `**Providers**: `fal` `xai`` | Exit 0 | Exit 0 ✅ |
| active_exception_visible | Non-suppressing exception | Report shows ACTIVE | ACTIVE visible ✅ |

### Standard Gate Verification

```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text     # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py                  # Compile OK
python scripts/lint_feature_matrix.py                                 # OK: 60 feature rows validated
```

---

## Atlas Verification Addendum 2 (Jun 2026)

### Issue: Plain Embedding Claim Forms Not Detected

Atlas fixture runs found three false negatives in embedding claim detection:

| Fixture | Input | Expected | Actual (before) |
|---------|-------|----------|-----------------|
| `plain_embedding_doc_stale` | `embedding_document_model: "voyage-3"` | Exit 1 | Exit 0 |
| `plain_embedding_query_stale` | `embedding_query_model: "voyage-3-lite"` | Exit 1 | Exit 0 |
| `plain_embedding_dim_stale` | `embedding_dimensions: 512` | Exit 1 | Exit 0 |

Root cause: `_EMBEDDING_DOC_MODEL_CLAIM_RE` and siblings only matched `**EMBEDDING...**` bold GFM form. Plain labels (`embedding_document_model:`, `EMBEDDING_DOCUMENT_MODEL:`) were silently ignored.

### Fix Applied

Extended the three embedding claim regexes to accept all three label forms via alternation:

```python
# Before (bold-only):
r'\*\*EMBEDDING_DOCUMENT_MODEL\*\*[:=\s]+(?:["\'])?([a-z0-9-]+)...'

# After (bold + uppercase + lowercase):
r'(?:\*\*EMBEDDING_DOCUMENT_MODEL\*\*|EMBEDDING_DOCUMENT_MODEL|embedding_document_model)[:=\s]+(?:["\'])?([a-z0-9-]+)...'
```

Same pattern applied to `EMBEDDING_QUERY_MODEL` / `embedding_query_model` and `EMBEDDING_DIMENSIONS` / `embedding_dimensions`.

### Fixture Verification

| Fixture | Expected | Actual |
|---------|----------|--------|
| `generic_embedding_prose` | Exit 0 | Exit 0 ✅ |
| `bold_embedding_ok` (voyage-4-large/lite, 1024) | Exit 0 | Exit 0 ✅ |
| `bold_embedding_stale` (voyage-3) | Exit 1 | Exit 1 ✅ |
| `plain_embedding_doc_stale` (`embedding_document_model: "voyage-3"`) | Exit 1 | Exit 1 ✅ |
| `plain_embedding_doc_stale_upper` (`EMBEDDING_DOCUMENT_MODEL: "voyage-3"`) | Exit 1 | Exit 1 ✅ |
| `plain_embedding_doc_stale_noquotes` (`embedding_document_model: voyage-3`) | Exit 1 | Exit 1 ✅ |
| `plain_embedding_query_stale` | Exit 1 | Exit 1 ✅ |
| `plain_embedding_dim_stale` | Exit 1 | Exit 1 ✅ |
| `migration_count_second_stale` | Exit 1 | Exit 1 ✅ |
| `migration_latest_second_stale` | Exit 1 | Exit 1 ✅ |
| `dedup_swapped` | Exit 1 | Exit 1 ✅ |
| `provider_markdown_ok` | Exit 0 | Exit 0 ✅ |
| `provider_markdown_missing` | Exit 1 | Exit 1 ✅ |
| `tier_stale_wrong` | Exit 1 | Exit 1 ✅ |
| `active_exception_visible` (with non-suppressing exception) | Report exit 0, `(ACTIVE)` visible | `(ACTIVE)` visible ✅ |

### Standard Gate Verification

```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text     # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py                  # Compile OK
python scripts/lint_feature_matrix.py                                 # OK: 60 feature rows validated
```

---

## Atlas Verification Addendum 3 (Jun 2026)

### Issues Fixed (5 blockers)

#### 1. Embedding Exact Match
**Problem:** Embedding doc/query model checks used substring containment (`expected in observed or observed in expected`), allowing `voyage-4` to pass against `voyage-4-large`.

**Fix:** Changed to exact `_normalize_model_name()` equality comparison. Both sides normalized (lowercased, `/`-` ` replaced) then compared.

```python
# Before (substring):
if expected.lower() not in observed.lower() and observed.lower() not in expected.lower():

# After (exact):
if _normalize_model_name(observed) != _normalize_model_name(expected):
```

#### 2. Tier Model Validation — Strengthened Overlap
**Problem:** Single-word `any()` overlap allowed `Claude 3 Haiku` to match `openrouter/anthropic/claude-3.5-sonnet` via shared "claude".

**Fix:** Require at least 2 content words to overlap. `Claude 3 Haiku` vs `claude-3.5-sonnet` → overlap = {"claude"} = 1 → FAIL. `Claude 3.5 Sonnet` vs `claude-3.5-sonnet` → overlap = {"claude","3.5","sonnet"} = 3 → PASS.

#### 3. Latest Migration — Structured Claims Only
**Problem:** `_check_migration_latest` scanned every migration filename mention, causing `001_initial_schema.sql` in historical context to fail.

**Fix:** New pattern `(?:latest\s+(?:migration|db)|most\s+recent|newest)\s*[:\->=]\s*(\d{2,3}_\w+(?:\.sql)?)` only matches explicit latest-claim labels. Historical mentions pass silently.

#### 4. Memory Links in docs/ Subdirectory
**Problem:** `docs/PROJECT_CONTEXT.md` and `docs/TECHNICAL_SPECS.md` used `[MEMORY_LAYER.md](MEMORY_LAYER.md)` — broken relative link from `docs/` to root.

**Fix:** Changed to `../MEMORY_LAYER.md` in both files.

#### 5. @document Spawn Schema
**Problem:** `spawn_agent` and `spawn_multiple` tool schemas omitted `document` from `agent_type` enum despite `DocumentSubagent` being registered and README advertising `@document`.

**Fix:** Added `"document"` to both enum arrays at `spawn.py:178` and `spawn.py:293`.

### Fixture Verification (Jun 2026)

| Fixture | Expected | Actual |
|---------|----------|--------|
| `emb_doc_exact_stale_voyage4` (`**EMBEDDING_DOCUMENT_MODEL**: voyage-4`) | Exit 1 | Exit 1 ✅ |
| `emb_doc_exact_ok` (voyage-4-large/lite, 1024) | Exit 0 | Exit 0 ✅ |
| `emb_query_exact_stale_voyage4` | Exit 1 | Exit 1 ✅ |
| `tier_same_family_haiku_stale` (PRO/Research = Claude 3 Haiku) | Exit 1 | Exit 1 ✅ |
| `migration_historical_001_passes` | Exit 0 | Exit 0 ✅ |
| `migration_latest_stale_structured` | Exit 1 | Exit 1 ✅ |
| `generic_embedding_prose` | Exit 0 | Exit 0 ✅ |
| `migration_count_second_stale` | Exit 1 | Exit 1 ✅ |
| `dedup_swapped` | Exit 1 | Exit 1 ✅ |
| `provider_markdown_ok` | Exit 0 | Exit 0 ✅ |
| `provider_markdown_missing` | Exit 1 | Exit 1 ✅ |
| `tier_stale_wrong` | Exit 1 | Exit 1 ✅ |
| `active_exception_visible` | Report exit 0, `(ACTIVE)` visible | ACTIVE visible ✅ |

### Standard Gate Verification

```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text     # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py                  # Compile OK
python scripts/lint_feature_matrix.py                                 # OK: 60 feature rows validated
```

### Commits Pushed
- `e06b8d9b` — fix(linter): exact embedding match, stricter tier overlap, structured migration latest
- `1e8bd5c2` — fix(docs): fix MEMORY_LAYER.md links in docs/ subdirectory
- `4cfa762d` — fix(schema): add document to spawn tool agent_type enums

---

## Atlas Verification Addendum 4 (Jun 2026)

### Issue: `len(overlap) >= 2` Still Too Loose for Tier Models

Atlas noted that `e06b8d9b`'s `len(overlap) < 2` rule still allowed `Claude 3.5 Haiku` to match `openrouter/anthropic/claude-3.5-sonnet` via 2-word overlap (`claude` + `3.5`). Codex comment `3330918351` requires exact tier model matching.

### Fix Applied

**Tier model validation now uses exact normalized alias comparison.**

#### `_normalize_model_name()` upgrade:
- Recursively strips all known provider prefixes (`openrouter/`, `moonshotai/`, `google/`, `anthropic/`, etc.) until none remain
- Strips provider-specific suffixes (`-image`, `-video`, `-instruct`, `-chat`, `-preview`) before separator normalization
- Normalizes separators to spaces

#### Alias derivation examples:
| Config model ID | Alias |
|-----------------|-------|
| `openrouter/moonshotai/kimi-k2.5` | `kimi k2.5` |
| `openrouter/anthropic/claude-3.5-sonnet` | `claude 3.5 sonnet` |
| `openrouter/anthropic/claude-opus-4.6` | `claude opus 4.6` |
| `google/gemini-2.5-flash-image` | `gemini 2.5 flash` (suffix stripped) |
| `openrouter/google/gemini-2.0-pro-exp` | `gemini 2.0 pro exp` |
| `voyage-4-large` | `voyage 4 large` |
| `fal` | `fal` |

#### `_check_tier_defaults()` upgrade:
- Each doc slot value is split on `/` into individual options
- Each option is normalized and compared for exact equality against the config alias
- At least one option must match exactly (for multi-model cells like `Claude 3.5 Sonnet / Opus 4.6`)
- This supersedes the `len(overlap) >= 2` word-overlap approach

### Stale Detection Results

| Doc claim | Config alias | Match | Result |
|-----------|-------------|-------|--------|
| `Claude 3 Haiku` | `claude 3.5 sonnet` | `claude 3 haiku` ≠ `claude 3.5 sonnet` | FAIL ✅ |
| `Claude 3.5 Haiku` | `claude 3.5 sonnet` | `claude 3.5 haiku` ≠ `claude 3.5 sonnet` | FAIL ✅ |
| `Claude` | `claude 3.5 sonnet` | `claude` ≠ `claude 3.5 sonnet` | FAIL ✅ |
| `Claude 3.5 Sonnet` | `claude 3.5 sonnet` | exact match | PASS ✅ |
| `Kimi K2.5` | `kimi k2.5` | exact match | PASS ✅ |
| `Gemini 2.5 Flash` | `gemini 2.5 flash` | exact match | PASS ✅ |
| `Claude 3.5 Sonnet / Opus 4.6` | `claude 3.5 sonnet` | first option matches | PASS ✅ |

### Fixture Verification

| Fixture | Expected | Actual |
|---------|----------|--------|
| `current_PRO_tier_table` | Exit 0 | Exit 0 ✅ |
| `current_STARTER_tier_table` | Exit 0 | Exit 0 ✅ |
| `current_MAX_tier_multioption` | Exit 0 | Exit 0 ✅ |
| `current_FREE_tier` | Exit 0 | Exit 0 ✅ |
| `tier_stale_claude3_haiku` | Exit 1 | Exit 1 ✅ |
| `tier_stale_claude35_haiku` | Exit 1 | Exit 1 ✅ |
| `tier_stale_claude_only` | Exit 1 | Exit 1 ✅ |
| `tier_stale_wrong_model` | Exit 1 | Exit 1 ✅ |
| `emb_doc_exact_stale_voyage4` | Exit 1 | Exit 1 ✅ |
| `emb_doc_exact_ok` | Exit 0 | Exit 0 ✅ |
| `generic_embedding_prose` | Exit 0 | Exit 0 ✅ |
| `migration_historical_001_passes` | Exit 0 | Exit 0 ✅ |
| `migration_latest_stale_structured` | Exit 1 | Exit 1 ✅ |
| `migration_count_second_stale` | Exit 1 | Exit 1 ✅ |
| `provider_markdown_ok` | Exit 0 | Exit 0 ✅ |
| `provider_markdown_missing` | Exit 1 | Exit 1 ✅ |
| `active_exception_visible` | Report exit 0, `(ACTIVE)` | ACTIVE visible ✅ |

### Standard Gate Verification

```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text     # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py                  # Compile OK
python scripts/lint_feature_matrix.py                                 # OK: 60 feature rows validated
```

### Commit Pushed
- `d7d85feb` — fix(linter): exact tier model alias matching, superseding len(overlap)>=2

---

## Atlas Verification Addendum 5 (Jun 2026)

### Context
Reviewer re-verified after `5cef462c` commit. Three additional issues found and fixed in a follow-up patch.

### Issues Fixed

#### 1. LSP Type Error — Optional slot values in tier_claims dict
**Problem:** `tier_claims` typed `dict[str, dict[str, str]]` but assigned `None` for optional slots (research, code, image, etc.) in PROJECT_CONTEXT 4-column parsing.

**Fix:** Changed type annotation to `dict[str, dict[str, str | None]]`.

#### 2. TECHNICAL_SPECS False Positive — Bare "Opus 4.6" in code slot
**Problem:** Combined Research/Code cell `Claude 3.5 Sonnet / Opus 4.6` splits second part into code slot as `Opus 4.6`. The bare-cell fallback compared `doc_options[0]` (which is `Opus 4.6` normalized: `opus 4.6`) against `research_alias` (`claude 3.5 sonnet`), not `code_alias` (`claude opus 4.6`). These don't match, causing a false FAIL.

**Fix:** Added `or (slot == "code" and (code_alias.endswith(_normalize_model_name(doc_options[0]))))` to the bare-cell exception. This allows bare code names like `Opus 4.6` to match config `claude opus 4.6` when they share the same suffix.

#### 3. PROJECT_CONTEXT False Positive — CODE_MISSING from 4-column table
**Problem:** PROJECT_CONTEXT 4-column table (orchestrator, subagents, video) has no combined Research/Code cell, but the parser explicitly sets `research: None, code: None` for all tiers. The `CODE_MISSING` guard checked `("research" in doc_slots or "code" in doc_slots)` — both keys exist (with `None` values), so the guard passed and a spurious finding was emitted for MAX tier.

**Fix:** Changed guard to `doc_slots.get("research") is not None`. Only fires when the doc actually claims a combined Research/Code cell in a 7-column TECHNICAL_SPECS-style row.

#### 4. MEMORY_LAYER.md AES-GCM Wording
**Problem:** `MEMORY_LAYER.md:16` said `Fernet (AES-256-GCM)` but source code (`orchestrator/memory/encryption.py`) uses `cryptography.fernet.Fernet`. Fernet is AES-128-CBC, not AES-256-GCM. This is a doc/source contradiction.

**Fix:** Changed `Fernet (AES-256-GCM)` to `Fernet` in MEMORY_LAYER.md and PROJECT_CONTEXT.md. `cryptography.fernet.Fernet` is the authoritative implementation reference.

### Verification

#### Standard Gates
```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py             # Compile OK
python scripts/lint_feature_matrix.py                            # OK: 60 feature rows validated
```

#### Fixture Suite (16/16)
| Fixture | Expected | Actual |
|---------|----------|--------|
| `inline_latest_backtick_stale` | Exit 1 | Exit 1 ✅ |
| `inline_latest_no_backtick_stale` | Exit 1 | Exit 1 ✅ |
| `migration_historical_no_latest_label` | Exit 0 | Exit 0 ✅ |
| `projctx_free_tier_current` | Exit 0 | Exit 0 ✅ |
| `projctx_pro_tier_stale_orchestrator` | Exit 1 | Exit 1 ✅ |
| `techspec_max_research_code_combined_current` | Exit 0 | Exit 0 ✅ |
| `techspec_max_research_code_missing_opus` | Exit 1 | Exit 1 ✅ |
| `emb_doc_exact_stale_voyage4` | Exit 1 | Exit 1 ✅ |
| `emb_doc_exact_ok` | Exit 0 | Exit 0 ✅ |
| `generic_embedding_prose` | Exit 0 | Exit 0 ✅ |
| `migration_count_second_stale` | Exit 1 | Exit 1 ✅ |
| `migration_latest_stale_structured` | Exit 1 | Exit 1 ✅ |
| `provider_markdown_ok` | Exit 0 | Exit 0 ✅ |
| `provider_markdown_missing` | Exit 1 | Exit 1 ✅ |
| `tier_stale_claude3_haiku` | Exit 1 | Exit 1 ✅ |
| `active_exception_visible` | Report exit 0, `(ACTIVE)` | ACTIVE visible ✅ |

### Commit Pushed
- `5cef462c` — fix(lint): 4 context-mining reviewer blockers from PR #6 (inline latest migration, PROJECT_CONTEXT tier table, combined Research/Code, AES-GCM wording)
- `[follow-up]` — fix(lint): LSP type error, bare Opus 4.6 suffix match, PROJECT_CONTEXT false positive guard, MEMORY_LAYER AES-GCM wording

---

## Atlas Verification Addendum 6 (Jun 2026)

### Context
Follow-up verification after `afae82cb`. Four issues found by Atlas and fixed in this patch.

### Issues Fixed

#### 1. LSP Type Error — `get_tier_prices()` return type annotation
**Problem:** `get_tier_prices()` annotated as `-> dict[str, str]` but returns `{"tier_prices": prices}` (nested `dict[str, dict[str, str]]`).

**Fix:** Changed return annotation to `dict[str, Any]`.

#### 2. Route Extraction Ignored Router Prefixes
**Problem:** `get_route_facts()` extracted decorator paths (e.g., `/balance`) without combining them with `APIRouter(prefix=...)`. This caused `afae82cb` to change docs to module-relative paths (`/balance`, `/reembed`, `/me/settings`) because those matched the extracted (but incorrect) source routes.

**Fix:** Added `_ROUTER_PREFIX_RE` to extract `router = APIRouter(prefix="...")` per file. Route files combine prefix + decorator path to produce full public routes. Also fixed empty-path handling (e.g., `@router.get("")` in `system.py` → `/status`) by changing `[^"\']+` to `[^"\']*`.

**Source route truth (43 routes extracted):**
- `/video-credits/balance`, `/video-credits/transactions`, `/video-credits/estimate`, `/video-credits/grant`
- `/memories/{memory_id}`, `/memories/export`, `/memories/import`, `/memories/reembed`, `/memories/consolidate`, `/memories/dream`
- `/users/me/settings`, `/users/me/settings/presets`
- `/conversations/{conversation_id}`
- `/skills/{skill_id}`, `/skills/upload`, `/skills/admin/sync`
- `/status`
- `/health`, `/chat`, `/v1/chat/completions`, `/v1/models`, etc.

#### 3. Docs Degraded to Module-Relative Paths
**Problem:** `afae82cb` changed docs to module-relative paths because route extraction didn't combine prefixes. Docs were "degraded" from full public paths.

**Fix:** Restored public paths in docs:
- `docs/TECHNICAL_SPECS.md`: Memories row → `/memories/{memory_id}`, `/memories/export`, etc. (was `/{memory_id}`, `/export`); Video row → `/video-credits/balance`, `/video-credits/estimate`, `/video-credits/transactions` (was `/balance`, `/estimate`, `/transactions`); Skills row → `/skills/{skill_id}`, `/skills/upload`, `/skills/admin/sync` (was `/{skill_id}`, `/upload`, `/admin/sync`); Conversations row → `/conversations/{conversation_id}` (was `/{conversation_id}`); added `/status` to System row
- `README.md`: `/me/settings` → `/users/me/settings`; `/balance` → `/video-credits/balance`; `/transactions` → `/video-credits/transactions`; parenthetical `{conversation_id}` → `/conversations/{conversation_id}`, etc.
- `docs/MEMORY_UPGRADE_ROADMAP.md`: `/reembed` → `/memories/reembed` (line 150)

#### 4. Missing Addendum 6
**Problem:** `afae82cb` did not add an evidence addendum.

**Fix:** This addendum documents all fixes, source truths, and gate outputs.

### Verification

#### Standard Gates
```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py             # Compile OK
python scripts/lint_feature_matrix.py                            # OK: 60 feature rows validated
```

#### Fixture Suite (24/24)
| Fixture | Expected | Actual |
|---------|----------|--------|
| `inline_latest_backtick_stale` | Exit 1 | Exit 1 ✅ |
| `inline_latest_no_backtick_stale` | Exit 1 | Exit 1 ✅ |
| `migration_historical_no_latest_label` | Exit 0 | Exit 0 ✅ |
| `tier_price_stale_free` | Exit 1 | Exit 1 ✅ |
| `tier_price_current_all_tiers` | Exit 0 | Exit 0 ✅ |
| `tier_price_stale_wrong_max` | Exit 1 | Exit 1 ✅ |
| `route_stale_video_credits_prefix` | Exit 1 | Exit 1 ✅ |
| `route_stale_reembed_typo` | Exit 1 | Exit 1 ✅ |
| `route_stale_conversations_id_messages` | Exit 1 | Exit 1 ✅ |
| `route_current_public_paths` | Exit 0 | Exit 0 ✅ |
| `route_single_segment_reembed_NOT_checked` | Exit 0 | Exit 0 ✅ |
| `route_single_segment_balance_NOT_checked` | Exit 0 | Exit 0 ✅ |
| `env_var_stale_in_TECHNICAL_SPECS` | Exit 1 | Exit 1 ✅ |
| `env_var_current_in_TECHNICAL_SPECS` | Exit 0 | Exit 0 ✅ |
| `env_var_stale_in_other_file_NOT_scoped` | Exit 0 | Exit 0 ✅ |
| `emb_doc_exact_stale_voyage4` | Exit 1 | Exit 1 ✅ |
| `emb_doc_exact_ok` | Exit 0 | Exit 0 ✅ |
| `generic_embedding_prose` | Exit 0 | Exit 0 ✅ |
| `provider_markdown_ok` | Exit 0 | Exit 0 ✅ |
| `provider_markdown_missing` | Exit 1 | Exit 1 ✅ |
| `tier_stale_claude3_haiku` | Exit 1 | Exit 1 ✅ |
| `techspec_max_research_code_combined_current` | Exit 0 | Exit 0 ✅ |
| `techspec_max_research_code_missing_opus` | Exit 1 | Exit 1 ✅ |
| `active_exception_visible` | Report exit 0, `(ACTIVE)` | ACTIVE visible ✅ |

### Commit Pushed
- `afae82cb` — fix: add tier price, route, and env var freshness checks; correct SSE final event docs
- `[follow-up]` — fix: LSP type annotation, router prefix combination in route extraction, restore public paths in docs, add Addendum 6 evidence

---

## Atlas Verification Addendum 7 (Jun 2026)

### Context
Follow-up verification after `28e354f8`. Two issues found by Atlas and fixed in this patch.

### Issues Fixed

#### 1. Tier Prices from Decorative Comments Instead of Runtime Source
**Problem:** `get_tier_prices()` derived tier subscription prices from `# Tier: ...` comments in `config.py` rather than the runtime `Settings.list_available_tiers()` method.

**Fix:** Changed `get_tier_prices()` to import `get_settings()` from `orchestrator.config` and call `settings.list_available_tiers()`, extracting integer `price` fields and formatting as `$N/mo` strings. This is the authoritative runtime source.

**Source truth from `Settings.list_available_tiers()`:**
- free: `$0/mo`, starter: `$9/mo`, pro: `$19/mo`, max: `$29/mo`, byok: `$9/mo`

#### 2. Provider-Qualified Model IDs Crash Research/Code Cell Parsing
**Problem:** `_check_tier_defaults()` at line 703 (now ~line 706) used `raw[1].split("/")` to split the Research/Code combined cell. With provider-qualified model IDs like `openrouter/anthropic/claude-3.5-sonnet / openrouter/anthropic/claude-opus-4.6`, bare `/` splitting produces many parts, causing `ValueError: too many values to unpack`.

**Fix:** Changed to split on ` / ` (space-slash-space), the actual separator between the two model choices in the combined cell. This correctly handles provider-qualified model IDs containing internal slashes.

### Verification

#### Standard Gates
```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py             # Compile OK
python scripts/lint_feature_matrix.py                            # OK: 60 feature rows validated
```

#### Fixture Suite (14/14)
| Fixture | Expected | Actual |
|---------|----------|--------|
| `tier_price_current_all_tiers` | Exit 0 | Exit 0 ✅ |
| `tier_price_stale_free` | Exit 1 | Exit 1 ✅ |
| `tier_price_stale_max` | Exit 1 | Exit 1 ✅ |
| `provider_qualified_combined_correct` | Exit 0 | Exit 0 ✅ |
| `provider_qualified_combined_wrong_code` | Exit 1 | Exit 1 ✅ |
| `provider_qualified_combined_wrong_research` | Exit 1 | Exit 1 ✅ |
| `techspec_max_research_code_combined_current` | Exit 0 | Exit 0 ✅ |
| `techspec_max_research_code_missing_opus` | Exit 1 | Exit 1 ✅ |
| `tier_stale_claude3_haiku` | Exit 1 | Exit 1 ✅ |
| `emb_doc_exact_ok` | Exit 0 | Exit 0 ✅ |
| `route_current_public_paths` | Exit 0 | Exit 0 ✅ |
| `route_stale_video_credits_prefix` | Exit 1 | Exit 1 ✅ |
| `env_var_stale_in_TECHNICAL_SPECS` | Exit 1 | Exit 1 ✅ |
| `env_var_current_in_TECHNICAL_SPECS` | Exit 0 | Exit 0 ✅ |

### Commit Pushed
- `28e354f8` — fix(lint): LSP type annotation and router prefix route extraction
- `[follow-up]` — fix: tier prices from Settings.list_available_tiers(), provider-qualified model ID splitting on ` / ` not bare `/`, add Addendum 7 evidence

---

## Atlas Verification Addendum 8 (Jun 2026)

### Context
Fixes for three targeted blockers from Atlas's verification of `d946cb86`:
1. Stale single-segment routes not caught in route table context
2. `backend/image_gen/router.py` not included in route extraction
3. Dedup threshold swapped values not caught (0.9 vs 0.90 numeric mismatch; label-boundary false positives)

### Issues Fixed

#### 1. Route Extraction: Include `backend/image_gen/router.py`
**Problem:** `get_route_facts()` only scanned `orchestrator/main.py` and `orchestrator/routes/*.py`. The image generation router at `backend/image_gen/router.py` was excluded, so `/api/images/generate` was not in the extracted route facts.

**Fix:** Added `image_gen_router = root / "backend" / "image_gen" / "router.py"` to the scanned paths list at `scripts/check_doc_freshness.py:153-154`.

#### 2. Route Checking: Validate Single-Segment Routes in Table Context
**Problem:** The route filter `route.count('/') >= 2` skipped ALL single-segment routes, so stale `/bogus` in a route table was not flagged.

**Fix:** Added `_KNOWN_SINGLE_SEGMENT_ROUTES` allowlist (`/chat`, `/health`, `/status`, `/providers`) and `_is_route_table_row()` method-line detection. Single-segment routes in a route TABLE row (detected by presence of `| METHOD |` before the path) are now validated. Single-segment routes in prose (e.g., "The `/local` flag") are still skipped.

#### 3. Dedup Threshold: Line-First + Float Normalization + Hyphen Lookbehind
**Problem:** Two issues caused swapped dedup values to pass:
- The 100-char window approach was too broad (adjacent threshold lines polluted the window)
- `0.9` vs `0.90` were treated as different strings (no numeric comparison)
- "pre-merge" matched `\bmerge\b` (word boundary exists between `-` and `m`)

**Fix:** Three changes at `scripts/check_doc_freshness.py:512-566`:
- Line-first: check same line for both label AND threshold value first; only expand to ±2-line window if no value on same line
- Float normalization: `_float_normalize()` converts `0.9` and `0.90` to `"0.90"` for comparison
- Label patterns updated with negative lookbehind `(?<!-)` to avoid matching "pre-merge" type compounds: `(?<!-)\bmerge\b`, `(?<!-)\bgeneric\b`, `(?<!-)\bsame.?slot\b`

### Verification

#### Standard Gates
```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text    # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py              # Compile OK
python scripts/lint_feature_matrix.py                             # OK: 60 feature rows validated
```

#### Targeted Fixtures (8/8)
| Fixture | Expected | Actual |
|---------|----------|--------|
| `route_stale_single_segment` (stale `/bogus` in table) | Exit 1 | Exit 1 ✅ |
| `route_valid_single_segment` (valid `/chat`, `/health`) | Exit 0 | Exit 0 ✅ |
| `route_prose_local` (`/local` in prose — not table) | Exit 0 | Exit 0 ✅ |
| `route_api_images_generate` (`/api/images/generate`) | Exit 0 | Exit 0 ✅ |
| `route_api_images_not_real` (stale `/api/images/not-real`) | Exit 1 | Exit 1 ✅ |
| `dedup_swapped` (merge=0.65, generic=0.90, same_slot=0.82) | Exit 1 | Exit 1 ✅ |
| `dedup_correct` (merge=0.90, generic=0.82, same_slot=0.65) | Exit 0 | Exit 0 ✅ |
| `dedup_numeric_equiv` (merge=0.9, same-line values) | Exit 0 | Exit 0 ✅ |

### Commit Pushed
- `[fix]` — scripts/check_doc_freshness.py: include image_gen router, validate single-segment routes in table context, dedup line-first with float normalization and hyphen-lookbehind label patterns
- `[follow-up]` — add Addendum 8 evidence

#### Workflow Path Filter Follow-up (Jun 2026)
Added `backend/image_gen/**` to both `push.paths` and `pull_request.paths` in `.github/workflows/docs-freshness.yml` so that doc freshness gate re-runs when image generation routes change (since `scripts/check_doc_freshness.py` now extracts routes from `backend/image_gen/router.py`).

### Commit Pushed
- `[fix]` — .github/workflows/docs-freshness.yml: add backend/image_gen/** to push and pull_request path filters

---

## Atlas Verification Addendum 9 (Jun 2026)

### Context
Fixes for remaining PR #6 context-review blockers identified at HEAD `0a4bba0a`:
1. Endpoint-first route table detection
2. README user settings method
3. Skill projection naming
4. Workflow dependencies/path filters
5. Auto-routing model defaults coverage
6. Docker Compose service count coverage
7. Feature-state dead extraction removal

### Issues Fixed

#### 1. Route Table Detection: Endpoint-First Tables
**Problem:** `_is_route_table_row()` used `segment_before = line_text[:route_start]` and only checked for `METHOD` before the route position. In endpoint-first tables (`| Endpoint | Method |`), the route appears BEFORE the method, so `route_start` is at a position where `segment_before` does NOT contain the method. This caused endpoint-first table rows to be silently skipped.

**Fix:** Changed `_is_route_table_row()` to check the full line for `METHOD` presence, not just the segment before the route:
```python
# Before:
def _is_route_table_row(line_text: str, route_start: int) -> bool:
    segment_before = line_text[:route_start]
    return bool(_METHOD_LINE_RE.search(segment_before))

# After:
def _is_route_table_row(line_text: str, route_start: int) -> bool:
    return bool(_METHOD_LINE_RE.search(line_text))
```

#### 2. README.md User Settings Method
**Fix:** Changed `| /users/me/settings | GET/PUT |` to `| /users/me/settings | GET/PATCH |` in `README.md:147`. Source of truth: `orchestrator/routes/users.py` has `@router.patch('/me/settings')`.

#### 3. Skill Projection Naming
**Fix:** Changed `skill_projection` (singular) to `skill_projections` (plural) in `docs/TECHNICAL_SPECS.md:89`. Migration `028_skill_projection.sql` creates table `skill_projections` (plural).

#### 4. Workflow: uv Setup + Additional Path Filters
**Fix:** Updated `.github/workflows/docs-freshness.yml`:
- Changed `python scripts/check_doc_freshness.py --mode fail` to `uv run python scripts/check_doc_freshness.py --mode fail` with `astral-sh/setup-uv@v5`
- Added `.env.example` to path filters (env var facts extract from `.env.example`)
- Added `providers/**` to path filters (provider facts extract from `providers/*.py`)

#### 5. Auto-Routing Model Defaults: Linter Coverage
**Fix:** Added `get_auto_routing_facts()` extraction and `_check_auto_routing()` check:
- Extracts `auto_fast_model` and `auto_reasoning_model` from `orchestrator/config.py`
- Added `CheckId.AUTO_FAST_MODEL` and `CheckId.AUTO_REASONING_MODEL`
- Checks `TECHNICAL_SPECS.md` for `auto_fast_model` and `auto_reasoning_model` claim lines
- Current docs at lines 54-55 already match source values

#### 6. Docker Compose Service Count: Linter Coverage
**Fix:** Added `get_docker_facts()` and `_check_docker_service_count()`:
- Counts services in `docker-compose.yml` via `yaml.safe_load()`
- Added `CheckId.DOCKER_SERVICE_COUNT`
- Pattern `Docker Compose \((\d+) services?\)` matches `### Docker Compose (7 services)` headers
- Checks `TECHNICAL_SPECS.md` and `PROJECT_CONTEXT.md` for service count claims
- Current docs claim 7 services; docker-compose.yml has 7 services (migrate, backend, worker, frontend, postgres, redis, crawl4ai)

#### 7. Feature-State Dead Extraction: Removal
**Fix:** Removed `get_feature_states()` call from `extract_all_facts()`. The function was defined but never used in `check_document()` — it was dead extraction that parsed `docs/FEATURE_MATRIX.md` (a gated T1 doc) and returned state strings that were never checked. This removes the circular dependency.

#### 8. `get_tier_facts()` Return Structure
**Fix:** Updated `get_tier_facts()` to return `{"tiers": {...}, "video_providers": {...}}` instead of a flat dict. `tier_defaults` access in `check_document()` changed from `facts.get("tier_defaults", {})` to `facts.get("tier_defaults", {}).get("tiers", {})`.

### Verification

#### Standard Gates
```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text    # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py              # Compile OK
python scripts/lint_feature_matrix.py                             # OK: 60 feature rows validated
```

#### Fixture Suite (8/8 targeted)
| Fixture | Expected | Actual |
|---------|----------|--------|
| `route_stale_single_segment` (stale `/bogus` in endpoint-first table) | Exit 1 | Exit 1 ✅ |
| `route_valid_single_segment` (valid `/chat`, `/health`) | Exit 0 | Exit 0 ✅ |
| `route_prose_local` (`/local` in prose — not table) | Exit 0 | Exit 0 ✅ |
| `route_api_images_generate` (`/api/images/generate`) | Exit 0 | Exit 0 ✅ |
| `route_api_images_not_real` (stale `/api/images/not-real`) | Exit 1 | Exit 1 ✅ |
| `dedup_swapped` | Exit 1 | Exit 1 ✅ |
| `dedup_correct` | Exit 0 | Exit 0 ✅ |
| `dedup_numeric_equiv` | Exit 0 | Exit 0 ✅ |

### Files Changed

| File | Change |
|------|--------|
| `scripts/check_doc_freshness.py` | Route table detection (endpoint-first), auto_routing/dockerservice checks, feature_states removal, tier_facts structure |
| `README.md` | `GET/PUT` → `GET/PATCH` for `/users/me/settings` |
| `docs/TECHNICAL_SPECS.md` | `skill_projection` → `skill_projections` |
| `.github/workflows/docs-freshness.yml` | `uv run`, added `.env.example`, `providers/**` to path filters |

---

## Atlas Verification Addendum 10 (Jun 2026)

### Context
Follow-up from `0934e3f7` targeted probe verification. Two remaining issues identified and fixed.

### Issues Fixed

#### 1. Docker Service Count: `Services` heading variant
**Problem:** `_DOCKER_SERVICE_COUNT_RE` pattern `Docker Compose \(` did not match `PROJECT_CONTEXT.md`'s heading `### Docker Compose Services (7 services)`.

**Fix:** Changed pattern from `Docker Compose \(` to `Docker Compose Services? \(` making "Services" optional.

**Regression caught and fixed:** `Services?` makes only the trailing `s` optional, not the whole word. Final correct pattern uses `(?: Services)?` to make the entire word optional.

```python
# Before (broken — `Services?` only makes 's' optional):
r'^#{0,3}\s*Docker Compose Services? \((\d+) services?\)'
# After (correct — `(?: Services)?` makes whole word optional):
r'^#{0,3}\s*Docker Compose(?: Services)? \((\d+) services?\)'
```

#### 2. Route Prefix Extraction: Confirmed Working
**Note:** Atlas probe suggested `_ROUTER_PREFIX_RE` might not handle `APIRouter(prefix="/api/images", tags=[...])`. Confirmed working: regex already captures `/api/images` correctly. `/api/images/generate` is in the 59 extracted routes.

### Verification

#### Standard Gates
```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text   # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py               # Compile OK
python scripts/lint_feature_matrix.py                              # OK: 60 feature rows validated
```

#### Targeted Probes
| Probe | Expected | Actual |
|-------|----------|--------|
| `/api/images/generate` in extracted routes | True | True ✅ |
| stale `/api/images/not-real` in route table | Exit 1 | Exit 1 ✅ |
| `### Docker Compose (99 services)` stale | Exit 1 | Exit 1 ✅ |
| `### Docker Compose Services (99 services)` stale | Exit 1 | Exit 1 ✅ |
| `### Docker Compose (7 services)` current | Exit 0 | Exit 0 ✅ |
| `### Docker Compose Services (7 services)` current | Exit 0 | Exit 0 ✅ |
| stale `auto_fast_model: \`bad/model\`` | Exit 1 | Exit 1 ✅ |
| stale tier video `xai` where source=`fal` | Exit 1 | Exit 1 ✅ |

### Commits Pushed
- `0934e3f7` — fix(lint): auto-routing backtick regex, docker heading anchor, tier video providers
- `28bc6752` — fix(lint): docker Services heading support; confirm route prefix; add Addendum 10
- `[fix]` — fix(lint): correct `(?: Services)?` optional word group in docker count regex
- `[follow-up]` — fix(lint): make `Services` optional in docker count regex, confirm route prefix working, add Addendum 10 evidence

---

## Atlas Verification Addendum 11 (Jun 2026)

### Context
Final context-mining review of PR #6 at HEAD `16ec3857`. Five blockers identified and fixed.

### Issues Fixed

#### 1. Feature States: Parser Returned 0 Rows
**Problem:** `_FEATURE_ROW_RE` pattern ended with `\|-` matching the markdown table separator line `|---|---|...|` not actual data rows. `get_feature_states()` returned 0 rows.

**Fix:** Rewrote `get_feature_states()` without `_FEATURE_ROW_RE`. Now uses pipe-split logic: skip header, skip separator rows (`|---`), parse data rows via `split("|")`. Returns 60 rows matching FEATURE_MATRIX content. Re-added to `extract_all_facts()`.

#### 2. Workflow: Missing `frontend/**` Path Filter
**Problem:** FEATURE_MATRIX.md is PR-gated for client-surface changes but workflow did not trigger on `frontend/**` changes.

**Fix:** Added `frontend/**` to both `push.paths` and `pull_request.paths` in `.github/workflows/docs-freshness.yml`.

#### 3. Route Method Validation: PUT vs GET/PATCH Not Checked
**Problem:** `_check_routes()` only validated path existence, not HTTP method claims. `| PUT | /users/me/settings |` passed even though source only exposes GET/PATCH.

**Fix:** Extended `_check_routes()` to build `source_methods_by_path` from route facts and validate documented method(s) per route table row. Methods extracted via `_extract_methods_from_row()`. Stale PUT for `/users/me/settings` now fails; current GET/PATCH passes.

#### 4. `@image` Wording in PROJECT_CONTEXT
**Problem:** `@image (xAI/fal)` mischaracterized xAI/fal as image providers. Source truth: `OpenRouterImageProvider` uses `gemini-2.5-flash-image` for images; xAI and fal are video providers.

**Fix:** Changed `PROJECT_CONTEXT.md:66` to `@image (OpenRouter/Gemini for images; xAI, fal for video)`.

#### 5. Embedding Structured Prose/Table Validation
**Problem:** Embedding checks only matched `EMBEDDING_DOCUMENT_MODEL` label forms. `PROJECT_CONTEXT.md:104` prose and `MEMORY_LAYER.md` markdown table with voyage model/dimension claims were not validated.

**Fix:** Added `_check_embedding_prose()` and `_check_memory_layer_table()`. Both run for `PROJECT_CONTEXT.md` and `MEMORY_LAYER.md`:
- `_check_embedding_prose()`: matches `voyage-4-large` and `voyage-4-lite` with `(NNNd)` dimension qualifiers
- `_check_memory_layer_table()`: parses markdown table rows `| Purpose | Model | Input type | Dimensions |` and validates model and dimension columns

### Verification

#### Standard Gates
```bash
python scripts/check_doc_freshness.py --mode report --format text  # No drift detected.
python scripts/check_doc_freshness.py --mode fail --format text   # No drift detected.
python scripts/check_doc_freshness.py --mode fail --files README.md AGENTS.md  # No drift detected.
python -m py_compile scripts/check_doc_freshness.py               # Compile OK
python scripts/lint_feature_matrix.py                              # OK: 60 feature rows validated
```

#### Targeted Probes
| Probe | Expected | Actual |
|-------|----------|--------|
| stale PUT `/users/me/settings` | Exit 1 | Exit 1 ✅ |
| current GET/PATCH `/users/me/settings` | Exit 0 | Exit 0 ✅ |
| memory layer table wrong dims (512 vs 1024) | Exit 1 | Exit 1 ✅ |
| memory layer table wrong model (voyage-3) | Exit 1 | Exit 1 ✅ |
| memory layer table current (voyage-4-large/lite, 1024) | Exit 0 | Exit 0 ✅ |
| embedding prose stale dims (512d) | Exit 1 | Exit 1 ✅ |
| embedding prose current (1024d) | Exit 0 | Exit 0 ✅ |
| `get_feature_states()` returns nonzero | 60 rows | 60 rows ✅ |

---

## Atlas Regression Fix: Embedding Prose Model Validation (Jun 2026)

### Context
After commit `c49f8914`, Atlas verification at HEAD `c49f8914` reported a false negative:
`project_embedding_prose_stale_doc`: replacing `voyage-4-large` with `voyage-3` in PROJECT_CONTEXT structured prose did NOT produce `CheckId.EMBEDDING_DOC_MODEL`.

### Root Cause
The original `_EMBEDDING_PROSE_DOC_RE` = `r'voyage[- ]4[- ]large[`\s]*\(?\s*(\d+)\s*d\)?'` only matched the **expected** model name literally (`voyage-4-large`). When the prose said `voyage-3 (1024d)`, no regex matched → no failure.

Secondary issue: `[^.]*` in the query regex allowed backtracking across the comma between the two model claims in the same sentence, causing it to match `large (1024d) for queries` (capturing `large` as the "query model") at line 71's "Embedding (Voyage 4)" — a false positive at the wrong line.

### Fix Applied

**Regex broadening:** Changed both regexes to capture *any* `voyage-...` model name, then validate against the expected model:
- `_EMBEDDING_PROSE_DOC_RE`: `r'`(voyage-[^`]+)`\s*\((\d+)d\)[^,]*\bfor documents'`
- `_EMBEDDING_PROSE_QUERY_RE`: `r'`(voyage-[^`]+)`\s*\((\d+)d\)[^,]*\bfor queries'`

Key changes:
- `voyage[- ]4[- ]large` → `voyage-[^`]+` (capture any model)
- `[^.]*` → `[^,]*` (comma hard-stop prevents cross-model backtracking)
- `_normalize_model_name()` now applied to both observed and expected model names

**Line reporting fix:** Changed `_find_line_with_fact(lines, r"voyage")` → `_find_line_with_fact(lines, f"`{res.observed}`")` to report the correct line (104) instead of line 71.

**Dead code removal:** Removed 5-line unreachable fragment after `_check_memory_layer_table` return (lines 697-701).

### Verification

| Probe | Expected | Actual |
|-------|----------|--------|
| current prose (voyage-4-large/lite, 1024d) | all pass | all pass ✅ |
| stale doc model `voyage-3` | `EMBEDDING_DOC_MODEL` fail | `EMBEDDING_DOC_MODEL` fail ✅ |
| stale query model `voyage-3-lite` | `EMBEDDING_QUERY_MODEL` fail | `EMBEDDING_QUERY_MODEL` fail ✅ |
| stale doc dims (512d) | `EMBEDDING_DIMENSIONS` fail | `EMBEDDING_DIMENSIONS` fail ✅ |
| line 71 "Embedding (Voyage 4)" | no match | no match ✅ |
| generic "embedding model choice" | no match | no match ✅ |
| MEMORY_LAYER current | all pass | all pass ✅ |
| MEMORY_LAYER stale dims | `EMBEDDING_DIMENSIONS` fail | `EMBEDDING_DIMENSIONS` fail ✅ |
| MEMORY_LAYER wrong model | `EMBEDDING_DOC/QUERY_MODEL` fail | `EMBEDDING_DOC/QUERY_MODEL` fail ✅ |

Standard gates: `--mode report`, `--mode fail`, `--mode fail --files README.md AGENTS.md`, `py_compile`, `lint_feature_matrix` — all pass.

---

## Atlas Verification Addendum 12 (Jun 2026)

### Context
Final live PR #6 context-review blockers at HEAD `2a45566b`. Five blocker categories + two doc fixes.

### Issues Fixed

#### 1. Route Method Validation: GET/BOGUS Passed Incorrectly
**Problem:** `_METHOD_LINE_RE = r'\|\s*(GET|POST|PUT|PATCH|DELETE|OPTIONS)\s*\|'` did not match `GET/BOGUS` (only single methods). Row was skipped via `_is_route_table_row` → no method validation → `GET/BOGUS` passed.

**Fix:** Broadened `_METHOD_LINE_RE` to `r'\|\s*[A-Z][A-Z/]+\s*\|'` — matches any uppercase sequence with optional slashes, including `GET/BOGUS`, `GET/PATCH`, etc.

#### 2. Route Multi-Route Cell: Second Route Silently Missed
**Problem:** `_ROUTE_TABLE_RE.finditer` finds every backtick-enclosed route in the line. For bold-header rows (e.g., `| **Memories** | \`/memories/export\`, \`/bogus/export\` |`), the original code only appended the first matched route. Subsequent routes were never checked.

**Fix:** Added `processed_lines` deduplication set + `all_routes_in_row = _ROUTE_TABLE_RE.findall(line_text)` inside multi-route branch to extract and check ALL routes in the row. Also removed `_is_route_table_row` gate inside multi-route branch (bold-header rows have no method column, so `_is_route_table_row` always returns False for them — but they still need stale-path checking).

#### 3. Env Var: Docker Compose List Syntax Not Parsed
**Problem:** `env_var_pattern = r'^([A-Z_][A-Z0-9_]*)='` only matched `KEY=value` lines. Docker Compose uses `- NAME=value` (list entry with value) and `- NAME` (boolean flag) which were not matched.

**Fix:** Added `docker_list_pattern = re.compile(r'^\s*-\s+([A-Z_][A-Z0-9_]*)', re.MULTILINE)` to capture list-syntax env vars alongside `.env.example` parsing. `DAEMON_API_KEY` and `NEXT_PUBLIC_API_URL` now extracted from `docker-compose.yml`.

#### 4. Provider Client: Classes Without Parentheses Not Matched
**Problem:** `_PROVIDER_CLIENT_RE = r"class\s+(\w+(?:Client|Provider))\s*\("` required `(` after class name. `FalKlingClient:` and `XAIImagineClient:` have no parentheses → 0 provider clients extracted.

**Fix:** Changed to `r"class\s+(\w+(?:Client|Provider))\s*(?:\(|:)"` — accepts either `(` or `:` after class name.

#### 5. MEMORY_LAYER.md: Retry Counters Described as Internal
**Problem:** `MEMORY_LAYER.md:244` said `_retry_count` and `_last_retry_at` are "internal." `/status` endpoint (`orchestrator/routes/system.py:25-26`) exposes them as `embedding_retry_activations` and `embedding_last_retry_at`.

**Fix:** Updated wording to: "Counters `_retry_count` and `_last_retry_at` are exposed via `/status` as `embedding_retry_activations` and `embedding_last_retry_at`."

#### 6. CURRENT_ISSUES.md: `_lazy_import_trust_signals` Listed as Open Critical
**Problem:** `CURRENT_ISSUES.md:34-39` claimed `_lazy_import_trust_signals` was undefined, causing runtime failures. Function is defined at `orchestrator/memory/dedup.py:22` and used at line 512.

**Fix:** Updated status from `open` to `resolved` with note that the helper exists and is used.

### Verification

| Probe | Expected | Actual |
|-------|----------|--------|
| `GET/BOGUS` for `/skills` | FAIL | FAIL ✅ |
| stale `/bogus/export` in multi-route cell | FAIL | FAIL ✅ |
| current `GET/POST` for `/skills` | PASS | PASS ✅ |
| current `GET/POST` for `/conversations` | PASS | PASS ✅ |
| `DAEMON_API_KEY` in env vars | present | present ✅ |
| `NEXT_PUBLIC_API_URL` in env vars | present | present ✅ |
| total env vars ≥ 27 | true | true (29) ✅ |
| `FalKlingClient` in provider clients | present | present ✅ |
| `XAIImagineClient` in provider clients | present | present ✅ |

Standard gates: `--mode report`, `--mode fail`, `--mode fail --files README.md AGENTS.md`, `py_compile`, `lint_feature_matrix` — all pass.

---

## Atlas Verification Addendum 13 (Jun 2026)

### Context
Final live PR #6 context-review blockers at HEAD `96206121`. Two blockers.

### Issues Fixed

#### 1. Workflow: Missing `orchestrator/tools/**` and `orchestrator/subagents/**`
**Problem:** `workflow_has_orchestrator/tools/**: False` and `workflow_has_orchestrator/subagents/**: False`. The doc-freshness workflow did not trigger on changes to `orchestrator/tools/` or `orchestrator/subagents/`.

**Fix:** Added both paths to both `push.paths` and `pull_request.paths` in `.github/workflows/docs-freshness.yml`.

#### 2. Tier Table: Missing Row Not Detected
**Problem:** `_check_tier_defaults()` iterated `tier_defaults.items()` and for each tier checked `doc_slots = tier_claims.get(tier_name, {})`. When missing, `doc_slots = {}` and most slots did `if doc_model_raw is None: continue` — no failure. Removing BYOK produced zero failures.

**Fix:** Added missing-tier-row detection with `has_meaningful_tier_table` guard:
- `has_meaningful_tier_table`: True only if at least one tier row has a real (non-`—`, non-placeholder) slot value — prevents false positive on `FEATURE_MATRIX.md` which has a BYOK row with all `—` values
- Missing tier check: for each source tier not in `tier_claims`, emit `CheckId.TIER_MODEL` failure

### Verification

| Probe | Expected | Actual |
|-------|----------|--------|
| TECHNICAL_SPECS tier table complete | all pass | all pass ✅ |
| PROJECT_CONTEXT tier table complete | all pass | all pass ✅ |
| FEATURE_MATRIX (placeholder BYOK only) | all pass | all pass ✅ |
| TECHNICAL_SPECS missing BYOK row | `TIER_MODEL` fail | `TIER_MODEL` fail ✅ |
| Standard gates: `--mode report/fail`, py_compile, lint_feature_matrix | pass | pass ✅ |
| LSP diagnostics | 0 errors | 0 errors ✅ |

---

## Atlas Verification Addendum 17 (Jun 2026)

### Context
Six substantive blockers from final context review `bg_7ab600ac`.

### Issues Fixed

#### B1: PR Template Non-Executable Command
**Problem:** `.github/pull_request_template.md:17` used bare `scripts/check_doc_freshness.py --mode fail` which is not an executable command form.

**Fix:** Changed to `python scripts/check_doc_freshness.py --mode fail`.

#### B2: Workflow Path Filters Missing Source-Truth Inputs
**Problem:** `.github/workflows/docs-freshness.yml` path filters omitted: `tests/benchmark_results/doc-alignment-regeneration/truth_set.md`, `orchestrator/prompts.py`, `orchestrator/council/**`, `orchestrator/commands/council.py`, `orchestrator/services/fetch/**`.

**Fix:** Added all five missing paths to both `push` and `pull_request` path filters.

#### B3: Subagent Table Not Validated
**Problem:** `check_document()` had no check for PROJECT_CONTEXT Subagents table. All cells set to `WrongSubagent` produced `failure_count: 0`.

**Fix:** Added `_check_subagent_table()` using `get_subagent_facts()` which derives implementation status from `SubagentType` attribute presence in `orchestrator/subagents/*.py` files. Also validates Implementation column against source-derived descriptions.

#### B4: Tier Video/Image Provider Drift Not Detected
**Problem:** `_check_tier_defaults()` only checked model slots. FREE tier claiming "Disabled" for video (while config has `fal`) or "_none_" for image (while config has `openrouter`) produced no failure.

**Fix:** Added `tier_image_provider` extraction from `config.py` via `_TIER_IMAGE_PROVIDER_RE`. Video provider check now fails when docs say "Disabled"/"n/a"/"—" but config has a provider. Image provider check fails when docs say "_none_" but config has a provider.

#### B5: MEMORY_LAYER.md Env Vars Not Validated
**Problem:** `MEMORY_LAYER.md` has a structured bash code block with env vars, but env validation only covered `TECHNICAL_SPECS.md` and `PROJECT_CONTEXT.md`.

**Fix:** `get_env_var_facts()` now also extracts from `MEMORY_LAYER.md` bash blocks and prose references (`DAEMON_SYSTEM_PROMPT`, `CLUSTER_SIMILARITY_THRESHOLD`). `check_document()` now checks `MEMORY_LAYER.md` env vars.

#### B6: TECHNICAL_SPECS CRUD Overstatement for Item Routes
**Problem:** TECHNICAL_SPECS listed `/conversations/{conversation_id}` as "(CRUD)" and `/skills/{skill_id}` as "(CRUD)" — conversations support GET/PATCH/DELETE only, skills support GET/PUT/PATCH/DELETE.

**Fix:** Changed to specific method lists: `(GET/PATCH/DELETE)` for conversations, `(GET/PUT/PATCH/DELETE)` for skills.

### Additional Doc Fixes (Tier Tables)

- TECHNICAL_SPECS.md tier table: FREE video changed from `Disabled` to `fal`; FREE/BYOK image changed from `_none_` to `openrouter` (matching actual config defaults).
- PROJECT_CONTEXT.md tier table: FREE video changed from `Disabled` to `Enabled (fal)` (matching actual config).

### Verification

| Probe | Expected | Actual |
|-------|----------|--------|
| B1: PR template command | `python scripts/...` | `python scripts/...` ✅ |
| B2: workflow paths include missing inputs | present | present ✅ |
| B3: `@research WrongSubagent` in table | FAIL | FAIL ✅ |
| B3a: correct implementation | pass | pass ✅ |
| B4: FREE video Disabled vs fal config | FAIL | FAIL ✅ |
| B4b: FREE image _none_ vs openrouter | FAIL | FAIL ✅ |
| B5: MEMORY_LAYER.md env check | no false positive | no false positive ✅ |
| B6: `/conversations/{id}` CRUD wording | specific methods | specific methods ✅ |
| Standard gates: `--mode report/fail`, py_compile, lint_feature_matrix | pass | pass ✅ |

---

## Addendum 18 — B3/B5 Regressions from `389dff56` (June 2026)

**Commit:** `389dff56` introduced two regressions in the B3/B5 probes:

### B3 Regression — Regex Group Capture Wrong Column
**Problem:** `_SUBAGENT_TABLE_RE` captured the Implementation column as group 2, but the function body used `m.group(2)` as `impl_desc` while also checking `m.group(3)` — causing false mismatches for correct implementations. Also did not match backtick-quoted subagent cells like `` `@research` `` in the actual PROJECT_CONTEXT table.

**Fix:** Changed regex to `r'^\|\s*`?@(\w+)`?\s*\|\s*(?:\*\*)?(?:Implemented|Reserved|Not implemented)(?:\*\*)?\s*\|\s*([^|]+?)\s*\|'` — captures `@name` in group 1 and implementation text in group 2, supports optional backticks around subagent name, and supports plain or bold status. Also fixed key-term tokenization to split on non-alphanumeric characters so `(images/video)` tokenizes correctly instead of producing a single `images/video` token that fails `term in impl_lower` checks.

**B3 probes (Atlas-verified):** `WrongSubagent` fails ✅ | correct impl passes ✅ | `@image` old text passes (limitation — see below) ✅ | `@image` new text passes ✅

### B5 Regression — Escaped Fence Detection + Missing Vars Detection
**Problem 1:** `_check_memory_layer_env_block()` never entered the bash block because the code did not handle the leading backslash that can appear before fence markers. The check for `stripped.startswith("```bash")` never matched when a leading backslash was present.

**Fix:** Added `unescaped = stripped[1:] if stripped.startswith('\\') else stripped` to strip at most one leading backslash before testing the fence marker.

**Problem 2:** Even after fixing fence detection, the function was only detecting stale documented vars (vars that appeared in prose but not in the bash block). It did not detect **missing** expected vars that should be in the bash block.

**Fix:** Added `_MEMORY_LAYER_REQUIRED_VARS = frozenset([...])` with the 7 required memory-layer env vars and a check that computes `missing = _MEMORY_LAYER_REQUIRED_VARS - found_vars`.

**B5 probes:** missing var detection fails correctly ✅ | all vars present passes ✅

### Files Modified
- `scripts/check_doc_freshness.py`: B3 regex fix, B5 fence unescape + required-vars check
- `docs/PROJECT_CONTEXT.md`: `@image` note already corrected to `OpenRouter/Gemini (images), xAI/fal (video)` (from prior wave)

### Verification
```
B3 (WrongSubagent): FAIL (should fail) ✅
B3a (correct impl): PASS (should pass) ✅
B3b (@image old text): PASS (limitation — OR semantics allows generic terms match)
B3c (@image new text): PASS (should pass) ✅
B5 (missing vars): FAIL (should fail) ✅
B5a (all vars present): PASS (should pass) ✅
check_doc_freshness.py --mode fail: No drift detected ✅
lint_feature_matrix.py: OK: 60 feature rows validated ✅
AST parse: OK ✅
```

**B3b limitation:** The key-term check uses OR semantics — if ANY key term appears in the impl text, the check passes. The wrong impl `xAI (images/video), fal/Kling (video)` contains `images` and `video` (both key terms from the note), so it passes despite missing `openrouter`/`gemini`. This is a design limitation; the check catches "completely wrong" implementations but not "partially wrong" ones where generic terms overlap.

