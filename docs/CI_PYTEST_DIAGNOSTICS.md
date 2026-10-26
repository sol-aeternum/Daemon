> Hosted update: the dated blocked decision below records the prior local-only
> contract. The owner-approved hosted-only adapter at the end supersedes its
> no-route status, not its trust limits or qualification/release gates.

# Local backend pytest ownership and diagnostics

Local caller/context integration applied after independent PRECHANGE source
review. Actual qualification and final applied-state review are tracked separately;
source review alone is not release or hosted qualification. This is not a new
product feature or a replacement CI framework.

## Exact local route

Only the pytest command row in `scripts/local_ci.sh` changes, to (its environment
default and corresponding comments also align to `.venv`, as described below):

```sh
python -I -S -B scripts/run_managed_backend_pytest.py -q
```

The stdlib caller resolves its actual checkout and ONE fixed-layout existing
ownership manifest. It requires manifest source equality and W/R/E identities,
active expected owner/nonce, and a real selected checkout-local venv. Relative
`UV_PROJECT_ENVIRONMENT` is relative to the repository; borrowed/symlinked venv
roots are refused. A normal `bin/python` symlink to system Python is allowed.
The ordinary backend caller additionally requires the selected environment to
be the same canonical `<repository>/.venv` used by both basedpyright selectors.
An explicit different real local venv is refused with a type-environment
conflict before lifecycle launch; an override does not redirect type analysis.
No helper import, shell-export eval, task creation/adoption, registration scan,
default raw-pytest fallback or new ambient context variable is provided.

The caller waits for the existing installed lifecycle executable's array argv:

```text
<installed-opencode-work> run <existing-task> -- <local-venv>/bin/python -I -B
  <actual-source>/tests/backend_pytest_supervisor.py
  --manager --context <one-canonical-JSON-argument> -- -q
```

This is a SEPARATE managed pytest invocation. Its original scope_job process
execs the local interpreter/supervisor directly, with the ready PID unchanged;
the enclosing CI shell is orchestration only. The manager is pytest-only, not an
arbitrary-command executor. It launches
`<same-local-python> -m tests.backend_pytest_client -q`, with no extra
selection/filter/skip. The client entry unconditionally authenticates and consumes
the existing hello through canonical `tests.backend_pytest_supervisor` before
importing pytest or entering collection; pytest then reuses that module's cache.
Missing, malformed, reset or EOF handoff aborts before pytest entry, without a
success conversion. Manager reset, retained-work, reap and completion checks are
unchanged. Nonpytest commands and CI aggregation are unchanged.

The 2026-10-23 targeted attempt reported 65 passing test items but outer/managed
exit 1 with a private-channel reset. Its selected source paths did not consume
the real hello; the historical stderr did not identify the failing syscall.
Eager consumption addresses that unread-hello path, not proof of the past syscall
or new host qualification. PRECHANGE review and affected execution remain required.
The caller preserves the lifecycle status; local_ci reports the actual gate
status and returns 1 if ANY blocking gate fails. Overall 0 needs every blocking
backend gate, not just pytest. The caller maps a signal to 128+signal only when
its direct lifecycle subprocess is signalled. It does not remap every pytest-client
status; a negative client returncode passed through SystemExit retains the existing
POSIX low-eight-bit exit behavior and remains nonzero.

One frozen canonical JSON context contains task, expected owner/nonce, source,
resource/evidence/manifest paths and local interpreter; maximum 8192 ASCII bytes.
CLI syntax is exactly `--manager --context JSON -- pytestargs`, with no absent
context fallback. The manager independently verifies all previous private-record,
no-follow, W/R/E, launching-scope and original-ready-PID predicates PLUS actual
supervisor/registered/context source equality and actual local interpreter before
client/domain/process effects. The same canonical context and SHA256 digest pass
ONLY in existing private setup/hello frames. The worker echoes its digest before
launch. Peer UID/PID, parent/scope, nonce, sequence and deadline checks remain.
CHANNEL is a locator, not context or authority. No run request precedes hello
validation. Imports only define functions/models; they do not read registration.

Fixture runner argv/environment, GNUtimeout fractional behavior, sole reaper,
held original FDs, direct/adopted waits/ECHILD, bothEOFs, empty leaf and original
normal deadline remain unchanged. D1 end is still the minimum of detection+10
and original request.deadline+10; rescued failure never becomes success. Unknown
work/handles/pins remain retained. Trusted coordinated same-UID/non-migrating
workload is a limit, NOT kernel exclusion of external deliberate/buggy migration.

## Verified failure versus UNKNOWN retention

The manager records a new invocation as unverified before fallible worker setup.
Worker exit (including an absent or unexpectedly exited worker) is not proof of
workload closure. Original handles remain pinned for any unverified retained job;
there is no automatic second rescue, numeric signaling or inferred cleanup.

A normal result requires all five closure facts, a normally reaped supervisor and
a checked empty held domain. An accounted failure requires the exact no-child
receipt or complete rescue facts, supervisor failure exit and a checked empty
held domain. It still returns nonzero. Verified accounting precedes client result
delivery, so a delivery error fails the suite without creating an unresolved pin
for an already-accounted workload. A lost or incomplete receipt remains UNKNOWN.

`tests/test_backend_pytest_retention.py` imports the actual module with external
effects stubbed. It checks release-marker transitions and manager retention, not
live process reaping, ECHILD, stream EOF or kernel cgroup ownership. Real managed
qualification and final assembled-source review remain separate required gates.

## Future acceptance DATA, not a command executed by source preparation

In an admitted serialized existing local task, the normal managed outer argv is:

```text
<installed-opencode-work> run <existing-task> -- /usr/bin/env
  UV_PROJECT_ENVIRONMENT=<actual-source>/.venv
  /usr/bin/bash scripts/local_ci.sh backend
```

The outer scope holds orchestration; the pytest adapter requests a second fresh
scope for the SOLE active pytest workload. No other workload/job/writer may run
against this serialized acceptance window. A launching outer scope is not an
unknown exit and is not a second pytest workload; nothing clears unknown history
or permits unrelated jobs. Resource/ownership guards apply to BOTH invocations.
No completed qualification job/test is rerun merely for readiness.

## Exact environment decision: FOUR selectors aligned

The qualified existing local environment is `.venv`. The reviewed source change
aligns precisely these FOUR selectors, each `.uv-venv` -> `.venv` (original locations):

1. `pyrightconfig.json:3`, `venv` (JSON selector).
2. `pyproject.toml:61`, `[tool.basedpyright].venv` (duplicate TOML selector).
3. `scripts/local_ci.sh:36`, `UV_PROJECT_ENVIRONMENT` default.
4. `.github/workflows/ci.yml:21`, backend `UV_PROJECT_ENVIRONMENT`.

Only corresponding explanatory comments also change. TypeCheckingMode, baseline,
venvPath, `--level error`, lint scope, commands/aggregation and mandatory gates
remain intact; no unrelated F configuration is copied. Neither dependencies nor
lock change. No solve/install/resync/new environment/symlink or runtime env-name
surface is introduced. These are checkout source/config changes only; no hosted
runner environment or deployment is changed by applying them. Environment-path
alignment is NOT weakening a type gate.

The prechange `tests/test_ci_workflow_gating.py:118-170` pinned the old defaults and
copied only local_ci. Its focused R2 adaptation changes those two expected
defaults, supplies a fictional adapter observer alongside its uv observer, and
retains status/aggregation/export assertions without running any real gate. A
new source/config test binds ALL FOUR selectors plus unchanged type/lint policy.
The ordinary adapter's focused negative refuses a real but incompatible override
before lifecycle run. Test execution remains a separate acceptance gate. Historical `.uv-venv` audit logs
are dated evidence, not active selectors; they remain unchanged.

This removes the PREVIOUS proposal's self-created environment-name blocker from
the coherent local candidate. Future acceptance can use the already qualified
`.venv`, without provisioning `.uv-venv`. No claim that every backend gate is
green follows from source review: actual final blocking gates
remain required. Explicit override remains exported honestly by local_ci; the
adapter accepts only canonical `.venv` equivalence for this full-backend route,
otherwise refuses. UV_PROJECT_ENVIRONMENT never silently redirects basedpyright.

## Positive Python source boundary

Both basedpyright configurations list the maintained runtime, test and tool
roots, including the legacy `backend`, `config`, `council`, `db` modules,
frontend Python fixtures and the two root-level Python tests. This retains
checking beyond the packaged `orchestrator*` modules. It is not an error
suppression, a changed baseline or a type-checking-mode reduction.

The only current untracked Python files outside this boundary are three named
BC14 historical transaction evidence copies. Their original bytes and earlier
nine-error diagnostic remain preserved; they are neither relocated nor repaired
to make a product gate pass. `tests/test_type_source_boundary.py` asserts that
all tracked Python files and all new untracked source except those exact
non-shipped evidence paths remain covered. Accidentally tracking that evidence
or adding source under an unclassified root fails the regression and requires
explicit source-boundary reconciliation. No directory-wide ignore is added.

## Exact hosted decision: BLOCKED; no authorized route in retained workflow

### Retained-contract classification: local qualification versus publication

Hosted execution is NOT a prerequisite for this finite source preparation or
registered-local qualification: Astra's binding local decision9-17 selects this
local interface, and19-25 expressly separates hosted capability/publication from
continued local preparation. Prior accepted direct/native local evidence is not
reclassified as incomplete merely because no hosted run occurred. No hosted
probe, test or stronger containment gate is added to local acceptance here.

This does NOT waive the real publication contract: repository `AGENTS.md:21-33,
128-129` requires the test gate and protected `Backend gates` before merge;
`.github/workflows/ci.yml:13-16,57-58` defines ubuntu-latest/rawpytest, and
`tests/test_ci_workflow_gating.py:22-30,77-99` keeps Pytest present/blocking.
The prechange C source already required original handoff: original
`tests/backend_pytest_supervisor.py:730-738` refuses missing CHANNEL, and actual
`tests/test_backend_pytest_runner.py:301-313,395-397` invokes it under ordinary
Linux discovery. There is no producer in the retained raw hosted path. R2's
workflow change is ONLY env-name/comments; it does not newly introduce that
pre-existing C working-state mismatch or demonstrate a new hosted regression.
This is source-backed incompatibility evidence, NOT an observed hosted run or
a claim that published baseline CI was ever green. The larger C publication
change still must resolve the real hosted gate; local success cannot clear it.

A NEW demand for hostile-sameUID exclusion, kernel lifetime isolation or root
containment would be unsupported extra scope. The retained fixture contract is
cooperative original ownership/exec, genuine reaps/ECHILD+bothEOF+empty leaf and
retained UNKNOWN; supervisor doc9-14 and Astra hosted decision21-25 preserve
that boundary. H1 below requires only an evidenced equivalent of THAT contract,
not new privileges or proof all externally migrated processes are excluded.

The actual `.github/workflows/ci.yml:13-58` provisions uv/dependencies and raw pytest. It has
no evidenced existing registration/launching scope/ready-PID producer or writable
delegated cgroup parent. The selected local fixed-layout adapter deliberately
refuses an arbitrary hosted checkout; passing literals or inventing a manifest
would fabricate the bootstrap proof. Kernel feature availability is unverified,
not asserted absent. The workflow change is only the environment selector/comments,
not the raw hosted pytest route.

Required capability is an EXISTING runner-owned bootstrap which holds the
designated writable parent, binds original exec identity/fresh invocation,
retains unknown work/handles with genuine reap/EOF semantics, and supplies this
bounded context. NONE is identified in the retained workflow. This finite
proposal therefore offers NO hosted execution route and no merge/release around
the required hosted check. Root service, sudo, Delegate, new registry/framework,
credentials, runner-label changes, skipping fixtures or weakening identity are
not authorized substitutes.

Finite resolution is **hosted publication BLOCKED on capability H1 and adapter H2**:

- **H1, runner-owned capability is UNEVIDENCED, not known absent.** Required
  read-only evidence must name an ALREADY supported runner execution interface
  and show its owned writable parent, original exec/ready-PID binding, fresh
  invocation/context production, and unresolved-work/handle retention semantics.
  Retained `.github` workflows, `scripts/` and relevant docs identify NO such
  interface for this backend job. Local installed lifecycle source is not proof
  that an Ubuntu hosted job has it. No host/remote probe was performed.
- **H2, repository hosted adapter is MISSING.** Even if H1 is evidenced, the
  local fixed-layout resolver cannot map an arbitrary checkout to that bootstrap.
  A separately scoped/pre-reviewed repository bridge would need to consume H1's
  genuine bounded context and execute this pytest-only supervisor as its original
  bootstrap exec. It must not manufacture registration/ready PID or weaken the
  local proof. No bridge implementation/command is invented in this proposal.

Exact necessary PRIMARY decision: retain this named blocker, OR authorize only
read-only acquisition of H1's existing interface/evidence and, IF sufficient,
the H2 bridge design/source preparation plus one corresponding hosted Pytest
step interface change. That change would REPLACE the raw `PYTHONPATH=. uv run
pytest -q` launch WITH the evidenced runner-owned original-exec bridge carrying
the bounded canonical context and unchanged full `-q` selection/status. An exact
working command cannot be specified honestly before H1 identifies its supported
interface; neither an environment flag nor this local command fabricates H1.
If H1 has no supported existing route, the authorized solution set is EMPTY:
report this blocker rather than provision a new authority or request broad OS
approval. No rootbroker/sudo/Delegate/runner-label/install/newregistry/framework,
skips, fake manifests or weakening gates is an alternative in this scope.

Astra permits local registered-checkout preparation and holds hosted bootstrap
changes. The new parent exception covers ONLY the environment-name selector/
comments, NOT H1/H2. Thus this is an exact unavoidable capability/authority
decision resolved inside this proposal, not an indefinite TODO. Neither phase's
review/access grant authorizes H1 evidence acquisition, H2 implementation or
workflow/bootstrap/security/environment deployment. No hosted-green promise.

## Evidence/review boundary

Prior accepted47/native install/D1/rescue receipts qualify their exact unchanged
sources only. New caller/context negatives and mechanically adapted model/probe
sources require their own affected acceptance evidence. The explicit rescue probe remains outside default
discovery and retains expected assertion-pass WITH manager/scope1 semantics.
PRECHANGE inspection and later final applied-code/acceptance-evidence review are
distinct bounded phases in one access proposal. Neither review/access nor future
execution/application is granted by this document; future receipt hashes do not
exist yet. Unchanged source reads may be reused only with exact hash equality and
separately admitted final-phase scope. Whole C remains partially verified.


## Approved hosted-only transient delegation adapter

The fixed `uv run python scripts/run_hosted_backend_pytest.py` entry selects three
serial fresh services: positive60s, accounted-rescue60s and full unfiltered suite1200s.
The unchanged25min step/30min job, all quality gates and15s failure-termination
grace remain. There is no retry, fourth service, local sudo execution or fallback.
Only the exact fresh-unit `sudo -n -- systemd-run` recipe is privileged. Test code
runs as the captured nonroot user/groups, with NNP and no capabilities; actual
installed systemd>=254, cgroupv2, credentials, namespaces, ownership and recipe
readback must agree before startup release. Unsupported capabilities fail closed.

The original manager remains in its exact control subgroup; its direct client and
work use the fresh owned payload subgroup. Hosted-tagged authentication checks
exact roles, parent/peer identity, private nonce and digest, not same-unit prefixes.
Local eight-field InvocationContext, local registration and local scripts remain
separate. Client pipes are continuously drained as exact bytes, independently of
outer manager streams. Each invocation retains five completion facts. The hosted
top-level gate also requires actual direct client reap, ECHILD/no unexpected child,
empty held payload/domain reads and both client EOFs while the original manager
lives. Outer independently verifies that proof before one receipt-bound final ACK.
Manager then exits directly without more work; outer additionally requires its
original pidfd death, actual direct sudo reap, propagated systemd-run status,
matching terminal InvocationID/ExecMain status/result and both outer EOFs.

Empty observations precede controlled manager exit; a pruned cgroup is never
treated as empty. Missing terminal unit metadata, identity, ACK, EOF, JUnit or log
proof is failed/UNKNOWN. Accounted-rescue manager exit1 remains failure evidence,
qualifying only its explicitly designated negative phase. Rescue/ACK/draining fit
inside the original phase deadline; termination grace cannot create success.
No keeper, observer process, extra unit, group/capability change, persistent setup,
privileged cleanup, root repository code or registration fabrication is permitted.

`pytest-diagnostics/hosted-closure.json` is the only additional uploaded metadata
file. It contains explicit sanitized identities/statuses/proof summaries, never
raw context, environment, argv or tokens. The original pytest.log/JUnit remain;
the pinned uploader retains these three explicit paths for seven days. Private
0700 context/evidence roots and0600 context files stay outside the upload list.
The trusted-same-UID/non-migrating assumption is unchanged: this is not isolation
from malicious peers or Docker authority. Source/synthetic tests do not qualify
actual runner capabilities; accepted source reviews, full required local CI and
the one bounded hosted run remain required. #454 is not declared resolved by
design acceptance or by the positive probe alone; no merge/release gate is waived.
