# Search services: deployment decision and operation

Decision date: **29 September 2026**. This records the operator's search-only
exception and approved direct-search-first implementation. It is not evidence
of a live rollout or a paid provider test. Related incident: [#352](https://github.com/sol-aeternum/Daemon/issues/352).

## Approved scope

The operator chose to keep standard Brave Search for now, earmarked for an
Enterprise/ZDR upgrade or removal later, and to build Tavily tooling alongside
it. The operator explicitly declined a hard cutoff date for Brave. One
operator-selected provider serves `web_search`; there is no automatic fallback.
Direct search and page reading support ordinary research without a subagent.

This is an explicit exception to the default search-service privacy requirement,
not an assertion that Brave is ZDR and not implementation of DEC12 inference
R routes. All inference approvals, pins and expiry checks remain unchanged.
It supports DEC11's single-assistant experience and AC12's bounded compute;
AC07 still requires explicit provider authority at dispatch.

## Brave: temporary standard-retention exception

- Service: `brave-web-search`, selected by `WEB_SEARCH_PROVIDER=brave` (default).
- Endpoint: `https://api.search.brave.com/res/v1/web/search`.
- The existing account has **no Enterprise/ZDR**. The operator accepted its
  published retention: API query logs retained for up to 90 days for
  billing/troubleshooting/abuse, subject to legal obligations. Do not describe
  search as zero-retention or infer exact serving geography from Brave's HQ.
- Dated evidence: [Brave privacy notice](https://api-dashboard.search.brave.com/documentation/resources/privacy-notice)
  (updated 25 August 2026; checked 29 September) and
  [Brave pricing](https://brave.com/search/api/) (checked 29 September).
- Public Search price: USD 5 per 1,000 calls. The deployment service ceiling is
  5,000 microusd per call; promotional/free credits do not expand account budgets.
- At initial approval, the deployment credential was present and API
  documentation/pricing were reviewed; live credential validity and account
  billing had not yet been re-tested. Record subsequent rollout evidence on #352.
- Approval lives only in `config/inference_policy.production.json`. The portable
  `config/inference_policy.json` remains deny-by-default.

`review_mode: "manual"` is an explicit tool-service-only approval mode. It
requires reviewer, evidence and a non-future review date, with no expiry field
value. Omission means the existing expiring review contract. Unknown modes and
contradictory manual reviews with an expiry fail closed. Inference routes cannot
use this mode to bypass their existing review or approval expiries.

**Follow-up, without an automatic cutoff:** obtain Brave Enterprise/ZDR terms
and pricing, or remove Brave after a replacement is qualified. Revisit on
provider policy/price changes or operator request. This remains a manual
operational obligation, not an automated reminder. Tracked in
[#353](https://github.com/sol-aeternum/Daemon/issues/353).

## Tavily: implemented adapter, unapproved service

- Service: `tavily-web-search`; endpoint: `https://api.tavily.com/search`.
- `WEB_SEARCH_PROVIDER=tavily` and `TAVILY_API_KEY` select credentials but cannot
  approve dispatch. Both shipped policies leave this service unapproved.
- Basic search only; generated answers, raw content and automatic parameter
  selection are disabled. No research, crawl or extract API calls are enabled.
- Public PAYG pricing is one credit for basic search at USD 0.008 per credit:
  [API credits](https://docs.tavily.com/documentation/api-credits).
- Before activation, verify the account's **Allow Use of Query Data = OFF**,
  reconcile that promise with Customer Input permissions in current terms,
  confirm upstream/subprocessor retention and processing geography, verify
  account availability and price, and record a separate operator review.
  Sources: [privacy setting](https://help.tavily.com/articles/4205958832-understanding-the-allow-use-of-query-data-setting),
  [terms](https://www.tavily.com/terms). The Brave exception does not extend to Tavily.

## Execution and accounting

Search uses the parent account compute scope and concurrency slot. Each call
checks the selected service's identity, approval, review and price ceiling before
reservation, after reservation and immediately before dispatch. Missing
credentials, denied approval, invalid arguments and refused budgets cause no
external search.

Each provider request is bounded to 10 results and a 30-second deadline, with
bounded query, response and snippet sizes. Provider endpoints are fixed, HTTP
redirects and automatic retries are disabled, and credentials/errors do not
include query content in application error messages. Results retain source URLs
and pass through the existing untrusted tool-result fence. The prompt asks for
minimal useful query terms and source citations; that instruction is not a
claim of automatic query redaction or restricted-project enforcement. The
30-second **work deadline** covers capability resolution, waiting for account
acquisition, final provider admission and the provider round trip, including
response observation. Pacing leaves five seconds for the network leg under the
normal 30-second budget. Necessary shielded reservation recovery and accounting
settlement can finish after the work deadline; there is no strict 30-second
whole-operation return guarantee. An interrupted acquisition waits for the
reservation result, registers any returned hold and settles zero without
dispatching. This protects against cancellation losing a committed hold.

Reserve the configured ceiling before dispatch. Successful fixed-price calls
settle their pinned price; after-dispatch failures or cancellation settle the
conservative reservation under the existing unknown-usage contract. This may
charge a failed search whose provider billing is unknown. Settlement failures
stop the operation rather than become ordinary model-visible retry suggestions.
Unexpected reported Tavily units are recorded at their full priced amount and
stop the turn; a charge above the reservation triggers the existing account
suspension contract. Usage is never capped to hide an overrun. A refusal detected
after reservation but before dispatch settles at zero, including retaining that
known zero outcome if the in-process settlement fails.
No provider signup, purchase or account funding change is included. The operator
separately authorized one synthetic Brave-backed search-and-answer deployment
check, capped at USD 0.10 total, after gates and independent review pass. This is
not a relevance benchmark or authorization to test Tavily live.

## Shared provider pacing (issue #360)

Added 30 September 2026, after issue #360's review. Search dispatches for one
provider credential are now paced through a shared admission gate
(`orchestrator/services/search_pacing.py`) so separately deployed backend and
worker processes coordinate capacity grants against one shared allowance.
The approval, pricing and accounting rules above apply, with no automatic
retry and no provider fallback. Network-arrival jitter is described below.

**Why.** Brave documents a 1-second sliding window per plan and bills only
successful calls ([rate limiting](https://api-dashboard.search.brave.com/documentation/guides/rate-limiting),
checked 30 September); Tavily documents 100 RPM on development keys and
1,000 RPM on production keys (production requires a paid plan or PAYGO) with no
simultaneous-burst guarantee
([rate limits](https://docs.tavily.com/documentation/rate-limits), checked 30
September). Brave's pricing page advertises a 50 queries-per-second capacity
for Search and custom capacity for Enterprise (checked 30 September). None of
these published numbers proves what the **active** credential's allowance is —
they are plan descriptions, not key state — so the gate starts conservative and
tightens only on evidence from real responses.

**How admission works.** The gate keys are per provider plus a SHA-256 digest
of the credential (opaque, never reversible; no credential, query or URL is
ever in a key, log or diagnostic). Until valid `X-RateLimit-Policy`
metadata has been learned from an actual response for that
credential, admission enforces a **fallback spacing of 1.1 seconds between
dispatch grants**. This is a deliberate temporary bootstrap under unknown key
capacity — explicitly **not** the production throughput model and not evidence
that the active key is limited to 1 QPS. Learned metadata lasts at least six
hours, or twice the longest window when longer, so known long quotas are not
forgotten while counted usage is relevant. Each window is enforced with **no
artificial spacing**: unused allowance can be granted as a concurrent burst.
Long/high-volume windows are counted conservatively as described below. The
operator-reviewed production target remains a
burst-capable shared limiter with a bounded queue; operator-configured capacity
(a provider-capacity setting rather than learned headers) is a future design
decision that has **not** been approved or built, and no new environment
variable is introduced by this change.

**Bounded state.** At most eight windows are accepted, with bounded integer
allowances and durations. Unsupported stored or provider policy metadata fails
closed. Windows with at most 20,000 entries and at most ~34.7 days use one
pruned, TTL'd sliding-window ZSET, with a hard 20,000-entry ceiling across all
windows. Longer or larger windows use at most eight counter fields containing
the current and previous whole buckets. Counting both buckets includes all
locally admitted usage in a rolling window and prevents double allowance at a
boundary; it may conservatively delay calls for up to another bucket. Counters
retain no per-call history, including at a one-million-call allowance. When
only counters are needed, admissions add no ZSET history. These limits cover
Daemon's shared admissions; use of the credential outside this gate is not
represented by local counters.

**Bounded refusals, never long sleeps.** A refusal or strictly validated reset
evidence can only *extend* the shared cooldown. Valid reset durations are stored
until reset, up to the parser's existing bounded maximum, including monthly
exhaustion. A wait exceeding the caller's remaining operation budget refuses
immediately; storage lifetime does not authorize a long sleep or periodic
probe. Without valid reset evidence, 429 retains the short status default and
billing/quota-family responses retain a 60-second default. No HTTP request is
replayed automatically. Redis factory creation, lock acquisition, Lua calls
and local sleeps all consume one admission budget. Response observation also
has a bounded budget within the remaining provider deadline. Malformed Lua
replies, including `None` and `[0, 0]`, never authorize dispatch.

**Charge and dispatch boundary (operator-approved adjustment).** Search opts
into dispatch-aware metering and may hold a temporary account reservation
during its bounded local wait. There is only one consuming admission, after
reservation and HTTP request preparation. Immediately after that atomic grant,
the caller synchronously rechecks authority, marks dispatch and calls
`client.send`, with no intervening await. A stalled reservation therefore cannot
bunch previously granted dispatches or consume a second capacity slot.

Before the mark, a pacing refusal/outage, local timeout, cancellation or setup
failure is positively known not to have begun provider dispatch and settles the
temporary hold at zero. Once marked, unknown outcomes settle the conservative
ceiling; confirmed delivered units require a marked dispatch and still use
positive unit validation. The transition cannot be reset through public charge
flags. A settlement failure stops the operation, retaining the known outcome
for existing scope cleanup. Legacy metered callers remain conservative by
default and need no dispatch mark. Missing credentials, denied initial approval
or capability, invalid input and refused account budgets still cause no search.

**Ambiguous reservation commit recovery (#365).** The internal entitlement
service captures the database-generated reservation identity and admission
context before transaction exit. An exit acknowledgement failure carries that
receipt, without assuming the transaction committed. Metered acquisition stops
provider work and resolves the receipt through a fresh connection acquisition:
account creation/uniqueness is checked, the same account row is locked, then the
exact reservation is read and its owner, amount, period and route/context are
verified. Recovery uses read-committed isolation so lookup after the lock barrier
cannot retain a snapshot from before the original commit. A committed hold
settles zero; verified absence after the barrier means rollback. No reservation
or provider call is replayed.

Database unavailability or context mismatch is a typed unresolved accounting
failure. The scope retains the candidate receipt and known-zero intent; cleanup
must revalidate it or fail explicitly, never silently drop it or settle a
different context. Recovery is shielded against repeated cancellation and may
outlast the work deadline. The receipt and known-zero intent are in-process,
not a new durable database record: a killed process still follows existing
conservative expired-reservation reconciliation. No schema migration is part
of this adjustment.

HTTP endpoints are fixed HTTPS hostnames, not pre-resolved DNS/IP pins. The gate
paces handoff to the HTTP client; DNS, connection and network latency can still
add jitter to actual wire arrival. It does not claim exact provider-arrival
spacing. Redis failure refuses before that handoff; no HTTP replay or provider
fallback occurs.

**Statuses.** 402 is reported as a billing requirement ("requires payment"),
per standard HTTP semantics. 429 is the provider's documented rate-limit
response. 432/433 appear in Brave's rate-or-quota family, but Brave's public
documentation does not define their semantics, so they keep the honest generic
rate-or-billing message instead of invented distinctions. Provider error
bodies are never read; only bounded, strictly parsed headers are used, and
malformed numeric/reset values are discarded. An explicitly present but
unsupported policy fails closed rather than becoming an unbounded limiter.

Page reading remains the existing `web_fetch` path; this change does not add
vendor-assisted fetching or certify unrelated fetch hardening. Search results
are evidence, not authorization to fetch private resources. Retired spawn tools
are omitted from the main assistant registry, including automatic retry dispatch.

## Selection, revocation and rollout

1. Run all local gates and fresh independent review before deployment.
2. Deploy the reviewed backend/worker code and select the production policy via
   the existing `DAEMON_INFERENCE_POLICY` setting. Both services receive the new
   search environment settings; no database migration is required.
3. Default selection remains Brave. Switching to Tavily requires both a reviewed
   service approval and explicit provider selection; a missing key, unavailable
   service or denied budget does not fall back to Brave.
4. Revoke Brave by setting its service `approved` to false (or removing the
   service entry) and restarting backend/worker to reload cached policy. Search
   schemas then disappear; direct calls also enforce the service gate.
5. Verify deployment registration/configuration, then use the separately
   authorized bounded synthetic check. Record actual settlement and outstanding
   holds; distinguish an orchestration-engine smoke from a browser-authenticated
   full UI test. Further paid tests need fresh authorization.

The inference approvals still expire on their separately documented date.
Brave's lack of a hard cutoff cannot keep an expired inference route active.

## Local deployment verification — 30 September 2026

After explicit operator approval, the verified bundle was loaded by recreating
backend/worker and rebuilding/recreating frontend. No additional migration or
paid diagnostic request was run. Final backend gates passed with 4,050 tests and
8 skips; frontend gates passed with 337 tests. Fresh read-only reviews, feature
matrix/freshness checks, all-files and new-file pre-commit checks, and a direct
redacted scan of the 70 changed/new source files passed. Full Bandit remains a
non-blocking inventory, as defined by the project gates.

Live health reports PostgreSQL and Redis healthy, and all three services started
without logged errors. A synthetic, isolated Redis namespace verified spacing,
three concurrent grants under learned capacity, and a fourth refusal using the
backend container's runtime. A browser smoke against the built app verified the
compact activity/citation UI and navigation/reload using mocked account/history
responses with service-worker routing disabled for interception. This is not a
paid search, real login, or end-to-end title-generation test. Metadata-only reads
also confirmed four live saved conversations now return positive derived counts
despite stale stored counters. Code remains uncommitted and unpushed.
