# Self-hosted web-reader fallback — design and isolated pilot

Date: 2026-09-30. Related incident: [#373](https://github.com/sol-aeternum/Daemon/issues/373).

**Status: ordinary/basic-stealth offline sandbox checks pass; live gateway remains gated.**
On 2026-10-01 the owner approved the recommended sandbox-compatible dedicated
runtime and isolated gateway pilot, including the pilot-only 45-second deadline,
32 MiB network-transfer ceiling and 1 GiB memory ceiling. This is an experimental
resource-contract exception, not a change to production's 2 MiB decoded-response
limit. The owner had previously selected the self-hosted design and offline pilot. This
does not authorize production fallback activation, new permanent services,
provider processing, paid proxies, login/paywall bypass, or CAPTCHA-solving
services. The earlier bounded supervisory review covered only offline checks;
the actual live boundary and sandbox-compatible setup need review and executable
containment evidence before any live URL. A successful offline test is not a
successful fetch of a challenged website.

## Evidence and governing contracts

- The reported OpenAI URL returned HTTP 403 with `cf-mitigated=challenge` through
  Daemon's direct transport. This identifies a challenge, not whether the article
  exists. [Cloudflare documents this header](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/).
- `orchestrator/services/fetch/service.py` enables only the direct strategy.
  The retained Crawl4AI adapter is disabled, and must not be enabled unchanged.
- The existing Compose crawler shares `daemon-network` with application and
  database services. The retained adapter lacks browser-subrequest containment
  and bounded response parsing. The installed browser configuration also defaults
  to ignoring certificate errors; a new reader must explicitly retain TLS
  verification. See `docker-compose.yml` and
  `orchestrator/services/fetch/strategies/crawl4ai.py`.
- The existing snapshot contract still governs:
  [CHUNKED_WEB_READING_DESIGN.md](CHUNKED_WEB_READING_DESIGN.md), including the
  stream-enforced 2 MiB decoded-response limit, 1 MiB extracted-text limit,
  encrypted account/conversation storage, immutable offsets and cache bypass.
- DEC06 requires waiting on material choices; AC06/AC07 require scope and
  data-policy enforcement. See [DAEMON_VISION.md](DAEMON_VISION.md).

## Candidate selection

Start with self-hosted Crawl4AI and compare ordinary Playwright, its basic
stealth adapter, and its installed Patchright-backed undetected adapter. This
reuses available packages without adding a dependency or assuming that the
installed browser binaries are compatible. Pin the observed image ID for the
pilot, never pull the mutable `latest` tag to repair a failure.

[Crawl4AI's documentation](https://docs.crawl4ai.com/advanced/undetected-browser/)
distinguishes basic fingerprint patches from undetected mode and explicitly
disclaims guaranteed access. Its current public documentation is newer than the
installed image, so installed source/API evidence governs pilot compatibility.
[Scrapling](https://scrapling.readthedocs.io/en/latest/fetching/stealthy.html)
currently uses Patchright by default, but adds another framework and features
outside this pilot's approved scope. Camoufox remains an alternative with a
[production-stability warning](https://github.com/daijro/camoufox).

TLS impersonation alone does not execute page JavaScript;
[curl_cffi](https://curl-cffi.readthedocs.io/en/latest/faq.html) is not equivalent
to a browser. [Jina Reader](https://jina.ai/reader) expressly says it does not
actively bypass anti-bot defenses. Hosted readers need separate processing,
retention and budget approval; none is selected here. No evidence establishes
that residential proxies are necessary for the reported source.

## Stage A: approved offline compatibility pilot

Use a separately owned disposable container, not the production crawler:

- Exact existing local image ID; entrypoint replaced with the synthetic fixture.
- Network mode `none`, non-root, read-only root, all capabilities dropped and
  no-new-privileges. No privileged mode, published ports, production environment,
  host mounts, Docker socket, credentials or user browser profiles.
- Size-bounded tmpfs for writable profiles/temp/cache; installed browser binaries
  remain read-only. Resource limits: 1 GiB container memory, 1 CPU, 128 PIDs,
  256 MiB shared memory, 128 MiB temporary space and a 90-second host deadline.
- Inspect actual Docker settings before launch and after execution. Verify
  loopback-only interfaces, no external route and bounded failed public/private
  numeric-IP connection probes from inside the namespace. No veth packet-capture
  claim is applicable to network mode `none`.
- Clear inherited image environment before running fixture/browser children.
  Disable service workers and downloads; retain certificate verification.
  Request Chromium's browser sandbox and record its outcome separately from
  Docker containment. Do not disable security checks to turn a launch failure
  into a pass.
- Render only `about:blank` and synthetic HTML. Validate title and article text
  independently of launch success; record each mode's errors and version.
- Capture complete bounded diagnostic messages, close owned browser processes,
  stop an owned timed-out container, and remove only that container. Retain
  credential-free evidence outside it. Never clean unfamiliar artifacts.

Earlier smoke checks inside the unused existing service proved ordinary Chromium
could render a synthetic article; a raw Patchright launch failed with
`TargetClosedError`. Neither result establishes this stricter boundary's
compatibility or the ability to fetch the reported URL.

## Stage B: selected gateway pilot — review and containment evidence required

### Option 1: public-only gateway (selected for the bounded live experiment)

The original browser/internal-bridge layout is superseded by the owner-approved
pipe transport variant below (2026-10-01). The browser retains `network=none`;
only a separate policy gateway has external connectivity. Approval covers
detailed design and review of this variant, not production deployment. Actual
implementation and live setup require the review and containment gates below.
No Compose services or host firewall rules are changed by this design.

Required enforcement:

- Fixed, small public-origin allowlist for the pilot, including necessary source
  subresources selected explicitly rather than all discovered origins.
- Normalize and validate authorities/ports; reject ambiguous encodings,
  userinfo, unsupported schemes and malformed CONNECT targets. HTTP forwarding
  and HTTPS CONNECT both obey destination policy.
- Resolve and validate each connection, then pin the actual socket to an approved
  public IP. Reject private/link-local/metadata/reserved ranges and deployment-owned
  public addresses. Cover redirects and all subresource connections.
- Prevent alternate egress via direct IPv4/IPv6, DNS, UDP/QUIC, WebRTC or another
  interface. The browser may access only the gateway's proxy interface, not its
  management interface. Validate containment at the network layer, not only
  through Playwright interception.
- Preserve browser TLS certificate verification; no TLS interception or custom
  trust root. Use fresh per-run cookies/cache and pass no user credentials.
- Proposed pilot-only ceilings: one browser at a time, 45-second run deadline,
  40 gateway connection admissions, four simultaneous connections and 32 MiB
  total network transfer per run, plus Stage A's process memory/PID limits.
  Count accepted HTTP forwarding/CONNECT connection admissions, not requests
  inside reused encrypted tunnels. The gateway cannot count those requests;
  no per-resource/request-count guarantee is claimed for this proposal.
  The concrete IPC design below supersedes successful-admission counting with
  the stricter ceiling of 40 OPEN attempts, including refused attempts.

**Approved pilot-only resource-contract exception:** CONNECT sees encrypted bytes, not
decompressed response bodies. Network-transfer, memory or final-DOM limits do
not enforce the existing streamed 2 MiB decoded-response ceiling. This option
uses the owner-approved pilot limits above, but still requires evidence of their
enforcement. It does not qualify the reader for production integration or change
the snapshot contract outside the isolated experiment.

### Option 2: network-disabled browser with bounded HTTP response relay

Keep the browser offline and mediate page resource requests through a separately
validated/pinned HTTP reader, supplying bounded responses over a controlled IPC
channel. This could retain direct transport's decoded-response ceiling without
TLS interception. It requires its own design/review: routing completeness,
cross-origin/cookie/redirect semantics, encoded-body handling and cancellation
must be correct. It also retains the direct HTTP client's TLS fingerprint and
may fail to reproduce browser challenge behavior. Not implemented or approved.

No architecture choice silently relaxes the existing snapshot bounds. If neither
option can meet its approved limits, stop with a truthful blocked result.

## Future persistent execution environment — proposed migration path

The owner asked whether the reader would migrate to Daemon's future persistent
sandbox/environment. The intended integration seam is a bounded reader executor,
not a dependency on one disposable container or browser profile. A long-lived
execution host or worker pool could replace the pilot launcher while retaining
short-lived, isolated per-fetch browser contexts. Persistent execution
infrastructure must not imply shared account cookies, unrestricted egress or
permission to persist every page. Saved source snapshots retain Daemon's existing
account/conversation storage and lifecycle independently of the executor.

This is a migration proposal, not an approved persistent-environment architecture
or a claim that it exists. Any future executor must be requalified for scope,
network/resource enforcement, cancellation and account isolation before routing
production reads to it. No durable-task schema, API or sandbox service is added
by this pilot.

## Qualification and failure criteria

Before any live experiment: approve the selected concrete architecture and its
resource contract, review actual setup/code independently, and verify containment
against controlled public/private/IPv6/metadata/DNS-rebinding test destinations.
Keep probes confined to owned fixtures; do not test internal production services.

For future production integration, keep direct fetch first and allow at most one
eligible bounded fallback. Do not automatically escalate on authentication denial,
404, policy refusal or exhausted budgets. Classify failures without raw exception
or response leakage. A challenge, login screen, paywall or empty result must not
be saved as article success. Verify original/final URL provenance and extraction
version; enforce extracted UTF-8 bounds before encrypted snapshot persistence.
No browser session sharing across accounts or plaintext page-cache shadow.

Test cancellation, deadline, oversize/decompression, redirect/subresource policy
refusal, duplicate admission and context-budget failures as well as happy paths.
Keep browser work bounded within account compute/capability policy; no paid
processor or unqualified service fallback. Existing backend/repo gates and fresh
independent review are required before production code is declared complete.

## Pilot evidence

The 2026-09-30 offline run used the exact local image
`sha256:4d8b065bf185962733cb5f9701f4122d03383fa1ab6b5f6a9873f04fa0416a84`
in the separately owned container `daemon-reader-offline-6b50572cbc7b`.
Pre-start and final Docker configuration checks passed. Runtime evidence showed
UID 999, only loopback, no IPv4 external route, a minimal six-key environment,
and failed numeric-IP connection probes to public/private/link-local targets.
The container exited without OOM and was removed successfully.

| Check | Observed result |
| --- | --- |
| Offline network/container boundary | Passed the inspected configuration and namespace/probe checks |
| Ordinary Playwright | Launch failed: `Chromium sandboxing failed` / `No usable sandbox!` |
| Basic stealth | Launch failed at the same precondition; stealth application was never reached |
| Patchright | Launch failed at the same precondition; article fixture was never reached |
| Live source reading / Cloudflare handling | Not attempted |

The harness completed normally; that exit code does **not** mean a browser case
passed. All three case records explicitly report failure. No package/browser
installation, privileged mode, sandbox disabling, network expansion or host
configuration change was used to repair the failure. The lower-level cause
(kernel/container namespace restrictions, security profiles or browser-image
configuration) has not been discriminated. This is evidence that the tested
image/environment does not meet the selected sandboxed-launch precondition,
not that Patchright or stealth is ineffective on the target site. It does not
establish the cause of the earlier default-config Patchright launch failure.

Read-only host inventory found unprivileged user namespaces enabled
(`kernel.unprivileged_userns_clone=1`, `user.max_user_namespaces=62670`) and
Docker's builtin seccomp policy active; no AppArmor restriction setting/profile
was observed in the inspected paths. This narrows the investigation but does not
prove which syscall or image requirement caused the failure. A sandbox-compatible
runtime with a narrowly reviewed namespace/security-profile setup is the next
design step, not permission to disable seccomp, add broad capabilities or change
global host settings.

Credential-free fixture, runner and complete bounded diagnostics are retained
under `/home/sol/.cache/daemon-fetch-pilot-20260930/`, with the run artifact named
`daemon-reader-offline-6b50572cbc7b.json`. A fresh read-only `review-go` checked
the concrete harness before execution; its evidence-retention, capability and
extraction-normalization findings were repaired and checked in one focused
follow-up. The reviewer did not run tests. Browser sandbox compatibility remains
blocked. The owner subsequently approved the gateway pilot/resource exception;
concrete live containment and sandbox setup remain under safety review. No
production fallback implementation or live-access claim is established by this
document.

### 2026-10-01 bounded runtime diagnosis

The approved pilot's sandbox review used the same network-disabled disposable
container boundary. Raw syscall probes with the installed Docker default showed
`unshare(CLONE_NEWUSER)` and `unshare(USER|PID|NET)` denied with `EPERM`.

An independently reviewed experiment compared a pinned upstream Moby reference
profile against that profile plus exact x86-64 namespace permissions. Reference:
`moby/profiles@2ceae35d351c156cb5a8efc0fdc4a08cf94569d8`, `seccomp/default.json`,
SHA-256 `6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`.
This reference is **not certified equivalent to the installed Docker builtin**.
All reference rules were retained; six rules allowed only the tested exact
`clone` USER/optional PID/NET combinations and two exact `unshare` combinations.
The A/B behaved as predicted: selected calls succeeded only with added rules,
while USER+MOUNT namespace creation and `setns` remained denied.

The first browser check under that six-rule profile progressed to an explicit
`sys_chroot("/proc/self/fdinfo/")` failure. That observed result disproved the
reviewer's initial assumption that this Chromium namespace-sandbox path did not
need `chroot`. One reviewed repair added the `chroot` syscall without granting
outer capabilities. Parent `chroot` remained denied with `EPERM`; a newly created
and UID/GID-mapped child user namespace could enter a synthetic filesystem jail.
No host profile, global sysctl, production container, dependency or image changed.

**Browser verification remains blocked:** the final ordinary, basic-stealth and
Patchright cases each hit the 20-second case deadline before returning a successful
case or `chrome://sandbox` diagnostic. The fixture does not establish which
awaited operation timed out; successful startup or sandbox enforcement must not
be inferred from successful syscall probes. No additional rule widening or live
browsing followed this single repair. All owned diagnostic containers were removed.

Evidence is retained in `/home/sol/.cache/daemon-fetch-namespace-probe-20261001/`:

- `daemon-reader-offline-b03d9b7043ed.json`: installed-default unshare probes.
- `daemon-reader-offline-1ec67f16e324-baseline.json`: pinned-reference negative leg.
- `daemon-reader-offline-6370efe01e97-sandbox-profile.json`: six-rule positive/negative leg.
- `browser/daemon-reader-offline-c4af5a5c2d40.json`: explicit Chromium chroot failure.
- `daemon-reader-offline-e8dace19fd3f-sandbox-profile.json`: repaired syscall checks.
- `browser/daemon-reader-offline-cd3c7375fba2.json`: final browser timeouts.

Fresh read-only review-go session `ses_f0b3a585dffecuWQjLGxbM2IrP` reviewed the
exact profile experiment and evidence-led chroot repair; the primary executed and
inspected the evidence. The final experimental profile is not approved for live
or production use by these checks. Runtime verification and concrete gateway
containment remain unresolved; the intervention stays blocked rather than
handing unverified live execution back as authorized. Next investigation must
discriminate launch, context/page initialization and internal-diagnostic waits
under the same containment before proposing any further security-boundary change.

### Stage-localized follow-up (2026-10-01)

Under the unchanged seven-rule experimental profile, an ordinary-Playwright
fixture with per-stage deadlines confirmed successful driver start, browser
launch (Chromium `143.0.7499.4`), context creation and request-route installation.
`context.new_page()` failed before synthetic content or sandbox diagnostics.
Enabling Playwright's browser debug output exposed `Zygote could not fork:
process_type utility ... child_pid -1`, missing zygote-child pings, and a browser
`SIGSEGV`. The failure is therefore localized to browser child-process/page
initialization, rather than remote navigation or extraction. This follow-up
tested ordinary Playwright only; it does not independently localize the earlier
basic-stealth or Patchright timeouts.

Artifacts in the same evidence directory:
`diagnostic/daemon-reader-offline-6904fa3f0648.json` (stage timing) and
`diagnostic/daemon-reader-offline-82d7948e0a04.json` (browser debug output).
Both owned containers were removed. No permissions, resource limits, network
access or production configuration were changed. The failed fork's exact syscall,
errno and cause remain unproven; the crash does not justify another speculative
syscall allowance. Browser sandbox qualification and live gateway execution
remain blocked. A separately scoped syscall-level diagnosis or owner-approved
replacement runtime is required before further repair.

### Nested PID diagnosis and bounded repair (2026-10-01)

After the owner requested continuation of syscall diagnosis, the primary inspected
Chromium's matching `143.0.7499.4` source: `content/zygote/zygote_linux.cc:430–436`
calls `NamespaceSandbox::ForkInNewPidNamespace`, whose implementation in
`sandbox/linux/services/namespace_sandbox.cc:217–229` uses
`clone(CLONE_NEWPID | SIGCHLD)` and drops child capabilities. The experimental
profile omitted this nested-PID operation. An unchanged-profile synthetic probe
reproduced `EPERM` for that exact call inside a mapped user namespace while plain
fork succeeded. This is source-backed reproduction, not a Chromium syscall trace
(`strace` was absent and no dependency was installed).

Fresh review-go session `ses_f0aabe667ffeF3FXpfwVGLCoYR` reviewed one exact additional
x86-64 rule: `clone` argument 0 equals `0x20000011`, `SCMP_ACT_ALLOW`.
The primary compared the generated profile against the previously executed
profile and verified that this was the only delta. The resulting eight-rule
extension has SHA-256
`ec97bb9f172a136a19a3af5eb0f6ed1236476e015e6c2966196f4a85bd9f1915`.
Its positive/negative probe passed: nested PID creation succeeds inside the new
user namespace, but the outer process still receives `EPERM`. Tested mount
namespace combinations, `setns`, and outer-process `chroot` remain denied.
No outer capabilities, global policy changes or network access were added.

**Ordinary and basic-stealth offline runtime checks now pass for this exact
image/profile.** Both extracted the expected synthetic article and returned
Chromium's `chrome://sandbox` diagnostics: namespace sandbox, PID/network
namespaces, Seccomp-BPF and TSYNC enabled. Yama ptrace protection reports broker
`Yes`, non-broker `No`. These are Chromium self-diagnostics plus the external
container/syscall checks, not a comprehensive independent kernel audit.
The ordinary stage-timed fixture also passed all cleanup stages.

**Patchright remains partially verified:** the combined fixture timed out waiting
for `chrome://sandbox` load after its synthetic-content operations. No successful
sandbox diagnostic or completed case was emitted for that mode. No live source
or stealth effectiveness has been tested. All owned containers were removed.

New evidence under the same external directory:

- `nested-pid/daemon-reader-offline-6fd7bb0fcdc5.json`: exact nested-PID denial.
- `nested-pid/daemon-reader-offline-ba08c587adc2.json`: corrected positive/negative checks.
- `daemon-reader-offline-13cdaf370841-sandbox-profile.json`: retained negative suite.
- `diagnostic/daemon-reader-offline-d66c6135adc5.json`: ordinary stage and sandbox success.
- `browser/daemon-reader-offline-97e56cbd70d8.json`: three-mode results, SHA-256
  `fe2cfa4bc9630fe3d6c940f5af48c114224f9e15eaa3bce853ea2272b486636a`.

Reviewer access to the external cache was denied, so the review assessed the
supplied exact rule and evidence excerpts; the primary inspected and checked the
actual artifacts. The primary corrected the reviewer's mount-flag typo to
`0x20020011` and does not accept its claim that mount denial was demonstrated to
be kernel-originated: the retained seccomp rule also denies that combination.
Successful checks resolve the tested ordinary/basic runtime blocker, not live
gateway containment or production readiness.

### Gateway transport variant — approved for detailed design and review

Recommend retaining the browser's `network=none` boundary and carrying a
loopback CONNECT proxy's opaque tunnel bytes over bounded, task-scoped process
IPC to a separate public-only gateway. This avoids attaching the browser to a
host bridge or Docker's embedded DNS. It requires a concrete reviewed design for
IPC framing, backpressure, cancellation, gateway DNS/IP pinning and limits before
implementation/live execution; no host socket or filesystem mount is implied.

This is the selected transport variant of the CONNECT gateway, **not** Option 2's
bounded HTTP-response relay: browser TLS remains end-to-end, and the approved
experimental byte/memory/time limits still do not enforce a per-response decoded
2 MiB cap. On 2026-10-01 the owner explicitly approved this variant for detailed
design and review. Production remains direct-only.

## Concrete IPC gateway design (2026-10-01)

**Design specification, not implemented.** The following defines the selected
pilot's implementation boundaries. Security-sensitive executable code receives
fresh review before execution; live navigation additionally requires passing the
fixture gates. No application API, SSE event, schema, environment surface or
production fallback adapter changes in this pilot.

### Processes and trust boundary

1. A task-owned supervisor launches two disposable, labelled containers via the
   existing Docker CLI. Only the trusted supervisor has access to Docker control.
   Neither child inherits that socket, host mounts, application secrets, proxy
   environment variables or application/database networks. No TTY, published
   ports, restart policy or persistent volumes. Use inspected immutable image
   IDs; the existing image may supply Python for both roles without starting its
   server entrypoint. New images/dependencies require separate approval.
2. The browser container retains the tested network-none/non-root/read-only/
   capability-drop/no-new-privileges boundary and exact qualified seccomp profile.
   A small local relay binds only `127.0.0.1` on an ephemeral port. Chromium uses
   that explicit HTTP proxy for HTTPS CONNECT; automatic proxy bypass is disabled.
   Any bypass attempt has no external network route. No remote-debugging TCP port
   is opened. Service workers and downloads remain disabled; fresh context per run.
   One Python entrypoint owns the attached stdin/stdout and runs the relay and
   browser-controller tasks in one event loop, launching Playwright/Chromium with
   their own private driver pipes. Only the entrypoint writes framed stdout;
   browser/driver diagnostics go to bounded stderr, never protocol output.
3. The gateway container has a fresh, dedicated egress network, no inbound listener
   and no peer containers. It uses the baseline seccomp profile, all capabilities
   dropped, non-root, read-only root, no-new-privileges, bounded scratch space and
   scrubbed environment. Its only input is framed stdin; stdout is framed output.
   Unlike the browser it necessarily has network capability. Destination
   enforcement in its small trusted parser/resolver/dialer is part of the security
   boundary; Docker bridge isolation alone does not prove gateway SSRF protection.
   No HTML, JavaScript, extraction or user-supplied code runs in this container.
   Initial gateway egress is IPv4-only on a dedicated Docker network with IPv6
   disabled; IPv6 dialing is prohibited even for public answers. Still validate
   all DNS answers and test IPv6 denial. Preflight inventory covers host/deployment
   IPv4 addresses and IPv6 addresses/prefixes (including absence of IPv6 routing),
   and must include explicitly supplied deployment-owned public addresses rather
   than assuming interface inspection discovers external NAT/load-balancer IPs.
   Future dual-stack egress requires a reviewed design revision.
4. The supervisor passes only validated frames between dedicated child pipes.
   Child stderr is drained into a 64 KiB aggregate diagnostic ring, never interpreted as
   protocol or commands. Bound Docker attach buffers too: the previous
   `subprocess.run(capture_output=True)` runner is unsuitable for live IPC.
   Fixed argument vectors only, no shell interpolation. Artifacts record exact
   profiles/images and configuration; startup failure never falls back to a
   less isolated launch. The gateway's external interface is attached only after
   reviewed implementation and offline fixture gates pass.
   Ring overflow drops oldest diagnostic bytes and records a truncation counter;
   it must not stop draining the pipes or allocate additional retained storage.

### CONNECT and destination policy

- HTTPS-only initial pilot, port 443 only; no HTTP forwarding, SOCKS, UDP, proxy
  chaining, TLS interception, custom roots or ambient credentials. Non-HTTPS
  redirects fail closed. TLS remains browser-to-origin with normal verification.
- A run manifest fixes exact canonical ASCII DNS hostnames in advance, including
  any necessary subresource hosts. No suffix wildcards or discovered-host auto-add.
  Initial manifest may contain only the reported `openai.com` source; blocked
  third-party subresources are reported rather than silently admitted.
- The local relay accepts only bounded HTTP/1.1 CONNECT headers (8 KiB maximum,
  2-second header deadline), strict `hostname:443` authority and a single matching
  Host header. Reject userinfo, IP literals, control/whitespace tricks, percent
  encodings, trailing dots, non-ASCII, paths, conflicting framing/headers, request
  bodies and non-CONNECT methods. Any bytes already read after the header terminator
  must be forwarded as tunnel data only after admission; never silently discard them.
  The gateway independently validates the hostname, port and manifest membership.
- Resolve per admission using a bounded resolver operation. Validate **every**
  returned address; mixed public/private answers reject the whole admission.
  Deny non-global, private, loopback, link-local, metadata, multicast, reserved,
  unspecified, IPv4-mapped IPv6 and translation/tunnelling ranges. Explicitly
  exclude deployment-owned public IPs, host/interface addresses and owned network
  ranges from a preflight inventory. Missing/stale inventory fails closed.
  IP parsing uses strict numeric addresses; no second hostname resolution during dial.
- Pin each dial to a validated numeric sockaddr, including family and port; verify
  the connected peer against it. Retries/family fallback use only validated answers
  within the same admission's bounded candidate list. Each new admission resolves
  and validates afresh, preventing a checked hostname from later dialing a private
  answer. DNS timeout/error/too many answers rejects the admission. DNS is the
  gateway's explicit control-plane exception; the browser has no DNS egress.
  Resolver deadline is 3 seconds, at most eight answers; connect deadline is
  3 seconds per admission across all candidate attempts. These share the overall
  deadline. Resolver cancellation cannot leave unbounded executor threads/jobs.
- CONNECT `200` is sent only after gateway admission and successful pinned connect.
  Every redirect/subresource needing a new tunnel repeats policy checks. Encrypted
  HTTP/2 reuse, SNI and requests inside a tunnel are not visible to this gateway:
  the enforceable allowlist is the **CONNECT destination**, not every HTTP origin
  served by a shared public IP. No stronger origin guarantee is claimed.

### IPC framing, lifecycle and bounds

- Use a fixed binary 12-byte header: type (uint8), flags (uint8, zero), reserved
  (uint16, zero), stream ID (uint32), payload length (uint32), all multibyte fields
  network order. Maximum payload 16 KiB, checked before allocation/read. Incremental
  parsers reject bad types, reserved fields, oversized/truncated frames and invalid
  transitions. No pickle, eval, dynamic imports or remote deserialization hooks.
- One supervisor-owned run per pair of pipes; no client-selected run IDs or shared
  session routing. Control stream 0 is separate from monotonically increasing
  tunnel IDs 1–40, never reused. OPEN carries a bounded canonical hostname and
  fixed port; OPEN_OK/OPEN_ERROR, DATA, WINDOW, HALF_CLOSE and CLOSE define tunnel
  state. No DATA before OPEN_OK or after local half-close; duplicate OPEN/CLOSE and
  stale IDs are protocol violations, never opportunities to reset accounting.
- At most 40 OPEN attempts total, including refused attempts; four pending/open
  tunnels at once. This is stricter than counting successful admissions only.
  A fixed 64 KiB receive-credit window per stream/direction and at most 64 KiB
  queued data per stream/direction bound memory. WINDOW can replenish consumed
  credit only; overflow/over-credit terminates the run. Fair scheduling prevents
  one slow tunnel blocking other streams indefinitely. A 4096-frame total ceiling
  per direction bounds control-frame floods. Invalid frames terminate both children.
- The gateway is authoritative for a shared 32 MiB aggregate sum of socket bytes
  read plus successfully written across all tunnels, never reset on reconnect.
  Reserve budget before each read/write and cap the operation to that reservation;
  refund only unused bytes. Concurrent tasks cannot each consume the same remaining
  budget. At exhaustion close all sockets and fail the run. These are TCP payload
  bytes, including tunneled TLS overhead, **not** packet headers, retransmissions
  or DNS traffic and not decoded content. Record directional counters separately.
  Use one gateway process/event loop and one accounting object. Reservation and
  refund operations are synchronous critical sections with no intervening await;
  only the actual socket operation may await after its bytes have been reserved.
  Cancelled operations reconcile their reservation exactly once or close the run
  without reusing it. A limit failure is terminal, not a new accounting epoch.
- The supervisor additionally caps aggregate DATA payload to the same limit and
  enforces a 45-second monotonic wall deadline from child start, including DNS,
  connect, navigation and extraction. Cleanup has a separate bounded grace period;
  deadline never extends to complete a page. EOF, cancellation, protocol failure,
  limit exhaustion or either child exit closes all tunnels, terminates both children
  and removes owned containers/network after label/image checks. Cleanup failure
  preserves evidence and reports a blocker; never remove unrelated resources.
- Browser keeps the approved 1 GiB/no-swap, 128-PID, one-CPU, 256 MiB shm boundary.
  Gateway additional ceiling (owner-approved 2026-10-01): 128 MiB/no-swap,
  32 PIDs, half a CPU and
  16 MiB scratch; these are separate from browser memory, not a claim of 1 GiB
  total-system usage. The supervisor uses streaming pipes, bounded queues and
  capped diagnostics, not whole-output buffering.
- Extracted UTF-8 output is capped at 1 MiB **before** crossing the pipe, including
  bounded title/URL metadata. RESULT chunks on control stream 0 count against a
  separate 1 MiB supervisor quota and use the same frame-size ceiling. One terminal
  result only; never route RESULT to the gateway or treat page text as protocol.
  No profile, cookies, page cache or raw response body is persisted. Evidence uses
  counters/status/configuration and bounded errors, without query strings or page
  content; any retained successful source text follows the separately approved
  snapshot contract, not a plaintext pilot artifact.

### Required acceptance evidence and implementation handoff

The implementer must provide tests and exact final-state evidence for:

| Requirement | Discriminating acceptance check |
|---|---|
| Browser confinement | Inspect network-none, only loopback, IPv4/IPv6 routes, capabilities, mounts and fd inventory; owned-fixture direct TCP/UDP/DNS/QUIC attempts cannot reach gateway/host/private destinations. No production-service scanning. |
| Destination enforcement | Parser edge cases; exact allowlist; private/metadata/IPv6/translated/mixed DNS answers; deployment public IP denial; rebinding with injected resolver plus an observed dial spy proving only the approved numeric address is used. |
| Tunnel semantics | Partial headers/frames, post-header bytes, concurrent and refused OPEN, stale IDs, half-close/EOF, DNS/connect failures and no premature CONNECT success. TLS invalid-certificate fixture must fail in browser. |
| Resource accounting | Boundary and concurrent budget exhaustion, including deliberately overlapping two tunnel reservations; reconnect cannot reset totals; header/frame/credit/queue/result/diagnostic caps; slow readers, frame floods and four-tunnel limit. |
| Cancellation and cleanup | Kill either child, close stdin, hang resolver/browser, cancel supervisor and hit deadline; verify no surviving sockets/jobs/owned containers or orphan network. |
| Sandbox regression | Exact qualified image/profile; ordinary/basic synthetic extraction and internal sandbox diagnostics after relay integration. Patchright remains optional until independently qualified. |
| Live readiness | Fresh review of actual runner/gateway/relay code and tests; supervisor independently verifies evidence before attaching gateway egress and navigating the fixed public URL. |

Policy/socket tests use dependency-injected resolvers and socket factories; owned
local TLS fixtures live in a disposable test-only topology, with production policy
unchanged. Fixture-only address exceptions cannot be enabled in live mode; a live
startup assertion rejects any fixture backend/exception. Integration tests must
exercise real pipes, socket behavior and browser proxy configuration, not only
mocked validators. Any mechanism needed to run fixtures beyond this boundary
requires review before execution.

Implementation may be built and unit-tested offline after design review. The
material security-boundary code must receive fresh independent review before its
container/egress execution. The approved pilot is not authorization to bypass
these gates, and no production activation follows automatically from live success.

### Design review disposition

Fresh read-only review-go session `ses_f0aa2f742ffeNbPXZJskinasXG` reviewed the
concrete design, not implementation. Its five required clarifications are now
incorporated: attempts supersede successful-admission counting, serialized byte
accounting, explicit diagnostic cap, precise entrypoint/pipe ownership and IPv4-only
gateway egress with complete deployment inventory. No executable containment or
live-access evidence is claimed. The architecture decision is sufficiently bounded
to resume offline implementation; actual security-boundary code and its fixture
execution remain subject to fresh review. Gateway's additional resource allocation
was subsequently owner-approved below, but has not been exercised and is not a
permanent host/deployment change.

## Offline core implementation — blocked verification (2026-10-01)

The pilot-only pure primitives now exist in `scripts/web_fetch_pilot_core.py`,
with unit coverage in `tests/test_web_fetch_pilot_core.py`. They contain no
socket/DNS calls, subprocess/container launch, production imports or fallback
activation. Tunnel lifecycle/credit, allowlist admission, actual pinned dialing,
IPC supervisor, wall deadline and live containment remain unimplemented.

The initial GLM implementation passed 148 targeted tests but fresh enforced
read-only explore-luna review found excessive eager frame accumulation, permissive
CONNECT control handling and mutable/forgeable reservation tokens. One worker
repair passed 198 tests on the primary's rerun. Follow-up review found two
remaining contract violations; the primary reproduced both on the final state:

- A 65,536-byte input completing a previously staged maximum frame emitted
  81,871 decoded payload bytes, exceeding the claimed 64 KiB per-feed ceiling.
- A caller-constructed `Reservation` subclass with custom equality/hash consumed
  an issued token's registry entry. Registry lookup must not trust subclass equality.

Review session: `ses_f0a9787b7ffeBLD8d1VVMoAo8l`. No second worker repair was
attempted. Verification remains blocked and these narrow issues are handed to
Astra before further implementation. Passing targeted tests do not close these
counterexamples; full project gates have not run on this code state. The temporary
worktree manager refused creation for insufficient free space, so implementation
was serialized with ownership limited to the two new files; unrelated checkout
changes were preserved. No live-resource allocation or production change occurred.

### Focused Astra correction — counterexamples closed

The subsequent bounded correction counts completed staged payload toward each
feed's 64 KiB decoded-output budget and rejects the whole batch before returning
an over-limit result. Reservation registry lookup now requires the exact token
type before hashing/equality; internal reservation records are frozen dataclasses.
Three regression tests cover the reproduced excess, exact-limit staged success,
and a hash/equality-overriding token subclass rejected before its callbacks run.

Primary final-state checks: 201 targeted tests pass; scoped Ruff lint/format,
basedpyright and high-severity Bandit pass. Fresh enforced read-only explore-luna
review `ses_f0a8ba692ffeaet7FyB9bFeocQ` found no issues in these corrections; it
did not run tests. The two escalation counterexamples are closed. This is scoped
verification of the pure core repair, not full-project-gate completion or proof
of network containment. Sol may resume ordinary offline pilot implementation
under the existing code-review and execution gates. No live or production change.

### Tunnel-ledger milestone — residual entry-validation blocker

`scripts/web_fetch_pilot_tunnels.py` and `tests/test_web_fetch_pilot_tunnels.py`
add pure exact-manifest admission, monotonic attempt IDs, pending/open limits,
half-close/terminal transitions and local-consumer credit accounting. No socket,
DNS, subprocess or browser operation is implemented or exercised. RESULT handling,
real pipe scheduling, deadlines and socket accounting remain separate work.

Primary verification: 249 combined core/ledger tests pass; repository-wide Ruff
lint, Ruff format check and basedpyright error-level gate pass. Fresh explore-luna
session `ses_f0a7a9a01ffeIzchJ29t7JOyLZ` found missing shape/sender validation in
the original ledger; one GLM repair added core-codec revalidation and regression
tests. Follow-up review found a remaining mismatch: integer subclasses pass a
check described as exact-integer validation. The primary reproduced an unhashable
integer-subclass ID causing `TypeError` after one attempt was recorded, without
setting the failed latch. This is a trusted in-process malformed-input contract
defect, not a demonstrated wire/network attack (decoded wire IDs are plain ints).
Further repair is paused for a bounded Astra correction of ID/count type checks
and their regressions. Passing tests do not close this counterexample. No live
execution or production integration is authorized by this milestone.

#### Exact-integer correction verified

Astra changed the three ledger entry checks to require `type(value) is int`
(frame ID, drain ID and drain count), rejecting subclasses before codec access,
lookup or arithmetic. Eight parameterized regressions cover both frame entry
points and drain arguments with unhashable and hostile subclasses; they verify
unchanged attempt/tunnel state, no subclass callbacks, and a latched failure.
Primary final-state evidence: 257 combined core/ledger tests pass, full-repository
basedpyright has zero errors, and scoped Ruff lint/format pass. Fresh enforced
read-only explore-luna `ses_f0a6fa920ffezRZEEkbfZI10Gg` found no issues in the
focused correction (no test execution by reviewer). The narrow escalation is
closed; full pilot and network-containment verification remain incomplete.

## Gateway-authoritative integration revision (approved 2026-10-01)

The owner selected gateway-authoritative admission with endpoint-local credit
and supervisor-owned completion, and approved the separate gateway allocation
above. Earlier descriptions of the pure ledger as a contract for BOTH sides do
not establish that two independent ledger instances can observe identical drain
events. This revision supersedes that integration assumption; the existing pure
ledger remains a tested primitive, not an implemented two-process runtime.

### Authoritative admission and refusal outcomes

- Only the gateway owns `TunnelLedger` admission/lifecycle state. The supervisor
  gives both children the same immutable run manifest; it never accepts manifest
  updates from page content, peers or RESULT. Browser relay does not independently
  use a full admission ledger that can suppress/refuse attempts unseen by gateway.
- Browser relay assigns consecutive IDs to complete, syntactically valid CONNECT
  attempts submitted over IPC. All submitted OPEN attempts reach the gateway,
  including hosts outside the exact manifest. The gateway counts them against
  40 attempts before accepting/refusing. Malformed local CONNECT fails that local
  socket without generating an OPEN; a separate 40 local connection-attempt cap
  includes malformed attempts and prevents an unbounded local-header workload.
- Gateway `open()` returning `False` is already a counted refusal with no pending
  socket. The runtime sends exactly one OPEN_ERROR for that attempt; it does not
  feed this locally generated refusal reply through the ledger's pending-reply
  handler, which intentionally has no pending entry for a refused attempt.
  The gateway runtime records refused-reply emission once per attempt, bounded
  by 40 identities. Duplicate/inconsistent emission is a run failure.
- An admitted OPEN remains PENDING until numeric pinned connect completes, then
  a locally generated OPEN_OK/OPEN_ERROR updates the ledger and is emitted. Relay
  keeps its own pending socket map for all submitted OPENs; exactly one reply is
  allowed. No local HTTP 200 precedes OPEN_OK. All terminal identities remain
  known; DATA on refused/closed streams and stale replies fail the run.
- Gateway-authoritative capacity includes DNS/connect tasks, not only connected
  sockets. No more than four active/pending gateway tunnels. Browser relay admits
  at most four concurrently pending/open local streams and rejects additional
  local sockets without creating an IPC stream. Neither endpoint releases active
  capacity merely because one direction half-closes.

### Local queue consumption versus acknowledgements

Each endpoint tracks sent bytes, received bytes queued, local consumption and
WINDOW acknowledgements; it does not pretend to observe the peer's I/O completion.
For each direction, initial credit is 64 KiB and the invariant is
`available send credit + sent-but-not-acknowledged bytes = 64 KiB`.
Here sent bytes include bytes reserved for emission; keep a separate emitted
counter to prohibit acknowledging DATA still waiting in the pipe-output queue.
Relay endpoint-local state is new bounded code (codec-validated frames, pending
socket map for every submitted OPEN, local counters); it is not a second
`TunnelLedger`. Existing ledger tests simulate both peers' events on a single
object and do not establish independent endpoint interoperability.

- Sender consumes credit before DATA emission. Data queued for framed output
  remains in its bounded queue until the local pipe write completes. The gateway
  ledger's trusted `drain` may record that local pipe consumption for gateway-
  originated bytes; this is not a claim that browser delivery has completed.
- Incoming WINDOW may acknowledge only DATA already emitted to that peer and not
  previously acknowledged. It cannot replenish bytes merely queued for emission,
  invent new credit, or acknowledge a different direction. The relay uses its
  endpoint-local sent/acknowledged counters; it never needs a remote `drain` call.
- A receiver queues DATA only within its granted receive window. It issues WINDOW
  only for actual local sink consumption: gateway socket-write completion for
  browser-originated DATA, or relay socket-write completion for gateway-originated
  DATA. The gateway ledger records its own real sink consumption via `drain`.
  At all times queued data and available credits obey the existing 64 KiB bounds.
- Socket-write cancellation with an unknown partial count fails the run without
  granting credit or refunding a byte reservation. Future async implementation
  must use explicit partial-write counts, not equate writer-buffer acceptance to
  remote delivery or assume cancelled writes transferred zero bytes.
- Each side sends HALF_CLOSE once on source EOF, only after its queued DATA has
  been emitted. The receiver drains prior DATA before propagating sink EOF. The
  gateway owns normal CLOSE after both directions half-close and all local queues
  and acknowledgement debts drain. An endpoint may send CLOSE for abort; simultaneous
  conflicting terminal events fail the run rather than recycling IDs or counters.
  The supervisor owns global cancellation/EOF termination of both children.
  Gateway runtime, not the ledger's generic CLOSE handler, checks normal-close
  queues and debts. A peer withholding WINDOW after both half-closes is terminated
  by the 45-second run deadline; ledger state alone does not detect its liveness.

### Bounded result completion (private pilot framing)

RESULT remains a supervisor-only control-stream frame; it never reaches the
gateway or its tunnel ledger. Its payload begins with a one-byte private subtype:
`1` for content chunk, `2` for a final status record. Frame flags/reserved remain
zero. This defines payload semantics, not a new application API or SSE event.
Unknown subtype, malformed final metadata or invalid result ordering latches the
whole run in the runtime result collector; the core codec treats these as opaque
RESULT bytes and does not enforce completion semantics.

- Content chunks contain nonempty extracted UTF-8 bytes, cumulatively at most
  1 MiB **including** final metadata. Subtype bytes count in IPC accounting but
  are not source text. Use a strict incremental UTF-8 decoder so byte-boundary
  splits do not imply invalid text; reject incomplete UTF-8 at finalization.
  Supervisor enforces two distinct 1 MiB ceilings: extracted content plus final
  JSON bytes, and cumulative RESULT payload bytes including every subtype byte.
  Binary frame headers do not count as source/result payload; they are bounded
  separately by the 4096-frame total and recorded wire-byte counter. A near-limit
  source may therefore exceed the stricter payload cap once tags are included;
  fail closed rather than silently truncate it.
- Exactly one final record, at most 4 KiB bounded UTF-8 JSON, closes the result.
  Its fixed fields are status (`success`, `blocked`, `error`), original/final URL,
  title and extraction-version. Metadata is independently bounded/validated;
  failure statuses cannot include content chunks or masquerade as article success.
  No subsequent RESULT or tunnel-open request from the browser is accepted.
- Final success alone is not completion. Supervisor requires a coherent nonempty
  source result, browser exit without protocol/limit errors, bounded gateway
  shutdown, and owned-resource cleanup. Child stderr/EOF is never a success marker.
  Content remains bounded in memory and is discarded on failure; no plaintext
  pilot page artifact or persistent browser profile is written.
- Supervisor total frame counters include RESULT traffic. JSON cannot alter policy,
  resource counters or command arguments. Its page-derived strings are untrusted
  data and cannot cause a further navigation or provider request.

Required integration tests now include refused OPEN with one error reply, no
cross-endpoint attempt desynchronization, sender-side WINDOW based on actual
emission, receiver-side WINDOW based on actual sink consumption, delayed-ACK and
half-close drains, final-record ordering/UTF-8/aggregate caps, and cancellation
after partial writes. These supplement rather than replace the concrete-design
acceptance table. Actual gateway/relay/supervisor code remains unimplemented;
this revision must pass fresh design review and subsequent actual-code review
before any executable boundary or live pilot run.

Fresh read-only review-go `ses_f0a6457fcffeQ3BKO9NpjSNjVF` assessed this design
against the primitives and found the revision feasible, requiring four
clarifications now incorporated: gateway-only ledger ownership (including its
docstring), relay-local accounting, runtime normal-close/deadline ownership, and
distinct RESULT accounting/subtype checks. It did not execute tests or approve
actual runtime code. The integration reconnaissance was read-only explore-luna
`ses_f0a6cf700ffed57QarR4x62fSz`; its mirror-state counterexamples are addressed by
the owner-selected gateway-authoritative design, not dismissed as test failures.

## Injected asynchronous gateway actor — boundary review outstanding

`scripts/web_fetch_pilot_gateway.py` and its tests now implement an asynchronous
gateway actor with injected FrameIO/resolver/connector/connection interfaces.
There is no default DNS/socket/pipe adapter or CLI. Sol worker authored only these
two new files; the primary inspected their actual code and ran 323 combined
core/ledger/gateway tests successfully. Scoped Ruff lint/format, basedpyright and
high-severity Bandit also pass. These tests simulate transport events in memory;
they do not certify real numeric dialing, TLS, kernel pipe publication or network
containment. The real browser relay/supervisor are still unimplemented.

Fresh enforced explore-luna review `ses_f0a51c866ffe423rtL5rBOd27O` identified two
requirements to close before actual boundary execution:

- Actual peer-address verification is optional: `peer_ip` alone is accepted when
  a connection lacks `getpeername`. An actual adapter must expose and verify the
  connected numeric sockaddr; a claimed property alone is insufficient evidence.
- Run timeout ends before unbounded cleanup `gather`. Cooperative cancellation
  currently has no maximum teardown duration, so a separately bounded cleanup
  grace and truthful failure/survivor ownership contract are required.

The primary reproduced both at the injected interface boundary: an object with a
claimed approved `peer_ip` but no socket-address accessor passed verification;
a 20 ms deadline plus 100 ms cooperative cleanup took 121 ms, with no cleanup
failure indication. The former is not evidence of a real private-network dial,
and the latter is not an escaped browser process. They demonstrate unqualified
adapter/teardown contracts. A focused Astra security-boundary intervention is
required before further affected execution, not a whole-pilot takeover.

The newly approved gateway memory/CPU/PID/scratch allocation has not been used.
No actual network/browser/container request, production fallback activation,
provider addition or application API change occurred. Full-project gates remain
incomplete and no production readiness is claimed.

### Gateway actor boundary correction verified (2026-10-01)

Astra made the connected-sockaddr accessor mandatory on the injected connection
contract. A qualified future adapter must derive it from the actual connected
socket; missing accessors and mismatched address/port are refused before OPEN_OK.
Checking a fake's accessor is not independent kernel-peer evidence.

Actor cleanup now waits at most two seconds (shorter injectable grace for tests)
after cancellation, using a bounded wait rather than cancellation-blocking gather.
If tasks survive, `GatewayOutcome.pending_tasks` and `cleanup_failed` expose the
failure; `Gateway.pending_tasks` retains owned handles until completion. This is
not successful cleanup or permission to abandon survivors. The future supervisor
must force termination/removal of the owned container on failed cleanup and
independently verify that result. The bound assumes nonblocking synchronous
adapter methods and a responsive event loop; neither Python task termination nor
OS container termination is implemented by this actor.

Three regressions cover absent sockaddr accessors, claimed-public/private-actual
peer disagreement, and cooperative but delayed cleanup exceeding the grace while
ownership remains visible. The fixture subsequently releases and awaits its owned
task. Primary verification: 326 combined tests pass, scoped Ruff lint and
basedpyright pass. Fresh enforced read-only explore-luna
`ses_f0a49773dffeYT9TZghI3x59j4` found no remaining issues in these two corrections;
it did not run tests. This closes the narrow actor-contract escalation for
offline development, not real-adapter/containment or full-project qualification.

## Real I/O adapters — controlled execution gate pending

`scripts/web_fetch_pilot_io.py` now provides nonblocking explicit-fd pipe/socket
I/O, bounded frame transport, connected-socket byte accounting/peer access, and
public-only numeric IPv4 dialing. It has no CLI, DNS resolver, browser launch or
container supervisor. Numeric dialing validates policy before socket creation,
uses a numeric sockaddr with `connect_ex`, and checks the actual peer; its real
public dial path has **not** been executed or qualified.

Primary checks: 16 fake-transport tests pass; the three real-kernel tests were
explicitly deselected. Scoped Ruff lint/format and basedpyright pass. Fake numeric
connector cases verify exact sockaddr selection, failed/incorrect-peer connects,
and cancellation ownership without creating a socket or resolving DNS.

Fresh enforced read-only explore-luna `ses_f0a3be31fffeBFtvbkVd6IAStf` reviewed
the source before boundary execution and a bounded clarification round. Gateway
publication ordering now means completion before **local** ACK processing; it
does not control when another process observes bytes. Fixtures use exception-safe
descriptor/socket ownership and retrieve pending send/receive tasks on failure.
The reviewer approved only the three owned anonymous-pipe/ephemeral-loopback
fixture paths for Astra execution: maximum DATA frame/publication/ACK ordering,
close waking a pending reader, and raw TCP peer/partial-byte/half-close behavior.
No public DNS/dial, existing-service probe, browser/container or live URL is in
that approval. Actual execution awaits the focused supervisory gate; successful
fake tests do not establish kernel behavior or network containment.

### Controlled local I/O execution verified

Astra executed the reviewed adapter suite: all 19 tests pass, including the
three owned anonymous-pipe/ephemeral-loopback cases. A separate in-process
cleanup check observed identical `/proc/self/fd` sets and no surviving fixture
tasks before/after each of those three cases. Combined core, ledger, injected
gateway and adapter suites pass **345 tests** on this state; scoped adapter Ruff
lint and basedpyright also pass. No code correction was needed for this gate.

This establishes the tested local frame round-trip/publication ordering,
close-wakes-reader, real socket peer/count/half-close behavior and fixture cleanup.
It does not exercise the real public NumericConnector, DNS, cross-process Docker
attach, TLS, a browser relay, container isolation or live sources. Full project
gates and full-pilot acceptance remain incomplete. The bounded local execution
gate is closed; ordinary offline implementation can resume under the existing
fresh-code-review and containment requirements for subsequent boundary work.

## Supervisor RESULT collector — pure checks pass, integration pending

`scripts/web_fetch_pilot_results.py` implements only the pure collector from the
approved completion revision. Its private final JSON keys are `status`,
`original_url`, `final_url`, `title`, and `extraction_version`. These are internal
pilot fields, not an application API/SSE/schema change. Unknown/duplicate fields,
non-string values, non-JSON constants, invalid Unicode, provenance/version
mismatches, and final URLs outside the immutable HTTPS destination manifest fail
closed. Extracted content uses strict incremental UTF-8; the two separate 1 MiB
quotas include final metadata and, separately, every RESULT subtype byte.

Every browser frame must pass `observe()` before any tunnel forwarding, so RESULT
traffic shares the 4096 browser-direction frame counter. Only RESULT is consumed
by the collector; it must never reach the gateway. The future supervisor must
separately count gateway frames and aggregate both DATA directions. EOF without
a final record, content accompanying a failure, empty success, and RESULT/OPEN
after finalization fail the run. Abort discards buffered content even after a
final record. Candidate data is accessible only after final plus clean frame EOF,
and remains an untrusted child claim, not a successful article or permission to
persist content. Browser exit, source classification, transport framing EOF,
gateway shutdown and owned-container cleanup still require supervisor evidence.

The implementation has no I/O, subprocess/browser launch, navigation, persistence
or completion attestation. Scoped lint/format/types pass. Fresh enforced read-only
explore-luna `ses_f09ffa7cfffegKdkWBKDqwTjvW` found no blocking collector defects
and accepted the observe-then-EOF seam with the documented supervisor obligations;
it did not execute tests or review the relay. Primary independently ran the
combined six pilot suites: **488 tests pass**, including 57 pure collector cases.
The first run exposed an incorrect expected chunk count in a near-limit test
(64 chunks rather than 65); the test expectation was repaired without changing
the collector limits. Neither component establishes live containment or
production eligibility.

## Browser-side injected relay — offline checks and review pass

`scripts/web_fetch_pilot_relay.py` implements the browser-side actor using injected
FrameIO, local acceptor and connection interfaces only. It has no OS listener,
browser, DNS, container launch or entrypoint. Gateway remains the sole owner of
`TunnelLedger`; relay-local counters track queued/emitted/acknowledged bytes and
actual local sink consumption. Complete valid CONNECT attempts reach the gateway
even when their destination is outside its manifest. Malformed/capacity refusals
still count against the separate 40 local attempts; at most four pending/open
local streams include incomplete handshakes.

The primary inspected the worker's two actual changed files and independently ran
all six pilot suites on the returned state: 488 tests pass, including 86 relay
cases. Scoped lint/format and types across all six code/test pairs pass; scoped
high-severity Bandit finds no medium/high issues. Feature-matrix validation and
documentation freshness pass. Fresh enforced relay/integration review in
explore-luna `ses_f09f4c9d3ffenloQUDT8xaVwrd` found no actionable defects. It
accepted the reviewed credit/half-close/deferred-retirement seams, while noting
the still-unimplemented supervisor obligation to observe browser frames before
forwarding and consume RESULT without exposing it to the gateway. The reviewer
did not execute tests or assess OS/live-boundary qualification.

| Acceptance evidence | Observed artifact | Remaining boundary |
|---|---|---|
| Admission/refusal, strict header and local attempt/capacity bounds | In-memory relay fixtures; gateway refusal then consecutive admitted OPEN integration | Real loopback listener and browser proxy behavior unimplemented |
| Prefetched bytes, partial HTTP 200/TLS writes, 64 KiB credits and publication-only WINDOW | Deterministic injected transport fixtures, including queued-vs-emitted ACK races | No real relay adapter or cross-process attach qualification |
| Both-direction aggregate DATA cap, half-close drains, in-flight terminal emissions and stale frames | Pure actor fixtures; normal two-actor exchange | Real DNS/numeric dial/TLS/container containment unqualified |
| Deadline/cancellation/unknown partial writes and retained cleanup handles | Injected slow-cancellation and late-accept fixtures | Supervisor force-teardown and observed owned-container removal unimplemented |
| RESULT Unicode, metadata/provenance, two quotas and final/EOF ordering | Pure collector tests and fresh source review | Collector not wired to child IPC; exit/classification/cleanup completion authority unimplemented |

Checked relay SHA-256: `1cdeb030cca14a4460b6e3e0148d53c8c8052cf79d1f112d9436abee224a1dad`;
collector SHA-256: `6d1c56c6658df808d00b58857c9e72a6b2a812f07a5afba2a15cbd7fab3c01c7`.
All relay outcomes remain INCOMPLETE. Full-project gates have not all run; this is
partially verified offline implementation, not completion of the pilot, proof of
article access, production eligibility or authorization for live execution.

## Injected supervisor IPC bridge — offline checks and review pass

`scripts/web_fetch_pilot_bridge.py` wires the collector into two bounded injected
FrameIO pumps. Every browser frame is observed before forwarding; RESULT is
consumed privately and never sent to the gateway. Gateway sender/shape checks
exclude RESULT and OPEN. The bridge applies the aggregate 32 MiB DATA ceiling
across both directions and counts each direction's frames/wire bytes separately;
browser counting includes RESULT in the collector's 4096-frame total.

The caller supplies a trusted absolute monotonic deadline from child start.
Remaining time above 45 seconds is rejected and expired time closes the pipes
without starting pumps; this does not prove the caller's claimed start timestamp.
Each pump retains at most one bounded frame under destination backpressure, with
no forwarding queue and two owned tasks. Cleanup is bounded by two seconds,
preserving any surviving tasks for the future force-teardown owner.

Browser final plus clean frame EOF yields only a provisional candidate, never an
article-success marker. Gateway EOF, malformed frames, quotas, deadline,
cancellation, close failure or incomplete forwarding discard the candidate.
Final EOF cannot hide a concurrent forwarding cancellation/error: separate
failure tracking is reconciled after bounded cleanup. Actual child exit,
extraction classification, owned identity/configuration verification, force
teardown and observed container removal remain unimplemented. No process,
browser, DNS, socket, real descriptor or container is created by this bridge.

Primary evidence: 22 new in-memory bridge cases pass; all seven pilot suites
pass **510 tests**. New bridge lint/format/types and high-severity Bandit pass.
Checked bridge SHA-256:
`f09f5ed9556adb4a084ce86f0af653646f9d3a9a7cf770b78437606136d19da6`.
Fresh enforced bridge/seam review in explore-luna
`ses_f09ed0df8ffeZ2BUC82wgnN5zt` found no actionable defects, including in the
final-EOF/cleanup race. It made no edits and executed no checks. These tests are
not new kernel/container qualification, do not close full-project gates, and
authorize no live URL. Controlled owned-pipe fixture preparation follows; actual
execution remains subject to fixture review and a bounded Astra execution gate.

### Owned kernel-pipe bridge gate prepared — not executed

Three fixtures in `tests/test_web_fetch_pilot_bridge_io.py` use only eight owned
anonymous pipe descriptors per case, with exception-safe cleanup. They reduce
each owned pipe's capacity to 4 KiB using `F_SETPIPE_SZ` (no host sysctl), so
maximum DATA frames require partial writes. Cases cover real duplex forwarding
and private RESULT/EOF, truncated frame EOF discarding content, and an unread
destination's deadline cancellation with no leftover descriptors/tasks.
No child process, socket, DNS, browser or container is involved; this cannot
establish cross-process/Docker attach or containment behavior.

Scoped fixture lint/format/types pass; **the three fixtures have not run**.
Fresh read-only follow-up in explore-luna `ses_f09ed0df8ffeZ2BUC82wgnN5zt`
found no actionable fixture findings and cleared source for only these three
owned-pipe paths under a focused Astra execution gate. Reviewed fixture SHA-256:
`cfa881b5c87167d764084aa410c5f8255dc1083ae5809badd8fdc6101ccf5dde`.

### Owned kernel-pipe bridge gate verified

Astra verified the reviewed bridge/fixture hashes and executed all three owned
anonymous-pipe fixtures: **3 passed**. Their before/after descriptor-set and
asyncio-task assertions passed, including the unread-destination cancellation
case. No code correction was needed. The combined eight pilot suites then passed
**513 tests**; scoped bridge/fixture basedpyright reported zero errors.

This closes only the same-process kernel-pipe bridge execution gate: duplex
forwarding, private RESULT consumption, malformed EOF rejection and bounded
blocked-output cleanup. Cross-process transport, child exit, DNS/numeric dialing,
TLS, browser proxying, container containment and forced teardown remain
unqualified. Full-project gates remain incomplete. Ordinary offline work returns
to Sol; no public fetch or production activation occurred.

## DNS implementation selection and real acceptor preparation (2026-10-01)

The owner selected **isolated helper resolution** over an installed async resolver
or deferring DNS. This authorizes implementation of short-lived owned standard-
library resolver helpers, not real DNS execution or an expanded gateway budget.
Helpers count toward the already-approved 32-PID/128-MiB gateway allocation.
Spawn/read/exit must obey the 3-second resolution deadline; timeout/cancellation
must kill and reap owned helpers or retain visible ownership and latch a fatal
failure requiring supervisor force-teardown. Lost helpers cannot be treated as
ordinary refusals followed by fresh allocations. No dependency is added.

`scripts/web_fetch_pilot_acceptor.py` is drafted as an explicitly supplied owned
loopback-listener adapter for the relay. It creates/discovers/binds no socket;
only an already listening IPv4 TCP socket bound to exactly `127.0.0.1` qualifies.
Successful construction transfers ownership; invalid listeners remain caller-
owned. Accepted sockets use their actual connected peer, not accept metadata.
One outstanding accept, cancellation/setup cleanup and unregister-before-release
ownership complement the previously qualified AsyncFD primitive. Its borrowed-fd
doc now says "owning socket adapter", covering both listener and connection
owners; no existing I/O logic changed.

Scoped acceptor lint/format/types pass. Fresh enforced source review in
explore-luna `ses_f09cd3b1effe0xEuQT2pFGfdg1` found no source-level ownership or
cancellation defects. Primary fake acceptor validation now passes 15 cases.
No acceptor socket or resolver helper has been created or executed.
The future launcher still must establish network-none, listener backlog, port/fd
inventory, scrubbed child configuration and resource/teardown evidence.

Three controlled kernel fixtures in `tests/test_web_fetch_pilot_acceptor_io.py`
create only owned `127.0.0.1` listeners on ephemeral ports (backlog four) and
clients targeting those same listeners. They cover actual peer/accessor and
transferred-connection lifetime, close waking a pending accept, and cancellation
followed by a fresh accept. Setup and teardown use socket contexts, gather and
close any returned-but-unassigned accepted connection, and assert equal fd/task
sets before/after. They create no wildcard listener, public connection, DNS,
process, browser or container. Scoped lint/format/types pass; **they have not
executed**. The same fresh reviewer found no concrete fixture cleanup finding and
cleared only their described loopback paths, not containment or live execution.
Fake validation passes; a bounded Astra execution gate still precedes kernel
qualification of these three loopback cases.

## Owned DNS-helper implementation — fake checks pass, review pending

`scripts/web_fetch_pilot_dns.py` implements an explicit fixed standard-library
spawner and bounded resolver actor. Import/construction perform no I/O; the
spawner requires a caller-qualified absolute interpreter path, fixed `-I -S -c`
source and canonical hostname argument, empty environment, closed inherited fds,
null stdin/stderr and bounded stdout. The helper requests IPv4 A candidates;
this is not enumeration of AAAA records. Every returned A candidate still passes
the gateway's unchanged public/deployment-owned address policy before dialing.
Native libc result allocation must be bounded by the gateway container's memory
limit, not falsely attributed to the parent JSON cap.

At most four jobs include spawn/read/wait/cleanup. Spawn publication remains
tracked through cancellation; late handles are killed and reaped or retained
visibly. Parent result buffering is capped at 4097 bytes (the extra byte detects
overflow), with 1024-byte reads; native asyncio transport buffers are separate.
Answers require stdout EOF, exit zero and strict bounded JSON. Cleanup uncertainty
latches `DNSCleanupFailure`, which deliberately escapes gateway ordinary-refusal
handling and prohibits new helper allocation. The future run owner must call
`aclose()` on every ending and reconcile retained tasks/handles with force teardown.

Primary inspected both actual worker files and independently ran 62 fake-only DNS
cases plus 15 fake acceptor cases: 77 pass. Combined ten qualified pilot suites
pass **590 tests** on the unchanged final source state; the three new real
acceptor cases were deliberately excluded. New DNS/acceptor scoped lint/types
pass. Reviewed DNS source SHA-256:
`f355ebad1f13d15d4eea69e89d53f97eec6e5db3837760664ac767ace57b475f`.
Fresh enforced DNS ownership/seam review in explore-luna
`ses_f09c2ba91ffeSwILRb0ppbp9TP` found no remaining material defect. Its initial
deadline finding was rejected against the explicit approved source at lines
457–459 (separate 3-second resolution and admission-wide connect limits, both
under the overall run deadline); the reviewer withdrew it after source
reconciliation. No deadline was changed and no contradictory contract remains.
No subprocess or DNS request has run; the literal helper source was parsed as
AST, not executed. Native process/pipe cleanup, real resolution and container
containment remain unqualified.

The initial combined run hit a pre-existing one-second gateway frame-cap fixture
watchdog before 4096 frames while type checking ran concurrently. The unchanged
case passed in isolation; the unchanged serialized combined suite passed 590
tests. No limit or assertion was weakened. This timing-sensitive fixture is
recorded as warning triage [#377](https://github.com/sol-aeternum/Daemon/issues/377),
not silently treated as stable full-project-gate evidence.

Scoped pre-commit/doc freshness/gitleaks and blocking high-severity Bandit pass.
Bandit inventory reports B104 at `tests/test_web_fetch_pilot_acceptor.py:181` for
the negative fake `0.0.0.0` bind value. This is fixture metadata explicitly
rejected before any FD adapter is constructed, not an actual wildcard listener;
the finding is a checked false positive and remains unsuppressed in inventory.

### Owned loopback acceptor execution gate verified

Astra verified the reviewed acceptor/fixture hashes and executed the three
owned ephemeral-loopback cases: **3 passed**, including their before/after fd
and task assertions. Actual connected-peer access, transferred connection
lifetime after listener closure, close waking an idle accept, and cancellation
followed by a fresh accept passed without code repair.

The combined eleven pilot suites then passed **593 tests** in a serialized run;
scoped acceptor/DNS types reported zero errors. The previously recorded gateway
watchdog warning (#377) did not recur in this run and remains tracked. This gate
executed no resolver helper, DNS lookup, browser or container. Native DNS process
ownership, cross-process transport, TLS, browser/container containment and forced
teardown remain unqualified; full-project gates remain incomplete. The focused
acceptor gate is closed and ordinary offline implementation returns to Sol.

## Native resolver-ownership fixtures — source gate clear, not executed

Three cases in `tests/test_web_fetch_pilot_dns_io.py` capture an immutable fixed
synthetic Python program before any spawn. They never invoke or patch
`StdlibSpawner`/`HELPER_SOURCE` and never resolve the inert `fixture.invalid`
argument. Each creates only one explicitly owned child, with isolated/no-site
Python, empty environment, closed inherited fds, null stdin/stderr and bounded
stdout. Programs output a tiny fixed JSON response or sleep for ten seconds;
the fixture applies short resolver deadlines, owned kill/reap and cleanup.
No shell, socket, browser, Docker or application-file writes are used.

The proposed cases cover native stdout EOF/exit publication, deadline kill/reap,
and actual process creation with delayed handle publication past caller failure.
Late ownership stays tracked, new admissions latch off, and the released handle
must be killed/reaped without resetting failure state. Nested cleanup releases
publication and recovers only recorded owned handles, even after assertion/query
cleanup failure; fd/task sets must match before/after each case.

Scoped lint/format/types pass; **the three cases have not executed**. Fresh
enforced source review in explore-luna `ses_f098c2ab4ffeIxQVtQBgQKYEK6` found no
blocking findings for only these controlled process paths. It did not execute
anything or qualify real DNS, StdlibSpawner provenance, gateway resource limits,
paused-reader/overflow handling or container containment. A native spawn wedged
before returning a handle remains a blocker, not clean-teardown evidence.
Reviewed fixture SHA-256:
`294aa1c9dcf3b942ab085f90d5a18b1f39a48ab0c777f16e2d987c66e72f8df6`.
Actual execution requires the focused Astra gate; no live DNS permission follows.

### Native resolver-ownership gate verified

Astra verified the reviewed resolver/fixture hashes and executed the three
synthetic-child cases: **3 passed**. The cases observed native stdout EOF and
exit zero, deadline kill/reap, and delayed handle publication followed by owned
kill/reap while retaining the fatal latch. Their before/after fd and task
assertions passed. No code repair was needed.

The combined twelve pilot suites passed **596 tests** in a serialized run;
scoped resolver/fixture types reported zero errors. This closes only the tested
native process-ownership paths with tiny output. It does not qualify actual DNS,
StdlibSpawner provenance, paused-reader/output-overflow cleanup, gateway resource
containment, browser behavior or forced container teardown. Full-project gates
remain incomplete. No DNS lookup or network request ran in this gate. Ordinary
offline implementation returns to Sol; production remains direct-only.

### Native stdout overflow ownership correction (2026-10-01)

A source-reviewed synthetic-child probe confirmed a cleanup defect on the host
Python runtime: after rejecting overflowing output and killing the child,
`DNSResolver` reported ordinary `DNSFailure`, no owned jobs/tasks/processes and
`cleanup_failed=False`, while stdout remained paused with one extra descriptor.
Child exit did not prove pipe closure. The probe's bounded recovery drain restored
the fd/task sets; no DNS or network request ran. This is tracked in
[#380](https://github.com/sol-aeternum/Daemon/issues/380), not evidence of gateway
image behavior or a production incident.

The owner selected **bounded drain** over replacing the stdout adapter. The
minimal repair serializes result and cleanup reads. After killing a helper,
cleanup discards at most 128 KiB plus one overflow-detection byte in 1024-byte
chunks, within the existing cleanup grace; it requires stdout EOF and verified
child exit before releasing job ownership. Unknown EOF, cap exhaustion or cleanup
failure remains fatal, disables admission and retains resources for teardown.
Normal answer-size limits and gateway resource limits are unchanged.

Fresh enforced explore-luna review `ses_f0950ca93ffejcfA2YNGf6rgRH` found no
findings in the repair and native regression before execution. Astra independently
ran 67 DNS cases and then all twelve pilot suites: **598 tests pass**. The new
native overflow regression checks EOF, process exit and restored descriptors
before its recovery backstop can mask a leak. A fake exited-child/withheld-EOF
case verifies fatal retention. Scoped lint/format/types pass. This closes the
specific stdout ownership escalation; native DNS, container limits and live
containment remain unqualified, and full-project gates remain incomplete.

## Offline gateway qualification preparation (2026-10-01)

`scripts/web_fetch_pilot_container_policy.py` now provides pure fixed-argument
construction and supplied-record preflight checks for a **network-none DNS-only
fixture**, not the future live gateway. Identity checks use the exact pinned
image, a fresh supervisor-owned name/label and a full container ID. Configuration
checks require the approved 128 MiB/no-swap, 32-PID, half-CPU and 16 MiB scratch
allocation, non-root/read-only/capability-drop/no-new-privileges, disabled IPC,
no published ports, no host mounts/devices and no restart or daemon log storage.
The trusted bootstrap must separately receive review; the checker performs no
Docker, process, file or network operation. Cleanup identity remains independently
checkable if the stronger configuration check fails, but actual removal is not
implemented or proven by this pure module.

Primary pure checks: **83 supplied-record/vector tests pass**; scoped lint/types
pass. Fresh review-go `ses_f091cb386ffeuxdwrrn0qCIIPy` identified missing
port-publication intent, namespace-mode and anomalous tmpfs-metadata checks.
One targeted repair requires publication disabled, default userns configuration,
private cgroup namespaces (explicit in argv) and empty tmpfs source/mode/propagation
metadata; regression tests cover these and malformed record shapes. Follow-up
source review confirmed the findings closed. Runtime/daemon provenance and actual
cgroup enforcement remain duties of the future launcher, not proof supplied by
the record checker. Native Docker interpretation of these flags and inside-container enforcement
remain unverified. Fresh review, the trusted payload and a bounded streaming
command/cleanup owner are still prerequisites to container execution. The first
combined launcher/fixture worker produced no files because its mandate exceeded
its session budget; the work was re-decomposed rather than accepted as complete.
Scoped pre-commit/doc freshness/gitleaks and high-severity Bandit also pass.
Bandit inventory retains nine B108 findings for `/tmp` configuration/fixture
strings (no file is opened by this module) and four low-severity test assertions;
no suppression or gate relaxation was added. These are not native temp-file
operations or evidence of a runtime vulnerability.
The tested DNS repair and 598-test reference state were preserved. No container,
actual resolver helper, egress attachment or source URL ran during this preparation.

### Trusted DNS payload — implemented, fixture execution blocked

`scripts/web_fetch_pilot_dns_payload.py` and its tests provide a bounded trusted
source bundle, bootstrap/fixture source literals and a provisional child-record
validator. The primary independently ran **51 pure tests** through the explicit
project environment; scoped lint/types pass. These tests parse fixture source as
AST and exercise the data builder/validator, not staging, DNS or native behavior.

Source inspection identified `sys.flags.unbuffered` in the fixture; the host
interpreter confirms that attribute does not exist. The passing AST tests do not
close this execution blocker. Fresh enforced explore-luna review
`ses_f08e38e34ffepU3g3b35nldas1` also found that ordinary `DNSFailure` can represent
failed process creation, false isolation booleans pass `ok` validation, and the
fixture's actual unsupported-cgroup record contradicts the validator's fixed
limits. The primary directly reproduced acceptance of all four false isolation/
no-new-privileges booleans and rejection of the literal unsupported record shape
using only the pure validator. These are source/data-contract checks, not native
execution. One bounded worker repair is in progress; the payload is not ready
for native execution. A bounded streaming launcher and
forced owned-container removal remain unimplemented. No helper or container ran.

### Payload repair verification — residual blockers (2026-10-01)

After the owner's requested pause, the primary inspected the returned worker
repair and independently reran the pure payload suite: **59 pass**. The repair
adds published-helper/exit evidence, required true success flags, pre-helper
route checks, fixed scrubbed locale and support for the actual unsupported shape.
It does not close all original criteria; no successful fixture run is claimed.

The primary's harmless interpreter-only `-I -S -u` diagnostic found binary stdout
is `io.FileIO`, not `io.BufferedWriter`; `write_through` is on the text wrapper,
not that binary stream. The repaired fixture instead requires BufferedWriter and
binary `write_through`, so the unbuffered qualification gate still fails. No
fixture source, DNS helper or container was executed to establish this finding.
The source-presence tests cannot substantiate its claimed runtime repair.

Pure validator checks also confirmed that `unsupported` bypasses exact types for
identity/security fields (accepting string UID and boolean effective capabilities),
accepts nonzero/verified-looking limit fields despite the specified zero/empty
shape, and raises `TypeError` rather than fixed `RecordRefused` when memory is
`null`, because it compares before type validation. These records remain
non-success, but violate the diagnostic's strict schema/refusal contract.

One worker repair has already been used. Further affected work is stopped for a
bounded Astra source/verification correction, not another worker repair loop or
permission for native execution. The inspected payload SHA-256 is
`9e37635b4c76a22a1088ba509f8dfb4432d38051521f3af5041372f8954621de`;
tests are `4badda43959955b0b3093339b93193af7bdb4dd68eeb36d702571f89f0fa0ce3`.
The verified container-policy source and DNS ownership repair are unchanged.

### Focused payload correction verified; aggregate watchdog still failing

Astra corrected the predicate to require binary `io.FileIO` and text-wrapper
`write_through=True`. A reviewed test extracts only that pure function's AST and
executes it with inert doubles, covering unbuffered success and five refusal
cases without running the fixture. All integer fields are now type/range checked
before comparison; common identity/route/security values apply before the
unsupported branch. Unsupported records require zero/empty limits and true
isolation/no-new-privileges flags. Tests mutate a genuinely valid unsupported
baseline so unrelated helper fields cannot mask a missed check. The bootstrap's
missing `__main__` invocation was also repaired and checked structurally.

Fresh enforced explore-luna `ses_f08925bbdffeVgaVdA0uCnXZzG` reviewed the actual
correction and isolated-predicate test; its additional unsupported-flag finding
was fixed in one focused follow-up, then confirmed closed. Primary focused
payload/container-policy validation passes **208 tests** (125 payload, 83 policy);
scoped types and lint pass. No bootstrap, actual DNS helper or container ran.

Combined fourteen-suite verification is **not green**: two runs each passed 805
tests but failed `test_independent_output_frame_count_limit`, the existing #377
one-second watchdog, at 4018 and 4074 frames respectively. The second run was
serial; no third retry, assertion change or timing relaxation followed. This
does not reopen the reproduced payload predicate/schema defects, but remains an
aggregate gate limitation. Full-project gates and native qualification are still
incomplete. Ordinary offline work can resume with this limitation recorded.

### Output frame-cap fixture isolated without timing relaxation

Sol inspected #377's failure path: the fixture both races a one-second outer
watchdog with its one-second gateway deadline and generates WINDOW feedback for
each single-byte output. The intended 4 KiB workload fits within the initial
64 KiB credit, so those acknowledgements are unnecessary to reach the output cap.
The fixture now sends only OPEN and does not replenish credit. It still attempts
the same 4096 one-byte reads, expects PROTOCOL at 4096 published output frames,
and now requires **exactly one** received frame plus closed endpoints and clean
task ownership. Neither watchdog, the runtime nor any frame/credit limit changed.
The independent input cap and WINDOW/credit tests remain intact.

Fresh enforced explore-luna `ses_f088988b1fferl5qBWLLRgkAdu` found no coverage
weakening or flow-credit defect. Primary targeted execution passes, followed by
the final serial fourteen-suite run: **806 tests pass** in 14.60 seconds. Scoped
gateway types and pre-commit/lint/format/doc freshness/gitleaks pass. Gateway
runtime SHA-256 remains
`a4a92d817df63e956fc26fe5deed5d49496df8b8bf041d7e06b19ea33b4c66c8`;
updated test SHA-256 is
`1a81481e62fd7d34a541278011b80913a45076b0bd8524653533395a8d6f33fb`.

This closes the observed output-cap fixture failure in the checked state, not a
universal performance guarantee on arbitrary hosts. Full-project gates, actual
DNS-helper/container qualification and live containment remain incomplete. The
combined run reuses previously qualified controlled kernel/synthetic-child
fixtures; it runs no actual DNS helper, bootstrap, browser or container.

### Supervisor pipe choice and remaining launch adapter

The owner selected **owned raw pipes** for the next supervisor launch adapter,
reusing the tested `AsyncFD` ownership/force-close path instead of parent asyncio
StreamReader transports. This is distinct from the DNS helper's previously chosen
bounded-drain repair. The new adapter is being prepared with injected fake pipe/
process backends only; native execution still requires fresh review and the
bounded supervisory gate. The Docker lifecycle driver, streaming frame/diagnostic
pumps and forced owned-container removal are still separate unimplemented work.

Read-only `docker create --help` confirmed the installed client's default image
pull policy is `missing`. The pure create vector now explicitly uses `--pull never`
and attaches stdin/stdout/stderr, matching the pinned-local-image/no-pull and
preflight channel contracts rather than relying on client defaults. Updated
policy tests pass **83 cases** and scoped pre-commit passes. This help invocation
created no container and is not inspection/runtime qualification. The 806-test
run above predates this argument-vector-only adjustment; its affected policy suite
was rerun, with native flag/configuration behavior still gated.

### Raw-launch ownership adapter — fake checks pass, lifecycle blocked

`scripts/web_fetch_pilot_process.py` and its tests add an explicitly invoked
native backend plus an injected one-child launcher/session. The intended native
path allocates raw pipes and passes child descriptors directly to subprocess
creation, leaving parent ownership with `AsyncFD`; no stdlib pipe-reader transport
is used. This source has **not** executed with native operations. Primary checked
19 fake tests, scoped lint and types: they pass, but do not substantiate cleanup.

Fresh enforced explore-luna `ses_f0848b830ffeIKEkYAwSTJhgNa` found three material
ownership defects: failed channel close is marked closed; cleanup can stop before
other endpoints and the child kill/reap; clean allocation failure never releases
its slot. The primary independently reproduced two using only the existing fake
backend: partial allocation returned ordinary failure, refused a subsequent launch
as occupied and then failed `aclose()` despite no live virtual fds; failure on the
last stdio close left one live virtual fd while `aclose()` returned with no reported
ownership and `cleanup_failed=False`. The fake backstop recovered that known
virtual descriptor; no OS fd, process, DNS or container was created.

Affected work is stopped for a narrow Astra ownership correction. The inspected
source SHA-256 is
`b7b83f568fd994b85e8733def3a4048cb857e8e0b642edc062db7dfbe42102ed`;
tests are `c151de25ee1380bafef3044d0ae6e86a4dee7a70859e9a5a5fd598bdc2b5199b`.
Passing fake tests do not authorize raw native launch or overall completion.

### Raw ownership correction — partially verified, residual classification blocker

Astra added explicit unknown-close state, retained until external disposition;
uncertain descriptor numbers are not retried, including a fake release/reuse
case. Cleanup attempts remaining endpoints and owned child kill/reap even after
one close fails. Clean allocation failures now release their slot. Partial
adoption records ownership incrementally; exact environment types and persistent
fatal reporting on a released session are enforced. Primary **32 fake tests pass**,
including first/middle/last close failures and allocation retry. Native operations
remain unexecuted.

Fresh enforced explore-luna `ses_f08418356ffe7JJZIKMLXduXPj` accepted those paths
but found a residual issue after the focused follow-up: the launch-timeout branch
unconditionally returns ordinary `ProcessLaunchFailure` after settlement. If late
publication encounters a failed kill and the child subsequently exits independently,
the fatal latch can remain set while settlement completes, yielding the wrong
failure classification. This is a source-reviewed hypothesis, not a newly executed
counterexample. A discriminating fake late-publication/kill-failure/independent-exit
test and fatal-latch check are still required. No further repair/review loop or
native execution followed; the bounded intervention remains **blocked**, not
returned as verified completion. Final-state aggregate and full-project gates
have not run for this raw adapter state.

### Raw adapter classification correction verified (2026-10-02)

On the owner's continuation, Astra added a fake regression that releases factory
publication only after the actual deadline stop, injects kill failure, then lets
the fake child exit independently. It reproduced the ordinary-failure defect
before the correction. Both post-settlement launch exits now check the fatal
latch before returning an ordinary launch failure. The regression verifies
drained resources, persistent fatal state and refusal of subsequent launch/close.

Fresh enforced explore-luna `ses_f063394bfffe5xAKXnXZeuuHly` confirmed this narrow
classification finding closed. Primary final-state checks: **33 raw adapter fake
tests** and the serial combined fifteen-suite run of **839 tests pass**; scoped
types, lint/format, doc freshness, gitleaks and high-severity Bandit pass. A test
optional-type diagnostic was corrected by retaining the known event reference;
the final suite and type check ran after that change. Source SHA-256:
`8e2b64bbb475adf06dc151c800215fbb358deda6f2a10ecb0d9ee8daffdec09d`;
tests: `7f7ad8d5f23ac9c1d837e5f8cd2047b5545603c863e1293770bba011758c6994`.

This closes the scoped source/fake-ownership escalation. The raw native backend,
actual DNS helper and container bootstrap remain unexecuted and unqualified.
Previously qualified kernel/synthetic-child fixtures ran in the combined suite;
no new native launch or live URL was attempted. Full-project gates remain
incomplete; ordinary offline implementation may resume under the existing
native-execution review and containment requirements.

### Controlled native raw-launch gate verified (2026-10-02)

`tests/test_web_fetch_pilot_process_io.py` adds eight controlled cases that drive
`RawLauncher` through the real `NativeBackend` (pipe allocation, descriptor close,
`AsyncFD` adoption, `create_subprocess_exec`) with one tiny isolated Python child
each: `-I -S`, empty environment, own session, absolute interpreter path, inert
marker argument and a ten-second `signal.alarm` self-bound. No shell, socket, DNS,
Docker, browser, bootstrap or application-file write is involved. The in-test
backstop kills/reaps only handles recorded at native creation; it never closes
descriptors, discovers pids or signals process groups. Cases cover a 256 KiB echo
with an observed real partial write, stdin half-close and stdout/stderr EOF; child
evidence of no inherited descriptors beyond 0–2, FIFO stdio, session leadership
and an environment limited to interpreter locale coercion; `close_stdio()`-only
EOF exit without kill; `aclose()` kill within grace; native spawn refusal releasing
all six descriptors and the slot; caller cancellation at the first launch yield and
after native creation but before handle publication; late publication after the
launch-deadline stop; and stale-session close/kill/aclose not touching reused
descriptor numbers. Every case asserts restored descriptor/task sets and drained
launcher ownership.

The source was reviewed before execution and the owner approved one run (fixture
SHA-256 `bfa2d8383f8349eb574c07467a48f08db8350c9ffb52900f2ad6b8c6585c3366`;
launcher and adapter hashes unchanged from the 2026-10-02 correction). Executed
under an external coreutils `timeout -k 5 90` watchdog with ResourceWarning and
unraisable-exception warnings as errors: **8 passed in 0.90 s**; no marker child
survived. The serial sixteen-suite pilot run then passed **847 tests**; scoped
types, lint/format, doc freshness, gitleaks and high-severity Bandit pass. No
code correction was needed.

This qualifies only the tested host-native one-child raw-pipe ownership paths.
It does not qualify Docker CLI attachment, container identity/limits/removal, the
actual DNS helper or bootstrap, streaming command ownership, a wedged native
spawn, or live containment. Full-project gates remain incomplete; production
remains direct-only. Next: the bounded streaming command executor (offline fakes
first), then the owned network-none Docker lifecycle, each as a separate scope.

## Bounded command executor — fake checks pass, review pending (2026-10-02)

The owner selected **bounded stdout capture** (not a streaming sink) and
**refuse-when-full** concurrency with no queue. `scripts/web_fetch_pilot_command.py`
adds `CommandExecutor` for trusted fixed-argv commands such as the future Docker
CLI driver: at most four simultaneous children, one `RawLauncher` each. The
monotonic deadline starts before spawn and is never reset by stream activity;
launch keeps its own bounded deadline and settle outside the I/O timeout so a
deadline cannot cancel a settle into a fatal latch. stdin (≤64 KiB) is written
with backpressure and then closed; stdout is captured to a 64 KiB cap, reading at
most one detection byte past it; stderr drains continuously into a 64 KiB
drop-oldest ring with exact total/dropped counters and is never parsed.

Outcomes are distinct. A verified exact exit returns `CommandResult`, so nonzero
is command failure for the caller. Deadline, stdout overflow, stdin refused by the
child, occupancy and executor close raise ordinary `CommandLimitFailure` after
proven clean teardown; a close-time kill or superseded launch never masquerades
as the command's exit. Launch refusal stays `ProcessLaunchFailure`. Teardown is
always an executor-owned task bounded by twice the close grace: caller
cancellation returns promptly without cancelling a settle, and any uncertain
teardown latches the executor fatal, retains the launcher visibly and refuses all
later runs. Framed browser/gateway stdout remains with the existing bridge.

The launcher gained one additive method, `RawSession.wait()`: it awaits the
retained owner reap without killing (shielded from caller cancellation; a
verification mismatch latches fatal). Without it the executor would kill a child
that closed its output before exiting and misreport its exit. Paths exercised by
the native raw-launch gate are unchanged.

Primary checks: 20 fake executor tests (a fake backend with reference-counted pipe
ends, bounded capacity/partial writes, EPIPE and scripted children) and 3 fake
`wait()` tests pass, stable across three runs; the serial seventeen-suite pilot
run passes **870 tests**; scoped types, lint/format, doc freshness, gitleaks and
high-severity Bandit pass. Two first-run failures were a fake defect (kill before a
fake child's first step published no exit) and a real classification gap (close
during launch surfaced as launch refusal); both were corrected before the reported
runs. SHA-256: command `95e7fbd5d526a031918d8fa4e1fd1a77428b747856744ab34a97c1c8408fdec0`;
process `699dcfaeac65e08b74e191dfcf33fe3b5335f683ad79daecb61175fffb09dbc9`; tests
`af79a6bede62946a0b298b0e34b444772f9abc8fbd24f082a23cc78b6bcaddc5` and
`7ba7083e7163d5cd190f4872346b72a8cdacdae2ca37bf8aa5308237921402c4`.

Fake tests do not qualify the executor natively. Independent review of the
executor and `wait()` change is pending; native executor execution needs its own
reviewed fixture gate. Docker lifecycle, actual DNS helper and live containment
remain unqualified; full-project gates remain incomplete.

### Native command-executor gate verified (2026-10-02)

The owner reviewed and approved the executor, the `RawSession.wait()` addition and
their fake tests, then approved one run of eight controlled native cases in
`tests/test_web_fetch_pilot_command_io.py` (SHA-256
`61a9c0ecb02326d7679d343522e050c393f5345bce587feed00841044bf6b97a`). They drive
`CommandExecutor` over the real `NativeBackend` with the same isolated, self-alarmed
Python children as the raw-launch gate (at most three per case; recorded-handle
backstop only). Cases: 60 KiB echo result with diagnostics; output EOF 0.2 s before
exit returning exact code 3 without kill; stdout cap plus one byte as a limit with
the child killed; a 1 MiB stderr flood leaving exactly the last 64 KiB and exact
counters; deadline stop; caller cancellation with executor-owned teardown;
refuse-when-full before allocation, then executor close reported as a limit for
both running commands; and missing-executable launch failure releasing the slot.
Every case asserts restored descriptor/task sets and drained executor ownership.

Executed under an external `timeout -k 5 90` watchdog with ResourceWarning and
unraisable-exception warnings as errors: **8 passed in 1.04 s**; no marker child
survived. The serial eighteen-suite pilot run then passed **878 tests**; scoped
types, lint/format, doc freshness, gitleaks and high-severity Bandit pass. Source
hashes were unchanged from the reviewed scope; no code correction was needed.

Not covered natively: a child that never reads stdin (a 64 KiB input fits the
default pipe exactly; fake backpressure tests cover it), Docker CLI behavior,
container identity/limits/removal, the actual DNS helper and live containment.
Full-project gates remain incomplete; production remains direct-only. Next: the
owned offline Docker lifecycle (scope C), fakes first, pending owner decisions.

## Owned offline Docker lifecycle — fake checks pass, review pending (2026-10-02)

Owner decisions: **pinned daemon** (fixed `/usr/bin/docker`, explicit
`--host unix:///run/docker.sock`, environment containing only a private empty
`DOCKER_CONFIG` directory, daemon provenance checked before any create);
**create → inspect → start -ai**; and **orphans report and block**. Read-only host
inspection found the local socket, no `DOCKER_*` variables, no client config file
and no contexts; no Docker command was run.

`scripts/web_fetch_pilot_docker.py` adds `OfflineContainerDriver` over an injected
command runner (the bounded `CommandExecutor`) and an injected attach launcher
(one `RawLauncher` child for `docker start --attach --interactive <full-id>`).
`preflight()` checks the config directory (absolute, normalized, real directory,
owned by this user, no group/other bits, empty) and pure daemon provenance from
`docker info`: Linux, cgroup v2, `runc` default runtime, memory/swap/PIDs/CFS-quota
support and seccomp. That is provenance, not enforcement evidence. Each start
lists owner-labelled containers and refuses with their full IDs if any exist; it
never removes them. One allocation at a time; the fresh unpredictable name is
recorded before create, so a create with unknown outcome is resolved by exact-name
lookup (at most two candidates) plus independent identity inspection, and a
same-name foreign container is never removed. The policy preflight runs on the
created record before anything executes. Teardown re-inspects identity, records
`State` as evidence, force-removes only the retained full ID, verifies absence by
ID listing, then closes the attach child (killing the CLI is never treated as
stopping the container). Any uncertain step latches the driver fatal, keeps the
allocation visible and refuses later starts. A late daemon-side create after an
unknown outcome is caught by the next start's orphan check. Cleanup is bounded to
six commands of at most 10 s each plus the attach close grace.

Policy correction: the create vector lacked `--interactive`, so a real create
would have failed its own `OpenStdin` preflight, and the DNS bootstrap reads its
payload from stdin. The vector now passes `--interactive`; the preflight also
requires `Config.StdinOnce == True`, because the daemon closes container stdin on
attach-client EOF only then. Whether `docker create -i --attach stdin` sets
`StdinOnce` is **unverified** until the native Docker gate; the check fails closed
rather than allowing a bootstrap that waits for EOF until its deadline.

Primary checks: 30 fake driver tests (fake daemon state behind the pinned
argv/env, injected create/inspect/rm outcomes, timeouts, foreign same-name and
mutated records; fake attach child) and 84 policy tests pass, stable across three
runs; the serial nineteen-suite pilot run passes **909 tests**; scoped types,
lint/format, doc freshness, gitleaks and Bandit (no findings) pass. The first run
exposed a real driver defect: release and fatal state were published only from the
teardown task's done-callback, so `aclose()` awaiting an already finished teardown
could return before that callback ran, reporting stale ownership or missing a
failed teardown. State is now published synchronously inside teardown; two
regressions fail against the previous behavior and pass now. SHA-256: driver
`6a53a1ab227ad91b33141535d68c58ac6d7d4141cda117cec3669f9c85358c0a`; policy
`18801dafa6d08ba7caf1fb8724798db33896205ab91843e0aaff9c82a4e3da51`; tests
`3277253bfd5bb859ce72bdeefa3e35ed99a1161f3789ee05116962f56cd2c4a3` and
`473c79b4cddd4a7df08bde8fc6f7b5f89af87441fc1f693793bbb2e1598bfbfa`.

No Docker command, container, actual DNS helper or network request ran. Owner
review of the driver and policy correction is pending; a native Docker gate
(pinned image presence, provenance, network-none create/inspect/remove with an
inert command, `StdinOnce` behavior) needs its own reviewed fixture and approval.
Full-project gates remain incomplete; production remains direct-only.

### Native Docker lifecycle gate verified (2026-10-02)

The owner approved the driver, the `--interactive`/`StdinOnce` policy correction
and one run of `tests/test_web_fetch_pilot_docker_io.py` (SHA-256
`d5179e4bcd96113a8a09e29188e52f9d2deaf6b109fc210da112e39080e83e1a`). The module is
opt-in through the existing `DAEMON_DOCKER_TESTS=1` switch, so wildcard pilot runs
skip it and never create containers. It drives the reviewed driver, executor and
raw launcher over `NativeBackend` against the pinned daemon with a private empty
`DOCKER_CONFIG`, the pinned local image (`--pull never`) and the policy's
network-none, read-only, cap-dropped, resource-limited create vector, running only
self-bounded coreutils commands (`timeout 20 cat`, `sleep 30`).

No owner-labelled container existed before the run. Executed under an external
`timeout -k 5 150` watchdog with ResourceWarning and unraisable-exception warnings
as errors: **3 passed in 2.24 s**. Observed: the pinned image ID is present locally;
daemon provenance passes; `docker create -i --attach stdin` **does** set
`Config.StdinOnce`, so the fail-closed preflight passed on the real record; an
attach round trip echoed the payload, delivered stdin EOF to the container and
returned exit 0 with recorded final state `exited/0`; a container observed
`running` was identity-checked, force-removed well inside its sleep and verified
absent. Descriptor/task sets were restored in every case. After the run no
owner-labelled container and no attach CLI process remained. The serial pilot run
without the opt-in passes **909 tests** with these 3 skipped; scoped types,
lint/format, doc freshness and gitleaks pass. No code correction was needed.

This qualifies create/preflight/attach/remove ownership for inert commands only.
It does not qualify in-container limit enforcement (cgroup/PID/memory observation),
the actual DNS helper or bootstrap payload, `fixture.invalid` resolution behavior,
the gateway, browser, TLS or any network path. Full-project gates remain
incomplete; production remains direct-only. Next per the handoff: stage D, the
actual DNS helper offline allocation gate in this network-none container.

### Actual DNS helper offline allocation gate verified (2026-10-02)

The owner approved one run of opt-in `tests/test_web_fetch_pilot_dns_container_io.py`
(SHA-256 `9265a09c7709bad934c178c962ae6290bce78fea1ed93471dbfd0827a8b0b85d`;
`DAEMON_DOCKER_TESTS=1`). Before Docker it refuses unless the trusted core, DNS and
payload modules match their reviewed hashes. Through the qualified driver it
created one policy-vector network-none container from the pinned image
(entrypoint replaced by `/usr/bin/env -i LANG=C.UTF-8 PATH=…`, so the image's
supervisord/Crawl4AI service never ran; the retained adapter stays disabled),
streamed the exact 71 KB trusted bundle to the reviewed bootstrap on stdin, and
validated the single record parent-side against hashes of the host bytes.

Read-only image inspection beforehand showed an official-Python-based image
configuration; the fixture's own identity check then confirmed
`/usr/local/bin/python` (3.12.12) and uid 999 inside the container. No
owner-labelled container existed before the run. Under an external
`timeout -k 5 150` watchdog: **2 passed in 3.44 s**. The validated record is
status `ok`: in-container checks of isolated/no-site/unbuffered flags, exact
`LANG`/`PATH` environment, only `lo`, zero IPv4/IPv6 routes and non-loopback IPv6
addresses, `CapEff` 0, no-new-privileges, seccomp mode 2, and cgroup v2
`memory.max` 128 MiB, `memory.swap.max` 0, `pids.max` 32 and `cpu.max`
`50000 100000` all passed **before** the helper existed; the actual
`StdlibSpawner`/`getaddrinfo` helper for `fixture.invalid` started, was refused
with exit 1 (`DNSFailure`), and owned cleanup completed with zero actor
jobs/tasks/processes and equal fd (7/7) and task (0/0) sets. Container exit 0,
final state `exited/0`, removal verified. A malformed bundle (one extra key) was
refused by the bootstrap before staging with exactly the fixed failure record and
exit 1. No owner-labelled container or attach CLI remained afterwards; parent
fd/task sets were restored. Pilot run without the opt-in: **909 passed,
5 skipped**; scoped types, lint/format, doc freshness and gitleaks pass.

This closes handoff stage D for the reviewed single-attempt payload: actual helper
publication/exit/cleanup and in-container limit observation in the network-none
allocation. It does not exercise repeated in-container cancellation (the payload
makes one attempt), public DNS, numeric dialing, peer identity, TLS, the gateway
actors under supervision, the browser producer or any live source. Full-project
gates remain incomplete; production remains direct-only. Next is handoff stage E
(test-only gateway dialing/TLS topology and browser producer integration), which
needs a fresh topology design and owner decisions before any implementation.

## Stage E owner decisions (2026-10-02)

Before any stage E implementation the owner selected: a **staged topology**:
E1 in-host loopback fixtures with test-only transport classes that live only under
`tests/`, while the real dial path keeps production policy and a live construction
path that cannot accept fixture resolvers/connectors; E3 later on a disposable
`--internal` Docker network for the two-container run. The invalid-certificate TLS
fixture **generates a throwaway self-signed certificate at test time** with the
existing `cryptography` dependency in pytest's private temporary directory (no
new dependency, nothing committed). The browser producer reuses the **same pinned
image** with the recorded qualified seccomp profile. Work starts with **gateway
dialing and TLS** (E1), then the offline browser entrypoint (E2), then two-container
integration (E3). No fixture exception may ship in `scripts/`; production remains
direct-only.

### Stage E1 in-host tunnel/TLS gate verified (2026-10-03)

The owner approved one run of `tests/test_web_fetch_pilot_gateway_tls_io.py`
(SHA-256 `4ffb19badf3ca2f34ed0bec924c64ef73e22bf0590a9f183d181c59d79dfdbfe`).
In one process, on loopback only, a TLS client reaches the relay's real
`LoopbackAcceptor`, relay and gateway exchange frames over two real pipes
(`FDFrameIO`/`AsyncFD`), and the gateway applies its real manifest/address policy to
injected resolver answers. The only test-only transport, `LoopbackFixtureConnector`
(under `tests/` only), records every numeric dial and maps the single validated
answer `8.8.8.8:443` to a local TLS echo fixture after asserting the real socket's
peer is that fixture. Certificates are throwaway self-signed EC keys generated at
test time with the existing `cryptography` dependency in pytest's `tmp_path`.
Relay, gateway, acceptor, I/O and core hashes matched the handoff before the run.

Under an external `timeout -k 5 120` watchdog with ResourceWarning and
unraisable-exception warnings as errors: **10 passed in 0.17 s**. Verified: a
default-verifying client rejects the self-signed certificate through the real
tunnel (one dial, to the validated candidate only); a client trusting only that
certificate completes TLS and an echo round trip, with relay and gateway byte
counters equal in both directions; a trusted certificate for another name still
fails hostname verification; mixed public/private, metadata, loopback,
IPv4-mapped IPv6 and public IPv6 answers return `502` with **no dial**; a
rebinding second admission is resolved afresh and refused, with only the first
public answer ever dialed; an unlisted host is refused with no resolution or dial.
Every case restored fd/task sets with no pending actor tasks or cleanup failure;
no listener survived. The pilot run now passes **919 tests, 5 skipped** (opt-in
Docker files); scoped types, lint/format, doc freshness and gitleaks pass. No code
correction was needed.

Not qualified: peer identity on real sockets (a loopback socket cannot truthfully
present the candidate; deferred to E3's internal network), `NumericConnector`'s
real socket path, DNS, containers, the browser, and any external connection. Next
is E2, the offline browser entrypoint in a network-none container.

### Stage E2 owner decisions (2026-10-03)

The browser entrypoint reaches the read-only, network-none browser container as a
**length-prefixed, hash-checked trusted bundle on stdin**, read by a small reviewed
bootstrap that stages it in `/tmp` and hands the remaining stdin to the entrypoint
as the frame stream (no image, mount or `docker cp` change). The qualified browser
seccomp profile (SHA-256 `ec97bb9f…1915`) is **copied byte-for-byte into the repo
and hash-pinned**, checked before every create. Browser scratch `/tmp` is
**128 MiB**, matching the recorded sandbox evidence, alongside the approved
1 GiB/no-swap, 128-PID, one-CPU, 256 MiB shm limits. E2 covers synthetic extraction
and `chrome://sandbox` diagnostics with the relay integrated, a RESULT over framed
stdout, and one proxied HTTPS navigation through an in-host fixture gateway to an
untrusted TLS fixture that Chromium must reject; ordinary and basic-stealth modes,
Patchright optional. Built in reviewed scopes: E2a policy/profile (pure), E2b
bootstrap/entrypoint source, E2c driver policy parameterization and native gate.

### Stage E2a browser policy and pinned profile — pure checks pass (2026-10-03)

`scripts/web_fetch_pilot_browser_seccomp.json` is the qualified browser profile
copied byte-for-byte (SHA-256 `ec97bb9f172a136a19a3af5eb0f6ed1236476e015e6c2966196f4a85bd9f1915`,
re-verified after pre-commit). The container policy module adds pure browser
functions alongside the unchanged gateway ones: `browser_seccomp_option` (hash-checks
the pinned bytes and returns the exact compact-JSON `SecurityOpt` entry the Docker CLI
stores, confirmed against the recorded browser run), `browser_create_arguments`
(attach/interactive, network none, read-only, `appuser`, all capabilities dropped,
pinned seccomp plus no-new-privileges, 1 GiB/no swap, one CPU, 128 PIDs, 256 MiB shm
with private IPC, private cgroup namespace, 128 MiB `/tmp`, no restart/logs/pull),
`require_browser_identity` and `require_offline_browser`. Browser and gateway names
use distinct patterns and each identity check refuses the other's names. 22 new pure
tests (exact vector, pinned bytes, tampered profiles, 18 boundary mutations, foreign
seccomp options) pass with the 84 gateway-policy and 30 driver tests; the pilot run
passes **941 tests, 5 skipped**; scoped types, lint/format, doc freshness, gitleaks and
high-severity Bandit pass. Policy SHA-256 `5d0fe98a24b594f2a065743fb6f87429120563be23f2a9f34b225607001e46fb`;
tests `01c2896a01c2ffdc8677eb12b184b15324d2f3a8ff0143a4c7adebc60ee875a6`. No container ran.

### Relay RESULT API — fake checks pass, owner review pending (2026-10-03)

Owner decisions: the relay gains a reviewed RESULT API (instead of a shared writer
outside it), and E2 proves browser certificate rejection from the supervisor side
(the in-host TLS fixture's observed alert plus a final `error` RESULT), never by
parsing browser diagnostics. `Relay.finish_result(content, final)` is a single call
by the browser entrypoint after its browser context closes. It refuses shapes before
any state change (bytes only; final 1–4096 bytes; strict UTF-8 content; RESULT
payload and source totals within the collector's 1 MiB quotas). From the call on it
closes the acceptor (without ending the run), refuses any handshake that has not yet
emitted OPEN with `503` and no IPC identity, and waits until every existing local
stream retires, so no OPEN can follow RESULT. It then emits content chunks (subtype 1,
at most 16 KiB − 1 each) and one final record (subtype 2) on control stream 0 through
the relay's own scheduler, each committed in order, and ends the run with the new
`RelayStatus.RESULT_COMMITTED` (reason EOF). That status is never article success.
Stop, deadline or teardown cancellation before the final commit raises `RelayFailure`
and leaves the run `INCOMPLETE`; caller cancellation propagates unchanged.

11 new fake tests pass with the 86 existing relay tests, three runs in a row,
including feeding the emitted frames into the real supervisor `ResultCollector`
(40 KB of multi-byte content split across chunk edges reassembles exactly), waiting
for a pending tunnel, refusing a late handshake without OPEN, six refused shapes,
before-run/twice refusal and a deadline before final commit. The previously approved
E1 loopback fixtures pass against the changed relay. Pilot run: **952 passed,
5 skipped**; scoped types, lint/format, doc freshness, gitleaks and high-severity
Bandit pass. Relay SHA-256 `edae904753bc1b28815862fe2e72e5f52e25c06c161c18c4ffd5e5664311b128`;
tests `ae4b2edc8ccb3142931e5869fb975e0969f8e609c9889273e744eabd0a6aea99`. This modifies a
reviewed concurrency module and needs owner review before the E2 entrypoint builds on it.

### Stage E2b/E2c browser bootstrap, entrypoint and driver policy — fakes pass (2026-10-03)

The owner approved the relay RESULT API and E2b/E2c. `scripts/web_fetch_pilot_browser_entrypoint.py`
is a real, linted and type-checked module staged into the browser container; it loads
Playwright and the image's stealth adapter by fixed name only inside the container.
It duplicates stdin/stdout into non-inheritable frame descriptors and points fds 0/1
at `/dev/null`/stderr, so no child or stray print can corrupt frames. It refuses
(exit 3) unless it runs non-root with only `lo` and no IPv4 routes; runs one relay on
`127.0.0.1`; launches Chromium with its sandbox, an explicit relay proxy with implicit
loopback bypass removed (`<-loopback>`), a fresh context, service workers and
downloads blocked and HTTPS verification intact; refuses (exit 4, RESULT `error`, no
navigation) unless the synthetic extraction and the recorded `chrome://sandbox` lines
match; then navigates the single trusted URL and hands one bounded RESULT to
`Relay.finish_result`. Failure statuses never carry content or error text; an
off-manifest final URL is `blocked`; success content is normalized and truncated on a
UTF-8 boundary under the quota. Exit 0 means every check passed and RESULT was
committed, never article success. Diagnostics are bounded stderr only.

`scripts/web_fetch_pilot_browser_payload.py` builds the length-prefixed strict JSON
bundle of the exact seven trusted module files plus run values (512 KiB cap), and the
browser `env -i` command: the recorded six-key environment and `python -I -u -c`
(site kept for Playwright, unlike the DNS bootstrap's `-S`). Its bootstrap literal reads
exactly one bundle from fd 0 with raw `os.read` (never buffered stdin), stages it in an
owned `/tmp` directory, statically imports the entrypoint and never writes stdout.
The Docker driver is parameterized by a trusted `ContainerPolicy` (gateway default,
unchanged behavior) with `browser_policy`, which hash-verifies the pinned profile once
and compares the stored seccomp option with those exact bytes at preflight.

Checks: 20 pure E2b tests (run config, classification and final records accepted by the
real `ResultCollector`, sandbox/synthetic evidence lines, exact bundle, AST-extracted
bootstrap functions over an in-memory fd 0 reading exactly one bundle and leaving
frame bytes unread, nine malformed bundles, no stdout writes) and 3 browser-policy
driver tests pass with all prior suites: **975 passed, 5 skipped**; scoped types,
lint/format, doc freshness, gitleaks and Bandit (no findings in the new modules) pass.
SHA-256: entrypoint `e9fb965741c315138765b0b275ba9b804c1616c4edfea5de887dd23cd13e029d`;
payload `868433fd7beee51a2240185ea58d405c6311980ca6a3c672babe559623ee2d57`; driver
`2f16669063dc911967f4ffa05e811409d92ff3c2045474132a7957efe87d94a4`. The opt-in native
gate `tests/web_fetch_pilot_browser_container_pending.py` (`931298914322008cf824c19293ec82966734796e2838b16c68f55ce8ccb8dced`)
is written but not executed; it awaits owner approval.

### Stage E2c offline browser container gate verified (2026-10-03)

The owner approved one run of opt-in `tests/test_web_fetch_pilot_browser_container_io.py`
(SHA-256 `931298914322008cf824c19293ec82966734796e2838b16c68f55ce8ccb8dced`). All E2
source hashes matched before the run; no owner-labelled container existed. Under an
external `timeout -k 5 300` watchdog with ResourceWarning and unraisable-exception
warnings as errors: **2 passed in 6.58 s** (ordinary and basic-stealth, one browser
per container). Per mode, supervisor-side evidence: the bundle was accepted by the
bootstrap; the browser stream ended with final RESULT plus EOF through `IPCBridge`
(8 browser and 8 gateway frames); the collector's candidate is status `error` with no
content and `final_url` equal to the trusted URL; the host-loopback TLS fixture saw one
handshake attempt, completed none and received Chromium's `certificate_unknown` alert
(`SSLV3_ALERT_CERTIFICATE_UNKNOWN`); the gateway dialed exactly once, only the validated
candidate; exit code 0 from both the attach CLI and Docker's recorded state, so the
in-container isolation, `chrome://sandbox` and synthetic checks passed and RESULT was
committed; identity-checked removal verified, no owner-labelled container or attach CLI
remained, and parent fd/task sets were restored. Untrusted stderr diagnostics, printed
for the record only, were consistent (sandbox and synthetic checks true, navigation
error category, relay `result_committed` with three OPENs). Three relay OPENs against
one dial means two CONNECTs were refused before dialing; which hosts they named was not
recorded. Pilot run without the opt-in: **975 passed, 7 skipped**; scoped gates pass.
No code correction was needed.

This closes E2: the browser producer works end to end offline with the relay, bridge,
collector and real gateway policy, and Chromium enforces certificate verification
through the proxy. It does not establish peer identity on real sockets,
`NumericConnector`'s real dial path, public DNS, a successful article extraction, or
containment against a live network. Next is E3: the two-container run on a disposable
`--internal` Docker network.

### Stage E3 owner decision (2026-10-03)

E3 uses a disposable `--internal` Docker network with a small **public-looking
(global-unicast) subnet**, the TLS fixture container aliased as the manifest host, and the
**unchanged** gateway, `DNSResolver`/`StdlibSpawner` and `NumericConnector`: no fixture
exception anywhere, and real-socket peer identity is checked. The address range is
shadowed only inside that isolated network. Before any gateway run, a reviewed probe must
show Docker's embedded DNS on the internal network does not forward unknown names to host
resolvers. The browser stays network-none.
Run scoping (owner decision, 2026-10-03): every container and network a run creates
carries a second label, `daemon.reader-pilot.run=<fresh 128-bit token>`, checked by
identity inspection. The orphan check refuses on any owner-labelled resource whose run
token differs or is missing, so crashed earlier runs still block while a run's own
concurrent resources do not.

### Stage E3a-1 run scoping — fakes pass, owner review pending (2026-10-03)

Gateway and browser create vectors take an optional `run_token` and, when given, add
`--label daemon.reader-pilot.run=<token>` right after the owner label; identity and
preflight checks then require that exact token (a wrong or missing token is refused).
`ContainerPolicy` callables now receive the run token. `OfflineContainerDriver` takes
an optional `run_token` (fresh 128-bit token by default, so existing callers and the
approved native fixtures need no change; malformed tokens refused) and always passes
it. `start` refuses through the new `foreign_ids()`: owner-labelled containers minus
those labelled with this run's token, from two exact label-filter listings (no output
parsing beyond full IDs). `owned_ids()` still lists every owner-labelled container.
New tests: run label placement, legacy vectors unchanged, malformed tokens, identity
with matching/wrong/missing tokens (gateway and browser), a same-run sibling not
blocking while the same container blocks a driver from another run (never removed),
fresh distinct default tokens. Pilot run **978 passed, 7 skipped**; scoped types,
lint/format, doc freshness, gitleaks and high-severity Bandit pass. Policy SHA-256
`37752bd629e10a8ae83d914767a3bdd4276c7f808dcb736f6aa87574523b63c0`; driver
`c1ea48e015518e7234b93ac1cc3a3ca82581468fd124e2c776127b8afb55497a`. The opt-in native
Docker fixtures were not re-run against this change.

### Stage E3a-2 owned internal network — fakes pass (2026-10-03)

The owner approved E3a-1 and committing the pilot on `feat/web-fetch-fallback-pilot`.
The container policy adds pure network functions: `network_create_arguments` (bridge,
`--internal`, `--ipv6=false`, subnet `1.2.3.0/29`, owner and run labels, and two options:
`gateway_mode_ipv4=isolated` so the host-side bridge gets no address and the host gains
no route to the subnet, and masquerade disabled), `require_network_identity` and
`require_offline_network` (exact driver/scope/internal/IPv6/attachable/ingress values,
exact option equality failing closed, one IPAM config on the fixed subnet with gateway
absent or `1.2.3.1`). Whether Docker 29.8.1 accepts and records the isolated gateway
mode exactly this way is unverified until the native probe. `OwnedNetwork` mirrors the
container lifecycle: run-scoped orphan check (report and block), name recorded before
create, unknown create resolved by exact name plus identity (at most two candidates;
substring and foreign same-name networks never removed), exact preflight, then
identity-checked `network rm` of the full ID with verified absence; teardown is an owned
task, state is published synchronously, and any uncertainty is fatal and retained.
16 new policy and 9 new lifecycle fake tests pass; pilot run **1002 passed, 7 skipped**;
scoped types and high-severity Bandit pass. Policy SHA-256
`edcc232fbee532db94886a2c8acb0bf26091cc0b75179a3032b9baedd454d471`; driver
`1aeacd94bfbf522d968b2fd76a1ff129761f9f2194eb42ddb2545ad9c2717f31`. No network was created.

### Stage E3b networked role and network probe — prepared, not executed (2026-10-03)

The gateway create vector and preflight were refactored so a third, **networked** role
reuses every gateway limit and replaces only the network attachment:
`networked_create_arguments` (names `daemon-gateway-…`/`daemon-fixture-…`, the owned
network by name, optional canonical `--network-alias`, run label required) and
`require_networked` (exactly one network endpoint, the owned network; network ID and
IPv4 address may be empty before start, otherwise the owned network and an address in
`1.2.3.0/29`; no IPv6; exact alias equality failing closed). The driver gains
`networked_policy`. Existing gateway vectors and checks are unchanged (all prior tests
pass); 14 new networked-role tests pass; pilot run **1016 passed, 7 skipped**.

The opt-in probe `tests/web_fetch_pilot_network_probe_pending.py`
(`972653aac36b8d60943da705d9914800adac15c6462c37af1a74e3a87091049b`) creates one owned
network and one fixture-role container aliased `openai.com`. Its stdlib program sends raw
DNS queries only to Docker's embedded resolver: first `daemon-pilot-probe.invalid`
(NXDOMAIN would suggest upstream forwarding; SERVFAIL/REFUSED/no answer would not), then,
only if not NXDOMAIN, the alias, which must return the container's own subnet address. It
also reports nameservers, interfaces, routes (no default route allowed) and its own
address. The host side checks read-only that no `1.2.3.x` route or address appears.
Worst case, one query naming `daemon-pilot-probe.invalid` reaches an upstream resolver.
On refusal it prints whitelisted configuration fields only. Awaiting owner approval.

### Stage E3b internal-network probe verified (2026-10-03)

The owner approved one run of opt-in `tests/test_web_fetch_pilot_network_probe_io.py`
(SHA-256 `972653aac36b8d60943da705d9914800adac15c6462c37af1a74e3a87091049b`). No
owner-labelled container or network existed beforehand. Under `timeout -k 5 150`:
**1 passed in 2.00 s**. Docker 29.8.1 accepted the network vector, and the exact network
and networked-container preflights (isolated gateway mode and masquerade options, IPAM,
alias equality) passed on the real records. The embedded resolver answered
`daemon-pilot-probe.invalid` with **SERVFAIL** (not NXDOMAIN): internal-network names are
not forwarded upstream. The alias `openai.com` resolved to the container's own address
`1.2.3.1` (isolated mode reserves no gateway address). Inside: nameserver `127.0.0.11`,
interfaces `eth0` and `lo`, a single subnet route and **no default route**. The host
never gained a `1.2.3.x` route or address before, during or after. Exit 0; container and
network removed and verified absent; no owner-labelled leftovers, no attach CLI, fd/task
sets restored. Pilot run without opt-in **1016 passed, 8 skipped**. This establishes the
E3 addressing premise; the gateway and TLS fixture roles are next (E3c).

### Stage E3c gateway entrypoint and two-container gate — prepared (2026-10-03)

`scripts/web_fetch_pilot_gateway_entrypoint.py` constructs the unchanged `Gateway` with
the real `DNSResolver(StdlibSpawner(sys.executable))` and `NumericConnector`, an explicit
inventory (empty only for this non-live fixture run) and framed stdio on private
non-inheritable descriptors; it refuses (exit 3) unless non-root with only `eth0`/`lo`
and no default route, always closes the resolver, and exits 0 only with clean gateway and
resolver ownership. The gateway bundle and command (`-I -S -u`, `PATH`/`LANG` only) reuse
the browser builder; the gateway bootstrap is derived from the qualified browser bootstrap
by four exact, once-only substitutions (the browser bootstrap bytes are unchanged, and
reversing the substitutions restores them exactly). 7 pure tests pass; pilot run **1023
passed, 8 skipped**. The opt-in gate `tests/web_fetch_pilot_two_container_pending.py`
(`4d3949b01bee9db9fcab65e6cfb976ea6682887e77489b011032d157e8e9dbf2`) runs one owned
network, a TLS fixture container aliased `openai.com` on port 443, the gateway container
and the network-none browser container under one run token, with no test-only transport
anywhere. It awaits owner approval.

### Stage E3c first run failed; exit-code defect corrected (2026-10-03)

The owner approved one run of `tests/test_web_fetch_pilot_two_container_io.py`
(`4d3949b01bee9db9fcab65e6cfb976ea6682887e77489b011032d157e8e9dbf2`). It **failed** after
37.33 s and was not retried. The network was created and all three containers started;
the TLS fixture reported ready. The browser's automation raised Playwright
`TargetClosedError` before any CONNECT: relay OPENs 0, gateway read/written 0 bytes and
ended on EOF with clean ownership, the fixture saw **0 handshake attempts**, and the
final RESULT was `error` without content. All three containers exited 0 and, with the
network, were removed and verified absent; no attach CLI survived and parent fd/task sets
were restored. The cause is not determined: the diagnostic recorded only the exception
type, and the same browser configuration passed twice in E2c.

The run exposed a real defect: the browser entrypoint's generic failure path sent the
`error` RESULT but left the exit code 0, contradicting the documented meaning "every
in-container check passed". All browse failures now go through a pure `browse_exit`
(0 only without an unexpected failure; refusals keep codes 3/4; anything else is the new
code 5), and the bounded diagnostic adds the last stage reached (`launch`, `context`,
`stealth`, `synthetic`, `sandbox`, `navigate`, `extract`), never error text. A pure test
covers the classification and that every stage is recorded. Pilot run **1024 passed,
9 skipped**. Entrypoint SHA-256
`755dc5e3ea7fcd16af097695184aa62ec7456e2e6d43bbfeb14205a80e28c75e`. The E2c browser gate
has not been re-run against this change. A second E3c run needs owner approval.

### E2c requalified; E3c second run reached the fixture, then failed (2026-10-03)

On the owner's direction the single-container E2c gate was re-run once against the fixed
entrypoint: **2 passed in 6.86 s** with the same supervisor-side evidence as before. E3c was
then re-run once: **failed in 7.18 s**, no leftovers, not retried. This time the full
production path worked: the gateway container's real `DNSResolver` resolved `openai.com`
through Docker's embedded DNS, the real `NumericConnector` dialed the fixture container and
the real peer check passed, Chromium's handshake reached the fixture through relay, bridge
and gateway (1 attempt), and Chromium rejected the certificate (`certificate_unknown`,
0 handshakes). The gateway then ended its whole run with reason `transport_error`
(717 bytes read, 1846 written), closing its stdout before the browser's final RESULT; the
bridge ended `gateway_eof`, the browser's `finish_result` raised `RelayFailure` and the
browser correctly exited 1 (the corrected exit classification at work). Gateway and fixture
exited 0; everything was removed.

Cause, verified in code: any exception on one tunnel's socket read or write
(`Gateway._transfer_read`/`_transfer_write`) closes the byte budget and ends the entire run
with `IO`, by the fail-closed rule that unknown-count operations are never refunded. The
specific exception in this run is not recorded (diagnostics carry no error detail);
a TCP reset from the fixture's close after the TLS error is the likely but unconfirmed
trigger. Consequence for live readiness: one peer resetting one tunnel would abort the whole
fetch. This is an owner decision because per-stream abort changes reviewed gateway/relay
protocol semantics.

### Proposed: per-stream abort (owner chose the approach; design awaiting approval)

Current behavior (verified in code): any exception on a tunnel socket ends the whole run on
**both** endpoints: `Gateway._transfer_read/_transfer_write` close the budget and finish
`IO`; the relay's guarded stream tasks finish `IO` too (e.g. when Chromium cancels a
request). The tunnel ledger already accepts CLOSE from either side in any non-terminal
state and discards queues; the gateway already treats an incoming browser CLOSE
abortively; the relay accepts CLOSE only when fully drained.

Proposal, no codec or frame-type change:

1. **Known-zero resets only.** A tunnel socket operation that raises
   `ConnectionResetError`, `BrokenPipeError` or `ConnectionAbortedError` from the actual
   read/write syscall transferred no bytes (AsyncFD/SocketConnection return a count or
   raise; no partial result is lost), so its reservation reconciles to zero. Every other
   failure (cancellation, `TransportError`, wrong count, budget, protocol) stays fatal for
   the run, unchanged.
2. **Abort handshake on one stream.** The endpoint that sees such a reset aborts only that
   stream: closes its socket, cancels that stream's tasks, drops its queued rows, and sends
   CLOSE. A CLOSE received outside the normal fully-drained conditions is an abort: the
   receiver tears that stream down the same way and replies with exactly one CLOSE once it
   has stopped emitting for that ID. The initiator discards, but still shape-checks and
   counts against the frame/DATA limits, frames for that ID until the reply CLOSE, then
   retires it. Normal gateway CLOSE (both halves closed and drained) keeps its current
   no-reply meaning; frame order on the pipe keeps both endpoints' views consistent.
3. **Bounds unchanged.** At most four streams can be aborting; aborted streams still count
   toward the 40 OPEN attempts, frame limits and the 32 MiB budget; IDs are never reused;
   a second abort, a reply for an unknown ID or any frame after the reply is a protocol
   failure for the run.
4. **Changes** are confined to `Gateway` and `Relay` (plus their tests); bridge, collector,
   ledger and codec are unchanged. Fake tests must cover reset-on-read and reset-on-write at
   each endpoint, crossing aborts, in-flight DATA/WINDOW discarded during the handshake,
   abort while OPEN is pending, and that non-reset failures remain fatal. Then the E1
   loopback, E2c and E3c gates are re-run with approval.

### Per-stream abort implemented — fakes pass (2026-10-03)

The owner approved the design reusing CLOSE. Implementation in `Gateway` and `Relay` only
(codec, ledger, bridge and collector unchanged):

- A `ConnectionResetError`/`BrokenPipeError`/`ConnectionAbortedError` raised by a tunnel
  socket's read, write or `shutdown_write` reconciles its reservation to zero (the relay
  does not count it as an unknown write) and aborts only that stream. Every other socket
  failure remains fatal for the run, unchanged.
- An owned abort task cancels that stream's tasks, drops rows still queued, lets a row the
  sender is already publishing complete, sends one CLOSE, and retires the stream. Emitters
  now await their row through `asyncio.shield`, so cancelling an emitter never cancels an
  in-flight row (without this, an abort racing a publication failed the whole run).
- **Refinements found during implementation, recorded for review:** (1) for a stream already
  being torn down, a `CancelledError` or closed-descriptor `TransportError` from its own
  pending read/write also reconciles to zero: the qualified adapters move bytes only inside
  the synchronous syscall, so an interrupted readiness wait moved nothing; elsewhere the
  unknown-count rule is unchanged (including the existing partial-write-before-cancel test).
  (2) The relay replies with exactly one CLOSE to **every** gateway CLOSE, normal or abort,
  because it cannot reliably tell them apart (a gateway reset during its final
  `shutdown_write` looks drained to the relay); the gateway records a normally closed ID at
  commit, before publication, and consumes exactly one reply for it.
- Each endpoint discards (still shape-checked and counted; relay-side DATA still counts
  toward the IPC DATA ceiling) frames for an ID awaiting its abort reply; crossing CLOSEs
  answer each other; OPEN reusing an aborting ID, a second reply or any frame after the
  reply is a protocol failure; at most four unanswered aborts per endpoint; a stream with no
  published OPEN is torn down locally without frames; `finish_result` also waits for
  outstanding aborts so RESULT stays last.

Tests: 12 new gateway and 9 new relay abort cases; four existing tests updated to the
approved contract (a gateway CLOSE before full drain is now an abort with one reply; a
browser CLOSE during admission gets one reply; interop waits for the reply-driven
retirement). Relay and gateway suites pass **185 tests three times in a row**; the pilot run
(including the approved E1 loopback fixtures against the changed actors) passes **1043,
9 skipped**; scoped types, lint/format, doc freshness, gitleaks and high-severity Bandit
pass. Gateway SHA-256 `4c2cead4d83c21d4d2e31a8937dbb9d0a14056cfeffe47c3b07305992fce0bb3`;
relay `0687f23b32a89d442b0f334b36c1554f927fe3e2745e269411b83677ddfe6acf`. The E2c and E3c
native gates have not been re-run against this change.

### Per-stream abort reruns: E2c passes, E3c still failed; peer-recheck cause found (2026-10-03)

With owner approval the gates were re-run once each against the abort implementation.
**E2c: 2 passed in 5.88 s** (browser frames now include the single reply CLOSE). **E3c:
failed in 6.03 s** exactly as before (gateway `transport_error` after the fixture's TLS
rejection; browser exit 1; no leftovers), not retried. A loopback-only check confirmed the
cause: after a TCP reset, `recv` raises `ECONNRESET` (now handled), but `getpeername()`
raises `OSError ENOTCONN`, and both actors re-checked the peer through
`SocketConnection.getpeername()` before every tunnel operation, so the reset surfaced as an
ordinary fatal `OSError` one step before the syscall the abort handles. The acceptor had the
same exposure for a client resetting just after `accept`.

Correction: `SocketConnection` records the peer once (the connector's verified
`(ip, 443)` after connect, the acceptor's verified loopback peer after accept, or a single
`getpeername` at construction) and never re-queries; a connected TCP socket's peer is
immutable, and the actors' per-operation check still runs against that record (fake
peer-mutation tests unchanged). The acceptor closes and skips a client whose
`getpeername` fails before verification and keeps accepting. New fake tests cover both.
Pilot run passes **1045, 9 skipped** including the approved E1 loopback fixtures. E2c and
E3c need another owner-approved run.

### Stage E3c two-container gate verified (2026-10-03)

After the peer-record correction, with owner approval: **E2c 2 passed in 9.91 s**, then
**E3c 1 passed in 8.18 s**. No owner-labelled container or network existed before either run
and none remained after; no attach CLI survived; parent fd/task sets were restored. E3c
supervisor-side evidence: one owned internal network (`1.2.3.0/29`, isolated gateway mode)
carried the TLS fixture container (alias `openai.com`) and the gateway container; the
gateway's **unchanged** `Gateway`, real `DNSResolver`/`StdlibSpawner` (Docker embedded DNS)
and real `NumericConnector` (numeric dial plus real peer check) tunnelled the network-none
browser's CONNECT to the fixture; Chromium rejected the self-signed certificate (fixture: one
attempt, no completed handshake, `certificate_unknown`); the fixture's reset was absorbed by
the per-stream abort and the gateway ended on normal EOF (717 bytes read, 1750 written, clean
resolver and actor ownership); the bridge ended with final RESULT plus browser EOF; the
collector's candidate is `error` with no content; all three entrypoints exited 0 with Docker
recording `exited/0`; identity-checked removal of every container and the network verified.
Untrusted stderr diagnostics (record only) were consistent.

This closes E3 and stage E's offline qualification: real-socket peer identity, the real
dial and DNS helper path, per-stream abort and the browser producer now pass together in an
isolated topology with no test-only transport. Not established: any public network path,
deployment-address inventory, successful extraction against a real page, Patchright, or
full-project gates. Production remains direct-only. Next is handoff stage F: fresh security
review of the actual runner/gateway/relay code and full-project gates, then — only with
explicit owner approval — the bounded fixed-URL live comparison.

### Security review and hardening (2026-10-03)

A focused security review of the pilot commits (the security-review skill, scoped to
`scripts/web_fetch_pilot_*` and the pinned profile) found **no high-confidence
vulnerabilities**. It checked destination policy, frame and RESULT handling, Docker argument
injection, preflight and seccomp scope, owned-resource removal, bootstrap staging, TLS and
secrets. On the owner's request its three below-the-bar items are now implemented:

1. **Inventory.** The gateway run carries an explicit `topology`. `internal` (the owned
   `--internal` network, no route out) is the only topology that runs and may carry an empty
   inventory. `egress` must carry a non-empty, well-formed owned inventory (parsed by the
   gateway's own `parse_owned_inventory`) and is still refused at runtime, before any frame,
   until live egress is separately approved. An empty inventory can therefore never accompany
   a run with a route out.
2. **Seccomp.** Daemon provenance now requires a `name=seccomp` entry with an explicit profile
   that is not `unconfined`. Both container entrypoints check their own `/proc/self/status`
   (`Seccomp: 2`, `NoNewPrivs: 1`, `CapEff` all zero) as part of the isolation refusal (exit 3)
   before any network or frame work.
3. **Requests for other sites inside a tunnel.** The browser cannot resolve DNS, so browser-side
   IP pooling cannot occur. As defence in depth for reuse of an open tunnel, the browser context
   routes every request through a guard that aborts anything that is not `https` to a manifest
   host on port 443 (subdomains are not implied); only a blocked count reaches diagnostics. The
   gateway's CONNECT allowlist remains the enforcement boundary.

New pure tests cover the topology and inventory rule, the runtime refusal ordering, both status
checks, the request guard and three rejected seccomp option forms; the E3c gate's gateway run
now declares `topology: internal`. Pilot run **1053 passed, 9 skipped**; scoped types, lint and
high-severity Bandit pass. The E2c and E3c native gates have not yet been re-run against these
changes.

### Post-hardening gate runs (2026-10-03)

With owner approval, after the hardening commit: **E2c 2 passed in 6.00 s**. The real
daemon passed the stricter seccomp provenance check, both browser entrypoints passed the new
status check, and the request guard blocked 0 page requests (the two extra relay CONNECTs
per run come from Chromium's own connection handling, not page requests, and are refused
before any dial). **E3c failed in 36.38 s**, not retried: the browser entrypoint reported
`TargetClosedError` at stage **`launch`** (Chromium died before any page or network
activity) and correctly exited 5 with an `error` RESULT; gateway and fixture ended cleanly
(fixture 0 attempts); everything was removed with no leftovers. This matches the very first
E3c run. Observed so far: the browser launched in all 5 single-container E2c runs, while
E3c failed at launch in 2 of 5 runs (the other runs reached the fixture). The cause is not
determined: the bounded diagnostics deliberately omit Chromium's log lines.

### Launch-log capture and E3c repeat runs (2026-10-03)

On the owner's choice the browser entrypoint now records, for launch-stage failures only, the
last 2 KiB of the launch error in printable ASCII (Playwright appends Chromium's own startup log;
no page content exists yet), as stderr diagnostics only. E3c was then run up to three times,
stopping at the first failure: **all three passed** (9.19 s, 6.79 s, 6.18 s), each with one
fixture attempt rejected (`certificate_unknown`), a clean gateway EOF, all three entrypoints
exiting 0 and no leftovers. The intermittent launch failure did not recur, so its cause remains
uncaptured. Tally across E3c runs whose code included the per-stream abort and the peer-record
fix: 4 passed, 1 failed at Chromium launch. Across all 8 E3c runs, Chromium died at launch
twice; the other two failures were the explained, since-fixed transport issues. The launch flake is a known open item for stage F, not a containment failure:
the browser refuses cleanly (exit 5, `error` RESULT) and nothing is left behind.

### Full-project gates in a clean worktree (2026-10-03)

`scripts/local_ci.sh` ran in a clean detached worktree of the branch at `805d32ce` (no local
changes; locked `uv sync`, `npm ci`), so unrelated uncommitted work in the main checkout could
not affect results. **Every gate covering pilot code passed; three blocking gates failed for
pre-existing reasons outside the pilot, which changes none of the files involved:**

| Family | Result |
|---|---|
| Backend | ruff check, ruff format, basedpyright, high-severity Bandit pass; Bandit inventory reports existing low findings (non-blocking); **pip-audit fails**: `litellm 1.96.0`, `urllib3 2.7.0`, `virtualenv 21.4.1` advisories, which current `main` locks identically (#376); **pytest fails**: 4994 passed, 125 skipped, 2 failed, both in `tests/test_model_roster_live.py`, whose fixtures hard-code period `2026-09` while the clock is in `2026-10` (#379); no pilot test failed |
| Frontend | npm ci, type-check, lint, format, tests and build pass; **audit fails** on the `braces` advisory (#418), already fixed on `main` by `d3f50d58` but not on this branch's base |
| Aggregate | feature matrix and pre-commit across all files (doc freshness, ruff, gitleaks) pass |

Evidence was added to #376, #379 and #418. The Definition of Done is therefore not met for
reasons outside the pilot; rebasing onto current `main` resolves #418, while #376 and #379
need their own fixes. Production remains direct-only.

### Rebased onto main: full-project gates pass (2026-10-03)

The earlier comparison used a stale local `main`. The 14 pilot commits (they touch only
pilot scripts, pilot tests and this document) were replayed without conflicts onto current
`origin/main` (`6a68b800`) as branch `feat/web-fetch-fallback-pilot-main`, leaving the original
branch and its two unrelated unmerged `fix:` commits untouched. In that clean worktree the pilot
suite passes (**1054 passed, 9 skipped**) and `scripts/local_ci.sh` reports **all blocking gates
passed**: ruff check and format, basedpyright, high-severity Bandit, pip-audit, pytest (**5340
passed, 139 skipped**), npm ci, the braces guard, type-check, lint, format, npm audit, Vitest
(**666 passed**), build, feature matrix and pre-commit. Only the non-blocking Bandit inventory
reports existing low findings. The three earlier failures (#376, #379, #418) were already fixed
on `main`; the #376 comment comparing against the stale `main` was corrected.

### Proposed: stage F bounded live comparison — plan awaiting owner approval (2026-10-03)

Nothing below has run. Offline stages A–E are qualified; live readiness needs the
prerequisites in (1) implemented, reviewed and gated offline first, then a single
explicitly approved live session (2). Production stays direct-only throughout, and success
does not authorize production activation.

**Target (recovered, not inferred).** The incident record in #373 (from the user's
screenshot) gives the exact URL `https://openai.com/index/introducing-dots/`. The direct
client received `403`, `server=cloudflare`, `cf-mitigated=challenge`; whether the article
exists behind the challenge is unknown. Initial manifest: `openai.com` only; any
subresource or redirect host is reported as blocked, never auto-added.

**(1) Prerequisites (offline, each reviewed before use)**

1. *Challenge and status classification (must-fix).* Today the browser entrypoint reports
   any page with text as `success`; Playwright does not throw on a `403`, so a Cloudflare
   challenge page would be reported as article content, which the design forbids. Record
   the main-document HTTP status and classify non-2xx as `blocked` (403/429/503) or
   `error`, plus a fixed, reviewed challenge-marker check (status header/title markers,
   names only), before any content is returned. Pure tests with synthetic pages.
2. *Egress topology.* An owned, run-scoped, IPv4-only Docker bridge network with egress
   (not `--internal`), no other containers, no published ports, masquerade as required for
   egress, created/inspected/removed by the existing lifecycle discipline; the gateway is
   its only member and the browser stays network-none. The gateway entrypoint accepts
   `topology: egress` only with a non-empty inventory, exactly one default route via that
   network, and the same seccomp/no-new-privs/capability checks.
3. *Deployment-owned inventory.* The gateway must refuse this host's own addresses and every
   deployment-owned public address. Host interface and Docker network ranges can be
   gathered; the external NAT/public addresses of this machine and of the production
   deployment cannot be discovered reliably and **must be supplied by the owner**. The
   inventory is recorded by hash in the evidence, gathered fresh at run start.
4. *Browser-confinement probes.* From inside the browser container (network-none), owned
   probe attempts for direct TCP, UDP, DNS and QUIC to public, private, metadata and gateway
   addresses must all fail; recorded as counts.
5. *Evidence without content.* Per run record only: mode, status class, HTTP status class,
   OPEN attempts/refusals, blocked hosts (names), bytes read/written, frame counts, exit
   codes, image/profile/config hashes, cleanup evidence. No page text, cookies, query
   strings or response bodies are persisted; any successful text is measured (length/hash)
   in memory and discarded unless a separate retention decision is made.

**(2) The live session (one approval covers exactly this)**

- Two runs, one browser each: ordinary, then basic-stealth. Patchright is excluded until
  independently qualified.
- 45-second run deadline; 40 OPEN attempts; four concurrent tunnels; 32 MiB TCP payload;
  existing container limits; no retries of a failed run within the session.
- Stop conditions: any containment-check failure, unknown cleanup, unexpected host, or a
  non-zero exit outside the documented codes stops the session immediately.
- Truthful outcome reporting: `success` only for coherent article text behind a 2xx main
  document; challenge/denial reported as `blocked`; no CAPTCHA services, paid proxies,
  credentials, login/paywall bypass or manifest widening.
- Results recorded in this design doc and #373.

**Decisions needed from the owner:** approve building prerequisites (1)–(5); supply the
deployment-owned public address inventory; confirm the single URL and `openai.com`-only
manifest; then, separately, approve the two-run live session.

### Live prerequisite 1: challenge and HTTP-status classification (2026-10-03)

Owner-approved first prerequisite (the must-fix). The browser entrypoint now keeps the main
document's response and decides the outcome **before** any extraction with the pure
`navigation_outcome(status, cf_mitigated, title)`: `blocked` for `cf-mitigated: challenge`, a
denial status (401, 403, 407, 429, 503) or a reviewed fixed challenge title (exact match after
whitespace normalization, e.g. "Just a moment..."), even behind a 2xx; `error` for any other
non-2xx or a missing response; `ok` only for a 2xx main document with no marker. Only `ok`
reaches `page.evaluate(EXTRACT_JS)`; `blocked` and `error` carry no content and report the
trusted original URL. Diagnostics add only the status class (`4xx`) and outcome. The #373 shape
(`403`, `cf-mitigated: challenge`, "Just a moment...") is a test case. 20 new pure tests; pilot
run **1074 passed, 9 skipped**. The E2c native gate has not been re-run against this change.

### Live prerequisites 2, 3 and 5 implemented offline (2026-10-03)

No egress network has been created and the gateway still refuses `topology: egress` at
runtime; these are pure, fake-tested pieces for a separately approved live session.

- **Egress topology (2).** Network policy is now a small table of kinds sharing one vector and
  preflight: `daemon-net-offline-*` (internal, unchanged byte-for-byte) and
  `daemon-net-egress-*` (bridge with a route out, IPv4-only, fixed private subnet
  `10.251.248.0/29` chosen clear of the local Docker networks and host routes, inter-container
  traffic disabled, masquerade on, exact option and IPAM checks). `require_offline_network`
  still accepts the internal kind only; `require_owned_network` checks whichever kind the name
  selects, refusing an egress name with internal configuration and vice versa. Networked roles
  are held to their own network's subnet. `OwnedNetwork` uses the kind-aware preflight; the
  gateway's pure route check requires zero default routes for internal and exactly one for
  egress.
- **Deployment-owned inventory (3).** `scripts/web_fetch_pilot_live.py` parses host-local IPv4
  addresses from `/proc/net/fib_trie` (loopback excluded) and merges them with owner-supplied
  deployment-owned addresses, which are required (empty refused, at least one IPv4 entry),
  validated with the gateway's own parser, bounded, and recorded by content hash and gather time.
- **Content-free evidence (5).** `run_evidence` reduces a run to mode, URL host plus full-URL
  hash (never path or query), RESULT status, content byte length plus SHA-256, exit codes,
  integer counters, configuration hashes and the inventory hash and size.

Prerequisite 4 (browser network-layer confinement probes) is an opt-in native gate prepared
under a pending name and awaiting approval. Pilot run **1084 passed, 9 skipped**.
