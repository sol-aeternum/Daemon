# Daemon Auth / Device Architecture

This document locks the architecture for replacing the legacy shared-credential authentication model with per-device opaque-token authentication. It is derived from `.sisyphus/plans/auth-device-model.md`, `_scratch_auth_audit.md`, and `_scratch_auth_research.md` only.

Hosted identity is specified separately in [`docs/HOSTED_IDENTITY.md`](HOSTED_IDENTITY.md). That layer uses Google or email proof to claim an account and issue Daemon-owned device sessions; it does not change this document's core rule that protected APIs trust only Daemon-issued access/device/session tokens.

## Locked Decisions

### Decision 1 — Hard-remove legacy credential model
The legacy shared-credential model is removed as an authentication approach. There is no API-key coexistence, fallback token env var, migration helper, deprecation path, or feature flag for the old model.

### Decision 2 — Preserve the singleton user
The existing singleton user ID `00000000-0000-0000-0000-000000000001` remains the user identity. Setup find-or-creates this user inside the setup transaction and must not destroy existing conversations, memories, settings, or video-credit ownership.

### Decision 3 — First boot is zero-active-device based
First-boot setup is available when `COUNT(*) FROM devices WHERE revoked_at IS NULL` is zero, not when the users table is empty. Revoking all devices and restarting the backend re-enters setup.

### Decision 4 — Setup token hash is shared runtime state
The setup token is generated on startup, written to the local operator token file with `0600` permissions, and stored only as a SHA-256 hash in the `system_state` table under `auth.setup_token_hash`. Multiple backend workers share the same verifier and burn the same row after successful setup. The plaintext setup token is never written to application logs.

### Decision 5 — Setup token never goes in URLs
The setup token is pasted into `/setup` as form/body data only. It must not appear in a URL, query string, Referer header, browser history, bookmark, proxy log, or server access log.

### Decision 6 — Setup is transaction-locked
Successful setup uses the shared auth-runtime advisory lock (`pg_advisory_xact_lock(hashtext('daemon:auth_runtime_state'))`), rechecks zero active devices inside the transaction, creates the first device/session, and deactivates setup.

### Decision 7 — Tokens are 256-bit opaque values
Setup, access, and refresh tokens are opaque CSPRNG values generated with `secrets.token_urlsafe(32)`, yielding 256 bits of entropy and about 43 base64url characters.

### Decision 8 — Token storage uses SHA-256 hashes
Access and refresh tokens are stored only as deterministic SHA-256 hashes. Plaintext access and refresh tokens are never stored or logged, and verification uses constant-time comparison.

### Decision 9 — Enrollment codes use HMAC-SHA256 verifiers
Enrollment codes are low-entropy codes and are stored only as HMAC-SHA256 verifiers keyed by `DAEMON_AUTH_PEPPER`. `pending_enrollments` stores no plaintext code and no raw `code_hash` lookup path.

### Decision 10 — `DAEMON_AUTH_PEPPER` is mandatory in production
Production startup fails if `DAEMON_AUTH_PEPPER` is missing or weak; it must be at least 32 random bytes / 43 base64url characters. Development uses `DAEMON_AUTH_PEPPER` when set; if absent and Postgres is configured, Daemon stores a development-only shared pepper in `system_state` under `auth.development_pepper` so pending enrollment verifiers remain valid across backend workers. If no DB is available, development falls back to a process-ephemeral pepper with a warning.

### Decision 11 — Access-token TTL is 30 minutes
Access tokens expire after 30 minutes and are accepted only as `Authorization: Bearer <device_access_token>` on protected routes.

### Decision 12 — Refresh-token TTL is 90 days
Refresh tokens expire after 90 days. Web refresh tokens live in FastAPI-set HttpOnly cookies; native refresh tokens are returned in response bodies for secure platform storage.

### Decision 13 — Enrollment TTL is 10 minutes
Pending enrollments expire after 10 minutes. Wrong enrollment attempts are tracked on the pending enrollment row, and expired, consumed, or exhausted enrollments fail closed.

### Decision 14 — Cleanup grace is 7 days and interval is 24 hours
Session cleanup keeps consumed rows long enough for reuse detection, then deletes stale sessions after the 7-day grace. The cleanup interval defaults to 24 hours and must not delete active or recently needed history.

### Decision 15 — FastAPI owns auth cookies
FastAPI is the only component that sets or clears auth cookies. Next.js API routes/proxies pass browser `Cookie` headers to FastAPI and pass through every FastAPI `Set-Cookie` header without comma-folding or rewriting.

### Decision 16 — Web refresh cookie is `__Host-daemon_refresh`
The production and secure-development web refresh cookie is named `__Host-daemon_refresh` and uses `Path=/; Secure; HttpOnly; SameSite=Strict` with no `Domain` attribute.

### Decision 17 — Insecure cookies are development-gated
`DAEMON_COOKIE_SECURE=false` is allowed only when `DAEMON_ENVIRONMENT=development` and must log a warning. In insecure development, FastAPI uses the unprefixed `daemon_refresh` cookie without `Secure` because browsers reject insecure `__Host-` cookies. Production cookies are always `Secure`.

### Decision 18 — Cookie-backed endpoints require CSRF/origin checks
Cookie-backed setup, enrollment completion, and refresh endpoints enforce CSRF checks with `Sec-Fetch-Site`, `Origin`, and `Referer`/origin fallback. Cross-site and `Origin: null` requests are rejected; sensitive absent-header cases fail closed.

### Decision 19 — Protected routes accept access tokens only
Protected API routes never authenticate with refresh cookies. Refresh cookies are valid only on refresh/setup/enrollment cookie-setting endpoints.

### Decision 20 — Refresh reuse revokes the device
Every refresh rotates to a new refresh token and atomically consumes the old session. Presenting an already-consumed refresh token is reuse and revokes the owning device plus all sessions.

### Decision 21 — There is no refresh grace window
No leeway/grace window accepts a consumed refresh token. The frontend must serialize refresh attempts across tabs with Web Locks where available and BroadcastChannel coordination fallback without transmitting tokens.

### Decision 22 — Native clients use the refresh-body path
Native enrollment and refresh use `client_kind='native'`, return replacement refresh tokens in JSON bodies, and set no cookies. Cookie plus body refresh token mixed mode is rejected with 400 before rotation.

### Decision 23 — Frontend access token is memory-only
The web frontend stores access tokens only in module-level memory / React context. Refresh tokens are hidden in HttpOnly cookies and JavaScript never reads them.

### Decision 24 — Browser storage is forbidden for credentials
No auth credential may be stored in browser `localStorage` or `sessionStorage`, including access tokens, refresh tokens, enrollment codes, setup tokens, API keys, JWTs, or equivalent credentials.

### Decision 25 — Route modules must be hardened
All formerly API-key-protected endpoints and currently-unprotected route modules must be deliberately wrapped with access-token auth, while `/health` stays public and auth endpoints are explicitly public only where required.

### Decision 26 — SSE/chat authenticates at request start
Daemon chat streaming uses POST/fetch rather than browser `EventSource`. The frontend pre-refreshes before long streams, sends `Authorization: Bearer <access_token>` when opening the request, and reconnects with a fresh token if a new request is needed.

## Schema Definitions

Task 5 will materialize this logical schema in the next migration. Names may add implementation-specific indexes, but the sensitive-data constraints below are locked.

### `devices`

```sql
CREATE TABLE devices (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  display_name TEXT NOT NULL,
  platform TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);

CREATE INDEX idx_devices_user_id ON devices(user_id);
CREATE INDEX idx_devices_active_user_id ON devices(user_id) WHERE revoked_at IS NULL;
```

### `sessions`

```sql
CREATE TABLE sessions (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  client_kind TEXT NOT NULL CHECK (client_kind IN ('web', 'native')),
  access_token_hash TEXT NOT NULL UNIQUE,
  access_expires_at TIMESTAMPTZ NOT NULL,
  refresh_token_hash TEXT NOT NULL UNIQUE,
  refresh_expires_at TIMESTAMPTZ NOT NULL,
  refresh_consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  revoked_at TIMESTAMPTZ
);

CREATE INDEX idx_sessions_device_id ON sessions(device_id);
CREATE INDEX idx_sessions_access_token_hash ON sessions(access_token_hash);
CREATE INDEX idx_sessions_refresh_token_hash ON sessions(refresh_token_hash);
CREATE INDEX idx_sessions_cleanup ON sessions(refresh_expires_at, revoked_at);
```

### `pending_enrollments`

```sql
CREATE TABLE pending_enrollments (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_by_device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  code_verifier_hash TEXT NOT NULL,
  wrong_attempts_remaining INTEGER NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pending_enrollments_user_id ON pending_enrollments(user_id);
CREATE INDEX idx_pending_enrollments_expires_at ON pending_enrollments(expires_at);
```

`pending_enrollments` stores the HMAC-SHA256 verifier only. It must not contain plaintext `code`, raw `code_hash`, or any lookup path based on a direct hash of the low-entropy code.

## Cookie and Proxy Model

FastAPI owns cookie issuance, replacement, and clearing. Next.js proxies are transport shims: they forward browser cookies and CSRF-relevant headers (`Origin`, `Referer`, `Sec-Fetch-Site`, `Host`, `X-Forwarded-*`) to FastAPI, then pass through each `Set-Cookie` response header separately.

Web responses return JSON access tokens only. Web refresh tokens are only in `__Host-daemon_refresh`; refresh cookies are never accepted as protected-route auth.

## CSRF and Origin Model

Cookie-backed endpoints enforce:

1. Reject `Sec-Fetch-Site: cross-site`.
2. Allow same-origin and user-initiated `none` where appropriate.
3. If `Sec-Fetch-Site` is absent, validate `Origin` against `DAEMON_ALLOWED_ORIGINS`.
4. Treat `Origin: null` as hostile.
5. Use `Referer` only as a fallback origin signal.
6. Fail closed for sensitive cookie-backed requests with missing origin metadata.

Native body-path refresh has no browser cookie and skips CSRF checks when no cookie is present.

## First-Boot Setup

```text
Diagram: Setup

Backend startup
  -> count active devices
  -> if count == 0: generate 256-bit setup token
  -> store SHA-256 token hash in system_state
  -> write plaintext setup token to local 0600 operator file
User opens /setup
  -> pastes token into form body (not URL)
  -> Next.js POSTs to FastAPI with CSRF headers
FastAPI setup transaction
  -> pg_advisory_xact_lock(hashtext('daemon:auth_runtime_state'))
  -> recheck zero active devices
  -> find-or-create singleton user
  -> create first device + web session
  -> return access token JSON + Set-Cookie refresh cookie
```

Setup is web-only for the first device. Native onboarding starts through enrollment after a web setup device exists.

## Enrollment

```text
Diagram: Enrollment

Existing authenticated device
  -> POST /v1/auth/enroll/start with access token
FastAPI
  -> generate pending_id + NNNN-NNNN code
  -> store HMAC-SHA256(code, DAEMON_AUTH_PEPPER)
  -> set expires_at = now + 10 minutes
  -> return QR payload daemon-enroll://<pending_id>#<code> and code text
New device
  -> POST /v1/auth/enroll/complete with pending_id, code, client_kind
FastAPI
  -> normalize code and compare HMAC verifier
  -> decrement attempts on wrong code
  -> consume pending enrollment on success
  -> create device + session
  -> web: access token JSON + refresh cookie
  -> native: access token + refresh token JSON, no cookie
```

Enrollment never stores plaintext codes and never performs direct raw-code-hash lookup.

## Refresh and Reuse

```text
Diagram: Refresh/Reuse

Client requests POST /v1/auth/refresh
  -> web: send refresh cookie only
  -> native: send refresh_token body only
FastAPI
  -> reject mixed cookie + body mode
  -> hash presented refresh token
  -> atomic consume: UPDATE sessions ... WHERE refresh_consumed_at IS NULL RETURNING *
  -> if consumed: insert replacement session and return replacement tokens
  -> if zero rows: second lookup distinguishes bad/expired/revoked/consumed
  -> if already consumed: revoke owning device + all sessions and clear cookie
```

There is no refresh grace window. Consumed session rows remain until cleanup grace so reuse can be detected.

## Recovery

```text
Diagram: Recovery

All devices revoked or expired
  -> active device count becomes zero
Backend restart
  -> startup sees zero active devices
  -> creates auth.setup_token_hash in system_state if absent
  -> worker that creates it writes the one-time setup token to the local operator file
Owner opens /setup
  -> enters token in form body
  -> creates a new first active device
Existing singleton data
  -> conversations, memories, settings, and credits remain attached to singleton user
```

Recovery is device-based, not account-reset based. Email recovery, password recovery, and social-login recovery are out of scope.

## Proxy Cookie Flow

```text
Diagram: Proxy Cookie Flow

Browser
  -> sends Cookie + Origin/Referer/Sec-Fetch-Site to Next.js /api/*
Next.js proxy
  -> forwards Cookie and CSRF-relevant headers to FastAPI
FastAPI auth endpoint
  -> validates CSRF/origin where cookie-backed
  -> rotates/sets/clears __Host-daemon_refresh
  -> returns one or more Set-Cookie headers
Next.js proxy
  -> passes through each Set-Cookie separately, no comma-folding
Browser
  -> stores HttpOnly refresh cookie; JS cannot read it
```

## Route-Hardening Model

Old directly protected `orchestrator/main.py` routes must move from legacy `require_api_key` to access-token auth: `/v1/tools/test`, `/providers`, `/chat/completions`, `/tts`, `/audio/token`, `/audio/scribe-token`, `/stt`, `/sound-effects`, and `/chat`.

Audit gaps to harden:

- `conversations.py`: currently zero auth on conversation CRUD.
- `users.py`: currently zero auth on settings routes.
- `system.py`: currently zero auth on `/status`.
- `memories.py`: currently partial auth; admin `/dream` protected but memory list/import/export/reembed/delete/create/update/confirm/consolidate routes are unprotected.
- `skills.py`: currently partial auth; admin sync protected but skill CRUD/upload/download/toggle routes are unprotected.
- `video_credits.py`: currently fully protected with legacy API-key/admin-key helpers and must move to device auth/admin semantics.
- `orchestrator/routes/images.py`: legacy Studio image endpoints are retained as device-authenticated retired routes that return 410 after auth; a hosted-identity replacement is tracked separately.

`/health` remains public. Generated media/file serving and model/catalog endpoints must be explicitly classified during hardening rather than inheriting accidental public/private behavior.

## Frontend Token Model

The web frontend uses an auth runtime/AuthProvider with memory-only access-token state. It refreshes with `credentials: "include"`, updates memory with the new access token, and clears memory state when logout/revoke clears the cookie. Existing localStorage credential references and direct backend calls carrying stored credentials are removed.

SSE/chat requests pre-refresh before opening long streams. The access token is attached at request creation; if a new request is required after expiry, the frontend refreshes and reconnects.

## Out-of-Scope Follow-Ups

The OAuth/PKCE and social login/OIDC exclusions below were scoped to the prior auth-device-model architecture task and implementation. Hosted Google/email identity claim is now specified by [`docs/HOSTED_IDENTITY.md`](HOSTED_IDENTITY.md) and remains an identity-proof-to-Daemon-session exchange, not direct provider-token API authorization.

The following remain explicitly out of scope for the auth-device-model foundation and hosted identity claim unless a later plan says otherwise: passkeys/WebAuthn/FIDO2, 2FA/TOTP, email/password recovery, push auth, API-key coexistence, runtime auth-mode switches, migration helpers, fallback auth env vars, setup-token URLs, broad organization administration, and provider tokens as protected API credentials.
