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
    InboxOriginalRetired,
    NonsuccessOutputPrepare,
    Profile,
    TerminalOutputPrepare,
    decode_entry,
    encode_entry,
    validate_chain,
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


INBOX_RETIRED_CATALOG = _replace(
    TERMINAL_CATALOG,
    "count(*)=16 AND bool_and(p.proowner",
    "count(*)=19 AND bool_and(p.proowner",
).replace(
    "'task_history_decode_terminal'",
    "'task_history_decode_terminal','task_history_decode_retired'",
)
INBOX_RETIRED_CATALOG = _replace(
    INBOX_RETIRED_CATALOG,
    "'public.task_history_terminal_project(bytea)'::regprocedure)",
    "'public.task_history_terminal_project(bytea)'::regprocedure,'public.task_history_retired_project(bytea)'::regprocedure)",
)
INBOX_RETIRED_CATALOG = _replace(
    INBOX_RETIRED_CATALOG,
    "'public.task_history_terminal_page(uuid,bigint,text)'::regprocedure)",
    "'public.task_history_terminal_page(uuid,bigint,text)'::regprocedure,'public.task_history_retired_page(uuid,bigint,text)'::regprocedure)",
)
INBOX_RETIRED_CATALOG += """
 AND pg_catalog.to_regprocedure('public.task_history_decode_retired(bytea)') IS NOT NULL
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_retired_project(bytea)','EXECUTE')
 AND NOT pg_catalog.has_function_privilege($1::name,'public.task_history_retired_project(bytea)','EXECUTE')
 AND pg_catalog.has_function_privilege($1::name,'public.task_history_retired_page(uuid,bigint,text)','EXECUTE')
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_retired_page(uuid,bigint,text)','EXECUTE')
 AND (SELECT pg_catalog.pg_get_constraintdef(c.oid) =
  'CHECK ((kind = ANY (ARRAY[''ACCEPT_PREPARE''::text, ''SEND_POSSIBLE''::text, ''STOP_INTENT''::text, ''ARTIFACT_PREPARE''::text, ''TERMINAL_OUTPUT_PREPARE''::text, ''INBOX_ORIGINAL_RETIRED''::text])))'
  FROM pg_catalog.pg_constraint c WHERE c.conrelid='public.task_history_projection'::regclass
  AND c.conname='task_history_projection_kind_check' AND c.contype='c')
"""
# Pin the complete fixture history API, including the legacy helpers invoked
# by acceptance, Send, completion and projection reads. These are hashes of
# the exact SQL bodies in 056/057/064/065 and inbox_retired_v1.sql, not values
# learned from the connected database.
INBOX_RETIRED_CATALOG += """
 AND (SELECT count(*)=19 AND bool_and(
  pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')=expected.sha
  AND p.prolang=(SELECT oid FROM pg_catalog.pg_language WHERE lanname='plpgsql')
  AND NOT p.proisstrict AND NOT p.proleakproof AND p.proparallel='u'
  AND p.pronargdefaults=0 AND p.provariadic=0)
  FROM (VALUES
   ('public.task_history_acceptance_page(uuid,bigint,text)','2672c608f88a1b38b01bca230d129ff733637470818f684f4b63935e5d9b7043'),
   ('public.task_history_acceptance_project(bytea)','b7c9cdc777bb83cd8aeda25125abbdb157785a254a4f3f899acbb7895e8ff1ac'),
   ('public.task_history_artifact_project(bytea)','3b1879a9bf1ba5a99772fa873a1876682d7bf345b0992e295c80fc4dbbd73074'),
   ('public.task_history_check_caller(boolean)','ffc19daec142dc6a5d3b8eb3b5a55fd33cbf39314e38f5d8cd91d037308fd5d9'),
   ('public.task_history_decode_acceptance(bytea)','f1251134f66530e627d121f11f95aef8e787cd0e10a642b0d6a3f19ac186c84f'),
   ('public.task_history_decode_artifact(bytea)','cf0855f3b73627f2b5a7365952a050933b3573bd846b0bc09d9e9803d0c34dc0'),
   ('public.task_history_decode_effect(bytea)','9437102792c2ae1c41c636560068c6eea2bd5f510d884c04ff5be1933bfaf1e1'),
   ('public.task_history_decode_retired(bytea)','b325f3780ba4c28a8a6e54ccc6d6631e2486e47cc8489ab5f0386345273b233b'),
   ('public.task_history_decode_terminal(bytea)','513a3f56835c3a725132c3dcaa419c8647e7182e6d9bde2591061c80414819c5'),
   ('public.task_history_immutable()','afff3cc45846f7411c31e5fdf9709c40d4576f1f33d99bed657f5b07ac35a5c1'),
   ('public.task_history_output_page(uuid,bigint,text)','c998c3980915a226230767021a589fc0c4277062befab864d89f50ad13f6b72e'),
   ('public.task_history_page(uuid,bigint,text)','6d31a1bf503ca3d764f896a5d77c3a15d6cde16577348352eca1542ce14684d0'),
   ('public.task_history_project_frame(bytea,boolean)','039cc985699f366e5f7600b6be00c91d13fec0e550b7ea62aa7de730f2026479'),
   ('public.task_history_retired_page(uuid,bigint,text)','9a8a58f27e98ee76c540066d2a5a3b5dde0e830ac8d6a8e8393b1d119995bca9'),
   ('public.task_history_retired_project(bytea)','fff9cb9bcd9ee44bcce1d647c289332236ad86df29f66542d74f268c2ce1f4b7'),
   ('public.task_history_send_project(bytea)','e06ce6a715602633a76f6c16ec9aa572e98ee7193b6cee2bbf088c885deeb30a'),
   ('public.task_history_stop_project(bytea)','e19fd4ed88f9509d9e6316de6f969852b05b1d42a3004dba8a4ec8c465374145'),
   ('public.task_history_terminal_page(uuid,bigint,text)','091393ff0c3e691a29ae23e335c20b217b8087d59c64127601fbfb6f5c14e2b8'),
   ('public.task_history_terminal_project(bytea)','c35be9008df1bf7f333457b61ac63a1e3fe9060693ce78b8b09001957d09564f')
  ) AS expected(signature,sha)
  JOIN pg_catalog.pg_proc p ON p.oid=pg_catalog.to_regprocedure(expected.signature))
"""


class InboxRetiredHistoryProjection(TerminalHistoryProjection):
    """Explicit fixture-only success profile; staging is never a retirement ACK."""

    _catalog = INBOX_RETIRED_CATALOG
    _reader = "SELECT * FROM public.task_history_retired_page($1,$2,$3)"
    _profile: Profile = "inbox-retired-v1"
    _kinds = (*TerminalHistoryProjection._kinds, InboxOriginalRetired)

    async def stage_retired(self, conn: Any, entry: Entry, *, deadline: float) -> StagedAcceptance:
        self._time(deadline)
        self._transaction(conn)
        if (
            type(entry) is not Entry
            or type(entry.command) is not Command
            or type(entry.command.data) is not InboxOriginalRetired
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
                await self._limits(conn, deadline)
                await self.qualify(conn, writing=False, deadline=deadline)
                if await conn.fetchval("SELECT CURRENT_USER::text") != self.worker:
                    raise HistoryError("projection_principal_refused")
                # Retirement is the sole fourth record, following one accepted
                # original, one completion Send and its terminal preparation.
                facts = await self._page(conn, 0, deadline)
                validate_chain(
                    self.expected.history_id,
                    (*[decode_entry(fact.frame, profile=self.profile) for fact in facts], entry),
                    profile=self.profile,
                )
                row = await conn.fetchrow(
                    "SELECT * FROM public.task_history_retired_project($1)", frame
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


TERMINAL_PROFILES: dict[type[AcceptanceProjection], Profile] = {
    TerminalHistoryProjection: "terminal-v3",
    TerminalV4HistoryProjection: "terminal-v4",
    InboxRetiredHistoryProjection: "inbox-retired-v1",
}
