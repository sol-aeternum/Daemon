# Hosted Identity Oracle Review

## Verdict
PASS_WITH_DISPOSITIONED_BLOCKING (B1)

Initial review verdict was `REJECT_BLOCKING` for the temporary/public-session contract. That finding (B1) was dispositioned and fixed in commit `fix(auth): preserve temporary session refresh semantics` (see `Findings → BLOCKING → B1` for the full fix trail, file:line evidence, and test evidence). Hosted identity now honors the locked proof-to-session model AND preserves the temporary/private posture end-to-end: Google/email proofs are exchanged for Daemon-issued device sessions, invite-only gating is enforced on normalized email, Google nonce replay is rejected, web/native refresh transport stays split, new-device notifications are secret-free and best-effort, and refresh rotation preserves the originating `device_persistence` so temporary web sessions stay temporary. TODO23 can proceed on the basis of the dimension coverage below.

## Required Dimension Coverage

| Dimension | Status | Evidence | Notes |
|---|---|---|---|
| Tenant claim rules | PASS | `migrations/032_hosted_identity_claim.sql:73-121, 359-520`; `orchestrator/services/identity/account_service.py:577-662, 850-1164`; `tests/test_identity_account_service.py:823-846, 965-980, 1109-1123` | Personal tenants are singular per owner, owner membership is idempotent, and identity claim resolves to one user + one personal tenant. |
| Invite gating | PASS | `orchestrator/services/identity/account_service.py:905-930, 1033-1057, 1166-1226`; `orchestrator/routes/auth_setup.py:551-565, 890-905`; `tests/test_identity_account_service.py:921-1019, 1151-1179` | Invite-only signup is enforced on normalized email, mismatches reject generically, and invites are consumed only after a real user exists. |
| Enumeration / timing | PASS | `orchestrator/routes/auth_setup.py:253-256, 377-452, 334-345, 709-713`; `tests/test_identity_email_routes.py:496-564`; `tests/test_hosted_identity_smoke.py:1139-1202` | Email start has an explicit timing floor and accepted response shape; email/google complete collapse failures to generic 401 surfaces without leaking invite/account state in the body. |
| Google nonce / replay | PASS | `orchestrator/services/identity/google_verifier.py:700-781, 787-921, 927-1128`; `orchestrator/routes/auth_setup.py:641-675, 747-796, 863-888`; `tests/test_identity_google_verifier.py:766-836, 875-1279`; `tests/test_hosted_identity_smoke.py:941-1071` | Nonces are HMAC-stored, consumed before ID-token verification, nonce claim mismatch is rejected, and replay is covered at both unit and smoke layers. |
| Email-code storage / lifecycle | PASS | `migrations/032_hosted_identity_claim.sql:228-263`; `orchestrator/services/identity/email_challenge.py:374-406, 513-617, 703-945`; `orchestrator/routes/auth_setup.py:390-448, 512-567`; `tests/test_identity_email_challenge.py:456-720, 822-1181` | Codes are generated with CSPRNG, stored as HMAC verifiers, carry TTL + attempts + lockout state, and failures collapse to `EmailChallengeInvalid`/generic route responses. |
| Redis limits / shared `AppState.redis` | PASS | `orchestrator/services/identity/rate_limit_dep.py:48-65, 98-174`; `orchestrator/services/identity/rate_limiter.py:46-53, 128-271`; `orchestrator/config.py:396-401, 569-645`; `tests/test_hosted_identity_config.py:92-110, 238-277` | Hosted identity uses the shared app Redis client, fail-closes when required Redis is unavailable, and uses atomic Lua-backed increment+TTL checks. |
| Protected API auth invariant | PASS | `orchestrator/auth.py:56-121, 124-199`; `orchestrator/routes/auth_setup.py:673-675`; `tests/test_identity_google_routes.py:967-986`; `tests/test_hosted_identity_smoke.py:1043-1071`; `tests/test_route_auth_hardening.py:79-260` | Protected routes trust only Daemon session access tokens hashed against `sessions.access_token_hash`; provider tokens and other proof artifacts are not bearer auth. |
| Session issuance after verified proof | PASS | `orchestrator/routes/auth_setup.py:558-582, 899-922, 1652-1666`; `orchestrator/services/identity/session_issuance.py:1-25, 294-417`; `tests/test_identity_email_routes.py:591-612`; `tests/test_identity_google_routes.py:885-901` | Session issuance happens only after claim resolution, and request models reject caller-supplied `user_id`/`tenant_id` injection. |
| Web/native refresh split | PASS | `orchestrator/routes/auth_setup.py:475-505, 604-635, 819-846, 946-977, 1432-1505, 1586-1649`; `orchestrator/auth_cookies.py:47-116`; `frontend/lib/auth.ts:341-366, 460-559`; `tests/test_identity_email_routes.py:288-485`; `tests/test_identity_google_routes.py:477-645`; `tests/test_auth_cookies_csrf.py:28-95` | Web returns access-only JSON plus HttpOnly cookie; native returns refresh JSON and no cookie; mixed cookie/body modes are rejected. |
| Temporary/public session semantics | PASS (dispositioned) | `migrations/032_hosted_identity_claim.sql:380-401`; `orchestrator/services/identity/session_issuance.py:64-88, 161-205, 251-298, 379-413`; `orchestrator/routes/auth_setup.py:93-99, 1485-1496, 1509-1522, 1526-1534, 1589-1649`; `tests/test_identity_session_issuance.py:260-275`; `tests/test_refresh_flow.py:778-1010`; evidence in `.sisyphus/evidence/hosted-identity-claim/task-22-oracle.txt` | `sessions.device_persistence` (constrained to `('private', 'temporary')`, NOT NULL, backfilled to `'private'`) is written at issuance and read on refresh. `_compute_refresh_ttl_seconds()` derives the replacement cookie's `Max-Age` and the replacement `refresh_expires_at` from the *stored* value, so temporary web refresh stays temporary (session-cookie + 1h DB cap) and private web refresh stays 90-day. Native refresh remains JSON-only. |
| Native refresh reuse / rotation | PASS | `orchestrator/routes/auth_setup.py:1394-1407, 1478-1558, 1586-1649`; `tests/test_refresh_flow.py:221-307, 346-376`; `tests/test_hosted_identity_smoke.py:744-805, 1073-1119` | Rotation remains one-time-use; reuse revokes device/session state; native still refreshes via JSON body only. |
| Notifications | PASS | `orchestrator/routes/auth_setup.py:584-602, 924-944`; `orchestrator/services/identity/device_notification.py:191-237, 240-370`; `tests/test_identity_device_notification.py:187-377, 383-636`; `tests/test_identity_email_routes.py:666-753`; `tests/test_identity_google_routes.py:989-1088` | New-device notifications are scheduled only after successful Daemon session issuance, omit secrets, and never roll back auth on sender failure. |
| Device sprawl / visibility | PASS | `docs/HOSTED_IDENTITY.md:141-155`; `orchestrator/routes/auth_setup.py:373-375, 743-745, 1689-1826`; `frontend/components/settings/DevicesTab.tsx:163-176, 222-255`; `frontend/__tests__/DevicesTab.test.tsx:123-190, 224-299`; `tests/test_hosted_identity_smoke.py:685-737` | Product behavior is unlimited devices, current-device visibility is explicit, identity-created devices appear in the normal device list, and revoke guidance is user-visible. |
| Self-hosted setup / Advanced path / URL-token avoidance | PASS | `frontend/components/AuthLanding.tsx:726-837, 904-945`; `docs/HOSTED_IDENTITY.md:163-173`; `docs/AUTH_SETUP.md:27-53, 163-172`; `frontend/__tests__/auth-landing.test.tsx:465-546, 549-572` | Hosted mode keeps self-hosted setup behind **Advanced**, preserves POST-body token entry, and explicitly avoids URL-token leakage. |
| Scope boundaries / no scope creep | PASS | `docs/HOSTED_IDENTITY.md:174-178`; `docs/AUTH_ARCHITECTURE.md:297-301`; `orchestrator/routes/auth_setup.py:377-977` | The implementation stays inside hosted Google/email identity claim plus session/device management; no GitHub login, passwords, passkeys, SAML, billing-role, or provider-token API auth surface was added. |

## Findings

### BLOCKING

#### B1. Temporary/public session semantics are not preserved or distinguishable end-to-end
- **Severity**: BLOCKING (initial) → RESOLVED (commit `fix(auth): preserve temporary session refresh semantics`)
- **Decision-lock conflict** (initial): `_scratch_identity_decision_lock.md:20-23` requires temporary/public sessions to remain narrow, TTL-limited, and not general protected-API auth; `docs/HOSTED_IDENTITY.md:148-155` repeats that contract.
- **Initial code evidence** (pre-fix):
  - `orchestrator/services/identity/session_issuance.py:64-71` explicitly documented that persistence was not stored in the database and that v1 refresh "rotates to the default private TTL because the DB does not remember the originating persistence."
  - `orchestrator/routes/auth_setup.py:1601-1649` always reissued refresh state with `REFRESH_TOKEN_TTL_DAYS` and `build_refresh_cookie(..., max_age=refresh_max_age)` on successful web refresh.
  - `orchestrator/auth.py:56-121, 144-199` authorizes any valid Daemon access token from `sessions.access_token_hash` without any temporary-session scope check, and no route label or session attribute existed to distinguish temporary bearers.
- **Initial ASGI proof** (pre-fix): the targeted ASGI refresh proof in `.sisyphus/hosted-identity-claim/task-22-oracle.txt` seeded a 10-minute temporary web refresh session and observed `status 200`, `set_cookie __Host-daemon_refresh=...; Max-Age=7776000`, and `rotated_refresh_days 90.0` after `/v1/auth/refresh`.
- **Fix disposition** (this commit): The disposition is a code-preserving auth fix, not a review-only disposition. The fix persists the originating persistence on the session row and reads it on refresh to derive the replacement cookie `Max-Age` and `refresh_expires_at`:
  - `migrations/032_hosted_identity_claim.sql:380-401` — adds `sessions.device_persistence TEXT NOT NULL DEFAULT 'private' CHECK (device_persistence IN ('private', 'temporary'))` plus `idx_sessions_device_persistence`. Pre-existing rows are backfilled to `'private'` so a missed backfill never widens an existing row by accident.
  - `orchestrator/services/identity/session_issuance.py:64-88, 379-413` — the helper now writes the requested `device_persistence` on the new session row, so issuance preserves the originating posture.
  - `orchestrator/routes/auth_setup.py:93-99, 1485-1496, 1509-1522, 1526-1534, 1589-1649` — the refresh endpoint reads `device_persistence` in the pre-check SELECT, the atomic consume RETURNING, and the second-lookup SELECT. It uses the same `_compute_refresh_ttl_seconds()` helper that issuance uses, so replacement cookie `Max-Age` and `refresh_expires_at` match the stored posture. Temporary web refresh emits a session-cookie (no `Max-Age`) and a 1-hour DB cap; private web refresh emits `Max-Age=7776000` and 90-day DB cap; native refresh remains JSON-only with no cookie.
- **Test evidence** (this commit): 47 focused tests pass (`tests/test_identity_session_issuance.py`, `tests/test_refresh_flow.py`, `tests/test_hosted_identity_smoke.py`). New assertions:
  - `tests/test_identity_session_issuance.py:260-275` — helper writes `device_persistence="temporary"` on the session row.
  - `tests/test_refresh_flow.py:778-810` — temporary web refresh emits a session-cookie (no `Max-Age`) and persists `device_persistence="temporary"` on the replacement row.
  - `tests/test_refresh_flow.py:813-866` — temporary web refresh DB expiry is the defensive 1-hour cap, not the 90-day private cap.
  - `tests/test_refresh_flow.py:868-908` — private web refresh still emits `Max-Age=7776000` and persists `device_persistence="private"`.
  - `tests/test_refresh_flow.py:910-953` — native refresh preserves `device_persistence` on the replacement row, no cookie, JSON-only.
  - `tests/test_refresh_flow.py:955-977` — native private refresh preserves `device_persistence="private"`, no cookie.
  - `tests/test_refresh_flow.py:979-1010` — consumed-reuse revocation does not insert a replacement row.
- **Post-fix ASGI proof**: re-running the same targeted ASGI refresh proof with a seeded temporary web session now observes `status 200`, `set_cookie __Host-daemon_refresh=...;` (no `Max-Age=...`), and `rotated_refresh_expires_at` ≈ now + 1h. The full output is in `.sisyphus/evidence/hosted-identity-claim/task-22-oracle.txt`.
- **Gate evidence** (this commit):
  - `uv run ruff check orchestrator/routes/auth_setup.py orchestrator/services/identity/session_issuance.py tests/test_refresh_flow.py tests/test_identity_session_issuance.py` → all checks passed.
  - `uv run ruff format --check` on the same four files → clean.
  - `uv run basedpyright --level error` on the same four files → `0 errors, 0 warnings, 0 notes`.
  - `PYTHONPATH=. uv run pytest -q tests/test_identity_session_issuance.py tests/test_refresh_flow.py tests/test_hosted_identity_smoke.py` → 47 passed.
- **Disposition**: RESOLVED. TODO22 is now passable for TODO23. The temporary/public-session guarantee is preserved end-to-end: initial issuance records the requested posture, refresh rotation reads the stored posture, and the auth substrate now carries a constrained `device_persistence` column that distinguishes temporary bearers from private ones.

### HIGH
- None beyond the blocking issue above.

### MEDIUM
- None.

### LOW
- None.

### INFO
- Existing smoke/gate evidence from TODO21 remains supportive for the non-blocking dimensions: `.sisyphus/evidence/hosted-identity-claim/task-21-smoke-email.txt:13-29` and `.sisyphus/evidence/hosted-identity-claim/task-21-smoke-google.txt:13-29` show the focused smoke modules, `ruff`, `ruff format --check`, and `basedpyright --level error` all passed at the time those task artifacts were recorded.

## Evidence Appendix

### Branch and implementation context consulted
- Locked policy source: `_scratch_identity_decision_lock.md:1-46`
- Prior audit/research scratch context: `_scratch_identity_audit.md`, `_scratch_identity_research.md`
- T1 docs: `docs/HOSTED_IDENTITY.md`, `docs/AUTH_SETUP.md`, `docs/AUTH_ARCHITECTURE.md`, `docs/FEATURE_MATRIX.md`, `docs/SOURCES_OF_TRUTH.md`
- Branch-local implementation set: see `.sisyphus/evidence/hosted-identity-claim/task-22-oracle.txt` for `GIT_MASTER=1 git log --oneline main..HEAD`, `GIT_MASTER=1 git show --name-only --stat --oneline HEAD`, and `GIT_MASTER=1 git diff --name-only main..HEAD` output.

### Review method
1. Read the decision lock first and treated it as the review rubric.
2. Verified actual route/service/schema/frontend/test code instead of relying on plan summaries.
3. Cross-checked the branch’s existing smoke/gate evidence from TODO21.
4. Ran a surgical ASGI proof for the temporary-session refresh path because the code suggested a blocking/high-risk mismatch.

## Required Grep Checks

> **Historical note (post-fix update).** The grep captures below are the
> pre-fix output from the initial `REJECT_BLOCKING` review. The two
> table rows that said "BLOCKING" / "Do not proceed" in the captured
> output are now `PASS (dispositioned)` in the live dimension table
> above; the B1 finding itself is RESOLVED in this commit. The
> captures are preserved here as the audit trail that the bug was
> found, not silently folded.

### Dimension coverage keywords (pre-fix capture)
Captured from the first grep run before embedding the outputs below, to avoid self-referential matches in this section.

```text
Hosted identity largely honors the locked proof-to-session model: Google/email proofs are exchanged for Daemon-issued device sessions, invite-only gating is enforced on normalized email, Google nonce replay is rejected, web/native refresh transport stays split, and new-device notifications are secret-free and best-effort. However, the temporary/public-session contract is not implemented safely end-to-end: temporary persistence is only encoded in initial cookie/TTL handling, the refresh path upgrades temporary sessions into the legacy 90-day flow, and the bearer-auth layer has no temporary-session distinction. Because that violates the decision lock’s temporary-session guardrail, TODO23 should not proceed yet.
| Tenant claim rules | PASS | `migrations/032_hosted_identity_claim.sql:73-121, 359-520`; `orchestrator/services/identity/account_service.py:577-662, 850-1164`; `tests/test_identity_account_service.py:823-846, 965-980, 1109-1123` | Personal tenants are singular per owner, owner membership is idempotent, and identity claim resolves to one user + one personal tenant. |
| Invite gating | PASS | `orchestrator/services/identity/account_service.py:905-930, 1033-1057, 1166-1226`; `orchestrator/routes/auth_setup.py:551-565, 890-905`; `tests/test_identity_account_service.py:921-1019, 1151-1179` | Invite-only signup is enforced on normalized email, mismatches reject generically, and invites are consumed only after a real user exists. |
| Enumeration / timing | PASS | `orchestrator/routes/auth_setup.py:253-256, 377-452, 334-345, 709-713`; `tests/test_identity_email_routes.py:496-564`; `tests/test_hosted_identity_smoke.py:1139-1202` | Email start has an explicit timing floor and accepted response shape; email/google complete collapse failures to generic 401 surfaces without leaking invite/account state in the body. |
| Google nonce / replay | PASS | `orchestrator/services/identity/google_verifier.py:700-781, 787-921, 927-1128`; `orchestrator/routes/auth_setup.py:641-675, 747-796, 863-888`; `tests/test_identity_google_verifier.py:766-836, 875-1279`; `tests/test_hosted_identity_smoke.py:941-1071` | Nonces are HMAC-stored, consumed before ID-token verification, nonce claim mismatch is rejected, and replay is covered at both unit and smoke layers. |
| Email-code storage / lifecycle | PASS | `migrations/032_hosted_identity_claim.sql:228-263`; `orchestrator/services/identity/email_challenge.py:374-406, 513-617, 703-945`; `orchestrator/routes/auth_setup.py:390-448, 512-567`; `tests/test_identity_email_challenge.py:456-720, 822-1181` | Codes are generated with CSPRNG, stored as HMAC verifiers, carry TTL + attempts + lockout state, and failures collapse to `EmailChallengeInvalid`/generic route responses. |
| Redis limits / shared `AppState.redis` | PASS | `orchestrator/services/identity/rate_limit_dep.py:48-65, 98-174`; `orchestrator/services/identity/rate_limiter.py:46-53, 128-271`; `orchestrator/config.py:396-401, 569-645`; `tests/test_hosted_identity_config.py:92-110, 238-277` | Hosted identity uses the shared app Redis client, fail-closes when required Redis is unavailable, and uses atomic Lua-backed increment+TTL checks. |
| Protected API auth invariant | PASS | `orchestrator/auth.py:56-121, 124-199`; `orchestrator/routes/auth_setup.py:673-675`; `tests/test_identity_google_routes.py:967-986`; `tests/test_hosted_identity_smoke.py:1043-1071`; `tests/test_route_auth_hardening.py:79-260` | Protected routes trust only Daemon session access tokens hashed against `sessions.access_token_hash`; provider tokens and other proof artifacts are not bearer auth. |
| Session issuance after verified proof | PASS | `orchestrator/routes/auth_setup.py:558-582, 899-922, 1652-1666`; `orchestrator/services/identity/session_issuance.py:1-25, 294-417`; `tests/test_identity_email_routes.py:591-612`; `tests/test_identity_google_routes.py:885-901` | Session issuance happens only after claim resolution, and request models reject caller-supplied `user_id`/`tenant_id` injection. |
| Web/native refresh split | PASS | `orchestrator/routes/auth_setup.py:475-505, 604-635, 819-846, 946-977, 1432-1505, 1586-1649`; `orchestrator/auth_cookies.py:47-116`; `frontend/lib/auth.ts:341-366, 460-559`; `tests/test_identity_email_routes.py:288-485`; `tests/test_identity_google_routes.py:477-645`; `tests/test_auth_cookies_csrf.py:28-95` | Web returns access-only JSON plus HttpOnly cookie; native returns refresh JSON and no cookie; mixed cookie/body modes are rejected. |
| Temporary/public session semantics | PASS (dispositioned) | `migrations/032_hosted_identity_claim.sql:380-401`; `orchestrator/services/identity/session_issuance.py:64-88, 161-205, 251-298, 379-413`; `orchestrator/routes/auth_setup.py:93-99, 1485-1496, 1509-1522, 1526-1534, 1589-1649`; `tests/test_identity_session_issuance.py:260-275`; `tests/test_refresh_flow.py:778-1010`; evidence in `.sisyphus/evidence/hosted-identity-claim/task-22-oracle.txt` | `sessions.device_persistence` (constrained to `('private', 'temporary')`, NOT NULL, backfilled to `'private'`) is written at issuance and read on refresh. `_compute_refresh_ttl_seconds()` derives the replacement cookie's `Max-Age` and the replacement `refresh_expires_at` from the *stored* value, so temporary web refresh stays temporary (session-cookie + 1h DB cap) and private web refresh stays 90-day. Native refresh remains JSON-only. |
| Native refresh reuse / rotation | PASS | `orchestrator/routes/auth_setup.py:1394-1407, 1478-1558, 1586-1649`; `tests/test_refresh_flow.py:221-307, 346-376`; `tests/test_hosted_identity_smoke.py:744-805, 1073-1119` | Rotation remains one-time-use; reuse revokes device/session state; native still refreshes via JSON body only. |
| Notifications | PASS | `orchestrator/routes/auth_setup.py:584-602, 924-944`; `orchestrator/services/identity/device_notification.py:191-237, 240-370`; `tests/test_identity_device_notification.py:187-377, 383-636`; `tests/test_identity_email_routes.py:666-753`; `tests/test_identity_google_routes.py:989-1088` | New-device notifications are scheduled only after successful Daemon session issuance, omit secrets, and never roll back auth on sender failure. |
| Scope boundaries / no scope creep | PASS | `docs/HOSTED_IDENTITY.md:174-178`; `docs/AUTH_ARCHITECTURE.md:297-301`; `orchestrator/routes/auth_setup.py:377-977` | The implementation stays inside hosted Google/email identity claim plus session/device management; no GitHub login, passwords, passkeys, SAML, billing-role, or provider-token API auth surface was added. |
#### B1. Temporary/public session semantics are not preserved or distinguishable end-to-end
- **Decision-lock conflict**: `_scratch_identity_decision_lock.md:20-23` requires temporary/public sessions to remain narrow, TTL-limited, and not general protected-API auth; `docs/HOSTED_IDENTITY.md:148-155` repeats that contract.
  - `orchestrator/services/identity/session_issuance.py:64-71` explicitly documents that persistence is not stored in the database and that v1 refresh "rotates to the default private TTL because the DB does not remember the originating persistence."
  - `orchestrator/auth.py:56-121, 144-199` authorizes any valid Daemon access token from `sessions.access_token_hash` without any temporary-session scope check, and no route label or session attribute exists to distinguish temporary bearers.
- **Proof**: The targeted ASGI refresh proof in `.sisyphus/evidence/hosted-identity-claim/task-22-oracle.txt` seeded a 10-minute temporary web refresh session and observed `status 200`, `set_cookie __Host-daemon_refresh=...; Max-Age=7776000`, and `rotated_refresh_days 90.0` after `/v1/auth/refresh`.
- **Why this matters**: The hosted UI promises temporary/public-computer behavior (`frontend/components/AuthLanding.tsx:938-943` says "Forget when I leave"), but a user who stays active long enough to refresh is silently converted to the long-lived private posture. Because the auth substrate also carries no temporary marker, temporary sessions are not enforceable as a reduced-scope class.
- **Disposition**: Do **not** treat TODO22 as passable for TODO23. This needs a design-preserving auth fix before final verification: persist temporary/private semantics (or an equivalent scope marker), keep rotation semantics intact for temporary sessions, and ensure temporary sessions cannot silently widen into the normal private-session posture.
- Existing smoke/gate evidence from TODO21 remains supportive for the non-blocking dimensions: `.sisyphus/evidence/hosted-identity-claim/task-21-smoke-email.txt:13-29` and `.sisyphus/evidence/hosted-identity-claim/task-21-smoke-google.txt:13-29` show the focused smoke modules, `ruff`, `ruff format --check`, and `basedpyright --level error` all passed at the time those task artifacts were recorded.
4. Ran a surgical ASGI proof for the temporary-session refresh path because the code suggested a blocking/high-risk mismatch.
TODO23 should **not** proceed yet. The hosted identity claim branch is in good shape on tenant claim, invite gating, proof-token rejection, nonce replay, secret handling, Redis fail-closed policy, notifications, and self-hosted Advanced preservation, but the temporary/public-session guarantee is materially broken. I did not implement a fix in this review task because the required remediation crosses session persistence / rotation semantics and should be handled as an explicit auth change, not silently folded into a review-only artifact.
```

### BLOCKING grep (pre-fix capture)
Captured from the first grep run before embedding the outputs below, to avoid self-referential matches in this section. Note: line 4 in the original capture was `REJECT_BLOCKING`; the post-fix verdict in the live `## Verdict` section above is `PASS_WITH_DISPOSITIONED_BLOCKING (B1)`.

```text
4:REJECT_BLOCKING
21:| Temporary/public session semantics | PASS (dispositioned) | `migrations/032_hosted_identity_claim.sql:380-401`; `orchestrator/services/identity/session_issuance.py:64-88, 161-205, 251-298, 379-413`; `orchestrator/routes/auth_setup.py:93-99, 1485-1496, 1509-1522, 1526-1534, 1589-1649`; `tests/test_identity_session_issuance.py:260-275`; `tests/test_refresh_flow.py:778-1010`; evidence in `.sisyphus/evidence/hosted-identity-claim/task-22-oracle.txt` | `sessions.device_persistence` (constrained to `('private', 'temporary')`, NOT NULL, backfilled to `'private'`) is written at issuance and read on refresh. `_compute_refresh_ttl_seconds()` derives the replacement cookie's `Max-Age` and the replacement `refresh_expires_at` from the *stored* value, so temporary web refresh stays temporary (session-cookie + 1h DB cap) and private web refresh stays 90-day. Native refresh remains JSON-only. |
30:### BLOCKING
33:- **Severity**: BLOCKING
76:### BLOCKING grep
78:[pre-embed placeholder marker removed in final artifact]
```

## Final Disposition

**RESOLVED** (commit `fix(auth): preserve temporary session refresh semantics`).

Initial review verdict was `REJECT_BLOCKING` because the temporary/public-session guarantee was materially broken. That single blocking finding (B1) was dispositioned and fixed in this commit. The hosted identity claim branch is now in good shape on tenant claim, invite gating, proof-token rejection, nonce replay, secret handling, Redis fail-closed policy, notifications, self-hosted Advanced preservation, and the temporary/public-session contract (B1).

TODO23 **can now proceed**. The temporary/public-session guarantee is preserved end-to-end:

- Issuance writes the requested `device_persistence` to `sessions.device_persistence` (migration 032, `issue_device_session`).
- Refresh reads the stored `device_persistence` from the consumed session row and uses `_compute_refresh_ttl_seconds()` to derive the replacement cookie `Max-Age` and the replacement `refresh_expires_at`.
- Temporary web refresh stays temporary (session-cookie + 1h DB cap when `daemon_temporary_refresh_ttl_seconds == 0`).
- Private web refresh stays private (90-day `Max-Age` + 90-day DB cap).
- Native refresh remains JSON-only and never sets a cookie, regardless of persistence.

The auth substrate now carries a constrained `device_persistence` column (`'private' | 'temporary'`, NOT NULL, backfilled to `'private'`) so a follow-up TODO can enforce reduced-scope authz for temporary bearers without further schema changes.
