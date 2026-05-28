# Authentication Setup Guide — Daemon

This guide covers Daemon's per-device authentication model for self-hosted deployments.

## First Boot Setup

On a fresh Daemon installation with no active devices, the backend logs a one-time setup token at startup:

```
>>> Daemon setup required. Open http://<host>:<port>/setup and enter token: <plaintext>
```

Open the URL in your browser. You will see a setup form — **paste the token from the logs into the form field**. Do not pass it as a URL parameter or query string.

### Why Token-in-URL Is Unsafe

Passing the setup token via URL exposes it to multiple leakage vectors:

- **Browser history**: URLs are stored in browser history, persisting beyond the session.
- **Referer headers**: When you navigate away from `/setup`, the Referer header sends the full URL (including the token) to the destination server.
- **Server access logs**: Most web servers log the full request URL — the token would be written to disk in plaintext.
- **Bookmarks**: If you bookmark the setup URL, the token is permanently stored in the bookmark.

Daemon's form-based flow avoids all of these: the token is transmitted in a POST body, never appears in a URL, and is logged by the backend only once at startup.

### Server Log Sensitivity

The one-time setup token **is written to server logs at startup**. Treat your logs as sensitive: anyone with access to server log output can see the token needed to complete first-boot setup. After setup is completed successfully, the token is burned and cannot be reused.

---

## Adding Devices (Enrollment)

After first-boot setup, you can enroll additional devices (browsers, native clients) from the Devices panel in the web UI.

### Enrollment Flow

1. Open Daemon in an existing enrolled browser.
2. Go to **Settings → Devices → Add New Device**.
3. A QR code and a text fallback are displayed:
   - **QR code**: Scan with a new browser or the native client camera.
   - **Text fallback**: Copy the enrollment link or code manually.

The QR payload uses the scheme `daemon-enroll://<pending_id>#<code>`. For manual copy, the UI shows both the `pending_id` and the `code` (formatted as `NNNN-NNNN`) separately.

### Device Types

- **Web**: Uses browser cookie-based authentication. After enrollment, the web device receives an HttpOnly refresh cookie and a short-lived access token held in JavaScript memory only.
- **Native**: CLI tools, mobile apps, or other native integrations. After enrollment, the native client receives `access_token` and `refresh_token` in the JSON response body. The native client must store the refresh token in the platform's secure credential store (e.g., OS keychain).

---

## Revoking Devices

From **Settings → Devices**, you can see all enrolled devices. Each device shows:

- Device name / kind (web or native)
- Last active timestamp
- Whether it is the current device

To revoke access:

1. Click **Revoke** next to the device you want to remove.
2. All active sessions for that device are immediately terminated.
3. Any access tokens for that device are invalidated instantly.
4. If you revoke the current web browser device, the refresh cookie is cleared and you are redirected to `/setup`.

Revocation is permanent. To regain access, the device must be re-enrolled from an already-authenticated browser.

---

## Recovery — All Devices Revoked or Lost

If all devices are revoked (or lost) and no enrolled browser can reach the Devices panel, recovery is possible because the setup condition is tied to **active device count**, not to whether setup has ever run.

When the server restarts with **zero active devices**, it will log a new one-time setup token:

```
>>> Daemon setup required. Open http://<host>:<port>/setup and enter token: <plaintext>
```

Because the in-memory setup token is invalidated on each restart, a new token is generated. Complete first-boot setup again from any browser to create a fresh device and regain access.

This means recovery requires server restart access — a self-hosted deployment's physical or container restart resets the setup flow when no devices are active.

---

## Development Environment Note

During development with `DAEMON_ENVIRONMENT=development`:

- `DAEMON_AUTH_PEPPER` is optional. If absent, Daemon generates a per-process random pepper and logs a warning. **All pending enrollments created with a development ephemeral pepper are invalidated after every server restart.** Enrollments must be re-initiated after each restart.
- Insecure cookies (`Secure=false`) are allowed with `DAEMON_COOKIE_SECURE=false`. Do not use these settings in production.
- CORS is relaxed for localhost origins.

### `DAEMON_AUTH_PEPPER` Requirements

`DAEMON_AUTH_PEPPER` is a shared secret used to derive enrollment code verifiers. It is never stored in the database.

| Environment | Requirement |
|-------------|-------------|
| **Production** | Must be set. Must be at least **32 random bytes** / **43 base64url characters**. Missing or weak values cause startup to fail. |
| **Development** | If absent, a per-process random pepper is generated with a warning. All pre-restart pending enrollments are invalidated. |

Generate a strong pepper for production:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Store the pepper in your deployment's secret management system (environment variable, secrets vault, Docker secret, etc.), not in source code or configuration files that are committed to version control.

---

## Browser Security

Daemon's web authentication uses a split-token design:

| Token | Storage | Lifetime |
|-------|---------|----------|
| `access_token` | JavaScript memory only | 30 minutes |
| `refresh_token` | `__Host-daemon_refresh` HttpOnly cookie | 90 days, rotating |

### What This Means

- The **access token** is never stored in `localStorage`, `sessionStorage`, or any browser storage. JavaScript holds it in memory for the duration of the browser session. Closing the tab or browser clears it.
- The **refresh token** is set as an `HttpOnly; Secure; SameSite=Strict` cookie named `__Host-daemon_refresh`. JavaScript cannot read it — not even via `document.cookie`. This blocks XSS attacks from stealing the refresh token.
- On every authenticated request, the frontend automatically refreshes the access token if it is expired or near expiry, using the HttpOnly cookie — no JavaScript involvement required.
- The `__Host-` prefix enforces that the cookie is locked to the exact host, has `Path=/`, and requires `Secure`. No `Domain` attribute is set, preventing the cookie from being sent to subdomains.

### What Daemon Does NOT Do

- **Does not store credentials in `localStorage` or `sessionStorage`**. Auth tokens are in-memory (access token) or HttpOnly cookie (refresh token) only.
- **Does not pass access tokens in URLs**. Auth header (`Authorization: Bearer <token>`) is used for API calls.
- **Does not use shared secrets for authentication**. The legacy single-secret model has been removed. All auth is per-device and revocable.

---

## Native Client Note

Native clients (CLI tools, mobile apps, scripts) authenticate differently than browsers because they cannot use HttpOnly cookies.

### Enrollment (Native)

1. Start enrollment from an enrolled web browser (Settings → Devices → Add New Device → Native).
2. The web UI shows a `pending_id` and `NNNN-NNNN` enrollment code.
3. In your native client, use the enrollment code to complete the flow.
4. The server returns a JSON response:

```json
{
  "access_token": "<opaque 43-char base64url token>",
  "refresh_token": "<opaque 43-char base64url token>",
  "expires_in": 1800
}
```

**Store `refresh_token` in your platform's secure credential store** (iOS Keychain, Android Keystore, macOS Keychain, Linux keyring, or a secure vault). Do not store it in plain text files, environment variables, or source code.

### Refresh (Native)

Native clients refresh by sending the stored `refresh_token` in the request body:

```json
POST /v1/auth/refresh
Content-Type: application/json

{
  "refresh_token": "<stored refresh token>"
}
```

The server returns a new `access_token` and `refresh_token` pair. The old refresh token is consumed and cannot be reused — reuse detection immediately revokes the entire device.

### Mixed Mode Rejected

Daemon rejects requests that mix cookie-based and body-based refresh in the same call. If you are using the cookie path (web browser), do not also send a body refresh token. If you are using the native body path, do not send a cookie. Mixed requests return `400 Bad Request`.

---

## Summary

| Topic | Key Point |
|-------|-----------|
| First boot | Form-based one-time token from server logs; never in URL |
| Adding devices | QR or manual code from enrolled browser; web cookie or native JSON-body token |
| Revoking | Immediate session/token invalidation; current-device revoke clears cookie |
| Recovery | Zero active devices + restart → new setup token logged |
| Pepper | Production requires ≥32 bytes / 43 base64url chars; missing/weak fails startup |
| Browser auth | Access token in JS memory; refresh in HttpOnly `__Host-` cookie |
| Native auth | Access + refresh returned in JSON; refresh must use platform secure storage |
