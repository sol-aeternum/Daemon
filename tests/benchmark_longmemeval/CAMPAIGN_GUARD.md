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

## Deliberately not implemented by this primitive

There is **no paid-launch CLI, destructive restore command or automatic recovery**.
`start_arm(verified_pins=..., restore=...)` and `finish_arm(qualification=...)` bind
the operator's independently verified evidence. Nonempty dictionaries are not proof
of identity, restore completion, dataset freeze, qualified manifests or acceptance.
Do not wire those methods to fabricated flags or invoke them as substitutes for
the remaining checks below.

Before any real run, implement/test/review the common launcher that verifies:

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
