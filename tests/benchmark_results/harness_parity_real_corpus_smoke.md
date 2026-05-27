# Harness Parity — Real Corpus Smoke Trace

**Date:** 2026-05-23T11:13:07.206021Z
**Decision:** `proceed-real-corpus-smoke-pass`
**Task:** `5. Smoke Trace Five Real Corpus Questions`

---

## 1. Scope

This artifact repairs the missing Task 5 evidence by validating the already-created five-row smoke output in `tests/benchmark_results/harness_parity_smoke/` and extracting per-row production prompt-surface evidence from the stored `memory_context` and `system_prompt` fields returned by the parity harness path.

## 2. Selected Questions and Category Mapping

| Question ID | Cleaned-corpus category | Preferred mapping | Synthetic user ID | Memories used | Judgment | Correct | Error |
|---|---|---|---|---:|---|---|---|
| `e47becba` | `single-session-user` | `IE-user` | `bdd96b6a-4123-5a23-894c-854940fbe7c7` | 5 | `incorrect` | `False` | `None` |
| `7161e7e2` | `single-session-assistant` | `IE-assistant` | `ab568119-18a0-5f5c-9f3e-9b03e8fa448a` | 5 | `incorrect` | `False` | `None` |
| `8a2466db` | `single-session-preference` | `IE-preference` | `d9871a5c-36e5-548c-9b39-8b890b78e169` | 5 | `partially_correct` | `False` | `None` |
| `0a995998` | `multi-session` | `multi-session` | `de5eb55c-d854-57fb-aa1c-a5251492df5b` | 5 | `incorrect` | `False` | `None` |
| `gpt4_59149c77` | `temporal-reasoning` | `temporal-reasoning` | `93b6fd14-716c-56aa-adee-6f4fb3ad315a` | 5 | `incorrect` | `False` | `None` |

Distinct categories confirmed: `single-session-user`, `single-session-assistant`, `single-session-preference`, `multi-session`, `temporal-reasoning`.

---

## 3. Commands Used

### Source smoke run (existing five-row output reused)

```bash
docker exec daemon-backend-1 python /tmp/opencode/longmemeval_parity_baseline_runner.py \
  --corpus /tmp/task5_real_corpus_smoke_subset.json \
  --output-dir /tmp/opencode/task5_real_corpus_smoke
```

### Validation / evidence derivation

```bash
python - <<'PY'
import json
from pathlib import Path
rows=[json.loads(line) for line in Path('tests/benchmark_results/harness_parity_smoke/results.jsonl').read_text().splitlines() if line.strip()]
summary=json.loads(Path('tests/benchmark_results/harness_parity_smoke/summary.json').read_text())
assert len(rows)==5
assert len({r['question_id'] for r in rows})==5
assert len({r['category'] for r in rows})==5
assert all(r.get('synthetic_user_id') for r in rows)
assert all(r.get('memory_context') and r.get('system_prompt') for r in rows)
assert any((r.get('memories_used') or 0) > 0 for r in rows)
assert summary['total_submitted']==5 and summary['runtime_excluded']==0 and summary['denominator']==5
PY
```

---

## 4. Production Path Confirmation

- Allowed measurement entry point remains `tests/longmemeval/parity_harness.py:parity_evaluate_single()` per `tests/benchmark_results/harness_parity_entry_contract.md`.
- `parity_evaluate_single()` calls production `build_memory_context()` at `tests/longmemeval/parity_harness.py:125-129` and production `assemble_system_prompt()` at `tests/longmemeval/parity_harness.py:131-134`.
- The smoke output rows used in this artifact contain the returned `memory_context` and `system_prompt` fields directly, which is the per-row evidence that the production path executed for these exact five questions.
- No forbidden legacy measurement path is referenced in `tests/benchmark_results/harness_parity_smoke/*`.

---

## 5. Row-Level Evidence

### 1. `e47becba` — `single-session-user` (IE-user)

- `synthetic_user_id`: `bdd96b6a-4123-5a23-894c-854940fbe7c7`
- `memories_used`: `5`
- `judgment`: `incorrect`
- `correct`: `False`
- `error`: `None`
- `extraction_count_total`: `53`
- `extraction_outcome_counts`: `{"completed": 7, "empty": 46}`
- `memory_context_hash`: `62af7cad332a0c50c1a24e94143137e5c8ea6298ecb0ec7b3b409f9e7f1df13f`
- `system_prompt_hash`: `5a8cea11b53895c124d313945c509c41ea0078461d473123550f20c0e3d2eac7`

Memory context excerpt:

```text
About this user:
- Fact: User is an artisan
- Project: User is applying for the position of Senior Motion Designer at Dash
- Project: User is applying for the position of Senior Motion Designer at Dash
- Fact: User's fri
```

System prompt excerpt:

```text
You are Daemon, a personal AI assistant.

When asked "who are you" or similar, respond: "I'm Daemon, a personal AI assistant."

If the user presses for specifics about your model or capabilities, be honest: explain you are currently running on a specific model (which may vary), that you can switch models automatically
```

### 2. `7161e7e2` — `single-session-assistant` (IE-assistant)

- `synthetic_user_id`: `ab568119-18a0-5f5c-9f3e-9b03e8fa448a`
- `memories_used`: `5`
- `judgment`: `incorrect`
- `correct`: `False`
- `error`: `None`
- `extraction_count_total`: `48`
- `extraction_outcome_counts`: `{"completed": 26, "empty": 21, "errored": 1}`
- `memory_context_hash`: `d889d50fb1a61432bf0952f358044de7949fd93446177ae3ee173bdcf6cf9784`
- `system_prompt_hash`: `59e3957692f92b607ade7e8e0241fa8dbec2f63a3c5247dccbf60b0a7252ea94`

Memory context excerpt:

```text
About this user:
- Fact: User's shift rotation is from Sunday to Saturday
- Fact: User's name is Admon
- Fact: User works as a social media agent
- Project: User is planning a worship service
- Fact: User has 6 other age
```

System prompt excerpt:

```text
You are Daemon, a personal AI assistant.

When asked "who are you" or similar, respond: "I'm Daemon, a personal AI assistant."

If the user presses for specifics about your model or capabilities, be honest: explain you are currently running on a specific model (which may vary), that you can switch models automatically
```

### 3. `8a2466db` — `single-session-preference` (IE-preference)

- `synthetic_user_id`: `d9871a5c-36e5-548c-9b39-8b890b78e169`
- `memories_used`: `5`
- `judgment`: `partially_correct`
- `correct`: `False`
- `error`: `None`
- `extraction_count_total`: `50`
- `extraction_outcome_counts`: `{"completed": 27, "empty": 23}`
- `memory_context_hash`: `0ce69b3276f835722191a5e41b1d21861ac3c0e57941ace9e226eeca40250e02`
- `system_prompt_hash`: `99c6824de257004dfd89911bfbaf3cb1c4045ca71dadec054837ac7eccb5dc4f`

Memory context excerpt:

```text
About this user:
- Fact: User is still getting the hang of color grading
- Project: User plans to stick with the Lumetri Color Panel for now
- Fact: User attended a writing workshop a few months ago
- Project: User is pl
```

System prompt excerpt:

```text
You are Daemon, a personal AI assistant.

When asked "who are you" or similar, respond: "I'm Daemon, a personal AI assistant."

If the user presses for specifics about your model or capabilities, be honest: explain you are currently running on a specific model (which may vary), that you can switch models automatically
```

### 4. `0a995998` — `multi-session` (multi-session)

- `synthetic_user_id`: `de5eb55c-d854-57fb-aa1c-a5251492df5b`
- `memories_used`: `5`
- `judgment`: `incorrect`
- `correct`: `False`
- `error`: `None`
- `extraction_count_total`: `44`
- `extraction_outcome_counts`: `{"completed": 35, "empty": 9}`
- `memory_context_hash`: `640a0779b94b8e84d06cdf85bf7348ab9aed4099e01058c441161badddd4b7f7`
- `system_prompt_hash`: `606e5cbda8979361a19f77b178589251ed92969e4e7884d097b39fb6b779a448`

Memory context excerpt:

```text
About this user:
- Fact: User usually remembers pickups and returns in their head but sometimes forgets
- Project: User needs to wash their favorite yoga pants, which they wore to the gym last Thursday
- Fact: User recei
```

System prompt excerpt:

```text
You are Daemon, a personal AI assistant.

When asked "who are you" or similar, respond: "I'm Daemon, a personal AI assistant."

If the user presses for specifics about your model or capabilities, be honest: explain you are currently running on a specific model (which may vary), that you can switch models automatically
```

### 5. `gpt4_59149c77` — `temporal-reasoning` (temporal-reasoning)

- `synthetic_user_id`: `93b6fd14-716c-56aa-adee-6f4fb3ad315a`
- `memories_used`: `5`
- `judgment`: `incorrect`
- `correct`: `False`
- `error`: `None`
- `extraction_count_total`: `48`
- `extraction_outcome_counts`: `{"completed": 26, "empty": 22}`
- `memory_context_hash`: `a9190b6327b7d3972bf8040d7b6406f6f45a954d138bc52df79f9f53f3d4cf73`
- `system_prompt_hash`: `251e1cebeabb7e106886e588f137682a067d3f644fdbe01cc07e2cb39b3b99f9`

Memory context excerpt:

```text
About this user:
- Fact: User has heard great things about the Brooklyn Museum's collection of ancient artifacts
- Fact: User has been meaning to visit the Brooklyn Museum
- Project: User is looking into something at the
```

System prompt excerpt:

```text
You are Daemon, a personal AI assistant.

When asked "who are you" or similar, respond: "I'm Daemon, a personal AI assistant."

If the user presses for specifics about your model or capabilities, be honest: explain you are currently running on a specific model (which may vary), that you can switch models automatically
```

---

## 6. Aggregate Validation

- `row_count`: `5`
- `unique_question_ids`: `5`
- `distinct_categories`: `5`
- `all_synthetic_user_id_present`: `True`
- `all_memory_prompt_evidence_present`: `True`
- `any_memories_used_gt_zero`: `True`
- `summary.total_submitted`: `5`
- `summary.runtime_excluded`: `0`
- `summary.denominator`: `5`
- `summary.correct`: `0`
- `summary.accuracy`: `0.0`

Secret hygiene check: no raw database URLs, Redis URLs, encryption keys, or API keys were found in `tests/benchmark_results/harness_parity_smoke/*`.

---

## 7. Artifact Decision

**ARTIFACT_DECISION: `proceed-real-corpus-smoke-pass`**

All Task 5 smoke invariants passed: exactly five rows, five unique question IDs, five distinct categories, `synthetic_user_id` present on every row, stored `memory_context` and `system_prompt` present on every row, and all five rows show `memories_used=5` (non-zero).
