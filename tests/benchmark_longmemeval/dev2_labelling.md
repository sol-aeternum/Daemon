# dev2 labelling protocol (final v3 contract)

Status: **owner-approved final v3; independent review/refreeze required before
execution.** Historical v1/v2 evidence stays unchanged and readable; its labels
cannot enter final M2. This amendment does not claim that any relabel has run.

## Final v3 amendment (owner ruling, 2026-11-08)

- Replace flat `memory_ids` with `target_groups`: a list of nonempty lists of
  interchangeable supplied IDs. Paraphrases, or same-session memories providing
  the same needed fact/date through `valid_from`, can be alternatives. Any member
  satisfies its group; every group is required. Complementary facts stay in
  separate groups. Duplicate IDs within a group and unknown IDs refuse. Groups
  may overlap when one memory supports multiple distinct requirements.
- Require `needs_memory_date` (exact boolean): true when the answer requires a
  stored date rather than text alone. Dates available only through `valid_from`
  count as answerable. Keep the temporal-cue-dropped-at-extraction observation in
  the note when applicable; do not invent lost temporal cues or unsupported dates.
- V3 includes the actual stored `valid_from`, independently of dataset session
  dates. Missing/unparseable/naive stored timestamps refuse. Question and session
  dates, session attribution and stable input ordering from v2 remain in force.
- Only the primary later relabels the 14-question batch, with **USD 0.05** maximum
  incremental spend. The existing CLI cap remains a cumulative whole-ledger
  ceiling (settled spend + open holds + next-call bound); bind that ceiling to
  the observed starting ledger plus 0.05, never reset or reuse an old journal.
- The prior seeded spot-check auto-carries only if the union of v3 group members
  contains every v2 target **for each originally seeded question**.
  `seeded_spot_check_changes(v2_entries, v3_entries, original_seeded_ids)` performs
  this deterministic comparison without reading evidence. Empty output means
  carry; otherwise report only its changed seeded IDs for rechecking. It neither
  redraws the sample nor waives the not-answerable-addition review rule.
- The primary then completes final labels for all 100 questions and records the
  exact M2 threshold before retrieval. No labels, packets, datasets or external
  evidence are inspected as part of this source-only implementation.
- **This is the final v3 freeze: no further labeller changes regardless of the
  outcome.** Stop/report an adverse result; do not redesign or tune the labeller.

V3 artifact pins (`artifact_hashes()`, also asserted by synthetic tests):

| Artifact | SHA-256 |
|---|---|
| prompt_v3 | `c1c715a00611ffeaa2f86e9449eb63264e7884fe691859c5d998a98b32125698` |
| label_schema_v3 | `4c8e7c091a350d7da93b680a62d5a81502f6c112db84fd0567c1afb8c48cf4b4` |
| label_record_schema_v3 | `d66afce175b7794f0d728e72d20b3a3f24623a06f732d54308b5c2aecdcd2f00` |
| request_template_v3 | `68c822ff191c4e9721635712ef15a49491492d92235645ea5339c50e594650be` |

## Task and inputs

One labeller pass per dev2 question, over that question's answer-session
memories, to separate "the store did not contain it" from "exposure failed".

Inputs per question: question text/date, reference answer, and answer-session
memories as `(id, session_id, session_date, text, valid_from)` tuples sorted by
session date, session ID, then memory ID. Retrieval is
not involved: the memory set is fixed by `source_conversation_id`, not by search.

Output: `target_groups` (supplied IDs only), `answerable_from_store` and
`needs_memory_date` (booleans), and one short `note`. Not answerable means empty
groups; answerable means at least one nonempty group.

## What the labeller never sees

- Any ranking or score of memories (input order is canonical, not ranked).
- Any exposure, candidate-answer or retrieval-system output.
- Any other question, question id, gold label set or prior verdict.

`tests/benchmark_longmemeval/dev2_labelling.py` builds the request from
`LabelInputV3` alone; nothing else can enter the request.

## Pinned parameters

| Parameter | Value |
|---|---|
| Model | `openrouter/openai/gpt-6-luna` |
| Route id | `luna-azure-eu` |
| Seed | `42` |
| Reasoning effort | `medium` |
| Max tokens | `4000` (groups plus a short note; headroom is for reasoning) |
| Temperature | omitted — the route rejects it |

Historical v1 artifact hashes (unchanged; retained in `artifact_hashes()` along
with unchanged v2 pins, SHA-256 over UTF-8):

| Artifact | Hash |
|---|---|
| prompt | `8287ddf4385a1ce5908b9b007f82bdb9b345e263778dda373c861a7b9fb894b7` |
| label_schema | `ef2a02089d0f41dea5d4f22c1bed57bb85e1b8463b5efcb47b0c7dfa65b3265f` |
| request_template | `cc60e376ba92180ded2c6bb622bb458f0b412a7a4ee4cb89f937892c9c718c9d` |

`tests/benchmark_longmemeval/test_dev2_labelling.py` asserts these values; any
historical prompt/schema/template remains unchanged. The v3 amendment above is
final, not authority for another version bump after observing results.

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
  target groups/interchangeability correct? yes / no — correction:
  any group or alternative missing? yes / no — ids:
  answerable_from_store correct?    yes / no — correction:
  needs_memory_date correct?        yes / no — correction:
  overall: accept / correct
```

The packet includes every `answerable_from_store = false` label in addition to
the 15% seeded sample, each marked "Seeded 15% sample" and/or "Every
not-answerable label". `no_memories` entries count as not-answerable labels.
Owner ruling, 2026-11-05.

The >20% stop is computed over the seeded sample only (owner ruling, 2026-11-06).
The not-answerable additions were chosen *because* of their label, so they get
their own correction rate: above 20% flags for owner review. They are still checked
and corrected when wrong.

## Disagreement rule

An owner correction replaces the label — it is not averaged or argued. Error
rate = seeded-sample questions needing any correction / seeded-sample questions.
Above 20% stops labelling; no partial-batch extension past the threshold. Earlier
versions allowed redesign, but the final v3 ruling forbids further labeller
changes regardless of outcome.

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
- Build the spot-check packet from a finished label document (read-only, no
  provider call):
  `python -m tests.benchmark_longmemeval.dev2_labelling packet --question-rows <rows.json> --checkpoint <checkpoint.json> --labels <labels.json> --population <ids.json> --output <packet.md>`
  The packet quotes benchmark text, so the output must be outside the repository
  and an existing packet is never overwritten. A stopped label document, or a
  label id absent from the rows, refuses before connecting.
- Raw model output stays out of Git. Only aggregates — per-question labels that
  the owner has spot-checked, the spot-check counts and the error rate — may be
  recorded in the evidence directory.
- V3 packets show each target group and every alternative, `needs_memory_date`,
  stored `valid_from`, session context and the note. Historical v1/v2 packet
  rendering and schema acceptance remain available for reading old evidence.
