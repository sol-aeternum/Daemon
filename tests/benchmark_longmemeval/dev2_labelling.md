# dev2 labelling protocol (frozen)

Status: **frozen before any call; no call has been made.** The hashes below pin
the exact prompt, schema and request template that any future run must send.

## Task and inputs

One labeller pass per dev2 question, over that question's answer-session
memories, to separate "the store did not contain it" from "exposure failed".

Inputs per question: the question text, its reference answer, and the stored
memories of the answer sessions as `(id, text)` pairs sorted by id. Retrieval is
not involved: the memory set is fixed by `source_conversation_id`, not by search.

Output: `memory_ids` (subset of the supplied ids), `answerable_from_store`
(boolean) and one short `note`.

## What the labeller never sees

- Any ranking, score or ordering of memories (input order is canonical id order).
- Any exposure, candidate-answer or retrieval-system output.
- Any other question, question id, gold label set or prior verdict.

`tests/benchmark_longmemeval/dev2_labelling.py` builds the request from
`LabelInput` alone; nothing else can enter the packet.

## Pinned parameters

| Parameter | Value |
|---|---|
| Model | `openrouter/openai/gpt-6-luna` |
| Route id | `luna-azure-eu` |
| Seed | `42` |
| Reasoning effort | `medium` |
| Max tokens | `4000` (ids plus a short note; headroom is for reasoning) |
| Temperature | omitted — the route rejects it |

Artifact hashes (`artifact_hashes()`, SHA-256 over UTF-8):

| Artifact | Hash |
|---|---|
| prompt | `8287ddf4385a1ce5908b9b007f82bdb9b345e263778dda373c861a7b9fb894b7` |
| label_schema | `ef2a02089d0f41dea5d4f22c1bed57bb85e1b8463b5efcb47b0c7dfa65b3265f` |
| request_template | `cc60e376ba92180ded2c6bb622bb458f0b412a7a4ee4cb89f937892c9c718c9d` |

`tests/benchmark_longmemeval/test_dev2_labelling.py` asserts these values; any
edit to the prompt, schema or template must be a new version, not a silent
change.

## Route preconditions (before the first call)

- Exact route `luna-azure-eu`; no fallbacks, `data_collection: deny`, ZDR on,
  parameters required — as `scripts/memory_query_decomposition.py`
  `verify_chat_route` checks.
- Route must be approved under the current inference policy and ZDR coverage
  valid (Go DeepSeek ZDR window ends 2026-11-30; recheck before any run).
- Attestation via `check_once` before any call, exactly as `run_live` does; the
  run aborts if attestation fails.
- No provider call is made by the module or tests; the run is an operator action
  with its own cost gate.

## Human spot-check (15% per question-level label batch)

`spot_check_sample(question_ids)` draws 15% (ceiling), at least 2, ordered by
SHA-256 of `[seed, id]`. Per sampled question the owner fills in:

```
question ID:
  labelled memory IDs correct?      yes / no — correction:
  any memory missing from the label? yes / no — ids:
  answerable_from_store correct?    yes / no — correction:
  overall: accept / correct
```

## Disagreement rule

An owner correction replaces the label — it is not averaged or argued. Error
rate = spot-checked questions needing any correction / spot-checked questions.
Above 20% stops labelling for redesign (prompt or schema version bump, new
freeze); no partial-batch extension past the threshold.

`answerable_from_store = false` cases are reported separately as **source
retention gaps** and, by rule, excluded from the exposure denominator: they say
the store never held the fact, so they cannot measure exposure failure.

## Artefacts

- Code: `tests/benchmark_longmemeval/dev2_labelling.py`
- Tests: `tests/benchmark_longmemeval/test_dev2_labelling.py`, `dev2_labelling.md`
- Run a dry check first (reads and validates the inputs, resolves answer
  conversations, writes nothing):
  `python -m tests.benchmark_longmemeval.dev2_labelling label --dry-run --question-rows <rows.json> --checkpoint <checkpoint.json>`
- The paid pass adds `--output <labels.json> --max-spend-usd <cap>`, where the
  cap is cumulative over the whole benchmark database ledger: settled spend plus
  open holds plus a conservative ceiling for the next call.
- Raw model output stays out of Git. Only aggregates — per-question labels that
  the owner has spot-checked, the spot-check counts and the error rate — may be
  recorded in the evidence directory.
