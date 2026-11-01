# Memory Phase 3B — historical-clock noted-dates campaign

Status: **campaign verified complete; noted-dates candidate rejected** ("no
measured effect" under the ratified rule). Production `RENDER_NOTED_DATES`
stays off. This report records results and proposals, not approval for another
experiment. Holdout remains untouched.

## Setup

Evaluate-only campaign on the frozen 50 dev questions, run on a remapped copy of
the qualified ingest snapshot ([#601]):

- Historical clock: each question is evaluated as of UTC
  `max(question_date, latest haystack_date + 1 minute)`.
- Stored timestamps were remapped to haystack session dates on a benchmark copy
  after ingest. Extraction and dedup decisions are the original ones. No paid
  re-ingest was done.
- Candidate C0 (option C) is control plus one line:
  `orchestrator/memory/provenance.py` `RENDER_NOTED_DATES = True`. Each rendered
  memory gets `(noted YYYY-MM-DD)`, and the memory block gets a short header on
  reading relative dates. The candidate commit `b114dc3d` is kept on
  `claude-work/memory-c0-noted-dates-on-v2-20261031` and is not for merge.
- Three control arms (A0, A1, A2) and C0 each ran from their own restore of the
  pinned remapped snapshot, after a separate action-time review per arm.

**Rule.** `2026-10-31-official-v2` ([amendment](ACCEPTANCE_RULE_PROPOSED.md)). It
is identical to v1 except the zero-category sanity check: it now applies only to
categories with at least 5 questions. The owner approved v2 after the v1 run
stopped (below), and before any v2 arm ran. The default rule remains
`2026-10-28-official-v1`.

**Stopped v1 run.** The first attempt of this campaign ran A0 (200 calls, no
retries). It then stopped with `Zero official category; investigate`: official
33/50, IE-preference 0/3. IE-preference has only three questions, so the v1 check
can stop a run on noise. The v1 ledger and results are retained unchanged and
were not replayed or reused.

## Result

| Arm | Official correct /50 | Generic strict | Abstention correct /5 |
|---|---:|---:|---:|
| A0 control | 33 | 0.64 | 2 |
| A1 control | 33 | 0.62 | 2 |
| A2 control | 32 | 0.64 | 2 |
| C0 noted dates | **37** | **0.72** | 2 |

Control pairwise official flips: A0/A1 0, A0/A2 3, A1/A2 3, so **F=3**. Required
net gain is `max(3, 2F)=6`. C0 versus A0: 5 gains, 1 loss, net **+4**. No category
loses (IE-preference +1, KU +2, TR +1, others 0), and the abstention delta is 0.
The net-gain criterion fails. No lexical-mode runs were made, so the
both-modes criterion was not assessed; it cannot change a rejection. Code verdict (`paired-result.json`):
`accepted: false`, "reject or investigate; no candidate rerun".

Controls are stable where it matters. The retrieved `memory_context` is
byte-identical across A0, A1 and A2 on 50/50 questions, and A0 and A1 agree on
every official label. All three A2 flips (`6613b389`, `6a1eabeb`, `75f70248`)
occur with identical context. `75f70248` is the label the Phase 3A diversity
candidate lost. A control losing it with unchanged context shows run-to-run
variability alone can produce that loss, though it does not rule out a
contribution from the diversity change. These runs do not separate answer
sampling from judge variability.

In C0, all 50 contexts carry the noted suffixes. With the suffixes removed,
each C0 context equals A0's plus one header line, on 50/50 questions:
`Dates show when each fact was noted; relative words like "today" refer to that
date.`

## Per-question reading (offline, $0)

- **Plausibly caused by noted dates (3):**
  - `gpt4_b0863698` (TR, "how many days ago … 5K charity run"). Phase 3A listed
    this as unrecoverable because the relative date was lost. With the noted date
    and clock, C0 answers "March 19 … 7 days ago". All controls ask for the date.
  - `852ce960` and `f685340e` (KU). C0 picks the later of two conflicting
    facts. The controls pick the earlier one or invert the order.
- **Not attributable (3):**
  - `09d032c9` (IE-preference) gain. The answers are near-identical generic
    tips.
  - `6613b389` (TR) gain. Control A2 also gained it, and C0's reasoning is wrong
    even though it was judged correct.
  - `gpt4_65aabe59` (TR) loss. A0 and C0 say substantively the same thing, so
    the label differs on judging.

Three plausible mechanism hits are below the threshold and are not a measured
effect. They are leads for design work, not evidence for production.

**Caveat.** The remapping is post-ingest. A noted date is the remapped session
date, not a separately anchored event time. In the 5K case the stored fact text
has no date. It works because the noted date (2023-03-19) happens to equal the
event date. Relative-date fact text is still not
historically anchored at extraction (option B, unimplemented). Do not read these
results as an end-to-end product temporal gain.

## Proposed follow-up — not authorized or executed

1. Do not rerun C0 or move it to holdout. Holdout validation is for qualifying
   candidates only.
2. Any further temporal work belongs to a separately approved design, such as
   option B source-time anchoring at ingest. That needs a fresh qualified
   baseline, its own noise measurement and explicit budget and restore limits.
3. A larger evaluation sample would shrink the noise band relative to the
   effect. It needs a new approved dev expansion that keeps the dev/holdout
   separation.

## Accounting and retained evidence

- Provider-reported cost from the 800 v2 receipts: **$1.10336214** (A0 0.27541366,
  A1 0.27861366, A2 0.26792366, C0 0.28141116). This is not verified billing. The
  stopped v1 A0 run added about $0.41.
- Campaign hold: $5.50, of which $4.4588280 was allocated to arms. One retry
  reservation: $0.0133575. A2's answer call for `75f70248` hit HTTP 429, waited
  the persisted 30 s and retried the identical request. Remaining retry headroom
  is $1.0278145. Attempt records: 600 completed plus 1 retained `failed_429`.
- Cumulative reserved holds across campaigns: **$26.7519050**. These are holds,
  not charges.
- Restores: this run used cumulative restores 8–11. **All 11 authorized
  restores are consumed.**
- Holdout payload: uninspected. No production change, merge of the candidate or
  deployment.

Raw evidence is retained outside Git under task evidence and the benchmark runs
directory:

- the v2 campaign ledger, receipts and `paired-result.json`;
- the stopped v1 ledger;
- per-arm action reviews and live-state captures;
- `paired-flips-v2-A0-A1-20261101.md`.

[#601]: https://github.com/sol-aeternum/Daemon/issues/601
