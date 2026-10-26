# Temporary Next Webpack HMR recipient and fallback-compiler repair

This development-only correction is scoped to pinned Next 16.4.1. Source and
current test evidence, not this maintenance reference, establish qualification.
It does not close #490 or the broader #477 automatically. Historical diagnostic
results (53 attempts, 49 pass, 4 fail, zero retries) remain separate; the mechanism
does not attribute every historical failure or demonstrate a production defect.

## Payload and lineage

`frontend/scripts/patch-next-hmr.cjs` pins both complete original and corrected
SHA256 identities. Its server payload edits remain the connection
initialization `this.publish({...SYNC...})` calls in
`next/dist/server/dev/hot-middleware.js:166` and
`next/dist/esm/server/dev/hot-middleware.js:156`, changed to
`this.publishToClient(client, {...SYNC...})`. The existing sender handles identical
serialization and the joining socket receives the unchanged snapshot.

Published source: [Next v16.4.1 hot-middleware.ts](https://github.com/vercel/next.js/blob/v16.4.1/packages/next/src/server/dev/hot-middleware.ts).
The locked tarball identity remains in `frontend/package-lock.json`; it is not
modified. Compiler BUILDING/BUILT broadcasts, payload/stat/error selection,
registration, App Router client logic and Turbopack remain upstream bytes.
**All four edited modules' source maps retain original sourceContent and mappings.**
Report raw installed file/function/line and
hashes when diagnosing; maps are not corrected or claimed byte-equivalent.

The owner-approved extension also pins both CJS and ESM
`client/dev/hot-reloader/pages/hot-reloader-pages.js`. Only in development, an
error page with the actual fallback runtime script namespace takes the existing
full-reload recovery path instead of checking a main-compiler notification against
its unrelated fallback hash. The runtime-script URL pathname must end in
`/_next/static/chunks/fallback/webpack.js`; asset prefixes/CDNs and query strings
do not change that identity. An ordinary `/_error` page alone is not sufficient.
Existing hash/idle gates, compile-error visibility and warning reporting remain;
duplicate successful notifications trigger at most one fallback reload per module
instance. No network/console filter or fake manifest is added. Normal updates,
reconnect handling and production behavior remain unchanged.

The 16.4.1 compatibility binding retains exactly those two literal corrections;
it is not an expanded version/hash allowlist. All four acquired module preimages
were checked against the locked archive before comparison with retained 16.3.8
source. The upstream vulnerability-insight constructor promise, notifications and
SYNC metadata, runtime-error replay/clearing, and other new upstream logic stay
unchanged. Synthetic collaborators now supply that constructor promise and an
explicit minimal environment; controls cover insight delivery and runtime-error
replay without claiming real optional-feature UI qualification. Historical 16.3.8
hashes and qualification remain dated evidence, not 16.4.1 clearance.

## Supported command boundaries

From `frontend/`, `npm run dev`, `npm run test:hmr`,
`npm run test:hmr:browser`, `npm run test:browser:cache` and
`npm run test:browser` explicitly run the built-in Node preparer before their
existing/dev test command. No new dependency, lifecycle/postinstall/prebuild hook,
feature flag, Dockerfile change, install/build/start guard or lock edit exists.
Skipping automatic lifecycle hooks does not skip these explicit command bodies.

Direct Next CLI, npx Playwright or programmatic custom-server invocations bypass
these prefixes. Before an authorized direct dev invocation, explicitly run
`node scripts/patch-next-hmr.cjs --apply` then `--check` in that **same stopped,
owned dependency tree**. This is a cooperative boundary, not arbitrary-shell or
OS-level writer exclusion. Never share a tree with a live dev/package writer
during patch application.

Clean locked install/build/start remain independent. The existing Docker install
layer remains unchanged. Broad Docker COPY includes tracked harness sources, but
does not execute them; only generated `.hmr-runs/` is newly excluded. A mixed
dev-test/build tree retains four changed dev-module bytes and **is not an
upstream-clean production artifact**. Production-only qualification uses a fresh
owned locked install; running a build separately after a patch failure does not
turn an uncertain tree into a clean artifact.

## Fail-closed state and recovery

The CLI accepts only `--apply` or `--check`, with no root/version/env override.
Package name/version, real ordinary owner-local paths, full-file hashes and
exact seams per artifact are required. All four original files may be prepared;
all four patched files succeed idempotently without target byte/mtime changes.
The same-release server-only state (both server postimages, both client preimages)
is the only supported partial state; only clients are written during extension.
The former 16.3.8 release is rejected, not migrated or reverse-patched in place.
Read-only check accepts only all four patched files and refuses in-progress locks.
Unknown/missing/symlink/unsupported/partial/mixed states are errors, never skips.

Preparers serialize through exclusive `.hmr-runs/patch.lock`. Pending outputs are
validated and staged on the same filesystem with preserved modes and exact
readback, then original identities are checked before each rename. **The
renames are not a multi-file atomic transaction.** Interrupted writes, renames,
readback or changed locks fail nonzero before an `&&` consumer starts and retain
staging/lock/failure evidence. Do not delete stale locks, fuzzy-repair bytes,
reverse-edit unknown files or silently roll back a partial pair. Preserve the
uncertain install and recover with a fresh supported locked install/task.

## Regression obligations and limits

The synthetic observer records response URL/status before attempting body reads.
An unavailable body keeps its separate error and is never counted as captured
served bytes. This preserves metadata on future runs; it cannot recover missing
response bodies or qualify any previously failed screen.

The synthetic server passes genuine Pages-fallback no-ID HMR upgrades through
unchanged. It uses the owned socket object only as a local tracking key; explicit
socket-close commands still address actual client IDs. After graceful HTTP,
socket and Next closure, the fixture flushes its stopped receipt and diagnostic
streams before exiting
itself with the existing exit code. A stopped receipt alone is not proof of exit.
Only fresh runtime evidence qualifies these harness repairs; they cannot stop an
already loaded retained fixture process.

Browser identity uses a prospective API receipt and actual Playwright
`pw:browser` launch log, associated with the returned worker browser object.
The HMR command enables only standard `DEBUG=pw:browser` logging before startup;
it does not change launch arguments or browser/security/network settings. The
observer forwards stderr unchanged, records actual launcher path/PID and rendered
argument text, resolves/hashes that binary before spawn and after launch, and
checks the supported live version API. This is **launcher provenance**, not CDP
command-line/loaded-memory attestation; log-rendered argument text is not promised
to be losslessly quoted argv. Synchronous logging/hashing adds timing cost. Missing,
ambiguous or changed launch identity fails the screen; catalog identity alone is
insufficient. The unsupported optional command-line query is not used or retried.
The parent approved this evidence-method correction after the first retained
screen failed before app startup (0 pass,1 fail,7 not run,0 retries); that failure
is not erased by subsequent qualification.

`npm run test:hmr` exercises exact preimage/guard/IO negatives, whole actual CJS
middleware and in-memory TypeScript-transformed ESM, plus whole installed client
decision modules with mocked transport/hot runtime/timers/dispatcher/DOM. These
effects are synthetic, **not native ESM-loader or browser qualification**.
Pages-client controls execute both full pinned formats: the original fallback
discriminator must attempt the wrong-compiler check; the repair reloads without
that request. Ordinary error pages, non-error documents, query-only lookalikes,
production, visible errors/warnings, legitimate updates, equal reconnect snapshots,
existing hash/status gates and honest update failures remain controls. Browser
verification stays separate; mocks alone do not prove delivery.
The unpatched preimage discriminator must broadcast changed join SYNC to existing
clients. The separate browser-free serial runner deliberately exits 1: exactly
one failed, one skipped, zero retries and no later execution marker are required;
this expected child failure is retained, not erased or counted as product failure.

`npm run test:hmr:browser` is a separate eight-case, one-worker, retries-zero,
explicit serial fail-stop screen. A private nonce/task/generation pipe controls
only its exact loopback 3151 child. Unknown frames/paths/edits/ports/socket IDs and
foreign ownership fail closed; no HTTP control endpoint, PID kill or service reuse
exists. Its pinned NextCustomServer initialized handler getters are test-only
internal APIs, not a public compatibility promise. Before bootstrap compilation
and validated arm, the fixture holds only exact `/_next/hmr` upgrades, without
accepting the WebSocket or modifying request/socket/head. It pauses each pending
socket and bounds admission to 16 sockets, 64 KiB retained head per socket and
120 seconds per entry. These are fixture bounds, not process-memory caps or changed
browser deadlines. Overflow, oversize, socket error and expiry fail visibly;
observed peer closure cancels that entry. Unrelated early upgrades are still rejected.
Arm drains each pending entry once through the same pinned Next handler, including
its origin checks and real handshake, then releases the pause after reader setup.
Stop permanently closes admission, clears pending timers/listeners and aborts
bootstrap fetch. It waits for a pending preparation stage before closing Next,
within the existing shutdown deadline; late startup cannot publish bound/armed or
resurrect sockets. A paused stream may defer observing peer FIN; final liveness
checks and expiry do not constitute perfect peer-liveness detection. An upstream
handoff throw/rejection closes fixture admission and initiates failed owned shutdown,
including on the already-armed path; it is never retried or hidden.
Restart therefore tests warm-stats ordering, not
stock CLI cold/no-stats startup. Shutdown requires actual owned sockets closed,
stopped receipt and clean child exit within ten seconds; uncertain resources stay
retained, never force-killed. Generated fixtures/evidence remain under `.hmr-runs`.

The six clean templates stay unchanged during controls. Only generated
counter/page/lazy source is mutated through fixed preimage-checked action names.
Cases require equal/changed multi-client joins, ordinary compilation, visible
state-retaining hot updates, server refresh, actual compile overlay/recovery,
needed runtime-error fallback reload, real socket reconnect, changed warm-stats
process restart, metadata/theme/indicator effects and final integrity. Served
bytes, frame/document/socket/hash identities, errors, mutations and ownership
receipts remain evidence. Missing preconditions, empty discovery, fail-stop skips
or teardown failures are not passing coverage.

Existing default Playwright config/e2e fixtures/tests/snapshots/assertions/timeouts/
scheduling/retries remain untouched. The actual **default four-worker inventory**,
existing cache-browser gate and all backend/frontend/repository gates remain
separate obligations. Generated next-env `.next/dev/types` transitions must be
captured separately from canonical `.next/types`, never silently restored or
called canonical-byte success. Cold/warm generated/cache state and actual browser
executable/version/hash/flags belong with every qualification result.

The separate stable-content rendering optimization in `MarkdownRenderer` caches
only a mounted pure Markdown subtree by text and a snapshot of all source fields
and their order. Fresh equivalent citation lists may reuse parsing; changed text,
source metadata or provenance must invalidate it. Wrapper styles, callbacks and
child-owned state remain live; no message/searchable hidden text is removed, and
no cross-mount/global cache is introduced. This is not another installed Next
payload or an HMR protocol change. Its parser-work discriminator, citation/update
negatives and actual browser checks remain separate from recipient qualification.

This recipient correction intentionally preserves conservative H1 SYNC -> H2
BUILT -> reconnect H2 reloads, same-hash restart ambiguity, no-stats ordering and
the existing same-request-ID replacement/old-close recipient-map limitation.
Request-insights dispatch and indicator expiry are mocked/function controls, not
optional-feature real UI or 24-hour browser qualification. No shortened expiry,
reload/error masking, retry-to-green, default serialization or snapshot updates
are permitted as acceptance substitutes.

## Retirement and rollback

Every Next update requires source/byte revalidation and the discriminating
regressions; guards intentionally fail on new versions/bytes. A sampled upstream
16.4.2 still broadcast connection SYNC, so upgrade alone is not evidence of repair.
Retire preparation only after equivalent upstream behavior passes these controls.
Approved rollback removes exactly the bounded source/command/gate/ignore/docs
changes and starts a fresh owned locked install restoring published bytes. Retain
failed evidence and disclose restored cross-client risk. No deployment, activation,
publication or cleanup is authorized by passing source tests.
