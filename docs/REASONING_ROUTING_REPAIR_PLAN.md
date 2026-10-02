# Reasoning routing repair plan (Package A)

Date: 1 October 2026. Status: **A-core complete when the last row below merges** (see [execution status](#execution-status)).
Companion: [conditional expansion plan (Package B)](REASONING_ROUTING_EXPANSION_PLAN.md).
Current behaviour diagram: [CHAT_ROUTING.md](CHAT_ROUTING.md).

This is an execution plan for another session or agent. It records approved scope,
decisions, verified baseline evidence, the PR sequence and the exit gate. It is not
proof that any repair has shipped: code, tests and gate output govern implementation
status (see [SOURCES_OF_TRUTH.md](SOURCES_OF_TRUTH.md)).

## Authority

Approved by the product owner on 1 October 2026:

1. **A-core implementation**: A-PR1 to A-PR5 below, including running the quality
   gates and opening pull requests.
2. **D2**: an explicit model selection uses that model's `default` parameter preset
   under the `routine` scope on both chat endpoints, whatever the message wording.
3. **D4**: a code fence on its own no longer selects the reasoning profile; the
   user-authored instruction text decides. Accepted consequence: coding requests
   without an analytic signal move to Luna at low effort, which has not been
   evaluated for coding.
4. **Chart adoption**: adopt `docs/CHAT_ROUTING.md` and its rendered chart, and make
   the chart update when models change from now on (A-PR5).

Later on 1 October 2026 the product owner authorized merging each A-core PR once
its security review is clean and CI passes; A-core is complete when all five are
merged.

**Not approved by this plan:** renewing or changing route approvals and any Package B
evaluation budget. Deployment and optional work were authorized on 2 October 2026;
Package B commencement was authorized the same day, subject to its own gates.

## Quick start for an executing agent

1. Read `AGENTS.md`, this plan, and the [execution status](#execution-status) table.
2. Re-verify the baseline against **current** `main` before editing. Line references
   below are for `48bb7c10`; if `main` has moved, re-read the cited code and update
   the evidence in your PR description. Stop and report if the behaviour differs.
3. Work in an isolated worktree created from current `main`, one branch per PR.
   Never work from a checkout with unrelated uncommitted changes.
4. Implement exactly one PR's scope. Run the gates in [Merge gate](#merge-gate).
5. Open the PR with `scripts/pr_create.sh -- <gh pr create args>`. Update the
   execution status row in the same PR. Merge only once its security review is clean
   and required checks pass (merge authority granted 1 October 2026).
6. Ask before any design choice this plan leaves open. The known open choices are
   marked **Ask first**.

## Verified baseline at `48bb7c10`

Verified by read-only inspection on 1 October 2026. Re-verified against `main`
`72ffacf2` (#382) when A-PR1 started: no routing file changed.

| Area | Behaviour | Evidence |
| --- | --- | --- |
| Classification input | Only the current user message; `turn_count` accepted but unused | `orchestrator/model_router.py:75-95` |
| Keyword matching | Any listed signal anywhere in the message, including quoted or pasted material | `model_router.py:93` |
| Word forms | Boundary matching rejects `trade-offs`, `implementing`, `compares`, `analyzing` | `model_router.py:71-72` |
| Code fence | Any ```` ``` ```` makes the message `complex` → reasoning | `model_router.py:88-90`; native `orchestrator/main.py:1996` |
| Fence tests | Fence → complex is pinned | `tests/test_routing.py:73-75`, `:137-139` |
| Upload default (native) | Empty text becomes "Please **analyze** the attached files." before classification → reasoning | `main.py:1962-1963`, `:1999` |
| Upload default (compatibility) | Empty text becomes "Please help with the attached input." → routine | `main.py:1353-1355`, `:1386` |
| Explicit model profile | Native: `routine` scope (`ModelDecision` default). Compatibility: profile derived from wording, so presets and the nested premium ceiling depend on keywords | `main.py:1999-2011`, `:2291-2295`; `:1386-1390`, `:1434-1438` |
| Compatibility contract | Last user message only; no history; `max_tokens`, sampling and `n` accepted but ignored; no doc or test asserts otherwise | `main.py:1349-1355`, `:1434-1449`; `orchestrator/models.py:19-32` |
| Reasoning candidates | All demanding and escalation routes are `premium`; within a group the lowest bounded cost wins, so Sonnet 5 is tried before Sol 6.1 while GLM 5.3 is unapproved | `config/model_routing.json:160-182`; `config/inference_policy.production.json`; `orchestrator/compute_runtime.py:1050-1259` |
| No downgrade | A reasoning request with no premium-eligible route is refused, never answered by a cheaper model | `tests/test_model_routing.py:357-382` |
| Automatic capability denial | Missing `premium_routing` falls through to `capacity_unavailable`, which is in `RETRYABLE_COMPUTE_CODES` | `compute_runtime.py:286-288`, `:1533-1576`; `main.py:2377` |
| Explicit capability denial | Already returns `capability_unavailable` | `compute_runtime.py:1537-1538`; `tests/test_model_routing.py:412`; `tests/test_compute_runtime.py:289` |
| Effort | Presets per model and profile, applied after selection; chat never sends its own effort | `orchestrator/model_routing.py:915-971`; `compute_runtime.py:1602`; `orchestrator/tools/completion.py:271-310` |
| Tool loop | Each tool round is a fresh guarded completion with its own reservation | `tools/completion.py:423-440` |
| Premium ceiling | Outer account scope's profile sets `account_allow_premium` for nested work | `compute_runtime.py:450`, `:1153-1157` |
| Holds | Paid plans hold the route's maximum output; unknown usage settles at the full hold; a charge above the hold suspends the account | `compute_runtime.py:1221-1250`; `orchestrator/entitlements/service.py` `_finish`; issue #342 |
| Routing visibility | SSE `routing` carries `{model, tier, reason}` with `reason` such as `classification:complex`; effort sent is recorded nowhere | `main.py:2025-2029`; `orchestrator/daemon.py:458-461`, `:541-545` |
| Logging | No application logging configuration; uvicorn defaults. INFO from `orchestrator.*` is probably not emitted (about 85% confidence; verify in A-PR4) | `Dockerfile:18`; `docker-compose.yml:104` |

## Scope

**A-core covers:** routing-contract correctness repairs, endpoint parity, truthful
denials, characterization and regression tests, server-log-only telemetry, and
documentation including the generated chart.

**A-core must not change:**

- the routine, research or background mappings, or any `config/model_routing.json`
  group or preset (notes may change);
- `config/commercial.json`, `config/inference_policy.json` or
  `config/inference_policy.production.json`;
- `account_allow_premium` semantics, reservation sizing, settlement or suspension;
- the SSE schema or the meaning of the routing `reason` string;
- the ledger schema;
- frontend code.

It adds no demand bands, semantic routing, quality escalation, continuity state,
new role or new premium authority.

**Excluded unrelated work:** the uncommitted Sol 6.1 port and other dirty files in
the main checkout, the web-fetch pilot, the Midnight UI (PR #382), and
`orchestrator/memory/store.py` edits.

## Optional work (authorized 2 October 2026)

The product owner authorized O1 to O7 on 2 October 2026. Each lands as its own PR under
the same merge rule (clean security review and green required checks). None blocks
A-core completion or Package B.

| ID | Work | Notes |
| --- | --- | --- |
| O1 (D1) | Disclosed routine answer when *inferred* reasoning is unavailable because of capability or budget | Implemented 2 October 2026 on native chat (routing event `fallback` + notice); explicit picks, the compatibility endpoint, outages and background work keep refusing |
| O2 (D3a) | Output target fitted to remaining budget, `max_tokens` equal to the hold, not below the profile floor | Implemented 2 October 2026 (resolves the hold-based-refusal half of #342); disclosed with `budget_fitted_output` |
| O3 (D3c′) | Provisional full-hold settlement reconciled down to the provider receipt | Addresses #342 unknown-usage charge; new outbound read needs approval |
| O4 (D7) | Make the compatibility endpoint parameter-faithful | Implemented 2 October 2026: history, `max_tokens` cap, explicit-model sampling/`stop`, `n`>1 refused |
| O5 | Prune over-broad signals (`vs`, `should i`, `plan for`) | Implemented 2 October 2026; a bare "should I …?" now runs on routine |
| O6 | Externally visible routing reason codes | Implemented 2 October 2026: additive `profile`, `reason_codes`, `effort` on the routing event, with bridge compatibility tests |
| O7 | Ledger columns for profile and effort | Implemented 2 October 2026: migration `042_reservation_routing_labels` (additive, nullable, with rollback); verified against an isolated Postgres |

## PR sequence

Order: A-PR1 → A-PR2. A-PR3 and A-PR4 are independent of each other and of A-PR1.
A-PR5 goes last because it documents the merged behaviour.

### A-PR1: classifier repairs

- **Objective:** classify only the user's own instruction text and match obvious word forms.
- **Files:** `orchestrator/model_router.py`, `tests/test_routing.py`, a new frozen
  classification fixture under `tests/fixtures/`.
- **Commits, in order:**
  1. *Characterization:* freeze current classifier outputs, including known-bad
     cases, so later commits change expected rows visibly.
  2. *Word forms* (verified defect): explicit variants for single-word signals only.
  3. *Provenance* (heuristic repair): remove properly closed fences (```` ``` ````
     and `~~~`) and `>` blockquote lines, then classify the remaining text. If
     nothing remains, classify the quoted text, so instructions written entirely
     inside quotes still work; fenced data never counts, even when nothing else
     remains (refinement approved 1 October 2026, so "fence only → routine" holds
     for code containing keywords). An unclosed fence strips nothing. The model
     always receives the full message.
  4. *D4* (approved): remove the fence-presence rule; update the two pinned tests.
- **Invariants:** signals stay English-only (documented limitation); existing
  negatives hold (`comparisondebugger`, `autocritique.txt`); length and turn count
  are not signals; no API change.
- **Regression cases:**

| Input | Expected |
| --- | --- |
| fenced log + "summarize this" | routine |
| "debug this" + fence | reasoning |
| "what language is this" + fence | routine |
| fence only | routine |
| `>` quoted email containing "compare" + "reply politely" | routine |
| entirely `>`-quoted "compare A and B" | reasoning |
| `>` "compare A and B" + "thanks" | routine (known limitation, documented) |
| unclosed fence followed by "compare" | reasoning |
| "list the trade-offs" / "implementing" / "compares" | reasoning |
| non-English analytic request | routine (unchanged limitation) |
| "hi"; long document + "summarise" | unchanged |

- **Acceptance:** fixture rows change only where each commit says; full
  `tests/test_routing.py` passes; the PR states that stripping is a routing
  heuristic, not proof of authorship or a security boundary.
- **Review:** independent reviewer focused on the provenance edge cases.

### A-PR2: endpoint parity

- **Objective:** one classification entry point for both endpoints; made-up upload
  text is never classified; explicit models follow D2.
- **Files:** `orchestrator/main.py`, `orchestrator/model_router.py`,
  `tests/test_model_override.py`, `tests/test_profile_admission.py`,
  `tests/test_chat_stream.py`.
- **Invariants:** explicit selection stays exact and is admitted against its own
  route (`main.py:855-868`); council admission unchanged; model-facing default
  text unchanged; the compatibility endpoint's last-message, parameter-ignoring
  contract is documented, not changed.
- **D2 consequence:** compatibility explicit selections lose the wording-derived
  premium ceiling for nested automatic helpers, matching native.
- **Regression cases:** upload with no text routes the same on both endpoints;
  upload + "analyze this contract" → reasoning on both; explicit Opus + "research"
  gets the same preset on both (Opus `default`, `high`); explicit unapproved model
  refused on both; council commands admitted under `council`.
- **Acceptance:** parity tests run identical inputs through both endpoints and assert
  the same profile, `reason` string and preset applied at dispatch; existing
  admission tests pass unchanged.
- **Review:** independent reviewer.

### A-PR3: truthful automatic capability denial

- **Objective:** when a missing `premium_routing` capability is the only blocker of an
  automatic request, return `capability_unavailable` (not retryable), matching the
  explicit path.
- **Files:** `orchestrator/compute_runtime.py`, `tests/test_model_routing.py`,
  `tests/test_compute_runtime.py`.
- **Invariants:** still no cheaper downgrade (the test at `:357` keeps passing and
  gains a code assertion); the diagnostic recomputation reserves nothing and calls
  no provider; fixed precedence capability → budget → context; codes and messages
  come from the existing vocabulary; SSE shape unchanged.
- **Regression cases:** Free during trial → premium admitted; Free after trial +
  reasoning classification → `capability_unavailable`, `retryable` false, no
  reservation, no provider call; Pro with zero budget → `budget_exceeded`
  (unchanged); oversized context → `context_limit` (unchanged); explicit premium
  without capability unchanged.
- **Acceptance:** native SSE error has `retryable: false` for this case
  (`main.py:2377`); all other denial tests unchanged.
- **Review:** independent reviewer focused on the entitlement path.

### A-PR4: server-log-only routing telemetry

- **Objective:** reconstruct retries, fallback, cancellation and final cost per turn
  without exposing content.
- **Files:** `orchestrator/compute_runtime.py` (scope entry/exit, candidates,
  dispatch, stream finalization, settlement), `orchestrator/tools/completion.py`
  (completion sequence), `orchestrator/main.py` (turn decision; pass `request_id`
  into the scope), a small routing-log module, tests.
- **Pre-flight:** confirm whether INFO from `orchestrator.*` reaches container logs
  today. Record the result in the PR.
- **Design:** dedicated `daemon.routing` logger with its own stderr handler at INFO,
  `propagate=False`, configured in code with **no new environment variable**. One
  line per event: `routing_event <json>`, built from allowlisted fields only.
  Logging failures never affect dispatch or settlement. No SSE or `reason` change.
- **Correlation keys:** `request_id` (HTTP request → turn), `scope_id` (turn; already
  on every reservation row), `completion_seq` (tool round or synthesis call),
  `attempt_index` (candidate position), `reservation_id` (joins the ledger).

| Event | Known when | Fields |
| --- | --- | --- |
| `decision` | at entry, before the scope opens | endpoint, auto/explicit, explicit model ID, profile, classifier version, rule IDs, signal IDs (fixed vocabulary), attachment flag, admission result |
| `scope_open` | scope entry | `request_id`, `scope_id`, operation, profile, `account_allow_premium` |
| `candidates` | before dispatch | `completion_seq`, plan, ordered route IDs, exclusion counts by reason (capability, budget, context, effort, sampling, excluded) |
| `attempt` | at dispatch | `attempt_index`, route, model, group, premium, explicit/pinned, caller-requested effort, preset effort, **effort sent**, `include_reasoning`, `max_tokens`, hold bound, `reservation_id` |
| `attempt_outcome` | after response or failure | first chunk seen, failure category, status, retryable, `output_released`, next action |
| `settlement` | **only after settlement** | `reservation_id`, actual amount, estimated flag, usage tokens (reasoning tokens if reported), overage, settled/released, path (normal, stream finally, scope cleanup, expiry recovery) |
| `scope_close` | scope exit | exit (normal, cancelled, closed or error code), completion and attempt counts, total settled, time to first output, duration. Named for the account scope because background operations use scopes too |

- **Never logged:** message or tool content, reasoning text, credentials,
  `api_base`, headers, email, raw `user_id`.
- **Privacy note for reviewers:** the signal ID reveals one fixed-vocabulary word the
  user typed. It is included because auditing misroutes needs it; a reviewer may
  reduce it to rule IDs.
- **Regression cases:** allowlist test rejects unknown fields and never emits
  synthetic secret or content strings; 503 before output then success → two
  attempts, two settlements, one `scope_close`; mid-stream cancellation → scope-cleanup
  settlement and cancelled `scope_close`; expiry-recovered settlement logs its
  `reservation_id`; handler emits INFO.
- **Acceptance:** one turn's records join its ledger rows by `scope_id` and
  `reservation_id`; dispatch behaviour unchanged.
- **Review:** independent reviewer plus a privacy review of the allowlist.

### A-PR5: contract documentation and generated chart

- **Objective:** the docs describe merged behaviour, and the chart cannot silently go
  stale when models change. Each earlier PR updates its own docs; this PR covers the
  cross-cutting truths and the chart.
- **Documentation:** `docs/MODEL_ROUTING.md` selection contract (lowest bounded cost
  first within a group; refusal conditions; compatibility adapter contract;
  current-message-only classification; provenance is a heuristic);
  `config/model_routing.json` notes only; a `docs/GLOSSARY.md` note mapping the
  glossary's roles to code profiles; verify #331 against `main` and close or comment.
- **Chart automation (decision 4):**
  - Add a generator (proposed `scripts/render_chat_routing.py`) that reads the
    routing catalog through `orchestrator.model_routing`, approval facts from
    `config/inference_policy.production.json`, and labels from the existing
    `orchestrator.catalog.get_model_name`. It rewrites only the content between
    `<!-- BEGIN GENERATED: chat-routing -->` and `<!-- END GENERATED: chat-routing -->`
    in `docs/CHAT_ROUTING.md`: the Mermaid source and the model-dependent
    configuration facts. `--check` exits non-zero when the document is stale.
  - Add `tests/test_chat_routing_doc.py` running the generator in check mode. Pytest
    runs in the required `Backend gates` check, so any PR that changes models,
    groups, presets or route approvals fails until the chart is regenerated. The
    doc-freshness hook is not in CI, so it is not the enforcement point.
  - Output must be deterministic: derive dates only from policy data, never from
    today's date.
  - Show selection order truthfully: within a group, lowest bounded cost wins, so
    list order is not priority. Reflect A-PR1 to A-PR3 (user-authored text, no
    fence-only rule, capability denial).
  - **Rendered image (decided: option a, 1 October 2026, under the authority to proceed
    on judgement; reversible).** The current PNG was produced by a renderer that
    is not in the repository. Options: (a) recommended: replace the PNG with an SVG
    generated by the same script from a fixed-layout template using only the standard
    library, so the image updates with the Mermaid source and needs no new
    dependency; (b) keep the PNG with a source-hash stamp that the test checks,
    regenerated by whoever has an approved renderer; (c) drop the image and rely on
    native Mermaid rendering. Adding any renderer dependency needs approval.
  - Register the generator and test in `docs/SOURCES_OF_TRUTH.md`.
- **Acceptance:** doc-freshness and feature-matrix checks pass; the chart test passes
  and fails on a deliberately stale fixture; no feature-matrix row changes (A-core
  adds no user-visible feature).

## Merge gate

**A-core is complete when** A-PR1 to A-PR5 are merged to `main`, each with:

- required checks green: `Backend gates`, `Frontend gates`, `Feature matrix gate`,
  `Pre-commit and secret scanning`;
- local gates for the touched area: `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run basedpyright --level error` (new errors
  clean against the baseline), both `bandit` runs, `uv run pip-audit`,
  `PYTHONPATH=. uv run pytest -q`, `python scripts/lint_feature_matrix.py`,
  `python scripts/check_doc_freshness.py --mode fail`,
  `uv run pre-commit run --all-files`;
- an independent review and the required top-level PR review comment, with no
  unresolved blocking threads;
- its documentation updated and its execution status row updated.

Optional work is not part of the exit condition.

**Stale branches:** if `main` moves and touches a file a PR changes, rebase, re-run
all gates and get the diff re-reviewed. Merge PRs that touch the same routing area
in sequence and re-review the second against the merged first.

**Blocked checks:** never merge around a required check. If a failure is
pre-existing or unrelated, record the blocker (issue per the anomaly protocol) and
get an explicit owner decision; never weaken a gate. If `scripts/pr_create.sh`
refuses, record why in the PR body.

## Deployment and runtime acceptance (not authorized here)

Merging does not prove deployment, and deployment does not prove runtime behaviour.

- **Deployment** is an operator action: build and roll out the image, select
  `DAEMON_INFERENCE_POLICY`, confirm route approvals. The deployment approvals in
  `config/inference_policy.production.json` expire on **6 October 2026**; after
  that, deployment routes fail closed regardless of this work.
- **Runtime acceptance** on an operator-controlled test account: `routing_event`
  lines appear in backend and worker logs and contain only allowlisted fields; one
  turn joins its ledger rows; Free after trial + reasoning classification returns
  `capability_unavailable`, not retryable; parity cases behave the same on the
  deployed build.

## Known limitations after A-core

- A Free account after its trial is still refused for reasoning-classified requests,
  now truthfully (O1 would change this).
- Over-broad English keywords still send some ordinary requests to premium (O5).
- Classification is English-only and current-message-only; short follow-ups such as
  "why?" do not inherit the previous turn (Package B).
- Paid holds still use the route maximum, so near-exhausted accounts can be refused
  and unknown-usage attempts settle at the full hold (#342, O2/O3).
- Code requests without an analytic signal run on Luna at low effort (D4 consequence).

## Anomalies to reconcile during execution

- Application INFO logs probably dropped (verify in A-PR4; file if confirmed).
- #331 appears implemented by #340; verify and close or comment in A-PR5.
- #342 remains open; A-core does not resolve it.

## Execution status

Update this table in each PR. Allowed states: Not started, In progress, PR open,
Merged, Blocked (with reason).

| PR | State | Branch / PR | Notes |
| --- | --- | --- | --- |
| A-PR1 classifier repairs | Merged | #384 (`4f9f0518`) | Security review clean; quadratic fence scan found and fixed before merge |
| A-PR2 endpoint parity | Merged | #386 (`193c47fc`) | Security review clean |
| A-PR3 truthful capability denial | Merged | #385 (`51f7684e`) | Security review clean |
| A-PR4 routing telemetry | Merged | #388 | Pre-flight confirmed uvicorn defaults drop application INFO logs; model-field free text never logged |
| A-PR5 docs and generated chart | Merged with the PR that adds this row | `docs/routing-chart-generated` | Image: generated SVG (option a), chosen under the 1 October authority to proceed on judgement; reversible. #331 verified as resolved by #340 (evidence comment; closure left to the owner) |
