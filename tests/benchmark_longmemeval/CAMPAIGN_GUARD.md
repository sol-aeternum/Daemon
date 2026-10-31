# Guarded evaluate-only campaign — implementation boundary

The owner approved option A on 2026-10-29: explicit, default-inert measurement
hooks at LLM/embedding sends and canonical runner stop checks. This is measurement
safety work, not a memory improvement, production budget authority or launch gate
waiver. PR #606's diversity candidate remains separately unmeasured/unmerged.

## Implemented primitive

`orchestrator/eval/campaign_guard.py` supplies an explicit campaign context, never
activated by an environment variable or normal production startup. A caller must
provide the ordered 50 IDs, per-question stage allocations, immutable campaign
pins and a token counter. The plan reserves the approved $4.35 whole envelope plus
the intact prior $6.0519050, for the approved replacement ceiling of $10.4019050;
it refuses allocations above the new reservation. The separate $1 capacity-screen
hold is not available to this campaign. One prior restore plus four replacement
restores is the maximum approved cumulative scope; the failed ledger is immutable.
These are local quoted-rate reservations, **not verified billing or a provider cap**.

Each A0/A1/A2/C0 arm requires embedding → reader → generic → official stages for
each question. Pending identity and request hash are strictly durable before send;
allowlisted raw receipt artifacts precede validated completion. Missing, malformed
or over-allocation usage, provider errors, cancellation, extra/reordered/concurrent
calls or durability failures latch a stop. Successful partial receipts stay retained.
No interrupted/open/stopped/completed campaign can automatically replay. A clean,
fully completed and externally qualified arm can hand off to a new pinned process
for the next arm, allowing control/candidate source revisions without live edits.

The owner-approved replacement uses a five-second minimum between LLM start
boundaries, serially and identically across all four arms. The async LLM hook waits
only the remaining interval, then revalidates and durably records pending before
dispatch. Embeddings are unpaced; calls outside an active campaign are unchanged.
Cancellation or a stop during the wait cannot dispatch. No automatic retries.

Hooks cover the benchmark LLM preparation/send boundary and qualified embedding
adapter dispatch/settlement. Each legacy embedding HTTP transport refuses an active
campaign before provider work, including when ordinary retry/fallback catches the
exception. The canonical runner preserves its partial row/checkpoint, then checks
the guard before advancing. Without explicit activation, existing production
admission, transport, accounting, error preservation and prompt behavior remain.

The campaign evidence directory must be outside the restored DB and share the
operator-pinned evidence parent. A nonblocking sibling file lock excludes other
cooperating campaign writers using that parent. It **does not prove all database
producers are excluded**, nor protect against arbitrary same-UID code. Receipt
files contain benchmark outputs and belong in private retained evidence, not Git.
Credentials and request headers are excluded from identities/artifacts.

## Common launcher and approved in-place restoration

`python -m orchestrator.eval.campaign_launch prepare` is read-only preparation.
It validates the frozen dataset, control/candidate source difference, shared code,
snapshot/ingest identity, public endpoint prices, cached tokenizer, catalogue and
protected fingerprint. It writes a private pinned plan and one COPY-aware-validated
restore script, hashed and reused unchanged across all arms. No inference or restore
occurs during preparation. Snapshot-derived SQL is private retained evidence, never
committed or supplied to a model reviewer.

For the approved stopped-A0 recovery, `prepare-replacement` instead requires exact
hashes of the original plan and failed ledger. It retains all prior reservations
and the pending call, reuses pinned immutable artifacts and historical catalogue
evidence, and verifies closed admission/identity/no sessions through a read-only
control connection. It never connects to or reopens the target during preparation.
Historical catalogue evidence is not a fresh measurement: the unchanged restore
boundary must recheck the live catalogue before DROP and the restored catalogue
and fingerprint afterward. Execution revalidates the recovery hashes. Original
plans with old accounting or without replacement lineage cannot execute.
The replacement directory is fixed to the failed campaign's sibling named with
`-replacement`; another directory cannot reset its allowance. An existing stopped
successor remains non-resumable. This shares the cooperative operator trust model
of the evidence lock, not protection against administrative copying/deletion.

### Explicit second replacement — owner-approved scope, 2026-10-30

`prepare-second-replacement` implements the separately approved stopped-A1 recovery,
not a loose resume/retry flag. It prepares offline from exact hashes of the previous
replacement plan and stopped ledger, recursively verifies the original replacement
lineage, and requires the exact stopped state: A1, question index 11, answer pending,
245 completed receipts, two verified replacement restores, closed admission, and
only A0 qualified. Both failed ledgers and all reservations remain intact. The
provider failure occurred during LiteLLM response conversion; an empty HTTP body
and final billing are **not established**.

The new plan retains $10.4019050 and adds $4.35: $14.7519050 campaign reservations,
or $15.7519050 including the separate, nonspendable $1 capacity hold. Only full
A1/A2/C0 executions are allocated; A0 remains the comparator, not an executable
recovery arm. Three prior restores plus at most three new restores gives a maximum
of six. There is no automatic fallback, partial-A1 reuse, third recovery, or reset
through a different output directory. The successor campaign is fixed to the failed
replacement directory plus `-recovery`; the plan filename is the previous replacement
plan stem plus `-recovery`, with the same suffix and parent.

The recovery evidence digest map binds both ancestor plans/ledgers, every completed
receipt, any partial pending receipt actually present, and the four qualified A0
artifacts. Preparation recomputes A0 qualification against the frozen fifty-question
dev set. Immutable archive/ingest/script pins remain unchanged. A source bridge binds
the old control to the new control by exact diff digest and before/after source-file
hashes: only `campaign_guard.py`, `campaign_launch.py`, `campaign_recovery.py` and the
diagnostic exception path in `evaluate.py` may change in the benchmark runtime
scope. This document is excluded as non-runtime documentation. Control/candidate
verification still permits only the original retrieval diversity difference; prompt,
prepared request, transport, pacing and retrieval behavior are not recovery variables.
Primary review must separately confirm that the allowlisted source diff preserves
the success path; a filename allowlist is not semantic proof.

Under the existing sibling lock, activation revalidates ancestors/evidence and the
fixed destination before exclusive creation. It durably copies exactly four A0
artifacts, checking source and destination hashes, and records imported provenance
without fabricating receipts or restores. Initial arm index is 1, with no new receipts
or restore attempts. A partially created destination is permanently non-resumable;
there is no cleanup/restart path. Clean handoffs recheck imported A0 hashes and the
recovery restore offset. Launch and the guard's immediately-before-`pending_restore`
hook reverify lineage, evidence and source bridge before mutation admission. These
checks retain the existing cooperative filesystem trust model. The restore module
and normal copied-A0 comparability/paired-result path are unchanged.

Provider exceptions in an active benchmark call produce a separate
`failure-<receipt_index>.json`, bound to the still-pending request identity. It
contains only static allowlisted exception classes (unknown becomes `UnknownError`),
integer HTTP status in 100–599, a bounded cycle-safe cause chain, and constant
classification. No exception serialization, raw bodies, headers, URLs, request data,
credentials or invented usage enters that artifact. Exact known-phrase classification
does not establish an empty HTTP body. Persistence failure retains pending and stops;
it never dispatches another request. Outside an active campaign the existing behavior
is unchanged.

This section records source implementation and approved scope, not live qualification
or execution clearance. Synthetic tests do not establish private evidence validity,
actual source bridge acceptance, current rates/privacy, database state, provider
behavior, final billing, or candidate acceptance. Final independent review, project
gates and exact-action Astra supervision remain required before any live action.

### Explicit stopped-official recovery and bounded 429 retries

The owner separately approved recovery of the exact stopped q49 official judge,
without regenerating the completed answer/generic judge or restarting A1. The
`prepare-retry-recovery` command and `retry_recovery_lineage` mode implement that
one exception. They do not loosen the second-replacement validator or authorize
arbitrary partial reuse, future automatic recovery, alternate output paths, or
provider fallback. Both the plan and campaign successor are fixed to `-retry`.

Preparation binds the exact approved parent plan/ledger, all 195 completed receipt
files, the sanitized `failure-0195.json`, all 49 A1 rows plus checkpoint/manifest,
and all four A0 artifacts, recursively rechecking earlier lineage. A pending partial
receipt, different pending request hash, wrong source/state, changed prefix, symlink
or foreign parent fails closed. The old four-file source bridge remains strict. A
separate retry bridge covers the reviewed guard/launcher/recovery/retry/attach/harness/
evaluator source delta; all other benchmark runtime source remains unchanged. `COMMON`
is not expanded, so old source-bound plans remain verifiable. New control/candidate
verification still permits only the retrieval diversity difference.

This mode **carries**, rather than adds, the existing $4.35 envelope: $10.4019050
prior plus $4.35 remains $14.7519050, with the separate nonspendable $1 capacity
hold unchanged ($15.7519050 total). The original three-arm allocation of $3.251061
leaves $1.098939 shared retry headroom. Every additional transport attempt reserves
the entire question/stage input allocation plus output cap at the pinned rates,
durably before waiting. Reservations never refund, including on success, failure,
cancellation or uncertain billing. A 429 is not evidence of zero provider billing.

An exclusive, non-resumable bootstrap copies exact retained prefix/receipt evidence
and attributes the already completed A1 restore explicitly: one local restore plus
three prior restores means four cumulative. Verified attach is separate from restore:
it durably records one-shot pending before any admission ALTER, verifies exact target,
closed admission and zero foreign producers, opens ten owned connections, recloses
admission and verifies catalogue/protected fingerprints before binding the fixed pool.
It runs no DROP, restore, migration or destructive psql. Failure uses bounded sealing
on the same control connection, never reconnecting, terminating foreign producers,
or replaying attach. A2/C0 alone consume restores five/six after clean handoffs.

Only this explicit active mode permits transport retries, inside one logical guarded
LLM call. LiteLLM retries remain zero and provider fallbacks remain off. Only a known
top-level `RateLimitError` with integer status 429 qualifies; known quota/exhaustion/
billing markers, timeouts, 5xx, empty responses and malformed usage never retry.
There are at most three total attempts, including the immutable historical failed
q49 attempt. Additional attempts wait 30 then 60 seconds, or a longer allowlisted
Retry-After seconds/date value. Malformed headers or waits exceeding 120 seconds stop
rather than clamping. Every actual start also respects five-second global pacing.
Per-attempt pending identities, full-bound reservations and dispatch states are
durable; unique sanitized failure artifacts never serialize exception/body/header/
URL/request/credential data or invent usage. Any interrupted attempt remains stopped.

Inside the canonical phase, a fresh fingerprint precedes importing the first 48
complete rows and q49 partial row. The q49 branch runs **before** the generic completed
result skip and calls only the official judge, with the historical request hash.
Only official fields/completion provenance change; original base fields and run IDs
remain intact. Q50 uses the normal four-stage path. The new executor manifest records
explicit inherited-evaluation/mixed-source provenance and retry policy; it does not
claim regenerated prefix rows or identical retry histories across arms. Scoring and
arm completion recheck prefix/provenance and settled attempt accounting in addition
to normal fifty-question qualification. No code-change/state-mismatch bypass is used.

Synthetic source tests and this contract do not qualify the real retained artifacts,
live attach state, external billing, paid execution, or candidate acceptance. Final
independent review, full project gates and fresh exact-action Astra review remain
mandatory. Six new scanner-discovered copy/bootstrap writer sites are explicitly
unknown, uncovered and deferred; account-generation/fencing runtime is unchanged.

### Exact completed-final-verdict successor — 2026-10-30

`prepare-final-verdict-recovery` is an offline, hash-bound import of the one
owner-approved, completed standalone A1 q50 official attempt 4. It permits no
inference or replay and does not relax the old max-three validator. The exact
parent plan/ledger and completed attempt/result hashes are pinned in
`orchestrator/eval/campaign_final_verdict.py`; only the fixed unused successor
plan and directory are accepted. The stopped parent and its terminal attempt 3
remain immutable. Exclusive interrupted bootstraps cannot resume.

All 199 old receipt files and the first 49 result lines are copied byte-identically.
Receipt 199 explicitly records a **normalized official result**, never a raw
provider/HTTP response. Q50's non-official base and original run ID remain intact;
the imported verdict is incorrect, and missing cost stays missing. Attempt 4 and
its $0.0025875 reservation are separate from the preserved historical attempt
ledger: $0.0226600 + $0.0025875 = $0.0252475 of the existing $1.098939 retry
headroom. Both ledger validation and subsequent A2/C0 retry admission count this
hold. The $14.7519050 campaign envelope and separate $1 capacity hold remain
unchanged ($15.7519050 total); A2/C0 retain max three attempts without resets.

A1 requires a fresh verified attach with no DROP or restore. Its canonical runner
seeds all 50 results without paid calls, then fingerprints the fresh finalization
window at start and end. The parent manifest has empty phases: no historical end
fingerprint is invented. The new manifest identifies inherited artifacts/run
provenance and explicitly limits its fingerprint claim to fresh finalization.
Three prior restores plus the attributed A1 restore remain four; only A2/C0 may
consume restores five/six after clean handoffs.

The one newly scanner-discovered bootstrap directory writer is explicitly unknown,
uncovered and deferred in the account writer catalogue; no scanner, account fence,
durable-task or quarantine runtime changes are made. Synthetic source checks do
not authorize live attach, paid execution, restore or candidate acceptance. Final
independent review, full project gates and fresh exact-action Astra review remain
required.

`python -m orchestrator.eval.campaign_launch arm` executes only one explicit arm.
It requires the clean pinned checkout and a fresh source/plan/arm/live-state-bound
Astra action-review artifact. This is trusted operator evidence, not a cryptographic
authorization mechanism. Do not fabricate an approval flag or replay an expired,
interrupted or uncertain action. The primitive methods still bind evidence; the
launcher supplies the actual checks rather than treating nonempty dicts as proof.

Owner-approved restoration **preserves the same database**, replacing only `public`:

1. Verify the exact container, loopback port, server system identifier, database OID,
   existing marker/owner, immutable archive/script and preserved evidence. Record a
   durable restore attempt/pending identity before any connection-state mutation.
2. Open the owned restore session and all ten evaluation connections. Disable new
   target connections, verify the exact owned PID set, and reject foreign sessions,
   prepared transactions, replication slots or out-of-scope persistent objects.
   Never terminate a foreign producer or use `DROP DATABASE ... FORCE`.
3. Verify catalogue definitions and the dependency identities reachable by CASCADE.
   Reject changed enums/types, schemas, ACLs, event triggers or unapproved objects.
   Run public replacement plus the pinned script in one transaction through the
   already-open session. Preserve the PG16 `pg_database_owner`/public-USAGE baseline.
4. Check restored catalogue and protected content fingerprint, then use the same
   fixed pool in the canonical runner. Admission stays closed; connection loss has
   no reopen/fallback path. Attestation stops before pool closure. Verify each arm's
   complete official coverage and qualification before advancing.
5. Leave admission closed between arms. Only clean, fully qualified campaign
   completion may restore its original open state. Any failed or uncertain action
   retains pending evidence for operator reconciliation, never an automatic retry.
   Setup failures after durable restore admission attempt one bounded closure on
   the same control connection, including failures during temporary reopening.
   Cancellation waits for that compensation before propagating. Only verified
   identity and closed-state readback record `failure_admission: closed`; control
   loss or ambiguous readback record `unknown`, requiring explicit reconciliation.

The database gate excludes ordinary new target writers. It does not claim isolation
from a same-UID administrator deliberately reopening the database from maintenance
SQL. Writes completed in `public` during connection setup are erased by replacement;
unexpected out-of-scope changes cause a stop, not deletion. Tests use a separately
owned, TCP-disabled PG16 fixture with public cached binaries, not the benchmark DB.

Before any real run, final source/gate evidence and action-time supervision must verify:

- Exact frozen IDs/dataset bytes, clean control/candidate SHAs and identical guard
  code/config, formatter and route pins, immutable snapshot hash and protected DB
  fingerprint. No code/state overrides, reentry or mid-run edits.
- Current approved route/privacy/rate evidence and cached tokenizer identity/chat
  overhead. The guard measures the **complete prepared** messages (including full
  generated hypotheses) plus a 64-token envelope allocation. This allocation and
  token counter must be validated, not inferred from historical usage or the old
  provisional 512-token boundary slack. Stop rather than truncate if any actual
  request exceeds its allocation. Inability to justify the $4.35 bound blocks launch.
- Exact restore command under required action-time supervision/pre-change review:
  approved container ID, loopback port, DB name, **existing** marker, archive hash,
  preserved evidence and effective exclusion of all other producers. Durably count
  the action before destructive work; uncertain restore stops, never retries.
  The existing `bench_db.sh restore` drops with FORCE and lacks these prechecks;
  never invoke that helper unguarded.
- Completed arm rows and both evaluate/score manifests: exact official coverage,
  all qualification/comparability/nonzero/plausibility/category/abstention gates and
  judge disagreement stop. Bind actual artifact hashes, not a bare success flag.
  Candidate compares only with A0; F is maximum pairwise official flips among the
  three controls. Embedding-only scope is approved; historical B is not a qualified
  lexical comparator. No candidate reruns until success.

### Historical-clock corpus (#601 remap) — owner-approved, 2026-10-31

`prepare --historical-remap LINEAGE` plans a campaign on a benchmark-only remapped
corpus: a copy of the qualified ingest whose stored times were moved onto each
session's haystack date. Preparation requires that the lineage derives from the
qualified snapshot/state constants. `--snapshot` must be the lineage's snapshot,
and `--ingest` must be a derived checkpoint whose `historical_remap` block hash-pins
this lineage and the original ingest checkpoint. `fact_harness.verify_historical_remap`
then requires the derived ingest to equal the original except for its
protected-state fingerprint, which must be the lineage's remapped state.
Qualification is the original ingest run's manifest; no new qualified manifest
is written. The live database is still verified against the original state at
preparation. Arms restore and evaluate the remapped snapshot/state pinned in
`pins.historical_remap` (`campaign_validation.expected_corpus`). Plans without
that block keep the original constants, so earlier campaigns verify unchanged.

The runner enables historical-clock mode only from a verified remap source:
retrieval then also receives `query_reference_time` = the question's as-of time,
and the manifest records the historical `message_timestamps` parity exception.
Comparability therefore refuses to mix the two modes, and a resumed run whose
recorded exceptions differ from its corpus's mode is refused.
The mode is re-verified wherever it is used: the recorded derived checkpoint must
be byte-identical, pass the lineage checks and hold exactly the run's ingest phase,
so an edited resumed checkpoint cannot switch it on. `run_arm` also requires the
plan's `historical_remap` pin and the run's verified source to agree. The existing
replacement, retry-attach and final-verdict recovery paths still require the
original corpus and fail closed for remap plans; a remap campaign has no approved
recovery path yet.

All final source gates/review and actual-state launch checks remain required. No
inference, ingest, restore, holdout inspection, source expansion, date adaptation,
reader-cap change, production deployment or candidate acceptance is established by
the synthetic guard tests. See [ratified acceptance rule](../benchmark_results/ACCEPTANCE_RULE_PROPOSED.md).

## Source verification — 2026-10-29

- 236 targeted tests passed, including the real LLM/qualified-embedding hooks,
  partial canonical result preservation, malformed usage, strict durability,
  duplicate writer, no-replay and four-arm clean-handoff cases. All inference and
  database collaborators were synthetic or mocked; no benchmark calls occurred.
- Full backend gates passed: 7,548 tests / 1,298 skips / 16 subtests, clean types,
  lint/format, high-severity SAST and dependency audit. The nonblocking full Bandit
  inventory remains reported, not suppressed.
- Full frontend gates passed: 1,634 tests, cache-browser regressions, locked install,
  security checks, types, lint/format, audit and build. Repo feature-matrix and
  pre-commit/doc/secret gates passed.
- A fresh enforced read-only OpenAI reviewer reported no source findings. This
  was a disclosed same-family fallback, not model-family-diverse review, and did
  not validate the deferred launcher/restore/tokenizer/qualification proofs.
- Writer inventory records the eleven new scanner sites explicitly as deferred
  and uncovered; the scanner/gates and account-generation/fence runtime were not
  changed. No production behavior, candidate acceptance or launch is established.
