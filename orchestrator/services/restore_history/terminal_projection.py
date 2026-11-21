"""Explicit isolated terminal projections. No replay or dispatch authority."""

from __future__ import annotations

import asyncio
from typing import Any

from .acceptance import AcceptanceProjection, StagedAcceptance
from .artifact_projection import ARTIFACT_CATALOG, ArtifactHistoryProjection
from .effects_projection import _replace
from .model import (
    Command,
    Entry,
    HistoryError,
    NonsuccessOutputPrepare,
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
    _terminal_kinds: tuple[type[Any], ...] = (TerminalOutputPrepare,)

    async def stage_terminal(self, conn: Any, entry: Entry, *, deadline: float) -> StagedAcceptance:
        self._time(deadline)
        self._transaction(conn)
        if (
            type(entry) is not Entry
            or type(entry.command) is not Command
            or type(entry.command.data) not in self._terminal_kinds
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


TERMINAL_V4_CATALOG = _replace(
    TERMINAL_CATALOG,
    "count(*)=16 AND bool_and(p.proowner",
    "count(*)=18 AND bool_and(p.proowner",
).replace(
    "'task_history_decode_terminal'",
    "'task_history_decode_terminal','task_history_decode_nonsuccess','task_history_valid_turn_finish'",
)
TERMINAL_V4_CATALOG += """
 AND pg_catalog.to_regprocedure('public.task_history_decode_nonsuccess(bytea)') IS NOT NULL
 AND pg_catalog.to_regprocedure('public.task_history_valid_turn_finish(jsonb)') IS NOT NULL
 AND (SELECT pg_catalog.pg_get_constraintdef(c.oid) =
  'CHECK ((kind = ANY (ARRAY[''ACCEPT_PREPARE''::text, ''SEND_POSSIBLE''::text, ''STOP_INTENT''::text, ''ARTIFACT_PREPARE''::text, ''TERMINAL_OUTPUT_PREPARE''::text, ''NONSUCCESS_OUTPUT_PREPARE''::text])))'
  FROM pg_catalog.pg_constraint c WHERE c.conrelid='public.task_history_projection'::regclass
  AND c.conname='task_history_projection_kind_check' AND c.contype='c')
"""


class TerminalV4HistoryProjection(TerminalHistoryProjection):
    """Opt-in exact v4 schema, reusing the existing direct A/W SQL ACLs."""

    _catalog = TERMINAL_V4_CATALOG
    _profile: Profile = "terminal-v4"
    _terminal_kinds = (TerminalOutputPrepare, NonsuccessOutputPrepare)
    _kinds = (*TerminalHistoryProjection._kinds, NonsuccessOutputPrepare)


TERMINAL_PROFILES: dict[type[AcceptanceProjection], Profile] = {
    TerminalHistoryProjection: "terminal-v3",
    TerminalV4HistoryProjection: "terminal-v4",
}
