# Midnight UI and bounded remediation

Status: **experimental user-visible capabilities; scoped PR candidate gates and source review pass**. This package combines owner-approved Midnight P1/P2-S, its diagnostic, dependency patches and bounded metadata/test repairs. It is not a routing change, production rollout, new API/schema, durable-task implementation or complete accessibility/device acceptance.

Product direction remains [DAEMON.md](DAEMON.md), DEC11–DEC12 and V03/V06/V07, D05, AC01–03. Runtime remains Z-only. Code, tests and [FEATURE_MATRIX.md](FEATURE_MATRIX.md) govern implementation status; this dated record does not supersede those contracts.

## Included behavior

- Chat scroll containment (#428): the chat Panel's library-generated wrapper explicitly uses `overflow: clip`, leaving messages as its vertical scroller and containing wheel chaining there. Header/composer and speech controls must remain anchored during long-history reload, expansion and keyboard focus. This addresses the confirmed extra panel scroll range, not an isolated explanation of which descendant produced it; synthetic overflow regressions supplement fictional long-chat tests. Source fix only; no rollout or owner acceptance claimed.

- Preserve blue/slate Dark/Light/System identity using shared semantic tokens; nonce-aware theme initialization, clearer composer/navigation and truthful retired voice/media copy.
- Copy assistant prose/code with success and manual fallback. Retain existing Stop, transcript, long-answer disclosure, history and Council compatibility.
- Keep draft text/attachments across settings, same-tab token refresh and a reload of the same tab. Owner decision (2 October 2026): text persists in that tab's sessionStorage and files in IndexedDB (≤25 MB each, ≤100 MB per tab, expiring 24 h after last save), so draft text ends when the tab closes; files a closed tab leaves behind are unreachable and deleted at expiry or the next sign-in/sign-out. A shared, nonsecret auth epoch in localStorage changes on every sign-in and sign-out, and records saved under another epoch are discarded, never restored. Scope drafts by conversation and sign-in; clear on login/logout (all tabs' saved copies), cross-tab token refresh, submit and explicit New Chat. No credentials, URLs or account identifiers stored; no auth API change. A duplicated tab shares the saved attachments of the tab it was copied from until either changes them.
- Conversation details reuse returned sources, completed file outputs and tool activity. Preview selects the clicked file rather than the latest file; compact dialog and desktop resize preserve focus, protected downloads, HTML sandbox and CSP.
- Retained Sources use existing owner-checked snapshot APIs for metadata/pagination, bounded JSON attachment export and confirmed single-item removal. Stale auth/conversation/operation results are discarded; unknown deletion outcomes require reconciliation and are not automatically replayed.
- Exact snapshot-route/origin/base matching prevents service-worker caching. Activation removes matching old entries only, preserving unrelated caches. This does not certify every owner browser's historical cache cleanup.
- The optional dedicated-browser diagnostic requires manual owner sign-in/readiness, loopback-only CDP and ownership/time guards. Read-only Sources mode checks at most three conversations without export/removal, original-link activation, pagination, messages or settings/memory changes. Ordinary HTTP rules, credentials, application auth and CSP remain unchanged.

## Bounded remediation

- npm-generated lock changes only the approved Next.js/ESLint family and DOMPurify patches: Next.js/`eslint-config-next` 16.3.8 and DOMPurify 3.4.16. No new package paths or unrelated refresh.
- uv-generated lock changes only LiteLLM 1.96.2, urllib3 2.8.0, virtualenv 21.7.13 and required python-discovery 1.6.0. No new direct dependency or unrelated update.
- Both memory writers serialize omitted/`None` metadata as `{}` under the existing NOT NULL/default contract, preserving supplied objects and transaction/connection ownership. No migration, API, backfill or production cleanup.
- Retrieval/teardown tests require an explicit test DSN, use migration-backed uniquely owned schemas and disposable crypto, and deterministically clean only their own schema. Missing DSN skips before connecting; malformed/unreachable explicit DSNs fail redacted. No application-DSN fallback.
- Diagnostic parity has exact reviewed filter/guard source-site exceptions, not global variable exemptions. New consumer/dynamic sites fail. Two accepting/drift roster tests freeze their fictional period; boundary refusals remain. Mocked HTTP tests have narrowly scoped public example-domain DNS fixtures; hostile DNS/SSRF tests remain unpatched.

## PR base and preservation

Candidate base: `main` at `48bb7c10833b500b075bc644cef27149a7882e0d`. The original development checkout was based on older `bdb3e292`; its whole files are not a valid replacement for newer main work. Main's Sol-upgrade configuration, completion/evaluation implementation and newer routing/experiment tests remain intact. Unrelated web-fetch pilot, nested worktrees, generated Workbox assets and private data are excluded.

The one-line current Sol test expectation and historical experiment workaround from the earlier dirty-checkout repair are not copied over main: main already has the matching Sol expectation and self-contained experiment fixture, plus newer regression coverage. Excluding those redundant older-file replacements avoids deleting tests or undoing the merged upgrade. Only still-applicable repairs travel.

## Evidence and limits

Historical approved-source checks include 29 browser regressions, ten P1 production fixtures and seven Sources production/UI-worker checks. All **265 frontend source/config/baseline hashes** from that checked state match this candidate, so that fictional-browser evidence remains applicable; those browser suites were not rerun during PR preparation.

Fresh rebased-candidate verification: **4,133 backend tests passed, 31 skipped, fourteen warnings; 484 frontend tests passed**. The unmodified `scripts/local_ci.sh` passes all blocking gates across its backend/aggregate and frontend family invocations: locked installs, lint, format, types, high-severity SAST, dependency audits, build, feature matrix and configured pre-commit/secret scanning. Backend execution remains network-isolated with only the disposable database socket; its normal HTTP audit cache contains fresh public responses from a separately passing live 125-package audit, with no expiry/config override. Both dependency audits report zero vulnerabilities. The count differs from the historical 4,568-test superset because unrelated pilot work is excluded and main's newer upgrade tests are retained; no test exclusions or skip guards were added for this candidate.

Fresh read-only `explore-luna` review inspected the scoped mixed-authored integration and found no actionable defect; the primary independently ran the gates and inspected actual diff/tree/hash evidence. Existing SAST inventory and [#381](https://github.com/sol-aeternum/Daemon/issues/381) React test synchronization warnings are reported, not suppressed. This source verification does not close the real-account/device acceptance limits below.

The initial draft's remote CodeQL check subsequently identified two test-fixture findings ([#383](https://github.com/sol-aeternum/Daemon/issues/383)): unsafe proxy URL string construction and a broad foreign-origin cache-test exception. The fixture-only repair constructs a fixed loopback URL and assigns path/query components, rejects malformed/non-HTTP(S) targets with 400, and compares cache origins exactly. Thirteen no-network regression cases, all 497 frontend unit tests, type checking, lint, format, production build, seven fictional Sources browser tests and aggregate checks pass; independent pre-change and actual-source review found no remaining blocker. Remote CodeQL on the repaired head remains required; earlier gate passes do not override these findings. Production routing, authentication and cache rules are unchanged.

The source-only offline backend runner exposes only a physically separate disposable PostgreSQL socket when explicitly enabled. No application database or ambient dotenv/credentials are accessible. The earlier verification Git repository initially lacked a baseline, making the scanner process 523 MB as additions; a separately owner-approved scratch-only baseline restored ordinary staged-diff semantics without changing rules or excluding files. No project commit or publication was authorized by that fixture approval.

Deployment/acceptance observations are separate from source checks: the owner-approved localhost UI was deployed before dependency remediation; bounded actual read-only Sources acceptance checked three conversations (two empty, one with two rows) with zero export/removal/pagination. Fictional fixtures and owner-reported P1 happy paths are not interchangeable with that authenticated observation.

Remaining limitations:

- Dependency patches are not deployed; the running image is unchanged.
- Real snapshot export/removal/pagination and owner-worker/private historical-cache cleanup remain unobserved.
- Broader device/accessibility/reconnect, protected-file negative cases and real memory rollback remain incomplete. [#372](https://github.com/sol-aeternum/Daemon/issues/372) tracks the protected-file proxy failure.
- Earlier application-DSN fixture cleanup remains unverified; disposable tests do not prove application-state cleanliness.
- Existing skipped/manual/Redis/Docker cases and SAST inventory debt are recorded, not waived.

Source-resolution evidence was posted without closing [#339](https://github.com/sol-aeternum/Daemon/issues/339), [#346](https://github.com/sol-aeternum/Daemon/issues/346), [#375](https://github.com/sol-aeternum/Daemon/issues/375), [#376](https://github.com/sol-aeternum/Daemon/issues/376), [#378](https://github.com/sol-aeternum/Daemon/issues/378) and [#379](https://github.com/sol-aeternum/Daemon/issues/379). Experimental feature labels remain; no deployment, cleanup, model-routing or broader permissions are implied by a PR.
