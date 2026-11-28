# Isolated application text flow

**Synthetic application qualification and final source review completed; no
production activation.** This bounded slice
connects the existing application page to the isolated original text runner from
[#706](https://github.com/sol-aeternum/Daemon/pull/706). It supports the continuity
direction in DEC04–DEC05 and DEC11; it does not activate a phone pilot. Keep
[#477](https://github.com/sol-aeternum/Daemon/issues/477) and the applicable
[#704](https://github.com/sol-aeternum/Daemon/issues/704) permission qualification
open. Product direction, source qualification and deployed acceptance are distinct.

## Composition boundary

Use the existing application page, Next `/api/chat` proxy, authenticated acceptance,
original task/attempt, budgets, qualified inference route, output persistence and
explicit Stop. Do not construct another runner or infer execution authority from a
history record. The provider is deterministic and synthetic in this qualification;
`mock_llm` is not a substitute for exercising the original execution boundary.

The application also needs the existing conversation list/detail and task-status
response contracts. The isolated bridge must qualify those reads under the same
history, authentication, original account generation and native identity checks.
Mounting the ordinary routers, or checking a key then fetching unqualified native
history, does not establish that boundary. Existing by-key recovery remains
available; it is necessary for the current application's reconciliation path.

This is explicitly constructed isolated composition only. Default acceptance-only
construction, production observation-only ingress and production worker refusal
remain unchanged. There is no deployment selector, environment flag, migration,
grant, new SSE type or production API activation in this slice.

The inert `create_isolated_ui_app` factory installs the three read adapters on the
existing isolated app. It is never selected by ordinary application startup.

## Bounded read contract

The qualification envelope is one original text task and its exact original
user/assistant pair in one otherwise-empty conversation in the account fixture.
Conversation listing must verify the whole envelope, not hide additional objects
behind a singleton response. Empty history requires positive healthy-account and
empty-content checks. Extra or mismatched tasks/messages, mixed history and
unsupported fields refuse rather than silently truncate or fall back.

An authenticated owned operation retains native transaction/cleanup ownership
through original-generation guards, identity and request-commitment checks, locked
message projection and final expiry revalidation. No response succeeds before
physical cleanup and history release finish. Failed owners are not renewed.
Foreign-resource absence must remain indistinguishable from healthy absence;
unavailable evidence is not absence. Handled responses are no-store. Existing
non-retryable recovery/unavailability responses do not authorize replacement work.

Use existing response models and owner/conversation-scoped value cursors. Message
pages preserve strict `before` and inclusive `through` bounds, including the
application's covered-range refresh. List totals precede cursor filtering. Cursors
are not authorization, and supplied bounds must never be silently ignored.

Only source-validated text fields and state-consistent metadata are in scope.
Preserve actual saved content, identity, timestamps, model and token counters; do
not manufacture usage or route information. A normal saved `reasoning_model`
without reasoning text is distinct from admitting reasoning content. Populated
unsupported tool, advisor, artifact, reasoning or arbitrary metadata refuses;
normalizers must not turn malformed data into apparently complete empty output.

Completed text requires the original terminal-v3 evidence and surviving native
result. Queued/running text is surviving SQL, not proof that a process is alive or
can resume. Current-host Stop observation requires original qualified evidence and
native state; in-memory scheduling membership is only an additional restriction,
never a receipt. Control-terminal recovery after process loss is not generally
qualified by this bridge. Unresolved work must remain visibly unresolved.

The original Stop path may persist `inference_dispatch_possible` and
`material_effect_possible` as true-only uncertainty warnings. The bounded
current-host cancelled read must preserve those native warnings, not erase them
or manufacture false values. Matching original Send evidence requires both
warnings; native execution evidence requires the material warning. Wrong types,
inconsistent evidence and unknown metadata refuse. This read restriction is not
a new inference-control API or a cancellation restore certificate. The existing
page warns: “Daemon stopped local work; provider activity or charges may continue.”

## Separate #761 inbox acceptance proof

The [#761](https://github.com/sol-aeternum/Daemon/issues/761) fixture is an
acceptance-only experiment against `29764eb759b5c3dc4fcda97d74f9296e84b79c63`.
It does not change this document's singleton app bridge or activate a chat
surface. `tests/history_inbox_support.py` owns its synthetic HTTP app and SQL;
there is no production route, migration, role grant, dependency, or flag change.

- The disposable database administrator provisions one inbox per account and
  generation. Only the fixture acceptor can insert links, and it can read roots
  but not links. The fixture worker role reads both tables but writes neither.
  Neither role owns, mutates, or deletes existing fixture links. Each link binds
  the authenticated account and generation to the original task, child
  conversation and two messages.
- With the original H ownership, W authentication, and account-generation
  capture, the native A transaction inserts the task and its messages, stages
  the link, durably appends `ACCEPT_PREPARE`, and stages the existing SQL
  projection on the same connection. The server-authored inbox marker is inside
  the existing canonical request commitment; the allocated IDs are in
  `ACCEPT_PREPARE`. Neither an H-only prepare nor an unverified SQL row is a
  receipt. A fresh receipt needs acknowledged A commit, cleanup of both
  connections and H release, then a separately guarded W read of the same bundle.
- The reader checks full H/SQL parity before looking for a principal's jobs.
  It verifies live bearer and generation, a bijection between protected records,
  links and original native rows, the sealed canonical intent, and the original
  queued state. It projects at most two accepted jobs **per account**; this is
  a closed fixture, not a history search. An exact reply reference or unique
  normalized name resolves; two plausible jobs prompt clarification. Foreign
  references and unknown references are identical, and unavailable evidence is
  not an empty inbox or a missed match. No read grants execution authority.
- Use the owned PostgreSQL fixture:
  `PYTHONPATH=. uv run pytest -q --compute-metadata-postgres tests/test_history_inbox_postgres.py`.
  The ASGI tests use two persisted, separately authenticated A sessions and a
  distinct B account, not browser or enrollment qualification. The database
  rollback, database commit-ACK loss, and post-confirmation HTTP response loss
  are separate faults. Later readback identifies an original; it cannot upgrade
  database uncertainty into a successful original invocation. All paths have
  zero scheduling, enqueue or attempts.

Scripted execution, terminal proof, completion and report delivery are later
slices. Retention is the disposable fixture lifetime. There is no restore,
retry, rollout, or `DURABLE_CHAT_ENABLED` clearance.

### Single-request inbox-to-runner qualification

`tests/history_inbox_runner_support.py` translates one synthetic inbox start
into the isolated host's ordinary text `POST /chat`. Its stable
`ibox1:<root UUID>:<request UUIDv4>` key uses canonical lowercase UUIDs and is
retained before sending. The root is a locator, not a credential. In this
disposable database only, an invoker trigger inserts the link from the native
acceptor's original task and message IDs while the acceptor still holds its
account-generation guard. A link failure rolls back the turn.
This is a fixture binding, not an inbox writer installed by Daemon.

Only the host's acknowledged, fresh acceptance can schedule the original
runner. A committed task whose database acknowledgement was lost can be read,
but that read cannot start it. If only the HTTP response was lost, the original
may already be running. Reopen uses the real bearer-authenticated task lookup;
the additional owner-scoped link check may refuse that result, never supply a
replacement. Reopen leaves work records unchanged; real authentication can
update session bookkeeping. The reader requires exactly one acceptance for the
account; a second makes inbox reads unavailable rather than choosing a task.
This fixture does not qualify multi-job UI or report delivery.

Run `PYTHONPATH=. uv run pytest -q --compute-metadata-postgres tests/test_history_inbox_runner_postgres.py`
inside the managed disposable runner. The other inbox and text-host modules are
regressions; the integrated proof requires the new module to pass without skips.

### Default-off desktop-to-phone inbox pilot

The experimental `/inbox` page pairs the actual development frontend with
the explicitly composed loopback text host. The original runner accepts
one scripted research request; there is no general intent routing or research
tool execution. The pilot's `POST /tasks/inbox-pilot` is not installed by
ordinary startup, and the normal chat write path is excluded in this
composition.

The inbox is implicit for the authenticated account and its original content
generation. The server adds an exact versioned purpose to the canonical
request under the existing acceptance writer. The protected commitment
binds that request and its stable key to the original task, conversation and
two messages. The original H/native acceptance is the binding; no inbox
table, fixture trigger, extra database privilege or additional writer is
used. Non-empty or unresolved account state refuses new inbox work under
the existing H operation. An uncertain acknowledgement never schedules a
replacement. Only a confirmed new acceptance can schedule the original.

Another authenticated device reads saved state through
`GET /tasks/inbox-pilot`, without inheriting the desktop's key. A saved
running state is not a liveness claim. The page says **Status unavailable**
if it cannot verify the result. On the next follow-up or refresh after
completion it offers one link to the original committed assistant text
through the existing authenticated conversation view. This report is not a
separate artifact, file or share link.

For disposable qualification, install the locked frontend dependencies and
Playwright Chromium. Run under the owned task wrapper:

```sh
PYTHONPATH=. uv run pytest -q --compute-metadata-postgres \
  tests/test_history_inbox_ui_postgres.py tests/history_inbox_ui_browser_postgres.py
```

The browser scenarios exercise the real setup/enrollment, app UI, saved
status and report reads. The lost-response case drops only the start
response after the real backend confirms it. The backend receipts compare
the task, attempt, result message and committed text before and after
detach, independent reopen, refresh, explicit same-key retry and failed
reads. Synthetic foreign credentials use a distinct persisted account and
the normal bearer verifier. No report or status response is mocked.

The 24 November 2026 owned run passed both browser scenarios (2 passed,
0 skipped). At 390 × 844 the composer fit without horizontal overflow
and its Send target was 44px high. Same-key retry returned 409 without
replacing the original; SQL and the synthetic transport recorded one
attempt and one dispatch per run. The other account's known task and
conversation reads matched its unknown-ID refusals. Real SQL lock
contention returned 503 and the UI removed the ready link. The opt-in
backend negatives, including rollback versus lost database acknowledgement,
are separate from the browser lost-response result.

Every inbox request resolves its bearer through the existing authenticated
refresh path, not the token captured when the view first rendered. An
expired token can be refreshed using the existing session; a refused or
superseded refresh cannot send the request under another account. Reopening
after auth coordination remains a read only. Neither refresh, a 401 from
the pilot endpoint, nor an uncertain POST response retries the start.
The review regression advances the still-open phone page's client clock
beyond the ordinary access-token lifetime and requires a real authenticated
refresh response and use of its new bearer for the completed report read.

To turn it off, stop the owned pilot fixture or omit its explicit inbox
composition; ordinary startup is already off. Non-development Next builds
render the not-found view instead of the inbox. This proves neither
physical-phone or installed PWA acceptance, live research, generalized
inbox persistence, nor deployed restore/runtime permissions. #477 and its
production release gates remain separate. This slice addresses DEC04–DEC05
and the bounded parts of AC03, AC09 and AC11 only.

### Explicit two-job inbox extension

The `inbox_pilot=True, inbox_two_jobs=True` isolated construction is a
separate closed terminal-v3 fixture. The existing one-job construction,
ordinary app bridge and default-off startup are unchanged. The v2 canonical
purpose admits exactly `Do a deep dive on X.` and `Do a deep dive on Y.`,
each at most once, in distinct original native conversations. There is no
new table, grant, production selector, runner, queue or global admission rule.
This is scripted research text, not general research or intent routing.

The second explicit start requires the first original to have a qualified
completed report AND the original same-host runner job to have finished with
its completed outcome and no open account reservation. It is refused while
the first is active or unresolved; reads never enqueue it later. A restart
can preserve readable saved state but does not reconstruct the current-host
runner outcome needed for another acceptance. No simultaneous provider
execution or automatic recovery is claimed.

The acceptor holds its original exclusive H operation throughout two W
transactions and the original A acceptance. A fixed private observation
collaborator borrowing the acceptor H and actual W store checks the complete
prior envelope under original-generation and content locks. Its W transaction
and physical holder release finish before the second W transaction starts.
The latter re-authenticates the same device/session/account and checks the
same generation without retaining W account/content SHARE locks across A.
The native A transaction alone authorizes the new acceptance. Keeping H
throughout serializes cooperating isolated acceptors/effects; it is not a
new general SQL concurrency authority. No read receipt or failed cleanup
permits dispatch. A blocked observation owner also refuses new admission in
this mode; the separate acceptor cannot treat an unusable read owner as a
reason to start another job. A later read failure does not revoke an already
admitted native A transaction or promote its readback to a dispatch receipt.

Both acceptance and UI reads check all account H prepares (including earlier
generations), global H/SQL parity, distinct task/conversation/message IDs,
the exact union of account and attached native rows, per-original canonical
commitments and text/state evidence before selection. An unsupported or
inconsistent job makes the envelope unavailable, including the other
report: terminal-v3 does not qualify failed/needs_attention content. Current-host
confirmed cancellation follows the existing restriction. No mixed history,
truncation, recency guess or fallback. Native conversation detail selects its
exact authorized original; the on-demand list has both and correct cursors.

Example (after X completes and Y is separately accepted and saved as running):

> **You:** How’s that going?
>
> **Daemon:** Do you mean research X or Y?
>
> **You:** Y
>
> **Daemon:** Y: Last saved as running.
>
> **You:** X
>
> **Daemon:** X: Your report is ready. [Open report]

Only the selected completed original has a link; it opens the existing
authenticated conversation containing the committed assistant text. The
clarification and topic selections are GETs, not accepted turns. No internal
IDs are rendered in the inbox, and no dashboard is required. The UI uses
the original fresh bearer, generation and abort fences and no-store requests.
The global auth coordination gate can unmount a protected view during token
refresh; reopening reads the saved inbox generically, without guessing or
carrying an interrupted selection past that gate. Explicit selection after
the refresh uses the new bearer. Lost acknowledgements never replay a start.
The exact-head qualification record, including any failures, belongs in the
associated PR.

Run the new and retained pilot cases inside the managed disposable runner:

```sh
PYTHONPATH=. uv run pytest -q --compute-metadata-postgres \
  tests/test_history_inbox_two_jobs_postgres.py \
  tests/history_inbox_two_jobs_browser_postgres.py \
  tests/test_history_inbox_ui_postgres.py \
  tests/history_inbox_ui_browser_postgres.py
```

### Managed clean restart: one original X and one successor

Source checkpoint: `03086f50e7020c3b8df5a5547ab12f218630471a` (merged #798).
The owner approved the following extension for #761/#477 on create-new,
disposable local persistence only. DEC04–DEC06 and AC03/AC09 still require
truthful interruption. Nothing selects this profile in production.

The existing durable evidence is sufficient to observe surviving completed X,
but insufficient to admit a subsequent Y under the approved two-job contract.

| Existing evidence | What it establishes | Missing evidence |
| --- | --- | --- |
| H acceptance and terminal-v3 projection, with the whole native envelope (`acceptance_host.py`, `_inbox_originals`; `observation.py`, `_task_owned` / `_terminal_owned`) | Original account/generation, committed request and surviving result identity/state, subject to the existing qualified reads | Original runner return and final cleanup |
| Native task, attempt, result and finished event (`tasks/store.py`, `_stage_completed_body`, `_end_attempt`) | Terminal publication, ended attempt and closed lifecycle admission | Acknowledged publication return, compute/stream/heartbeat cleanup and runner outcome |
| Original account reservation ledger (`entitlements/service.py`, `_finish`) | Durable financial disposition, amounts and original accounting period; open holds remain independently checkable | Local producer cleanup or acknowledgement of settlement by its original owner |
| Original runner job (`text_execution.py`, `_finished`; `tasks/runner.py`, `_run_chat_task`) | Current-process returned outcome after the runner's retained cleanup paths | Persistence across process loss |

`AttemptSink._complete` publishes inside `_execute`, while its `account_compute`
context is still open. Scope exit and the runner's `finally` drain stream,
settlement and heartbeat owners later. In particular, a terminal commit can
survive a lost acknowledgement or release failure. Closing the attempt lifecycle
in that commit fences cooperating admission; it does not certify whole-call
cessation (see [ATTEMPT_LIFECYCLE.md](ATTEMPT_LIFECYCLE.md)). Acquiring H in another
process only establishes current H ownership. H is not held for the entire
runner or around provider work. Neither elapsed time, an ended lease, a missing
process, empty new-host job maps, closed lifecycle nor zero open reservations
can establish the missing successful return.

The existing discriminating regressions are
`test_completed_sql_is_not_second_start_authority_until_runner_returns` and
`test_reconstructed_host_reads_first_but_cannot_admit_second` in
`tests/test_history_inbox_two_jobs_postgres.py`. The first holds the job after
actual runner completion but before its return to the scheduling owner; even
with committed output the second admission is refused. The latter reconstructs
the host in-process and observes X without Y admission. The separate
`tests/test_history_ui_process_postgres.py` exercises a genuine new interpreter
over surviving ordinary text. The checkpoint extends this diagnostic to the
unchanged two-job HTTP adapters with a separate authenticated device: completed
or interrupted original readback and explicit Y refusal, with original durable
rows unchanged. It is an ASGI process diagnostic, not a loopback browser restart
qualification.

The independent review also identified a further proof obligation: `_resolve`
can retain an acknowledged `state.result` as its returned status even when the
invocation has become blocked, and the runner's cleanup loop retrieves owner
exceptions. A returned `completed` string alone is therefore not a certificate
that every cleanup succeeded. The same-host admission predicate also checks
owner latches and the durable reservation ledger, but does not explicitly
inspect the original invocation's uncertainty. Whether a post-terminal
settlement/cleanup error with zero open reservations can reach same-host Y
admission needs a discriminating fault test, not assumed clearance or a
silent change to the existing guard. The new handoff requires positive
cleanup evidence, including that no such uncertainty remains.

**Minimum handoff:** retain evidence from the original execution owner
that the exact original scheduled job returned `completed`, its required local
cleanup finished successfully, and its execution authority was not poisoned.
Bind that evidence to the existing H identity/profile and acceptance record,
account and original content generation, task, attempt epoch/lifecycle and
financial scope, and the original terminal/report identity. Prove the original
owner's cessation for the supported clean-restart scenario. Recheck the
qualified surviving whole H/SQL envelope, exact native completion and
account-wide absence of open reservations at new admission. Terminal-v3's
legacy metadata branch does not perform all of terminal-v4's native attempt/event
checks; the exact original attempt and scope must be explicitly validated for
the new handoff, not assumed from the report projection. The handoff must
be immutable, narrowly scoped, bounded and independently qualified against
the same owned persistence. It must not contain a reusable dispatch receipt,
credential, report content or a general resume permission. Old records with
no such evidence remain readable where qualified but cannot authorize Y.

**Approved disposable pilot:** the original
execution owner publishes the protected handoff. The launcher establishes the
order of process lifetimes; it does not issue a durable completion certificate.
The implementation uses the following contract:

1. **Check before retiring.** On the original event loop, require exactly one
   already completed X, no pending submission or schedule, healthy owners, an
   acknowledged terminal publish, and successful cleanup and settlement by
   that exact invocation. Refuse an active X without cancelling it. Once
   eligible, latch ingress and scheduling closed without an intervening await.
   Drain and revalidate. A failure after the latch leaves the producer closed.
2. **Publish once.** In a separate opt-in profile derived from terminal-v3,
   `inbox-retired-v1`, append one worker-authored `INBOX_ORIGINAL_RETIRED` fact referencing the
   terminal and sole completion Send record IDs in the same H chain. The former
   binds the acceptance, generation, task, epoch and report; the latter binds
   the producer, lifecycle, scopes and reservation. Revalidate the sealed X
   request, completed native attempt/event/result and exact original settlement.
   The stager checks the closed lifecycle and compute metadata under row locks.
   Refuse extra Sends, attempts, a Stop anywhere in X's history, a restore
   interval or conflicting evidence. Under exclusive H, append the fact and
   stage its immutable projection in a guarded W transaction. The SQL
   change is limited to the fixture: private decoder, direct-W-only staging,
   bounded complete mixed-history A/W read and exact privilege qualification.
   No backfill or publication retry.
3. **Require acknowledged retirement and exit.** One launcher exclusively
   creates and owns the disposable persistence for both processes. It may start
   one successor only after receiving a private acknowledgement of the handoff
   commit, releases and host/pool closure, and successfully reaping the exact
   old process. A fresh private Unix socket binds that successor to this fixture
   and handoff. At provisioning, the Linux kernel cookie for the launcher's
   loopback listening socket, the boot ID and the private root inode bind the
   fixture's history identity. Reopening the same port or copying the H and SQL
   does not recreate that socket. The launcher holds the private root's
   exclusive lock throughout; children never inherit that lock. There is no
   attach-existing path after launcher loss. Even matching H
   and SQL cannot admit Y after a lost handoff acknowledgement, failed shutdown
   or uncertain exit. H/SQL divergence refuses the entire envelope. Loss of the
   launcher closes admission and starts shutdown. A stalled transport read
   can outlive that transition; its exit is never counted as clean retirement.

Only a subsequent explicit, distinct request may enter the existing two-phase
W precheck and A acceptance under the exclusive H operation. The new evidence
only satisfies the prior-original restriction in that precheck; all existing
whole-envelope, generation, fresh bearer, key, owner, reservation, physical
release and original NEW acceptance requirements remain. The successor checks
the protected history, current native report/attempt/event and original settled
reservation. The lifecycle and dispatch-metadata checks are attested at staging
on the exclusively owned fixture. A status check or report
open creates no task, attempt, reservation or dispatch. No scheduling on startup,
replay of X, inferred completion of interrupted X, or settlement by observation.
The UI continues short status, explicit ambiguity clarification and a single
authorized report link; unsupported evidence refuses truthfully.

Qualification uses owned PostgreSQL/Redis, actual process exit and fresh
owners/pools, loopback Next/Chromium, separately issued devices and the original
runner with a synthetic transport:

- Successful X: capture exact task, attempt, conversation, report and reservation
  identities and committed text; observe acknowledged job return and cleanup;
  cleanly retire the host. Reopen X on the second device after restart with no
  dispatch, then submit distinct Y with a fresh key. Require one new original
  and exactly one synthetic dispatch for each of X and Y, unchanged X and
  reconciled original-period charges/holds.
- Kill or interrupt before terminal commit, after commit before original
  acknowledgement/cleanup, during financial cleanup, and at handoff publication
  boundaries. Missing, conflicting or uncertain evidence refuses Y without
  replay/settlement. Include a real settlement commit followed by loss of its
  acknowledgement, including when the ledger already shows zero open holds.
  Cover Stop and cancelled X, lost database and HTTP
  acknowledgements, bearer refresh/revocation, account generation change, and
  foreign account/identity or key mismatch.
- Keep single/two-job same-host, ambiguity, report authorization and physical
  cleanup regressions. Compare protected history and durable row counts,
  per-attempt reservation and period ledger before/after each read, refusal and
  accepted request. Run unchanged project gates and independent source review.

Run `tests/test_history_inbox_restart_postgres.py` and
`tests/test_inbox_retired_projection_postgres.py` with `--compute-metadata-postgres`,
and `tests/test_inbox_retired_projection.py` for codec/chain refusal. The
fixture SQL is `tests/fixtures/inbox_retired_v1.sql`, outside the migration
chain. See the draft PR for exact-head results. This does not qualify a
production browser/backend, crash recovery, remote provider shutdown or
another successor.

### Question-driven research in the disposable inbox

The [approved search-only slice of #761](https://github.com/sol-aeternum/Daemon/issues/761#issuecomment-7230025736)
uses a separate `inbox-research-v1` composition. A person can submit a bounded
question instead of a scripted X or Y prompt. The authenticated acceptance
commits the question to its original task. Only a fresh, acknowledged
acceptance can schedule it. A second distinct question requires a separate
explicit start after the first original finishes on the same host.

The original task has three priced calls: the model asks for `web_search`,
the search service returns passages from a controlled corpus, and the model
synthesizes that evidence. Each call needs its own reservation and protected
possible-Send record within the same attempt. The native inference recovery row
identifies only the first possible dispatch. The three individual records,
their reservation metadata and settlements distinguish subsequent calls; a
possible-Send record alone does not establish that the transport returned.
The only terminal publication belongs to the original assistant message.

For example, an inbox user can ask “What feeds the fictional Dawn Lake?” and
later “Research: How does the fictional Hill Library work?” The search fixture
returns a field note for the first question and a library handbook for the
second. Both searches also return a shared bulletin. Changing its opening
time between requests changes the new report while the first report keeps
its original bytes and message ID.

“How’s that going?” reads the saved status. With two questions it asks which
one the person means; selecting a question reads its original status. Only a
selected completed report has an **Open report** link. The report retains its
source references. Neither report opening nor authentication refresh runs
search or synthesis. After process loss, surviving records can be observed
where their evidence validates; they cannot grant another start or settle a
reservation. The managed X/Y retirement protocol above does not apply.

`tests/test_history_ui_process_postgres.py -k research-interrupted` kills the
original interpreter after its first marked model dispatch. A separately
authenticated device in a new interpreter reads the surviving running state.
The original open reservation remains open, the report link is absent, and an
explicit second start is refused. This test exercises process loss; it does
not attribute the successful managed X/Y handoff to a research attempt.

Run the profile, browser and interruption checks together on owned services:

```sh
PYTHONPATH=. uv run pytest -q --compute-metadata-postgres \
  tests/test_history_inbox_research_postgres.py \
  tests/history_inbox_two_jobs_browser_postgres.py \
  tests/test_history_ui_process_postgres.py
```

This slice concerns DEC04–DEC05/DEC10 and AC03/AC09. It uses deterministic
transports and does not qualify live research quality, page fetch, source
snapshot writes, production activation or recovery of interrupted research.
[#761](https://github.com/sol-aeternum/Daemon/issues/761) and
[#477](https://github.com/sol-aeternum/Daemon/issues/477) remain open.

### Disposable profile: retrieved pages and cited evidence

The [approved disposable source profile](https://github.com/sol-aeternum/Daemon/issues/761#issuecomment-7232651920)
extends the question-driven proof with native page retrieval. Its qualification
is separate from the search-only and managed X/Y qualifications above. A search
snippet identifies a candidate page; it is not evidence that the task read it.

For each original, qualify one search, a model selection of one or two distinct
URLs from that search, sequential article retrieval, and cited synthesis on the
original result-message ID. Three model calls and one search each use their own
reservation. Direct reads have no provider charge. Each read instead needs its
own protected record before transport and a second record tied to the owned
snapshot, task pin, and exact passage. A committed pin alone does not prove the
synthesis model received the passage.

`inbox-sources-v1` retains at most the first 1,000 Unicode code points per
article in the model's tool results. A second check compares those results to
the final synthesis input. Each paragraph of the model's answer refers to
one or both selected pages; each evidence entry quotes text at checked
character offsets in the identified snapshot and links to its source URL.
The fixture verifies passage identity, quotation and range mechanically.
It tests the meaning of the answer separately against known facts, including
conflicting notices and an unanswered question. This does not measure a live
model's judgment or citation quality.

The SQL profile installs only on a fresh disposable 065 fixture. Its page and
projection functions expose protected frames without granting raw table
access. At pin time the PostgreSQL 16 fixture checks that the snapshot is a
top-level insert by this transaction, in the first transaction-ID epoch,
with change tracking enabled and no updates to the snapshot heap. The
native snapshot, task event and SQL projection commit together. The history
file is separate: loss of parity or acknowledgement cannot authorize another
read, a new task or synthesis.

In the browser control, two independently enrolled clients share the originals.
The search descriptions stay fixed; only a page body changes. The second
report must reflect that change, while the phone reads the first report with
its original ID, bytes and citations. Check authority and accounting around
clarification, lost submission acknowledgement, refresh and revoked/foreign
sessions. Readback must neither dispatch nor renew the snapshot. If the full
source expires, only the bounded evidence already committed with the report
can support readback; do not claim a fresh source check.

The process-loss qualification kills the original interpreter after the
possible-read mark, after a returned page but before its pin, and after native
pin COMMIT before the caller receives the acknowledgement. A new interpreter
may show a verified saved status or refuse the read. It cannot start another
original, retry the source, complete synthesis or settle an outstanding hold.

Run the disposable source checks with the cached owned PostgreSQL image,
owned Redis and loopback browser prerequisites:

```sh
PYTHONPATH=. uv run pytest -q --compute-metadata-postgres \
  tests/test_source_history_model.py tests/test_source_projection_postgres.py \
  tests/test_source_snapshot_insert_witness_postgres.py \
  tests/test_history_inbox_sources_postgres.py \
  tests/test_source_publication_faults_postgres.py tests/test_source_cited_synthesis_postgres.py \
  tests/test_history_ui_process_postgres.py tests/history_inbox_two_jobs_browser_postgres.py
```

**Original live acceptance plan (not run; corpus and question superseded below).** One public question:
as of the evaluation date, which US passport product can a first-time adult
applicant use for international air travel versus land crossings, and what
application and acceptance fees apply to each? Use the State Department eligibility
guidance and official fee schedule. Before any request, record the exact pinned
model, provider and endpoint, dated route-specific ZDR evidence, current prices,
source-service approvals and no-fallback policy. Search-service terms are
separate from model retention; standard Brave is not ZDR. Prefer an owned index
and explicitly approved official sources. Obtain owner approval for the named
route and experiment. Preprice the worst case (three model calls, one search,
up to two reads) at **US $0.25 aggregate or less**. Include all charged source
services and outstanding holds; charge unknown post-send usage to its ceiling.
One attempt, no automatic retry. Pass only if each travel-use and itemized fee
claim maps to a dated retrieved passage, conflict or missing evidence is
disclosed, citations are real, and another device opens the exact saved report
with no new dispatch or charge. Record latency and reconcile the original ledger.

### Owned-index pre-registration (28 November 2026; no live acceptance yet)

One original question, asked by an adult in the UK using standard service:
**“For a standard 34-page adult UK passport, what are the online and paper fees, how long does it usually take, and when does that clock start? Do the two official pages agree about the extra paper cost?”**
Use the two-entry local index only. Titles and neutral descriptions are discovery aids.
Neither the expected answer below nor the scoring rubric belongs in the model prompt.

**Execution boundary.** The disposable host constructs the existing
`HistorySourcesExecution`. Its original `SourceRun` binds the local lookup to the
owned projection and its specific tool and approval. The worker uses the same
binding call as for ordinary source research. Sharing that execution path does
not make the two reports interchangeable: a reader must match the committed purpose.
The production isolation inventory and its negative tests apply unchanged.

| Official page | Captured UTC (no credentials; HTTP 200, no redirect) | Raw HTML SHA-256 | First 1,000 extracted code points, UTF-8 SHA-256 |
|---|---|---|---|
| [GOV.UK — Passport fees](https://www.gov.uk/passport-fees) | 2026-11-28 08:50:34 | `70f8289e019cc19f8ec81458b64198194526e2b4d5c41c78767a257d9878bb96` | `97996fd316f3c246a2cb14523e746cbabf7579b929a573513ee1a13fcba4c2c5` |
| [HM Passport Office — About our services](https://www.gov.uk/government/organisations/hm-passport-office/about/about-our-services) | 2026-11-28 08:50:35 | `6e382339dd4d0820c0421202f49c31b12fe6b5a063e46e5007c295a387617d17` | `eddc1ce8f75a1ca20f326a5ef87218e3759ac0ae42727288554988a3f58a0ef5` |

The two compressed captures in `tests/fixtures/uk_passport/` omit request-specific
anti-forgery and CSP metadata/attributes. The native HTML-to-Markdown extractor produced
identical complete articles before and after removal. The constructor verifies the
decompressed, sanitized HTML SHA-256: fees `fe49432932de1e8046d785a8e3b2322a1e2503a1e8db03c25967dc64c33ded84`,
services `d05bc92c316294b69f8ff36c3bbe2881f48074aca51467f05cd418b45c2d31c6`.
The URL is the official origin;
the time of an offline snapshot is not a new visit to GOV.UK. Contains public sector
information licensed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

**Answer key and decision rule.** Offsets are half-open Unicode code-point ranges
in the native article, not offsets into HTML:

- Fees `[174,214)` gives the online/paper column order; `[229,295)` gives the adult
  standard 34-page row: **£102 online, £115.50 on paper**, a difference of **£13.50**.
- Services `[138,186)` says **usually within three weeks**, not a guarantee.
  `[604,667)` starts processing **when HM Passport Office receives the documents**.
  `[188,313)` explains that an interview or a request for more information can extend it.
- Services `[772,902)` says paper costs **an extra £16.00**. Report the discrepancy
  against the fee table. Do not invent a reason, revised fee or guaranteed travel date.
- All four answers must follow from the actual selected, dated snapshot passages.
  Verify quoted substrings, URLs, offsets and meaning independently. Any omitted central
  fact, hidden conflict, invented citation or unsupported resolution fails quality,
  even if the model's format validates.
- Require confirmed `stop`, timely closure and one reconciled original-period row for
  each of three model sends plus the zero-cost lookup. Unknown usage after send uses
  the ceiling and cannot earn a measured-accounting pass. Compare IDs and saved UTF-8
  report bytes across independently enrolled clients, with no change to sends, pins,
  retention, holds or spend. No retry, successor or dispatch by a reader.

**Route decision ([#803](https://github.com/sol-aeternum/Daemon/issues/803), 28 November 2026): no live dispatch.**
The pinned candidate is OpenRouter `POST https://openrouter.ai/api/v1/chat/completions`,
model `deepseek/deepseek-v4.1-flash-1017`, only/order `coreweave/fp8`,
`allow_fallbacks=false`, `require_parameters=true`, `data_collection=deny`,
`zdr=true`, no plugins. The [exact endpoint](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash-1017/endpoints)
and [ZDR catalog](https://openrouter.ai/api/v1/endpoints/zdr) agree; this row
reports `safety_retention=false`. Two distinct content-retention exceptions remain:

- [Gateway terms](https://openrouter.ai/docs/guides/features/zdr) allow flagged
  content retention for CSAM review, reporting and legal preservation. The key's
  effective account/guardrail choice for “All other models” also matters.
- [CoreWeave's serverless terms](https://docs.coreweave.com/products/inference/serverless/privacy-and-security)
  allow content fragments in diagnostic logs for **up to 14 days** on exceptional
  errors. Agent Lens may store content if enabled. Operational billing/latency
  metadata is distinct from content. No applicable upstream contractual override
  is established. Neither a public `attested` result nor the operator's historical
  account-logging attestation proves that the effective account excludes these cases.
  CoreWeave's terms apply to first-party-served models. The matching checkpoint and
  price support, but do not prove, the upstream contract for OpenRouter traffic.
  The [W&B MSA](https://wandb.ai/site/wb-terms) grants broader service-improvement
  rights; its free/academic terms also allow testing and development. No evidence
  establishes which negotiated provisions govern this route.

In the [offline monitor check](https://github.com/sol-aeternum/Daemon/issues/803#issuecomment-7236941765),
changing **only** `safety_retention` to `true` still returned `attested`.
Before seeking live approval, require dated, non-secret evidence of the key's effective
ZDR-only restrictions, disabled prompt logging/tracing, applicable content-retention terms
at both hops, and all account charges. No account settings were inspected or changed.

**Priced alternative, not an authorized substitute:** direct
`POST https://api.inceptron.io/v1/chat/completions`, **`deepseek-ai/DeepSeek-V4.1-Flash-1017`**.
The [provider's own model catalog](https://api.inceptron.io/v1/models) lists FP4,
tools and structured outputs, $0.10/M prompt, $0.60/M completion, $0 per request.
[Terms §6.2](https://www.inceptron.io/termsofservice) and the
[privacy policy](https://www.inceptron.io/privacy) specify transient payload
processing, no training and default zero retention, unless logging is enabled or
otherwise agreed. Metadata telemetry is separate. Direct routing avoids the
OpenRouter gateway exception. No current Daemon direct-account attestation, credential
binding, billing agreement or approved native route exists in this qualification.
Do not infer that a member of the OpenRouter catalog qualifies the direct endpoint.

| Maximum for one original | Per model send | Three sends |
|---|---:|---:|
| Candidate: 32,000 bounded input × $0.30/M + 4,000 output (including reasoning) × $1.20/M | $0.014400 | $0.043200 |
| Direct alternative, same limits: $0.10/M input + $0.60/M output | $0.005600 | $0.016800 |

Use integer micro-USD, full uncached rates, one zero-cost owned lookup and at most
two zero-cost owned reads. No top-up, paid source/egress service, new hosting, external
search, retry, provider substitution or priced tool. These are whole-run maxima **only
if the account incurs no additional charge**; unknown fees/tax or inadequate credit
refuse admission. On the proposed route 206,800 µUSD already spent/held plus 43,200
equals the 250,000 µUSD ceiling; one more µUSD refuses. Quote all three maxima before
the original starts, then include outstanding, settled and remaining legs before
each send; unknown post-send usage costs the full bound. Today's authorized live spend
is zero. A separate owner decision and current account evidence are required.

**Offline acceptance.** Against disposable PostgreSQL and loopback browsers, the
synthetic model completed one original with three model reservations and one settled
zero-cost lookup. Two sequential native reads pinned the registered passages. A
separately enrolled browser opened the same conversation, message and UTF-8 report
without changing sends, holds, spend or snapshot expiry. Tests also cover a successor
request, a changed lookup reservation after snapshot expiry, Stop before the first
page transport, incomplete synthesis, a whole-attempt budget shortfall of one µUSD,
and a missing usage tail charged to the send's full hold before any lookup.
Run these checks with:

```sh
PYTHONPATH=. uv run pytest -q --compute-metadata-postgres tests/test_owned_index_postgres.py \
  tests/history_inbox_two_jobs_browser_postgres.py::test_owned_index_original_reopens_on_independently_enrolled_browser
```

These checks establish the offline execution path and compare a scripted answer
with the rubric. Live answer quality, provider latency and account billing are unmeasured.

## How partial text reaches the current page

The isolated POST stream remains acceptance metadata only. It does not emit answer
tokens or a fabricated final result. The existing Next proxy treats a durable
stream ending without terminal-task evidence as an interruption. The page can
then reconcile the original key, read its saved conversation, adopt the persisted
streaming placeholder and follow saved text through the existing history poller.

This is a **polling/recovery path**, not live POST token rendering. Source tracing
alone does not prove that the page's busy, scope and selection guards permit the
adoption. Qualification must demonstrate actual saved partial text in the first
client's DOM before closing that page. A JSON response assertion is insufficient.

Disconnecting a view is not Stop. A second independently authenticated device must
reopen the same original conversation without creating another task or attempt.
Explicit Stop must use the existing server cancellation authority and confirm its
outcome separately from detaching the transport. A local fake iterator closing
proves neither remote-provider cessation nor that billing stopped.

## Synthetic fixture and acceptance requirements

Use disposable PostgreSQL, the real self-hosted setup/enrollment/refresh handlers,
independently issued device sessions, normal cookie/CSRF/origin controls, and an
owned loopback backend plus fresh Next process. Any Redis used by auth is disposable
and owned, not a shared production service. Synthetic fixture provisioning is not
production role or privilege qualification. Do not mock protected auth, task,
conversation, route-qualification or budget responses. This does not qualify
hosted email/Google onboarding.

Acceptance requirements (the verification record below distinguishes executed
evidence from outstanding checks):

- Actual page-generated Send key, original SQL identity, rendered saved partial,
  normal covered-range refresh and disconnect without implicit cancellation.
- A separate issued device reopening the original output, with completion and
  explicit Stop in separate deterministic scenarios.
- Stop before dispatch, during execution and both completion-race orderings;
  one original task/attempt/provider call, or zero dispatch on budget refusal.
- Revocation and expiry after waits; duplicate/conflicting sends, concurrency and
  uncertain acknowledgements without replacement or replay.
- A real setup/enrollment companion to the lost-acknowledgement regression:
  protected `POST /chat` under the original commit-then-lost-ACK fault commits
  exactly one original account/key/input task with zero original runner jobs,
  attempts, reservations or provider calls; an independently enrolled device
  reads that same task by key, reads alone create no effects, and a deliberate
  same-key POST adds no task or schedule.
- Recovery/unavailability responses retaining display and original blocked keys,
  without automatic resend, settlement or alternate-backend fallback.
- Authentication refusals remain final even with empty, malformed or timed-out
  error bodies; a second history origin cannot mask a received 401. Auth or
  selection cancellation still prevents stale publication.
- Wrong identity/generation/commitment, extra attached rows, unsupported metadata,
  scoped cursor negatives and uncertain commit/reset/history-release cleanup.
- Actual fresh-process recovery distinguished from in-process reconstruction;
  surviving SQL is not lost-output reconstruction or automatic resumption.
- Unchanged production startup/refusal and source-inventory negative checks,
  required project gates and final independent review.

No phone, deployed-client, live-provider, general history/restore, cards, CSV or
lost-output reconstruction readiness follows from this source candidate. Separate
owner approval and the remaining release gates still govern any activation.

## Verification record

The dedicated real-browser PostgreSQL fixture passed both completion and Stop
scenarios on 13 November 2026. It runs the actual application page and normal Next
proxy with real self-hosted setup/enrollment, two independently issued devices and
the original runner. It checks the browser-generated JSON key against the original
SQL task key, committed and rendered partial text before detachment, normal
covered-range refresh, second-device readback, native terminal outcome, one task,
attempt and provider call, and local fake-iterator closure. Stop displays the
existing provider-activity/charges uncertainty notice. No task/history/auth
responses are fabricated.

This is not qualification of the whole application bootstrap: home-suggestion and
task-input presentation endpoints are outside this isolated fixture and return 404.
The fixture serves the public model catalog (`GET /v1/catalog`, no credentials) and
the native bearer-authenticated `GET /users/me/settings` and
`GET /users/me/entitlements` reads through their existing handlers and
dependencies. Settings uses the composed memory store and cipher on the borrowed W
pool; the fixture adds no resource owner, grant or settings-mutation route. The
page scenario must observe those real success responses before Send and after the
second independently issued device (settings through the profile page, entitlements
through the existing Next proxy), without fulfilling or fabricating any protected
response. Initial anonymous refresh returns 400; actual setup/enrollment then
issues the working sessions. The scenario uses the normal Auto/Balanced/Auto
defaults, not model selection or broader controls.

On 17 November 2026, the refreshed source passed both actual-browser scenarios
with these three bootstrap reads, plus 90 integration tests covering the text
runner, authenticated reads, bridge/process recovery and source inventories.
The real-enrollment protected lost-acceptance-ACK companion passed: one original
task committed, no runner job/attempt/reservation/provider call followed, the
second device observed it by key, and the same-key negative probe scheduled
nothing. This native HTTP/SQL fault proof and the separate browser completion/Stop
and Next transport regressions are distinct layers, not one browser-to-SQL
lost-response experiment. No historical performance/HMR diagnostic was rerun.

Fresh-interpreter tests separately kill an executing producer or let it complete,
then open the new qualified read bridge over surviving SQL with zero new provider
calls. Those are direct bridge reads in a new process, not a restarted deployed
browser/server or lost-output reconstruction. They preserve the original task,
attempt and outstanding reservation; they do not resume or settle uncertain work.

Dedicated metadata, cursor, account-isolation and retention regressions accompany
the source. A fresh enforced read-only final source review completed with
disclosed OpenAI-family overlap; its unreadable-refusal/key-retention finding was
repaired and re-reviewed. The consolidated PostgreSQL/core/auth/process/inventory
selection passed 306 tests; focused final refusal/retention tests passed 46 tests.
Required project gates and current-head hosted checks are recorded separately in
the associated PR; the browser result alone does not clear those gates.

### PR #709 review follow-up

The history transport preserves received 401 responses even when their bodies
are empty, malformed or fail after the read deadline. It never masks those
authentication refusals by trying another history origin. Focused regressions
cover valid bodies, cooperative timeout rejection, late body resolution and
selection/auth-generation cancellation; stale leases still reject publication.

A matched default-browser comparison used fresh registered checkouts of base
`6b8beb6bffe69f6f0040b2566acc301fc362e8b9` and the repaired candidate, identical
locked dependencies, browser binary, configuration, fixtures, CI settings and
four workers, with sequential server ownership. JSON reporting did not change
tests, assertions or retries:

| Scope | Base | Repaired candidate |
|---|---|---|
| Full default inventory, 56 tests | 31 passed / 24 failed / 1 flaky | 29 passed / 24 failed / 3 flaky |
| Bounded navigation/scroll diagnostic, 16 instances | 12 passed / 3 failed / 1 flaky | 15 passed / 0 failed / 1 flaky |

All 22 design-token CSP failures have matching error signatures on both
revisions after removing only checkout paths, ANSI formatting and per-response
nonces. The FPS threshold fails on both. In the bounded diagnostic, the settings
shortcut fails both attempts on the base and is flaky on the candidate at the
same profile-navigation assertion and observed URL. These are not newly cleared
tests. The diagnostic repeats the selected cases twice and is
not a substitute for the full inventory.

Other navigation/scroll outcomes differ across runs. Both long-chat viewport
cases pass on both revisions in the diagnostic, but mobile scrolling fails and
desktop scrolling/navigation is flaky in the repaired full run. The mobile
trace records an unrequested document navigation with Fast Refresh activity;
that association does not establish the complete cause. Those differences
remain unqualified under #490, not proven unrelated to this PR. No CSP,
performance, timeout or assertion gate was weakened, and no merge clearance
follows from this assessment. The associated PR retains detailed per-test
evidence and the owner decision gate.
