# Message source time and absolute dates in extraction (B1, B2)

Status: **Proposed, for owner decision. Nothing here is implemented or approved.** Tracks #601. Requested by the owner on 2026-11-07, as design only.

## Why

A benchmark ingest stores everything at wall-clock time: all 6,959 Block C memories carried the ingest date, while their sessions ran from 2021 to 2024. Today's fix is a benchmark-only remap of a copy. An import of real history would hit the same problem, and it has no remap.

Separately, the extractor receives no date (`orchestrator/memory/extraction.py:215-218,259-267`), so relative wording stays unanchored. About 5.4% of Block C memories (376 of 6,959, rough regex) contain it. None contain a wall-clock year, so the text itself isn't contaminated.

## Summary of the proposal

- **B1:** a new nullable `messages.source_at`, and one `effective_time` threaded from extraction through dedup to the memory write. When it's absent, behaviour is exactly today's, so live chat doesn't change.
- **B2:** each message reaches the extractor with its date, and the prompt resolves relative dates to absolute ones. This changes live extraction, so it ships only after passing an evaluation.

**This departs from the owner's wording in one place.** The request names `valid_from` and `created_at` on memories. This design recommends leaving `created_at` as system time, for the reasons under Decision 2.

## Decisions needed

| # | Decision | Recommendation |
|---|---|---|
| 1 | Message source time: overload `messages.created_at`, or add `source_at`? | Add `source_at` |
| 2 | Memory `created_at`: source time, or system time? | System time, with `valid_from` and `updated_at` carrying source time |
| 3 | Chunk spanning more than one date | Split at date boundaries when source time is explicit |
| 4 | An older fact arriving after a newer one | Never supersede; design properly with the importer |
| 5 | B2 date format and timezone | Per-message date; user's saved timezone, else the deployment default |
| 6 | B2 gates | As under Evaluation, with thresholds set before any run |
| 7 | Spend bound for the fresh ingest at the B pin | Owner decision; see Rollout |

### Decision 1: `source_at` on messages

`messages.created_at` is the history cursor. `docs/HISTORY_PAGINATION.md:81-95` specifies immutable `(created_at, id)` keys, durable tasks force it strictly upward to preserve order (`orchestrator/tasks/store.py:1168-1169`), and the extraction watermark reads it (`orchestrator/worker/jobs.py:686-716`).

| Option | For | Against |
|---|---|---|
| **A. Add `source_at`, nullable (recommended)** | Cursors, ordering and watermarks untouched; `NULL` means `created_at` is the source time | One migration (next number 065); every reader that wants source time must use `COALESCE(source_at, created_at)` |
| B. Write history into `created_at` | No migration | The extraction watermark would treat new work as already observed. Pagination tolerates backdated inserts by requiring a refresh (`docs/HISTORY_PAGINATION.md:93-95`), so the watermark is the real blocker, not the cursor |

### Decision 2: `created_at` on memories

| Option | For | Against |
|---|---|---|
| **A. Keep system time (recommended)** | Keeps "when Daemon learned this" for audit; no change to age-based logic | Departs from the owner's wording; anything reading `created_at` as event time must move to `valid_from` |
| B. Backdate it, as requested | Matches the wording; one timestamp to reason about | Breaks the logic below, and loses when the memory was written |

The evidence against B:

- **Dedup would change:** `orchestrator/memory/dedup.py:259-264` compares `created_at` with now for its explicit-suppression window. A backdated memory falls outside it.
- **It wouldn't fix recency:** `_days_since_accessed` reads `last_accessed_at`, then `updated_at`, then `created_at` (`orchestrator/memory/retrieval.py:312-324`). `updated_at` is never null, so `created_at` is never reached.
- **It isn't needed for temporal windows:** they already prefer `valid_from` (`orchestrator/memory/retrieval.py:173`).

- **Other jobs want system time:** dreaming uses `created_at` as an activity throttle (`orchestrator/memory/dreaming.py:381-395`), and consolidation as cluster order (`orchestrator/memory/consolidation.py:153`).

So with explicit source time, the write sets `valid_from` and `updated_at` to it, and `created_at` keeps `NOW()`. No written contract defines `created_at` as system time (`MEMORY_LAYER.md:144-149`), so this is a recommendation, not a documented invariant.

**Recency stays access-based.** Source-time recency isn't a goal in production: `last_accessed_at` outranks `updated_at`, and retrieval touches set it to `NOW()` (`orchestrator/memory/store.py:2599-2610`), so a memory someone just used counts as fresh, which is correct. An imported memory counts by its source time only until first use.

**Historical-clock evaluations must keep touches off.** With the clock pinned to a past question date, a touched memory has a negative age, which clamps to the freshest bucket (`orchestrator/memory/retrieval.py:312-324`), while untouched ones keep their history. Recency would then be inconsistent within a run. B1 doesn't remove this requirement; M2 already meets it.

### Decision 3: chunks spanning dates

The extractor returns no event time (`orchestrator/memory/extraction.py:735-750`), and a chunk can hold several messages (`orchestrator/worker/jobs.py:679-683`). A fact from a multi-day chunk has no way to say which day it belongs to.

| Option | For | Against |
|---|---|---|
| **A. Split at date boundaries when source time is explicit (recommended)** | Every memory's date is correct to the day; live chunking unchanged | More chunks, and so more spend, on imports |
| B. Take the chunk's last message time | No chunking change; matches the observation time already passed | Can misdate facts by days |
| C. Have the extractor return a date per fact | Most precise | Changes the output schema; the model becomes the source of `valid_from` |

Benchmark sessions carry one date, so A and B match there.

### Decision 4: older facts arriving late

Closes run through several sites, and they close **every** active memory in a slot family, not only the matched one (`orchestrator/memory/dedup.py:382-418,447-474,981-1009`; `orchestrator/memory/store.py:2570-2581,2612-2652`). Under `effective_time`, an older fact arriving after a newer one would produce `valid_to < valid_from`, and so would any later in-family write older than a member.

Benchmark ingests are in date order, and no importer exists to test against. **Proposed for B1,** in every close helper: never close a memory whose `valid_from` is later than `effective_time`. The older fact is still inserted, and the event is logged. That leaves two active memories on one slot until the importer's design handles it, as closed history. No unique index forbids that, but whether any reader assumes one active memory per slot isn't established; implementation must check before merging. Live writes have no `effective_time`, so they're unaffected.

### Decision 5: date format and timezone

`messages_to_extraction_text` renders `[User]: text` (`orchestrator/memory/extraction.py:126-140`). Proposed: `[User, 2023-05-12]: text`, using `COALESCE(source_at, created_at)`. Per-message rather than per-chunk, so a chunk that keeps two dates stays honest.

Live chat prompts use the saved user timezone, then the deployment default (`docs/FEATURE_MATRIX.md:33`). Proposed: extraction does the same. **Whether the worker can read that setting isn't yet established.** Benchmark dates carry no timezone and are treated as UTC.

## B1 design

1. **Migration 065:** `messages.source_at TIMESTAMPTZ NULL`. No backfill.
2. **`insert_message`** (`orchestrator/memory/store.py:687-722`) takes an optional `source_at`. No existing caller passes it, so live chat stores `NULL`.
3. **`effective_time`:** derived in the worker from the chunk's last message, and only when `source_at` is set. It's passed through `process_extraction` to dedup and the store.
4. **Where it applies:**
   - **The memory insert** (`orchestrator/memory/store.py:1842-1846`) relies on column defaults for `valid_from` and `updated_at`, so it must be extended to write them.
   - **Closes:** `dedup.py:995-996` and its siblings, `store.py:2570-2581`, `store.py:2612-2652`.
   - **Reinforcement and dedup:** `updated_at`, and the suppression clock at `dedup.py:263`, which must be decided explicitly.
   - **Deliberately not applied:** `touch_memory` and `bulk_touch_memories` (`store.py:2592,2605`), since access time is real time. `dreaming.py:381-395` and `consolidation.py:153`, which want system time.
   - **Known conflict:** `orchestrator/memory/trust_signals.py:67,133,145,182,231` resets `updated_at` to wall clock on trust changes, which undoes a historical value. These signals fire in live use, so that's acceptable for imports but must not run during a benchmark ingest. Implementation confirms that.
5. **Currency:** add `source_at` to the extraction source-version fields (`orchestrator/worker/jobs.py:201-208`), so correcting it counts as a change.
6. **Harness:** `tests/longmemeval/ingest.py:355-405` passes the session date as `source_at` and builds its worker-shaped rows from it. Conversations get the same treatment only if a reader needs it; none is known today.

**Unchanged by design:** live chat behaviour, cursors, watermarks, `memory_extraction_log.created_at`, fencing (`orchestrator/memory/extraction_fencing.py`), ledger times. No API, SSE or environment change. An import route is out of scope.

## B2 design

Additions to the extraction prompt, exact wording to be frozen and hashed before evaluation:

- Each message shows its date.
- Rewrite relative dates in a fact as absolute ones, resolved against that message's date.
- If a date can't be determined, such as "recently", keep the original wording. **Never invent a date.**

A wrong absolute date is worse than an unresolved relative one, because it reads as fact. So the fabrication gate below is the one that matters most.

**Unknowns for implementation:** how the extraction prompt is pinned or qualified, and what a change invalidates, wasn't established. Absolute dates may change dedup similarity, embeddings and entity inputs, which the evaluation measures.

**Rendering:** dormant `RENDER_NOTED_DATES` (`orchestrator/memory/provenance.py`) shows a noted date from `valid_from`. With B1 it would finally show true dates. Whether it's still needed after B2 is a separate decision; this design doesn't enable it.

## Evaluation (B2)

Dev2 has 39 `temporal-reasoning` questions among its 100. Proposed in two stages, with thresholds set before any run, as M2's were.

**Stage 1: extraction replay.** Same chunks, current prompt against B2, on those 39 questions' answer sessions, plus held-out sessions that were never used to write the prompt. Each arm needs its own fresh corpus: replaying into one database hits `content_hash` conflicts and carries dedup state across arms (`orchestrator/memory/store.py:1842-1847`). Small spend, with a cap fixed beforehand.

| Measure | Proposed gate |
|---|---|
| **Wrong absolute dates,** checked by hand on a seeded sample of at least 100 resolved dates | At most 2%, and none invented where the source gave no basis. Below 100, report only |
| **Systematic shift:** resolved date minus message date, compared with what the wording implies | No consistent offset, such as a month or year out, across any wording |
| **Fact-text diff:** facts whose wording changed beyond the date | Report; owner reviews any meaning change, since this can't be caught by date checks alone |
| Relative wording left where a date was resolvable | Materially below the 5.4% baseline; report only |
| Fact count, and facts lost compared with the current prompt | No material loss; owner reviews any drop |
| Dedup outcomes on the same chunks | Report only |

**Stage 2: full evidence.** The fresh ingest at the B pin (owner ruling) as the new baseline corpus, then exposure and answer measures, with temporal-reasoning questions reported separately.

## Rollout

1. B1 PR: migration, plumbing, tests. Live behaviour unchanged, proved by test.
2. B2 PR: prompt behind Stage 1. Merges only if the gates pass.
3. **Fresh dev2 ingest at the B pin** becomes the baseline (owner ruling). About 8 hours and USD 30 at Block E's rate, plus relabelling, since memory IDs change.

**The B pin includes #689, so the ledger no longer bounds provider spend during that ingest.** Campaign caps sum discounted actuals, and the owner's review on #689 keeps campaigns pinned until a separate bound exists. That has to be decided before step 3. A provider-side credit limit on a dedicated key is the simplest option, and it's an owner action.

## What becomes obsolete

After the new baseline: the historical-time remap, `HISTORICAL_MESSAGE_TIMESTAMPS` and the matching parity exception (`orchestrator/eval/fact_harness.py:166-192`), for new campaigns only. Earlier artifacts and their lineage stay as evidence. M2 and M3 results on the remapped copy stay valid for that corpus and aren't comparable by memory ID with the new one.

## Approval surface

Migration 065; a change to the extraction prompt, affecting all users; updates to `MEMORY_LAYER.md` and `docs/TECHNICAL_SPECS.md`. Export, when built, must carry `source_at`. Row-level deletion covers the new column. `tests/fixtures/account_writer_catalogue.json` may need entries.

## Not settled

- Reflection and decay jobs under historical `valid_from` and `updated_at`. Dreaming, consolidation and trust signals are covered above.
- Whether the worker can read a user's timezone.
- Whether any reader assumes one active memory per slot.
- Restore-history message projections, and native-publication column handling, for `source_at`.
- Extraction prompt pinning and qualification.
- The importer's timestamp contract, which nothing in code or docs defines beyond the product promise (`docs/DAEMON.md`).
