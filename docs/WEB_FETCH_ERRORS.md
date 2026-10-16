# Direct-fetch error contract (#373)

Source-only fix for the masked-challenge defect; not deployed. Direct fetching
does not establish successful access to the article behind a challenge, nor
qualify a browser, reader integration or vendor fallback. PR #431 remains a
separate held pilot. This preserves the authority and truthful-outcome intent
of V05 / AC06; it enables no additional access.

`FetchContentError` already propagates through `FetchService` to `WebFetchTool`.
The direct strategy now uses these fixed, bounded categories, exposed as the
existing `{"error": "<code>"}` tool-result shape:

| Code | Evidence |
| --- | --- |
| `upstream_challenge` | Explicit `cf-mitigated: challenge` response header (case/whitespace normalized), including on a 2xx or redirect response |
| `access_denied` | HTTP 401, 403, 407 or 429 without the explicit challenge marker; authentication/access/rate denial, not proof of a challenge |
| `http_error` | Other non-2xx final response, missing redirect location, or exhausted redirect limit; 503 alone is not challenge evidence |
| `transport_error` | Target DNS unavailability/no validated addresses, exhausted bounded address attempts, or connection/read timeout/failure |

The header check is adapted from pilot commit
`1f06d1bb70f07c9b42c24e891ed4739def268148` after inspecting current direct-fetch
behavior. Its broad title heuristics and combined denial/challenge category are
not imported: a 403, `server: cloudflare`, or generic page title/body text alone
does not prove a challenge. Detection is intentionally limited to this explicit
marker; other challenge mechanisms are not claimed to be recognized.

Classification occurs after the existing bounded transport returns, before
extraction, cache writes or snapshot creation. Existing response-size, encoding
and read-deadline bounds take priority if the body cannot be safely read.
Challenge/denial errors terminate the strategy chain without retries or fallback.
Transport errors also propagate through the existing bounded-error path; this
changes the old `None` result, not the production direct-only strategy selection.
No bodies, header values, URLs or exception details are included in these errors.

Successful `FetchResult` fields, provenance, extraction, cache behavior and reader
JSON remain unchanged. Content-policy rejection, unsupported/unsafe URLs and
unexpected failures retain their existing paths (`fetch_failed` for a tool fetch
that returns `None`); `page_too_large` and `extraction_failed` remain unchanged.
Cancellation still propagates. URL/redirect/DNS validation, pinned transport,
SSRF policy, cookies, identity, address/redirect limits and byte/time bounds are
not relaxed or reconfigured. No snapshots or network probes are made to qualify
this fix; regressions use synthetic HTTP responses and mocked stores.
