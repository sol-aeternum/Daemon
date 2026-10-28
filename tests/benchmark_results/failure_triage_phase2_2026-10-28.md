# Memory tuning Phase 2 — DB-backed failure classification

Status: **DB-backed classification and local verification complete; PR gates remain enforced**.
All 32 non-correct first-pass A/A2 observations (16 unique questions) are covered.
No experiment or tuning is underway.

## Owner decisions and boundaries

- Phase 1 PR [#597](https://github.com/sol-aeternum/Daemon/pull/597) was merged with
  explicit owner approval, after required exact-head checks and review. Merge:
  `78e0ff4a772db64b7496f0847323ffdd5eb78ddf`.
- The owner explicitly selected official strict accuracy as primary, with the
  unchanged generic series secondary, and ratified the existing thresholds and
  qualification gates in `ACCEPTANCE_RULE_PROPOSED.md`. No known baseline/noise
  limitation was waived. Rule version: `2026-10-28-official-v1`.
- This branch starts from merged main. Scope is classification of every non-correct
  first-pass official A/A2 row using actual benchmark DB evidence, with original
  hypotheses, contexts and ingest artifacts unchanged. No static stage assignments
  from older non-parity baselines are reused as current evidence.
- Production memory paths/signatures, tasks, durable chat, fencing, account-generation
  and quarantine code remain untouched. No new dependency, API, model route,
  ingest, source-expansion design, holdout access, production cap change or paid
  inference is included.
- Existing Phase 1 returned-usage estimate is $0.3030775; conservative reservation
  remains $1.7019050, including the unknown original provider request. Actual
  billing is unknown. Phase 2 has added no inference spend.

## Initial database availability

The existing benchmark-only helper's `status` command reported that its `postgres`
service is not running. No database was started, restored or mutated by that check.
The immutable qualified Phase D dump remains the recorded source; a live-state
qualification check is required before using live database contents as evidence.

The owner explicitly authorized starting only existing container
`daemon-lme-bench-postgres-1` (`9aa9b80e8c782e1efddaa8a6839f525b58be5b85cca067db10b81c8ec0002bef`)
and inspecting read-only transactions, with no restore/migration/deletion/re-ingest.
Inspection confirmed Compose project `daemon-lme-bench`, service `postgres`, the
existing benchmark volume, and loopback-only port 55432. This is permission to use
that exact resource, not lifecycle adoption or cleanup authority.

The first managed start command was refused **before execution** by an incomplete source
resource scan. A single read-only invocation of the unchanged installed scanner
also refused after **8.000 seconds / 43,867 entries**: its eight-second deadline was
exceeded, below the 200,000-entry bound. This establishes a scan timeout for this
diagnostic, not a CPU/IO root cause. No container started, DB operation ran, guard
changed, retry loop or direct-start bypass occurred. The owner-authorized DB use
was deferred until normal managed admission recovered.

On the next owner continuation, the unchanged scan completed in 2.822 seconds.
All owned scopes were exited; the previously authorized exact container then
started normally and became healthy. No guard, deadline, service definition,
volume, permission or dependency was changed to obtain admission.

## Qualified read-only DB evidence

The database is `daemon_lme_bench`, with marker
`daemon-longmemeval-benchmark-v1`. Queries ran with server default read-only plus
an explicit repeatable-read, read-only transaction; no write-capable store methods
or provider/embedding calls were invoked. All 50 question users matched the
qualified Phase D fingerprint exactly before and after the evidence export:

`251dd9cfed9a20fe6c719f277359a8eecf9505aa0c6d2441ca954e5838fa5c0a`

| Fingerprinted table | Rows |
|---|---:|
| conversations | 2,126 |
| messages | 23,381 |
| memories | 25,435 |
| memory_extraction_log | 8,633 |
| entities | 0 |

No restore, migration or re-ingest was needed. The immutable dump's recorded
SHA-256 remains `3fd65416fdc24e845a184d7b8d3e0828811e048cd8341a39fe285fc14dc1f5a1`.
The current DB is evidence for that qualified ingest state, not a new evaluation.

The 16 failed-question users contain **7,954 memories**, all with stored embeddings
in `openrouter:azure:openai/text-embedding-3-small:1024` and all explicitly nonlocal.
The cited answer-bearing memories are active/open/cloud-eligible. Missing vectors
therefore do not explain those prompt omissions. No live route qualification or
inference health claim is made from stored vectors.

Every case includes source-session ingest completion, relevant DB facts or empty
extraction records, saved A/A2 hypotheses and final contexts, and falsification
checks. **There are zero matching retrieval-log rows in this ingest state.** Those
logs are excluded from the canonical fingerprint and cannot be reconstructed by
pretending saved contexts are ranking traces. Where a stored fact is absent from
the final prompt, the selection/rendering boundary is proven; candidate exclusion,
score order, top-k cutoff and later formatting are not separately established.

Bounded evidence and coverage are in
[`failure_triage_phase2_2026-10-28/`](failure_triage_phase2_2026-10-28/).
Full per-user DB exports are retained in the registered task evidence area, with
their hashes in `db_evidence_manifest.json`; only relevant facts and source excerpts
are committed. The existing benchmark encryption key was used in memory only,
never printed/copied, and no production `.env` or provider credential was needed.

## Coverage inventory

The first official A/A2 passes each have **16 non-correct rows**, the same 16 IDs
and saved context hashes in both arms: **32 observations** requiring DB evidence.
All are official `incorrect`; generic partial labels do not remove them from scope.

| Category | Question IDs |
|---|---|
| IE-assistant | `4388e9dd`, `71a3fd6b`, `7a8d0b71` |
| IE-preference | `0a34ad58`, `09d032c9` |
| KU | `852ce960`, `f685340e`, `f685340e_abs` |
| MR | `28dc39ac`, `1192316e`, `gpt4_372c3eed_abs` |
| TR | `6613b389`, `gpt4_af6db32f`, `gpt4_b0863698`, `gpt4_93159ced_abs`, `0bb5a684` |

This inventory is derived from committed dev-only official results; DB classification
is separately evidenced below. The extra A judge repetition's variable preference
question is reported in Phase 1, not substituted for the frozen first-pass scope.
Both arms are covered individually, but the same 16 questions are not presented as
32 independent cases or as a new accuracy estimate.

## DB-backed classification

Labels identify the earliest defensible evidence boundary or an explicit ambiguity;
they are not a causal improvement claim or automatic experiment selector. Historical
three-stage taxonomy IDs from older baselines are not reused. In particular,
date-grounding and judge ambiguity are not forced into a false extraction/ranking
diagnosis. All 32 official labels remain non-correct; none is manually rescored.

| Evidence label | Unique questions | A/A2 observations | IDs |
|---|---:|---:|---|
| Source-retention gap | 3 | 6 | `4388e9dd`, `71a3fd6b`, `7a8d0b71` |
| Stored-answer omission from prompt | 5 | 10 | `28dc39ac`, `1192316e`, `6613b389`, `0bb5a684`, `0a34ad58` |
| Historical-time grounding gap | 2 | 4 | `gpt4_af6db32f`, `gpt4_b0863698` |
| Update ordering/provenance gap | 2 | 4 | `852ce960`, `f685340e` |
| Abstention overreach | 3 | 6 | `f685340e_abs`, `gpt4_93159ced_abs`, `gpt4_372c3eed_abs` |
| Rubric/response ambiguity | 1 | 2 | `09d032c9` |
| Total | **16** | **32** | Complete first-pass official A/A2 coverage |

### Source-retention gaps (not retrieval-only opportunities)

- `4388e9dd`: the DB source user fiction brief **and** assistant script describe
  Andy's stained white shirt. The completed support session has two zero-fact
  extraction records and no linked memories. This is authored-fiction/task content,
  not necessarily a user-profile fact; calling it solely an assistant-role loss is
  inaccurate.
- `71a3fd6b`: the assistant source contains the Speyer tourism-board phone number.
  The completed session logged zero facts and has no linked memories.
- `7a8d0b71`: the assistant campaign plan contains the influencer allocation.
  Only two user-project memories survive, neither preserving that allocation.
  A same-amount closing-cost fact elsewhere is not campaign evidence.

The absence is supported by scoped extraction records and all-user entity/anchor
searches, not by absent prompt text alone. Broadening source retention is an
**approval-gated source-expansion/ingest design**; it is not authorized here and
must not silently turn fictional characters or assistant plans into user facts.

### Five stored-answer omissions

- `28dc39ac`: the DB contains 70-hour Odyssey, 25-hour normal and 30-hour hard
  Last of Us runs, 10-hour Celeste and 5-hour Hyper Light Drifter facts: 140 hours.
  The prompt spends two of five slots on paraphrases of the 70-hour fact, includes
  30 hours, and omits the remaining 40 hours. Duplicate occupancy is observed;
  deduplication alone is not proved to recover every missing fact.
- `1192316e`: the one-hour morning routine is stored; the prompt contains only
  the 30-minute commute and associated plans. Both readers ask for preparation time.
- `6613b389`: May 15 engagement and July 22 anniversary facts are stored; the
  prompt omits the anniversary date and both readers request it.
- `0bb5a684`: January 10 workshop and January 17 team-meeting facts are stored;
  the prompt omits the meeting date. A timestamp formatter alone cannot expose
  an event fact that never reaches the prompt.
- `0a34ad58`: Suica ownership and downloaded TripIt facts are stored but omitted;
  the prompt contains generic Tokyo plans. The separately approved higher-cap test
  did not fix either strict label, so truncation is not a sufficient explanation.

These five are a defensible target set for a future **single-variable** prompt-
surface selection/diversity experiment, not proof of a particular ranking bug.
Any candidate still runs on all 50 dev questions and must satisfy the ratified
qualification, noise, category and abstention gates—not just improve this subset.

### Time grounding and update ordering are different problems

For `gpt4_af6db32f`, the source session is February 12 and question March 1, 2023,
but the stored/rendered fact says "today" and DB timestamps are from October 2026
ingestion. For `gpt4_b0863698`, the March 19 "today" run precedes a March 26 query,
but the stored event loses its day and the reader has no query as-of anchor.
Relevant memory metadata is empty. The historical dates survive in fixture/ingest
artifacts, not in those facts' historical DB timestamps.

The ingestion adapter does not accept a haystack-date argument and the parity
answer builder accepts only question/context; their function ASTs are unchanged
from Phase D code. This is a **measurement/date-parity limitation**, filed as
[#601](https://github.com/sol-aeternum/Daemon/issues/601), not a newly proven production
memory defect. Do not silently inject gold benchmark dates, render ingestion time
as event time, change the immutable baseline, or count harness-only gains as a
production improvement. Ingest-level repair needs its own estimate and approval.

`852ce960` and `f685340e` instead have **both conflicting values in the prompt**.
Source sessions and persisted insertion/valid-from order distinguish older/later
claims, but the rendered text does not show those cues. The reader picks an older
amount or reverses previous/current frequency. Ordering/provenance loss is a
plausible contributing explanation, not proof that formatting alone will fix the
reader. A future shared-path provenance experiment must distinguish recorded
observation/ingestion times from actual event dates and preserve history.

### Abstention and judge-boundary cases

- `f685340e_abs`: tennis evidence is not table-tennis frequency. The response
  supplies tennis frequency while contrasting the sports, instead of clearly
  saying the asked frequency is unknown. This is judge-sensitive wording;
  confidence is medium and no negative personal fact is invented.
- `gpt4_93159ced_abs`: NovaTech tenure and Google Photos use do not establish a
  current Google job. The reader accepts the false premise and invents an interval.
- `gpt4_372c3eed_abs`: the prompt explicitly says considering a Master's, yet the
  reader adds a typical two-year degree duration. Missing PCC context is secondary:
  even retrieving PCC cannot establish an unrecorded completed Master's duration.
- `09d032c9`: the power-bank fact is stored **and rendered**, and both answers use
  that resource while giving battery-saving advice. The official label remains
  incorrect. Possible rubric/judge mismatch or insufficiently focused personalization
  needs human adjudication; it is not demonstrated extraction/retrieval loss and is
  not silently rescored or counted as an available memory gain.

## Next work requires a bounded experiment decision

No change is accepted or implemented by this classification. Options to discuss:

1. A shared-path selection/diversity intervention for the five stored-answer
   omissions, with exactly one declared variable and no source expansion.
2. A shared-path ordering/provenance rendering intervention for the two conflicting-
   update cases, without presenting ingestion time as historical event time.
3. A shared production-prompt abstention intervention, checking the entire dev set
   for new abstention losses and correct-answer regressions.

Source expansion and historical-date adaptation are separate approval-gated work,
not bundled into any of these. Pure saved-judge repeats are still not the full
canonical evaluate-only noise evidence required for candidate acceptance. Known
qualification gaps, B's no-facts/summary floor, and actual billing uncertainty are
not waived. Do not start holdout validation or ingest, or accept a candidate, on
the strength of this triage report.

## Verification status

Read-only marker/fingerprint checks and primary case-evidence inspection completed.
The original five offline tests pass, covering exact A/A2 failure membership, source/context
hashes, literal dev-support excerpts, DB provenance, retained thresholds and explicit
metric/causal caveats. Fresh scoped read-only review found no defects. It used the
disclosed same-family OpenAI fallback; it did not inspect full DB exports or execute
queries/tests and does not independently prove semantic absence.

Current-head PR review identified a primary-metric qualification gap: completed
generic results can coexist with failed official judgments. The rule now explicitly
maps all primary comparisons to official labels and requires exactly one binary
official verdict for every frozen dev ID, with matching complete evaluate/score
summaries. An additive offline coverage helper and eight additional regression
cases reject missing, duplicate, extra, wrong-ID and invalid-label series, including
the successful-generic/failed-official shape from #597. Partial measurement scoring
and checkpoint behavior remain unchanged; this is a measurement qualification
repair, not a production-memory gain or threshold change. The three targeted test
modules pass (101 tests). Fresh scoped read-only review of the helper found no
defects, with the same disclosed OpenAI-family limitation and no reviewer-run tests.
All blocking full local gates also passed on the repaired state in 465 seconds:
backend 7,332 passed / 1,265 skipped / 14 warnings / 16 subtests; frontend 1,473;
cache-browser 30; types clean. Hosted current-head checks/review and human final
review remain required before merge.

All blocking managed local gates passed in 589 seconds: backend 7,281 passed /
1,265 skipped / 14 warnings / 16 subtests; frontend 1,473 tests; cache-browser 30;
types clean. A further read-only fingerprint after those gates still exactly matched
qualified Phase D (`db_post_gate_check.json`). The required PR wrapper reruns full
gates after rebase; hosted current-head checks/review and human review still govern
merge. No gate was weakened and no candidate was accepted.

Phase 2 adds **$0 inference spend**; cumulative Phase 1 estimate/reservation remains
unchanged. The benchmark container stays running for the authorized read-only work;
no lifecycle adoption or cleanup occurs. Existing non-blocking inventories remain
tracked; the new date-parity warning is #601.
