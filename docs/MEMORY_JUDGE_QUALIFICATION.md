# Memory equivalence judge screen — 4 October 2026

**Results: original screen failed; fresh audited follow-up passed the unchanged
qualification rule. PR #447 still requires final integration gates and review.**

This is a fictional-only screen of the PR's configured automatic background
judge: `openrouter/openai/gpt-6-luna`, medium effort, pinned to `azure/eu`.
It does not qualify the older deployment checkout's low-effort preset, another
route, the complete account-budget runtime, or arbitrary future inputs.

## Method and authorization

The owner separately approved up to 24 provider requests, 100,000 cumulative
input tokens, 48,000 output tokens and US$1.00, counting retries, fallbacks and
uncertain attempts. No private memory, production account writes, deployment,
embedding activation or merge was authorized.

`scripts/qualify_memory_judge.py` exercises the production equivalence prompt,
planner and strict parser using a fictional owner and a bounded direct transport.
It bypasses production account/DB operations. An offline parity test compares the
wire format with the installed production LiteLLM adapter. Provider selection is
pinned with fallback disabled, ZDR/data-collection controls, price ceilings,
zero retries and no redirects. Public route attestation is checked before every
send; EU evidence is the exact pin and live listing, not the generic Azure receipt
label. The response model must exactly match `openai/gpt-6-luna`.

The fixture `tests/fixtures/memory_judge_qualification.json` was prepared before
inference and not used to tune the production prompt. Twelve batches contain 12
positive controls and 34 negatives across nine categories: negation, changed
value, person, time, condition, scope, uncertainty, underspecification and prompt
injection. Positive positions are varied deterministically. Fixture, code, policy,
prompt and request hashes are frozen in the operator-local ledger. This procedural
holdout is a small screen, not a statistically comprehensive benchmark.

**Predeclared pass rule:** every response valid, zero false-equivalent negatives,
and every clear positive control classified equivalent. The rule has not been
changed after seeing the responses.

## Observed outcome

| Check | Result |
| --- | --- |
| Valid normal-stop responses | 12 / 12 |
| Negatives incorrectly classified equivalent | 0 / 34 |
| Positive controls classified equivalent | 11 / 12 |
| Overall predeclared rule | **Fail** |

The missed positive was incoming **“Mira keeps a red notebook.”** versus candidate
**“Mira has a notebook that is red.”**, classified `uncertain`. The production
decision preserves the incoming fact in that case. This is a conservative false
negative, not an observed destructive false merge. It remains a failed positive
control; possible semantic differences between “keeps” and “has” do not justify
retroactively changing the frozen ground truth.

No semantic negative was merged in this screen, including the three prompt
injection candidates. This finite observation does not prove universal safety.
This original result alone did not close the qualification objection. The later
follow-up below supplies separate evidence; neither run is deployment approval.

## Budget and retained evidence

The separate durable ledger is
`~/.cache/daemon-memory-445/judge-qualification-20261004.json`.

- Requests: **12**.
- Permanently reserved input/output: **32,080 / 24,000 tokens**.
- Reserved cost including fee allowance: **US$0.02954336**.
- Reported usage: **4,867 input / 3,502 output tokens**.
- Reported model cost: **US$0.00246147**, before the conservative fee allowance.

The full reservations remain charged against the approved cumulative caps.
The embedding ledger is separate and unchanged. No retries were made. The later
follow-up below retained these reservations. Any further evaluation must retain them,
use an explicitly agreed prospective rule and fresh held-out inputs, and receive
independent pre-dispatch review. Do not reset a ledger or rerun until passing.

The strict completion repair independently rejects missing/unknown/filter/length
terminal reasons, refusal/tool/function-call output, multiple choices and non-text
content before verdict JSON can suppress a fact. That repair is verified by
offline regression checks; live quality qualification is evaluated separately below.

## Fixture audit and prospective follow-up

After the first result, the owner requested a fixture audit and fresh validation.
A separate read-only semantic audit found one materially debatable positive:
“keeps” may convey retention or storage that “has” does not. The other eleven
positive labels and all 34 negative labels were defensible under the unchanged
information-preservation prompt. The original result and denominator remain
unchanged; the audit does not retroactively turn the first screen into a pass.

Fresh validation is authorized within the remaining cumulative allowance, using
the same prompt and strict pass rule, with new unambiguous fictional controls.
The runner passed 176 focused offline tests, targeted lint and type checks, and
independent pre-dispatch review. Its real-parent dry-run also passed. Remaining
reservations are at most 12 requests, 67,920 input tokens, 24,000 output tokens and
US$0.97045664. The immutable original ledger SHA-256 is
`60c4a77e548c4a38e14f4240468f24652c82e49bed153e5342ce82eb8dc7ce55`.

The new fixture has 12 batches, 10 positive controls and 38 negatives, including
two all-negative batches. Follow-up reservations are 33,185 input tokens, 24,000
output tokens and US$0.02981077. Both stages together reserve 24 requests, 65,265
input tokens, 48,000 output tokens and US$0.05935413. The follow-up ledger has a
fixed sibling path ending `.json.followup.json`, pins the immutable parent hash,
holds both ledger locks and carries parent reservations into every budget check.
Unknown attempts retain their reservations and prevent resume. This protects
cooperative operator use, not deliberate deletion or copying of ledgers.

Reviewer concerns about one-way entailment were adjudicated before dispatch:
“never” versus “not last summer” and “exactly three” versus “at least three” are
deliberate non-equivalences because they lose information. They remain negatives.
The reviewer incorrectly described the first-run miss as a false positive and
the dry-run as requiring an existing successor: the first miss was a false
negative, and a first-time real-parent dry-run passed without a successor file.
Neither mistaken description changes the accepted dispatch-safety analysis.

## Follow-up observed outcome

**Passed the unchanged prospective rule:** 12/12 valid responses, 10/10 positive
controls classified equivalent, and 0/38 negatives incorrectly classified
equivalent. Both all-negative batches passed. The production prompt was unchanged.
The primary independently replayed every stored receipt through the production
planner/parser and recomputed each score, matching the recorded results exactly.

This closes the bounded judge-screen objection for the PR's frozen medium-effort
Luna/Azure-EU profile. It is finite fictional evidence, not a universal accuracy
claim, full-runtime accounting certification, or qualification of deployment's
older low-effort preset. The original failed result remains unchanged.

The successor ledger SHA-256 is
`e855e3a4750506a7a4ea9bd75f39666770938229dcd2111e68fca0e5e7223bb2`.
Both runs consumed the complete 24-request allowance and reserved all 48,000
output tokens. Cumulative reserved cost is US$0.05935413; reported model cost is
US$0.00515812 before fee allowance. No further calls are authorized by this budget.
Original judge and embedding ledger byte hashes were independently checked and
remain unchanged. No merge, deployment or production memory mutation occurred.
