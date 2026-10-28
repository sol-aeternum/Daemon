# LongMemEval Phase 1 — measurement, not tuning

Status: **measurement code verified locally; paid execution pending**.
No production memory change, primary-metric choice, acceptance-rule ratification,
ingest or holdout evaluation is included. Interpret interim findings **under proposed rule**.
Phase 0 holdout PR: [#587](https://github.com/sol-aeternum/Daemon/pull/587), unmerged.

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
| Official A/A2/B + two more official A passes | 250 | 0.6023600 | Not run |
| Generic A/A2/B | 150 | 0.9571700 | Not run |
| Saved-A paired reader caps, including both judges | 6 | 0.0491650 | Not run |
| Total | 406 | **1.6086950** | **$0 spent so far** |

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
Paid execution and final result/PR gates remain pending.
The subsequent managed commit was refused before execution by lifecycle lock
contention (`Errno 11`), not a code/check failure. All recorded scopes are exited;
the reviewed changes remain staged and no unchanged retry or guard bypass was
attempted. Commit and paid execution are blocked pending runner recovery.

After the measurement results, **stop for owner decisions** on primary metric and
`ACCEPTANCE_RULE_PROPOSED.md`. No DB-backed Phase 2 failure triage or Phase 3 tuning
is accepted or started by this implementation.
