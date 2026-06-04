# Task 17 Amendments — Applied Evidence

## Amendment Summary

Four concrete amendments from Oracle gate review (lines 148-153) were applied to `scripts/check_doc_freshness.py`:

### Amendment 1: Source-Derived Provider Validation

**Before:**
```python
_KNOWN_REMOVED_PROVIDERS = frozenset(["sora"])

def _check_video_providers(doc_content: str) -> CheckResult:
    for removed in _KNOWN_REMOVED_PROVIDERS:
        if removed in doc_content.lower():
            return CheckResult(CheckId.VIDEO_PROVIDERS, False, "no Sora (deleted)",
                             f"Sora is deleted and should not be mentioned")
    return CheckResult(CheckId.VIDEO_PROVIDERS, True)
```

**After (second implementation — Atlas-verified):**
```python
def _check_video_providers(doc_content: str, valid_providers: frozenset[str]) -> CheckResult:
    # Singular forms: capture exactly one provider name after "provider:" or "video provider:"
    singular_patterns = [
        (r'\bprovider:\s*([a-z]+)',),
        (r'\bvideo\s+provider:\s*([a-z]+)',),
    ]
    # Plural/list forms: capture raw comma-separated names or brace-enclosed set
    plural_patterns = [
        (r'\bproviders:\s*([a-z, ]+)',),
        (r'\bvideo\s+providers:\s*([a-z, ]+)',),
        (r'\bVALID_VIDEO_PROVIDERS\s*=\s*\{([^}]+)\}',),
    ]

    singular_claims: set[str] = set()
    plural_claims: set[str] = set()

    for pat, in singular_patterns:
        for m in re.finditer(pat, doc_content, re.IGNORECASE):
            if m.lastindex and m.lastindex >= 1:
                singular_claims.add(m.group(1).lower())

    for pat, in plural_patterns:
        for m in re.finditer(pat, doc_content, re.IGNORECASE):
            if m.lastindex and m.lastindex >= 1:
                raw = m.group(1)
                names = re.findall(r'["\']?([a-z]+)["\']?', raw, re.IGNORECASE)
                plural_claims.update(n.lower() for n in names)

    # Singular claims: validate each is in the valid set
    for claim in singular_claims:
        if claim not in valid_providers:
            valid_list = ", ".join(sorted(valid_providers))
            return CheckResult(
                CheckId.VIDEO_PROVIDERS, False,
                f"valid providers: {valid_list}",
                f"invalid: {claim}",
                f"video provider '{claim}' is not in the valid provider set",
            )

    # Plural/list claims: exact set comparison (unsupported AND missing)
    if plural_claims:
        unsupported = plural_claims - valid_providers
        missing = valid_providers - plural_claims
        if unsupported or missing:
            valid_list = ", ".join(sorted(valid_providers))
            claimed_list = ", ".join(sorted(plural_claims))
            parts = []
            if unsupported:
                parts.append(f"unsupported: {', '.join(sorted(unsupported))}")
            if missing:
                parts.append(f"missing: {', '.join(sorted(missing))}")
            detail = "; ".join(parts)
            return CheckResult(
                CheckId.VIDEO_PROVIDERS, False,
                f"providers in {valid_list}",
                f"claimed {claimed_list} ({detail})",
                f"video provider set mismatch",
            )

    return CheckResult(CheckId.VIDEO_PROVIDERS, True)
```

**Key distinction**: Singular (`provider: xai`) validates one provider is in the valid set. Plural (`providers: xai, kling`, `VALID_VIDEO_PROVIDERS = {...}`) requires exact set match — no unsupported AND no missing.

**Atlas fixtures verified:**
- `video providers: xai, kling` → exit 1 (unsupported kling + missing fal)
- `video providers: xai` → exit 1 (missing fal)
- `VALID_VIDEO_PROVIDERS = {"xai", "kling"}` → exit 1 (unsupported kling + missing fal)
- `provider: xai` → exit 0 (singular, xai is valid)
- `provider: kling` → exit 1 (singular, kling is invalid)

**Call site updated** (line ~435):
```python
res = _check_video_providers(text, frozenset(facts["providers"]["video_providers"]))
```

### Amendment 2: Linter Scope Alignment

**Before (docstring claimed checks for):**
```
  - migration_count / migration_latest
  - embedding_models / embedding_dimensions
  - dedup_thresholds
  - provider_names
  - route_names
  - feature_states
  - env_var_names
```

**After (docstring now honestly lists active checks):**
```
  - migration_count / migration_latest
  - embedding_document_model
  - dedup_thresholds (merge, supersede_generic, supersede_same_slot)
  - video_providers (source-derived from VALID_VIDEO_PROVIDERS)
```

### Amendment 3: Report Mode Visibility Fix

**Before (lines 581-584):**
```python
    if all_findings:
        print(format_text(all_findings, all_exceptions, all_malformed))
    else:
        print("No drift detected.")
```

**After:**
```python
    if all_findings or all_malformed:
        print(format_text(all_findings, all_exceptions, all_malformed))
    else:
        print("No drift detected.")
```

This ensures malformed exceptions and missing explicitly requested files are printed in report mode even when there are zero drift findings.

## Commands Run

### Verification on aligned docs:
```bash
python scripts/check_doc_freshness.py --mode report
# Output: No drift detected.
# EXIT_CODE: 0

python scripts/check_doc_freshness.py --mode fail
# Output: No drift detected.
# EXIT_CODE: 0
```

### Provider validation fixture tests (see task-17-provider-validation.md):
```bash
python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-amendments/fixture-stale-provider.md
python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-amendments/fixture-stale-provider.md
python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-amendments/fixture-aligned-provider.md
```

### Report mode malformed/missing fixture tests (see task-17-report-mode.md):
```bash
python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-amendments/fixture-malformed-exception.md
python scripts/check_doc_freshness.py --mode report --files /tmp/opencode/doc-freshness-amendments/fixture-nonexistent.md
python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-amendments/fixture-malformed-exception.md
```

## Fixture Files Created (under /tmp/opencode/doc-freshness-amendments/)

- `fixture-stale-provider.md` — Claims "video provider is kling" (unsupported)
- `fixture-aligned-provider.md` — Claims "video provider is xai" (valid)
- `fixture-malformed-exception.md` — Contains malformed DOC_FRESHNESS_EXCEPTION with empty reason
- `fixture-no-issues.md` — Clean file with no issues
- `fixture-nonexistent.md` — Referenced but does not exist on disk (for missing-file test)

## Files Modified

- `scripts/check_doc_freshness.py` — Three amendments applied

## Files NOT Modified (as required)

- `.sisyphus/plans/doc-alignment-regeneration.md` — Read only
- `TRIAGE.md` — Not modified
- `.pre-commit-config.yaml` — Not modified
- GitHub Actions — Not modified
