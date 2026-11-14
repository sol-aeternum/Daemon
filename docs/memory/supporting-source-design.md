# #601 supporting-source publication design (core model)

Status: **Proposed implementation contract; no implementation authorization.**
Prepared 12 November 2026; reduced to the core model the same day after a review the owner requested.

### Scope after the 12 November review

This document now covers only the source/assertion model, rendered-context
capture, tool-result adapters and atomic memory publication (§§3, 5, 7, 9).
The full earlier draft, including every removed section, is preserved only in
the history of the unmerged branch `fix/601-natural-memory-offline`; it is not on
`main`.

| Earlier part | Disposition |
| --- | --- |
| D1: saved notes surviving evidence expiry (§3.1) | **Deferred.** No existing adapter can meet the post-expiry permission contract. Strict expiry applies: no derivative survives ordinary payload expiry. Reopen only with a concrete eligible adapter. |
| D2: reminder ownership, legacy claims, recovery ledger (§8) | **Replaced by the #695 fix.** The live `data/reminders.json` holds 0 entries, so nothing is imported or claimed. Reminders are now an encrypted account-owned table (PR #698, merged). |
| D3: PostgreSQL-authoritative skills (§4) | **Split out** to `docs/ACCOUNT_OWNED_SKILLS_DESIGN.md`, since implemented (PRs #701–#708, merged). #91 is still open. |
| D3: contextual-home revocation (§6) | **Split out**, gated on a read-only legacy inventory whose result decides whether the design is acceptable. |
| D4: #695/#696 as qualification prerequisites | **Fixed directly** as standalone changes (PRs #698 and #697, merged). |

**Approval boundary:** nothing here approves runtime implementation, migration or
cutover. Actual checks are recorded in §13; the behavioral matrix in §11 is
planned, not executed.

## 1. Authority and boundaries

The owner approved this direction, subject to review:

- Track actual memory-tool inputs and version supporting sources before certifying
  model-written assertions. Preserve the earlier segment-level correction design.

This document proposes the concrete contracts, tests and unresolved release
conditions. It implements nothing, changes no existing feature status, and
authorizes no production migration, deployment, paid inference or M2 changes.
The correction-admission and summary-provenance foundations (migrations
`071_message_correction_admission.sql` and `072_summary_provenance.sql`, with
`memory/summary_provenance.py`) exist only on the unmerged branch
`fix/601-natural-memory-offline`. They are not on `main` and not part of this
document. Every reference below to 071/072 or `summary_provenance.py` means that
unmerged work; its recorded verification is retained there.

DEC03/DEC08 require inherited restrictions; DEC05 distinguishes acceptance from
completion; DEC11 requires portability. Current Z-only execution remains unchanged.

## 2. Source evidence and design consequences

| Existing source | Consequence |
| --- | --- |
| `memory/store.py:get_user_settings/update_user_settings/merge_user_preferences`, `memory/injection.py:format_preferences_block`, `timezones.py` | Settings need a managed source revision; rendering includes precedence/default rules and resolved timezone, not arbitrary settings JSON. |
| `migrations/053_resource_registry.sql`, `054_resource_input_batches.sql`, `resource_inputs.py` | Existing resource identities are reusable only where actually composed; prepared text alone is not a permission/version receipt. |
| `services/web_snapshots.py`, `tools/web_fetch.py`, `tools/completion.py` | Pin the selected immutable snapshot and final returned projection, including the separately prepended saved-source list. |
| `tasks/fence.py:FencedTool.execute_invocation`, `tasks/store.py:_worker`, `migrations/050_task_inference_recovery.sql` | The operation-start receipt commits before the inner tool; the recovery SQL helper already locks account/lifecycle/task rows. Neither is an atomic memory-publication receipt. |
| `memory/revisions.py:mutation`, `summary_provenance.py:validate_reference` | Reuse original capture, same-connection publication and post-lock source revalidation. |

## 3. Common source and assertion model

Proposed additive logical tables; exact DDL and migration numbering require a
separate reviewed implementation patch. Do not modify applied 071/072 schemas.

1. **Source heads:** immutable source UUID, scope (`account` or explicitly
   installation-published), account when private, kind, incarnation, monotonic
   state/lifecycle revision, active version and availability. Scope/owner cannot
   be changed in place. Delete/recreate uses a new incarnation.
2. **Source versions:** immutable revision identity, source identity, original
   account generation where applicable, renderer/format version, sealed dependency
   membership and bounded encrypted payload only where an existing immutable
   payload cannot be referenced. Payload purge is separate from identity metadata.
3. **Observations/manifests:** owner, originating generation/erasure epoch,
   request/dispatch/invocation identity, exact ordered rendered inputs and their
   dependency references. Closed classifications: source-backed, external
   observation, derived result, fixed control, unsupported.
4. **Assertion support:** immutable support UUID attached to each memory segment,
   with version-qualified source edges and parent support IDs. Splits/copies keep
   support identity; new assertions get new support. Canonical reuse does not union
   new sources into an existing assertion. Independent segments remain independent.
5. **Lifecycle roots:** source revocation/expiry and restricted scope authority. Every derived object must retain its roots, including assistant
   continuations used as future model input, summaries, tools and indexes.

Composite owner constraints prevent a private source/observation/segment from
being linked to another account. Installation skills are an explicit exception
only for operator-published non-private content, never inferred from a filename,
`system` label or `created_by`. IDs and scopes come from authenticated server
context; model arguments, imported manifests and Git metadata grant no authority.

Separate **freshness** from **continued permission**. An in-flight publication
must match its captured versions. A published assertion may retain historical
evidence from an older version if that source kind permits it. Revocation,
deletion, permission expiry, changed locality or lost permission overrides every historical
cutoff. Settings/skill/reminder semantic replacement conservatively retires old
support; clock passage or usage counters alone do not revoke assertions. Reactivation
is a new publication, never clearing an old revocation marker.

Ordinary payload-retention expiry is a different transition, `payload_expired`,
not a synonym for revocation. In this revision no derivative survives either
transition (D1 is deferred); the distinction is recorded so a later, separately
approved exception has something to attach to. Unclassified removal defaults to
revocation; a purge job cannot guess that a DELETE meant harmless expiry.

All readers filter qualification before ranking, LIMIT, counts, decryption and
export. A cached index hit is a candidate, not authority. Immediate exclusion is
transactional; cleanup may be asynchronous only after exclusion is effective.
Missing/partial closure is unknown, never an empty independent closure.

## 4. Published skills and portable editing

Split out to a separate design (see the scope table). Until that design lands,
the skill index is an unversioned local input; §12 lists the decision this forces.

## 5. Settings and actual rendered context

Version only consumed preference fields and timezone contribution using the exact
renderer precedence/defaults. Preserve absent versus explicitly default values.
Display-name-only changes do not invent a prompt dependency. Arbitrary settings
or credentials are not copied into observations.

Both settings writers update governed revision and active source version in the
same transaction. Raw DML advances a monotonic revision and invalidates any stale
sealed head; it cannot generate a fictitious encrypted version. A trusted renderer
may seal the current baseline under lock before use. No historical backfill.

Dispatch captures what was actually sent, including prepared attachments, history,
memory segments, skill-index selection, time block and retained tool results.
Preserve original message UUID/version through native and durable preparation;
prepared extra bytes need their own received-input/source references. Unsupported
or unsaved inputs cannot certify a memory mutation; ordinary chat may report the
storage limitation rather than claim the memory was saved.

For each returned tool batch, all sibling calls use the generating dispatch's
manifest. A prior sibling's result becomes input only in a subsequent dispatch.
Observe tool output after final truncation/omission/wrapping. Omitted fixed markers
exclude discarded payloads; retained assistant continuations retain their original
closure. Bind invocation UUID, ordinal, active lifetime and actual asyncio task;
ContextVar propagation alone is not authority. Reset bindings before yielding.

## 6. Contextual-home revocation

Split out to a separate design, gated on the read-only legacy inventory (see the
scope table). Until then accepted home copies are unversioned local inputs (§12).

## 7. Resources and tool-result adapters

| Input/result | Required evidence and lifecycle |
| --- | --- |
| Received attachments | Exact received/rendered text/image/placeholder, original capture; retain local resource/version linkage when present. Do not call metadata-only input an uploaded original. Preserve current accepted formats; C1 PNG-only qualification cannot silently narrow ordinary inputs. |
| C1 resources | Actual composed account/resource/version/generation pins, current permission, source-detachment state. Retained bytes after conversation deletion do not renew the old derivation. No invented project permissions. |
| Saved web read/find/list/manifest | Selected snapshot UUID and actual section/match/metadata projection; expiry checked on every read and publication. Deletion/cascades revoke use before removal. Both TTL purge paths record a typed expiry transition; no derivative survives ordinary payload expiry in this revision. |
| Web search / HTTP | Bounded encrypted observation of normalized final response plus input closure and trusted origin metadata. Exclude authorization headers, cookies and credential-bearing URLs from provenance. Bound response reads before buffering; never silently retain a full response behind a truncated result. |
| Time / calculation | Captured clock/timezone observation; evaluator version and argument closure. Neither becomes a new independent user fact. |
| Notification / document | Operation acknowledgment and argument closure; document assets retain original lifecycle, with invalidate-before-unlink at existing cleanup. Receipt is not proof of delivery or independent evidence. |
| Memory / reflection / skills / reminders | Actual selected revisions and support closure; list membership is a versioned observation. Never relabel local-source text as an external observation. |
| Errors / omitted / unsupported | Only fixed normalized controls have empty semantic closure. Dynamic errors retain dependencies or are normalized. Unknown adapters refuse dependent memory publication. |

Private `ToolExecution` evidence is generated by adapters, not parsed from model
arguments or response text. No public SSE contract change is proposed. New receipt
payloads cannot outlive the original source, conversation/tool-trace lifecycle or
approved cap. Bounds and expiry must be pinned in implementation review, not guessed
from available disk. No complete plaintext prompt or semantic receipt hash in logs.

## 8. Reminders

Replaced by the #695 fix (see the scope table). Reminder notes become ordinary
account-owned rows; as a memory-tool input they follow the §7 adapter row for
local sources.

## 9. Atomic memory publication and concurrency

### Existing helper/trigger interactions (source facts, not new guarantees)

| Path | Actual interaction / consequence |
| --- | --- |
| `tasks/store.py:_worker/_references` | Recovery helper precedes account/task/attempt/message locks. Message `FOR UPDATE` query is not explicitly UUID-ordered. Cancellation is checked before yield; post-yield checks cover invocation state and lease time. The prefix can wait before any proposed temporal try-lock. |
| `050_task_inference_recovery.sql:task_inference_recovery_lock` | Account advisory and account FOR UPDATE, deletion rows, lifecycle, task, attempt, compute metadata and recovery locks. Function-local `lock_timeout='250ms'` does not bound later Python-issued SQL. Observation is not mutation authority. |
| `services/account_control/guard.py:revalidate` | Blocking account SHARE plus a fresh statement for causes/operations; no commit ownership or timeout. Savepoint rollback before acquired locks requires revalidation. |
| `memory/revisions.py:enter` | Unordered advisory try-lock, then clock INSERT ON CONFLICT **before** clock-row NOWAIT; updates mutation receipts/clock and inserts a mutation. FK/uniqueness/conflict waits remain possible. Owned `mutation` chooses the blocking ordered path; borrowed mutation neither commits nor observes commit. |
| SQL066 revision triggers | BEFORE requires the transaction's mutation record but does not take the temporal barrier. AFTER inserts revisions/segments, with FK/unique/index work; active-content uniqueness can conflict with a different target ID. |
| SQL067 message guard | Manages revision/admission and reads conversation state; does not lock source conversations for a later consumer. Enclosing DML and FK constraints still acquire locks. |
| SQL068 summary guards / `validate_reference` | Guards check immutable/sealed membership but do not stabilize all source rows. Python validation takes blocking conversation/message SHARE locks; cannot be reused unchanged for a NOWAIT late join. |
| `services/account_control/transaction.py:control_transaction` | Distinguishes pre-commit, COMMIT attempted and acknowledged states. Only acknowledged rollback/release proves a clean refusal; cancellation/termination/release failure is not rollback proof. No enclosing deadline is supplied by this helper. |

Thus the proposal is **nonwaiting explicit late acquisitions with bounded failure
for remaining SQL**, not a fully nonblocking or already proven deadlock-free
transaction. Cooperative locks alone cannot certify arbitrary raw SQL writers.

Native publication uses original capture, account guard, temporal barrier, stable
source/head locks and target CAS on one connection. Durable publication additionally
requires current task/attempt/lease/cancel/inference authority on that connection,
including final checks before commit. The operation-start receipt is not sufficient.

The concrete proposed lock sequences are:

- Native memory publication: account SHARE guard → temporal advisory barrier and
  knowledge-clock row → conversations ordered by UUID → source roots/versions
  ordered by kind and UUID → target memory/segment CAS. Source locks remain held
  through commit; requalify after waits.
- Covered durable publication: existing recovery helper's account advisory →
  account/control rows → lifecycle → task/attempt → inference metadata/recovery;
  then existing `_worker` account/authentication and referenced message locks.
  Only then attempt the temporal advisory lock with `pg_try_advisory_xact_lock`
  and the knowledge-clock row with NOWAIT. Every subsequently acquired conversation,
  source/root and memory row lock on this late-join path also uses NOWAIT, in the
   native sorted order. Contention attempts whole-transaction rollback and is a
   definite refusal only after rollback/release acknowledgment. No blocking fallback,
   automatic retry or authority recapture.
- Non-covered durable publication: existing account advisory/account check →
  task/attempt/message references → the same nonblocking temporal/source join.
- Conversation deletion retains account guard → temporal → conversation → active
  task check. Running-task refusal is preserved. The late durable join cannot wait
  on locks this path holds while deletion waits for a task/message lock.

The implementation must inventory *all* SQL-trigger and helper-acquired locks,
including relation, FK parent, unique/conflict and INSERT locks. Not every implicit
lock offers NOWAIT. Proposed publication-specific limits: set transaction-local
lock timeout to 250 ms and statement timeout to 1 s **before the existing blocking
prefix**, with an enclosing monotonic 2 s publication budget capped by remaining
request/lease authority. These are review targets, not changed global timeout policy;
they are initial engineering limits to validate under contention and realistic
workloads, **not product latency, completion or remote-stop guarantees**. An
insufficient budget refuses publication. Connection acquisition, rollback and
release need owned cleanup and retained phase evidence; elapsed budget does not
prove server rollback or stopped work. No retry-to-success or increased limits to
make qualification pass. A reviewed implementation may instead restructure a path
to avoid an implicit late conflict, but may not silently remove the bound.

No wrapper may fall back from NOWAIT to blocking SQL. Existing `enter` and summary
validation helpers must be adapted/qualified for the chosen path rather than merely
wrapped in a try-lock. Retain `_worker`'s
actual final inference/lease checks on the same transaction. Its current cancel
check is before yielding, with the task row held locked; it is not a post-yield
cancel check. The publication invariant is: cancellation committed before task-lock
acquisition refuses publication; publication that acquired that lock first may
commit before a competing cancel is acknowledged. Recheck cancellation immediately
before commit as a defensive same-transaction check, without claiming it observes
an uncommitted cancellation blocked on our lock. Tests T1/T2 must prove
the actual sequence, including SQL helper locks and uncovered attempts.

Precompute/provider work stays outside transactions. Inside the short commit:
revalidate after lock waits, bind a task-owned borrowed connection, write segment,
sealed support and invocation result receipt together, then revalidate task authority.
Unknown COMMIT is reconciled by original operation identity and recorded result;
never issue a new write identity or replay based on an extraction log. Lease loss,
cancel, source change or stale target produces a terminal conflict/refusal.

Keep three receipts distinct: **operation-start admission** (already committed
before the inner tool), **atomic semantic-publication result** (new stable command
identity, assertion/support/result together), and **later commit-visibility
observation** (temporal evidence). Reconciliation queries the original publication
identity under its owner; operation-start alone cannot prove a write or authorize
replay. A committed result followed by failed connection release remains committed
but unavailable; lost COMMIT acknowledgment remains uncertain; pre-COMMIT failure
is “not published” only with affirmative rollback evidence. No source generation,
target fingerprint or invocation identity is refreshed to cure any of these states.

The dedicated natural-correction workflow remains subsequent work: unique
`(owner,user_message_id,natural_correction_kind)` across generations, one admitted
message, one certified target, unchanged fingerprint, encrypted receipt and
minimal consumed/revoked marker; reconciliation precedes explicit abandonment.

## 10. Rollout and retention

1. **Inventory, no mutation:** enumerate writer/reader adapters and payload sizes.
   Report counts privately; do not send real content to models.
2. **Additive schema:** immutable IDs, constraints, lifecycle roots, observation
   tables and startup qualification. No legacy provenance fabrication.
3. **Shadow mode (new, required before any enforcement):** capture manifests and
   evaluate every publication rule on real dispatches, but refuse nothing. Record,
   per rule, how often a memory write *would* have been refused: unsupported
   adapter, unversioned local input, per-item or per-dispatch receipt cap, account
   or installation quota, lock or statement timeout, publication budget. Content-free
   counters only. Enforcement is proposed per rule, from these measurements.
4. **Qualify offline:** run the §11 matrix with synthetic fixtures, including
   actual PostgreSQL locks. Review final source.
5. **Enforce per rule:** each refusal rule is switched on separately, with its
   measured refusal rate and user-visible wording approved by the owner.
6. **Rollback:** disable enforcement or return to a compatible binary. Do not drop
   revocation metadata. Correct bad data with a new validated revision.

Crash rules: before commit, no active change; after commit before acknowledgment,
look up the original command; cleanup failure leaves sources revoked and payload
removal pending; restart requires schema/authority qualification and resumes only
idempotent jobs with stable identities.

### Proposed retention schedule for owner review

These are proposed policy values, not claims about existing implementation:

- External observation copies: attach to the existing conversation/tool-trace
  lifecycle, with an additional maximum 30-day payload lifetime; earlier source
  expiry wins. Derivatives are excluded at every K. Existing snapshot policy is
  unchanged.
- Observation payload cap: proposed 64 KiB per textual tool observation, 256 KiB
  aggregate new textual receipts per dispatch. Existing stricter response limits
  win. Existing immutable large/image inputs use qualified references, not duplicate
  binary receipts. Overflow yields an explicit storage-unavailable result, never
  silently certifies truncated evidence for untruncated model input.
  Measure bytes of the canonical final plaintext UTF-8 observation envelope,
  including normalized output, wrappers, attribution and dependency descriptors,
  after escape/serialization rules are fixed. Per-item maximum is 65,536 bytes;
  aggregate maximum is 262,144 bytes. Equality is permitted, one byte over is not.
  Repeated source references still count their serialized descriptor bytes; they
  do not license hidden payload storage. Encryption/framing/storage overhead is
  accounted separately against a finite storage quota, not silently included in
  the content budget or ignored. Partial-result truncation occurs before model
  dispatch and observation sealing and must preserve valid UTF-8/JSON; if the
  actual sent input cannot be represented within bounds, mark dependent storage
  unavailable rather than forge complete coverage.
- Finite receipt-storage quota (new proposal for confirmation): 16 MiB per account
  and 1 GiB across the installation for this new observation/receipt payload class,
  counting ciphertext, envelopes, descriptors and staged/pending reservations, not
  only plaintext. Existing stricter limits win. Saved-memory content has its own
  governed store; this quota cannot be bypassed
  by relocating a receipt there or deleting required revocation metadata. Account-
  qualified atomic reservations precede storage, use the reviewed lock/budget
  contract, and survive uncertain commit until reconciled. No concurrent overfill,
  partial receipt, silent eviction of accepted state or limit increase on failure.
  At capacity, deny dependent memory publication with a visible storage-unavailable
  result; ordinary chat may continue under its separate rules. Validate these
  initial quota values against representative workloads before confirming them.
- Minimal content-free IDs, command outcomes and revocation/audit metadata: retain
  for the owning account lifetime where required for replay safety; account purge
  removes them. Installation metadata follows explicit operator retirement policy.

No Git clone, downloaded export or disconnected client is remotely erasable by
Daemon. Explain that boundary on export. Application backups remain subject to
existing approved retention and restore-replay gates, not these shorter cache TTLs.

## 11. Acceptance tests and review gates

All below are **specified, not executed**. Prior 071/072 gates do not qualify this
new design. Use fictional two-account fixtures, no live providers or production DB.

| ID | Discriminating evidence required |
| --- | --- |
| P1 | Preferences/defaults/timezone/skill-index selection and fallback match exact sent bytes; raw DML and ABA cannot retain stale certification. |
| I1 | Native/durable actual dispatch parity, attachment transformation, selected web versions, partial output, suppressed tools and prepended source-list coverage. |
| I2 | Sibling-call vs next-dispatch provenance; copied asyncio context, expired binding, nested reflection and generator yield cannot borrow mutation authority. |
| E1 | Web expiry/delete/cascade, resource detachment and generated-file cleanup invalidate copies before removal; no older-version fallback. |
| B1 | Envelope sizes 65,535 / 65,536 / 65,537 bytes and aggregate 262,143 / 262,144 / 262,145 bytes: exact accept/accept/refuse boundaries, including empty and multi-observation cases. |
| B2 | Multibyte UTF-8, JSON escaping, long attribution/dependency lists, wrappers, truncation/omitted markers, repeated references and encrypted framing: measure canonical bytes, never split encoding or undercount; source-body absence after expiry is inspected. |
| B3 | Realistic synthetic mixes: long history + multiple search snippets, maximum skill index, partial web sections, attachment references and multi-round tools; measure envelope/ciphertext sizes, quota reservations and storage-refusal frequency without paid inference. Test account/global quota at one byte below/equal/above, concurrent last-slot reservations, crash/unknown commit and expiry reclamation. |
| T1 | Actual PG publication vs cancellation/lease expiry/account reset/source deletion, both lock orders, bounded contention without deadlock or partial assertion. |
| T2 | Uncertain commit, duplicate invocation and worker restart cannot double-publish or overwrite a later correction; no refreshed generation/target. |
| T3 | Covered/uncovered prefix message contention, first-clock INSERT/FK conflict, unsealed mutation updates, different-memory active-content uniqueness, revision/segment trigger writes and summary SHARE locks: bounded failure with acknowledged whole rollback, or truthful uncertainty. Inspect clock/mutation/revision/segment/support/result state, not only exceptions. |
| T4 | Lease expires during wait; cancellation before/after task lock; COMMIT response lost; acknowledged commit then release failure; cleanup cancellation. Verify original-identity reconciliation, no replacement effect and no false “nothing written.” |
| M1 | Shadow mode: every refusal rule evaluated on real dispatches with nothing refused; content-free per-rule counters reconcile with dispatch totals; turning a rule on changes only that rule's outcomes. |
| X1 | Account purge, source erasure, expired payloads, raw DML, corrupted ciphertext and unsupported adapters fail closed; no semantic content in audit/logs. |

Before implementation: primary source adjudication, one eligible independent
pre-change review, and owner review of this full design (including proposed legacy
and retention behavior). Before completion: final diff review, focused PostgreSQL/
crash tests and all repository blocking gates; preserve nonblocking inventories.
Update feature matrix, API/UI documentation, migration/reset/startup catalogues
with implementation, without claiming deployed status. No tests may weaken existing
071/072, account, transport, temporal or erasure assertions to accommodate the design.

## 12. Outstanding exact decisions

- **Unversioned local inputs.** The skill index and accepted home copies reach the
  prompt but have no source versions until their own designs land. Either they are
  recorded as an explicit `unversioned local input` classification that does not
  block publication (recommended for shadow mode), or every dispatch that includes
  them is uncertifiable and memory writes refuse. Owner decision required; shadow
  measurements (§10 step 3) should inform it.
- Proposed receipt caps, the 16 MiB/account + 1 GiB installation quotas and the
  transaction budget values, each confirmed against shadow measurements.
- Concrete API/CLI payloads and DDL are implementation-review artifacts; no new
  endpoint, authorization role or public event schema is silently approved here.

These are visible activation/implementation gates, not permission to substitute
uncertified sources or lose existing user data.

## 13. Pre-change review record

The earlier full draft received three enforced read-only OpenAI-family reviews
(same-family fallback disclosed; prior DeepSeek and Anthropic authorship excluded
those reviewers). Each returned conditional acceptance for design discussion only.
Findings that still apply to this reduced scope: the late durable lock sequence and
nonblocking refusal policy need concurrency qualification, and the description of
the existing cancellation check was corrected to the pre-yield check/task-lock
serialization invariant in §9.

A 12 November review of the full draft, requested by the owner, found it bundled five independent
projects, that its reminder migration had no data to migrate, that D1 had no
eligible source, and that refusal rates were unmeasured. This revision is the
result. It has not itself been independently reviewed.

No behavioral test, runtime migration or production inventory has been performed
for this design. Prior 071/072 test receipts remain evidence for the unchanged
foundation only. All rows in §11 are acceptance specifications awaiting
implementation authorization.
