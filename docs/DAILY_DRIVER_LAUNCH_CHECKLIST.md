# Personal daily-driver launch checklist

Source checkpoint: `6383d90be344c50569557c0cf8925ca478828de7` (main verified
before the complete namespace-inventory source slice). This checklist separates
merged source, synthetic qualification and deployed acceptance. The independent memory evaluation lane is
not a launch prerequisite. Keep [#477](https://github.com/sol-aeternum/Daemon/issues/477)
open until its applicable release conditions are met or explicitly revised.

| Gate | Current evidence | Release requirement still outstanding |
| --- | --- | --- |
| Protected acceptance, Send and Stop | #625/#628/#631/#637 merged; [composition boundary](RESTORE_HISTORY_COMPOSITION.md), immutable 056/057 projections and original native fences | Historical observation/client source is merged in #650; authenticated H runtime composition remains; no history record is execution authority |
| Cross-device reopen and uncertain work | Existing owner-scoped task APIs/observer preserve persisted results; focused reconnect diagnostics exercise actual device access checks on synthetic SQL/POSIX fixtures | H-aware source responses/client refusal are merged in #650 (RESTORE_HISTORY_OBSERVATION.md); deployed cross-device and held-key acceptance still require qualification |
| Stop and publication races | #637 Send/Stop and #653 H-ordered durable partial text/native terminal fences are merged, with no runtime execution install | Authenticated runtime wiring and streamed/partial/artifact publication qualification; Stop prevents subsequent local admission/publication, not guaranteed remote cessation or billing cancellation |
| Migration path | Closed 044–050 runner merged and independently qualified on the approved index-absent synthetic profile | Fresh target/baseline verification, approved writer quiescence and exact live migration actions; 051–060 need their own reviewed execution scope, not incidental inclusion in the closed runner |
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

Main includes #650 historical observation and #652 backend reader/lifecycle
installation under the explicit [interim observation-only
contract](RESTORE_HISTORY_RUNTIME.md). Producer ingress and background/worker
execution remain unavailable whenever H is selected. This is not deployed
acceptance: authenticated acceptance/Send/Stop/output composition, real identities,
keys, restricted grants, schema upgrade and restore/launch qualification remain
unchecked operational gates. #477 stays open. Benchmark qualification is separate.


## Remaining runtime and artifact authority

Durable text publication uses the existing W TaskStore transaction and H/SQL
parity without adding an output-history protocol. Authenticated acceptance,
Send/Stop runtime installation and live/progress transport remain uncomposed;
this dependency does not relax the observation-only barrier.

C1's `ResourceStore.publish_output` is a trusted injected publication pool, while
`ResourceCSV` separately uses restricted output reservations. No runtime host
currently selects that trusted publisher for the W-only worker. The approved
backend-owned P design supplies the separate SQL boundary in 058 and this
source-only 059 continuation. Complete and qualify the split output
collaborator before artifact composition; do not hand W an A/P credential, C1
mount or broader grants. No runtime publisher is installed. #477 stays open.

The subsequent approved backend-owned artifact design is being implemented in
bounded source slices. `058_resource_output_presentations.sql` supplies only
the separate P presentation capability described in
[output authority](RESOURCE_OUTPUT_AUTHORITY.md). No P login/grant, artifact mount,
socket or runtime publisher is installed. Trusted file authority, the split
output collaborator, local authentication and artifact restore evidence remain
outstanding;
this source SQL does not remove the observation-only barrier or complete #477.


Source continuation after main `7290a1cb`: 059 supplies restricted P catalog
observation/admission/staging for existing reserved output/append intents
([catalog contract](RESOURCE_OUTPUT_CATALOG.md)). Both 058/059 remain uncalled.
Trusted file verification/output collaborator, local
transport, artifact restore evidence and authenticated runtime composition remain
outstanding. No live migration/ACL/configuration or launch status is changed.


Source continuation after main `6383d90b`: 060 supplies a bounded complete P
namespace/marker snapshot and shares native J1 parity validation
([inventory contract](RESOURCE_OUTPUT_NAMESPACE_INVENTORY.md)). It performs no
file effect or repair and is not runtime-installed. Trusted output/file and append
collaborators, authenticated local transport, artifact restore/lifecycle evidence
and full execution composition remain outstanding. No live acceptance is claimed.

Source continuation on the reviewed 060 dependency: the uncalled
[reserved CSV publisher](RESOURCE_RESERVED_OUTPUT_PUBLISHER.md) joins H ownership,
original sealed task authority, restricted P catalog staging and verified files.
It handles only an already-reserved first output, with no new runtime caller,
reservation, append, presentation or transport. Observation-only remains in force;
artifact restore/lifecycle evidence, split collaborators and authenticated complete
execution composition still gate launch. No deployed qualification is claimed.
