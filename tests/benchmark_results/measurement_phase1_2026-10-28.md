# LongMemEval Phase 1 — measurement, not tuning

Status: **planned measurements complete after owner-authorized residual recovery;
metric and acceptance-rule decisions not ratified**. PR creation remains subject
to the required final gated wrapper; no merge authorization is implied.
No production memory change, primary-metric choice, acceptance-rule ratification,
ingest or holdout evaluation is included. Interpret interim findings **under proposed rule**.
Phase 0 holdout PR: [#587](https://github.com/sol-aeternum/Daemon/pull/587), owner-authorized
merge verified at `26bccd15`. No Phase 1 merge is authorized.

## Judge contract

The generic prompt and three-way parser/scoring are unchanged. Its synthetic
Q/H/A prompt hash matches pre-change `a85fb723`:
`da1fc00ff2a5b44efc8f219f9722f993040c21ee9b601758e09eaab249325672`.

The second metric copies all six official question-type prompts and the abstention
override from [upstream `evaluate_qa.py`](https://github.com/xiaowu0162/LongMemEval/blob/d6dc8b50a2d9ac0c99485ea28fa5755c62414c34/src/evaluation/evaluate_qa.py).
Source SHA-256: `ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251`.
All seven prompt cases were independently compared byte-for-byte with the pinned
function; tests freeze synthetic golden hashes. Abstention uses the official
`"_abs" in question_id` predicate before question-type dispatch. The official
`"yes" in raw_output.lower()` label predicate is preserved, even for unexpected
text; raw output, unexpected-output counts and `length` finish counts expose that
parser limitation rather than silently tightening its behavior.

Upstream requires an explicit metric-model argument; there is no default. Its
`gpt-4o` option is `gpt-4o-2024-08-06`, temperature 0, max tokens 10, n=1, no seed.
This harness keeps the existing pinned Azure-via-OpenRouter snapshot and seed 42;
those are **declared deviations**, not an exact replication of upstream serving.
Generic cap remains 256; official cap is 10. Benchmark transport retries and
provider fallback are disabled. No generic prompt, sampling, reader cap or
production routing decision is changed by adding the second metric.

Canonical results record answer raw output/finish reason plus both judges' raw
outputs, finish reasons, fingerprints and token usage. Legacy `accuracy`, strict
accuracy, category counts and generic `judgment` remain intact. The additive
report shows coverage and null accuracy when unobserved, and a separately labeled
abstention subset overlapping original types. Historical rows are never backfilled
with invented finish reasons or raw judge outputs.

## Saved-hypothesis execution lane

`python -m tests.longmemeval.rejudge` accepts only an A/A2/B arm, generic/official
metric, fresh output directory and cumulative prior cost reservation. It pins the
50-row dev dataset hash, exact IDs, references, question types and context hashes;
there is no arbitrary dataset or holdout argument. Source manifests must be qualified.
There are **no database connections, retrievals, reader calls or snapshot writes**.
This is judge-only measurement, not a new canonical evaluate run; restoring a DB
cannot change a stored hypothesis. Canonical evaluate runs still require restoring
the qualified immutable snapshot first, under the existing runbook.

One run is one 50-row arm/metric, with an exclusive output directory, committed
code pin, durable per-call results and pending-call metadata. No resume or automatic
retry exists. On failure retain the whole reservation, pending call and receipts;
the primary reconciles effects before proposing residual work. Never replay an
uncertain request. The primary serializes runs, carries forward **all** reservations
(including failed/uncertain runs), and stops before the cumulative authorization.
The CLI's prior-reservation argument is operator accounting, not a global concurrent
budget service or an exemption from the primary's cumulative ledger.

Planned: official rejudge all 150 A/A2/B hypotheses; repeat official A twice more
(250 official calls total); also rejudge all 150 generic hypotheses to capture raw
evidence. Compare official judgments with both historical and fresh generic labels,
and report all three official A repetitions paired by question. Do not inspect
holdout text. Stop for disagreement beyond approximately 20%, implausible new
results or other original stop conditions; do not rerun candidates until they pass.

## Reader-cap diagnostic (owner approved separately)

Historical `0a34ad58` hits 256 completion tokens in A/A2/B, without saved finish
reasons. Equal-to-cap is a proxy, not proof of the exact historical termination.
Production does not have a universal 256-token cap: the ordinary completion path
applies caller/context controls and route/account/budget limits (see
`orchestrator/tools/completion.py` and `orchestrator/compute_runtime.py`).

The owner approved only `0a34ad58`, saved A context, caps **256 vs 1024**, the same
pinned reader, and both judges on each new answer. `tests.longmemeval.reader_cap`
refuses if the saved system-prompt hash no longer matches the shared production
composer. It makes no DB/retrieval/ingest calls and does not change the canonical
reader cap. Keep this labeled a **saved-context diagnostic**, not a production
improvement or a qualified new retrieval result. Capture both finish reasons,
new reader outputs and both judge results; keep partial call receipts on failure.

## Cost estimate and ledger

Public [Azure endpoint quote](https://openrouter.ai/api/v1/models/openai/gpt-4o-2024-08-06/endpoints),
checked 2026-10-28: $0.0000025/input token and $0.00001/output token before cache
discounts. Saved-hypothesis reservations use UTF-8 prompt bytes plus 64 envelope
tokens and the maximum output cap. The cap diagnostic uses the identical saved
reader prompt's known token count plus slack, and same-tokenizer output caps with
static judge-text byte bounds. Stop if observed usage exceeds the reservation.
These are conservative quoted-rate estimates, **not billing receipts**; do not
claim exact billed cost when provider billing evidence is unavailable.

| Plan | Calls | Reserved USD | Actual execution |
|---|---:|---:|---|
| Official A/A2/B + two more official A passes | 250 | 0.6023600 | 250 responses returned |
| Generic A/A2/B (original plan) | 150 | 0.9571700 | 150 responses after fresh recovery; 1 additional original request remains uncertain |
| Saved-A paired reader caps, including both judges | 6 | 0.0491650 | 6 responses returned |
| Total originally planned | 406 | **1.6086950** | 406 responses returned; 407 requests attempted including the unknown original |

The original sequence held **$1.5595300**, with returned usage estimated at
**$0.2636950** before recovery. Including the authorized residual and completed cap
diagnostic, the final reservation is **$1.7019050**, and the returned-usage estimate
is **$0.3030775**. Actual billing and the original failed request's usage remain
unknown; the original failed batch's entire reservation stays held. See the
cumulative ledger in `tuning_pass_2026-10-28.md` and final recovery results below.

The cumulative evaluate-only authorization is $10. Every ingest and any spend
beyond $10 require an estimate and owner approval. No ingest or final holdout
execution is authorized here. Credentials are never included in source, prompts,
manifests, outputs or model-review packets.

## Baseline reporting corrections

- The printed historical ABS=0 has an empty category denominator. Actual `_abs`
  rows remain KU (1), MR (2), TR (2). Historical generic strict abstention is A=1/5,
  A2=1/5, B=2/5; these are not the official abstention-aware scores.
- Four summary-bearing rows occur in all three arms: `6cb6f249` (3 summaries),
  `982b5123_abs` (1), `3ba21379` (3), `1de5cff2` (1). B retrieved zero facts, but
  was not wholly memory-free. Extraction permits `category='summary'`, and
  `MemoryStore.get_recent_summaries` reads those rows directly. No absence of
  separate summary jobs proves absence of summary-category memories.
- Original result rows, score files, manifests, dev input and snapshot are unchanged.
  The harness parity exception and historical README receive explicit wording
  corrections only; no re-ingest or production summary behavior change is proposed.

## Verification and remaining approval gate

Targeted offline suite: 137 passed, including unchanged generic tests, official
golden prompts/route pins, saved-input pins, output-reuse refusal, pending-call
retention, missing usage, cap isolation and benchmark-environment refusal.
After bounded resource supervision, the post-review 137-test regression suite
passed. The full managed `scripts/local_ci.sh` then passed all blocking gates in
677 seconds: 7,180 backend tests passed / 1,264 skipped / 14 warnings; 1,464
frontend tests passed across 109 files; 30 cache-browser regressions passed.
Lint, format, types (zero errors/warnings), blocking SAST, dependency audits,
frontend build, feature matrix and all-file pre-commit passed. The earlier
type-check recorder failure is superseded by this completed final-state gate run.
Existing non-blocking Bandit inventory remains tracked in issue #313; no gate
was weakened. The unchanged optional default-browser inventory from Phase 0
remains recorded in issue #490, not treated as a pass.
The required fresh read-only review used a disclosed OpenAI same-family fallback
because the configured Go routes were unavailable. It found one medium-severity
failure-path defect: missing usage cleared the pending call before cost reconciliation.
The marker now remains set and a regression assertion covers that path. The primary
accepted and repaired the finding; no other material findings were returned.
The initial managed commit was refused by lifecycle lock contention (`Errno 11`).
After independently verifying lock availability, the commit succeeded and was
rebased onto main, including the owner-provided `scripts/with_key.py`. Its key-only
credential handoff was verified without displaying values or sourcing production
settings. Code was clean and fixed at **`52774035201964da27f8a322252627eaa95467d3`**
through every paid call. Rebased full gates passed again in 404 seconds: backend
7,209 passed / 1,265 skipped / 14 warnings / 16 subtests; frontend 1,473 tests;
cache-browser 30. A prelaunch lock refusal on the first batch created no manifest
or scope; only after observing the exact lock unheld was a changed-state launch made.

## Initial partial results — preserved history, not an acceptance decision

Raw dev-only outputs, finish reasons, fingerprints, usage, original manifests and
hash inventory are retained in [`measurement_phase1_2026-10-28/`](measurement_phase1_2026-10-28/).
The primary checked every returned row against frozen dev IDs/questions/references
before copying artifacts; no holdout payload was accessed. Paths in immutable
manifests/ledger preserve the original local recovery locations, not portable paths.

| Arm | Historical generic strict | Fresh generic strict | Official strict (first pass) |
|---|---:|---:|---:|
| A | 30/50 (60%) | 30/50 (60%) | 34/50 (68%) |
| A2 | 31/50 (62%) | 31/50 (62%) | 34/50 (68%) |
| B | 6/50 (12%) | **Incomplete: 6/36 observed** | 5/50 (10%) |

The 36 B rows are the completed prefix, not a random subset or a full-arm score.
Do not report 6/36 as a comparable B accuracy or backfill missing fresh labels
with historical judgments.

| Category (count) | Fresh generic A | Official A | Fresh generic A2 | Official A2 | Official B |
|---|---:|---:|---:|---:|---:|
| IE-user (9) | 9 | 9 | 9 | 9 | 0 |
| IE-assistant (9) | 5 | 6 | 5 | 6 | 3 |
| IE-preference (3) | 0 | 1 | 1 | 1 | 0 |
| KU (9) | 5 | 6 | 5 | 6 | 1 |
| MR (10) | 6 | 7 | 6 | 7 | 0 |
| TR (10) | 5 | 5 | 5 | 5 | 1 |
| Abstention subset (5, overlapping) | 1 | 2 | 1 | 2 | 2 |

Counts are correct answers, not percentages. B retains summary context as documented
above. There is no retrieval/reader change behind the metric difference.

- Official-vs-fresh-generic strict disagreement: A **4/50 (8%)**, A2 **3/50 (6%)**,
  and B **2/36** on the observed prefix only. Historical-generic-vs-official B is
  **1/50 (2%)**. No observed comparison exceeded the approximately 20% stop threshold.
- Fresh generic A/A2 have **zero strict label flips** versus their historical labels;
  each has one three-way-label change involving `partially_correct`. B has one
  strict flip in the completed prefix; its remaining labels are unknown.
- Official A repeated scores are **34/50, 34/50, 33/50**. Pairwise disagreements are
  **0/50, 1/50, 1/50**, all variability on preference question `75f70248`. A single
  score does not establish zero judge noise, despite fixed temperature and seed.
- All **386 returned judgments** have finish reason `stop`, not `length`, and the
  recorded served model is the pinned `openai/gpt-4o-2024-08-06`. Official raw outputs
  are only case/punctuation variants of yes/no. The additive unexpected-output count
  counts `Yes.`/`No.` as non-exact tokens; it is not evidence of semantic parse failure.

## Original stopped failure — preserved history

Generic B completed 36 calls, then stopped on the request for `c5e8278d` at
2026-10-28T08:22:49Z. Its manifest retains that pending ID and the whole reservation.
Only `BenchmarkProviderError` and standard LiteLLM help text survived sanitization;
the underlying exception class/status was discarded. **No timeout, rate limit,
authentication, quota, insufficient-funds or billing cause is established.**
The diagnostic gap is filed as [#595](https://github.com/sol-aeternum/Daemon/issues/595).
The call may or may not have executed/billed. No replay, fallback, debug tracing or
account lookup was used to resolve it. The sequence and all receipts remain retained;
the original sequence did not reach the reader-cap diagnostic. Subsequent new calls
were separately reconciled and owner-authorized as recorded below. No new canonical
evaluation, snapshot restore or ingest occurred.

At this checkpoint the 14 missing B outcomes and reader-cap diagnostic remained
incomplete. The final section below supersedes this checkpoint's coverage status,
not its immutable receipts or the original request's unknown outcome.

### Partial-evidence verification

The unchanged implementation retains its completed rebased full-gate evidence.
After adding these artifacts/docs, aggregate gates passed (89-row feature matrix,
all-file doc freshness, lint/format and secret scanning). The primary checked all
386 raw rows for dev-only membership/reference consistency, pinned served model,
normal finish, raw output and nonnegative token-usage fields; the copied artifact
hashes preserve the original records. A fresh scoped read-only review of the new
report and aggregate evidence found no material defects. It used the disclosed
same-family OpenAI fallback and did not inspect raw payloads or execute checks.
These checks verified the partial report, not the then-missing B outcomes or billing.

## Owner-authorized residual reconciliation

The owner subsequently chose **one new replacement judgment** for `c5e8278d`,
with the uncertain original call and its entire reservation preserved, plus the
13 never-attempted B judgments and the previously approved cap diagnostic. The
original request outcome remains unknown; this approval does not invent a status,
usage receipt or recovered response for it. No completed question is rerun.

The bounded residual lane uses `--residual-from` together with an explicit
`--replacement-question-id`. It validates the stopped original generic arm's
source hashes, run identity, completed prefix, original question/reference/context
and hypothesis fields, pending next ID, and carried reservation before any call.
It refuses nested recovery, mismatched/tampered receipts, dropped reservations,
or a missing explicit replacement. Output must be a fresh directory; original
artifacts are not edited. A replacement receipt is explicitly marked as new and
linked to the failed run. This is not automatic resume, fallback or replay.

Failure handling now retains only an allowlisted provider exception type and a
numeric HTTP status from the exception cause chain. It never stores exception
text, response contents, headers or arbitrary class names. This improves future
classification without retroactively classifying the original failure (#595).
Generic prompt/scoring and all reader/judge route/sampling pins are unchanged.

The 14-row residual reservation is **$0.0932100**, and the cap diagnostic remains
**$0.0491650**. Including the full old reservation, the carried-plus-planned amount
is **$1.7019050**, below the $10 authorization. Targeted offline tests:
**151 passed**; types: zero errors and
warnings. Fresh scoped read-only review found no defects (disclosed same-family
OpenAI fallback, not a diversity claim). Full managed gates passed in 472 seconds:
backend 7,223 passed / 1,265 skipped / 14 warnings / 16 subtests; frontend 1,473
tests; cache-browser 30. All blocking gates passed before paid recovery.

## Final recovery results and decision boundary

Recovery code was committed as `cf62a34d9eeaee53bc6eb7d9f43303ae639a79e2` before
the fresh residual and diagnostic ran. A launch-pin typo was refused before ledger,
output directory or provider calls; the read-back exact SHA was then supplied.
All 14 residual judgments and all six diagnostic calls completed. The original
36 generic-B rows are byte-preserved, and their IDs are disjoint from the 14 new
rows; together they cover exactly the frozen 50-question B arm. Only `c5e8278d`
is marked as a new replacement for the unknown original request. The original
failed manifest is still stopped/pending and is never rewritten as successful.

### Complete paired judge scores

| Arm | Historical generic | Fresh generic | Official first pass | Fresh-generic/official agreement |
|---|---:|---:|---:|---:|
| A | 30/50 (60%) | 30/50 (60%) | 34/50 (68%) | 46/50 (92%) |
| A2 | 31/50 (62%) | 31/50 (62%) | 34/50 (68%) | 47/50 (94%) |
| B (assembled verified prefix + fresh residual) | 6/50 (12%) | 7/50 (14%) | 5/50 (10%) | 48/50 (96%) |

| Category (count) | Generic A | Official A | Generic A2 | Official A2 | Generic B | Official B |
|---|---:|---:|---:|---:|---:|---:|
| IE-user (9) | 9 | 9 | 9 | 9 | 1 | 0 |
| IE-assistant (9) | 5 | 6 | 5 | 6 | 3 | 3 |
| IE-preference (3) | 0 | 1 | 1 | 1 | 0 | 0 |
| KU (9) | 5 | 6 | 5 | 6 | 2 | 1 |
| MR (10) | 6 | 7 | 6 | 7 | 0 | 0 |
| TR (10) | 5 | 5 | 5 | 5 | 1 | 1 |
| Abstention subset (5, overlapping) | 1 | 2 | 1 | 2 | 2 | 2 |

Cells are correct-answer counts. Full paired three-way tables and disagreement IDs
are in `phase1-final-summary.json`. B's fresh generic strict change from history is
one question, `852ce960`, in the original completed prefix—not the replacement.
No full-arm judge disagreement exceeds 20%. No memory candidate, category-regression
acceptance decision or validated production accuracy gain is inferred from these
different metrics. B still has summary context; its category zeros remain a known
baseline limitation, not a newly accepted candidate under the unratified rule.

Official A repeat scores remain **34, 34, 33 / 50**, with pairwise flips **0, 1, 1**
on preference question `75f70248`. Official first-pass A/A2 have zero paired strict
flips; fresh generic A/A2 preserve the historical one-question difference. Saved-
hypothesis rejudging measures judge variability: it does **not** replace the proposed
rule's two full canonical evaluate-only reruns for measuring candidate noise *F*.

### Saved-context reader-cap diagnostic

| Cap | Prompt tokens | Completion tokens | Finish | Generic | Official |
|---|---:|---:|---|---|---|
| 256 | 2,418 | 256 | `length` | partially correct | incorrect |
| 1024 | 2,418 | 334 | `stop` | partially correct | incorrect |

The two calls use the identical saved A context and shared composed system prompt;
their recorded context/system hashes match. This directly demonstrates truncation
in the **new 256-token diagnostic call**, not an invented historical finish reason.
Increasing the cap allowed the answer to terminate normally but produced **no strict
gain under either judge**. This single saved-context test neither changes the
canonical 256-token baseline nor authorizes a production cap change. Raw new answers,
both judge outputs, call receipts and usage are retained in `reader_cap_a_1/`.

### Final cost/coverage reconciliation

- Original official/generic returned responses: **386**; new residual judgments:
  **14**; diagnostic: **2 reader + 4 judge** responses.
- Total: **406 returned responses** (404 judge, 2 reader) from **407 attempted
  requests**, including the original one with unknown outcome. No ingest calls.
- Residual quoted-rate estimate: **$0.0153575**; diagnostic: **$0.0240250**;
  cumulative returned-usage estimate: **$0.3030775**. These are not billing receipts.
- Cumulative conservative reservation: **$1.7019050**, retaining the failed batch
  in full. Actual billed cost and the unknown request's charge remain unknown.
- All expected outcomes are observed only because the owner explicitly authorized
  a new replacement, not because the original request was proved unexecuted.

The final recovery ledger, reconciliation record and raw artifacts are retained
alongside the initial stopped ledger; `artifact_inventory.json` hashes both phases.
The diagnostic fix addresses future classification (#595), not retrospective cause.

**Pause for owner decisions after final report/PR gates:** recommend the official
category-aware judge as the primary metric, with unchanged generic strict scoring
as a secondary/regression series. This is a recommendation only. Ratify the primary
metric and `ACCEPTANCE_RULE_PROPOSED.md` (including the baseline/noise interpretation)
before Phase 2 DB-backed triage or Phase 3 tuning. No such work has started.

The final scoped read-only synthesis review found one stale diagnostic-status
sentence in the progress ledger; the primary corrected it and clarified both
execution SHAs. No metric math, artifacts or recovery logic changed. Review used
the disclosed same-family OpenAI fallback; original failure outcome/billing remains
unknown. Final result validation checks all 402 result rows (400 rejudged hypotheses
plus two diagnostic answers) against dev IDs/references and verifies artifact hashes.

## PR P2 repair — secondary-judge outage must not erase base evidence

The PR review found that canonical evaluation discarded a successful reader answer
and generic judgment when the newly additive official call raised. The owner
approved the narrow repair: retain the successful base observation, separately
mark official status failed/coverage missing, and preserve the existing generic
completion contract without automatic replay on re-entry.

The helper now returns the answer, context/hashes, generic label, raw metadata and
both successful usages even when the official call fails. It records only safe
official failure diagnostics; failed official usage is unknown, not zero. There is
no fabricated official incorrect label or top-level generic error. The canonical
manifest exposes official observed/missing counts and failed-call counts separately.
Checkpoint and JSONL-only re-entry retain completed generic observations, so no
reader/generic/retrieval or official retry is silently performed. Explicit recovery
must use the saved hypothesis, not generate a new reader answer.

Synthetic canonical-runner regressions cover timeout and HTTP-429 causes, retained
base fields and paid usage, null official accuracy with missing coverage, safe
diagnostics, checkpoint re-entry, and flushed-result adoption after checkpoint loss.
No paid inference, snapshot restore, ingest or measurement-artifact rewrite is
part of this repair. Recorded experiment SHAs and outcomes remain unchanged.
Verification: 155 targeted tests and all blocking local gates passed (448 seconds):
backend 7,276 passed / 1,265 skipped / 14 warnings / 16 subtests; frontend 1,473
tests; cache-browser 30; types clean. Fresh scoped read-only review found no defects,
using the disclosed OpenAI same-family fallback. Current-head hosted checks and
review are still required before any merge; no merge approval is implied.

After the measurement results, **stop for owner decisions** on primary metric and
`ACCEPTANCE_RULE_PROPOSED.md`. No DB-backed Phase 2 failure triage or Phase 3 tuning
is accepted or started by this implementation.
