# Wave 0 Eval State Connection Check

**Date:** 2026-04-29
**Scope:** DB-A — eval pipeline connectivity and retrieval non-emptiness verification
**Benchmark user:** `12345678-1234-5678-1234-567812345678`

---

## Evidence

### Eval Pipeline Code Review

`tests/longmemeval/evaluate.py` — `run_evaluation()`:
- Constructs an `asyncpg` pool from `settings.database_url`
- Passes the pool to `MemoryStore(pool, encryption)` constructor
- `evaluate_single()` calls:
  1. `retrieve_user_memories(...)` with `include_l0=True`, `log_retrieval=...`, `retrieval_triggered_by='longmemeval'`, `include_dream_observations=True`
  2. `answer_with_llm(...)`
  3. `judge_answer(...)`

This confirms the eval reads from the same DB URL as the rest of the system, not a separate fixture DB.

### Direct Retrieval Replay (question_id `e47becba`)

**Question:** "What degree did I graduate with?"
**Reference answer:** "Business Administration"
**Recorded retrieved_memory_ids_count in eval results:** 5

Direct live replay of the same retrieval call returned **5 memories**:

| # | Slot | Score | Content |
|---|------|-------|---------|
| 1 | `education.degree.bachelors.computer_science` | 0.6644 | "User graduated with a Bachelor's degree in Computer Science on May 15th, 2022" |
| 2 | `education.bachelor.business_administration` | 0.6493 | "User graduated with a Bachelor's degree in Business Administration five years ago" |
| 3 | `education.bachelor.computer_science` | 0.4842 | "User graduated from the University of Michigan with a Bachelor's degree in Computer Science" |
| 4 | `education.master.stanford.computer_science` | 0.3945 | "User graduated with a Master's degree in Computer Science from Stanford University in June 2022" |
| 5 | `education.college.graduation` | 0.3604 | "User graduated from college" |

---

## Interpretation

The eval pipeline is connecting to the populated benchmark-user store. Retrieval is non-empty for this question — the eval is not suffering from an empty-DB or wrong-DB artifact. The 5 returned memories include the correct answer (Business Administration) but the system chose Computer Science as top result, which is why the question registers as answered incorrectly by the judge. This is a **ranking/relevance failure**, not a connectivity or empty-store failure.

---

## Verdict

**DB-A ruling: NOT a moot failure due to empty retrieval.**

The eval failure on question `e47becba` is not explained by an empty retrieval result. The store is populated and retrieval produces non-trivial output. The wrong answer stems from the system selecting the wrong memory (Computer Science over Business Administration) despite both being retrieved — a relevance-ranking issue, not a state/connection issue.
