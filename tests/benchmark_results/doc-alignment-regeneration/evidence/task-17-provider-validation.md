# Task 17 — Provider Validation Evidence

Source: `VALID_VIDEO_PROVIDERS = {"xai", "fal"}` extracted from `orchestrator/routes/video_credits.py:158`.

## Source-Derived Provider Set (JSON report)

```json
{
  "providers": {
    "video_providers": [
      "fal",
      "xai"
    ]
  }
}
```

## Provider Check Logic

The linter distinguishes singular from plural structured claims:
- **Singular** (`provider: xai`, `video provider: xai`): validates the claimed provider is in the valid set. Does NOT require the full set.
- **Plural/list** (`providers: xai`, `video providers: xai`, `VALID_VIDEO_PROVIDERS = {...}`): requires exact set match — no unsupported providers AND no missing required ones.

## Atlas Edge Case Fixtures

### Fixture: Plural list with invalid provider — `video providers: xai, kling`

**File:** `/tmp/opencode/doc-freshness-atlas-check/provider-list-invalid.md`
```
# Provider fixture

video providers: xai, kling
```

**Fail Mode:**
```
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-atlas-check/provider-list-invalid.md
/tmp/opencode/doc-freshness-atlas-check/provider-list-invalid.md:1 [CheckId.VIDEO_PROVIDERS] expected='providers in fal, xai' observed='claimed kling, xai (unsupported: kling; missing: fal)'  video provider set mismatch
EXIT=1
```

**Passes:** ✅ Correctly detects unsupported `kling` AND missing `fal` in plural list claim.

---

### Fixture: Plural list with omission — `video providers: xai`

**File:** `/tmp/opencode/doc-freshness-atlas-check/provider-list-omission.md`
```
# Provider fixture

video providers: xai
```

**Fail Mode:**
```
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-atlas-check/provider-list-omission.md
/tmp/opencode/doc-freshness-atlas-check/provider-list-omission.md:1 [CheckId.VIDEO_PROVIDERS] expected='providers in fal, xai' observed='claimed xai (missing: fal)'  video provider set mismatch
EXIT=1
```

**Passes:** ✅ Correctly detects missing `fal` in plural list claim (xai alone is not the full set).

---

### Fixture: INVALID set literal — `VALID_VIDEO_PROVIDERS = {"xai", "kling"}`

**File:** `/tmp/opencode/doc-freshness-atlas-check/provider-set-invalid.md`
```
# Provider fixture

VALID_VIDEO_PROVIDERS = {"xai", "kling"}
```

**Fail Mode:**
```
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-atlas-check/provider-set-invalid.md
/tmp/opencode/doc-freshness-atlas-check/provider-set-invalid.md:1 [CheckId.VIDEO_PROVIDERS] expected='providers in fal, xai' observed='claimed kling, xai (unsupported: kling; missing: fal)'  video provider set mismatch
EXIT=1
```

**Passes:** ✅ Correctly detects unsupported `kling` AND missing `fal` in set literal.

---

### Fixture: Singular valid — `provider: xai`

**File:** `/tmp/opencode/doc-freshness-atlas-check/provider-singular-valid.md`
```
# Provider

provider: xai
```

**Fail Mode:**
```
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-atlas-check/provider-singular-valid.md
No drift detected.
SINGULAR_VALID_EXIT=0
```

**Passes:** ✅ Singular claim accepts any single valid provider.

---

### Fixture: Singular invalid — `provider: kling`

**File:** `/tmp/opencode/doc-freshness-atlas-check/provider-singular-invalid.md`
```
# Provider

provider: kling
```

**Fail Mode:**
```
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-atlas-check/provider-singular-invalid.md
/tmp/opencode/doc-freshness-atlas-check/provider-singular-invalid.md:1 [CheckId.VIDEO_PROVIDERS] expected='valid providers: fal, xai' observed='invalid: kling'  video provider 'kling' is not in the valid provider set
SINGULAR_INVALID_EXIT=1
```

**Passes:** ✅ Singular claim rejects an unsupported provider.

---

## Original Fixtures (amendments session)

### Fixture: Stale/Unsupported Provider (kling)

**File:** `/tmp/opencode/doc-freshness-amendments/fixture-stale-provider.md`
```
# Video Provider: kling

The video provider is kling (via fal.ai).
```

**Fail Mode:**
```
$ python scripts/check_doc_freshness.py --mode fail --files /tmp/opencode/doc-freshness-amendments/fixture-stale-provider.md
... [CheckId.VIDEO_PROVIDERS] expected='providers in fal, xai' observed='invalid: kling' ...
EXIT_CODE: 1
```

### Fixture: Aligned/Valid Provider (xai)

**File:** `/tmp/opencode/doc-freshness-amendments/fixture-aligned-provider.md`
```
# Video Provider: xai

The video provider is xai.
```

**Fail Mode:** No drift detected. EXIT_CODE: 0.

---

## Summary

| Fixture | Claim | Plural? | Unsupported | Missing | Fail Exit |
|---------|-------|---------|-------------|---------|-----------|
| provider-list-invalid | `video providers: xai, kling` | ✅ | kling | fal | 1 |
| provider-list-omission | `video providers: xai` | ✅ | — | fal | 1 |
| provider-set-invalid | `VALID_VIDEO_PROVIDERS = {"xai", "kling"}` | ✅ | kling | fal | 1 |
| provider-singular-valid | `provider: xai` | ❌ | — | — | 0 |
| provider-singular-invalid | `provider: kling` | ❌ | kling | — | 1 |
| fixture-stale-provider | `video provider is kling` | ❌ | kling | — | 1 |
| fixture-aligned-provider | `video provider is xai` | ❌ | — | — | 0 |

Provider validation is source-derived from `VALID_VIDEO_PROVIDERS = {"xai", "fal"}`. No hardcoded sentinel remains.
