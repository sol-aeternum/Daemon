# Sol 6 → 6.1 upgrade evaluation — 30 September 2026

## Approved scope

The operator requested testing/integrating Sol 6.1 as supported by evidence and
approved a reusable manifest-driven upgrade workflow. This separate experiment
uses current main at `0a5426db`, preserving the prior Sonnet worktree and its
historical report. Model suitability and endpoint approval remain independent.

The operator explicitly approved:

- Exact models `openrouter/openai/gpt-6-sol` and
  `openrouter/openai/gpt-6.1-sol`, each pinned to **`azure/eu`** through the existing
  `https://openrouter.ai/api/v1` gateway. Provider fallback stays disabled. The
  provider pin does not imply that the gateway itself is region-restricted.
- The unchanged frozen 14-case corpus, two models and two repetitions at **high**:
  56 attempts. The eight regression cases, two models and two repetitions at
  **xhigh**: 32 additional attempts. Both settings match existing Sol presets.
- Four actual production-streaming tool probes, one per model/setting, at most
  two provider calls each: **92 total attempts / at most 272 provider calls**.
- 16,000 context tokens, 4096 output tokens, 90 seconds per whole attempt; three
  calls per benchmark attempt and two per streaming probe.
- **USD 22 incremental cap within the original USD 25 September isolated
  evaluation allowance**, no added funding or rollover. The planning bound is
  272 × USD 0.080256 = **USD 21.829632**. Actual per-dispatch serialized reservation
  bounds and the USD 22 admission guard remain authoritative.
- Evaluation-only route qualification on the already attested OpenRouter account.
  Prepare an integration PR only if supported by compatibility/non-regression
  evidence and human acceptance; no deployment is authorized.

The frozen manifest is `tests/fixtures/model_upgrades/sol61_20260930.json`. Corpus
SHA256: `dac5ba663539fdafa2ed350b9c9253e98e5bb46fa1f6987efbc32ae30d0bd4fe`.
The former held-out cases are reused regression evidence, not unseen cases.
Screen each model/setting at 15/16 acceptable regression answers, zero hard
violations and zero wrong-ID fetches; human review is required. Preserve utility
p95 <=10s, orchestration <=30s and synthesis <=60s. This bounded corpus does not
certify demanding reasoning, coding, multimodal quality or full council quality.

## Independent public qualification

Exact endpoint/catalog/ZDR/provider metadata was independently captured using
read-only GETs on 30 September under the durable private run directory:
`/home/sol/.local/state/daemon-evaluations/sol61-20260930/`.
`public-evidence-manifest.json` records URLs, acquisition time and content hashes.
`qualification.json` records exact-model membership and the approved scope.

Sources:

- [Sol 6 endpoints](https://openrouter.ai/api/v1/models/openai/gpt-6-sol/endpoints)
- [Sol 6.1 endpoints](https://openrouter.ai/api/v1/models/openai/gpt-6.1-sol/endpoints)
- [Catalog](https://openrouter.ai/api/v1/models)
- [Exact ZDR endpoints](https://openrouter.ai/api/v1/endpoints/zdr)
- [Provider policies](https://openrouter.ai/api/frontend/v1/all-providers)
- [Official migration/effort guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra)

| Property | Sol 6 | Sol 6.1 |
| --- | --- | --- |
| Catalog revision | `gpt-6-sol-20260922` | `gpt-6.1-sol-20260929` |
| Exact Azure EU endpoint active and ZDR-listed | Yes | Yes |
| Context / output ceiling advertised | 1,050,000 / 128,000 | 1,050,000 / 128,000 |
| Base EU input/output USD per million | 2.20 / 11.00 | 2.20 / 11.00 |
| EU cache-read USD per million | 0.22 | 0.11 |
| Reasoning settings | low, medium, high, xhigh, max, none | low, medium, high, xhigh, max |
| Mandatory reasoning | No | Yes |
| Tools, JSON schema, auto/required/named tool choice advertised | Yes | Yes |

Both Azure EU endpoint parameter-name sets match. Neither advertises temperature
or top_p support; the current slot declares seed sampling only. The 272K-prompt
tier is USD 4.40 input / 16.50 output for both models. Evaluation context is capped
below that threshold; any future native-limit deployment ceiling must cover it.
Azure's public provider policy reports training, OpenRouter training, prompt
retention and publishing all false. These are provider attestations, not an
independent inspection of Microsoft's resource logging configuration.

Account privacy evidence comes from the existing operator attestation in
[INFERENCE_ROUTE_APPROVALS.md](INFERENCE_ROUTE_APPROVALS.md#operator-approval-and-account-attestation).
The operator approved using that same account for this isolated comparison.
Evaluation policy expires at **2026-10-01T00:00:00Z** and remains separate from
deployment qualification. No new inference retention exception is introduced.

## Compatibility questions and preflight

Official native OpenAI guidance requires Responses for Sol 6.1 tool calling;
OpenRouter advertises a tools-compatible Chat Completions interface. Public
metadata does not certify this bridge. Test both non-streaming evidence chains
and the application's actual streaming/tool continuation through guarded
account-funded dispatch.

The prior no-network Sonnet diagnostic found that production streaming drops
reasoning metadata before the next assistant tool turn (#343). This path remains
present on current main, including sanitization in `_prepare_call_params`.
Streaming probes observe the actual continuation without preserving metadata on
the application's behalf. Any material production fix requires its own review.

Read-only ledger intake found **USD 0.960986 settled**, zero reserved and zero
open reservations, confirming that the earlier allowance remains intact. The
old temporary Sonnet artifact directory is unavailable; its raw outputs are not
being reconstructed from summaries. New artifacts use durable private storage.

## Execution and integration decision

Pre-dispatch verification: 87 targeted tests passed, including transport and
admission failure stops; full backend gates passed with 3805 tests, 7 skipped
and 14 existing warnings. The final reporting/probe type check has zero errors.
The initial reviewer hypothesis about stream-iteration errors bypassing a stop
was falsified; a related pre-dispatch exception classification gap was repaired
and covered by negative tests. Fresh read-only `explore-luna` reviewed the mixed
GLM/Sol/Astra diff and the scoped repair, finding no remaining material blocker
in the reviewed execution paths. The reviewer did not execute tests.

Real read-only database preflight accepted all 92 scheduled attempts and confirmed
the USD 21.829632 planning bound, USD 0.960986 prior usage and zero open holds.
The initial evaluation catalog lacked mandatory profile declarations; these were
corrected only in the private evaluation catalog before execution.

Frontend gates initially found the existing high-severity `brace-expansion`
audit blocker (#364). The operator approved a minimal package-manager patch;
only the three affected transitive lock entries changed. No application dependency
or audit suppression was added. All seven patched frontend gates passed; final
aggregate gates passed (73 feature rows, doc freshness, Ruff and gitleaks).

## Completed execution and ledger reconciliation

All **92 attempts completed mechanically**, with **198 provider calls** and no
recorded task failures, stops or unknown charges. Both models completed 12/12
diagnostic attempts, 16/16 high regression attempts, 16/16 xhigh regression
attempts and 2/2 streaming probes. All 12 structured-answer schema checks passed.
Every semantic verdict remains **pending**, with no human reviewer assigned.
These counts do not establish acceptable answers or zero semantic violations.

Independent per-phase reconciliation verified the exact requested/runtime/returned
model, provider pin, explicit effort, output/context bounds and known settlement
for every dispatch. Earlier diagnostic/regression rows are exactly preserved in
the combined results. The ledger moved from 808 to **1006 reservations**, exactly
198 new calls, with **zero open holds or reserved funds** at completion.

| Accounting measure | USD |
| --- | ---: |
| Incremental account charge | 0.182173 |
| Provider-reported aggregate cost | 0.1820962 |
| Total September account usage, including earlier experiments | 1.143159 |
| Authorized incremental cap | 22.00 |
| Original aggregate September allowance | 25.00 |

The small difference between ledger and provider totals is per-call integer
microusd rounding. No funding was added, no month boundary crossed, and no failed
or completed attempt was replayed.

### Descriptive comparison

The settings are explicit controls, not a claim of equal internal reasoning
budgets. Values below include all attempts in each named slice and have not been
normalized by human-acceptable answers.

| Slice | Attempts per model | Sol 6 median / p95 seconds | Sol 6.1 median / p95 seconds | Sol 6 / 6.1 account charge USD |
| --- | ---: | ---: | ---: | ---: |
| Diagnostic, high | 12 | 4.482 / 7.144 | 4.831 / 8.954 | 0.021182 / 0.022974 |
| Regression, high | 16 | 3.768 / 6.824 | 5.371 / 7.471 | 0.026899 / 0.030681 |
| Regression, xhigh | 16 | 5.152 / 8.617 | 6.320 / 9.323 | 0.030188 / 0.039360 |
| Streaming probes, high and xhigh combined | 2 | 6.416 / 6.815 | 8.364 / 8.615 | 0.003503 / 0.007386 |
| All attempts | 46 | 4.575 / 6.947 | 5.963 / 8.615 | 0.081772 / 0.100401 |

Sol 6 used 98 calls and Sol 6.1 used 100. Across the **44 matched benchmark
pairs** (streaming probes excluded), Sol 6.1 was faster in 11 and cheaper in 14;
the median paired latency difference was **+0.685657 seconds**. This is a small
sequential screen, not a statistical performance guarantee.

All predeclared latency screens passed. Nearest-rank p95 equals the maximum in
these small subgroups:

| Slice / setting | Utility p95, Sol 6 / 6.1 | Orchestration p95, Sol 6 / 6.1 | Synthesis p95, Sol 6 / 6.1 |
| --- | ---: | ---: | ---: |
| Diagnostic / high | 3.523 / 3.625 | 7.144 / 8.954 | 5.136 / 7.236 |
| Regression / high | 2.411 / 4.720 | 6.824 / 6.880 | 6.541 / 7.471 |
| Regression / xhigh | 4.412 / 3.647 | 8.617 / 6.938 | 6.947 / 9.323 |

### Production streaming evidence and limits

Each of the four probes executed exactly one `fetch_policy(POL-18)` call and
produced a final answer through the actual production loop. Sol 6.1's advertised
OpenRouter Chat Completions bridge worked for these two streaming tool probes;
this does not certify native OpenAI Chat Completions tool support or all tools.

All eight streaming response objects omitted the provider display name. That
field remains null in the frozen raw/results artifacts; no provider was inferred
into it. Independent **read-only generation receipts**, linked by returned
generation ID, report **Azure** and the exact published revisions
`openai/gpt-6-sol-20260922` / `openai/gpt-6.1-sol-20260929` for all eight calls.
Provider display names do not independently attest geography: the exact EU
subroute is constrained by the outbound `only`/`order` pin and disabled fallback.
The extra requests retrieved existing metadata; they dispatched no inference.

The existing metadata-loss issue (#343) remains real: Sol 6's xhigh probe emitted
one `reasoning.encrypted` detail on its first tool turn, while the outbound
continuation carried zero details. That continuation nevertheless completed in
this run. Neither Sol 6.1 probe emitted first-tool-turn details, so their success
does not establish that dropping mandatory continuation metadata is safe.
Readable reasoning summaries appeared on later final-answer streams. No
production metadata-preservation fix is included in this evaluation.

## Decision and remaining acceptance

**Retain Sol 6 for now.** Sol 6.1 completed the bounded mechanical/protocol screens
but was slower and more expensive in this sample; no human-reviewed quality
advantage or semantic non-regression has yet been established. Production routing,
council roster and deployment qualification remain unchanged, with no upgrade PR
or deployment performed. No additional paid testing is needed under this scope.

Human review is the remaining acceptance step. The generated packet contains all
92 opaque-labeled answers with prompts, expected criteria, tool steps and pending
verdict fields. Keep the model-label map separate until verdicts are recorded.
In particular, each model/setting must independently satisfy the 15/16 regression
floor, hard-violation and wrong-ID criteria before integration can be considered.
Cost per acceptable answer and semantic paired regressions remain uncomputed.
Any later serving qualification must explicitly address its deployment limits,
price tiers and expiry rather than copying this evaluation-only approval.

### Durable artifacts

The private directory named above contains:

- `preflight.json`, `pre-dispatch-ledger.json`, frozen source snapshot/hashes/diff;
- `state.json`, each phase's results and independent reconciliation JSON;
- `streaming-generation-receipts.json`, recording the read-only attribution check;
- `summary.json`, `human-review.md`, `human-verdicts.json`, separate `review-map.json`;
- qualification/source JSON and manifests, backend/frontend/aggregate gate logs;
- `VERIFICATION_LEDGER.md`, reviewer adjudication, limitations and anomaly records.

Final state SHA256:
`a7aca93a46c20f9ac042a6e1eb8c660f7d4bffdcdd42c57fc4c72e577497f62a`.
Combined streaming-results SHA256:
`339aef46d9930b6b0c7cedb6b03c6a3a80fec76c0fc4966c8dad345182ae23f7`.
The earlier Sonnet worktree and historical experiment identities were preserved.
