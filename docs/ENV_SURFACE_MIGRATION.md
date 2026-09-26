# Environment surface migration

Covers the env-surface cleanup that reorganizes `.env.example`, tightens two
Compose security defaults, and adds an env-parity gate. Read
`AGENTS.md` ("Keep the env surface in sync in the same commit") for the rule this
change is meant to enforce.

## Scope, and the condition everything below depends on

This note describes the repository's own `docker-compose.yml` path. **The actual
production launch configuration is unproven.** No tracked file records which
command, override file, or systemd unit the production host uses. Everything in
"what a missing key resolves to" below is therefore conditional:

- **Audited Compose path** — `migrate`, `backend`, `worker`, `frontend`,
  `postgres`, `redis`, `crawl4ai` as defined in `docker-compose.yml`, with the
  repository root bind-mounted at `/app` in the three Python services and the
  root `.env` interpolated by Compose.
- **Any other launch path** — a different Compose file, `--env-file`, an
  orchestrator, or a hand-built image. There, missing-key behavior follows that
  configuration plus the code defaults in `orchestrator/config.py` and
  `config/video_pricing.py`, not the table here. Supply the real launch
  configuration to make this note deployment-specific.

No value in any real `.env` was read, listed, or changed for this work, and no
live or production setting was modified.

## What changed in the repository

| File | Change |
|---|---|
| `.env.example` | Regrouped into secrets → deployment configuration → commented optional overrides. Dead declarations removed. Previously undocumented non-tier Settings/video inputs are documented once; tier overrides retain an enumerated coverage exception. |
| `docker-compose.yml` | Two missing-key fallbacks only: `DAEMON_ENVIRONMENT` and `DAEMON_COOKIE_SECURE`. |
| `.gitignore`, index | `.envsitter/` is ignored and the previously tracked `.envsitter/pepper` entry is removed from the index. No history rewrite. |
| `tests/test_env_surface_parity.py` | New gate over the example, the settings classes, Compose interpolation, the frontend sources, and the documented per-service exceptions. |
| `AGENTS.md` | Scope-aware rule for keeping the env surface in sync, with the migration-note requirement. |

## The only two behavior changes

Both are missing-key fallbacks in the audited Compose path. An explicit value in
your environment still wins, so existing explicit `development` / `false` values
remain opt-ins and are unchanged.

| Variable | Missing key before | Missing key after | Explicit value |
|---|---|---|---|
| `DAEMON_ENVIRONMENT` | `development` in backend and worker | `production` in backend and worker | Unchanged; still passed through verbatim. |
| `DAEMON_COOKIE_SECURE` | `false` in backend | `true` in backend | Unchanged; still passed through verbatim. |

`.env.example` leaves both names commented, so a freshly copied file takes the
production fallbacks. Existing `.env` files are not rewritten; a local
development stack opts back in by uncommenting `DAEMON_ENVIRONMENT=development`
and `DAEMON_COOKIE_SECURE=false`.

**Existing sessions end on upgrade when the cookie fallback applies.** The
refresh cookie is named `daemon_refresh` when insecure and
`__Host-daemon_refresh` when secure, and the backend reads only the name that
matches its current mode. A deployment that omitted `DAEMON_COOKIE_SECURE` (and
so ran insecure) switches to the secure name, so every signed-in user must sign
in again after the first refresh fails. A deployment reached over plain HTTP on
a host other than `localhost` cannot keep a session at all afterwards, because
browsers drop `Secure` cookies there; serve it over HTTPS or set
`DAEMON_COOKIE_SECURE=false` together with `DAEMON_ENVIRONMENT=development`.

Two rules that make this class of edit easy to get wrong:

- **Absent is not the same as empty.** `KEY=${KEY}` injects an empty string
  when host interpolation cannot resolve the name, overriding the code default
  and bind-mounted `.env`. Removing the Compose entry allows the dotenv/code
  sources to apply and removes shell passthrough for that name. With `:-`, both
  an absent and an explicitly empty interpolation value use the fallback.
- **A code default that is `production` is not a substitute for configuration.**
  After this change, a deployment that sets neither `DAEMON_ENVIRONMENT` nor
  `DAEMON_ALLOWED_HOSTS` starts in production mode and fails host validation at
  startup. Set the values in the table below before deploying.

### Worker behavior is deliberately preserved

Apart from the approved environment-mode fallback, worker configuration is preserved. The nine keys
below remain backend-only by explicit role exception, and were **not** copied
into the worker environment:

`DAEMON_ALLOWED_ORIGINS`, `DAEMON_ALLOWED_HOSTS`, `DAEMON_DEFAULT_TIMEZONE`,
`DAEMON_PUBLIC_ORIGIN`, `DAEMON_COOKIE_SECURE`,
`DAEMON_RATE_LIMIT_CHAT_PER_TOKEN_PER_MINUTE`,
`DAEMON_RATE_LIMIT_CHAT_PER_USER_PER_MINUTE`,
`DAEMON_RATE_LIMIT_CHAT_PER_IP_PER_MINUTE`,
`DAEMON_INTERNAL_PROXY_HMAC_SECRET`.

These entries configure HTTP host/CORS policy, chat-prompt timezone, cookies,
request rate limits, CSRF origin, and trusted proxy authentication. They remain
on the serving process's explicit injection surface. This preserves the existing
worker precedence contract rather than asserting the worker cannot read them.
Making the two lists identical would have silently changed worker behavior
(missing-key origins, public origin, cookie flags, and a new shell-override
channel where previously only the bind-mounted file applied), so list unification
was not performed. Any future move of one of these nine to the worker needs its
own approval.

## Production keys to set, per the audited Compose path

Review this table against the real launch configuration. "Current absence result"
is what the audited path produces, not an observation of production.

### Add or confirm

| Key | Why it matters now | Notes |
|---|---|---|
| `DAEMON_ENVIRONMENT` | Selects production validation: strong pepper required, insecure cookies rejected, empty host allowlist rejected. | Set it explicitly. The new `production` fallback means an unset key no longer keeps a production host in development mode. |
| `DAEMON_COOKIE_SECURE` | Secure flag on auth cookies; production requires `true`. | Set it explicitly for HTTPS. The new `true` fallback means an unset key no longer downgrades to `false` on the backend. |
| `DAEMON_ALLOWED_HOSTS` | Production rejects an empty allowlist at startup, and the Compose entry is empty by default. | Set the real external and internal hostnames. `*.example.com` wildcards are supported. Never ship `*` unless you accept any Host header. |
| `DAEMON_AUTH_PEPPER` | Production refuses to start without at least 32 random bytes. | Must be a strong, persistent, operator-generated value. Do not reuse another secret. See "Two different peppers". |
| `DAEMON_ALLOWED_ORIGINS` | Browser origins allowed to make cross-origin requests; Compose injects `http://localhost:3000` when the key is absent or empty. | Replace with the real browser origins. Denying all cross-origin use with an empty value requires an explicit service-environment override: an empty root dotenv value alone triggers the `:-` fallback. |
| `DAEMON_PUBLIC_ORIGIN` | Origin used for CSRF validation on cookie auth; Compose injects `http://localhost:3000` when the key is absent. | Replace with the real HTTPS public origin. |
| `NEXT_PUBLIC_API_URL` | Public backend URL baked into the browser bundle at image build time. | See "Frontend URLs". Set it before `docker compose build`. |
| `DAEMON_INTERNAL_API_URL` | Optional server-only upstream for the Next.js route handlers. | See "Frontend URLs". Setting it in the root `.env` alone has no effect. |
| `POSTGRES_PASSWORD` | Compose refuses to start when it is missing or empty, and production rejects known-default passwords. | Mandatory. No rename, no new fallback. |
| `DAEMON_ENCRYPTION_KEY` | Fernet key for memory content; memory startup fails without it. | Mandatory. Key presence says nothing about validity. |

### Keep — these look dead but are not

Do not delete these. Each has a live consumer, and the cleanup deliberately kept
every one of them.

| Key | Consumer |
|---|---|
| `ENV` | Declared `Settings` input. Deprecated as a label, but no behavior reads it and it is not safe to remove from a file other tooling may read. |
| `STREAM_PING_INTERVAL_S` | Live legacy fallback for the SSE keepalive interval, applied whenever `DAEMON_SSE_KEEPALIVE_INTERVAL_S` is unset. |
| `ENCRYPTION_KEY` | Read directly (not via `Settings`) by `scripts/test_retrieval_quality.py`, which prefers it over its own development-only placeholder. It is **not** the production memory key; that is `DAEMON_ENCRYPTION_KEY`. Note for that script an empty value is not the same as an absent value. |
| `NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE`, `NEXT_PUBLIC_GOOGLE_CLIENT_ID`, `NEXT_PUBLIC_EMAIL_ENABLED` | Still passed as image build arguments, read by `frontend/lib/deployment.ts` and the frontend tests, and kept in the image environment for compatibility. `GET /v1/auth/config` is authoritative at runtime. |
| `LOG_LEVEL`, `DEFAULT_PROVIDER`, `OPENROUTER_BASE_URL`, `REQUEST_TIMEOUT_S` | No explicit Compose environment entry on the Python services, so on the audited path they depend on the bind-mounted `/app/.env`. Removing them from that file restores the code defaults. `MOCK_LLM` is separately forwarded by both service environments. |

### Removed declarations — dead or nonfunctional

These were removed from `.env.example`. Remove them from a deployment only after
checking operator tooling that lives outside this repository, because a key
present in an environment file but unread by the app is invisible to the audit
and harmless to keep.

| Key | Note |
|---|---|
| `TIER1_MODELS` | Was a `/v1/models` recommendation label, not configuration. No field, no reader. |
| `LITELLM_MODEL` | No reader. Distinct from the live `LITELLM_MODE` used by tests. |
| `OPENCODE_API_KEY`, `OPENCODE_BASE_URL`, `OPENCODE_MODEL` | No audited provider implementation reads them; the advertised provider option is not implemented in the audited code. |
| `PROVIDER_CUSTOM_BASE_URL`, `PROVIDER_CUSTOM_API_KEY`, `PROVIDER_CUSTOM_MODEL`, `PROVIDER_CUSTOM_REQUIRES_AUTH` | The resolver looks for `PROVIDER_{NAME}_*` names, but undeclared fields are ignored when configuration is parsed, so these never took effect; an unknown provider name falls back to the `openrouter` configuration. The example now carries an accurate "not supported" note instead of a working-looking example. The resolver code is unchanged. |
| `TIER_PRO_VIDEO_COST_PER_SEC`, `TIER_MAX_VIDEO_COST_PER_SEC` | Commented examples of names absent from the audited fields. **There is no value-preserving rename.** The real inputs are `VIDEO_COST_5S` … `VIDEO_COST_30S` (integer credit amounts) and `VIDEO_TIER_PRO_DISCOUNT` / `VIDEO_TIER_MAX_DISCOUNT` / `VIDEO_TIER_BYOK_DISCOUNT` (multipliers applied to those credits). A dollars-per-second figure and a credit-count or discount factor are different quantities, so port the intent manually instead of renaming. |
| `OPENAI_SORA_API_KEY` | No field, reader, or provider path. `OPENAI_API_KEY` is the live key and is unchanged. |

### Documentation only — no production change required

The 49 previously undocumented settings/video inputs are now documented in
`.env.example` as commented optional overrides showing defaults or illustrative
values for optional settings, and
the parity gate requires that coverage going forward. **Adding them to a
deployment is not required.** Leave a name out and the code default in
`orchestrator/config.py` / `config/video_pricing.py` applies unchanged. Set one
only when you deliberately want a different value, and prefer documenting the
decision in a compose override rather than in the shared `.env`.

For names without a Compose environment entry, the audited path relies on the
bind-mounted root `.env`. Both services read `/app/.env`, but any process
environment value still outranks the file. Existing Compose injections remain
authoritative for names they cover.

## Frontend URLs

The frontend has no source bind mount, so it never reads the root `.env`. Two
distinct things are easy to conflate:

- `NEXT_PUBLIC_API_URL` is **build-time**. Compose interpolates it as a build
  argument (falling back to `http://localhost:8000`) and it is inlined into the
  browser bundle. The frontend service also pins a runtime `NEXT_PUBLIC_API_URL`
  to `http://localhost:8000`, so the build-time value and the container runtime
  value can disagree. Set the variable before building an image for a real
  deployment; changing it afterwards requires a rebuild.
- `DAEMON_INTERNAL_API_URL` is **server-only runtime**, used by the Next.js
  route handlers, which fall back through `NEXT_PUBLIC_API_URL` to a
  handler-specific default (typically `http://backend:8000`). Compose does not forward
  it into the frontend service, so declaring it in `.env.example` documents the
  knob but does not wire it. Forwarding it, and reconciling the build/runtime
  public-URL mismatch, are separate follow-ups below.

## Two different peppers

- `DAEMON_AUTH_PEPPER` is an application credential: the HMAC pepper for
  enrollment codes. Production requires a strong persistent value, and rotating it
  invalidates outstanding enrollment codes. **It is not affected by the tooling
  change below.**
- `.envsitter/pepper` belongs to the local EnvSitter tooling, not to Daemon. It
  was tracked in the repository; this change ignores `.envsitter/` and removes
  the entry from the index (no history rewrite, so it remains in past commits).
  Operators should delete their local copy and regenerate it through EnvSitter.
  Existing fingerprints will no longer match, and nothing in the application
  reads this file.

## Not in this change

Deliberately deferred, each needing its own approval:

- Repairing or removing the non-functional custom-provider resolver path ([#319](https://github.com/sol-aeternum/Daemon/issues/319)).
- Converting credential settings fields to `SecretStr` so values stop appearing
  in reprs and logs.
- Restructuring the tier model/provider/temperature configuration surface.
- Forwarding `DAEMON_INTERNAL_API_URL` into the frontend service and reconciling
  the build-time versus runtime public API URL ([#320](https://github.com/sol-aeternum/Daemon/issues/320)).
- Repairing the retrieval-quality diagnostic script so it stops reading a
  separate key name ([#321](https://github.com/sol-aeternum/Daemon/issues/321)).
- Removing the disconnected `SettingsPanel` component and the
  `NEXT_PUBLIC_API_BASE_URL` name it declares; both are kept for now, and the
  parity gate carries an explicit allowlist entry for that stale source.
- Establishing and documenting the real production launch path, which is what
  would let this note state observed production values instead of conditional
  ones.

## Verifying your own deployment

- Diff the key names in your environment against `.env.example` by name only;
  no value needs to change for any of the additions.
- Confirm your launch path resolves `DAEMON_ENVIRONMENT`, `DAEMON_COOKIE_SECURE`
  and `DAEMON_ALLOWED_HOSTS` the way this note assumes.
- The parity gate (`tests/test_env_surface_parity.py`) covers repository drift
  only. It reads class metadata and tracked files; it does not inspect any
  environment file, start the app or a worker, or contact the network.
