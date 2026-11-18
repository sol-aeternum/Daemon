# Temporary frontend audit exception: picomatch ReDoS (#735)

This records a temporary, narrowly scoped exception in the frontend dependency
audit gate. It is not a dependency change and does not authorize merging,
deployment or any other advisory. The machine-readable record that the gate
enforces is `frontend/audit-exceptions.json`; this document holds the
reachability evidence behind it.

## Exception

| Field | Value |
|---|---|
| Advisory | [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) / CVE-2026-112366, npm advisory source 1247710 |
| Package and range | `picomatch` `<=4.0.7`, high (CVSS 3.1 7.5, CWE-1333). No first patched version; `latest` is 4.0.7. Upstream widened the range on 2026-11-17T15:56:44Z |
| Created | 2026-11-17T21:30:00Z |
| Expires | **2026-12-01T21:30:00Z** (14 days). On and after this instant the gate fails, even with no findings |
| Owner | sol-aeternum (repository owner) |
| Tracking | [#735](https://github.com/sol-aeternum/Daemon/issues/735) |
| Removal | Remove the record and restore the plain `npm audit --audit-level=high` script when a picomatch release fixing the advisory, or an authoritative advisory correction, is available and verified, or on expiry, whichever comes first |

Installed copies when recorded: `node_modules/picomatch` 2.3.2,
`node_modules/tinyglobby/node_modules/picomatch` 4.0.5,
`node_modules/vite/node_modules/picomatch` 4.0.7 and
`node_modules/vitest/node_modules/picomatch` 4.0.5. The record lists every
affected package and node path npm reported, through `vite`, `vitest`,
`@vitejs/plugin-react`, `tailwindcss` (via `chokidar`, `micromatch`,
`fast-glob` and `sucrase`), `eslint-config-next` and
`@typescript-eslint/typescript-estree`.

## What the gate does

`npm run audit:ci` runs `node --test scripts/audit-ci.test.cjs`, then
`scripts/audit-ci.cjs`, which runs the same `npm audit --audit-level=high`
check with `--json`.
- **Visibility:** it prints the complete, unfiltered report, then every finding
  with the advisories it inherits.
- **Attribution:** npm decides per edge whether a dependent inherits a
  dependency's advisory, and its JSON names only the dependency. So a high or
  critical finding passes only when this exact advisory is the *only* high or
  critical advisory anywhere in its transitive closure, and every node path is
  one the record lists. A package or parent that also carries any other high or
  critical advisory fails, even when npm might have judged that edge
  unaffected.
- **Exactness:** the advisory must match the record's GHSA URL, package,
  range, severity and npm source id. A change that still reports it at high or
  above (a raised severity, or a range change that still covers the installed
  versions, such as a fixed release) fails closed. A change that drops it below
  the threshold or outside the installed versions leaves the record unused,
  which also fails (see below), so the record must then be removed.
- **Fails closed on:**
  - expiry, a record not yet valid, or a window over 14 days;
  - a malformed or unexpected record;
  - npm error objects, including registry or network failures, even when the
    advisory text appears in them;
  - spawn errors, signals and timeouts, and exit codes other than 0 or 1;
  - output that isn't JSON, an unknown report version or shape, metadata
    counts that disagree with the findings, or references to unreported
    packages;
  - a finding more severe than every advisory in its closure;
  - an npm exit code that disagrees with the findings;
  - a record that matched no finding. An empty audit (offline mode, omitted
    dev dependencies, a mirror answering `{}`) must not pass silently, and an
    unused record must be removed.

The audit always runs in `frontend/`, wherever the script is invoked from.

Moderate and lower findings are reported and stay below the unchanged
threshold. The offline tests cover each case. Each of the 24 safeguards was
disabled in turn, and every such mutation failed the suite.

## Reachability evidence

The ReDoS needs an attacker-supplied glob pattern, such as `+(*)` or `+(*a)`,
matched against a crafted string. Untrusted strings matched against a fixed,
trusted pattern do not trigger it. Measured on both Next's bundled copy and the
installed copy: `makeRe('+(*a)')` took 4, 8 and 123 ms for 16, 20 and
24-character strings (exponential), while Next's default `**` stayed linear at
about 0.2 ms on a 100,000-character crafted path.

**The deployed image keeps build and dev dependencies.** The frontend
Dockerfile runs `npm ci` with dev dependencies and serves `npm run start`
(`next start`); Compose does not override the command. So the analysis covers
the running server, not package classification.

- **Production server, dynamic:** a production build was served with
  `next start` under a module-load trace covering every glob-matcher package
  (picomatch, micromatch, minimatch, anymatch, tinyglobby, fdir, chokidar,
  readdirp, fast-glob, playwright, prettier, rollup, vite, vitest, tailwindcss).
  Requests covered pages, unknown routes, API routes, the service worker, a
  real `/_next/image` optimization, and hostile glob-shaped URLs and paths up to
  5,000 characters.
  - The only matcher module loaded was Next's bundled
    `next/dist/compiled/picomatch`.
  - The only pattern it compiled was `**` (Next's default image
    `localPatterns`).
  - None of the npm-installed copies was loaded. Hostile inputs were answered in
    milliseconds, and glob-shaped image URLs were refused (400) before any
    matching.
- **Production server, static:**
  - In Next's server code, only the image optimizer
    (`shared/lib/match-local-pattern.js` and `match-remote-pattern.js`) and the
    config loader use the bundled picomatch. They compile patterns from
    configuration, and the request only supplies the string.
  - `frontend/next.config.mjs` sets no `images` patterns. The built
    `images-manifest.json` has no remote patterns, and the default local
    pattern is `**`.
  - Next's bundled `glob` is used only by `next build`.
  - The application's own code imports no matcher package.
- **Browser and service worker:** no picomatch code appears in `.next/static`
  or `public/sw.js`.
- **Build, lint and test:** `tailwindcss`, `vite`, `vitest`, `eslint` and
  `@typescript-eslint` match repository-authored globs (`tailwind.config.ts`
  content, test include patterns, lint configuration) during `next build`,
  linting and tests. Anyone able to change those patterns can already change
  the code being built.

## Limitations and residual risk

- **Coverage:** the dynamic trace covered a representative route set, not every
  route or state; static search covers the remainder. A future feature that
  passes user-controlled text to any matcher as a pattern would invalidate this
  analysis. The gate's path check catches a new npm dependency path, but not
  new application code using an already-installed copy.
- **Copies npm cannot see:** bundled copies inside `next`, `playwright`,
  `playwright-core`, `prettier` and `rollup` are invisible to `npm audit`. The
  exception neither hides nor covers them. Next's bundled copy predates the
  upstream `analyzeRepeatedExtglob` guard and is reachable only with
  configuration patterns.
- **Vulnerable versions stay installed:** the exception does not remove the
  vulnerable versions from the image; it accepts them for 14 days on the
  evidence above.
- **Per-edge decisions:** npm's per-edge inheritance decisions are not in its
  JSON. The gate therefore attributes conservatively and fails rather than
  guessing.
