# Crawl4AI service adapter — disabled, uncalled

This is a maintained client for the **0.9.5 service contract**, not an enabled
fallback or a qualified browser boundary. `FetchService.crawl4ai_strategy` remains
`None`; its production strategy chain remains direct-only. No service image,
deployment configuration, environment variable, permission or dependency is changed.
This internal prerequisite does not promote a feature-matrix state.

## Contract and local protections

`orchestrator/services/fetch/strategies/crawl4ai.py` captures the existing
operator-owned `crawl4ai_url` origin at construction and posts only to `/crawl`.
It rejects origin userinfo, query, fragment and non-root paths. The HTTP client
does not inherit ambient proxies or follow service redirects. Callers provide
only one URL, never an endpoint or arbitrary Crawl4AI configuration.

The adapter applies the existing fetch blocked-domain and HTTP(S)/port/DNS
admission before submission. An unavailable resolver does not authorize remote
fetching. It checks the returned request identity and admits any reported final
redirect URL before returning content. `SsrfViolation` and bounded content errors
retain their existing behavior; ordinary failures retain `FetchResult | None`
compatibility. Fixed internal diagnostics distinguish service HTTP/API errors,
malformed responses, target HTTP/per-result failure and explicit challenge
evidence. Neither bodies, requested URLs nor raw exceptions are logged. Only an
explicit `cf-mitigated: challenge` target header is classified as challenge
evidence; generic 403s, page text and upstream heuristic anti-bot messages are not.
Nothing retries or bypasses a challenge.

Requests use `urls`, `browser_config`, and `crawler_config`, not the obsolete
`extraction_config`. They contain finite internally authored settings: Chromium,
headless mode, the existing Daemon User-Agent, cache mode `disabled`, no retries,
a bounded page timeout and no downloads, stealth, captures or certificate
inspection. There are no caller scripts/hooks, proxy/CDP/session controls, LLM
extraction, deep crawling or fallback controls. These are request constraints,
not proof of immutable consumed server configuration or isolation.

The service response has plural `results`. The adapter requires a boolean
envelope success and **one** result with its own boolean success and successful
integer target HTTP status. In 0.9.5, `status_code` is the first navigation hop;
`redirected_status_code` is the reported navigation response. A redirect requires
a reported successful final status and an admitted reported final URL. A failed
reported final status cannot be hidden by a successful first hop. Envelope
success alone is insufficient. A different reported final URL also requires a
successful reported final status, even when the first hop was 2xx. Structured
markdown is schema-checked when supplied, then deliberately ignored; absent/null
markdown does not prevent usable HTML. Strings masquerading as structured
markdown and malformed shapes are rejected. Bounded HTML is returned with its
bounded title and then processed through Daemon's existing article/metadata
extractors. Upstream-generated markdown is never presented as `web-reader-v1`.
Shared extractor failure logs are redacted, including title/extraction errors.

A single client deadline includes slot waiting, DNS admission, transport,
streaming and response validation. The adapter uses the existing bounded decoded
response reader (including output-limited gzip/deflate decoding) and additionally
bounds UTF-8 HTML. The enclosing fetch service retains its extracted-content
bound. The decoded cap applies to the **whole JSON envelope**, including unused
markdown and metadata: HTML under its own cap can still be rejected when the
combined envelope is too large. This intentional fail-closed shared budget does
not promise the full HTML allowance independently of envelope overhead.
Unsupported encoding or truncated compressed service bodies retain the existing
bounded reader's `extraction_failed` error contract; this is not evidence of a
target-page HTTP failure. Synchronous parsing/title work is size-bounded and
expiry-checked, not preemptible CPU isolation; article extraction remains the existing
service contract. Streams and clients close on failure and client cancellation.

## Source provenance (not runtime evidence)

Pinned public source:
[`unclecode/crawl4ai` commit `838869bdda93f25e6d50b3f50ae9577d9b19a586`](https://github.com/unclecode/crawl4ai/tree/838869bdda93f25e6d50b3f50ae9577d9b19a586),
annotated v0.9.5 tag object `62002d6d572f5f59964160c0636a4c6f1f9ef449`.
Retained source inventory SHA256:
`2971d6fd0de585cc04e8b9c61407dd71b291068c8149bc9c843a1a96aa6e91a9`.
The inventory and all 69 listed source hashes were checked without executing
downloaded code or tests. Contract anchors at that commit:

- `deploy/docker/schemas.py:7–10`: request fields.
- `crawl4ai/async_configs.py:283–330,1280–1291,2354–2363`: untrusted field gates
  and supported bare config dictionaries.
- `deploy/docker/api.py:769–773,846–900`: admission/config loading, service egress
  enforcement, result serialization and plural envelope.
- `crawl4ai/models.py:120–159,254–282`: optional structured markdown, per-result
  success/status/HTML and reported redirects.
- `crawl4ai/async_crawler_strategy.py:763–801,1177–1196` and
  `crawl4ai/async_webcrawler.py:480–497,970–985`: original URL echo,
  first-hop status/string-valued navigation headers and reported final status/URL.
- `crawl4ai/cache_context.py:59–87`: disabled cache mode reads/writes neither
  content cache. This **does not establish zero retention**.
- `deploy/docker/egress_broker.py:182–215`: released server TLS/egress machinery.
  Request-admitted browser fields do not include TLS-ignore or sandbox controls.
- `crawl4ai/async_webcrawler.py:635–648`: upstream heuristic blocking reports are
  not independent explicit challenge evidence.

Registry metadata identified index
`sha256:0b05666882bfb67f45d559a5b827839c54127b74aedd723f8ab338a3571b5fc9`
and amd64 manifest
`sha256:636a6f9e5127f54771f14f709bf1b95693aacc30512426b26d09768b0269f1f4`.
These are not Git commits. Attestations are not verified signatures,
reproducibility or runtime proof. No image was pulled, built or run for this work.
Prior pilot (#431) and categorized-error work (#533) remain separate; no pilot
browser/DNS/gateway/relay stack or private patch is imported.

## Activation remains blocked on separate qualification and authority

Local admission is **not remote DNS pinning**. Checking a reported final URL
cannot prevent or reconstruct prior redirect/subresource access. Intermediate
redirect-domain policy, remote proxy coverage and equivalence with Daemon's
direct SSRF enforcement require deployment-specific evidence. Reuse released
service egress/TLS machinery; do not infer it is configured or effective merely
because the client sends a particular field. Server operator overrides and
mutable defaults/configuration remain qualification concerns.

Chromium sandboxing is opt-in/off by default upstream and is not
runtime-qualified here. No sandbox or security settings are changed. Remote
per-request isolation and immutable consumed configuration are unproven.

**Client timeout/cancellation does not prove server cancellation or descendant
termination.** The single local semaphore limits client calls only; timed-out
server jobs could overlap later calls. Remote workload/resource bounds need
qualification before activation, not an unsupported cancellation claim.

Disabling content-cache reads/writes does not remove browser state, service
monitoring/logs or other retained metadata. For example, upstream
`deploy/docker/api.py:756–759` passes the target URL to monitoring. Retention,
deletion and processing policy require independent qualification.

Future image execution, controlled runtime/egress/cancellation/sandbox tests,
live comparisons, deployment settings, credential provisioning and any enabled
selection/fallback need separate explicit authority and security review. This
change authorizes none of them. Synthetic mock tests prove only the client
contract; existing repository gates do not qualify the remote browser service.
