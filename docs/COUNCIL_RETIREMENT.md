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

- If `DAEMON_MODEL_ROUTING` is unset, each release uses its own bundled file.
  No override or database migration is needed.
- For a deployment-owned override, keep the pre-retirement file unchanged at
  its existing path and retain an immutable copy for rollback. Stage a
  separate, versioned copy for the new release. Remove the `council` profile,
  suitability entries, and presets from **the new copy only**. For exact
  selections with no remaining automatic workload, keep the default model
  metadata and use an empty suitability list (as for Gemini Flash and Grok in
  the bundled file). Do not promote a model into an automatic pool or change
  provider approvals or plans.
- Bind both backend and worker revisions to their own mounted file and
  `DAEMON_MODEL_ROUTING` path. Old replicas must retain access to the old
  file, including on restart; new replicas use the staged file. Before
  switching traffic, run the local loader from **each matching release**
  with its own mounted path (use the same path the service will see):

  ```sh
  python -c 'import sys; from orchestrator.model_routing import load_model_routing; load_model_routing(sys.argv[1])' /path/to/release-routing.json
  ```

  A successful load validates the file without starting the application or
  calling a provider. A file from the other release is not a substitute: the
  old loader requires `council`, and the new loader rejects it.
- Stop routing new traffic to old backend replicas and drain or stop any
  in-flight request-bound Council streams before declaring execution retired.
  Old client bundles can still send the command; the upgraded backend refuses
  it. If rolling back, restore the old backend **and** worker binaries with
  the old file and `DAEMON_MODEL_ROUTING` binding together. A binary-only
  rollback with the new override breaks routing, and rolling back to the old
  release restores Council execution.
- Council had no native durable job or session-table resume handler. The
  isolated text acceptance surface also refuses old command syntax; if a
  legacy text task reaches the worker, it cannot run as ordinary chat. Existing
  task cancellation, lease fencing, and accounting reconciliation still apply.
