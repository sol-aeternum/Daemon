"""Explicit, uninstalled completed-text projection. No replay or dispatch authority."""

from __future__ import annotations

import asyncio
from typing import Any

from .acceptance import StagedAcceptance
from .artifact_projection import ARTIFACT_CATALOG, ArtifactHistoryProjection
from .effects_projection import _replace
from .model import (
    Command,
    Entry,
    HistoryError,
    Profile,
    TerminalOutputPrepare,
    decode_entry,
    encode_entry,
)

TERMINAL_CATALOG = _replace(
    ARTIFACT_CATALOG, "count(*)=13 AND bool_and(p.proowner", "count(*)=16 AND bool_and(p.proowner"
)
TERMINAL_CATALOG = TERMINAL_CATALOG.replace(
    "'task_history_decode_artifact'",
    "'task_history_decode_artifact','task_history_decode_terminal'",
)
TERMINAL_CATALOG = _replace(
    TERMINAL_CATALOG,
    "'public.task_history_stop_project(bytea)'::regprocedure)",
    "'public.task_history_stop_project(bytea)'::regprocedure,'public.task_history_terminal_project(bytea)'::regprocedure)",
)
TERMINAL_CATALOG = _replace(
    TERMINAL_CATALOG,
    "'public.task_history_output_page(uuid,bigint,text)'::regprocedure)",
    "'public.task_history_output_page(uuid,bigint,text)'::regprocedure,'public.task_history_terminal_page(uuid,bigint,text)'::regprocedure)",
)
TERMINAL_CATALOG += """
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_terminal_project(bytea)','EXECUTE')
 AND NOT pg_catalog.has_function_privilege($1::name,'public.task_history_terminal_project(bytea)','EXECUTE')
 AND pg_catalog.has_function_privilege($1::name,'public.task_history_terminal_page(uuid,bigint,text)','EXECUTE')
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_terminal_page(uuid,bigint,text)','EXECUTE')
"""


class TerminalHistoryProjection(ArtifactHistoryProjection):
    _catalog = TERMINAL_CATALOG
    _reader = "SELECT * FROM public.task_history_terminal_page($1,$2,$3)"
    _profile: Profile = "terminal-v3"
    _kinds = (*ArtifactHistoryProjection._kinds, TerminalOutputPrepare)

    async def stage_terminal(self, conn: Any, entry: Entry, *, deadline: float) -> StagedAcceptance:
        self._time(deadline)
        self._transaction(conn)
        if (
            type(entry) is not Entry
            or type(entry.command) is not Command
            or type(entry.command.data) is not TerminalOutputPrepare
            or entry.history_id != self.expected.history_id
        ):
            raise HistoryError("invalid_projection")
        frame = encode_entry(
            entry.history_id, entry.seq, entry.prev_digest, entry.command, profile=self.profile
        )
        if decode_entry(frame, profile=self.profile) != entry:
            raise HistoryError("invalid_projection")
        try:
            async with asyncio.timeout_at(deadline):
                await self.qualify(conn, writing=False, deadline=deadline)
                if await conn.fetchval("SELECT CURRENT_USER::text") != self.worker:
                    raise HistoryError("projection_principal_refused")
                row = await conn.fetchrow(
                    "SELECT * FROM public.task_history_terminal_project($1)", frame
                )
                self._time(deadline)
                if (
                    row is None
                    or set(row.keys())
                    != {"history_id", "seq", "record_id", "digest", "frame", "created"}
                    or row["created"] is not True
                ):
                    raise HistoryError("projection_result_unavailable")
                fact = self._fact(
                    {k: row[k] for k in ("history_id", "seq", "record_id", "digest", "frame")}
                )
                if fact.frame != frame:
                    raise HistoryError("projection_result_unavailable")
                return StagedAcceptance(fact, True)
        except HistoryError:
            raise
        except Exception:
            raise HistoryError("projection_unavailable", uncertain=True) from None
