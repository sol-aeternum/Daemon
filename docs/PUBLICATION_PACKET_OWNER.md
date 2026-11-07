# Uncalled publication protocol and RAM packet owner

Owner-approved source/test prototype following #677. No listener, runtime host,
setting, SQL, grant, credential or storage initializer is installed. The backend-only
native profile described below is uncalled. H-configured runtime remains
observation-only. This is not production transport compatibility, distinct-UID
qualification or launch clearance; #477 stays open.

## Trust boundary and restart

`AuthenticatedWorker` is an out-of-band synthetic authentication fact, never
decoded from worker bytes. It does not authorize an assignment. Backend-only
`admit` receives an immutable `AssignmentAuthorization`: original backend
incarnation, assignment ID, assigned worker, operation ID, 32-byte native-fact
binding and absolute expiry. The binding represents the original native
account/generation/attempt/job/slot facts; it is not a MAC or live permission.

There is no worker registration or admission opcode. Worker, handle, incarnation
and original operation ID must match before observation/effects. Reusing an
assignment ID with a changed handle, operation, expiry, worker or binding refuses.
Exact repeated admission never renews retention. Changed request IDs cannot
create fresh work.

A new backend incarnation has no RAM table. Old handles refuse even with changed
session/request IDs; old authorizations cannot be admitted into the new owner.
**A future trusted native issuer must establish immutable assignment provenance
and must never wrap an old job in a fresh authorization.** This prototype supplies
no real issuer or authentication, and cannot sandbox a trusted Python caller
fabricating facts. Native restart fencing and authorized historical observation
remain separate integration requirements. No durable assignment store, packet
reconstruction, adoption or new client recovery contract is inferred.

## Closed synthetic frames

One four-byte unsigned network-order length precedes canonical ASCII JSON.
Decoding accepts one complete frame, a closed version-1 field set, canonical
nonzero UUIDs/base64 and literal booleans. Duplicate/extra/missing keys, number
aliases, malformed UTF-8, trailing frames and oversized/malformed input refuse.
Direct typed callers receive the same validation. A future listener must bound
reads before allocating the complete frame.

Requests contain `version`, `session`, `handle`, `request_id`, `action`, `payload`.
The fixed actions are preparation, reservation, catalog, presentation, terminal,
observe and retire. Only preparation has a payload. This slice's payloads and
results are synthetic bytes, not the real CSV/client schema, encrypted descriptor
format or arbitrary executable callbacks. Existing native codecs remain unchanged.

Replies contain version, original request identifiers/action, status, phase,
result and `retry_safe=false`. Only established replies contain phase/result.
Pending, retired and needs-attention replies contain neither. Errors have closed
safe codes. Original effect exceptions remain in-process; a future listener must
not serialize their messages, packets, SQL, paths or credentials.

`retire` is RAM retirement, not native Stop, remote cessation or billing
cancellation. It never acknowledges a protected Stop operation. Historical reads
remain in the separately authorized task-observation path.

## Ownership and established results

`PacketOwner` owns one PID/event loop and fixed session/effect/budget properties.
Construction performs no I/O or effect calls. Fork/loop mismatch refuses before
touching owned work. This is cooperating Python, not an in-process sandbox.

The fixed `SyntheticEffects` methods are fake phase operations. Their completion
must include ALL physical work/cleanup. A future adapter must retain the existing
H/native current-authority checks, known top-level COMMIT and actual driver/file
cleanup; this module cannot discover a library's detached workers. It never
constructs, replaces or clears H/coordinator/writer owners. Observing a blocked
collaborator latches this owner even if a fake later changes its flag.

Worst-case packet bytes are reserved before preparation starts. One immutable
command/content packet is retained across the five separate phases, with at most
one input fingerprint/result per phase. Identical active duplicates return
pending without another effect. Settled duplicates return only their retained
established result. Conflicts, changed IDs and out-of-order phases refuse.

Physical completion yields a candidate. Only interested caller settlement,
known completion and an unexpired deadline establish a result. Cancellation
before the effect starts prevents it. Cancellation, deadline, unknown COMMIT or
cleanup failure after admission latches needs-attention and drains owned work,
preserving the original exception through repeated cancellation. Late success or
diagnostic readback never becomes success. Observation after uncertainty returns
no result; later effects refuse.

Admission/busy refusal creates no queued waiter or effect task. Active/draining
ownership and byte reservations survive until operation AND caller settlement
finish. Only the internal logical start gate may be cancelled before an effect
method begins; running physical work is drained. Logical deadlines do not bound
kernel/library cleanup. Call
`close_admission` synchronously before scheduling shutdown. `aclose` also closes
admission before suspending, drains all work/settlement through cancellation,
then drops payloads. Retirement, expiry and shutdown never evict active work or
authorize another owner to resume uncertain work.

## Explicit prototype limits

Operating budgets are mandatory constructor arguments, not production defaults
or environment settings. Hard caps:

| Item | Prototype cap |
| --- | --- |
| Frame JSON / prefix | 8 KiB / 4 bytes |
| Synthetic preparation body / established result | 2 KiB / 1 KiB |
| Assignment entries including tombstones | 64 |
| Live packets / physical operations | 8 / 4 |
| Packet bytes per assignment / globally reserved packet bytes | 8 KiB / 64 KiB |
| Retention / one effect's logical budget | 60 seconds / 2 seconds |

Tests use smaller explicit budgets, including four entries, two live packets,
one operation and 64 packet bytes per assignment. Effect deadlines take the
minimum of original assignment/retention expiry, caller deadline and effect
budget. Requests, observations and repeated admission do not renew expiry.

`held_bytes` counts reserved packet bytes only. Results have a separate bound:
entries times five phases times 1 KiB. Fingerprint/assignment metadata also has
bounded count/size. These are retained-data bounds, not a process-RSS guarantee.
Trusted effect allocation, caller buffers, real encoding and future listener
connection/decoding queues need their own production admission qualification.

Expiry drops inactive payloads/results but retains negative assignment tombstones
and input fingerprints for the entire incarnation. Exhaustion refuses new
assignments. There is no tombstone eviction, automatic restart or slot recycling.
A controlled future restart does not revive old assignments or recreate packets.

## Qualification and remaining scope

Synthetic tests exercise closed frames, authentication versus authorization,
original IDs, phase/input conflicts, known-only lost-reply results, commit/cleanup
uncertainty, per-phase cancellation, late results, scheduling races, expiry,
byte/in-flight/tombstone exhaustion, shutdown and restart refusal. Results are
reported separately. Existing native methods and runtime installation are unchanged. The source
guard retains negative owner/profile construction and activation classifications; ADMITTED,
module allowlists and runtime import/construction/injection restrictions remain
unchanged. No guard exception installs this prototype.
The catalogue explicitly retains the RAM expiry and logical start-gate calls as
deferred, uncovered store candidates. Its scanner and coverage rules are unchanged;
these classifications do not claim a runtime fence or persistent-write authority.

Real authentication, immutable native authorization issuance, external CSV/result
wire schemas, listener/admission configuration and complete H/native runtime
integration still need their bounded contracts and qualification. Independent artifact restore evidence,
lifecycle/privacy and deployment release gates remain open. Real identities,
keys, roles/grants, mounts/sockets, migrations and deployment require specific
operational approvals. No paid inference is introduced.

## Native phase boundary qualification

`tests/test_packet_native_phases_postgres.py` exercises this owner around the
actual H-owned preparation, reservation, file/catalog, presentation and terminal
methods on owned synthetic PostgreSQL/POSIX fixtures. It retains the original
native objects out of band and uses tiny fixed input/result bytes. This is a
test bridge, not a production adapter, assignment issuer or CSV/result codec.
The existing distinct P/R0/W pools, private files, H/J1 and native checks remain
unchanged. No runtime import or construction exception is added.

The cases distinguish identical-result replay from repeated native effects;
exercise Stop, generation and missing-projection boundaries; interrupt actual
phase COMMIT acknowledgements and holder resets; and observe ownership through
repeated cancellation, logical deadline and cleanup failure. They reopen original
task/file results only as synthetic fixture evidence. Results are reported
separately; this description is not deployed or authenticated acceptance.

Two boundaries remain explicit. An abandoned outer caller blocks the packet owner
and cannot establish a late result, but its uncancelled native driver can finish
known cleanup with the H owner still healthy. This does not select a production
interest-propagation or owner-replacement policy. Native terminal publication can
also return a known control-only status, such as `failed:task_generation_stale`,
without publishing new content. The test bridge refuses to invent its wire mapping;
a real adapter must preserve that status rather than call it task success or
mislabel the native COMMIT unknown.

Native CSV supports up to 8 MiB, and native descriptor bounds also exceed the
prototype's selected packet surface. These tests use a small fitting packet;
they do not raise the prototype caps, choose transport/memory budgets or qualify
real request/result formats. Those contracts, immutable native assignment
provenance, worker authentication and complete runtime composition remain open.

## Backend-only native profile

The owner-approved next slice adds `NativeProfile` to the same `PacketOwner`
ownership/settlement engine. It remains uninstalled: no issuer, authentication,
listener, external wire schema, producer, pool creation or runtime caller is
added. The observation-only startup barrier, existing permissions, SQL, native
APIs and synthetic frames are unchanged. This is source qualification, not live
acceptance or restore clearance; #477 remains open.

`admit_native` requires a closed original context: assignment authorization,
`AttemptIdentity`, `JobBinding`, captured `Claim` and output slot. A domain-separated
hash binds their original control facts, backend incarnation, worker and assignment;
it is a negative comparison, never a MAC or authority issuer. Current authorization
is still checked by every native H method. The original Claim is retained through
terminal completion, rather than reconstructed from worker data or SQL. Its input
is not consumed by the profile or included in the control-fact hash.

The same entry/packet table retains one immutable native command/CSV packet.
An additional bounded negative job/slot index points at those same entries and
prevents re-admission under a fresh assignment within the incarnation. A private
capture token refuses substituting an equal-but-reconstructed context. Retirement,
expiry and shutdown release the Claim/context, packet and results only after
physical work and caller settlement end; negative facts survive until shutdown.
A later runtime must prove immutable issuance and restart fencing independently.
This cooperating Python boundary cannot defend against a trusted caller forging
all original native facts.

`handle_native` accepts closed typed preparation rows/filename or terminal text.
It matches the supplied worker/handle/session/original operation before input
encoding. Only fixed named H-owned prepare, reserve, catalog, present and complete
methods execute. Existing P/R0/W pools must be distinct and remain caller-owned.
No generic callback, SQL reconstruction, packet adoption or retry is introduced.
Native records cannot pass through the synthetic encoder/decoder.

Known results retain their native types: no-value preparation/reservation,
`Publication`, presentation UUID, and terminal `TaskStatus` restricted to completed,
failed, cancelled or needs-attention. An established terminal phase can report a
known `FAILED` control outcome without publishing new content. It never implies
all tasks succeeded. The profile validates the existing descriptor and actual
content bytes; it preserves the original command and packet across phases.

Cancellation of an interested caller blocks this owner and drains its uncancelled
native driver, including actual pool reset and H/file cleanup. A late result is
not established. Native uncertainty retains native failure latches. Outer
abandonment alone does not poison a native H owner whose work/cleanup completed
normally; it never permits replacing this blocked packet owner to retry.
Logical deadlines still do not bound kernel/library cleanup.

Native budgets are all required, with no runtime defaults or environment keys:

| Item | Native source-profile hard cap |
| --- | --- |
| CSV / canonical existing command | 8388608 / 262144 bytes |
| Packet / globally reserved packets | 8650752 / 67108864 bytes |
| Input rows / columns per row / cells | 10000 / 1000 / 100000 |
| Filename / terminal UTF-8 | 1024 / 1048576 bytes |
| Entries / live packets / operations | 64 / 8 / 4 |
| Retention eligibility / effect deadline | 60 / 2 seconds |

The CSV cap preserves the existing native 8 MiB maximum; the command allowance
covers the existing encrypted descriptor plus bounded control fields. Cell and
text limits conservatively bound pre-encoding traversal/copies without selecting
an external transport contract. Exact encoded CSV bytes are measured before
effects, including quoting and UTF-8 expansion. Smaller caller-supplied budgets
can refuse. Synthetic budgets and frames retain their original limits.

These are packet/data and count bounds, not an RSS guarantee. Caller-allocated
rows, original Claim/input, encoding temporaries and future ingress queues remain
outside reserved packet accounting. Results are fixed native control facts rather
than arbitrary bytes. Expiry remains explicit/lazy; no retention service or live
delete policy is installed. Active/draining ownership is never evicted.

Tests exercise native phase replay, current Stop/generation/projection checks,
known control-only failure, lost COMMIT acknowledgements before and after every
phase, real held pool resets under cancellation/deadline/failure, and shutdown.
Separate unit tests cover binding substitution, negative re-admission, strict
modes/types, byte/cell caps, no-I/O construction and narrow source guard rules.
Runtime construction/admission/calls remain forbidden, including in the existing
H startup namespace. Only the exact static native type imports and pure bounded
CSV encoder call are permitted; ADMITTED is unchanged.

Real immutable issuance, service authentication, external request/result framing,
whole-ingress admission, distinct-UID runtime ownership, complete execution host,
artifact restore/lifecycle evidence and live rollout remain separate gates.
