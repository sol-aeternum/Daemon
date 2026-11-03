# UI design tokens

The token values live in `frontend/app/globals.css`; named Tailwind utilities
are mapped in `frontend/tailwind.config.ts`. This implements issue #34.

- Use `--color-text-primary`/`secondary`/`muted` on app surfaces. Use
  `--color-text-inverse` only on inverse surfaces: it changes with the theme.
- Use `--color-text-on-accent` for filled accent controls, `on-status` for
  error/stop fills, and `on-warning` for warning fills.
- Media stays dark in both themes. `bg-media` and `text-on-media` support
  opacity modifiers such as `/50`; document paper and QR quiet zones have
  separate fixed light background tokens.
- `text-xs` is the smallest label size (12px at the default root size).
  Use the font-size scale instead of arbitrary pixel classes.
- `min-h-touch` and `min-w-touch` reference `--touch-min` (44px).
- Use scale utilities for ordinary spacing. Component geometry that cannot
  use the scale has a named token, including sidebar width, message widths,
  preview heights, and the Studio and skill-editor grid columns.
- Safe-area utilities retain the device inset while respecting spacing tokens.

The owner-approved Midnight P1 refresh evolves the existing blue/slate roles in
both themes; Dark/Light/System remain the appearance choices. Its interaction
scope and implementation evidence are recorded in
`docs/research/midnight/DESIGN.md` and `IMPLEMENTATION.md`. The standalone study's
screenshots are not application release evidence.

`npm run lint` rejects literal Tailwind values, black/white utilities, and the
retired `daemon-*` color namespace in app and component source, including
conditional templates and shared class maps. Explicit `var(--token)` values
and named utilities remain supported. This policy does not inspect generated
document contents or dynamic inline measurements.

`npm run test:browser -- --project=design-tokens` checks eight pages in both
themes, including label sizes, touch sizing, overflow, and screenshot baselines.

Linux browser tests use the checked-in **Noto Sans 2.015** hinted TTF faces under
`frontend/e2e/fonts/`, matching the existing references. The release URLs, archive
checksums and individual font checksums are pinned in `manifest.json`; `OFL.txt`
retains the upstream license. Regular/Medium/Bold and their italics reproduce the
reference environment's weight selection (600 uses Bold). Noto Sans Mono 2.014
Regular supplies the chat keyboard hint; Liberation Sans 2.1.5 Regular supplies
missing arrow glyphs. Their upstream licenses are retained beside the fonts.
No fonts are downloaded
at test runtime. Update this test-only dependency only with separately reviewed
visual evidence; a font update does not authorize PNG replacement.

The Playwright launcher verifies the font bytes and gives Chromium only the fixed
`FONTCONFIG_FILE` pointing to `fonts.conf` and a checkout-local `XDG_CACHE_HOME`.
That config loads only these local faces, explicitly pins antialiasing, slight
hinting and the LCD filter, and uses a checkout-local ignored cache. Host font
packages, user fontconfig,
and caller font overrides cannot select a different system font. It applies to
both the default development inventory and complete qualification on Linux; app
CSS, production assets and deployment configuration are unchanged. Other platforms
retain their native browser environment and cannot qualify the Linux references.
Each design-token capture writes a retained `resolved-fonts.json`, including on
passing tests, with the browser version and Chromium CDP's actual resolved font
families, PostScript names, custom-font flags and glyph counts from text nodes.
The same font evidence is attached to `capture-surface.json`.
The computed CSS stack and `document.fonts.ready` alone do not identify a face.

For owner-approved combined qualification, run from `frontend/`:
`npm run test:browser:qualification -- /absolute/retained/output-root`.
The command runs the intact DevTools runtime-observability case on an owned
development server, waits for it to exit, builds with the existing
`NEXT_PUBLIC_API_URL=http://127.0.0.1:3181`, then runs the remaining inventory and
the existing Midnight/contextual-home projects against `next start`. A failed
stage stops the command; a production-only pass is not combined qualification.
Development and production cannot concurrently mutate the same build output.
The separate CSP-preview fixture keeps its original isolated development server.
Isolated loopback ports 3180 (preview) and 3181 (app) leave default development
ports untouched. All servers refuse reuse. Budgets, retries, assertions and PNGs
are unchanged. Ordinary UI fixtures explicitly model unavailable service-worker
capability, assert that model, and retain Playwright's worker-registration block.
They exercise the real auth/cache barrier with synthetic acknowledgements, not
live accounts. Dedicated PWA/cache qualification remains separate and unchanged.
The development inventory still includes the observability case. The owner-approved
non-blocking CI inventory uses the complete combined command, never production-only
coverage; `npm run test:browser` remains the development diagnostic entry point.
The combined runner forwards only existing executable/home, managed temporary
directory, Python-environment and CI controls. It pins the isolated API origin;
credentials and Node injection options are not inherited by its child stages.
Browser output is retained in the CI `browser-regressions` artifact. Review
intentional visual changes before updating baselines with
`npm run test:browser -- --project=design-tokens --update-snapshots` on Linux.

The design-token fixture uses synthetic nested profile preferences (Alex, empty
instructions, personal suggestions explicitly off), empty memories, and an explicit
unavailable embedding configuration from `/status`. Missing/failed status stays
unknown; static eligibility stays unverified. A deliberate JSON 404 for
`/api/entitlements` preserves the conservative **Plan unavailable** fallback without
a live account or plan dependency. The initial `/home-suggestions` read explicitly
returns disabled, consistent with the saved suggestions-off preference.

Capture checks require loaded fixture content/fonts/images, the fixed browser
clock, 1440×1000/DPR1, en-US/UTC, the requested theme, stable geometry, zero scroll
offsets, and no idle DevTools badge or runtime-error overlay. Profile and memory
retain the original **top of the nested main scroller**: `fullPage` does not expand
that scroller. Their offscreen controls are checked in the DOM, not claimed as
pixel coverage. Additional scrolled-state references require separate approval.

`design-token-capture.ts` attaches raw console/network/runtime diagnostics and fails
unexpected page errors, console errors and request/HTTP failures before capture
and again at teardown. Known development diagnostics remain recorded: exact
source/message/style-hash matches for the already-observed Next DevTools CSP
violations, and the same-request Chromium `ERR_ABORTED` after an acknowledged
synthetic cache-clear 204 (reproduced by a standalone headers-only fetch control).
These are not CSP repairs or production/browser-equivalence claims. New errors,
application CSP errors, or failed cache-clear acknowledgements are not exempt.
The only other expected HTTP errors are the explicit fixture 404 and the dedicated
status-error case's 503. No pageerror is consumed, and no CSS hides error UI.

Owner-approved production diagnostics also record acknowledged RSC prefetch
cancellations separately: exact allowlisted same-origin page, GET, `_rsc`, both
RSC/prefetch headers, the same request's HTTP 200 `text/x-component` response,
and exactly `net::ERR_ABORTED`. The allowlist defaults empty outside this fixture.
Raw responses and failures remain attached. This acknowledges response headers,
not full body consumption. Missing/non-200 responses, other errors, API paths,
unmarked navigation, foreign origins and every page/CSP error still fail.

Reference PNG replacements are not part of fixture/capture qualification. Retain
the failing full-image comparisons and review their pixels separately; a passing
fixture check is not a green visual gate or approval to update snapshots.

On 30 October, the owner separately approved 14 reviewed references: the sidebar
history-pagination controls, chat message paging/Contents rail, and the durable
catalog with its clearly labelled legacy-media section, in both themes. Only
those references were regenerated; landing references, comparison tolerances and
assertions were unchanged. This is source-only visual qualification, not rollout.
