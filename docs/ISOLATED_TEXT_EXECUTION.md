# Isolated original-runner text execution

**Source candidate; qualification and final review in progress.** This is a
bounded source-only qualification composition for DEC04–DEC05 and DEC11, not
production activation or a new user-facing execution mode. The rollout tracker
[#477](https://github.com/sol-aeternum/Daemon/issues/477) stays open.

## Scope and authorities

The approved slice connects the explicitly constructed authenticated acceptance
host to the **original** durable text runner. It preserves the original task,
claim, account generation, attempt identity, invocation, financial reservation,
qualified route, pricing, budget and settlement authorities. No replacement task,
runner, recovery wrapper or dispatch permit is introduced.

The ordinary acceptance-only host remains the default. Execution requires an
explicit, exact text composition with the complete terminal-v3 SQL/history
profile. Production H startup remains observation-only; production worker startup
continues to refuse H execution. This design supplies no deployment, migration,
grants, credentials, environment selector or flag enablement.

The explicit collaborator is `HistoryTextExecution` in
`orchestrator/services/restore_history/text_execution.py`. Only an explicitly
supplied collaborator on `IsolatedAcceptanceHost` selects it; neither normal
startup nor environment discovery constructs it. Open/close own qualification
and original runner children, while the caller owns the borrowed pools.

The composed worker uses its own original qualified store and memory objects on
the borrowed W pool. It does not receive the acceptor store/pool or K1. The host's
original acceptor and reader stores remain uncomposed. Exact pool, encryption,
profile, identity, principal and ownership bindings must agree before admission.

## Text-only restriction

Preparation uses the base prompt, saved preferences/timezone and the original SQL
conversation history. It does not load a skill index, query vector memory or call
an embedding provider. Normal production preparation and skills/memory behavior
are unchanged. Tools, search, embedding calls and optional background extraction,
evaluation and trust-signal writes are unavailable in this isolated composition.
An empty registry alone is insufficient: dispatch boundaries and unsolicited
provider tool-call deltas must also refuse before tool execution or another round.

Unsupported attachments, temporal scope, supplied identity, suggestions, capability
metadata, explicit research requests and unsupported model/provider selection must
refuse before acceptance rather than silently lose requested behavior. Automatic
text routing still passes through the original qualified route and compute policy.

## Required binding and execution order

The original worker composition carries an independent required-H marker. The
original runner installs its exact required-composition context before claiming
work and retains it through child cleanup. Compute scopes capture that requirement
independently of optional inference arguments. Missing, crossed, lost or downgraded
bindings refuse even when optional arguments are omitted. The requirement is a
negative constraint, never authority to dispatch.

The private native context and `ComputeHistoryBinding` are defined in
`orchestrator/tasks/_history_binding.py`. `AttemptIdentity` and its persisted wire
format are unchanged. Normal unbound scopes retain their existing behavior.

Each permitted completion retains this order:

1. Original account/attempt-qualified reservation commits.
2. H verifies parity and original identity and stages `SendPossible` through the
   original native mark transaction, exactly once.
3. Native COMMIT, actual connection cleanup and H release are acknowledged.
4. Original lease, Stop, invocation and deadline checks run again.
5. The original provider transport may begin.

H is never held around provider work or an entire runner invocation. Claim,
registration, execution start, renewal, publication and resolution need their own
bounded H/native dispositions. Non-renewing coherent attempt observation remains
independent of H waits and cannot extend a lease or grant execution.

Any historical Send, Stop or terminal-output evidence refuses a new positive claim
before takeover, epoch changes or result-body clearing. Historical facts are not
acknowledgements or permission to replay. Scope recording and lifecycle registration
remain two native transactions: an acknowledged scope link followed by registration
failure is retained inert evidence, not successful execution or atomic SQL commit.

## Acceptance, observation and Stop

Only a genuinely new, fully acknowledged acceptance may schedule an original
runner child. The composition owns that child independently of HTTP streams.
Duplicate/historical submissions, unknown acknowledgements, lookups and reconnects
must not schedule replacement work. Scheduling failure retains the original task
without creating a second task or repair grant. Disconnect only detaches a view.

The acceptance stream remains task/conversation metadata, not a new token or
completion stream. A second authenticated device observes the original surviving
partial/final SQL output through the existing by-key response. This does not
reconstruct text lost from SQL or qualify a deployed phone client.

Explicit Stop reuses the existing cancel response contract. It owns H before SQL
and retains the actual host bearer/session/device authorization transaction while
the original native cancellation owns a separate top-level transaction. A private
authorization expectation is only comparison evidence under that retained owner,
not a reusable permit. After native blocking locks and before mutation, a fresh
wall-clock check must still establish the original owner, token, unrevoked session
and device, and unexpired access. No fresh positive account-generation capture is
required for negative Stop authority.

An accepted Stop stages `StopIntent` in the same original cancel transaction. If
normal completion wins, Stop reports the existing terminal conflict without a new
Stop fact. If Stop wins, further positive Send/publication refuses. Acknowledgement
requires native and auth transaction cleanup and H release. Historical Stop and
unknown commit/cleanup do not manufacture a fresh success acknowledgement.

Local fake-provider iterator closure must be measured separately from cancellation
intent. Neither a native cancelled state nor local closure proves remote cessation,
billing cancellation or a refund. Existing incurred-liability accounting is distinct
from permission to send again.

## Failure and qualification limits

An H append can survive a failed or uncertain native transaction. The resulting
negative inconsistency blocks work pending separately authorized reconciliation;
it is not a repair instruction or Send grant. Unknown append, stage, COMMIT,
connection reset or H-release outcomes must not become native-only fallback,
defer/requeue, late-result promotion or a replacement invocation.

Shutdown closes admission and drains HTTP owners, original runner/heartbeat
children and physical SQL/history cleanup before closing coordinators or borrowed
pools. Unfinished or uncertain cleanup retains ownership; blocked owners are not
automatically renewed.

Qualification uses synthetic data, actual disposable PostgreSQL and a deterministic
fake at the original provider transport boundary. It must cover account/device
isolation and revocation, budget refusal, stable/conflicting keys, unknown outcomes,
Stop before/during execution and against terminal commit, and second-device reads.
Actual process restart requires fresh process-owned pools/history owners and proof
of no repeated dispatch after historical Send; in-process reconstruction is not
process restart. Until executed and independently reviewed, these are acceptance
requirements, not passed results. No general partial/control-terminal/card restore,
lost-output reconstruction, live-provider or phone readiness is claimed.

The focused candidate suite includes an actual exec-owned worker death
after Send and a surviving SQL partial. A new interpreter reconstructs all pools
and history owners, observes that same task/partial, and refuses historical Send
before another claim. The original task remains `running`, with its original
attempt and outstanding reservation; this is conservative no-replay evidence,
**not** successful automatic resume, remote cessation or financial cleanup. A
separate fresh-interpreter check reopens an already completed original task and
its surviving SQL result without another provider call. Neither check reconstructs
lost text or qualifies a deployed worker restart. Candidate checks do not replace
final-state qualification and independent review.
