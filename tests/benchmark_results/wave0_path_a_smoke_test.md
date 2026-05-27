# Wave 0 Path A Smoke Test — Question `e47becba`

## Diagnostic status

This memo is **diagnostic** and **not pass/fail** proof for the full corpus.

The targeted PA2 smoke now reaches the live answer and judge paths after the harness-local
OpenRouter routing fix. It writes exactly one emitted result row for `e47becba` and captures
the aligned prompt metadata. However, the live answer is still incorrect, so this run proves
the routing seam is fixed — not that Path A is benchmark-correct end to end.

## Question details

| Field | Value |
|-------|-------|
| question_id | `e47becba` |
| question | `What degree did I graduate with?` |
| reference | `Business Administration` |
| category | `IE-user` |

## Thin prompt baseline (historical pre-alignment)

Before the production-alignment fix, the harness used a **single user message** with
inline memory bullets.

Prior thin-prompt answer (exact text):

> **I'm sorry, the provided memories do not contain information about your degree.**

## Production-aligned prompt shape (actual live smoke path)

The active answer path now uses the production-style two-message structure:

- **system**: assembled Daemon system prompt
- **user**: `What degree did I graduate with?`

For this specific smoke run, the emitted `answer_prompt_metadata` proves the aligned answer
path was reached:

- `provider_endpoint_slug = "openai"`
- `seed = 42`
- `temperature = 0.0`
- `system_message` present
- `user_message = "What degree did I graduate with?"`

Important nuance: the captured `memory_content` is the empty string (`""`), and the row shows
`memories_used = 0`. So the aligned structure is live, but no retrieved memory block was present
in this particular answer prompt.

## Commands executed

Required command from the task:

```bash
BENCHMARK_MODE=1 python -m orchestrator.eval.longmemeval evaluate \
  --dataset /tmp/longmemeval-review/data/longmemeval_s.json \
  --output-dir tests/benchmark_results/wave0_full_corpus_aligned \
  --question-id e47becba
```

### Attempt 1 — exact host-shell command

The exact command still failed first in the host shell with DB hostname resolution:

```text
socket.gaierror: [Errno -2] Name or service not known
```

### Attempt 2 — same benchmark invocation with host-only DB hostname override

The benchmark was rerun with a **host-only** `DATABASE_URL` hostname swap (`postgres` →
`127.0.0.1`) so it could reach the already-populated recovered state from the host shell.

The rerun reached answer generation and judge evaluation successfully:

```text
INFO:orchestrator.eval.runner:[evaluate] TARGETED MODE: evaluating question_id=e47becba only
INFO:orchestrator.eval.runner:[evaluate] [1/500] e47becba evaluating (targeted, forcing re-eval)
LiteLLM completion() model= openai/gpt-4o-2024-08-06; provider = openrouter
LiteLLM completion() model= openai/gpt-4o-2024-08-06; provider = openrouter
INFO:orchestrator.eval.runner:[evaluate] Complete: 1 questions written to tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl
```

## Observed smoke-run result

Artifacts written under:

- `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_results.jsonl`
- `tests/benchmark_results/wave0_full_corpus_aligned/longmemeval_checkpoint.json`

Verification summary:

- Result count: `1`
- Question IDs present: `['e47becba']`
- Error present: `false`
- `answer_prompt_metadata` present: `true`
- `provider_endpoint_slug`: `openai`

Observed row summary:

| Field | Observed value |
|-------|----------------|
| `question_id` | `e47becba` |
| `hypothesis` | `Let me check that for you. Please hold on a moment.` |
| `judgment` | `incorrect` |
| `error` | absent |
| `memories_used` | `0` |
| `retrieved_memory_ids` | `[]` |
| `answer_model` | `openai/gpt-4o-2024-08-06` |
| `answer_fingerprint` | `fp_3028a26f07` |
| `judge_model` | `openai/gpt-4o-2024-08-06` |
| `judge_fingerprint` | `fp_c9e874ca3a` |

## Prompt evidence capture status

### Actual smoke artifact state

Unlike the earlier blocked runs, this smoke artifact **does** contain prompt metadata:

- `answer_prompt_metadata`
- `system_message`
- `user_message`
- `memory_content`
- `memories_raw`
- `answer_fingerprint`

Captured prompt facts:

| Field | Observed value |
|-------|----------------|
| `system_message` | present |
| `user_message` | `What degree did I graduate with?` |
| `memory_content` | empty string (`""`) |
| `memories_raw` | empty list (`[]`) |
| `provider_endpoint_slug` | `openai` |
| `seed` | `42` |
| `temperature` | `0.0` |

### Structural prompt comparison

- **Historical thin prompt**: single user message with memory bullets inline.
- **Aligned live smoke path**: `[system, user]` with benchmark metadata captured in the result row.
- **Actual captured nuance**: the aligned structure is live, but the retrieved memory section is empty for this run (`memory_content=""`, `memories_used=0`).

## Answer comparison

| Run | Observed output |
|-----|------------------|
| Historical thin prompt | `I'm sorry, the provided memories do not contain information about your degree.` |
| PA2 aligned smoke run | `Let me check that for you. Please hold on a moment.` |
| Reference | `Business Administration` |

Comparison:

- The PA2 output now differs from the earlier failure mode in a meaningful way: it is a **live model answer**, not a provider error or missing-session error.
- It also differs from the thin-prompt abstention string.
- But it is still **incorrect** and does not recover the reference fact.

## Emitted-row isolation check

The targeted writer now emits exactly one row to `longmemeval_results.jsonl`, so there is no
row leakage into the emitted result artifact.

Diagnostic nuance: the checkpoint still retains the earlier full `evaluate.results` map internally
(500 stored question keys), even though `completed_count` is rewritten to `1` and the emitted JSONL
contains only `e47becba`. That internal retention is tracked separately in `TRIAGE.md`.

## Diagnostic takeaway

What this smoke **does** prove:

1. The harness-local routing fix works for the answer/judge path: OpenRouter no longer fails with
   `No endpoints found for openai/gpt-4o-2024-08-06.`
2. The targeted run emits exactly one JSONL row for `e47becba`.
3. The aligned answer path now captures live prompt metadata (`system_message`, `user_message`,
   `provider_endpoint_slug`, fingerprints, seed, temperature).

What this smoke **does not** prove:

1. It does not prove the aligned prompt solves `e47becba`.
2. It does not prove the full corpus will pass.
3. It does not prove the retrieval/memory side is healthy for this question, because the live row
   used zero retrieved memories.
