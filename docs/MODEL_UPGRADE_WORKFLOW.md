# Routine model upgrade evaluations

## Authority

The operator approved this narrow reusable workflow on 30 September 2026 while
requesting a Sol 6.1 comparison. It implements DEC11's replaceable-model direction
using the existing qualified dispatch and account ledger (AC07/AC12). It does not
change commercial plans, provider policy requirements or the Z-only runtime.

An experiment manifest describes a proposed comparison. Operator approval of its
models, settings, routes, funded period and spending envelope is still required.
Only the independently reviewed inference policy can qualify a serving endpoint.
Evaluation results never activate a model or deploy a service automatically.

## One workflow per release

1. **Qualify.** Capture dated exact-model endpoint, ZDR, provider-policy, price,
   limits and parameter evidence. Reconfirm the applicable account attestation.
   Use an evaluation-only inference policy and a catalog with empty presets, so
   the compared settings are explicit. Pin one provider and disable fallback.
2. **Freeze.** Add a manifest under `tests/fixtures/model_upgrades/` naming exact
   models, candidate labels, route IDs, provider pins, corpus digest, conditions,
   repetitions, probes and screening criteria. Change the experiment identity
   for every independently approved run; never adopt a previous experiment's
   completed or interrupted state.
3. **Preflight.** Independently test/review the runner and use `--dry-run` against
   the marked isolated database/account. Check all scheduled settings, whole-run
   planning bounds, the authoritative ledger and the funded-month boundary.
4. **Compare.** Run diagnostic, regression, then production-streaming phases
   against the same immutable manifest, policies, corpus and implementation.
   The shared executor retains durable pre-dispatch intent, per-call reservations,
   settlement, exclusivity checks, unknown-cost accounting and no replay.
5. **Adjudicate.** Review the automatically generated model-blinded packet.
   Record human verdicts, reviewer identity, failed assertions, hard violations
   and wrong-ID fetches before exposing the separate model-label key. Completion
   and schema flags are mechanical evidence, not semantic acceptance.
6. **Decide.** Reconcile paired quality regressions, failures, latency and cost per
   acceptable answer. Separately inspect production-path evidence. Prepare a
   surgical routing/roster/qualification PR only for the supported envelope;
   passing a bounded synthetic screen never certifies general reasoning/coding.

## Interfaces and storage

- `scripts/model_upgrade_spec.py`: strict manifest validation, paired schedule,
  descriptive summaries and human-review packet generation.
- `scripts/model_upgrade.py`: generic CLI and the existing executor's experiment
  profile. Manifest, policies, selected catalog and implementation are fingerprinted.
- `scripts/model_upgrade_stream.py`: observes the actual `completion_with_tools`
  loop through an explicit dispatch callback; every call still goes through
  `guarded_completion`. Only frozen read-only fixture tools are registered.
- `scripts/model_routing_followup.py`: shared execution/accounting implementation.
  Its original experiment and CLI defaults remain separate. Source changes
  naturally change implementation fingerprints; this is not permission to resume
  historical runs under changed code.

Use a **durable private directory outside disposable worktrees and `/tmp`** for
run artifacts. The run directory and its parent must be mode `0700`; state,
results and review artifacts are private. Credentials belong only in the process
environment, never manifests, command arguments, provider evidence or reports.
The Sol run uses `/home/sol/.local/state/daemon-evaluations/sol61-20260930/`.

Example, with explicitly supplied evaluation credentials/policy paths:

```sh
PYTHONPATH=. uv run python scripts/model_upgrade.py \
  --manifest tests/fixtures/model_upgrades/sol61_20260930.json \
  --account <isolated-account-uuid> --run-dir <private-durable-directory> \
  --phase diagnostic --dry-run
```

Remove `--dry-run` only for an authorized run. Then use `--phase regression` and
`--phase streaming`. Final execution generates `summary.json`, `human-review.md`,
`human-verdicts.json` and a separate `review-map.json`. Offline `--report` can
render retained state without inference; it must not overwrite existing reports.
Raw provider payloads and outbound continuation messages remain in private state.

Version 1 deliberately retains the established 16K/4096/90-second envelope rather
than adding arbitrary token/deadline settings. Comparison and endpoint failover
remain separate experiments. A new manifest is less implementation work, not a
substitute for route qualification, a role-appropriate corpus or approval.

## Failure and compatibility boundaries

- A recorded attempt is never replayed, including an interrupted or unknown-cost
  call. Stops require investigation; another result filename cannot bypass this.
- A failed/truncated answer and its cost stay in the denominator. Unknown cost
  stays null or conservatively reserved, never zero or free.
- A selected funded month is immutable. Dispatch stops before a call could cross
  that boundary; there is no automatic allowance rollover or funding purchase.
- Regression fixtures reused from an earlier held-out set are labeled reused
  evidence. They are not newly unseen evidence on subsequent releases.
- A metadata-preserving non-streaming benchmark does not certify the production
  streaming continuation. Probes record tool execution, final-answer events,
  route/model attribution, usage, settlement and reasoning metadata observations.
  They observe production behavior rather than repairing it inside the harness.
- Latency/cost summaries include failures. Semantic acceptance and quality-normalized
  cost remain pending until human adjudication. Statistical evidence is limited
  by the displayed case/repetition/subgroup counts.

See [the Sol comparison](SOL_UPGRADE_EVALUATION.md) for the first new run and its
explicit authorization. Implementation and live verification status are recorded
there; this workflow document is not a completion certificate.
