"""Approved service selection; no connections, journal initialization or import I/O."""

from __future__ import annotations

import re
from typing import Any, Literal

from orchestrator.config import Settings
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.services.deletion_journal.barrier import configured_journal

_ROLE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")


def task_startup_selected(settings: Settings, service: Literal["http", "worker"]) -> bool:
    """Partial/missing/foreign-role configuration cannot opt into legacy execution."""
    acceptor = settings.daemon_task_acceptor_role
    worker = settings.daemon_task_worker_role
    credential = settings.daemon_task_acceptor_database_url
    if service == "worker" and credential:
        raise ObservationBlocked("inference_worker_acceptor_credentials")
    selected = bool(acceptor or worker or credential)
    if not selected:
        if settings.durable_chat_enabled:
            raise ObservationBlocked("inference_configuration_required")
        return False
    if (
        not isinstance(acceptor, str)
        or not isinstance(worker, str)
        or _ROLE.fullmatch(acceptor) is None
        or _ROLE.fullmatch(worker) is None
        or acceptor == worker
        or (service == "http" and not credential)
    ):
        raise ObservationBlocked("inference_configuration_required")
    if configured_journal(settings) is None:
        raise ObservationBlocked("inference_journal_required")
    return True


async def require_legacy_task_schema(pool: Any) -> None:
    """Removing configuration cannot downgrade a migrated host after flag-off.

    Positive catalog evidence only. No account/task rows are read or changed.
    A host with 050 must retain qualified control/recovery even with no NEW work.
    """
    if (
        await pool.fetchval(
            "SELECT pg_catalog.to_regclass('public.task_inference_recovery') IS NULL"
        )
        is not True
    ):
        raise ObservationBlocked("inference_configuration_required")
