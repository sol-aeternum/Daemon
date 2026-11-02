"""057 projection profile; closed H1 fields, no execution permit.

The legacy 056 profile stays strict. This explicit profile reads acceptance,
send and Stop together and retains A-only acceptance / W-only effects writes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .acceptance import AcceptanceProjection, CATALOG, StagedAcceptance
from .model import (
    AcceptPrepare,
    Command,
    Entry,
    HistoryError,
    SendPossible,
    StopIntent,
    decode_entry,
    encode_entry,
)


def _replace(query: str, old: str, new: str) -> str:
    if query.count(old) != 1:
        raise RuntimeError("history catalog source changed")
    return query.replace(old, new)


EFFECT_CATALOG = _replace(
    CATALOG, "count(*)=5 AND bool_and(p.proowner", "count(*)=10 AND bool_and(p.proowner"
)
EFFECT_CATALOG = _replace(
    EFFECT_CATALOG,
    "p.prosecdef=(p.proname<>'task_history_decode_acceptance')",
    "p.prosecdef=(p.proname NOT IN ('task_history_decode_acceptance','task_history_decode_effect'))",
)
EFFECT_CATALOG = _replace(
    EFFECT_CATALOG,
    "CASE WHEN p.proname='task_history_decode_acceptance' THEN",
    "CASE WHEN p.proname IN ('task_history_decode_acceptance','task_history_decode_effect') THEN",
)
EFFECT_CATALOG = _replace(
    EFFECT_CATALOG,
    "OR (p.oid='public.task_history_acceptance_page(uuid,bigint,text)'::regprocedure",
    "OR (p.oid IN ('public.task_history_send_project(bytea)'::regprocedure,'public.task_history_stop_project(bytea)'::regprocedure) "
    "AND a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname::text=$2)) "
    "OR (p.oid IN ('public.task_history_acceptance_page(uuid,bigint,text)'::regprocedure,'public.task_history_page(uuid,bigint,text)'::regprocedure)",
)
EFFECT_CATALOG += """
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_send_project(bytea)','EXECUTE')
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_stop_project(bytea)','EXECUTE')
 AND NOT pg_catalog.has_function_privilege($1::name,'public.task_history_send_project(bytea)','EXECUTE')
 AND NOT pg_catalog.has_function_privilege($1::name,'public.task_history_stop_project(bytea)','EXECUTE')
 AND pg_catalog.has_function_privilege($1::name,'public.task_history_page(uuid,bigint,text)','EXECUTE')
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_page(uuid,bigint,text)','EXECUTE')
 AND (SELECT c.relkind='r' AND c.relpersistence='p' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
 AND c.relowner=(SELECT relowner FROM pg_catalog.pg_class WHERE oid='public.task_history_binding'::regclass)
 FROM pg_catalog.pg_class c WHERE c.oid='public.task_history_projection'::regclass)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
 WHERE c.oid='public.task_history_projection'::regclass AND a.grantee<>c.relowner)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
 WHERE a.attrelid='public.task_history_projection'::regclass AND x.grantee<>(SELECT relowner FROM pg_catalog.pg_class WHERE oid=a.attrelid))
 AND (SELECT count(*)=9 AND bool_and(a.attnotnull AND NOT a.attisdropped AND a.attgenerated='' AND a.attidentity='')
 FROM pg_catalog.pg_attribute a WHERE a.attnum>0 AND a.attrelid='public.task_history_projection'::regclass)
 AND (SELECT jsonb_object_agg(a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod)) FROM pg_catalog.pg_attribute a
 WHERE a.attnum>0 AND a.attrelid='public.task_history_projection'::regclass)
 = '{"history_id":"uuid","seq":"bigint","record_id":"uuid","digest":"bytea","frame":"bytea", "kind":"text","account_id":"uuid","account_generation":"bigint","task_id":"uuid"}'::jsonb
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c WHERE c.conrelid='public.task_history_projection'::regclass
 AND (NOT c.convalidated OR c.condeferrable OR c.condeferred))
 AND (SELECT count(*)=2 AND count(DISTINCT(c.contype,c.conkey))=2 AND bool_and(
 (c.contype='p' AND c.conkey=ARRAY[1,2]::smallint[]) OR (c.contype='u' AND c.conkey=ARRAY[1,3]::smallint[]))
 FROM pg_catalog.pg_constraint c WHERE c.conrelid='public.task_history_projection'::regclass AND c.contype IN ('p','u'))
 AND (SELECT count(*)=1 AND bool_and(c.conkey=ARRAY[1]::smallint[] AND c.confkey=ARRAY[2]::smallint[]
 AND c.confrelid='public.task_history_binding'::regclass AND c.confdeltype='r' AND c.confupdtype='a')
 FROM pg_catalog.pg_constraint c WHERE c.conrelid='public.task_history_projection'::regclass AND c.contype='f')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid='public.task_history_projection'::regclass
 AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indimmediate OR i.indpred IS NOT NULL OR i.indexprs IS NOT NULL))
 AND (SELECT count(*)=2 AND bool_and(t.tgenabled='O' AND t.tgfoid='public.task_history_immutable()'::regprocedure
 AND t.tgtype IN (27,34)) FROM pg_catalog.pg_trigger t WHERE NOT t.tgisinternal AND t.tgrelid='public.task_history_projection'::regclass)
"""


class HistoryProjection(AcceptanceProjection):
    _catalog = EFFECT_CATALOG
    _kinds = (AcceptPrepare, SendPossible, StopIntent)
    _reader = "SELECT * FROM public.task_history_page($1,$2,$3)"

    async def stage_effect(self, conn: Any, entry: Entry, *, deadline: float) -> StagedAcceptance:
        """Exact caller transaction only. Historical rows are never fresh receipts."""
        self._time(deadline)
        self._transaction(conn)
        if (
            type(entry) is not Entry
            or type(entry.command) is not Command
            or type(entry.command.data) not in (SendPossible, StopIntent)
        ):
            raise HistoryError("invalid_projection")
        if entry.history_id != self.expected.history_id:
            raise HistoryError("invalid_projection")
        frame = encode_entry(entry.history_id, entry.seq, entry.prev_digest, entry.command)
        if decode_entry(frame) != entry:
            raise HistoryError("invalid_projection")
        query = (
            "SELECT * FROM public.task_history_send_project($1)"
            if type(entry.command.data) is SendPossible
            else "SELECT * FROM public.task_history_stop_project($1)"
        )
        try:
            async with asyncio.timeout_at(deadline):
                await self._limits(conn, deadline)
                await self.qualify(conn, writing=False, deadline=deadline)
                if await conn.fetchval("SELECT CURRENT_USER::text") != self.worker:
                    raise HistoryError("projection_principal_refused")
                if await self._page(conn, 16384, deadline):
                    raise HistoryError("projection_result_unavailable")
                row = await conn.fetchrow(query, frame)
                self._time(deadline)
                if (
                    row is None
                    or set(row.keys())
                    != {"history_id", "seq", "record_id", "digest", "frame", "created"}
                    or type(row["created"]) is not bool
                ):
                    raise HistoryError("projection_result_unavailable")
                fact = self._fact(
                    {k: row[k] for k in ("history_id", "seq", "record_id", "digest", "frame")}
                )
                if fact.frame != frame:
                    raise HistoryError("projection_result_unavailable")
                return StagedAcceptance(fact, row["created"])
        except HistoryError:
            raise
        except Exception:
            raise HistoryError("projection_unavailable", uncertain=True) from None
