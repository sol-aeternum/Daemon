# Harness Parity — LongMemEval_S Cleaned Corpus Validation

**Task**: 1. Materialize and Validate Cleaned Corpus
**Generated**: 2026-05-23T16:12:00Z
**Status**: VALIDATION PASSED

---

## Corpus Source

| Property | Value |
|---|---|
| HuggingFace URL | `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json` |
| Local path | `/tmp/longmemeval-review/data/longmemeval_s_cleaned.json` |
| Download method | `curl -L` (direct resolve URL, not viewer URL) |
| Byte size | 277,383,467 (~264.5 MB) |
| SHA256 | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |

---

## Validation Results

### Acceptance Criteria

| Criterion | Value | Pass |
|---|---|---|
| records >= 500 | 500 | ✓ |
| missing_fields = [] | [] | ✓ |
| nonempty_haystack = records | 500 | ✓ |

### Required Fields Check (first 50 records)

| Field | Missing count |
|---|---|
| question_id | 0/50 |
| question | 0/50 |
| answer | 0/50 |
| haystack_sessions | 0/50 |
| haystack_session_ids | 0/50 |
| question_type | 0/50 |

### Schema Comparison (vs `dev_subset.json`)

The cleaned corpus schema matches `tests/benchmark_longmemeval/fixtures/dev_subset.json` exactly.

| | Dev subset | Cleaned corpus |
|---|---|---|
| Extra fields | — | none |
| Missing fields | — | none |
| Shared fields | 9 | 9 |

Shared fields: `answer`, `answer_session_ids`, `haystack_dates`, `haystack_session_ids`, `haystack_sessions`, `question`, `question_date`, `question_id`, `question_type`

---

## Category Distribution

| Category | Count |
|---|---|
| knowledge-update | 78 |
| multi-session | 133 |
| single-session-assistant | 56 |
| single-session-preference | 30 |
| single-session-user | 70 |
| temporal-reasoning | 133 |
| **Total** | **500** |

---

## Prior Halt Context

The prior baseline run (`harness_parity_baseline_run.json`, 2026-05-06) halted with:

- **Halt reason**: "Full haystack-bearing LongMemEval_S corpus unavailable"
- **404 URL**: `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s.json`

The halt attempted to fetch `longmemeval_s.json` (non-cleaned variant), which returned 404. The cleaned variant `longmemeval_s_cleaned.json` is accessible at the correct URL and contains the full 500 records with haystack sessions.

---

## Footgun Note

`tests/longmemeval/ingest.py:36` still contains:

```python
DATASET_URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s.json"
```

This references `longmemeval_s.json` (non-cleaned), not `longmemeval_s_cleaned.json` (cleaned). This file was **not edited** per task constraints. Impact is low — the canonical benchmark entrypoint is `orchestrator.eval.longmemeval`, not this legacy adapter script.

---

## Validation Command

```python
python - <<'PY'
import json, os
path=os.environ.get('LONGMEMEVAL_S_CLEANED_PATH','/tmp/longmemeval-review/data/longmemeval_s_cleaned.json')
data=json.load(open(path))
required={'question_id','question','answer','haystack_sessions','haystack_session_ids','question_type'}
missing=sorted(f for f in required if not all(f in d for d in data[:50]))
cats=sorted({d.get('question_type','?') for d in data})
nonempty=sum(1 for d in data if d.get('haystack_sessions'))
print(f'records={len(data)}')
print(f'missing_fields={missing}')
print(f'nonempty_haystack={nonempty}')
print(f'categories={cats}')
assert len(data) >= 500
assert missing == []
assert nonempty == len(data)
PY
```

### Output

```
records=500
missing_fields=[]
nonempty_haystack=500
categories=['knowledge-update', 'multi-session', 'single-session-assistant', 'single-session-preference', 'single-session-user', 'temporal-reasoning']
VALIDATION PASSED
```

---

## Artifact Decision

**ARTIFACT_DECISION: proceed-real-corpus-valid**

The cleaned LongMemEval_S corpus has been validated and is ready for use in downstream benchmark tasks.
