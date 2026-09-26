---
title: Daemon — Product Vision and System Principles
version: "0.2"
status: living-draft-with-approved-product-decisions
created: 2026-09-26
updated: 2026-09-26
implementation_status: assessed-separately-not-release-certified
---

# Daemon — Product Vision and System Principles

> **Daemon is your persistent agent workspace. It works in the cloud by default, uses your connected devices and services when needed, and carries the same work forward wherever you access it.**

**The work belongs to the user—not to a device, chat thread, model, or execution environment.**

## 1. Purpose, authority, and reading guide

This is a durable synthesis of the product discussion and decision interview on **26 September 2026**. It guides product decisions, architecture discussions, issue creation, and coding agents. It records a destination and proposed design constraints, not a claim that these capabilities already exist.

Version 0.1 was produced without repository inspection. The subsequent Stage 0 assessment inspected the current working tree and tests; see [the dated reconciliation](DAEMON_RECONCILIATION.md). Existing implementation and incomplete gates do not establish deployment readiness. This document does not authorise a wholesale rewrite, deployment, purchase, or expansion of access permissions.

Four labels distinguish intent from unsettled design:

| Label | Meaning |
| --- | --- |
| **Vision** | Direction explicitly supplied by the product owner in the originating discussion. Preserve unless explicitly revised. |
| **Approved product decision** | A choice explicitly made in the follow-up interview, recorded in [DEC01–DEC10](DAEMON_VISION_DECISIONS.md). Approval is limited to the stated product behaviour, not its implementation architecture. |
| **Proposed** | A recommended interpretation, design default, or delivery criterion. Not yet a ratified implementation decision. |
| **Open** | A choice requiring further evidence or an explicit product/architecture decision. |

Unless labelled **Vision**, **Approved product decision**, or identified as a sourced external constraint, the requirements and recommendations below are **Proposed**. Words such as *must* express the intended strength of a proposed requirement, not evidence that it is approved or implemented.

This document governs product direction, not current implementation status. Follow [SOURCES_OF_TRUTH.md](SOURCES_OF_TRUTH.md) for implementation facts. Existing accepted architecture and policy decisions remain in force unless explicitly superseded. An Open item may be partly resolved: link its existing contract rather than silently reopening it. Uncommitted code is evidence of behaviour, not by itself evidence of approval. In particular, preserve the existing [commercial and provider-policy contract](SUBSCRIPTION_ARCHITECTURE.md).

**Fast reading path:** sections 2–4 describe the product; sections 5–10 describe operating boundaries; sections 11–14 guide delivery and future agents. Stable IDs reference principles, decisions, and acceptance criteria.

## 2. Executive definition

Daemon is one logical, always-available agent with persistent workspaces, files, memory, and ongoing tasks. The hosted environment is its default place of work. A user can use the product entirely from a phone without installing or maintaining another computer.

Optionally, users connect additional devices. Those devices become permission-controlled sources of information and, where supported and authorised, places where actions can execute. They do not replace the hosted workspace or create separate assistants.

The user should be able to say:

> “Daemon, bring up those documents from my work laptop and prepare the presentation for my phone.”

Daemon should resolve the relevant resources, check authority, perform the work in the appropriate environment, preserve the result, and make it available where the user is. Users should not manually copy context between a chat assistant and a separate work agent.

**The defining promise:**

> “I asked Daemon. It found the right material, worked within the boundaries I set, kept the task moving without an open chat window, and brought the result back to wherever I was.”

**Positioning:** cloud-first, device-extended, task-centred, permission-bound.

## 3. Product direction — Vision

| ID | Principle | Product consequence |
| --- | --- | --- |
| **V01** | **One persistent Daemon across devices.** | The same ongoing work, relevant memory, files, and workspace state remain available across sessions and supported clients. |
| **V02** | **The hosted workspace is the default working environment.** | Useful work must not depend on the user's desktop remaining connected or the user administering a server. Tasks needing a device remain subject to its availability. |
| **V03** | **Phone-only use is a complete endpoint.** | Install the app, create an account, and use Daemon. Pairing a computer expands capability; it is not required to make the core product useful. |
| **V04** | **Devices are optional extensions.** | Users can connect supported devices through simple account-based enrolment, with QR pairing as an intended interaction. |
| **V05** | **Device access follows user permissions.** | Reading, moving files, and taking actions across devices are permitted only within authorised scope and applicable platform constraints. |
| **V06** | **No artificial chat/work divide.** | Discussion, execution, artifacts, and follow-up must not become mutually inaccessible because the user chose the wrong mode. |
| **V07** | **Work persists beyond the interaction.** | Files, memory, workspaces, and task continuity are durable capabilities, not disposable context belonging only to the current conversation. |

### Interpretation of “work by default” — Proposed

Daemon is **work-capable by default**, not action-taking regardless of intent. Explanation and brainstorming are legitimate work. They need not invoke a sandbox or an elaborate workflow.

Hypothetical or explanatory discussion is not an instruction to act. “What would happen if we deleted these?” must not trigger deletion. Natural-language requests, including polite questions such as “Can you prepare this presentation?”, can authorise work within their clear intent and existing permissions. Ask when ambiguity materially changes the action, authority, destination, or consequence. Tool choice is automatic within the request and standing permissions; expansion of authority is not.

### Workspace and continuity defaults — Approved product decisions

Each user has **one personal workspace with optional projects** (DEC01). Projects organise work; relevant context is **shared unless restricted** (DEC02), not indiscriminately loaded into every prompt. A restricted project may use permitted workspace context, but its files, memory and results stay inside unless explicitly shared (DEC03). Account/tenant boundaries and source restrictions always apply.

**Every accepted request**, including ordinary questions, continues after client closure within its authority and budget (DEC04). Acceptance means **durably saved and tracked**, not necessarily ready to execute or guaranteed to succeed (DEC05). When a material choice is unclear, make useful independent progress, save it, and wait for the user's answer rather than guessing that choice (DEC06).

## 4. Canonical user experience

### 4.1 Start with one device

A new user installs the app and signs in. They can immediately provide information, ask questions, commission work, and review saved results. No VPN account, paired computer, or terminal setup is required.

**Approved product decisions (DEC01, DEC07–DEC08):** the personal workspace is available immediately; projects are optional. Uploaded documents become reusable workspace resources, associated with a project when applicable, under the storage/retention policy. A clear request authorises necessary hosted processing within existing disclosed policy. Selected useful source-linked memory may be retained automatically, inheriting source restrictions and remaining inspectable, correctable and removable. Document claims must be distinguished from facts about the user. Uploading is not permission for external sharing or an unapproved processor.

Exact retention periods, storage limits and deletion propagation remain open; “retained” does not mean unlimited storage or perpetual retention.

### 4.2 Separate signing in from granting local access — Proposed

The desktop application has two independent roles:

- **Use Daemon:** access the same tasks, conversations, artifacts, and approvals.
- **Connect this computer:** explicitly permit selected local resources or operations to be made available to Daemon.

Signing in must not silently enrol the computer for filesystem access or execution. A user may use the desktop interface permanently without granting either.

### 4.3 Enrol a device, not a permanent phone–computer dependency — Proposed

The proposed pairing flow is:

1. The signed-in desktop starts a short-lived enrolment request bound to its device identity.
2. The phone scans a QR code and displays the device, account, and requested connection for confirmation. Both endpoints confirm the same transaction.
3. The user selects local resources and capabilities, including whether contents may be processed in the hosted environment.
4. The companion registers only approved capabilities and becomes visible in the account's device inventory.

The QR code must not expose a reusable account credential. Exact key provisioning, authentication, recovery, and expiry require a separate security design.

The computer joins the user's Daemon environment, not the particular phone used for approval. Replacing the phone should not require rebuilding every device relationship. Provide a secure desktop-first alternative; the phone is a convenient approval surface, not the universal root of trust.

### 4.4 End-to-end reference workflow — Proposed

**Request:** “Bring up those documents from my work laptop and prepare the presentation for my phone.”

**Preconditions:** legitimate laptop enrolment; authorised resources; permitted cloud processing; reachable inputs or an acceptable authorised snapshot.

| Step | Expected behaviour |
| --- | --- |
| Resolve | Identify project, device, documents, and outcome. Ask when remaining ambiguity materially affects the result. |
| Authorise | Check resource, processing, and destination permissions. Do not search outside scope merely to improve discovery. |
| Acquire | Read the minimum useful input; preserve source identity, version/fingerprint, retrieval time, and processing permissions. |
| Execute | Use hosted execution with appropriate isolation by default. Input location does not determine where the whole job runs. |
| Validate | Check source fidelity, artifact completeness, and presentation rendering. File creation alone is not acceptable-work evidence. |
| Persist | Save the editable presentation and task state as durable workspace artifacts. Preserve originals unless modification was requested and authorised. |
| Deliver | Make the result reviewable from the phone, with suitable renditions and explicit offline-save support; do not assume arbitrary phone filesystem access. |

An illustrative completion message is: “The presentation is ready. I used three documents from your approved Reports folder. The originals are unchanged.” It is valid only when recorded evidence supports every claim.

A missing permission, offline source, or stale snapshot should produce a specific task state and explanation—not fabricated access, silent substitution, or loss of the request.

## 5. Proposed design defaults

| ID | Default | Reason and boundary |
| --- | --- | --- |
| **D01** | Separate durable state, coordination, and execution. | Failed/discarded execution must not erase the project or expose every device. |
| **D02** | Expose scoped device capabilities, not a flat trusted network. | Folder-read authority must not imply arbitrary shell or network access. |
| **D03** | Start by evaluating an outbound authenticated companion connection. | Prove device extension before committing to a general VPN or mesh. Transport remains open. |
| **D04** | Model a task independently from a chat session or model run. | Client closure, model changes, or worker restarts must not redefine task identity. DEC04–DEC05 approve continuity, not a schema. |
| **D05** | Make discussion, artifacts, activity, and approvals views of shared state. | Eliminate silos without requiring one giant prompt or screen. |
| **D06** | Check permissions outside the model and at the execution boundary. | The model proposes actions; it cannot grant itself authority. |
| **D07** | Prefer on-demand resource access before continuous synchronisation. | Connecting a resource need not upload, index, or mirror all its contents. |
| **D08** | Run compute for useful work, not merely because the service is persistent. | Always available does not require continuous reasoning or permanently active per-user machines. |
| **D09** | Prefer bounded application operations over unrestricted local execution. | A document workflow should not require general computer control. |
| **D10** | Preserve common privacy and continuity principles across tiers. | Differentiate capacity and workload, not artificial context silos or selling user data. Preserve the existing commercial contract. |

### 5.1 Logical components

These are responsibilities, not separate microservices or a required technology stack.

| Component | Responsibility |
| --- | --- |
| **Clients** | Submit requests; view work; supply inputs; display activity; collect approvals. |
| **Persistent workspace** | Store projects/tasks, artifacts/versions, relevant memory, source references and retention metadata. |
| **Coordinator** | Manage lifecycle, discovery, planning, routing, budgets, approvals and recovery. |
| **Policy enforcement** | Evaluate authority independently of model output; bind grants to scope, purpose, operation, destination and validity. |
| **Execution sandbox / bounded executor** | Run assigned work with permitted inputs/tools and scoped credentials; publish outputs durably. Isolation mechanisms depend on the operation; do not imply arbitrary-code support or isolation guarantees that are absent. |
| **Device companion** | Advertise capabilities, report availability, validate requests locally, execute authorised operations and return evidence. |
| **Service connectors** | Provide separately authorised access to resources whose authoritative home is an external service. |
| **Activity and audit record** | Record actions, approvals, provenance, outcomes and failures without indiscriminate sensitive-payload logging. |

The coordinator is not synonymous with a model. One logical Daemon may use multiple models or workers. Delegation inherits restrictions; it must not widen authority or leak context across boundaries.

### 5.2 Network analogy and limits

Borrow Tailscale's simple enrolment, authenticated device identity, and secure reachability—not an assumed commitment to its topology or commercial dependency. Tailscale separates coordination from a device-level data plane; its coordination service handles metadata rather than carrying application traffic. [S1](#s1)

A hosted Daemon executor reading a document is deliberately a processing endpoint. A tunnel does not resolve application permissions, inference-provider disclosure, retention, or isolation.

Choose transport through an architecture decision covering security, deployment, licensing, connectivity, mobile constraints, and cost. Do not implement custom cryptography.

## 6. Permissions, data movement, and privacy

### 6.1 Authority is more than filesystem read/write — Proposed

| Dimension | Example grant | What it does not imply |
| --- | --- | --- |
| **Discover/read** | Search/read a selected folder. | Other folders, the whole device or every account resource. |
| **Process/transfer** | Process permitted contents using the hosted workspace and approved providers. | Any processor or arbitrary external recipients. |
| **Create** | Write new outputs to a named destination. | Replace/delete existing files. |
| **Modify/move/delete** | Perform a specified authorised operation. | Authority over unrelated originals or destinations. |
| **Execute/control** | Invoke a bounded runtime or approved local application operation. | Unrestricted shell, administrator access or every app. |
| **Retain/index** | Retain a copy/searchable representation under explicit policy. | Permanent mirroring, unlimited retention or unrestricted memory access. |
| **Share externally** | Send a specified artifact to an approved recipient/destination. | Publication, onward sharing or unrelated-task access. |

**Read-only does not mean information cannot leave the device.** Files, excerpts, filenames, embeddings, summaries and derived memory can all be sensitive. Processing/retention scope must cover representations as well as originals.

A permission profile might say: “Read this folder, process it in my hosted workspace, and create new outputs in my Daemon folder. Ask before changing originals or sharing outside my account.” Display actual scope and avoid prompting for every covered read. DEC07–DEC08 specify hosted-upload defaults; they do not automatically grant companion access.

A local-only policy must not silently become cloud processing. Use a genuinely permitted path or explain the unavailable capability and request a deliberate policy change. Local inference is not assumed to exist.

### 6.2 Enforcement and approval rules — Proposed

Authorisation is the intersection of task intent, standing grants, source/project restrictions, organisation policy and platform capabilities. A folder grant is not an instruction to act on every file.

Enforce scope before retrieval and at the operation boundary. Bind approvals to material actions and relevant content/version; changed targets, destinations or destructive effects require reevaluation.

Treat documents, web content, tool output and device-provided text as untrusted input, not instructions that expand permissions. Retrieval indexes and caches must enforce source restrictions.

Capability tokens do not constrain code with unrestricted operating-system privileges. General shell/automation requires explicit filesystem, egress, credential and privilege containment, or an honestly disclosed higher-trust mode. Do not market unenforced scope as a sandbox.

### 6.3 Isolation, revocation, and deletion

**Approved product decisions (DEC02–DEC03):** account/tenant isolation remains mandatory. Projects share relevant permitted context by default. Restricted projects may consume permitted workspace context but cannot disclose their own files, memory or results outside without explicit permission. Source restrictions also apply to derivatives, indexes and caches. A shared interface is not authority to bypass these boundaries.

**Proposed:** separate these user actions:

- **Disconnect/revoke:** stop future device access and invalidate outstanding authority under a defined revocation model.
- **Cancel work:** stop/prevent operations where still possible, reporting completed or uncertain effects.
- **Remove retained information:** apply defined deletion policy to copies, indexes, derived memory, artifacts and backups as appropriate.

Revocation cannot undo completed actions or recall delivered information. Define expiry, offline grants, cancellation races and maximum revocation delay before unattended actions ship. Devices must reject expired/invalid authority rather than indefinitely trusting cached approval.

The companion requires a trusted install/update path, protected credentials and an incident/revocation process before broad distribution.

### 6.4 Honest hosted privacy — Proposed

Hosted execution processes plaintext within authorised environments and may disclose selected inputs to inference providers. Transport encryption does not make those processors unable to see inputs.

Document which parties process which data, purposes, storage locations, retention, training use and deletion. Policy eligibility precedes price/capability; fallback must not weaken policy. Do not claim zero retention, local-only processing or provider-invisible encryption without implementation and applicable contractual evidence.

No free tier should be funded by undisclosed training use or data exploitation. The existing [provider qualification contract](SUBSCRIPTION_ARCHITECTURE.md#privacy-invariants) remains in force; live qualification and resource-specific policy require further evidence. The vision does not relax that contract.

## 7. Durable context and resource semantics

### 7.1 State that should survive

| Concept | Minimum responsibility |
| --- | --- |
| **Personal workspace** | Account-owned home for ongoing work, optional projects, resources, artifacts and relevant context (DEC01). |
| **Project** | Optional organisation of work with purpose, instructions and linked resources; shared context by default or explicit outward restrictions (DEC02–DEC03). |
| **Task** | Objective, success conditions, inputs, status, policy, budget, checkpoints and results. |
| **Run/operation** | Execution attempt, worker/device, observed outcome, retry identity and resource consumption. |
| **Artifact** | Stable output identity, version, format, lineage and access/retention controls. |
| **Source reference/snapshot** | Authoritative origin, identity, version/fingerprint, retrieval time and permission context. |
| **Memory** | Scoped attributable information with provenance, correction, removal and freshness handling (DEC08). |
| **Device/connector** | Identity, capability scope, availability, authorisation and protocol version. |
| **Grant/approval** | Who authorised what, for which purpose/scope and validity conditions. |

Apart from the approved product semantics cited above, this decomposition is Proposed. Concepts need not map one-to-one to tables, but their responsibilities must not disappear into a transcript or ephemeral model context.

Memory is not an authoritative substitute for exact files or current task state. Distinguish user-provided facts, preferences, source-derived information and agent inferences. Users can correct/remove memory; old summaries must not silently override newer evidence.

### 7.2 Access, copy, sync, and move are different — Proposed

**Access** retrieves when needed. **Copy** creates a retained instance. **Sync** maintains replicas. **Move** changes location and may delete the source. None should be silently substituted for another.

Cross-device moves need destination and source-deletion authority. Verify destination integrity before deleting originals; preserve actionable partial-transfer state on failure.

An imported snapshot is not automatically authoritative. Check the expected version before writeback and surface conflicts instead of overwriting changes. Prefer an authorised service connector when it reaches the actual source without unnecessary device dependence.

## 8. Availability and reliable execution

**Persistent** means state survives. **Available** means the service can accept, expose and coordinate work. **Autonomous** means authorised work can continue without another interaction. None promises infinite compute or always-online endpoints.

**Approved product decisions (DEC04–DEC06):** every accepted request, including ordinary questions, survives client closure. Acceptance means durable recording and tracking, not guaranteed success or immediate readiness. Client closure is not cancellation. Material ambiguity permits independent progress, then a saved waiting state and a question to the user. Partial output is not successful completion.

**Approved product decision (DEC09):** notify users when capacity interrupts work, preserve progress and provide a control to prevent auto-resume on that halted task. Otherwise resume when ordinary capacity is available after rechecking currency, permissions and input freshness. Extra charges require existing authority. Notification channels and failed-recheck behaviour remain design choices.

**Proposed implementation constraints:** use durable orchestration and bounded workers; persist waiting/approval/resource states without inference loops. Distinguish queued, running, waiting for input/approval/resource, paused for budget/policy, completed, failed and cancelled. Reconcile uncertain external outcomes rather than falsely marking success/failure.

Record stable operation identities and reconcile effects before retrying. Do not assume exactly-once delivery or duplicate sends, writes or destructive actions. Recheck authority/freshness on delayed resumption. The accepted product behaviour does not select a database schema, queue protocol or SSE contract.

Device inventory should distinguish connected, sleeping/unreachable, revoked and unsupported capability. Pairing does not imply wake-on-LAN. Cached data requires permitted retention and acceptable freshness; identify snapshots as such.

Maintain a user-visible record of sources, operations, approvals, artifacts and blockers. This is an evidence trail, not hidden model reasoning or indiscriminate sensitive telemetry.

## 9. Platform boundaries and verification gates

The promise is **the same Daemon everywhere, extended by supported platform capabilities**, not identical remote-control powers on every OS.

| Platform consideration | Verified constraint or proposed gate |
| --- | --- |
| **iOS/iPadOS resource access** | Apps are sandboxed; access outside the container uses system services. Pairing does not grant arbitrary OS/other-app access. [S2](#s2) |
| **iOS background availability** | No unrestricted continuous background execution or guaranteed arbitrary wakeups. Specific APIs, including continued processing, do not establish an always-running general agent. [S3](#s3) |
| **Android file access** | SAF supports user-selected resources and persistable URI permissions, subject to restrictions and availability; not arbitrary other-app private data. [S4](#s4) |
| **Google Play autonomous UI control** | AccessibilityService policy prohibits autonomous initiation/planning/execution, with a narrow verified disability-assistance exception; deterministic rule-based automation is distinguished. A general assistant does not automatically qualify. This is not a blanket prohibition on all Android APIs. [S5](#s5) |
| **Desktop companions** | Proposed gate: validate per-OS permission behaviour, packaging, background operation, signing/updates and containment before promises. No OS support is certified here. |
| **Managed/work devices** | Proposed gate: legitimate organisational authorisation; pairing must not bypass employer controls. |

The mobile client's complete role is access to the hosted product plus supported local inputs, approvals, notifications and integrations. It need not be a universal unattended worker.

Sources were checked on **26 September 2026**. They are design inputs, not store-approval guarantees. Recheck exact OS/API/channel/policy before release. Sideloading is a separate product/security decision, not an assumed workaround.

## 10. Non-goals and expansion boundaries — Proposed

The initial product does not require a general-purpose VPN, whole-device continuous sync, unrestricted remote administration, a new OS, or universal UI automation.

It does not require a permanently allocated VM or active frontier model per user, identical device capabilities, or all account information in one context window.

Self-hosting product support, local inference, bring-your-own infrastructure, collaboration, device-to-device execution and advanced scheduling may be extensions. They are not prerequisites for the core cloud-first workflow. Existing self-hosted deployment support does not make user administration a hosted prerequisite.

“No chat/work divide” does not require elaborate workflows for every request. “Always available” does not guarantee access during outages or to offline devices. Claims must match supported service/resource availability.

## 11. Delivery sequence and acceptance criteria

These are capability gates, not dates or instructions to replace working implementations. Satisfy existing gates through evidence where code already supports them. Apart from the selected Stage 1 scope (DEC10), delivery mechanisms and acceptance tests remain Proposed.

### Stage 0 — Reconcile the vision with the repository

Inspect architecture, persistence, tasks, permissions, clients, connectors and tests. Map V/D IDs to code and classify implemented, partial, absent or unverified. See [the dated assessment](DAEMON_RECONCILIATION.md). Identify the next increment and decisions it genuinely requires.

### Stage 1 — Prove broad hosted continuity

**Approved product decision (DEC10):** ordinary questions, research and existing supported tools are the first continuity milestone, rather than one document workflow. Accept requests durably, close the client, and later review truthful status and saved results elsewhere. No paired computer is required. Preserve correct outcomes and boundaries through client/worker interruption, clarification and capacity pauses.

**Proposed delivery evidence:** name the supported operation set and test each operation's recovery class. A narrow CSV/DOCX workflow can be a test case but does not complete this milestone. Retired/unqualified tools do not become supported through this decision. Define input/output workflows and real cross-client/restart checks; no automatic replay of uncertain external effects. See [the decision draft](DURABLE_REQUEST_DESIGN.md) before schema/API changes.

### Stage 2 — Prove optional device extension

Support one desktop platform with explicit enrolment, selected-folder access, separately authorised hosted processing and visible revocation. Complete the reference presentation workflow. Presentation authoring and rendering validation are explicit dependencies, not assumed consequences of an existing file generator. On-demand reading is sufficient; broad local execution and continuous sync are not prerequisites.

### Stage 3 — Expand actions under tested boundaries

Add output destinations, conflict-safe writeback, service integrations and specific local operations. General execution requires an explicit containment/approval review. Expand platforms based on verified APIs and demonstrated workflows.

### Acceptance catalogue

| ID | Test | Observable pass condition |
| --- | --- | --- |
| **AC01** | Phone-only completion | A new user produces/reopens a useful persistent artifact without another enrolled device. |
| **AC02** | No mode silo | Discuss input, create and revise an artifact with relevant context without manual export between modes. |
| **AC03** | Cross-client continuity | Closing/reopening elsewhere exposes the same accepted work, status, inputs, approvals and saved result, including ordinary requests. |
| **AC04** | Optional enrolment | Sign-in grants no local access; enrolment exposes only approved capabilities; phone replacement preserves device identity. |
| **AC05** | Reference workflow | Approved laptop documents yield a validated editable presentation reviewable on the phone, with provenance and unchanged originals. |
| **AC06** | Scope/injection resistance | Out-of-scope requests and embedded instructions cannot expand access, invoke ungranted tools or redirect data. |
| **AC07** | Data-policy routing | Restricted inputs are never silently sent to unapproved processors, including fallback/delegation. |
| **AC08** | Revocation/offline handling | Offline work waits truthfully; revoked/expired authority cannot outlive the defined bound; snapshots are identified. |
| **AC09** | Recovery/deduplication | Worker restart, connection loss and replay preserve committed results without duplicate material effects; uncertain effects are reconciled. |
| **AC10** | Conflict-safe writes/moves | Changed originals are not overwritten silently; partial transfers do not prematurely delete sources; outcomes are recorded. |
| **AC11** | Isolation/information removal | Account/tenant isolation and source restrictions hold; unrestricted projects can share permitted context, while restricted-project information cannot leave without explicit permission. Memory/copy removal follows documented lifecycle/backup policy. |
| **AC12** | Bounded compute/autonomy | Waiting does not loop inference; capacity pauses preserve state and notify users; disabled auto-resume is respected; enabled resumption rechecks scope/freshness and stays within task mandate. |

Stage 1 covers AC01–03 and applicable AC06–07/09/11–12; Stage 2 adds AC04–05/08 and cross-device variants; Stage 3 adds AC10 and action-specific variants. A stage cannot defer controls its own features require. Account/resource isolation is required for the initial supported scope; restricted projects must not be advertised before enforcement is verified.

Use automated tests and reproducible end-to-end checks, recording environments and actual outcomes. “The model said it completed” is not completion evidence.

## 12. Economics and product validation — Proposed

Charge primarily for capacity: inference budget/quality, workload size, concurrency, storage, execution time and bounded autonomous work. Persistence does not imply continuously provisioned compute.

Free/trial use should demonstrate a real outcome with honest limits and common privacy/continuity principles. Do not sell artificial forgetting or mode fragmentation. Storage, retention and availability entitlements still require sustainable definitions.

Preserve existing commercial architecture rather than treating plan names, the independent trial or accounting controls as undecided. Launch prices, quota sizing, task-level economics, billing integration and live provider qualification remain separate decisions. More capacity never confers wider device permissions.

Measure completed useful outcomes: phone-only completion, time to useful result, cross-client recovery, resource success, intervention burden, cost including storage/transfer/retries, and unauthorised-operation tests. Validate willingness to return/pay; device count or pairing demos alone do not prove value. Set numeric targets from measured baselines.

## 13. Open decisions

| ID | Decision | Evidence needed / timing |
| --- | --- | --- |
| **O01** | Companion transport | Threat model, connectivity, licensing/distribution and cost before implementation. |
| **O02** | Companion identity, QR, keys, recovery and revocation | Dedicated security design; existing client-session auth is groundwork, not companion grants. |
| **O03** | Supported platforms/packaging | APIs, distribution, demand and maintenance before commitments. |
| **O04** | Project/context enforcement | Product defaults resolved by DEC01–DEC03; representation, restrictions on existing derivatives and controlled sharing remain open. |
| **O05** | Storage, retention, export, deletion and backups | Upload/memory defaults resolved by DEC07–DEC08; lifecycle details and costs remain open. |
| **O06** | Hosted/local containment | Actual runtime guarantees, threat model, credential/egress controls and tests before relevant execution ships. |
| **O07** | Provider/data-policy operation | Preserve current provider contract; verify deployment qualification, processing locations and resource-specific restrictions. |
| **O08** | Snapshots, caching, indexing, sync and writeback | DEC07 defines hosted upload retention intent; operational consent/conflict/revocation semantics still need design. |
| **O09** | Task budgets, launch economics and abuse controls | Preserve current plans/accounting. DEC09 sets pause/resume UX; task-level limits, sustainable allowances and billing integration remain open. |
| **O10** | Task/recovery/service guarantees | DEC04–DEC06/09–DEC10 establish product behaviour; acceptance transactions, APIs, retries, cancellation bounds, notification channels and service objectives require design/testing. |
| **O11** | Collaboration, self-hosting product support, local models and proactive work | Separate product/security/operating case; not initial prerequisites. |

Resolve only decisions needed by the next increment. Do not block hosted progress on speculative companions or silently approve open architecture.

## 14. Guidance for future agents and maintainers

1. Read repository instructions, this vision's labels and [the interview decisions](DAEMON_VISION_DECISIONS.md).
2. Inspect current implementation/tests and existing architecture; do not infer implemented capability from intent.
3. Reference V/D/AC/DEC IDs and any O decision a change would settle. Surface conflicts instead of overriding contracts.
4. Prefer the smallest coherent improvement toward broad continuity. Do not rewrite unrelated systems or add dependencies to resemble a diagram.
5. Explain authority, dataflow, retry/failure, persistence and cost implications with proportional evidence.
6. Report changes, verification, remaining unknowns and any required architecture approval.

The vision is not authority to access devices, send data, deploy or expand permissions. Follow the task mandate and repository rules.

### Repository integration and change control

This is the product-direction reference at `docs/DAEMON_VISION.md`; links belong in the documentation index and agent instructions, not copies of the whole document. Keep schemas, protocols, threat models and resolved tradeoffs in linked design/decision records.

Preserve IDs and mark superseded entries rather than renumbering. Record changes and reasons. Promote proposals only with explicit approval; generated plans are not approval. Recheck dependent platform/provider claims at design/release. Implementation claims require actual code/test/issue/release evidence. Update date/changelog for material changes.

## 15. Sources and provenance

**Product provenance:** the product owner's originating discussion on 26 September 2026 established V01–V07. The follow-up interview established [DEC01–DEC10](DAEMON_VISION_DECISIONS.md). Other architecture and acceptance framing remains Proposed unless separately approved. The dated reconciliation records implementation evidence separately.

**External verification:** the primary sources below were checked on 26 September 2026. They support the cited constraints, not the entire architecture or release approval. Policies/APIs can change.

<a id="s1"></a>
**S1 — Tailscale: Control and data planes.** Coordination responsibilities and encrypted device data plane; reports validation on 5 January 2026.

<https://tailscale.com/docs/concepts/control-data-planes>

<a id="s2"></a>
**S2 — Apple Platform Security: Security of runtime process in iOS, iPadOS, and visionOS.** Sandbox, entitlements and system services; published 19 December 2024.

<https://support.apple.com/guide/security/security-of-runtime-process-sec15bfe098e/web>

<a id="s3"></a>
**S3 — Apple Developer Technical Support: iOS Background Execution Limits.** Limits and supported mechanisms; change history includes 9 January 2026.

<https://developer.apple.com/forums/thread/685525>

<a id="s4"></a>
**S4 — Android Developers: Access documents and other files from shared storage.** SAF scope, persistable URI permissions and restrictions; reports update on 16 September 2026.

<https://developer.android.com/training/data-storage/shared/documents-files>

<a id="s5"></a>
**S5 — Google Play Console Help: Use of the AccessibilityService API.** Autonomous-action restrictions, deterministic automation and verified accessibility-tool exception; no independent update date established.

<https://support.google.com/googleplay/android-developer/answer/10964491?hl=en>

## 16. Changelog

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | 2026-09-26 | Initial external discussion-derived draft; implementation not assessed. |
| 0.2 | 2026-09-26 | Repository integration after reconciliation/review/interview. Adds approved product decisions and shared-by-default projects, durable acceptance of all requests, retained uploads/scoped memory, controllable auto-resume and broad Stage 1 continuity. Preserves V/D/AC/O IDs and existing architecture authority; no claim of implemented continuity. |
