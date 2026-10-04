"""Read-only, immutable authority for a single progressive speech operation.

This does not refresh credentials, write last_seen, or widen request-entry auth.
The original HTTP bearer hash and all three authenticated identities remain bound
for the lifetime of the stream, including across browser credential rotation.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import time
from typing import Any

from fastapi import Request

from orchestrator.auth import AuthenticatedDevice, _extract_bearer_token
from orchestrator.auth_tokens import hash_token
from orchestrator.speech.contracts import SpeechError

REFRESH_SECONDS = 5.0
REFRESH_BUDGET_SECONDS = 2.0

_QUERY = """
SELECT s.access_expires_at, clock_timestamp() AS checked_at
FROM sessions s JOIN devices d ON d.id = s.device_id
WHERE s.access_token_hash = $1
  AND s.user_id = $2 AND s.device_id = $3 AND s.id = $4
  AND s.revoked_at IS NULL AND d.revoked_at IS NULL
  AND s.access_expires_at > clock_timestamp()
"""


class SpeechAuthority:
    def __init__(self, pool: Any, auth: AuthenticatedDevice, token_hash: str):
        self._pool, self._auth, self._token_hash = pool, auth, token_hash
        self._expires = 0.0
        self._refresh_due = 0.0
        self._error: SpeechError | None = None
        self._ready = asyncio.Event()
        self._failed: asyncio.Future[SpeechError] = asyncio.get_running_loop().create_future()
        self._monitor: asyncio.Task[None] | None = None
        self._refresh_lock = asyncio.Lock()

    @classmethod
    async def from_request(cls, request: Request, auth: AuthenticatedDevice) -> SpeechAuthority:
        token = _extract_bearer_token(request.headers.get("Authorization"))
        if token is None:
            raise SpeechError("speech_authorization_lost", 401)
        pool = request.app.state.app_state.db_pool
        if pool is None:
            raise SpeechError("speech_authorization_unavailable", 503)
        authority = cls(pool, auth, hash_token(token))
        # No producer or successful response exists until this initial check passes.
        await authority.refresh()
        authority._monitor = asyncio.create_task(authority._watch())
        return authority

    def _fail(self, error: SpeechError) -> None:
        if self._error is None:
            self._error = error
            self._failed.set_result(error)
        self._ready.set()

    def _check(self) -> None:
        if self._error is not None:
            raise self._error
        if time.monotonic() >= self._expires:
            error = SpeechError("speech_authorization_lost", 401)
            self._fail(error)
            raise error

    async def checkpoint(self) -> None:
        self._check()
        await self._ready.wait()
        self._check()
        # Enforce freshness even when this task resumes before an overdue
        # background monitor after event-loop starvation.
        await self.refresh(only_if_due=True)
        self._check()

    async def wait_failure(self) -> None:
        # Shield the shared future: a cancelled HTTP waiter cannot disable the
        # monitor or accidentally mark authority successful.
        raise await asyncio.shield(self._failed)

    async def refresh(self, *, only_if_due: bool = False) -> None:
        async with self._refresh_lock:
            if self._error is not None:
                raise self._error
            if self._expires:
                self._check()
            if only_if_due and time.monotonic() < self._refresh_due:
                return
            old_expiry = self._expires
            budget = REFRESH_BUDGET_SECONDS
            if old_expiry:
                budget = min(budget, max(0.0, old_expiry - time.monotonic()))
            budget_deadline = time.monotonic() + budget
            self._ready.clear()
            try:
                async with asyncio.timeout(budget):
                    for attempt in range(2):
                        started = time.monotonic()
                        try:
                            async with self._pool.acquire() as connection:
                                row = await connection.fetchrow(
                                    _QUERY,
                                    self._token_hash,
                                    self._auth.user_id,
                                    self._auth.device_id,
                                    self._auth.session_id,
                                )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            if attempt:
                                raise SpeechError("speech_authorization_unavailable", 503) from None
                            continue
                        if row is None:
                            raise SpeechError("speech_authorization_lost", 401)
                        # Anchor at query START, not arrival. Round-trip latency
                        # subtracts validity instead of extending the DB lease.
                        remaining = (row["access_expires_at"] - row["checked_at"]).total_seconds()
                        # A delayed query must not revive a lease that expired
                        # while the loop could not deliver its timeout callback.
                        if old_expiry and time.monotonic() >= old_expiry:
                            raise SpeechError("speech_authorization_lost", 401)
                        if time.monotonic() >= budget_deadline:
                            raise SpeechError("speech_authorization_unavailable", 503)
                        self._expires = started + max(0.0, remaining)
                        self._check()
                        self._refresh_due = started + REFRESH_SECONDS
                        break
            except TimeoutError:
                error = (
                    SpeechError("speech_authorization_lost", 401)
                    if old_expiry and time.monotonic() >= old_expiry
                    else SpeechError("speech_authorization_unavailable", 503)
                )
                self._fail(error)
                raise error from None
            except SpeechError as error:
                self._fail(error)
                raise
            except Exception:
                error = SpeechError("speech_authorization_unavailable", 503)
                self._fail(error)
                raise error from None
            finally:
                self._ready.set()

    async def _watch(self) -> None:
        try:
            while True:
                await asyncio.sleep(
                    max(0.0, min(self._refresh_due, self._expires) - time.monotonic())
                )
                self._check()
                # Expiry must interrupt even an in-progress DB refresh. A blocked
                # authority read must never grant its full budget past expiry.
                async with asyncio.timeout_at(self._expires):
                    await self.refresh(only_if_due=True)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._fail(SpeechError("speech_authorization_lost", 401))
        except SpeechError as error:
            self._fail(error)
        except Exception:
            self._fail(SpeechError("speech_authorization_unavailable", 503))

    async def close(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            with suppress(asyncio.CancelledError):
                await self._monitor
