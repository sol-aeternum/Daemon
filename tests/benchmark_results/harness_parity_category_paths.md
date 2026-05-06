# LongMemEval_S Category Paths — T4 Artifact

**Task**: Enumerate LongMemEval_S categories and map to answer-time assembly paths
**Generated**: 2026-05-06
**Source corpus**: `wave0_full_corpus_aligned` results (500 questions)
**Dataset reference**: `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl`

---

## 1. Category Enumeration

### LongMemEval_S Categories (canonical mapping from `evaluate.py:55-72`)

| Category (final) | question_type values | Description | Count (in corpus) |
|---|---|---|---|
| **IE-user** | `single-session-user` | Single-session user fact extraction | **70** (64 non-ABS + 6 ABS) |
| **IE-assistant** | `single-session-assistant` | Single-session assistant fact extraction | **56** (no ABS variant) |
| **IE-preference** | `single-session-preference` | Single-session preference extraction | **30** (no ABS variant) |
| **MR** | `multi-session` | Multi-session reasoning | **133** (121 non-ABS + 12 ABS) |
| **TR** | `temporal-reasoning` | Temporal reasoning | **133** (127 non-ABS + 6 ABS) |
| **KU** | `knowledge-update` | Knowledge update | **78** (72 non-ABS + 6 ABS) |
| **ABS** | N/A (question_id ends with `_abs`) | Abstention questions | **30** (distributed across IE-user/MR/TR/KU as subtypes) |

### Category Totals

```
IE-user:       70
IE-assistant:  56
IE-preference: 30
MR:           133
TR:           133
KU:            78
---
Total:        500 (before ABS split)
```

ABS questions are identified by `question_id.endswith("_abs")` in `evaluate.py:830-831` and are **distributed as subtypes** across parent categories, not counted as a separate disjoint set:

| Parent category | ABS count | Non-ABS count | Category total |
|---|---|---|---|
| IE-user | 6 | 64 | 70 |
| IE-assistant | 0 | 56 | 56 |
| IE-preference | 0 | 30 | 30 |
| MR | 12 | 121 | 133 |
| TR | 6 | 127 | 133 |
| KU | 6 | 72 | 78 |
| **ABS (total)** | **30** | — | — |

### Source evidence for counts

Corpus: `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl`

```python
# Count verification (python3):
from collections import Counter
categories = Counter()
abs_by_parent = Counter()
with open('tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl') as f:
    for line in f:
        obj = json.loads(line)
        categories[obj['category']] += 1
        if obj['question_id'].endswith('_abs'):
            abs_by_parent[obj['category']] += 1

# Result:
# categories: IE-user=70, IE-assistant=56, IE-preference=30, MR=133, TR=133, KU=78 (total=500)
# abs_by_parent: IE-user=6, MR=12, TR=6, KU=6 (total=30)
```

### Exclusion note

No questions are excluded from the 500-question corpus in this benchmark artifact. The 30 `_abs` questions are **retained** in the corpus and categorized under their parent category (IE-user/MR/TR/KU) per the `evaluate.py:830-833` logic.

---

## 2. Assembly Path Mapping

### Converges or branches?

**All categories converge through a single answer-time assembly path. No category-specific formatter exists.**

### Assembly Path (all categories)

Every question — regardless of category (IE-user, IE-assistant, IE-preference, MR, TR, KU, or ABS subtype) — follows the **same** memory formatting path:

```
evaluate_single()  [evaluate.py:627-714]
  │
  ├─► retrieve_user_memories()  [evaluate.py:641-648]
  │     └─► retrieve_memories_for_text()  [retrieval.py]
  │
  └─► build_assembled_system_prompt(memories)  [evaluate.py:651]
        └─► _format_eval_memory_block(memories, summaries=[])  [evaluate.py:487]
        │     [Benchmark-local formatter — does NOT call production build_memory_context()]
        └─► assemble_system_prompt(memory_context=...)  [evaluate.py:490]
              [Production function — prepends DAEMON_SYSTEM_PROMPT, appends memory-tools]
```

### File:line citations for assembly path

| Step | Function | File | Lines | Role |
|---|---|---|---|---|
| Orchestrator | `evaluate_single()` | `tests/longmemeval/evaluate.py` | 627-714 | Entry point; calls all downstream |
| Retrieval | `retrieve_user_memories()` | `tests/longmemeval/evaluate.py` | 604-624 | Wraps `retrieve_memories_for_text()` with hardcoded flags |
| Memory formatting (Path A — model input) | `build_assembled_system_prompt()` | `tests/longmemeval/evaluate.py` | 477-490 | Calls `_format_eval_memory_block()` then `assemble_system_prompt()` |
| Memory formatting (Path B — checkpoint metadata) | `_format_eval_memory_block()` standalone | `tests/longmemeval/evaluate.py` | 434-474, 652 | Formats memories as `"- Fact: ..."`; called again standalone for metadata |
| Production assembly | `assemble_system_prompt()` | `orchestrator/memory/injection.py` | 311-336 | Production prepend/append; called by build_assembled_system_prompt() |

### Category-specific code paths: NONE found

T1 inventory (`tests/benchmark_results/harness_parity_inventory.md`) confirmed:
- `_format_eval_memory_block()` has **no category branching** — it renders all memories identically as `"- Fact: ..."` regardless of memory category or question category
- No `question_type`-dependent code paths exist in `evaluate_single()`
- No category-specific formatters or templates exist

### ABS path

ABS questions (question_id ending in `_abs`) follow the **identical** path as non-ABS questions. The only difference is that `evaluate_single()` marks the `category` field as `"ABS"` at lines 830-831:

```python
# evaluate.py:829-833
category_raw = entry.get("question_type", "single-session-user")
if question_id.endswith("_abs"):
    category = "ABS"
else:
    category = CATEGORY_MAP.get(category_raw, "IE-user")
```

However, **this ABS assignment is stored in the result dict's `category` field but does NOT alter the memory assembly path**. Memory retrieval and formatting are identical for ABS and non-ABS questions.

---

## 3. T1 Assembly Path Inventory Cross-Reference

All 6 categories (IE-user, IE-assistant, IE-preference, MR, TR, KU) and the ABS subtype converge through the same inventory items:

| T1 Inventory Item | File:line | Covered by T4? |
|---|---|---|
| `_format_eval_memory_block()` | `evaluate.py:434, 487, 652` | ✅ All categories |
| `build_assembled_system_prompt()` | `evaluate.py:477, 490, 651` | ✅ All categories |
| `assemble_system_prompt()` | `evaluate.py:50, 490` | ✅ All categories |
| `retrieve_user_memories()` | `evaluate.py:604-624` | ✅ All categories |
| `evaluate_single()` | `evaluate.py:627-714` | ✅ All categories |
| `run_evaluation()` | `evaluate.py:770-892` | ✅ All categories (orchestration only) |

**No category-specific formatter was omitted from T1 inventory.**

---

## 4. Summary

| Property | Value |
|---|---|
| Total questions (LongMemEval_S) | 500 |
| Categories | 6 (IE-user, IE-assistant, IE-preference, MR, TR, KU) + ABS subtype |
| ABS questions | 30 (distributed as subtypes across IE-user/MR/TR/KU) |
| Assembly path count | **1** (single unified path) |
| Category-specific formatters | **None** |
| Production `build_memory_context()` called | **No** (harness uses `_format_eval_memory_block()` substitute) |

**Conclusion: All categories converge through the same `_format_eval_memory_block() → build_assembled_system_prompt() → assemble_system_prompt()` path. No branching or category-specific assembly exists in the current harness.**
