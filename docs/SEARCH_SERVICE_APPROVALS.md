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
reservation and again before dispatch. Missing credentials, denied approval,
invalid arguments and refused budgets cause no external search.

Each provider request is bounded to 10 results and a 30-second deadline, with
bounded query, response and snippet sizes. Provider endpoints are fixed, HTTP
redirects and automatic retries are disabled, and credentials/errors do not
include query content in application error messages. Results retain source URLs
and pass through the existing untrusted tool-result fence. The prompt asks for
minimal useful query terms and source citations; that instruction is not a
claim of automatic query redaction or restricted-project enforcement.

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
