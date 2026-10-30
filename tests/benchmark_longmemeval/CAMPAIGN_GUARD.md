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
the intact prior $1.7019050; it refuses allocations above the new reservation.
These are local quoted-rate reservations, **not verified billing or a provider cap**.

Each A0/A1/A2/C0 arm requires embedding → reader → generic → official stages for
each question. Pending identity and request hash are strictly durable before send;
allowlisted raw receipt artifacts precede validated completion. Missing, malformed
or over-allocation usage, provider errors, cancellation, extra/reordered/concurrent
calls or durability failures latch a stop. Successful partial receipts stay retained.
No interrupted/open/stopped/completed campaign can automatically replay. A clean,
fully completed and externally qualified arm can hand off to a new pinned process
for the next arm, allowing control/candidate source revisions without live edits.

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
