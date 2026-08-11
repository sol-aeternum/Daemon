# Hosted Identity Architecture

This document locks Daemon's hosted identity claim layer. It supplements
[`docs/AUTH_ARCHITECTURE.md`](AUTH_ARCHITECTURE.md): Google Sign-In and email code
flows prove account identity, then Daemon issues its existing per-device sessions. They do
not replace Daemon device token authorization for protected APIs.

## Scope

Hosted identity is for the public hosted onboarding path, such as `daemon.ai`, where a
user proves control of a Google account or email address, claims their personal tenant,
and enrolls the current browser or native app as a Daemon device.

Self-hosted first-boot setup remains available as the **Advanced** path. Hosted identity
does not remove the setup-token flow, existing enrollment flow, refresh rotation, or
self-hosted recovery guidance.

## Locked Invariants

1. **Identity proof is not API auth.** Protected APIs trust only Daemon-issued access,
   device, and session tokens. Google credentials, Google ID tokens, email codes, invite
   tokens, setup tokens, and enrollment tokens never authorize normal APIs directly.
2. **Daemon owns session issuance.** Google and email completion endpoints exchange a
   validated identity proof for a Daemon device/session pair, then protected routes keep
   using `Authorization: Bearer <daemon_access_token>`.
3. **Refresh transport stays split.** Web refresh uses a FastAPI-set HttpOnly cookie;
   native refresh uses JSON body tokens stored in the platform secure store. Mixed
   transport is rejected.
4. **Signup is invite-only by default.** Hosted production does not create public accounts
   unless a later explicit product decision changes the signup mode.
5. **One account owns one personal tenant.** The personal tenant is the default workspace
   for hosted identity. Ambiguous singleton backfill aborts rather than guessing.

## Identity Model

### Google identity

- The durable Google identity is the Google `sub` claim. Store it as the provider subject
  for the Google identity provider record.
- Email is mutable profile data, not the durable account key. A Google account changing
  email must not create a second Daemon account when the `sub` is already known.
- Google-to-email-account linking is allowed only when all of these are true:
  - the Google email is verified;
  - the normalized email exactly matches the existing Daemon account email;
  - no conflicting provider identity already exists for that account; and
  - invite policy permits the link.
- If any linking condition fails, the request is rejected or halted for explicit operator
  review. Daemon must not merge accounts by fuzzy email matching or by unverified email.

### Email identity

- Email-code sign-in proves control of the normalized email address for that challenge.
- The email address remains mutable account metadata. Re-verification is required for any
  future address change or sensitive linking decision.
- Invite matching is exact on normalized email. Invites are not transferable across
  different normalized addresses.

## Google Web Sign-In Flow

Daemon uses the Google Identity Services manual JavaScript callback with a server-issued
nonce.

1. The frontend calls the Daemon Google-start endpoint.
2. The server generates a CSPRNG nonce, stores only a verifier or HMAC-bound challenge
   record with TTL and single-use state, and returns the nonce plus challenge reference.
3. The frontend initializes GIS with the configured client ID, manual callback, and the
   server-issued nonce.
4. GIS invokes the browser callback with a Google credential. The browser posts the
   credential, challenge reference, nonce, client kind, and device metadata to Daemon.
5. Daemon verifies the ID token server-side: signature/library result, issuer, audience,
   expiry, subject, verified email, nonce binding, and replay/single-use state.
6. Daemon resolves or creates the account according to invite policy, claims or reuses the
   account's personal tenant, and issues a Daemon device/session.

The GIS `login_uri` auto-post pattern is not the approved flow. Switching to `login_uri`
is a halt condition: it requires explicit approval and must implement Google's
`g_csrf_token` double-submit check before any credential-bearing auto-post endpoint is
accepted.

## Email Code Flow

The email code flow is a public identity-proof flow and must be resistant to enumeration,
replay, brute force, and database disclosure.

1. `start` accepts a normalized email candidate and returns a generic accepted response.
   The response must not reveal whether an account or invite exists.
2. Daemon generates the email code with a CSPRNG and stores only an HMAC/verifier artifact,
   never the plaintext code.
3. Challenge records carry a short TTL, attempt cap, consumed/locked state, and HMAC-bound
   request metadata where needed for abuse analysis.
4. Redis-backed rate limits cover starts and submissions by normalized-email hash and IP
   hash. Hosted production fails closed if required nonce, challenge, or rate-limit Redis
   enforcement is unavailable.
5. `complete` validates the code in constant-time style where practical, decrements attempts
   atomically on failure, consumes on success, and uses the same generic failure response
   for wrong, expired, locked, missing, or replayed codes.
6. Plaintext email codes must not appear in database rows, application logs, audit rows,
   analytics, evidence files, browser storage, or API responses. Development mail sinks may
   display the code only through an explicit dev-only path.
7. After successful proof and invite/account resolution, Daemon issues a normal
   device/session for the requested web or native client kind.

## Session Issuance and API Authorization

Hosted identity completion uses the same authorization substrate as setup and enrollment:
Daemon-created devices, sessions, access tokens, and refresh tokens.

### Web

- Web completion returns access-only JSON for JavaScript memory and sets the rotating
  refresh token in the `__Host-daemon_refresh` cookie in production and secure development.
- The cookie is HttpOnly, Secure, SameSite=Strict, Path=/, and has no Domain attribute.
- JavaScript never receives the refresh token and must not store access tokens, Google
  credentials, email codes, or Daemon credentials in `localStorage` or `sessionStorage`.

### Native

- Native completion returns both `access_token` and `refresh_token` in JSON.
- The native client stores the refresh token in the platform secure store, such as iOS
  Keychain, Android Keystore, macOS Keychain, Linux keyring, or an app-owned secure vault.
- Native refresh sends the refresh token in the request body and sets no cookies.

### Mixed transport

Requests that mix cookie refresh and body refresh are rejected before rotation. The check
must preserve the existing refresh reuse model: consumed refresh tokens are never accepted,
and reuse detection revokes the affected device and sessions.

## Personal Tenant Claim

- Every hosted identity account has exactly one personal tenant with singular owner
  membership.
- Existing singleton installs backfill into one personal tenant only when the current data
  is unambiguous.
- Backfill aborts on duplicate singleton ownership, conflicting durable provider identities,
  duplicate personal tenants for the same account, or any state that would require an unsafe
  automatic merge.
- Hosted identity does not introduce full organization administration, billing roles, or
  multi-owner tenant semantics.

## Add-Device and Device Policy

Identity sign-in from a new browser or native app is the hosted add-device path for an
existing account: once Google or email proof succeeds, Daemon creates a new device and
session for that account's personal tenant. Existing in-app enrollment remains available
for adding devices from an already-authenticated device.

Daemon allows unlimited devices by default. Device management must emphasize visibility and
revocation rather than a hard cap. Temporary or public-computer sessions, when offered, are
narrow capability sessions with a short TTL and cannot authorize normal protected APIs
unless a route is explicitly labeled `temporary-session-allowed`.

After successful session creation, Daemon sends a best-effort new-device notification. The
notification is sent only after the session exists, never blocks login, and contains no
secrets, tokens, codes, nonces, invite material, or provider credentials.

Residual phishing and social-engineering risk remains in scope for operator awareness:
attackers can still trick users into entering email codes, selecting the wrong Google
account, or approving unexpected sign-ins. Daemon mitigates this with nonce-bound
challenges, single-use proofs, generic responses, short TTLs, new-device visibility, and
revocation; these controls reduce but do not eliminate user-targeted deception risk.

## Frontend Deployment Env Contract

The hosted landing and Google sign-in button are gated by two `NEXT_PUBLIC_*` env vars that
are baked at Next.js build time. They mirror the backend hosted-identity knobs and must be
set in `.env.example` and the frontend `docker-compose.yml` environment block:

| Var                                  | Default                    | Effect                                                                                                                                                 |
| ------------------------------------ | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE` | `self-hosted` (when unset) | `hosted` switches the landing to Google / email sign-in primary; `self-hosted` keeps the setup-first landing.                                          |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID`       | empty (no Google button)   | Public Google OAuth web client ID. When set, the hosted landing renders the Google sign-in button. Must match the backend's `DAEMON_GOOGLE_CLIENT_ID`. |

Required pairing:

- `DAEMON_HOSTED_IDENTITY_ENABLED=true` ↔ `NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE=hosted`
- `DAEMON_GOOGLE_CLIENT_ID=<server client id>` ↔ `NEXT_PUBLIC_GOOGLE_CLIENT_ID=<same public client id>`

Hosted mode and the Google button are only meaningful when the backend has
`DAEMON_HOSTED_IDENTITY_ENABLED=true`; the backend `fail-closed` gate in
`orchestrator/routes/auth_setup.py` rejects hosted email/Google requests with
`404 hosted_identity_disabled` regardless of frontend configuration when the backend is
self-hosted. Setup, enrollment, and device endpoints remain available for self-hosted and
recovery flows on the same router.

When hosted auth runs through the Next.js frontend auth proxy, operators may optionally set
`DAEMON_TRUST_PROXY_FORWARDED_CLIENT_IP=true` on the backend so identity rate limits can
key on the original browser IP carried by the frontend's internal `X-Daemon-Client-IP`
header instead of the proxy/container hop. The frontend auth proxy only sets that internal
header from `X-Forwarded-For` / platform client-IP headers when its `DAEMON_TRUSTED_PROXY_IPS`
allowlist contains the immediate proxy IP from `x-real-ip`; configure the reverse proxy to
overwrite, not append, client IP headers. Leave both settings unset/false for direct/self-hosted
deployments; the default safe posture is to trust only the immediate client IP and ignore
arbitrary forwarded headers.

## Runtime Config

Daemon exposes a public, unauthenticated, no-store endpoint that lets the frontend
read its deployment mode and provider availability at runtime instead of relying
on a build-time `NEXT_PUBLIC_*` env:

| Method | Path                | Auth     | Cache                    | Body                                                                                                                                                                                                                |
| ------ | ------------------- | -------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/v1/auth/config`   | None     | `Cache-Control: no-store` | `{ "mode": "self_hosted" \| "hosted", "email": { "enabled": <bool> }, "google": { "enabled": <bool>, "clientId": <public id or ""> } }` |

The endpoint returns only non-secret runtime data. It never serializes the
audience allowlist, mail sender mode, refresh TTLs, pepper, or any other secret
or secret-adjacent value. The `google.clientId` is the public OAuth client ID,
not a secret. The frontend caches a successful response for at most 60 seconds
and refreshes it while the auth provider remains mounted. An unavailable or
invalid response retains the fail-safe unresolved-mode behavior (`/setup`).

`mode` is sourced from `DAEMON_DEPLOYMENT_MODE` (default `self_hosted`).
`email.enabled` is true only when both `DAEMON_HOSTED_IDENTITY_ENABLED` and
`DAEMON_EMAIL_ENABLED` are true. `google.enabled` is true only when both
`DAEMON_HOSTED_IDENTITY_ENABLED` and `DAEMON_GOOGLE_ENABLED` are true.
`google.clientId` is the value of `DAEMON_GOOGLE_CLIENT_ID` or `""` when unset.

When `mode == "hosted"`, the legacy `POST /v1/auth/setup` endpoint refuses to
initialize owner/admin state with `403 setup_disabled_in_hosted_mode`. The
self-hosted setup-token flow remains available in `self_hosted` mode.

## Self-Hosted Advanced Setup

Hosted deployments should present Google and email code sign-in first. The self-hosted
setup-token path remains available under **Advanced** for operators who run their own
Daemon instance or need zero-active-device recovery.

The Advanced setup path keeps the existing security properties documented in
[`docs/AUTH_SETUP.md`](AUTH_SETUP.md): setup tokens are pasted into a form body, never a
URL; first-boot setup creates the first web device; additional devices can still use
enrollment; and native clients continue to use JSON-body refresh tokens.

## Explicit Non-Goals

This hosted identity layer does not add passwords, GitHub login, passkeys/WebAuthn/FIDO2,
SAML, broad organization administration, push authentication, billing policy, provider-token
API auth, or a fallback shared API-key mode.
