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

This is not qualification of the whole application bootstrap: catalog,
entitlement-display, home-suggestion and task-input presentation endpoints are
outside this isolated fixture and return 404. Initial anonymous refresh returns
400; actual setup/enrollment then issues the working sessions. The scenario uses
the normal Auto/Balanced/Auto defaults, not model selection or broader controls.

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
