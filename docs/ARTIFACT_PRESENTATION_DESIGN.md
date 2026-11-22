# Chat output presentation — proposed design

Status: **Owner-approved design, 2026-11-22; not implemented.** Baseline:
`main` at `d9b330c4`. The approved target is D0 = E1 together with the
recommended D1–D7 and the tool contract in §6. **Script execution is not
enabled by this approval:** the current candidate ships passive previews and
downloads only, and E1 execution stays off until the release condition in D6
is met. Implementation follows the
delivery slices in §8. This approval does not cover any new SSE event type
(D5 P2), dependency, environment variable, migration or deployment. Runtime
inference remains DEC12-qualified Z-only.

## Approval history

- 2026-11-22: the owner asked for a design after a chat returned a full HTML
  dashboard as source only.
- 2026-11-22: the owner replied "Approve as recommended" to the first draft.
  That draft had been written from a checkout **436 commits behind `main`**.
  Two of its recommendations conflict with contracts already approved on `main`
  (§2). Implementation was not started. The decisions marked **changed** in §5
  need a fresh choice. The unchanged ones are carried forward and only need the
  owner to confirm they still stand.
- 2026-11-22: after the revision against `main`, the owner chose **D0 = E1**
  (sandboxed executable blocks, for the `render_block` path only). The D2
  classification ruling (chat blocks are message content, outside
  `ARTIFACT_CONTINUITY.md` §6) and the carried-forward D1 and D3–D7 are
  recorded under the earlier "Approve as recommended". #762's static behaviour
  for fenced HTML is unchanged.
- 2026-11-22: source review of #778–#781 found that the draft let ordinary
  fences run, ran SVG in the script frame, and claimed "no network access"
  beyond what the CSP can prove (it does not govern frame navigation or
  WebRTC). The owner chose **harden and narrow the claim**: D1, D6 and §8 below
  are corrected accordingly. Fences and SVG never execute; the frame is
  hardened and the UI promises only isolation from Daemon and the user's data.
- 2026-11-22: re-review of #778 asked that egress isolation be a condition for
  enabling E1, not only a wording change. The owner chose **keep execution
  off**: no generated script runs until browser qualification closes the
  residual egress in D6, or the owner explicitly approves a revised risk
  boundary.

Terminology: GLOSSARY reserves **artifact** for a durable, owned output with
identity and lineage (#489). This design produces **chat blocks**: content
scoped to a conversation that renders inside a message or in the Details panel.
Chat blocks are not artifacts and are never published to the resource registry.

## 1. Goal

| Output | Where | Example |
|---|---|---|
| Small block | Inline in the message | Calculator, small chart with a slider |
| Complete document or page | Card in the message, preview in the Details panel | Full dashboard, report, long code file, CSV |
| Any block | Download from either place | `.html`, `.md`, `.svg`, `.csv`, `.json`, source file |

## 2. Baseline on `main` (evidence)

1. **Inline HTML is static by design.** #762 (closing #759) kept the
   DOMPurify sanitizer: no script, style, inputs or handlers. It also added
   explicit "unavailable" states. `orchestrator/prompts.py:145-160` calls
   `html:interactive` a legacy tag for a static preview. The same prompt tells
   the model to answer executable requests with an ordinary `html` fence and say
   it will not run. The reported dashboard answer did exactly that.
   `docs/FEATURE_MATRIX.md:84` documents the same behaviour.
2. **Durable resources allow only passive previews.** `docs/ARTIFACT_CONTINUITY.md`
   §5 (line 145) says registry resources get only passive allowlisted previews,
   with "HTML/SVG and unsupported formats get a file/text fallback". §6 requires
   #487 presentation envelopes to persist in "an explicitly encrypted
   field/table, not arbitrary message metadata".
3. **The registry is not live.** The registry and publication layer
   (`docs/RESOURCE_REGISTRY.md`, migrations 053–064) is owner-approved but
   implemented as source-only. It is not activated. `generate_document` does not
   publish through it.
4. **Generated files are narrow and short-lived.** `generate_document` supports
   only `csv`/`docx` (`orchestrator/tools/document.py:30`). Files are deleted
   after 24 h (`orchestrator/worker/jobs.py:1332`).
5. **Tool-call arguments are already persisted, encrypted, per message.**
   See `orchestrator/daemon.py:817` and `orchestrator/memory/store.py:805-806`,
   which uses the `daemon.encrypted_tool_trace` format.
6. **Later turns lose tool calls.** `build_openai_messages_from_history` replays
   only role and content (`orchestrator/daemon.py:163-173`).
7. **Tool arguments are buffered, not streamed.** The client sees a tool call
   only once it is complete (`orchestrator/tools/completion.py`).
8. **The preview frame can still reach the network.** Its CSP allows `https:`
   for images, fonts and media (`frontend/proxy.ts:59-70`).
9. **Reusable pieces exist.** These include `FileDownloadCard`, the Details →
   Outputs tab with `FilePreview`, the chat Preview → panel action, tool-call
   pairing (`frontend/lib/toolActivity.ts:112`) and the static
   `InlineArtifact` states.

## 3. Non-goals

- Account-wide catalog, versioned durable artifacts or media intake (#489).
- Registered component trees chosen by the model (#487). Option E2 below would
  reuse that track rather than duplicate it.
- Binary export (PDF/DOCX). `generate_document` remains that path.
- Blocks that call tools, read Daemon data or take actions.
- Network access from blocks. This is a requirement, not a property the
  current frame has proven; see the D6 release condition.

## 4. Proposed flow

```text
model ── render_block{kind,title,content,display,revises?} ──▶ backend
  validate kind/UTF-8/size/count/revises (same conversation + owner)
  → model gets {block_id, version, display, bytes, sha256}  (no content echo)
  persisted: arguments in encrypted tool trace; compact result; stub in content
client pairs call↔result (tool_call_id, else sha256) → BlockCard
  small html|svg + display≠panel → inline frame; otherwise card → Details panel
  Download (client Blob), Copy, Code/Preview toggle
later turn: stub visible → model calls read_block(block_id) to revise
```

## 5. Decisions

### D0 — What "interactive" means (**new; reverses or keeps #762**)

| Option | Summary | Tradeoff |
|---|---|---|
| **E1. Sandboxed executable blocks (recommended)** | `kind: html` from `render_block` may contain script. It runs only in an opaque-origin frame (`sandbox="allow-scripts"`, no `allow-same-origin`) hardened against egress (D6). Fenced HTML keeps #762's static behaviour. | Delivers the requested sliders and live charts with modest work. It deliberately reverses #762 for this tool path only. Residual risks: model logic can be wrong, a fake form can be drawn inside the frame, and a runaway script can hang the tab (mitigated in D6). |
| E2. Trusted components only | The model emits a bounded data spec. App-owned chart, table and parameter components render it, so no model code runs. | Fits #487 and the static rule. Only interactions that were declared up front are possible, a chart renderer is needed (a hand-written SVG component or a dependency approval), and the scope is much larger. |
| E3. E2 first, E1 behind "Run" | Native components for common cases; executable blocks need an explicit click. | Safest mix, with the largest build. |

### D1 — Emission (unchanged; reconfirm)

**C. A tool as the primary path; fences as the fallback.** `render_block` is
the rendered path. Under #762, fences keep the static preview and source-only
behaviour, and HTML/SVG fences gain a Download action. E1 applies to
`render_block` only: an ordinary fence never executes, whether inline, opened
in the Details panel or anywhere else, and has no Run control.

### D2 — Where block source lives (**changed: classification needed**)

| Option | Summary | Tradeoff |
|---|---|---|
| **S1. Encrypted tool trace, classified as message content (recommended)** | The source stays in the persisted tool-call arguments. A chat block is treated like the text of an assistant message, not as a #487 presentation envelope or a #489 resource. | No schema change. Encrypted in the existing trace format. Deleted with the conversation. Needs an explicit owner ruling that `ARTIFACT_CONTINUITY.md` §6 does not apply, because chat blocks hold no pinned resource references. |
| S4. Publish through the C1 registry | Blocks become versioned resources. | Blocked: the registry is not activated, and §5 forbids active HTML previews for resources. It would need a contract amendment plus activation. |
| S2. `generated_files` on disk | Same storage as csv/docx. | Plaintext at rest and deleted after 24 h. Not recommended. |

### D3 — Revisions (unchanged; reconfirm)

**R1. A stub plus `read_block`.** The persisted assistant content gains a stub
such as `[block blk_… v2 "Sales dashboard"]`. A read-only
`read_block(block_id, version?)` returns the source for the same conversation
and owner, wrapped as untrusted. `revises` creates `version + 1`. Earlier
messages keep rendering their own version.

### D4 — Placement and limits (unchanged; reconfirm)

- `display` takes `auto | inline | panel`. Inline is allowed only for
  `html`/`svg` up to 48 KB. Everything else goes to the panel. Until E1 is
  enabled (D6), only `svg` is placed inline; `html` blocks open as a card.
- Limits: 256 KB per block, 4 blocks per turn, a 120-character title.
- These limits are code constants. They are not environment variables.
- Below 768 px the panel opens as a full-screen sheet.

### D5 — Progress (unchanged; reconfirm)

**P1.** The activity line shows "Preparing…" while the tool call is still
generating. A `tool_call_progress` SSE event that reports bytes is a separate
approval.

### D6 — Sandbox (updated for D0)

- Use one frame route for inline and panel previews and drop the
  srcdoc-plus-nonce path.
- The frame CSP restricts the egress it governs: `default-src 'none'` (so no
  `connect-src`, workers or nested fetches), inline script and style only,
  `img-src data: blob:`, `font-src data:`, `frame-src 'none'` and
  `form-action 'none'`. This closes §2.8 for every preview, static ones
  included. The sandbox omits popups, forms and top navigation.
- Defence in depth, not proof: the frame document injects a guard that
  removes the WebRTC constructors from the content window, tears the content
  down if its document is replaced by anything but the frame's own write, and
  turns DNS prefetch off by header. No single directive or guard is relied on
  for navigation containment; only the browser gate below is evidence, and
  only for the routes and browser it exercises.
- Residual egress, open: a nested `about:blank` frame created by the content
  still exposes WebRTC (CSP cannot block it and the guard does not reach it),
  and DNS-prefetch suppression is not browser-verified.
- **Release condition for E1 (approved target vs current candidate).** The
  current candidate ships passive previews (svg as an image, markdown, csv,
  source views) and downloads only; `kind: html` blocks are shown as source
  and never executed, inline or in Details. Script execution is enabled only
  when browser qualification demonstrates that the residual egress above is
  closed, or the owner explicitly approves a revised risk boundary. Until then
  no UI may claim that blocks have no network access.
- Only `kind: html` from `render_block` uses the executable frame. `svg`
  renders passively as an image (no script, no subresources) inline and in
  Details.
- Once E1 is enabled, executable blocks:
  - render with visible "Generated content" chrome;
  - run automatically inline only up to 48 KB, while panel blocks run on an
    explicit click;
  - accept from the frame only a bounded `height` message and a `ready` message;
  - can be stopped with a "Stop" control that unloads the frame.
- Static fences keep the sanitizer, so #762's behaviour is unchanged.
- HTML never opens in a new tab: a `blob:` URL would inherit Daemon's origin.

### D7 — Download (unchanged)

The browser builds the download from the stored source as a `Blob` with a fixed
MIME type per kind, named `<slug>[-v<n>].<ext>`. No server file is written and
nothing expires.

## 6. Tool contract (proposed)

`render_block`:

| Field | Type | Rules |
|---|---|---|
| `kind` | `html`, `svg`, `markdown`, `code`, `csv`, `json` | Required |
| `title` | string | Required; 1–120 chars; rendered as text only |
| `content` | string | Required; UTF-8; no NUL; ≤ 256 KB; `csv`/`json` must parse |
| `language` | string | Only for `code`; allowlisted values |
| `display` | `auto`, `inline`, `panel` | Defaults to `auto`; D4 enforced |
| `revises` | string | Optional; an existing `block_id` in the same conversation and owner |

- **Result:** `{"success": true, "data": {"block_id", "version", "kind", "display", "bytes", "sha256"}}`.
- **Errors:** `too_large`, `invalid_kind`, `invalid_content`, `unknown_revision`, `turn_limit`.
- **`read_block`:** read-only and capped at 256 KB. Unknown and foreign ids
  return the same `not_found`.
- **Pairing:** a call and its result are matched by `tool_call_id`. If that is
  missing, the client uses the latest unpaired call, and only when `sha256`
  matches. Otherwise it shows a safe failure card.

## 7. Privacy and lifecycle

- **Storage and deletion:** the source lives only in the encrypted tool trace,
  and the stub holds only id, version and title. Deleting a conversation
  deletes its blocks.
- **To verify before building:**
  - memory extraction ignores tool arguments;
  - the skill evaluator's trace cap (`orchestrator/skill_evaluator.py`) bounds
    large arguments;
  - export and reset cover tool traces;
  - a trace of 4 × 256 KB fits the persistence limits.
- **Logs:** kind, bytes and outcome code only. No content or titles.

## 8. Delivery (after approval)

Each PR covers one concern:

1. Docs: approval record, `SOURCES_OF_TRUTH.md` registration, and a prompt note
   if D0 changes.
2. Frontend: shared egress-hardened frame and fence Download. This keeps the
   #762 static behaviour for fences.
3. Backend: `render_block`/`read_block`, validation, the content stub, and
   prompt guidance.
4. Frontend: `BlockCard`, inline and panel rendering, versions and download.
   The executable mode ships later, in its own PR, once the D6 release
   condition is met.
5. Feature matrix and `TECHNICAL_SPECS.md` updates alongside the PRs they
   describe.

Each PR passes the blocking gates in AGENTS.md. Browser tests must show:

- the frame cannot read parent cookies, storage or DOM;
- it cannot load `https:` resources;
- egress probe: generated content aimed at a local listener through fetch,
  XHR, WebSocket, EventSource, beacon, image, CSS, prefetch, script, worker,
  nested frame, form, popup, top navigation, self navigation and meta refresh
  produces zero hits, and the WebRTC constructors are absent. This is
  necessary for E1 but not sufficient while the D6 residual egress is open;
- until E1 is enabled, no generated script runs: ordinary `html` fences and
  `render_block` html blocks stay non-executable inline and in Details;
- `svg` blocks never reach the script frame;
- a runaway script can be stopped;
- the mobile sheet, keyboard paths and light/dark themes work.

## 9. Decisions recorded

| Decision | Choice |
|---|---|
| D0 | E1 — sandboxed executable blocks for `render_block` only (target); execution off until the D6 release condition is met; fenced HTML stays static (#762) |
| D1 | C — tool primary, fences as fallback; fences never execute (Download only) |
| D2 | S1 — encrypted tool trace; chat blocks are message content, outside `ARTIFACT_CONTINUITY.md` §6 |
| D3 | R1 — stub plus `read_block` |
| D4 | 48 KB inline, 256 KB per block, 4 per turn, code constants |
| D5 | P1 — activity status only; P2 needs separate approval |
| D6 | One frame route, egress-hardened frame (CSP, navigation teardown, WebRTC guard) with a narrowed isolation claim, Stop control, click-to-run in panel, passive SVG |
| D7 | Client-side Blob download |
