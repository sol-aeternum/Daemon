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
