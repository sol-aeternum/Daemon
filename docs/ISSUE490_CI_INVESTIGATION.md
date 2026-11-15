# #490: one hosted production-inventory investigation

This draft uses published `main` as its baseline. It deliberately excludes local
memory-card repair `669720ae` and development HMR repair `d00383b0`. It is **not**
the combined development → fresh build → production qualifier. The separate
default-development DevTools CSP problem is not repaired or exempted here.

## Budget and admission

The existing browser-inventory job recognizes the exact same-repository branch
`investigate/490-ci-fps-20261115`. Its experiment runs only on the PR's `opened`
event, first run attempt. Later updates/reopening/reruns do not run it. Ordinary
PR/main inventory behavior outside that branch is retained. This event guard is
not a durable replay ledger: open **one** draft PR, never recreate/rerun it to get
a pass. Any interrupted experiment consumes its authorization.

The existing public hosted runner incurs no new provisioning. Record actual CPU
model/count/affinity, ancestor CPU/memory limits and readable CPU pressure before
admitting the browser. Fewer than four effective CPUs or unavailable critical
telemetry blocks the experiment. Four workers can saturate even a four-CPU VM;
capacity is not proof that a subsequent failure is application work.

After locked dependency installation and a fresh API-origin-3181 build, run:

```sh
npm run test:browser -- --config=playwright.production.config.ts --workers=4 --reporter=./e2e/issue490-reporter.ts --output=test-results/issue490-browser --update-snapshots=none
```

`CI=1` retains the existing retry. The frozen 69-case manifest is derived from the
retained production inventory; the reporter/finalizer checks exact identities
and attempts, not merely a count. Assertions, per-test deadlines, screenshot
references/tolerances, CSP and retain-on-failure tracing remain unchanged.
Existing memory-card or unpatched preview-fixture failures remain visible.

## Data and isolation

Use only owner-controlled reviewed same-repository code and locked dependencies,
not arbitrary fork PRs. Checkout the event head SHA without persisted git
credentials. The job has contents-read permission and no secret expressions.
Install/build/test children receive an explicit non-secret tooling environment,
not GitHub/provider credentials. This reduces exposure; it is **not** an OS
sandbox against hostile code. Platform actions still use GitHub credentials.

Raw build/browser output, screenshots and failure traces remain in separate
ephemeral VM paths. They are not uploaded. The custom reporter emits a private
candidate containing only numeric/status fields and frozen case indices. The
controller rejects unknown fields, invalid paths/numbers and oversized data,
then reconstructs public results using static source identities. The upload
allowlist is exactly `runner.json`, `results.json` and `host.jsonl` from the
finalized public directory; completion is marked only after final writes.
No browser console, errors/stacks, URL/query, headers, cookies, DOM, HAR,
screenshots, raw trace, environment file or compiled source is exported.
Unrelated CI jobs' existing artifacts are not sanitized by this experiment.

## Interpretation

Record exact checked-out SHA, build ID/hashes, command, runtime versions, process
exits and per-case attempts. Aggregate CPU ticks and CPU/memory/I/O pressure are
sampled at 2 Hz. Numeric Node-side brackets around the original scroll evaluation
include RPC overhead: they are approximate scroll windows, not frame-level CPU
attribution. Counter resets or incomplete coverage are unknown, not zero load.

Retained local measurements at `669720ae` were 19.82/24.61 FPS in the normal
qualification, and 17.84 FPS at four workers versus 48.40 FPS at one worker in a
sequential profiled pair. Host CPU activity/stall pressure differed substantially.
That supports contention as a contributor, not a unique host-only cause. This
fresh baseline excludes the memory-card change and cannot be described as a
byte-identical rebuild of the prior candidate. Old rendering event wall-time
lower bounds are not CPU residency or complete rendering-pipeline totals.

The job remains nonblocking inventory; inspect actual outcomes and artifact
integrity rather than an overall green badge. Missing/skipped/interrupted cases,
collector errors and runner suitability are distinct from test failures. Keep
#490 open unless its actual criteria are met. Neither a four-worker desktop pass
nor the old one-worker diagnostic establishes physical-phone acceptance. No
merge, deployment, paid inference or paid runner provisioning is authorized.
