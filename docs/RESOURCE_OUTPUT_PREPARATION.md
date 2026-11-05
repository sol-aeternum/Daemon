# H-owned first-output command preparation

This source-only continuation adds `HistoryEffects.prepare_output` before the
existing [reservation join](RESOURCE_OUTPUT_RESERVATION_JOIN.md). It returns an
exact `IntentCommand` and bounded CSV bytes for an original retained `JobBinding`
and slot. No startup, worker, socket, HTTP or producer installs it. H-configured
runtime remains observation-only, and #477 stays open.

## Eighth restricted observation capability

Migration `062_resource_output_preparation.sql` adds
`resource_output_preparation_snapshot(jsonb)`. It preserves the seven existing
signatures, snapshot formats and table permissions. Existing seven-capability
callers can keep using those interfaces without the optional eighth EXECUTE.
The new reader requires all seven prior direct P capabilities plus its own
direct, nongrantable EXECUTE. Its helper remains owner-only. Existing owner,
membership, A/W/R0/H, SET ROLE, temporary namespace and direct-table/column/sequence
exclusions remain. No login, application grant, table, stored format or initializer
is introduced.

062 also repairs the shared private clean-P guard: a noninherited role membership
can still provide a SET ROLE path even when `has_function_privilege` reports no
currently inherited native permission. The guard checks actual membership in
native-function and table/column/sequence grantees. This enforces the existing
exclusion for all selected P entrypoints; it changes no public signature, result
format or ACL. The seven-capability healthy profile remains compatible. Real
synthetic A/W/R0 membership probes discriminate the refusal. Unsafe migration
defaults roll back this private-guard change together with the new reader.

The closed version-1 result contains exactly `schema_version`, `attempt_id` and
`task`. The task has the original `id`, `user_id`, `conversation_id`,
`result_message_id`, `input_version` and `input_ciphertext`. Only the current
matching `task_attempts.id` is exposed, not a task-attempt scan or a generic read.
The existing native account/operation/lifecycle/task/attempt locks and fresh
lease/Stop checks precede the result, with a final native check after source
and attempt waits. This is row-locking observation in an owned READ COMMITTED
transaction, not a SQL READ ONLY transaction or reusable authority.

The existing explicit `snapshot_byte_limit` caps encoded SQL output before Python
decoding. Overflow refuses rather than truncating. Closed keys, canonical UUIDs,
version/type checks and duplicate-key refusal precede use. SQL cannot authenticate
the sealed task: trusted Python must independently decrypt and compare its origin
and exact source IDs with H acceptance.

## Preparation and packet ownership

The typed operation accepts the original `AttemptIdentity`, `JobBinding`, integer
slot, CSV rows and filename, plus an absolute monotonic deadline. It uses the
existing deterministic `encode_csv` with the existing 8 MiB encoded bound and
descriptor filename bounds. Complete request validation/encoding precedes H/SQL;
no path, namespace, arbitrary MIME or callback is accepted. It neither selects a
production transport/memory admission budget nor authenticates a socket peer.

H lifetime/operation ownership precedes native SQL. The join compares independent
identity/key/full H-SQL parity, original acceptance/generation and no historical
Stop. The owned P transaction reads the original sealed task and current attempt;
Python authenticates the origin and exact conversation/result-message targets.
The backend allocates intent/resource/version UUIDs and descriptor ciphertext
once for that explicit call using the existing codecs and source binding.

Only known top-level P COMMIT plus actual native/H cleanup permits returning the
packet and bytes. The caller must retain that exact command before separately
invoking `reserve_output`. Preparation writes no reservation, file, catalog,
presentation or terminal result; it is not dispatch or publication permission.
Later phases independently reacquire every required current native/H check. A
prepared packet does not promise that a later lease or Stop check will succeed.

This adds no durable prepared-command store. A lost packet/reply is not permission
to regenerate ciphertext or allocate replacement IDs; no automatic retry, command
adoption or lost-reply recovery is supplied. The original job/slot and operation
identity remain the caller's. A future authenticated host must define retained
packet ownership before invoking reservation; this uncalled method supplies no
runtime/client recovery contract by itself.

The existing two-second absolute H effect cap is unchanged. Once ownership begins,
cancellation, lost COMMIT acknowledgement, late completion and failed cleanup
retain actual native cancellation/reset/release and H resources, preserve the
original failure and block mutating reuse. No uncertain readback promotes success,
and the renewable read-owner exception cannot replace this mutating owner.
Logical expiry does not bound kernel cleanup. Shutdown must drain actual work.

## Source admission and qualification limits

The exact H reservation-type import additionally admits `JobBinding` and
`IntentBinding`; only the exact static unaliased `encode_csv` pure import and its
literal existing bound are admitted. Typed P preparation seams remain confined
to the named H owner. ADMITTED, module allowlists, consumer construction,
injection, generic callback execution and runtime activation stay restricted.

Synthetic tests exercise real original-schema restricted roles and POSIX H,
closed decoding, unchanged seven-interface behavior, current authority/source
refusal, actual COMMIT/reset/cancellation/shutdown faults, and the real fixture
namespace upgrade path. Qualification results are reported separately; this
document does not claim a passing gate or live acceptance merely from test names.

Authenticated bounded transport, full execution composition, independently
retained artifact restore evidence/reconciliation and lifecycle/privacy/GC remain
launch gates. All real credentials/grants, UID/key/storage/socket provisioning,
live migrations, deployment and activation remain separate operations. No paid
inference or worker key/storage/credential expansion is introduced.
