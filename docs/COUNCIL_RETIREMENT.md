# Council retirement

Council is no longer an executable feature. The chat endpoints reject a
leading `/council` command, including old `--default`, `config:` and colon
subcommands, with HTTP 410 and “Council has been retired.” There is no inference,
tool action, new message, session or durable task for a retired invocation.
Mentioning Council in normal chat is not an invocation.

## Saved conversations

The owner can still reopen old replies and copy saved answer prose using the
ordinary transcript. Historical errors retain their prose; an interrupted answer
remains visibly incomplete. The old configuration card no longer offers a
resume button. Duplicate event archives are not rendered as tool activity.
Advisor events and ordinary tool traces are unaffected.

There is no dedicated transcript export. The existing owner-checked
`GET /conversations/{id}` history JSON (including pagination) retains the
saved text, status and legacy metadata/trace fields. No new read endpoint or
new database access is required. Migration `020_create_council_sessions.sql`,
the table, foreign key, and stored transcripts stay untouched. Retiring the
writer stops new Council event copies; existing legacy plaintext copies
tracked by [#332](https://github.com/sol-aeternum/Daemon/issues/332) remain a
separate data-retention and migration decision.

## Rollout

- If `DAEMON_MODEL_ROUTING` points to a deployment-owned file, remove the
  `council` profile, each `council` suitability entry, and the `council` presets
  from that file before starting the new backend and worker. For already
  approved exact-selection models with no remaining automatic workload, keep
  the default model metadata and use an empty suitability list (the checked-in
  file does this for Gemini Flash and Grok). Do not move
  any model into a new automatic pool or change provider approvals or plans.
  No environment variable or database migration changes are required.
- Stop routing new traffic to old backend replicas and drain or stop any
  in-flight request-bound Council streams before declaring execution retired.
  Old client bundles can still send the command; the upgraded backend refuses
  it. Reintroducing an old backend binary would reintroduce execution.
- Council had no native durable job or session-table resume handler. The
  isolated text acceptance surface also refuses old command syntax; if a
  legacy text task reaches the worker, it cannot run as ordinary chat. Existing
  task cancellation, lease fencing, and accounting reconciliation still apply.
