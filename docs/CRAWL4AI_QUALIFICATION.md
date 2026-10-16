# Crawl4AI isolation controls — source-only, not runtime-qualified

This is a small internal prerequisite for the [disabled adapter](CRAWL4AI_ADAPTER.md)
merged in #535. Production remains **direct-only**, with `crawl4ai_strategy=None`.
No native Docker backend, CLI launcher, Compose/environment/dependency change,
activation switch or reader integration is provided. No feature-matrix promotion.
It is not a replacement for upstream's browser/egress/TLS machinery.

## Dependency cut and reused controls

Extraction source: #431 head `14d0323e2ae50f36e217367ae63f89f454863217`.
The original branch, repair worktree and research remain retained for parent review.
This change does not close #431. Control reuse, not whole-module copying:

| Original module / control | Extracted module | Guarantee / dependency removed |
| --- | --- | --- |
| `web_fetch_pilot_config_snapshot.ConfigSnapshot`, `require_config_archive` | `scripts/crawl4ai_qualification/config.py` | Exclusive owned input, no-follow/inode/bytes verification and non-extracting readback. Removes old derivative config hash and mixed policy dependency. |
| `web_fetch_pilot_container_policy.require_c4ai_identity`, `require_c4ai`, shared strict checks | `policy.py` | Independent name/run/full-ID/image expectations and finite resource/privilege/mount acceptance checks. Removes 0.9.4 image, custom seccomp, sandbox override and all browser/gateway/network-role code. |
| `web_fetch_pilot_docker.OfflineContainerDriver` staging/preflight/unknown-create/removal/cancellation semantics | `lifecycle.py` | One allocation, staged-input binding, provenance checks, owned teardown and uncertainty retention. Replaces the mixed driver dependency with an injected protocol; no native executor implementation. |
| `web_fetch_pilot_c4ai_evidence` structured denial and authenticated inline control | `evidence.py` | Pure bounded response checks with fixed numeric seed probes and collector run/endpoint/request identity. Removes the pilot HTTP client, polling and live entrypoint. |

Original direct/transitive import graph (exact filenames have prefix
`scripts/web_fetch_pilot_`, or `tests/test_web_fetch_pilot_` for tests):

```text
c4ai_evidence -> stdlib
config_snapshot -> container_policy (old hash/error)
docker -> container_policy, config_snapshot, command, process
command -> process -> io -> core
c4ai_phase1_io tests -> c4ai_evidence, command, docker, process
                    -> browser_container_io, gateway_tls_io, two_container_io tests
config_snapshot tests -> config_snapshot, container_policy, docker
                      -> container_policy and docker tests
browser_container_io -> bridge, browser_payload, command, docker, gateway,
                        io, process, results, gateway_tls_io tests
gateway_tls_io -> acceptor, gateway, io, relay
two_container_io -> bridge, browser_payload, command, docker, io, process,
                    results, browser_container_io and gateway_tls_io tests
bridge -> core, gateway, results
browser_payload -> browser_entrypoint, gateway_entrypoint
browser_entrypoint -> acceptor, io, relay
gateway_entrypoint -> core, dns, gateway, io
gateway -> core, io, tunnels
relay -> core, gateway, io
acceptor -> io; dns -> core; results -> core; tunnels -> core
```

New graph (all external dependencies are Python stdlib):

```text
config -> stdlib
policy -> config
evidence -> config
lifecycle -> config, policy
test_crawl4ai_qualification -> these four modules, pytest, stdlib
                           -> existing FetchService only for disabled-chain regression
```

No excluded pilot helper is imported or copied transitively. An AST regression
checks the source cut has no pilot, subprocess, socket or HTTP-client imports.
Independent fake transport/inspect/HTTP/archive fixtures replace the old test
helper graph. They do **not** execute the original pilot or downloaded upstream.

## Threat model and demonstrated source checks

The host UID, independently selected transport and local daemon are trusted.
This does not resist root, an adversarial same-UID process or Docker administrator.
Transport identity/response provenance is an external obligation: a service JSON
body cannot authenticate its own collector, run, endpoint or API credential.

- `stock_config` pins the **unmodified upstream** config hash. Generic
  `ConfigSnapshot` only stages bounded bytes; it is not an approval mechanism.
  The lifecycle accepts stock-pinned bytes, creates its own input, and checks
  retained descriptor/device/inode, ownership, modes, single link, size and bytes.
  Source-path replacement cannot change those owned bytes. Staged replacement,
  links, ancestor substitution or mutation refuse; foreign inputs are retained.
- Readback must be exactly one bounded regular `config.yml`, without extraction,
  links, extra entries or missing end markers. Before and after readback the
  modeled allocation must be unstarted with the expected identity and mount.
  Staged bytes are checked again immediately before modeled start.
- The closed **offline acceptance specification** retains the pilot's finite
  2 GiB memory/swap, 2 CPU, 256 PID, 256 MiB shared-memory and bounded tmpfs
  ceilings, read-only root, non-root user, dropped capabilities and no-new-privileges.
  No host network, published ports, extra mounts/devices, credentials or security
  environment overrides are admitted. Unknown `Config`/`HostConfig` fields fail
  closed. This narrow fixture shape is not claimed compatible with native inspect.
  No create arguments are emitted, settings applied or custom seccomp installed.
- Full image-config ID and daemon ID are independent supervisor inputs, **not**
  inferred from the inspected record. The registry manifest is not that image ID.
  Native derivation/qualification of those inputs is not implemented here.
- Cleanup reconciles a lost create acknowledgement only by exact owned name and
  independent identity. It reinspects before removal by full ID and requires
  authoritative absence before input deletion. Wrong identity, multiple matches,
  failed commands or uncertain observations retain inputs and latch failure.
- Operations/teardown are driver-owned and shielded from caller cancellation.
  Per-operation waits have a finite ceiling; a non-returning transport stays
  pending/retained and cannot be raced by cleanup or another allocation. Repeated
  caller cancellation does not discard teardown. This does not forcibly stop an
  uncooperative coroutine, native process or remote job. No fabricated exit code.
- Application evidence requires the exact integer-400 structured single-seed
  denial, all fixed numeric private probes, a successful authenticated inline
  marker result, and an unauthenticated 401 control from the same collector run.
  Generic 401/403/500, transport/timeout, wrong identities, malformed/oversize or
  duplicate-key JSON, envelope-only success and mismatched content fail.
  Numeric seeds avoid treating DNS failures as proof of policy enforcement.

Tests explicitly substitute a **synthetic** config digest for lifecycle fixtures;
they do not attest stock YAML startup. The stock pin itself rejects synthetic
bytes. No tokens, service bodies or requested URLs are emitted as evidence logs.

## Upstream provenance and unresolved qualification

Upstream v0.9.5 tag object `62002d6d572f5f59964160c0636a4c6f1f9ef449` points to
commit `838869bdda93f25e6d50b3f50ae9577d9b19a586`. Retained source inventory SHA256:
`2971d6fd0de585cc04e8b9c61407dd71b291068c8149bc9c843a1a96aa6e91a9`.
All 69 inventory source hashes were verified read-only. Unmodified
`deploy/docker/config.yml` SHA256 is
`bb332d560b613aec9084bf752e886b070dba33b080f74f2b8d2701258b1f4cfc`.
Stock config is a provenance fixture, **not an approved security profile**.

Source anchors at that commit:

- `utils.py:112–148`, `server.py:84` under `deploy/docker/`: server YAML load,
  default merge and environment overrides.
- `server.py:515–531,605–613`: `/config/dump` normalizes **caller configuration**;
  it does not read back consumed server YAML.
- `api.py:720–739,769`, `utils.py:352–364,382–397`, `server.py:557–573`: numeric
  single-seed refusal maps to the checked structured HTTP400 detail.

Evidence levels must remain distinct:

1. Reviewed upstream source bytes — pinned provenance.
2. Owned staged bytes and modeled allocation/readback checks — synthetic tests.
3. Native container-filesystem readback — **unverified compatibility**. Pilot
   `docker cp` is only a candidate; no native invocation is implemented or run.
4. Actual daemon-effective consumption/merge/environment and executed image code
   — **unverified**. Filesystem bytes alone do not prove this.
5. Runtime resource enforcement, sandbox, egress/TLS/subresources/redirects,
   cancellation/descendant termination and per-request state isolation/retention
   — **unverified**. An inline control is not a network/TLS positive control.

Released upstream defaults include a disabled Chromium sandbox and pooled browser
state. The narrow no-network/no-credential acceptance fixture is intentionally not
a runnable authenticated remote reader profile. No hardened YAML, network,
sandbox/host/seccomp setting or alternate browser implementation is selected here.

Before any future runtime work, obtain separate approval for one bounded plan:
exact immutable image digest/platform and verified image-config ID, chosen
settings and credentials, native readback **and effective-consumption** observation
method, synthetic fixtures, resource/time ceilings and identity-owned cleanup.
The historical amd64 manifest lead is
`sha256:636a6f9e5127f54771f14f709bf1b95693aacc30512426b26d09768b0269f1f4`;
it is not qualification evidence. Settings/consumption-method choices remain open,
so this change requests no blanket runtime approval. Parent review must precede
merge or replacement/closure of #431.
