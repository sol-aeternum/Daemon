# UI history pagination (#230)

**Contract approved by the owner on 28 October 2026:**
[approval record](https://github.com/sol-aeternum/Daemon/issues/230#issuecomment-6461676784).
Source baseline: `df8085b40f2da796664b70b0e40d1f1d1823f7e0`.

## Implementation boundary

Backend slice: `orchestrator/routes/conversations.py`, the UI-only page readers
in `orchestrator/memory/store.py`, and `orchestrator/history_pagination.py`.
Frontend foundations in `frontend/lib/historyScope.ts` and `historyPages.ts`
implement scoped GET cancellation, strict cursor-page validation, precise tuple
ordering and a display-only older-prefix projection. `conversationPager.ts` is
connected to the single history hook/provider for live conversation-list paging
and bounded refresh/revalidation. Sidebar Load More/Retry controls are wired on
chat, settings and shared sidebar surfaces. This is source-only, not deployed.
`messageHistory.ts` now owns selected persisted pages separately from the bounded
SDK tail, with older-page, tail-gap bridge and covered-range refresh methods.
The provider and selected/follower reads use it. Load Older/Retry, displayed-prefix
adoption, scroll/media anchors and focus/visibility/reconnect refresh are now wired
locally. This integration is partially verified, not release-ready. Deferred
task-qualified saved-tail adoption now survives composer/SDK/Stop completion.
Browser qualification passes for mobile/desktop anchors, delayed media, focus,
list access beyond 200 and bounded outbound send/retry payloads. The owner-approved
normal merge from `main` now integrates #302's merged contents implementation
(#591); current-head project gates and PR review remain required. No release or
deployment claim is made.
No deployment, migrations or model-context/extraction/retrieval changes.

## Requests and responses

Opt into cursor mode; page sizes default to 100 and accept 1–100:

```http
GET /conversations?pagination=cursor&limit=100
GET /conversations?pagination=cursor&limit=100&cursor=<next_cursor>
GET /conversations/<id>?pagination=cursor&limit=100
GET /conversations/<id>?pagination=cursor&limit=100&before=<next_cursor>
```

Existing conversation/message fields and `active_task`/`latest_task` remain.
List responses add `page`; details add `message_page`:

```json
{
  "has_more": true,
  "next_cursor": "<opaque continuation>",
  "start_cursor": "<first display boundary>",
  "end_cursor": "<last display boundary>"
}
```

Fetch `limit + 1` rows and return at most `limit`. `has_more` describes the query's
observation, not a permanent completeness guarantee. `next_cursor` is null iff
there is no additional row. Empty pages have null start/end boundaries. For
lists, continuation is the end boundary; for chronological message display it is
the start (oldest) boundary. The lookahead row is never decrypted or returned.

List `total` counts **all authorized matching conversations**, not the current
page or cursor suffix. One SQL statement observes count and page, even on an
empty page beyond the end. Clients must not interpret `total` as a snapshot ID.

Keep tokens verbatim: decoding/re-encoding a timestamp through JavaScript `Date`
loses microseconds. Tokens are bounded, versioned and scoped to owner and list
order or conversation. They are not capabilities or credentials; every read
still enforces ownership. Deleting the cursor row does not invalidate its value
boundary. Malformed, unsupported or wrong-scope tokens return
`400 {"detail":{"code":"invalid_cursor"}}`; invalid limits return 422.

## Ordering and consistency

Lists use descending `(COALESCE(pinned,false), effective_last_activity_at, id)`.
Effective activity preserves the existing owner-qualified saved-message and
conversation activity calculation. All three tuple fields participate in the
exclusive continuation comparison; UUID is the unique tie-breaker.

This is **live pagination, not snapshot consistency**. Pins, updates and new
messages can move conversations across a previously read boundary. An activity
cutoff alone would not fix that. Clients must implement the refresh policy below.

Messages use immutable `(created_at,id)` keys, select descending strictly before
`before`, trim, then return ascending for display. All history statuses remain
visible. An inclusive `through=<boundary>` lower bound supports refreshing a
loaded range, optionally combined with `before` to walk it in bounded pages:

```http
GET /conversations/<id>?pagination=cursor&through=<previous_oldest_boundary>
GET /conversations/<id>?pagination=cursor&through=<previous_oldest_boundary>&before=<next_cursor>
```

`has_more` on these requests describes only that range. Its exhaustion must not
overwrite the independently retained older-history continuation. Concurrent new
tail rows do not shift an older boundary. Late commits/backdated inserts inside
an already traversed range require a refresh; no cross-request snapshot is
promised. Content/status updates never change the ordering key.

## Compatibility

- Legacy list default 20/max 100 and detail default 100/max 500 remain.
- Offset reads remain supported. Their optional page object is null; list
  `total` is corrected for these callers too.
- Existing title-search matching, offsets, 50-result client pages and controls
  are preserved. Cursor mode refuses nonblank search; it does not replace search.
- Cursor mode refuses nonzero offsets. Continuation parameters require cursor
  mode; silently falling back to page one would hide a client error.
- `get_messages`, `get_recent_messages`, extraction's cursor and their callers
  remain separate and unchanged. No schema/index/dependency change is required.

## Frontend integration policy (source connected; final verification outstanding)

One `ConversationHistoryProvider` owns persisted history and cursors. List Load
More upserts by ID and advances the server boundary even when returned rows are
duplicates or hidden drafts. Sort rendering by the full current tuple, not by
insertion order. Keep traversal boundaries separate from rendered positions.

Refresh every 30 seconds by sweeping from the head through the frozen lowest
traversed boundary. Merge rather than replace page one. Revalidate previously
loaded IDs absent from the sweep via detail `limit=1`: update/reorder survivors,
remove only on authoritative 403/404, retain on transient failure with stale
status. Adopt a new continuation only after the sweep completes; retain loaded
survivors outside it and deduplicate future pages. Serialize reads; yield between
slices of at most five requests, including revalidation. A conversation moving
upward behind a sweep is recovered by a later head sweep, not a false snapshot
promise. Use an updates-pending indicator during incomplete reconciliation.

Older-message loads prepend/deduplicate by real persisted ID. Fast task polling
merges the latest tail; when it no longer overlaps, page backwards to bridge the
gap with a visible syncing state. Reconnect/idle refresh covers the previously
loaded range; only a completed successful covered-range read can establish a
message's absence. Preserve older cursors and local streaming authority.

Scope all state/requests to auth generation, conversation/filter and request
generation. Abort on scope changes; compose caller cancellation with timeout and
discard stale responses after authentication, fetch and body parsing. Preserve
loaded content on transient errors. Cancellation must not cause URL fallback.

**Display history is not model context.** Keep paginated history outside SDK
submission/regeneration state. Derive `displayedMessages` from provider-owned
persisted history plus the SDK transport/live tail. Loading older pages must not
expand outbound messages or fallback context. Existing model-context assembly
and task/idempotency behavior stay unchanged.

Add native, keyboard-accessible Load More/Load Older buttons with 44px mobile
targets, visible focus, busy/error/retry and polite status. Make partial history
explicit. Preserve the first visible message ID plus viewport offset through
prepend/layout/media sizing; never force bottom-follow. Define focus/anchor
fallback when the control or anchor disappears.

Coordinate shared hook/provider/page edits with #302 serially. The contents rail
consumes the same displayed messages and partial state, updates on prepend, uses
message IDs and loaded-relative labels, and does not create another cache or
claim the whole conversation is indexed. Keep loaded content in the DOM and
preserve existing browser-find/collapse behavior.

## Verification and remaining delivery

`tests/test_history_pagination.py` exercises cursor validation, route metadata,
bounds, ownership, legacy search/offset compatibility and page trimming.
`tests/test_history_pagination_postgres.py --history-postgres` uses the existing
finite, network-none cached PostgreSQL harness with synthetic tables (no app
migrations or DSN fallback): 0/1/100/101/200 messages, equal timestamps, deleted
anchors, insertions, inclusive refresh, owner-qualified activity/counts, list
movement and final/empty/search pages. Run via the registered task runner so its
resource/evidence directories are supplied. It stops only its owned fixture.

Legacy browser fixtures now use cursor-compatible IDs, owner fields, bounded page
metadata and query-aware route matchers. Remaining work is current-head PR
gating/review and the existing DevTools CSP inventory. The previously separate
#302 patch is now merged upstream through #591 and integrated through a normal
owner-authorized merge, not copied from an unmerged peer checkout.
Required additional cases: account/filter/A–B–A races after each async boundary,
polling after older pages, more than one page arriving while disconnected,
partial refresh failure, no context expansion, and mobile/desktop anchor/focus
and contents-rail updates. Full project gates and final review are still required
before declaring #230 complete.

### Frontend foundation slice

`historyScope.ts` accepts the existing auth-generation getter; it does not change
authentication or hold credentials. Same-generation readiness notifications do
not invalidate requests. Selected requests also carry a committed selection epoch
so A–B–A is not mistaken for the original selection. Search families can be
invalidated before debounce. The GET reader checks its lease after authentication,
fetch and body parsing, composes cancellation with a timeout covering the body,
and never falls back after intentional cancellation. Callers must keep the lease
through validation/publication, guard deferred consumers/updaters, and release
it after their final publication. The list controller and title search use this
scoped GET transport. Mutation response publication checks auth generation, and
the legacy request helper composes caller cancellation and checks generation
before URL fallback. Selected-message and task requests now also use captured
selection/auth scopes. Consumer-side recovery guards and SDK/display adoption are
connected, including deferred saved-tail recovery; final browser/project
verification remains open.

`historyPages.ts` rejects an entire malformed page before its continuation can be
adopted: real UUID identities, owner/conversation agreement, bounded rows, exact
timestamp order and coherent metadata are required. Legacy index-generated IDs
are never accepted as persisted identities. Cursors remain opaque; timestamps
are compared at microsecond precision without changing the TypeScript target.

The display projection takes an explicit oldest persisted boundary captured when
a server tail is adopted into the SDK. It prepends only provider rows strictly
older than that cut and preserves SDK object identity/order for the tail. Newer
persisted polling results cannot create duplicate versions of live messages with
different IDs. It never matches by content or timestamps of local messages.
The caller must adopt the boundary atomically with the corresponding SDK tail,
fence auth/selection changes and preserve unresolved turns. An unadopted new chat
has no persisted prefix. This pure projection is not a second history cache.

`frontend/__tests__/history-scope.test.ts` and `history-pages.test.ts` cover these
helpers with synthetic deferred-response, identity, metadata, ordering and
display/transport-isolation cases. Provider/page and follower regressions now also
exist; real outbound payload/browser qualification and final review remain
outstanding. Helper tests alone do not establish UI safety.

### Conversation-list integration slice

`conversationPager.ts` replaces the list's first-page replacement with one
provider-owned ID map. It preserves loaded rows, updates the opaque continuation
even through duplicate-only/hidden-draft pages, and sorts by the complete precise
tuple. It serializes Load More and refresh reads; each refresh freezes the lowest
traversed key, sweeps from the head, and revalidates absent loaded IDs using detail
`limit=1`. Only authoritative 403/404 removes a missing ID; transient failures
retain content, the old continuation and an updates-pending/error state.
Continuations are adopted only after a complete sweep. Row revisions prevent
older reads from undoing optimistic edits or deletions. If explicit paging
extends the traversal while a sweep is yielding, that sweep does not overwrite
the extended continuation. At most five requests run per scheduling slice,
including revalidation, with queued explicit paging prioritized before the next
refresh step. This remains eventual live reconciliation, never a snapshot claim.

`useConversationHistory.ts` subscribes to the one list owner with
`useSyncExternalStore`. List/search output is render-gated by auth generation;
account changes abort old list/search reads and clear their account-owned state.
Same-generation readiness keeps loaded content. Search retains its existing
offset/50-result behavior, invalidates a changed query before debounce and
advances offsets by raw server rows rather than deduplicated display length.
Selected detail uses the separate persisted owner below. Rendering now consumes
the display projection while submission/event/task logic retains the SDK tail.

Sidebar pagination controls sit outside the visible-row branch so an entire page
of hidden drafts cannot make older conversations unreachable. They use native
buttons, the existing 44px touch token, focus styling, busy/partial/error state
and a polite status region. The same button remains on exhaustion to preserve
the focus target. `conversation-pager.test.ts`, `conversation-paging.test.tsx`
and the existing search/persistence suites cover these paths, including account
and late-body/create-response races, StrictMode reconnect and bounded scheduling.
Real browser qualification and full project gates remain required.

### Selected-message owner slice (verification incomplete)

`messageHistory.ts` replaces selected-detail React state with one scoped owner.
Initial and follower reads use cursor mode and return only the latest bounded
tail in `Conversation.messages`. Older loads upsert into the persisted map and
advance an independent continuation without changing the tail object. Tail polls
bridge backwards to the previously loaded newest value when windows no longer
overlap; covered-range refresh uses the paired oldest opaque cursor/value and
removes absent rows only after the whole range succeeds. Both traversals yield
after five requests; transient/validation failures retain loaded progress and
retry boundaries. Initial retry delays/cap and permanent 403/404 states remain.
Authoritatively inaccessible selected history is cleared rather than relabelled
as a transient success.

Auth/committed-selection epochs abort and detach old queues, including A–B–A and
transports that ignore abort. Public selected history is render-gated. Returned
tail objects are tagged with their scope, and a `canUse` check rejects stale
scope or a superseded tail revision at the initial/follower/reconciliation SDK
setter sites. Task GETs/cancellation capture the same selected scope before
authentication and return unknown/unconfirmed after invalidation. Independent
legacy detail lookups remain tail-only and are auth-scoped.

The chat child subtree is keyed by actual auth generation, never by conversation
ID; normal new-chat promotion keeps streaming/Stop state. Unmount stops the local
SDK observer, not the durable server task. A locally initiated turn qualifies its
stream generation before transport starts; conversation events with no qualified
current generation cannot navigate after an auth reset. After-await recovery
consumers also check their captured scope, and suggestion readiness notifications
only reset state when the actual auth generation changes. Final recovery/adoption
verification remains open.

`message-history.test.ts` covers tail/map separation, gap bridging, covered-range
failure/deletion/cursor behavior, scope invalidation, retry caps and disposal.
Selected-history hook/body-race tests were also added. The latest completed
seven-file integration run passed 97 tests, including adoption, prepend/media
anchors, recovery and selected-history cases. Earlier frontend type/lint/format
checks passed, but predate the latest view-ownership changes. Lost producer
records were reconciled under unchanged guards as stopped with exit status
unknown; no unresolved scope was found before the latest regression run.
The selected-owner review found no defect with a disclosed same-OpenAI-family
limitation; it does not cover the newer display/scroll/recovery integration.

### Display integration and deferred recovery repair

`TailAdoption` publishes the persisted presentation boundary before the SDK setter
can render. `page.tsx` prepends only provider rows older than that adopted cut,
keeps Load Older out of SDK messages, gates wrong-route SDK rendering/regeneration,
and exposes loaded-relative message labels and persisted tool/reasoning metadata.
The same native history button remains mounted on exhaustion. Browser testing
showed that native `disabled` still drops focus; history/list pagination controls
now use guarded `aria-disabled` instead. Real Chromium tests verify focus retention
through loading/exhaustion, including paging beyond 200 conversations.
`useChatScroll.ts` preserves a surviving message ID/viewport offset across prepends
and delayed media sizing, with explicit user-scroll release and successor fallback.
Gap/range incompleteness remains visible across successful later tail polls.

The discriminating test in `durable-page-integration.test.tsx` first reproduced
`showSaved` losing authority when the composer was busy. After bounded supervision,
the repair retains only task ID, history scope and local-send revision, not a
message snapshot. Flushes after composer completion and committed provider/busy
changes read the provider's current tail. Null/stale rendered getters retain
authority; successful adoption consumes it. New sends, selection epochs, unmount
and task mismatch invalidate it. An SDK error alone does not block task-authorized
adoption once safe. Authority is never broadened to a different task; this does
not promise automatic visibility of a newer completed task while generic adoption
is otherwise blocked.

The current state passed frontend type/lint/format, production build, dependency
audit at its configured threshold, braces security checks and all 1,607 Vitest
tests across 116 files. New tests exercise both provider-commit/read
orderings, newer sends, A–B–A, stale/null getters, SDK/Stop completion, failed
ownership validation, StrictMode and late callbacks after unmount. A fresh
read-only source review found no defect, with a same-OpenAI-family limitation and
unavailable external diff; focused reviews of the later focus and Retry-status
repairs likewise found no source defect. These limits remain disclosed; no merge
or deployment approval is implied.

`playwright.history.config.ts` and `e2e/history-pagination.spec.ts` add isolated
loopback/synthetic cursor and SDK fixtures at mobile/desktop widths. Chromium's
long managed temporary path exceeded its UNIX-socket limit; a relative browser
temp path inside the registered checkout fixed launch without external scratch.
The browser reached 200 displayed messages and found exhausted-control focus
loss. After guarded verification resumed, all three dedicated browser tests passed:
390px/1440px focus and real prepend/media anchors, access beyond 200 conversations,
and captured actual SDK send/retry requests containing the bounded latest tail but
none of the older display-only prefix. Foreign-origin requests are aborted; no
provider/backend inference is used. The media fixture is an assistant Markdown
message because user messages intentionally display text literally.

Qualification also found [#600](https://github.com/sol-aeternum/Daemon/issues/600):
the SDK calls `onFinish` after `onError`, so unconditionally restoring Connected
hid Retry after both HTTP and streamed errors. A narrow presentation repair now
derives connection status from `isError`/`isDisconnect`, without changing task or
submission behavior. The new callback-order regression failed before repair and
passes now; actual browser Retry is exercised using an HTTP 503 fixture.

Backend full lint/format/type and high-severity SAST pass (zero high findings);
the full SAST inventory is non-blocking. Python audit reports no known
vulnerabilities. Frontend audit passes its high-severity threshold but reports
two moderate findings; braces test tooling reports one low finding, tracked with
the existing dependency inventory in #311. No dependency or audit threshold was
changed. Feature-matrix validation passes.

The owner subsequently authorized gate-only reads of checked-in benchmark
artifacts/checkpoints, without paid inference, live benchmark execution or artifact
edits. Under that narrow authorization, full backend pytest passed: 7,167 passed,
1,272 skipped. The new dynamic SQL list reader also has an explicit unresolved,
uncovered catalogue entry, preserving the writer scanner rather than weakening
it; all 89 catalogue tests pass. All-files pre-commit and an additional pass over
new untracked task files pass, including secret scanning. The HTTP-cache browser
gate passes all 30 tests, and its ownership fixture passes all three tests.

The first non-blocking default browser inventory reported 9 passed / 44 failed,
including legacy fixtures incompatible with cursor reads. The fixtures have now
been migrated through `e2e/history-fixtures.ts`: real persisted UUIDs, matching
owner/conversation IDs, truthful bounded pages, exclusive `before`, inclusive
`through`, and pathname matchers accepting cursor query strings. Existing search
offset fixtures and behavioral assertions remain intact; helper contract tests
pass.

The final serial inventory reports 32 passed / 21 failed, with every remaining
failure confined to design-token diagnostics rejecting the same Next.js DevTools
inline-style CSP error tracked in #490. Both long-chat viewport tests pass in
this run; earlier parallel and cold-start runs exceeded the unchanged overall
test deadline. The runner lost its completion lock after writing the final
inventory report, so command-exit success is not inferred from that report.
No CSP policy, diagnostic allowlist, visual baseline, assertion or timeout was
weakened. This non-blocking inventory remains uncleared and is a disclosed PR
limitation, not a release-clearance claim. These results predate the upstream
contents integration below.

### Upstream contents integration (#591 / #302)

After PR #605 was opened, the owner authorized a normal merge of current `main`,
including unchanged upstream benchmark artifacts (gate-only reads, no live
benchmark execution or artifact edits). Contents navigation from merged #591 now
consumes the same `displayedMessages` as rendering and scrolling, including newly
prepended prompts. No second persisted owner or SDK-tail expansion is introduced.

Focused supervision established scroll precedence: explicit contents navigation,
browser find, Latest or user input revoke pending prepend/saved-reading placement;
otherwise prepend anchoring precedes saved reading anchors and bottom-follow.
Each placement frame checks an invalidated operation token and its container
before touching scroll, focus or saved UI metadata. Committed conversation/auth
changes and unmount invalidate these operations, including A–B–A and already
delivered stale callbacks; speculative renders do not revoke the current view.
Resize callbacks cannot override pending explicit placement. Browser find and
collapsed-message reading-position restoration remain supported.

The integrated five-file UI regression run passes 96 tests, including StrictMode,
auth changes, stale callback delivery, saved-restoration cancellation, delayed
media and navigation during prepend. Existing production contents/ergonomics
browser assertions pass all 11 cases. The history browser suite retains its
original focus/media/payload assertions and passes all five cases, including
separate mobile/desktop checks for contents navigation into a prepended page
before media completes. Browser
HOME is confined to the task-managed temp directory; relative TMPDIR retains the
UNIX-socket workaround, with no broad environment forwarding or parity allowlist.

A fresh enforced read-only review of the integrated source and relevant callers,
tests and fixtures found no source-backed defect. It uses the existing OpenAI
OAuth fallback and is same-family with the authors; it is not independent-family
approval or permission to merge. Full current-head gates/CI, completed Codex PR
review and human final review remain required. No PR merge or deployment.

After upstream integration the full backend gate passes 7,357 tests (1,273
skipped; 16 subtests). The frontend suite passes 1,634 tests across 119 files.
The sole code-gate failure was an exhaustive-deps warning about reading a mutable
scope ref in cleanup; cleanup now captures its scope token without weakening
invalidation or suppressing the rule, and lint passes. Repeated cache fixture
verification encountered occupied loopback ports followed by TIME_WAIT sockets;
the fixture correctly refused exclusive binding. No foreign listener was probed,
adopted or terminated, and the gate was not skipped or loosened.
After the sockets expired, ownership checks passed all three cases and the
HTTP-cache browser gate passed all 30. Backend, frontend and aggregate blocking
gates are verified across the final-state runs; the cache retry and lint repair
are recorded rather than relabelled as an initially clean run.

The integrated default non-blocking browser inventory was rerun with no retries:
32 passed / 21 failed, a normal test-failure exit with no lifecycle ambiguity.
All 21 failures remain design-token diagnostics for the single DevTools CSP
style hash tracked in #490; there were no timeouts or screenshot-comparison
failures. Policies, diagnostic allowlists, baselines and deadlines remain intact.
