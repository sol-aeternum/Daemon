"""Unselected-host native fence (#731): serialize H binding against native work.

Owner-approved protocol, one advisory key K shared with migration 073:

* L1 ``NativeFence.open``: a dedicated, unpooled presence connection takes K
  shared (session scope) and only then, in a separate statement, proves the H
  schema absent or unbound. It is held for the process lifetime. Losing it
  marks the fence dead and invokes ``on_lost`` (production: exit the process).
* L2 ``NativeFence.guard``: the asyncpg ``setup`` hook of every unselected
  pool. On each acquire it requires L1 alive, takes K shared on that
  connection, then re-proves the predicate. asyncpg's release reset runs
  ``pg_advisory_unlock_all()``, and a failing ``setup`` closes the connection,
  so the lock covers exactly one borrowed connection.
* L3 (migration 073): inserting a binding takes K exclusively for its
  transaction and refuses (``history_native_hosts_active``) while any L1/L2
  holder exists; afterwards every new L1/L2 sees the binding and refuses.

Every acquisition is a non-blocking try, so nothing waits or deadlocks.
Session-mode connections are required (no transaction-mode pooler). Residual,
documented risk: an external effect already started by a live process whose
every session was cut (partition, privileged termination), and owner or
superuser DDL removing 073. Neither is claimed as enforced.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

import asyncpg

from .model import HistoryError
from .runtime import history_unselected_compatible

logger = logging.getLogger(__name__)

# Two-int key: a separate lock space from the migration runner's bigint key.
FENCE_KEY = (1145588051, 1)
_TRY_SHARED = "SELECT pg_catalog.pg_try_advisory_lock_shared($1::int4,$2::int4)"
FENCE_EXIT_CODE = 70
# Bound how long a dead client can keep the binder refused over TCP.
_PRESENCE_SETTINGS = {
    "application_name": "daemon-native-fence",
    "tcp_keepalives_idle": "30",
    "tcp_keepalives_interval": "10",
    "tcp_keepalives_count": "3",
}


def exit_process() -> None:
    """Production ``on_lost``: no native work may outlive the presence session."""
    logger.critical("native fence presence lost; exiting")
    os._exit(FENCE_EXIT_CODE)


async def _qualify(conn: asyncpg.Connection) -> None:
    # Two statements: the predicate's snapshot must postdate the lock.
    if await conn.fetchval(_TRY_SHARED, *FENCE_KEY) is not True:
        raise HistoryError("history_fence_unavailable")
    if not await history_unselected_compatible(conn):
        raise HistoryError("history_configuration_required")


class NativeFence:
    """Process-lifetime L1 presence plus the per-acquire L2 pool guard."""

    def __init__(self, on_lost: Callable[[], None] = exit_process) -> None:
        self._on_lost = on_lost
        self._conn: asyncpg.Connection | None = None
        self._open = False
        self._closing = False

    @property
    def alive(self) -> bool:
        return self._open and self._conn is not None and not self._conn.is_closed()

    async def open(self, dsn: str, *, timeout: int = 10) -> None:
        if self._conn is not None:
            raise HistoryError("history_fence_unavailable")
        conn = self._conn = await asyncpg.connect(
            dsn, timeout=timeout, server_settings=dict(_PRESENCE_SETTINGS)
        )
        try:
            conn.add_termination_listener(self._terminated)
            await _qualify(conn)
        except BaseException:
            await self.close()
            raise
        self._open = True

    def _terminated(self, _conn: object) -> None:
        was_open, self._open = self._open, False
        if was_open and not self._closing:
            self._on_lost()

    async def guard(self, conn: asyncpg.Connection) -> None:
        """asyncpg pool ``setup``: refuse the acquire unless still unbound."""
        if not self.alive:
            raise HistoryError("history_fence_lost")
        await _qualify(conn)

    async def close(self) -> None:
        self._closing, self._open = True, False
        conn, self._conn = self._conn, None
        if conn is not None and not conn.is_closed():
            await conn.close()
