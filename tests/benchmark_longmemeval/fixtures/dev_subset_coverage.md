# Canonical LongMemEval Dev Subset Coverage

This fixture locks an exact 50-case canonical iteration subset for `orchestrator.eval.longmemeval`.
It is derived from the canonical LongMemEval source dataset and keeps the Phase 1 cell floors explicit instead of relying on informal sampling.

## Source and intent

- Source dataset: `/tmp/longmemeval-review/data/longmemeval_s.json`
- Harness lane: canonical only (`orchestrator/eval/longmemeval.py` + `orchestrator/eval/runner.py` + `tests/longmemeval/ingest.py`)
- Why the subset exists: the preserved full-corpus baseline evidence showed the canonical lane remained too slow for tight iteration even after barrier fixes, so this subset keeps canonical experimentation tractable without switching to the fast lane.

## Deterministic selection rules

1. Partition cases by primary `question_type`; treat `_abs` question IDs as overlapping `abstention` members in addition to their primary cell.
2. For each required primary cell (`single-session-user`, `single-session-assistant`, `multi-session`, `temporal-reasoning`, `knowledge-update`), take the 5 smallest cases ordered by `(len(haystack_sessions), len(answer_session_ids), question_date, question_id)`.
3. Take the 5 smallest remaining abstention cases using the same ordering.
4. Fill the remaining 20 slots by round-robin over all primary question types (`single-session-user`, `single-session-assistant`, `single-session-preference`, `multi-session`, `temporal-reasoning`, `knowledge-update`), always taking the next smallest unselected case for that type.
5. Round-robin fill is the tie-break that keeps the subset stratified at exactly 50 instead of letting the globally lightest MR/TR pool consume almost all tail slots.

## Coverage summary

| Cell | Locked cases | Floor | Status |
| --- | ---: | ---: | --- |
| single-session-user | 9 | 5 | meets floor |
| single-session-assistant | 9 | 5 | meets floor |
| multi-session | 10 | 5 | meets floor |
| temporal-reasoning | 10 | 5 | meets floor |
| knowledge-update | 9 | 5 | meets floor |
| abstention | 5 | 5 | meets floor |

## Corpus-plan tractability snapshot

- Questions locked: 50
- Haystack refs inside locked subset: 2126
- Unique session IDs inside locked subset: 2094
- Unique normalized corpus sessions inside locked subset: 2079
- Reference full-corpus canonical scale from preserved evidence: 500 questions and 18,464 unique normalized corpus sessions.

## Locked case map

| # | question_id | primary cell | overlap cells | haystack refs | canonical corpus refs | answer session ids |
| ---: | --- | --- | --- | ---: | ---: | ---: |
| 1 | `b86304ba` | `single-session-user` | `-` | 41 | 41 | 1 |
| 2 | `8550ddae` | `single-session-user` | `-` | 42 | 42 | 1 |
| 3 | `86f00804` | `single-session-user` | `-` | 43 | 43 | 1 |
| 4 | `19b5f2b3` | `single-session-user` | `-` | 43 | 43 | 1 |
| 5 | `caf9ead2` | `single-session-user` | `-` | 43 | 43 | 1 |
| 6 | `fca762bc` | `single-session-assistant` | `-` | 42 | 42 | 1 |
| 7 | `4388e9dd` | `single-session-assistant` | `-` | 43 | 43 | 1 |
| 8 | `e8a79c70` | `single-session-assistant` | `-` | 44 | 44 | 1 |
| 9 | `d596882b` | `single-session-assistant` | `-` | 44 | 44 | 1 |
| 10 | `71a3fd6b` | `single-session-assistant` | `-` | 44 | 44 | 1 |
| 11 | `28dc39ac` | `multi-session` | `-` | 38 | 38 | 5 |
| 12 | `ba358f49` | `multi-session` | `-` | 41 | 41 | 2 |
| 13 | `6cb6f249` | `multi-session` | `-` | 41 | 41 | 2 |
| 14 | `2318644b` | `multi-session` | `-` | 41 | 41 | 2 |
| 15 | `1192316e` | `multi-session` | `-` | 42 | 42 | 2 |
| 16 | `8c18457d` | `temporal-reasoning` | `-` | 41 | 41 | 2 |
| 17 | `gpt4_4edbafa2` | `temporal-reasoning` | `-` | 41 | 41 | 2 |
| 18 | `6613b389` | `temporal-reasoning` | `-` | 41 | 41 | 3 |
| 19 | `gpt4_af6db32f` | `temporal-reasoning` | `-` | 42 | 42 | 1 |
| 20 | `gpt4_b0863698` | `temporal-reasoning` | `-` | 42 | 42 | 1 |
| 21 | `852ce960` | `knowledge-update` | `-` | 39 | 39 | 2 |
| 22 | `6a1eabeb` | `knowledge-update` | `-` | 40 | 40 | 2 |
| 23 | `5831f84d` | `knowledge-update` | `-` | 40 | 40 | 2 |
| 24 | `f685340e` | `knowledge-update` | `-` | 41 | 41 | 2 |
| 25 | `184da446` | `knowledge-update` | `-` | 42 | 42 | 2 |
| 26 | `f685340e_abs` | `knowledge-update` | `abstention` | 43 | 43 | 2 |
| 27 | `982b5123_abs` | `temporal-reasoning` | `abstention` | 44 | 44 | 2 |
| 28 | `gpt4_93159ced_abs` | `temporal-reasoning` | `abstention` | 44 | 44 | 2 |
| 29 | `80ec1f4f_abs` | `multi-session` | `abstention` | 44 | 44 | 3 |
| 30 | `gpt4_372c3eed_abs` | `multi-session` | `abstention` | 44 | 44 | 3 |
| 31 | `ad7109d1` | `single-session-user` | `-` | 44 | 44 | 1 |
| 32 | `2bf43736` | `single-session-assistant` | `-` | 44 | 44 | 1 |
| 33 | `75f70248` | `single-session-preference` | `-` | 42 | 42 | 1 |
| 34 | `a3332713` | `multi-session` | `-` | 42 | 42 | 2 |
| 35 | `0bb5a684` | `temporal-reasoning` | `-` | 42 | 42 | 2 |
| 36 | `0977f2af` | `knowledge-update` | `-` | 44 | 44 | 2 |
| 37 | `c5e8278d` | `single-session-user` | `-` | 44 | 44 | 1 |
| 38 | `c4f10528` | `single-session-assistant` | `-` | 44 | 44 | 1 |
| 39 | `0a34ad58` | `single-session-preference` | `-` | 42 | 42 | 1 |
| 40 | `92a0aa75` | `multi-session` | `-` | 42 | 42 | 2 |
| 41 | `gpt4_65aabe59` | `temporal-reasoning` | `-` | 42 | 42 | 2 |
| 42 | `3ba21379` | `knowledge-update` | `-` | 44 | 44 | 2 |
| 43 | `545bd2b5` | `single-session-user` | `-` | 44 | 44 | 1 |
| 44 | `1de5cff2` | `single-session-assistant` | `-` | 45 | 45 | 1 |
| 45 | `09d032c9` | `single-session-preference` | `-` | 43 | 43 | 1 |
| 46 | `cc06de0d` | `multi-session` | `-` | 42 | 42 | 2 |
| 47 | `gpt4_93159ced` | `temporal-reasoning` | `-` | 42 | 42 | 2 |
| 48 | `59524333` | `knowledge-update` | `-` | 44 | 44 | 2 |
| 49 | `25e5aa4f` | `single-session-user` | `-` | 45 | 45 | 1 |
| 50 | `7a8d0b71` | `single-session-assistant` | `-` | 45 | 45 | 1 |

## Overlap notes

- Abstention is the only overlap cell in this subset; every `_abs` case still keeps its primary `question_type` membership.
- The locked abstention quintet overlaps three primary cells: 1 `knowledge-update`, 2 `multi-session`, and 2 `temporal-reasoning`.
- `single-session-preference` is not a required Phase 1 floor, but the round-robin fill still locks 3 preference cases so the dev subset does not erase that benchmark slice entirely.

## Machine-checkable summary

```json
{
  "target_size": 50,
  "cell_floor": 5,
  "required_cells": [
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "abstention"
  ],
  "primary_fill_order": [
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update"
  ],
  "selected_question_ids": [
    "b86304ba",
    "8550ddae",
    "86f00804",
    "19b5f2b3",
    "caf9ead2",
    "fca762bc",
    "4388e9dd",
    "e8a79c70",
    "d596882b",
    "71a3fd6b",
    "28dc39ac",
    "ba358f49",
    "6cb6f249",
    "2318644b",
    "1192316e",
    "8c18457d",
    "gpt4_4edbafa2",
    "6613b389",
    "gpt4_af6db32f",
    "gpt4_b0863698",
    "852ce960",
    "6a1eabeb",
    "5831f84d",
    "f685340e",
    "184da446",
    "f685340e_abs",
    "982b5123_abs",
    "gpt4_93159ced_abs",
    "80ec1f4f_abs",
    "gpt4_372c3eed_abs",
    "ad7109d1",
    "2bf43736",
    "75f70248",
    "a3332713",
    "0bb5a684",
    "0977f2af",
    "c5e8278d",
    "c4f10528",
    "0a34ad58",
    "92a0aa75",
    "gpt4_65aabe59",
    "3ba21379",
    "545bd2b5",
    "1de5cff2",
    "09d032c9",
    "cc06de0d",
    "gpt4_93159ced",
    "59524333",
    "25e5aa4f",
    "7a8d0b71"
  ],
  "required_cell_counts": {
    "single-session-user": 9,
    "single-session-assistant": 9,
    "multi-session": 10,
    "temporal-reasoning": 10,
    "knowledge-update": 9,
    "abstention": 5
  },
  "primary_counts": {
    "single-session-user": 9,
    "single-session-assistant": 9,
    "single-session-preference": 3,
    "multi-session": 10,
    "temporal-reasoning": 10,
    "knowledge-update": 9
  },
  "corpus_plan": {
    "total_haystack_refs": 2126,
    "unique_session_ids": 2094,
    "unique_normalized_contents": 2079
  },
  "selection_rules": [
    "Seed each required primary Phase 1 cell with its 5 smallest cases by (haystack_sessions, answer_session_ids, question_date, question_id).",
    "Seed the abstention overlay with its 5 smallest remaining _abs cases using the same deterministic ordering.",
    "Fill the remaining 20 slots round-robin across all primary question types, including single-session-preference, to stay stratified instead of letting the lightest MR/TR pool dominate the tail.",
    "Credit overlap cases to every cell they belong to; abstention is an overlay, not a separate primary question_type."
  ]
}
```
