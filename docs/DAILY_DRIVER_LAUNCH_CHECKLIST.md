# Personal daily-driver launch checklist

Source checkpoint: `fd8b62a0f5c7ff80be702a6493c892b973fadf54` (main verified
before publication). This checklist separates merged source, synthetic
qualification and deployed acceptance. The independent memory evaluation lane is
not a launch prerequisite. Keep [#477](https://github.com/sol-aeternum/Daemon/issues/477)
open until its applicable release conditions are met or explicitly revised.

| Gate | Current evidence | Release requirement still outstanding |
| --- | --- | --- |
| Protected acceptance, Send and Stop | #625/#628/#631/#637 merged; [composition boundary](RESTORE_HISTORY_COMPOSITION.md), immutable 056/057 projections and original native fences | Approved historical observation/client source is implemented; authenticated H runtime composition remains; no history record is execution authority |
| Cross-device reopen and uncertain work | Existing owner-scoped task APIs/observer preserve persisted results; focused reconnect diagnostics exercise actual device access checks on synthetic SQL/POSIX fixtures | H-aware source responses/client refusal are implemented in this slice (RESTORE_HISTORY_OBSERVATION.md); deployed cross-device and held-key acceptance still require qualification |
| Stop and publication races | #637's isolated Send/Stop and narrow terminal-completion fence is merged | Authenticated runtime wiring and streamed/partial/artifact publication qualification; Stop prevents subsequent local admission/publication, not guaranteed remote cessation or billing cancellation |
| Migration path | Closed 044–050 runner merged and independently qualified on the approved index-absent synthetic profile | Fresh target/baseline verification, approved writer quiescence and exact live migration actions; 051–057 need their own reviewed execution scope, not incidental inclusion in the closed runner |
| Principal/storage/startup binding | Separate backend A/W and W-only worker startup is merged; [startup contract](TASK_STARTUP_COMPOSITION.md) | Reviewed live credentials, exact grants, private H/J1 storage/key identity, service-specific access and a pinned deployed source; no credentials/grants/storage are provisioned by this checklist |
| Restart and recovery | Prior isolated PostgreSQL/POSIX and service-startup tests cover specific boundaries | Pinned-runtime restart/reconnect check with original task/results and truthful uncertain outcomes; observed container source/flags/identity read back after any separately approved deployment |
| Restored database | H/protected SQL mismatch detection exists in isolated joins; historical evidence does not authorize replay | Supported offline restore barrier/reconciliation, independently retained acceptance/possible-send/Stop evidence, and financial/auth/key/account/file continuity; neither schema equality nor matching J1 alone is restore readiness |
| Lifecycle and privacy | #469 is closed as a design decision, not completed deletion/reset; #486 has source/browser cache-protection qualification | Remaining deletion/reset/retention/GC fences and any explicit personal-pilot contract; no implicit lifecycle/privacy waiver from a closed issue |
| Browser flows and clients | Existing task/cancel/reset and pending-submission behavior has source tests; browser inventory failures remain separately disclosed | Relevant real-browser reconnect, held-key uncertainty, Stop, revoked/account-change access and file continuity on the qualified runtime; generic green browser job badges do not establish every screenshot/functional case passed |
| Enablement | Owner conditionally authorizes durable chat ON once ready | Meet applicable gates, obtain specific live migration/privilege/deployment approvals, then verify the actual enabled flag/state; flag-off is not rollback and must preserve dispatch evidence |

Last saved deployment evidence is historical: the application database was at
043, the backend/worker were held stopped, and their old containers bind-mounted
the dirty legacy checkout. This pass has not freshly qualified live schema,
images, effective configuration or health. It does not claim the merged source
is running. The retained private backup/isolated schema-restore rehearsal did not
qualify restoration of original ownership/ACLs, real account/key data or independent
dispatch continuity, and has not been repeated.

The original SQL-only by-key gap is reproduced by retained diagnostics: missing
SQL was not proof the request had never been accepted. This slice implements
the owner-approved H-aware read/client contract, with the separately approved observation-only backend startup described below. Its source tests cannot
substitute for authenticated runtime composition or deployed/browser acceptance.

## Observation-only startup source update

Based on merged historical observation `b1cb9b9e`, the next source slice installs
the backend reader and lifecycle under the explicit [interim observation-only
contract](RESTORE_HISTORY_RUNTIME.md). Producer ingress and background/worker
execution remain unavailable whenever H is selected. This is not deployed
acceptance: authenticated acceptance/Send/Stop/output composition, real identities,
keys, restricted grants, schema upgrade and restore/launch qualification remain
unchecked operational gates. #477 stays open. Benchmark qualification is separate.
