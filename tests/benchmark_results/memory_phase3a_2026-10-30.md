# Memory Phase 3A — completed campaign and offline findings

Status: **campaign verified complete; diversity candidate rejected**. Offline
failure analysis is complete; historical-date parity [#601] remains unresolved.
This report records results and proposals, not approval for another experiment.

## Frozen campaign result

All four arms qualified on the frozen 50 dev questions under acceptance rule
`2026-10-28-official-v1` (official strict primary, generic strict secondary).

| Arm | Official correct /50 | Generic strict correct /50 | Strict judge disagreements | Abstention correct /5 |
|---|---:|---:|---:|---:|
| A0 control | 35 | 31 | 4 | 2 |
| A1 recovered control | 35 | 30 | 5 | 2 |
| A2 control | 34 | 31 | 3 | 2 |
| C0 diversity penalty 0.10 | 34 | 31 | 3 | 2 |

Control pairwise official flips are 0, 1 and 1: **F=1**. Required net gain is
`max(3, 2F)=3`. C0 versus A0 has no gains and one loss (`75f70248`,
IE-preference), net **-1**. Other category deltas and abstention delta are zero.
Retain control retrieval; this candidate does not qualify for holdout validation.

**Correction to the earlier campaign summary:** A2 and C0 have equal scores,
not identical official labels. A2 loses `09d032c9` relative to A0, while C0 loses
`75f70248`. They differ on two labels. This correction leaves F, the threshold,
and rejection unchanged. [Public correction][correction].

## Recovery provenance and verification

A1 preserved its first 49 result lines and question 50's nonofficial fields,
importing only the separately approved completed fourth official-judge verdict.
The imported receipt is explicitly normalized evidence, not a raw HTTP response.
Historical failed attempts, original stopped ledgers and reservations remain
preserved. A1's fresh attach/finalization reused all 50 saved results without
inference or another restore; its fresh fingerprint window is not represented
as the failed parent's historical end state. A2 and C0 completed 200 stages each.

All four qualifications, matching evaluation/score summaries, comparability,
artifact hashes, settled attempt ledger and paired result were independently
recalculated by the primary. Final benchmark identity was unchanged, original
connection admission restored, and sessions/prepared transactions/replication
slots checked as zero. A1 has mixed preserved-source/finalization provenance;
retry histories differ across controls. This is not an identical-history claim.

Source revisions:

- Control: `7eba3df30094db9f1f08e1f073fb431c02e214e8`.
- Candidate: `ba0eedde74404227c932f28c8a2fa8a3aab2a890`.
- Final ledger SHA256: `4d3f609cc1e383a1a89982b304a25cb5bcfbbb60175b72394a84bdcf149f5f35`.
- Plan file SHA256: `7a7379f05bcbd2c77811a3f12b92b56a80fc3aa7997c6d47ad530638d715e862`.
- Dev fixture SHA256: `6612e3a03c79b92bab2f1750d4661908e3dc37359715b7fb6b62c6f758bddb34`.

Recovery verification: 165 targeted tests passed; final full backend suite
7,756 passed, 1,298 skipped and 16 subtests passed. Lint, formatting, types,
blocking SAST, dependency audit, feature matrix and pre-commit passed; unchanged
frontend evidence was reused. Fresh read-only source/action and offline-analysis
reviews used a disclosed same-family OpenAI fallback after Go unavailability.
These reviews are not independent-family verification. Intermittent unrelated
cancellation-test timeouts are tracked in [#620]; final confirmation passed
without changing those tests or weakening gates.

## Offline control failure analysis

All 50 memory contexts are byte-identical across A0/A1/A2. The union of official
failures is 16 questions, with 46 failed observations (15+15+16). These are exactly
the [Phase 2 case set][phase2]; all 16 contexts match that evidence and all 16
prior raw DB-export hashes were verified. This follow-up made no DB connection.

| Primary classification | Unique questions | IDs |
|---|---:|---|
| Required source detail not retained | 3 | 4388e9dd, 71a3fd6b, 7a8d0b71 |
| Stored evidence absent from prompt | 5 | 28dc39ac, 1192316e, 6613b389, 0bb5a684, 0a34ad58 |
| Historical source/query date grounding | 2 | gpt4_af6db32f, gpt4_b0863698 |
| Conflicting facts without visible chronology | 2 | 852ce960, f685340e |
| Unsupported-answer/abstention behavior | 3 | f685340e_abs, gpt4_93159ced_abs, gpt4_372c3eed_abs |
| Reader omits supplied personalization | 1 | 09d032c9 (A2 only) |

The five exposure gaps concern gaming-duration components, morning preparation
duration, anniversary date, team-meeting date, and existing Suica/TripIt resources.
Stored facts exist, but the saved prompts omit them. This does not establish
whether candidate retrieval, ranking, cutoff or rendering caused the omission;
matching historical rank traces were not available in the evidence inspected.

The power-bank case has a clearer current diagnosis than Phase 2's rubric
ambiguity: A0/A1 use the supplied power bank and pass; A2 omits it and fails with
identical context. Abstention cases confuse tennis with table tennis, NovaTech
tenure with Google employment, and a contemplated Master's with a completed
degree. The table-tennis case remains wording/judge-sensitive.

Strict generic-versus-official disagreements include `80ec1f4f_abs`, `c4f10528`
and `59524333` in all controls, plus `09d032c9` in A0/A1 and `75f70248` in A1.
All are official correct versus generic non-correct. They overlap successes,
not additional official failures; rubric sensitivity does not justify relabeling.

C0 changes 30/50 contexts. On its allergy-question loss, both contexts retain
the shedding-cat fact and omit the reference's recent-deep-clean detail. C0
replaces a plant-plan fact with a TV fact. One answer/judge sample does not prove
a causal retrieval regression or support claiming that the cat fact was lost.

## Historical-date parity (#601)

At the control revision above:

- `orchestrator/eval/fact_harness.py:546–585` retains session dates and uses them
  for stable sorting when all dates parse, but the ingest call at 1517–1525
  omits the date.
- `tests/longmemeval/ingest.py:326–405` accepts no source date and uses inserted
  message timestamps for extraction checkpoint observation.
- `tests/longmemeval/evaluate.py:657–687,727–755` supplies neither historical
  query time nor source time to the measured gather/answer path.
- `orchestrator/memory/injection.py:444–466` assembles the base prompt and memory
  without a historical clock. The manifest already discloses ingestion-time
  timestamps and `query_reference_time=None` (`fact_harness.py:166–172`).

Source-statement time, event time, ingestion time and query-as-of time are
different clocks. The Super Bowl source/query dates are 2023-02-12/2023-03-01;
the 5K dates are 2023-03-19/2023-03-26. Their stored facts have empty metadata and
2026-10-27 `valid_from` values. The former retains unanchored "today"; the latter
lost the relative date. Both fail in all four arms despite the event being
retrieved. Adding question date alone cannot recover the missing event anchor.
Rendering ingestion time as historical event time would be wrong.

The frozen campaign comparison remains valid under its disclosed conditions;
historical-temporal and end-to-end product claims remain limited. These two are
confirmed examples, not proof that only two questions are affected. Do not
exclude them post hoc, rescore results or count a time-adapted candidate against
the old baseline as a production memory gain. A separately approved time-parity
design needs provenance, timezone/precision and unknown-date handling, staged
source/query-time checks, no answer/support-label leakage, and a fresh qualified
baseline if adopted. [Issue update][date-update].

## Proposed next experiment — not authorized or executed

Target the five stored-but-not-prompted cases with a single retrieval-width
comparison: **5 to 10 items**, preserving control ranking, source pool, prompt,
answer cap and renderer token budget. First obtain approval for a bounded
coverage diagnostic on the qualified snapshot using saved compatible query
embeddings, if available. Stop if they are unavailable; no new inference is
implicitly authorized. Verify that missing eligible facts actually reach the
rendered prompt at width 10. Do not assume their ranks are 6–10.

If the preflight does not expose the missing evidence, reject this experiment
before paid execution and investigate candidate retrieval. If supported, freeze
fresh controls/noise measurement, complete 50-row qualifications and explicit
new budget/restore limits. Preserve the official-primary rule: gain at least
`max(3,2F)`, category loss at most 2, and no net abstention loss. Date adaptation,
ingest changes and source expansion remain separate designs and experiments.

## Accounting and retained evidence

- Completed-call reported cost: **$1.51303867**, excluding uncertain calls and
  question 50 attempt 04's missing cost field; this is not verified billing.
- Approved holds: **$15.751905** (campaign $14.751905 plus capacity hold $1).
- Retry holds: **$0.1873100**; remaining reserved retry headroom **$0.9116290**.
- All **six authorized restores consumed**. Offline analysis added **$0**.
- Holdout payload remains uninspected and uncommitted. No merge or deployment.

Detailed raw campaign artifacts remain in retained task evidence outside Git:
`campaign-final-20261029-replacement-recovery-retry-final-verdict/`,
`final-verdict-campaign-verification-20261030.json`,
`control-failure-analysis-20261030.json`, and
`analyze-controls-offline-20261030.py`. The last two record per-case observations,
input hashes and deterministic assertions; old ledgers/results were not rewritten.
The committed report and existing Phase 2 case records provide the findings;
this publication does not claim all raw recovery evidence is present in Git.

[#601]: https://github.com/sol-aeternum/Daemon/issues/601
[#620]: https://github.com/sol-aeternum/Daemon/issues/620
[phase2]: failure_triage_phase2_2026-10-28.md
[correction]: https://github.com/sol-aeternum/Daemon/pull/606#issuecomment-6520523369
[date-update]: https://github.com/sol-aeternum/Daemon/issues/601#issuecomment-6520520457
