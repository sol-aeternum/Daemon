# Temporary maintained braces derivative (#418)

This is an internal dependency remediation, not a new product capability or an
official upstream release. It enables unchanged frontend gates to evaluate the
speech compatibility work in [#409](https://github.com/sol-aeternum/Daemon/pull/409).
It does not authorize merging, deployment, registry publication or new credentials.

## Lineage and exact scope

`frontend/vendor/braces-provenance.json` maps the published MIT-licensed
`braces@3.0.3` tarball, its registry integrity, immutable release-test commit,
upstream PR #72 head, the minimal five-file backport and packaged file hashes.
Readable source, the original archive, the patch, unchanged release tests and
the reproducible derivative archive are committed under `frontend/vendor/`.

The private, unpublished `@daemon-internal/braces@3.0.3-daemon.1` retains the
original author's MIT attribution. This scope is only a local label; no public
registry namespace ownership is asserted. Source code is the independently
reviewed candidate: metadata and a prominent derivative notice are the only
packaging changes. The upstream project does not maintain or endorse our fork.

The depth guard addresses **GHSA-vfj7-8cjw-p6xm / CVE-2026-93687**: string parsing
and recursive AST walkers have a hard ceiling of 100 nested levels, with stricter
`maxDepth` supported. String input is rejected by `SyntaxError`; over-depth direct
AST input by `RangeError`. Fractional options retain upstream PR semantics (parser
and walker boundaries differ); they are tested, not normalized by this backport.
This is not a claim to fix every expansion resource bomb, arbitrary parent-pointer
cycles, or all possible malformed AST inputs. The upstream PR's unrelated parse
and stringify fixes are deliberately excluded from the reviewed minimal patch.

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
- bounded depth, option, literal/malformed input and direct-AST regressions against
  the actually installed module, plus real micromatch/fast-glob/chokidar fixtures;
- original-source baseline compatibility and all 764 unchanged release tests;
- negative fixtures: reverted security code (even with regenerated provenance and
  the private identity), corrupt archive, installed/readable source drift, weakened
  test bytes, missing lock integrity and nested unpatched consumer resolution.

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
latest release, exact PR head/status and GitHub advisory metadata affecting the
original version. Any change or lookup failure fails visibly for reassessment;
it never removes the fork, weakens a gate, publishes, writes issues or changes
dependencies automatically. No new secrets or write permissions are needed.
The maintainer must enable failure notifications and review failures promptly.
Scheduled workflows run only after this workflow is on the default branch;
until then run `npm run security:braces:watch` manually. Hosting outages, disabled
workflows, notification settings and advisory databases remain limits; neither
this watch nor name-based scans guarantee discovery of all derivative risks.

On an upstream advisory or PR change, inspect actual affected paths and amend or
block use based on evidence. Never silently accept new metadata as clearance.
On an official fixed release, compare coverage, test the normal dependency graph
and full gates, obtain review and then remove the local override/direct dependency
and vendored packaging in a separate change. Keep useful exploit regressions and
historical lineage. A scanner withdrawal alone is not an instruction to revert
the patch. A broad toolchain migration is a separate owner decision.
