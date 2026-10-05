# Contextual home — approved first-release contract

Owner approval: 5 October 2026, in the implementation approval question following
the centred-tooltip study. Product/API contract approved; production code and
release verification have not begun. No deployment is authorised.

## Approved behaviour

- Composer-first home with zero to three contextual task summaries.
- Each suggestion owns a detailed prompt ready before display. Hover or keyboard
  focus shows the exact prompt, centred over the full row and clamped to screen
  edges. No per-item Why rationale, Start chat badge or Preview button.
- Clicking/tapping immediately submits that prompt and its source context in a
  new chat, through normal admission and execution. Showing/previewing does not
  submit. Ordinary recents remain navigation.
- Unrelated unsent text/files must not be sent, mixed into the request or lost.
- Normal model/effort controls, Council compatibility and approval boundaries
  remain supported; no retired capability is re-enabled.

## Approved implementation boundaries

- Use at most six recent, owner-checked **cloud** conversations. Exclude local,
  unknown, inaccessible or deleted sources; no memory or connector input.
- Read bounded excerpts, generate at most three ready prompts using existing
  qualified background inference and account budgets. Do not add a model route,
  workload profile or plan allowance.
- Generate only on source changes or explicit refresh, never per keystroke or
  merely because the home screen was visited. Cap generation at four per hour.
- Use an encrypted, account-scoped Redis cache with one-hour expiry. Hide expired
  or changed candidates rather than serving stale private context.
- Add authenticated suggestion-list/refresh endpoints and an optional suggestion
  ID on `/chat`. The server resolves context and revalidates source ownership,
  locality and content before binding it. A client-provided link is not binding.
- Default off until enabled once. Disabling stops future generation, not only
  rendering. In-flight and queued work must recheck before publishing/dispatch.
- No new dependencies, database migration, integration, provider qualification,
  external live evaluation or deployment is authorised.
- Create the PR only after independent review and the existing project gates.
  Do not weaken gates, overwrite unrelated work or merge/deploy the PR.

## Bounded supervisory review

Trigger: new sensitive cross-conversation context handling and chat/API protocol
seams. Sol has stopped affected implementation pending an Astra-led safety review.
This is a design preflight, not a transfer of ordinary implementation ownership.

Resolve the concrete safe implementation boundary for source snapshots/revisions,
encrypted persistence, cache/rate/opt-out races and new-chat dispatch. Return to
Sol-high as soon as the issue is resolved; retain human approval requirements if
the approved contract proves insufficient. No runtime diff exists to approve yet.

### Astra preflight — implementation constraints (5 October 2026)

The approved scope is implementable without a new database schema or processor.
The following constraints close the preflight issue; they are acceptance criteria
for implementation, not a claim that security or concurrency has been tested.

1. **Server-owned candidates.** An opaque suggestion ID resolves only inside the
   authenticated account's encrypted cache. The model supplies bounded summary
   and prompt text, not authority, source IDs, endpoints or executable commands.
   Validate output shape/length/count and map source references through the
   server's supplied source allowlist. No tools during generation. Never promote
   generated `/council`, `/local` or other control syntax into a routing command.
   Preserve the user's ordinary explicit model choice and existing budget policy.
2. **Exact bounded snapshot.** Retrieve only owner-matching, explicitly cloud
   conversations and owner-matching complete user/assistant messages, in stable
   order. Bound source count, message count, per-source text and aggregate bytes
   before decrypting/dispatching as far as practical. Fingerprint the canonical
   selected source set including content, IDs, status, locality and displayed
   title; do not use timestamps alone. Check eligibility before provider dispatch,
   cache publication, listing and click. Deleted, changed, unknown or unreadable
   sources yield no candidate; never substitute fresh text under an old preview.
3. **Generation admission.** Use an account-wide atomic rolling-hour admission
   bound (four attempts, including failed/explicit refresh attempts), plus a
   bounded single-flight lease. Do not reset the quota on refresh or opt-out.
   Failed Redis/admission/encryption means unavailable, not uncached inference.
   Leases need token-checked release and publication fencing so a timed-out
   generator cannot publish over a newer result. Use existing guarded background
   compute for each admitted call; no ad hoc provider requests or retries.
4. **Lifecycle and opt-out.** Cache reads do not renew the one-hour expiry.
   Remember processed source-set identity separately from cached payload expiry
   so expiry alone does not cause per-visit regeneration. Empty/failed generation
   must not cause a retry loop; another attempt requires a source change or
   explicit refresh and still consumes admission. Default the preference false
   and require a strict boolean. Coordinate preference updates, dispatch admission
   and publication using a shared account-scoped protocol with a revocation epoch
   or equivalent fence. The current settings read/merge/write path is not adequate
   protection against stale concurrent writes resurrecting opt-in. An opt-out
   acknowledged before a new generation's admission must prevent that admission;
   an already admitted external call cannot be recalled, but its result must not
   be published or served after opt-out. Hide immediately in the client and
   invalidate cached candidates server-side. No model call while holding a DB
   transaction or a row lock for its entire duration.
5. **New-chat boundary.** Resolve the candidate before routing/preparing messages;
   do not let client `messages`, attachments, metadata or a destination ID replace
   its prompt/source or append it to an existing conversation. A suggestion click
   must use a new destination and an isolated SDK send, guarded by auth generation
   and duplicate-activation state. The transport's `body.id = currentId` assignment
   must not silently override that destination. Consume/claim a candidate
   atomically for submission and give replay a truthful existing/in-progress or
   refusal result, not a second chat. Do not automatically retry uncertain sends.
6. **Admission snapshot and persistence.** At the server acceptance boundary,
   revalidate the exact sources and opt-in, then bind an immutable copy to the new
   request. Serialise that short step with source changes/deletion using existing
   DB transactions/appropriate row locks; do not hold locks during model work.
   Later source deletion does not retroactively erase an already accepted copied
   user turn. Persist prompt and bound context under existing content encryption
   or an explicitly encrypted envelope, never plaintext generic metadata. Keep
   the prompt distinguishable from inspectable source data on history reload.
   Bound context must survive follow-up turns/reloads after the Redis cache expires.
   Treat excerpts as untrusted data, never hidden system instructions. Suggestion
   requests must fail closed on persistence failure instead of using `/chat`'s
   ordinary "continue without persistence" fallback. Preserve normal compute
   admission and truthful error states, not a guarantee that every click succeeds.
7. **Browser lifetime.** No suggestion payload/excerpts in localStorage,
   sessionStorage, IndexedDB or service-worker caches. No-store responses and
   auth-generation checks cover list, enable, refresh, click and late results.
   Existing user-authored draft persistence remains separate; preserve those
   drafts without copying suggestion data into their storage path.

Required negative evidence: wrong account/message owner, local/unknown scope,
source edit/delete between list and click, opt-out during generation/publication,
concurrent enable/settings write, five concurrent refreshes and rolling-window
boundary, expired lease/late publisher, cache expiry without source changes,
malformed model output/command prefix, corrupt ciphertext/Redis failure,
duplicate click/request, stale auth response, unrelated pending files/drafts,
history reload/follow-up after cache expiry, and persistence/admission failure.
Use fictional inputs and independent final diff review; passing mockup checks
do not discharge these requirements.

### Preflight decision and return packet

**Resolved: safe to begin bounded implementation under the constraints above.**
This closes the design-preflight escalation only, not final security approval.
No runtime implementation was performed during Astra supervision.

Fresh permission-enforced `review-go` review
(`ses_ef4c09220ffeeYVSWO5rGKoe3M`) found no blocking design issue. Astra inspected
the relevant code and adjudicated its notes:

- **Rejected:** the review incorrectly claimed conversations have no locality
  field and suggested treating all server-stored owner data as cloud eligible.
  `migrations/003_create_conversations.sql:5` explicitly defines `pipeline` as
  cloud/local; `MemoryStore.get_conversation` selects `c.*` at `store.py:239`.
  Require `pipeline == 'cloud'`; never infer locality from storage or ownership.
- **Accepted clarification:** the four-per-hour generation bound is separate
  from ordinary `/chat` rate limits. Use atomic Redis admission and fencing;
  tests must establish rolling-window/concurrent-request behavior. No model call
  may bypass existing guarded background accounting.
- **Implementation obligation:** choose and document concrete lease token,
  revocation fence and encrypted context envelope details within this contract
  before writing the affected execution path. If implementation requires scope,
  schema or API expansion beyond the owner's approval, stop and ask; do not
  quietly weaken these constraints or claim this preflight approves a future diff.

Final-state check: only this design document changed during Astra supervision;
runtime files remain untouched. Primary inspected the settings update, chat
routing/persistence, message encryption/mutation and conversation locality paths.
`git diff --check` returned clean; executable gates are intentionally pending
because no production implementation exists. Quota at preflight: GPT 32% remaining;
Go rolling/weekly 0% used, monthly 42% used, authoritative pace unknown (NORMAL,
not surplus). No new project or tooling anomaly requires filing.

**Next steps for Sol-high:** implement backend bounded candidate/cache/preference
and source-bound chat path with negative tests, then integrate the approved UI
and isolated new-chat/draft flow. Use the copied study as visual evidence, not
runtime code or real model-quality evidence. Independently inspect all worker
changes, run final project gates and obtain fresh cross-model review under the
authorship rules. Commit/push only scoped verified changes, then create the PR
through the gated wrapper; do not merge or deploy. Return readiness is immediate:
there is no remaining unresolved supervisory blocker in the approved scope.

## Implementation checkpoint — bounded UI supervisory escalation

5 October 2026: both implementation workers have returned provisional changes;
neither has final-state verification. The earlier "runtime untouched" statement
describes the preflight only, not the current working tree. No commit, push, PR
or deployment exists. Original dirty `/home/sol/daemon` work remains untouched.

**Specific escalation:** the GLM frontend return violates authentication,
cross-account payload lifetime and usable opt-out boundaries. Sol stopped the
affected repair after source inspection; this is not permission for Astra to take
over the entire implementation. Resolve and verify these UI blockers and return
to Sol-high at the first safe idle boundary. Backend final verification and the
PR remain Sol's ordinary pending work unless they reveal a new escalation.

### Actual changed artifacts and independent findings

- GLM UI owner `ses_ef4b97f07ffeEsO08103tuw905`: new
  `frontend/lib/homeSuggestions.ts`, `frontend/hooks/useHomeSuggestions.ts`,
  `frontend/components/home-suggestions/{SuggestionRow,HomeSuggestionsPanel}.tsx`,
  rewritten `frontend/components/WelcomeScreen.tsx`, new
  `frontend/__tests__/home-suggestions*` files and relevant changes to
  `chat-discoverability.test.tsx` / `midnight-presentation.test.tsx`.
- Worker reports 34 passing / 8 failing UI checks, no completed lint/format/type
  gates. These are claims, not primary-verified test evidence. Temporary PROBE
  tests and console logging remain in `home-suggestions-ui.test.tsx:49-69`.
- Fresh read-only `explore-luna` review
  `ses_ef49d3abaffewh2Dj1csKyr4X3` independently identified five blockers:
  stale account payload retained across auth generation, double Bearer headers,
  absent persistent opt-out control, display/activation past expiry, and wrong
  API origin. Sol inspected and accepts these source-backed findings:
  `useHomeSuggestions.ts:149-152,165,306,434,494,554-583`,
  `auth.ts:482-487`, `homeSuggestions.ts:205-209`,
  `HomeSuggestionsPanel.tsx:122-132`, and the lack of an expiry timer.
- Further checked obligations: `dismiss` exists at hook `:526` but is omitted
  from its returned object; server error/unavailable status is converted to empty
  at `:212-221`; ready rows have no global refresh control; tooltip anchors to
  the main button instead of full row; per-row open state permits overlapping
  previews and hovering the portal does not retain it. The pause override resets
  only when input changes empty/non-empty, not on the next text edit. The pause
  test mistakenly expects an empty composer to be paused, and composer-order
  test uses `data-test-id` rather than `data-testid`. Preserve the approved
  interaction while fixing these, not merely assertions to get a green run.
- The reviewer also hypothesised an auth-change gap between the successful
  enable response check and the synchronous call to `refresh()`. Sol has not
  accepted that specific finding: there is no intervening await in the normal
  Response path, and refresh rechecks after awaiting its header. Astra should
  adjudicate based on actual execution, not manufacture a race via exotic getters.

### Primary changes and checks already observed

Sol owns `frontend/app/page.tsx`, `frontend/app/api/chat/route.ts`,
`frontend/lib/suggestionSubmission.ts`, `frontend/lib/pwaCaching.ts`,
`frontend/components/SuggestionSourceContext.tsx`, the four
`frontend/__tests__/suggestion-*` test files, and
`frontend/e2e/contextual-home.spec.ts` / its production config entry. Page
integration now passes `composer`, `onSuggestionSelect`, and pending state, but
still passes removed `setInput`; remove that obsolete prop during bounded repair.

Primary directly observed: 21 transport/source/bridge tests plus 3 page integration
tests passed; owned-path ESLint passed; feature matrix validator passed (81 rows).
These do not verify GLM UI or final backend. Page checks use mocked SDK/component
transport and assert isolated exact prompt, no draft/file reset/transfer, duplicate
activation guard and auth revocation. Browser fixtures are not yet exercised.

### Backend worker return (pending ordinary verification)

Sol backend owner `ses_ef4ba2b5bffe1xMpnPU3dM68cJ` changed
`orchestrator/home_suggestions/{__init__,contracts,cache,service}.py`,
`orchestrator/routes/home_suggestions.py`, `orchestrator/routes/users.py`,
`orchestrator/models.py`, `orchestrator/main.py`, `orchestrator/memory/store.py`,
`orchestrator/worker/{jobs,worker}.py`, and three
`tests/test_home_suggestions*.py` files. Worker claims 193 focused/regression
passes, but the last lease-deadline change and two typing fixes are untested.
No real Redis/PostgreSQL or provider calls were performed. A fresh independent
backend review and primary final-state checks remain mandatory. Ordinary-chat
source changes are hooked; Council source-change coverage remains uncertain.

Concrete limits proposed by the backend: six conversations, four complete
messages each; full plaintext message 8,192 bytes, excerpt 2,048 bytes, source
8,192 bytes, aggregate 32,768 bytes, title 1,024 bytes; cache one hour; lease
180 seconds, generation at most 150 seconds clipped to remaining lease minus
five seconds; three candidates, summary 160 characters, prompt 4,000 characters.
These fit the approved bounded scope, but implementation must be checked.

### Environment / bounded return criteria

- `.venv` exists (588 MiB) but basedpyright expects `.uv-venv`; fix local mapping
  with a safe ignored symlink/runner environment, not config relaxation or another
  duplicate install. Runtime type-check claim exited 3 and is not a passing gate.
- Locked frontend install succeeded with no vulnerabilities. `.next` is a new
  ignored symlink to `/home/sol/.cache/opencode-contextual-home-20261005/next` to
  avoid temporary-disk exhaustion. Do not overwrite existing data or undo this.
- `.triage.local.md` holds two tooling warnings (blocked install scripts and
  Node's experimental global localStorage getter). Backend reported an additional
  cross-filesystem hardlink warning; primary has not yet recorded/verified it.

Return when the concrete UI failures and lifetime/auth/opt-out defects are repaired
and independently checked, with final targeted tests, type/lint/format evidence
and finding adjudication. Use mocked fictional inputs; no new deps/schema/API
scope. If another material blocker cannot be resolved in the one scoped repair,
report blocked rather than another review loop. Leave changed paths and exact
remaining checks for Sol (backend review, integration browser run, full gates,
scoped commit/push, gated PR creation; no merge/deploy).

### Astra UI repair return — supervisory issue resolved

The five confirmed frontend blockers are repaired: full auth headers are passed
unchanged; URLs use the existing configured backend/development fallback; state
is masked synchronously and cleared on auth generation change; persistent opt-out
is exposed separately from session-local hiding; expiry removes rows without
network generation and guards activation. Enable/disable mutations are serialised
locally and late disable responses are request-scoped. Server error states stay
distinct from empty, `dismiss` is returned, and global refresh is available.

Related bounded UI fixes: tooltip uses the complete row, only one preview is
open, hovering its portal retains it, and Escape still dismisses it. The temporary
typing override is tied to the input revision. Dismissal undo is reachable, hidden
copy no longer claims generation stopped, and the composer hint reflects its new
position. Removed debug probes; repaired erroneous empty-input/expiry/default-copy
and test-ID assertions rather than changing the product contract. The obsolete
page `setInput` prop and invalid SDK text-message `id` prop were removed; the
local duplicate-submission receipt remains independent from SDK message IDs.

Changed during this bounded repair: frontend `hooks/useHomeSuggestions.ts`,
`lib/homeSuggestions.ts`, `components/WelcomeScreen.tsx`, both
`components/home-suggestions/*.tsx`, `app/page.tsx` (two prop corrections only),
`__tests__/home-suggestions-{hook,ui}.test.tsx`, new
`__tests__/home-suggestions-origin.test.ts`; scoped formatting also touched the
worker's home-suggestions parser tests and the two adjusted existing UI tests.
No backend/config/provider/dependency changes during this repair.

Final-state primary evidence:

- Seven targeted files / **53 tests passed**, including page integration:
  home-suggestions parser/hook/UI/origin, chat-discoverability,
  midnight-presentation and suggestion-page-integration. Log:
  `/home/sol/.cache/opencode-contextual-home-20261005/ui-repair-tests.log`.
- `npm run type-check` passed after the final tooltip changes.
- Scoped `eslint ... --max-warnings 0` passed; scoped `prettier --check` passed;
  `git diff --check` passed. No gate was weakened.
- Fresh permission-enforced `explore-luna` review
  `ses_ef49451b1ffemMkVhek1dFFXIb` inspected repaired execution paths and tests,
  finding **no material blocker to resuming ordinary Sol integration**. It did
  not execute tests or certify backend behavior. The previous speculative gap
  immediately before synchronous `refresh()` is rejected: no await intervenes
  after the enable response's generation check; refresh guards its own await.
- The existing Node localStorage tooling warning remains recorded locally.
  Development fixture/lint failures were repaired; no new project anomaly remains
  to file. No live inference, commit, push, PR or deployment occurred.

**Return to Sol-high now.** Remaining ordinary work: independently inspect and
test final backend (including real Redis/PostgreSQL where possible), reviewer
adjudication, the production browser fixture (its centring expectation should
measure the full row including dismissal, not only the main button), whole-project
gates, final requirement reconciliation, scoped commit/push and gated PR creation.
The backend worker's last edits remain unverified; do not claim completion from
its earlier 193-test report. UI tests are mocked transport evidence and do not
prove server permission/opt-out/budget enforcement or a deployed release.

## Integrated implementation evidence (5 October 2026)

**Implemented, final PR gates pending.** This supersedes earlier checkpoint
statements that runtime code had not begun; they remain historical evidence.

Acceptance ledger:

| Requirement | Final implementation / observed check |
|---|---|
| Zero to three bounded cloud-grounded ready prompts | Owner/cloud/complete-message snapshot SQL; strict generated-output parsing and source allowlist; 69 focused backend checks passed |
| Exact centred hover/focus preview, immediate new chat | Full-row portal tooltip; isolated SDK/bridge body; production Next/React browser fixtures passed at 375, 768 and 1440 px, with exact prompt/new destination/no unrelated attachment and source inspection after reload |
| Preserve unrelated drafts and avoid replay | Primary page tests verify text/File identity stays in original scope; duplicate activation guarded; bridge refuses mixed payloads and never fails over an uncertain suggestion send |
| Default off / real opt-out | Isolated strict-boolean setting merge with user-row locking and monotonic Redis revocation epoch; hook disables separately from local hiding; negative tests cover queued and late publication refusal |
| Admission / freshness / encryption | Redis TIME rolling four-attempt Lua, fenced lease, no GET inference, separate processed identity, one-hour encrypted payload; source fingerprint/click CAS and encrypted persisted context |
| Real Redis/PostgreSQL behavior | Three fictional integration tests passed against isolated disposable containers from already-cached images: Lua concurrency/claims/expiry/rolling boundary/revocation, SQL owner/cloud binding/encryption/opt-out, message-edit and parent-delete locks; containers removed by their fixture |
| Existing permissions, budgets and history | Guarded account/background inference remains; isolated suggestion destination; copied user context distinct on history and kept after cache expiry/truncation; backend reviewer inspected normal/Council paths |
| No unrelated architecture/dependency/deployment change | No schema migration, dependency, provider/model route, env variable or deployment change; feature matrix updated as Web experimental / Mobile eligible, not a release claim |

The production browser run exposed a fresh-tab integration gap: generation zero
has no in-memory token until existing cookie refresh completes. The hook now
permits that one authenticated bootstrap through `ensureAuthHeader`; revoked
nonzero lifetimes with no token stay blocked. A new bootstrap regression test and
14 hook checks passed; production rebuild and all three browser cases then passed.
No auth helper, permission, token-storage or server contract changed for this fix.

Independent backend `review-go` review `ses_ef49069acffeupS25SJTQB7gh0` found no
material security/concurrency blocker. Primary inspected actual diff and evidence.
Its minor refresh-error description is rejected: the route emits unavailable,
not an invented empty result. Invalid pre-existing settings yielding 500 is not
an observed runtime anomaly or authority bypass; no unrelated settings rewrite.
Read-only review cannot claim test execution; primary ran the actual checks.

Observed gates before final wrapper run: backend lint/format/high-severity Bandit,
dependency audit and **5,104 pytest passes / 152 skips** passed; basedpyright first
identified two optional-value assertions in the new integration test, then passed
with **zero errors/warnings/notes** after correction. Full Bandit remains the
existing non-blocking finding inventory. Frontend type/lint/format/audit/security
and **875 tests** passed, and the final production build passed. One unrelated
worker probe with console output was discovered in final path inspection and
removed; it had not existed in the initial isolated checkout. Final gates will
reconcile the resulting test count on the integrated state.

Evidence logs and browser captures are local under
`/home/sol/.cache/opencode-contextual-home-20261005/`; disposable build-cache
module-resolution repair and Node/install warnings are recorded only in ignored
`.triage.local.md`. No existing deployment, real account content or live inference
was used. Browser fixtures verify real rendering with mocked APIs, not live auth,
provider recommendation quality, software keyboard or a complete accessibility
audit. Council completion is not an automatic generation event in this version;
manual refresh and source revalidation still work, and Council execution remains
compatible. Candidate invalidation occurs on server reads/clicks, not via a new
push/event protocol. These limitations belong in the PR.

## Final dispatch-boundary escalation packet

**Blocked pending bounded supervisory repair; no PR/commit/push performed.**
Fresh integration `explore-luna` review `ses_ef4876e21ffe6a3fLfEpigklyC` found
one material auth-lifetime gap in the Sol-owned page seam. Sol inspected and
accepts the finding: activation captures the generation, but the transport sets
`chatRequestGenerationRef.current = getAuthGeneration()` when invoked later.
The SDK may yield before that invocation. Auth revocation clears the suggestion
ref, but the queued send still owns the old prompt/ID; relabelling it with the new
generation permits its dispatch with new credentials. Backend ownership checks
refuse an unavailable ID, but cannot undo disclosure of that stale request to the
server under the wrong account. Existing mocks do not exercise this interval.

**Specific Astra mandate:** close this one dispatch boundary and verify it,
then return to Sol-high immediately. Do not take over backend, docs, gating or
the PR. No API/schema/auth-provider change is needed or approved. A minimal
implementation should retain the activation generation in the particular queued
send's transport options/body (client-local only), check it before and after
awaiting the existing auth helper, and remove any internal guard field before
network dispatch. Do not rely on the mutable suggestion ref after revocation,
and never replace the original lifetime with the current one. Ordinary chat's
existing model/destination behavior must remain unchanged.

Required discriminating regression: defer the SDK transport invocation after
activation, switch/clear auth, then invoke it; **no request carrying the old
prompt/ID** may reach fetch. Also revoke while auth refresh is awaited. Test the
actual transport boundary rather than only asserting `stop()` on a mocked SDK.
An unchanged-generation activation must still send exactly once with a new null
destination; unrelated drafts/files and private-source persistence must remain
isolated. Use fictional inputs only. Fresh permission-enforced review is required
for the auth repair; do not treat the previous reviews as approval of its diff.

Preserved state: 69 focused backend tests and 3 real disposable Redis/PostgreSQL
integration tests passed; full backend pytest observed 5,104 passes / 152 skips,
type checker zero diagnostics after two test-only narrowing assertions; full
frontend 875-test gate passed at the pre-bootstrap state and final rebuilt
production browser fixture passed all three viewport cases after bootstrap fix.
No source code has been changed since accepting this final-review finding.
All changes remain uncommitted on `feat/contextual-home`; backend review found
no material blocker and the generation-zero bootstrap finding was rejected by
the fresh reviewer as bounded/safe. Final all-family wrapper gates remain pending.

Return criteria: exact deferred-dispatch and refresh-revocation checks pass,
scoped type/lint/format pass, independent finding adjudication recorded, no new
material blocker. Remaining Sol work: final full-family gate runner / pre-commit,
freshness and final requirement/path inspection, scoped commit/push, gated PR
creation and visible top-level PR review comment; never merge/deploy. Local
tooling anomalies remain in ignored `.triage.local.md` only.

### Astra dispatch repair return — resolved

Activation now includes its captured generation in the queued SDK body. New
`frontend/lib/chatTransportFetch.ts` rejects a missing/stale suggestion lifetime
before credential refresh and rechecks after it; the internal guard is deleted
before network dispatch. The normal chat model/destination behavior is preserved.
`frontend/app/page.tsx` delegates its existing transport callback to this helper.
No backend/API/auth-helper/provider or token-storage change was made.

Final-state evidence: **17 tests passed** across
`suggestion-dispatch-auth.test.ts`, `suggestion-page-integration.test.tsx`,
`suggestion-chat-bridge.test.ts` and `suggestion-submission.test.ts`. The new test
uses the actual SDK `DefaultChatTransport`, defers its fetch callback, switches
auth after the send is queued, then invokes the helper and observes no network
call, refresh or generation relabelling. Separate checks cover revocation during
refresh, missing lifetime and unchanged-generation exact dispatch without the
guard. `npm run type-check`, scoped ESLint and scoped Prettier checks pass.
The test-only SDK-options type error was corrected and final checks rerun.

Fresh read-only `review-go` review `ses_ef4827bc8ffeteDblvS4FM8hQ5` found no
blocker. Adjudication: accept the source-backed closure of the queued-send race;
reject its description of the type tool as basedpyright (this was frontend tsc),
and its suggestion that per-request auth refresh is new (the original callback
already did it). Its pending typecheck note is now discharged by the final pass.
It did not run tests or review backend authority. Existing Node tooling warning
is unchanged; no new anomaly remains to file.

Changed files in this intervention: new transport helper and auth regression test,
page transport/activation seam, page integration expectation and this return
packet. **Return to Sol-high immediately:** the specific auth escalation is
resolved. Remaining ordinary work is final all-family gates, final browser build
verification after the transport change, staged-path/requirements reconciliation,
scoped commit/push, gated PR and its visible review summary. No commit, push,
PR, deployment or merge was performed by Astra.

## Final integrated verification

After the dispatch repair, `scripts/local_ci.sh` passed **every blocking gate**
on the integrated feature tree: 5,104 backend tests passed (152 skipped), 879
frontend tests passed across 70 files, backend/frontend type/lint/format/security
audits and production build, feature matrix, pre-commit and secret scanning.
Existing full-Bandit findings remain non-blocking inventory; no gate was weakened.
Documentation freshness and staged whitespace checks passed independently.

The final production Playwright contextual-home suite passed all three viewport
cases again after the new transport helper was built, verifying exact centred
preview, immediate isolated new-chat submission and persisted source inspection.
Final logs: `final-all-gates.log`, `browser-integrated.log` and
`browser-integrated/` under the local evidence directory stated above.

Status: **verified source implementation, not deployed**. Independent review
findings are resolved as recorded above. The scoped feature commit and gated PR
are the only authorised remaining publication actions; human review is required
before merge. Anomalies: no new unresolved project/upstream warning to file;
three local tooling observations are preserved in ignored `.triage.local.md`.

### Verified repository evidence

- Clean branch `feat/contextual-home`, based on `origin/main` at `67f1f63a`, in the
  registered worktree `/tmp/opencode/contextual-home-20261005`.
- `frontend/app/page.tsx:465-495`: transport overwrites outgoing `body.id` with
  active `currentId`; immediate submit after asynchronous creation cannot assume
  the new ID has already propagated.
- `frontend/app/page.tsx:696-741,838-850`: normal submission has auth-generation
  checks and submission receipts; existing New Chat explicitly resets drafts and
  must not be reused unchanged for suggestion submission.
- `orchestrator/models.py:100-120`: no trusted suggestion/context-binding field.
- `orchestrator/main.py:2321-2373,2429-2443`: destination owner checks, ordinary
  persistence and history retrieval exist; no candidate/source revalidation path.
- `orchestrator/routes/conversations.py:120-163`: account-scoped list and
  owner-checked detail retrieval are available.
- `orchestrator/memory/store.py:957-1054`: messages may change in place without
  a dedicated revision column. Recent-message reads need explicit status/order
  handling. Content is encrypted; generic metadata is JSON, not a safe place for
  plaintext source excerpts (`:972-973`).
- `orchestrator/worker/jobs.py:778-787`: existing background/account-compute
  mechanism is reusable, not authority to bypass budgets or provider policy.
- Read-only evidence children: `ses_ef4e176b7ffe1USi1c2tUPRah8` (frontend) and
  `ses_ef4e1769affeaqXiELYGbYs1H4` (backend); reports independently inspected by Sol.

### Current verification / omissions

Only the approved standalone report/study was copied from `/home/sol/daemon`;
the original dirty checkout and unrelated changes are untouched. The study's
final centring run passed 86 layouts, 28 interaction entries and 29 exercised
controls, with no skipped controls, errors, warnings or external requests; its
hash-bound evidence is in the study folder. This verifies fictional interaction,
not runtime context binding, privacy isolation, inference quality or budget races.

Production tests, security gates, full build, commit, push and PR are unperformed.
No credentials or real private content have been passed to an external provider.
Future implementation should use mocked/fictional test inputs unless separately
authorised. The worktree was created with 2.70 GiB free; monitor build/install
space rather than bypassing lifecycle guards or deleting unrelated temporary data.
