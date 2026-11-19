# Temporary maintained braces derivative (#418)

This is an internal dependency remediation, not a new product capability or an
official upstream release. It enables unchanged frontend gates to evaluate the
speech compatibility work in [#409](https://github.com/sol-aeternum/Daemon/pull/409).
It does not authorize merging, deployment, registry publication or new credentials.

## Lineage and exact scope

`frontend/vendor/braces-provenance.json` maps the published MIT-licensed
`braces@3.0.3` tarball, its registry integrity, immutable release-test commit,
upstream PR #72 base head and selectively adopted commits, the minimal five-file
backport and packaged file hashes. The watched final head is recorded separately.
Readable source, the original archive, the patch, unchanged release tests and
the reproducible derivative archive are committed under `frontend/vendor/`.

The private, unpublished `@daemon-internal/braces@3.0.3-daemon.2` retains the
original author's MIT attribution. This scope is only a local label; no public
registry namespace ownership is asserted. Source code is the independently
reviewed candidate: metadata and a prominent derivative notice are the only
packaging changes. The upstream project does not maintain or endorse our fork.

The depth guard addresses **GHSA-vfj7-8cjw-p6xm / CVE-2026-93687**: string parsing
and recursive AST walkers have a hard ceiling of 100 nested levels, with stricter
`maxDepth` supported. String input is rejected by `SyntaxError`; over-depth direct
AST input by `RangeError`.

`3.0.3-daemon.2` selectively backports two further PR #72 fixes on top of the
daemon.1 depth guard; it is not a wholesale adoption of the closed PR:

- **Fractional `maxDepth` parsing** (upstream commit `2569eada`): the parser now
  enforces `nesting + 1 > maxDepth` at both `(` and `{`, so fractional options
  below 1 reject the first nesting level with `SyntaxError` and `1.5` admits
  exactly one level. Exact integer boundaries (100 accepted / 101 rejected) and
  stricter integer limits are unchanged. Direct AST input to the walkers still
  rejects fractional over-depth with `RangeError`, independent of the parser;
  string input through `compile`/`expand`/`stringify` reaches the parser first,
  so the composite error is the parser's `SyntaxError`. Unrelated quote-handling
  and comma invalid-flag changes from the initial PR remain excluded.
- **`expand()` parent-chain cycles** (upstream commit `f7ba960d`, adopted
  verbatim for `lib/expand.js`): both upward parent traversals use a shared
  `queueOwner` helper that detects revisited nodes with a `Set` and throws
  `RangeError('AST parent chain contains a cycle')`, terminating at brace, root
  or parentless nodes as before. For a stable AST the second ancestor lookup
  executes before the recursive first child loop, so the same cycle may be
  caught earlier; both call sites are guarded, and dedicated helper unit tests
  (VM-extracted, no production exports) plus call-site inspection cover them.
  No fixed hop or resource bound is claimed for long acyclic parent chains.
  `stringify` retains the original 3.0.3 undefined-parent recursion; that is
  not evidence of adopting the later upstream `e072fe4` stringify change.

This is not a claim to fix every expansion resource bomb, cycles in walkers
that do not traverse parents, or all possible malformed AST inputs. The
upstream PR's unrelated parse and stringify fixes are deliberately excluded
from the reviewed minimal patch.

## Resolution and independent admission

The root `braces` dependency uses a committed relative local tarball, and npm's
`"braces": "$braces"` override directs every consumer to it. npm generates the
lockfile's archive SHA512 integrity. A bare transitive relative `file:` override
was rejected after scratch testing resolved it relative to a consumer directory.
There are no install hooks, moving Git dependencies, handwritten lock changes or
modifications to a globally installed package. Docker copies the vendor input
before `npm ci` and validates installed bytes before building.

**The new identity will not inherit the upstream package's name-based npm advisory
matches. An audit pass alone does NOT prove this vulnerability is repaired.** The
original full `npm audit --audit-level=high` gate remains unchanged. A mandatory
additive `npm run security:braces` gate in CI and `scripts/local_ci.sh` verifies:

- original tarball integrity and unchanged MIT attribution;
- exact independently reviewed fixed-code hashes, readable source/archive equality,
  the private manifest, patch lineage and relative, integrity-pinned lock resolution;
- installed bytes and the actual dependency graph, rejecting original/nested
  unpatched copies and checking each brace consumer and fast-glob path;
- bounded depth, option, fractional-option, parent-cycle, literal/malformed input
  and direct-AST regressions against the actually installed module, plus real
  micromatch/fast-glob/chokidar fixtures;
- original-source baseline compatibility and all 764 unchanged release tests;
- negative fixtures: reverted security code as a whole and selectively per fix
  (fractional parser guard, parent-cycle guard — each even with regenerated
  provenance and the private identity), corrupt archive, installed/readable
  source drift, weakened test bytes, missing lock integrity and nested unpatched
  consumer resolution.

The depth/consumer child has a 10-second deadline and 64 MiB JS heap; fixture paths
contain only fictional files, with watchers closed and only owned fixtures removed.
Mocha/bash-path/fill-range test tools have their own exact manifest and npm lock,
installed with scripts disabled and independently audited at high severity as a
blocking part of the additive gate. Test resolution substitution is confined to
Mocha/bash-path tooling, never application braces resolution. Preserved third-party
source/tests are excluded only from application ESLint/Prettier reformatting;
security/hash checks are mandatory and unchanged app rules remain in force.

## Reproduce and update

From `frontend/`:

```sh
npm ci
npm run security:braces
npm run audit:ci
npm run security:braces:watch
```

The archive can be reconstructed using `npm pack --ignore-scripts` in
`frontend/vendor/braces/`, with `--pack-destination ..`. Verify SHA256/SHA512 against
provenance and the package lock before adoption; never accept a freshly packed
archive merely because it has the same name. Source changes need security review,
new reviewed hashes, a truthful new derivative version, reproducible packing and
package-manager lock generation. Do not update fixed hashes to make a failing
gate pass without reviewing the underlying code. The original source plus the
zero-fuzz patch reconstructs all code; package metadata/README changes are explicit.

## Owner, watch and retirement

Daemon's repository maintainer (sol-aeternum) owns this temporary fork, including
new upstream advisory triage, compatibility and release review. Issue
[#418](https://github.com/sol-aeternum/Daemon/issues/418) is the tracking record;
[upstream PR #72](https://github.com/micromatch/braces/pull/72) and
[release issue #73](https://github.com/micromatch/braces/issues/73) track replacement.

The daily read-only `Vendored braces upstream watch` workflow checks the upstream
latest release identity (version, shasum and integrity), the exact PR #72 head,
state and merge flag, and GitHub advisory metadata affecting the original
version. The reviewed baseline recorded in `braces-provenance.json` — currently
release `3.0.3`, PR head `28d440b5` closed and unmerged, and the single
`GHSA-vfj7-8cjw-p6xm` advisory — is a change-detection record, not a security
guarantee, and is never silently refreshed: any change, missing or incomplete
field, full advisory page, HTTP error, network failure, timeout or invalid JSON
fails visibly for reassessment. Offline fixture tests for the watcher run as a
blocking part of `npm run security:braces`, exercising the accepted baseline and
each change/error separately with injected fetch; the scheduled watch itself
never removes the fork, weakens a gate, publishes, writes issues or changes
dependencies automatically. No new secrets or write permissions are needed.
The maintainer must enable failure notifications and review failures promptly.
Scheduled workflows run only after this workflow is on the default branch;
until then run `npm run security:braces:watch` manually. Hosting outages, disabled
workflows, notification settings and advisory databases remain limits; neither
this watch nor name-based scans guarantee discovery of all derivative risks.
A changed baseline never clears the derivative by itself: re-verify the actual
adopted commits and code bytes before any metadata-only acceptance.

On an upstream advisory or PR change, inspect actual affected paths and amend or
block use based on evidence. Never silently accept new metadata as clearance.
On an official fixed release, compare coverage, test the normal dependency graph
and full gates, obtain review and then remove the local override/direct dependency
and vendored packaging in a separate change. Keep useful exploit regressions and
historical lineage. A scanner withdrawal alone is not an instruction to revert
the patch. A broad toolchain migration is a separate owner decision.

## Exposure boundaries and retirement candidates

These checks alone validate the repository declaration, the clean-installed dependency
graph and vendored bytes. They do not verify a runtime request path, deployed
container bytes or production exposure: the Docker build path copies vendor
inputs before `npm ci` and validates installed bytes, but what actually runs in
a deployment is verified separately, if at all. Record deployment unknowns
honestly when reporting status.

Retirement review (no migration implemented here): the braces consumers split
into independent upgrade paths — the Tailwind side (its own `fast-glob`/
`micromatch` resolution) and the Next ESLint side resolve separately, so a
future upstream fixed release or replacement can be adopted per path. Each
path needs its own compatibility check and gate run; removing the Tailwind 3
path alone does not remove the Next ESLint path. The global override must stay
until every remaining consumer has a reviewed compatible replacement.

Read-only deployment observation on 2026-10-05: the running frontend image
`sha256:ea2dc2e104c00246c88e98864a9bac6fbe0c6bfa4a94c8dd15ffe4828359dd48`
contained `@daemon-internal/braces@3.0.3-daemon.1`, with installed `lib/expand.js`
SHA256 `2974d5b8763a358d81dfa5b4b804329f525239f34429c396b93a540219504809`;
micromatch, chokidar and fast-glob were also installed. No direct imports of these
packages were found in application JS/TS source, but dynamic and transitive paths
are not ruled out. This establishes installed/deployed old derivative bytes, not
a remotely exploitable request path, and does not deploy or certify daemon.2.
