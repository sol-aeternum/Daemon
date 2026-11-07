# Post-catalog output restore boundary

Source baseline: main `9857aa674d37ae9fdc0b836095f1797d2092182a`, after
#688. This is synthetic qualification of existing methods, not a recovery
implementation, new history format, SQL capability, API or runtime admission.
The H-configured runtime remains observation-only; #477 stays open.

## The specific remaining gap

The `artifact-v2` catalog prepare protects the first CSV catalog transaction.
It does not independently record the later presentation, durable partial
checkpoints or native terminal publication. Its two matching H/SQL facts
(acceptance and catalog prepare) therefore cannot establish that those later
outputs survived a whole-database rollback.

`tests/test_artifact_output_restore_postgres.py` uses the actual H-owned joins
with the v2 projection and five SQL checkpoints. Every source run publishes the
original CSV, one presentation, partial sequences 3 and 7, then the native
completed result. The source's final state is independently observed before
restoring an older synthetic archive. No missing output is replayed or adopted.

| Restored checkpoint | Surviving observation | Missing later output despite matching catalog history |
| --- | --- | --- |
| Catalog | Original task running, CSV readable, no card, partial sequence 0 | Card, both partial checkpoints and terminal result |
| Presentation | Original task running, CSV and card present, partial sequence 0 | Both partial checkpoints and terminal result |
| First partial | Original task running, card present, original partial at sequence 3 | Later partial and terminal result |
| Later partial | Original task running, card present, original partial at sequence 7 | Terminal result |
| Terminal | Original completed task, final text and terminal metadata, original CSV/card | Consistent positive control only |

The tests require unchanged independent LOG/HEAD bytes after the catalog commit,
complete v2 parity after restore, original task/publication IDs, exact checkpoint
text/sequence/status and unchanged SQL/file facts after observation. A passing
test demonstrates the missing guarantee; it does not turn apparent RUNNING into
permission to continue or qualify a historical observation/client contract.

Six additional controls restore SQL before a later independent Stop or possible
Send, then separately try presentation, partial and terminal entrypoints. Each
must refuse before its native write, block the original owner and refuse a
second call without changing SQL or files. The possible-Send control performs
no provider effect. These controls preserve existing negative authority; they
do not manufacture missing output evidence or allow a writer reset.

## Isolation and evidence limits

The existing bounded restore fixture closes healthy original owners and actual
pools before reopening. It restores synthetic data into a fresh, verified-owned
database in its cached, network-none PostgreSQL container, compares logical
schema and original ACLs, and closes/drops only a reconciled owned target.
Uncertain targets are quarantined until the owned outer container stops.
No private application archive, globals or credentials are used.

Artifact bytes are unchanged after the catalog checkpoint in this matrix; it
does not qualify append or artifact-file rollback/reconstruction. Native reads
use synthetic captured ownership, not real HTTP/device authentication. No new
assignment, output retry, readback promotion, pruning, retention or recovery
disposition is implemented. J1 and the existing H/coordinator failure latches
are unchanged. Exact executed results are reported separately; default CI
skips these cases without `--compute-metadata-postgres`.

## Proposed next source slice — requires owner decision

Recommend completed-text-first independent negative evidence, joined to the
existing normal completion transaction rather than activating execution now.
It would detect a lost completed result even when catalog parity still matches.
It would not close the separate presentation/partial or whole restore gates.

The decision needs to cover a new explicitly selected, closed history profile
and terminal-prepare record, a validating W-only protected SQL stager with
bounded A/W complete-history readback, and a typed internal transaction seam
for the native completed-output body. The seam must preserve current guards,
stage before the same known top-level COMMIT and retain actual cleanup ownership.
Native control-only refusal/quarantine outcomes must remain unchanged and are
explicitly outside this first completed-text fact: `_worker` can settle refusal
in a separate `finish_refusal` transaction, and `_complete` can quarantine an
interrupted invocation before publishing text. Their independent restore
coverage remains open. Existing public task APIs, direct table
permissions and default H1/v2 behavior must stay unchanged. No generic callback
or uncertain-outcome readback would be admitted.

The terminal fact would be negative metadata bound to original acceptance,
task/result message, account generation and original attempt/origin. No text,
artifact bytes, raw content digest or execution permission is proposed. A lost
append/COMMIT/release acknowledgement must remain blocked; matching historical
evidence must refuse replacement work. Existing finite history bounds would
remain, without pruning or a silent capacity increase. Exact format, schema
allocation and authority changes must be approved before implementation.

Protecting all terminal statuses together would require a broader reviewed seam
through those separate control transactions and their generation/refusal rules.
Presentation-first would protect cards but still lose completed text; adding
every partial checkpoint at once would also change retention/capacity behavior
for long streams. Completed-text-first has the smallest high-value boundary, with
those gaps explicitly retained. Controlled reconciliation is a separate later
decision: neither parity nor a terminal prepare may repair, adopt or resume
historical work. Live migrations, grants, credentials, storage, deployment and
activation remain separate operational approvals under #477.
