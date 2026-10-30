"""Uncalled current-connection qualification for the H1 SQL source candidate.

No pool, transaction, history lock, filesystem work or runtime admission is owned
here. Results are staged/observed metadata only, never native COMMIT acknowledgement,
fresh H durability or execution permission.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

from orchestrator.tasks.inference import (
    ROLE_PRECONDITION,
    acceptor_preflight,
    check_principal,
)

from .model import (
    MAX_RECORDS,
    AcceptPrepare,
    Command,
    Entry,
    HistoryError,
    Identity,
    decode_entry,
    encode_entry,
)
from .projection import ProjectionFact
from orchestrator.tasks._invocation import block

CATALOG = """SELECT
 $1::text<>$2::text
 AND (SELECT count(*)=2 AND bool_and(rolcanlogin AND NOT
 (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls))
 FROM pg_catalog.pg_roles WHERE rolname::text IN ($1,$2))
 AND NOT pg_catalog.pg_has_role($1::name,$2::name,'MEMBER')
 AND NOT pg_catalog.pg_has_role($2::name,$1::name,'MEMBER')
 AND (SELECT count(*)=2 AND count(DISTINCT c.relowner)=1 AND bool_and(
 c.relkind='r' AND c.relpersistence='p' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity)
 FROM pg_catalog.pg_class c WHERE c.oid IN
 ('public.task_history_binding'::regclass,'public.task_history_acceptance'::regclass))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
 WHERE c.oid IN ('public.task_history_binding'::regclass,'public.task_history_acceptance'::regclass)
 AND a.grantee<>c.relowner)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
 CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
 WHERE a.attrelid IN ('public.task_history_binding'::regclass,'public.task_history_acceptance'::regclass)
 AND x.grantee<>(SELECT relowner FROM pg_catalog.pg_class WHERE oid=a.attrelid))
 AND (SELECT count(*)=5 AND bool_and(p.proowner=(SELECT relowner FROM pg_catalog.pg_class
 WHERE oid='public.task_history_binding'::regclass) AND p.prokind='f'
 AND p.prosecdef=(p.proname<>'task_history_decode_acceptance')
 AND p.provolatile=CASE WHEN p.proname='task_history_decode_acceptance' THEN 'i'::"char" ELSE 'v'::"char" END
 AND p.proconfig @> ARRAY['search_path=pg_catalog']::text[]
 AND cardinality(p.proconfig)=1)
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname LIKE 'task_history_%')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname LIKE 'task_history_%' AND a.grantee<>p.proowner
 AND (a.is_grantable OR a.privilege_type<>'EXECUTE' OR NOT
 ((p.oid='public.task_history_acceptance_project(bytea)'::regprocedure AND a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname::text=$1))
 OR (p.oid='public.task_history_acceptance_page(uuid,bigint,text)'::regprocedure
 AND a.grantee IN (SELECT oid FROM pg_catalog.pg_roles WHERE rolname::text IN ($1,$2))))))
 AND pg_catalog.has_function_privilege($1::name,'public.task_history_acceptance_project(bytea)','EXECUTE')
 AND NOT pg_catalog.has_function_privilege($2::name,'public.task_history_acceptance_project(bytea)','EXECUTE')
 AND pg_catalog.has_function_privilege($1::name,'public.task_history_acceptance_page(uuid,bigint,text)','EXECUTE')
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_acceptance_page(uuid,bigint,text)','EXECUTE')
 AND (SELECT count(*)=22 AND bool_and(a.attnotnull AND NOT a.attisdropped
 AND a.attgenerated='' AND a.attidentity='') FROM pg_catalog.pg_attribute a
 WHERE a.attnum>0 AND a.attrelid IN
 ('public.task_history_binding'::regclass,'public.task_history_acceptance'::regclass))
 AND (SELECT jsonb_object_agg(a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod))
 FROM pg_catalog.pg_attribute a WHERE a.attnum>0 AND a.attrelid='public.task_history_binding'::regclass)
 = '{"singleton":"boolean","history_id":"uuid","version":"smallint","key_id":"smallint", "key_check":"text","request_schema":"text"}'::jsonb
 AND (SELECT jsonb_object_agg(a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod))
 FROM pg_catalog.pg_attribute a WHERE a.attnum>0 AND a.attrelid='public.task_history_acceptance'::regclass)
 = '{"history_id":"uuid","seq":"bigint","record_id":"uuid","digest":"bytea","frame":"bytea", "account_id":"uuid","account_generation":"bigint","task_id":"uuid", "conversation_id":"uuid","user_message_id":"uuid","result_message_id":"uuid", "commitment_version":"smallint","key_id":"smallint","key_commitment":"text", "request_commitment":"text","request_schema":"text"}'::jsonb
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c WHERE c.conrelid IN
 ('public.task_history_binding'::regclass,'public.task_history_acceptance'::regclass)
 AND (NOT c.convalidated OR c.condeferrable OR c.condeferred))
 AND (SELECT count(*)=2 AND count(DISTINCT (c.contype,c.conkey))=2 AND bool_and(
 (c.contype='p' AND c.conkey=ARRAY[1]::smallint[]) OR
 (c.contype='u' AND c.conkey=ARRAY[2]::smallint[]))
 FROM pg_catalog.pg_constraint c WHERE c.conrelid='public.task_history_binding'::regclass AND c.contype IN ('p','u'))
 AND (SELECT count(*)=4 AND count(DISTINCT (c.contype,c.conkey))=4 AND bool_and(
 (c.contype='p' AND c.conkey=ARRAY[1,2]::smallint[]) OR
 (c.contype='u' AND c.conkey IN (ARRAY[1,3]::smallint[],ARRAY[8]::smallint[],ARRAY[6,13,14]::smallint[])))
 FROM pg_catalog.pg_constraint c WHERE c.conrelid='public.task_history_acceptance'::regclass AND c.contype IN ('p','u'))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid IN
 ('public.task_history_binding'::regclass,'public.task_history_acceptance'::regclass)
 AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indimmediate OR i.indpred IS NOT NULL OR i.indexprs IS NOT NULL))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c WHERE c.contype='f'
 AND c.conrelid='public.task_history_binding'::regclass)
 AND (SELECT count(*)=1 AND bool_and(c.conkey=ARRAY[1]::smallint[] AND c.confkey=ARRAY[2]::smallint[]
 AND c.confrelid='public.task_history_binding'::regclass AND c.confdeltype='r' AND c.confupdtype='a')
 FROM pg_catalog.pg_constraint c WHERE c.contype='f' AND c.conrelid='public.task_history_acceptance'::regclass)
 AND (SELECT count(*)=4 AND bool_and(t.tgenabled='O' AND t.tgfoid='public.task_history_immutable()'::regprocedure
 AND t.tgtype IN (27,34)) FROM pg_catalog.pg_trigger t WHERE NOT t.tgisinternal AND t.tgrelid IN
 ('public.task_history_binding'::regclass,'public.task_history_acceptance'::regclass))
"""


@dataclass(frozen=True, slots=True)
class StagedAcceptance:
    fact: ProjectionFact
    inserted: bool


class AcceptanceProjection:
    """Only the supplied current connection is checked; no cached qualification.

    A successful qualification is not proof of H binding or readiness. Staging
    never commits, releases or returns an accepted-task ACK. Reads and duplicate
    rows are historical facts only. No connection/resource is retained.
    """

    def __init__(self, expected: Identity, *, acceptor: str, worker: str) -> None:
        if (
            type(expected) is not Identity
            or type(acceptor) is not str
            or type(worker) is not str
            or not acceptor
            or not worker
            or acceptor == worker
        ):
            raise HistoryError("projection_config")
        expected.wire()
        self.expected = expected
        self.acceptor = acceptor
        self.worker = worker

    async def qualify(self, conn: Any, *, writing: bool, deadline: float) -> None:
        if (
            type(writing) is not bool
            or type(deadline) not in (float, int)
            or not math.isfinite(deadline)
        ):
            raise HistoryError("projection_config")
        if asyncio.get_running_loop().time() >= deadline:
            raise HistoryError("projection_deadline")
        if writing and not conn.is_in_transaction():
            raise HistoryError("projection_transaction_required")
        try:
            async with asyncio.timeout_at(deadline):
                if writing:
                    await acceptor_preflight(conn, expected=self.acceptor, other=self.worker)
                else:
                    # A and W use their incompatible native qualifications.
                    role = await conn.fetchval("SELECT CURRENT_USER::text")
                    if role == self.acceptor:
                        await acceptor_preflight(conn, expected=self.acceptor, other=self.worker)
                    elif role == self.worker:
                        await check_principal(conn, self.worker, self.acceptor)
                        if await conn.fetchval(ROLE_PRECONDITION) is not True:
                            raise HistoryError("projection_principal_refused")
                    else:
                        raise HistoryError("projection_principal_refused")
                if await conn.fetchval(CATALOG, self.acceptor, self.worker) is not True:
                    raise HistoryError("projection_catalog_refused")
            if asyncio.get_running_loop().time() >= deadline:
                raise HistoryError("projection_deadline")
        except HistoryError:
            raise
        except Exception:
            raise HistoryError("projection_unavailable", uncertain=True) from None
        except BaseException as exc:
            exc.add_note("H1 SQL qualification interrupted; caller retains transaction ownership")
            raise

    @staticmethod
    def _time(deadline: float) -> None:
        if type(deadline) not in (float, int) or not math.isfinite(deadline):
            raise HistoryError("projection_config")
        if asyncio.get_running_loop().time() >= deadline:
            raise HistoryError("projection_deadline")

    @staticmethod
    def _transaction(conn: Any) -> None:
        if not conn.is_in_transaction():
            raise HistoryError("projection_transaction_required")

    def _fact(self, row: Any) -> ProjectionFact:
        try:
            if set(row.keys()) != {"history_id", "seq", "record_id", "digest", "frame"}:
                raise HistoryError("projection_result_unavailable")
            frame = row["frame"]
            if type(frame) is not bytes or type(row["digest"]) is not bytes:
                raise HistoryError("projection_result_unavailable")
            entry = decode_entry(frame)
            if (
                type(row["seq"]) is not int
                or not 1 <= row["seq"] <= MAX_RECORDS
                or not isinstance(entry.command.data, AcceptPrepare)
                or entry.history_id != self.expected.history_id
                or (row["history_id"], row["seq"], row["record_id"], row["digest"])
                != (entry.history_id, entry.seq, entry.command.record_id, entry.digest)
            ):
                raise HistoryError("projection_result_unavailable")
            return ProjectionFact(
                entry.history_id, entry.seq, entry.command.record_id, entry.digest, frame
            )
        except (KeyError, TypeError, AttributeError):
            raise HistoryError("projection_result_unavailable") from None

    async def _page(self, conn: Any, after_seq: int, deadline: float) -> tuple[ProjectionFact, ...]:
        self._time(deadline)
        rows = await conn.fetch(
            "SELECT * FROM public.task_history_acceptance_page($1,$2,$3)",
            self.expected.history_id,
            after_seq,
            self.expected.key_check,
        )
        self._time(deadline)
        if len(rows) > 32:
            raise HistoryError("projection_result_unavailable")
        facts = tuple(self._fact(row) for row in rows)
        cursor = after_seq
        for fact in facts:
            if fact.seq <= cursor:
                raise HistoryError("projection_result_unavailable")
            cursor = fact.seq
        return facts

    async def _limits(self, conn: Any, deadline: float) -> None:
        self._time(deadline)
        rows = await conn.fetch(
            "SELECT name,setting::bigint AS milliseconds FROM pg_catalog.pg_settings "
            "WHERE name IN ('lock_timeout','statement_timeout')"
        )
        self._time(deadline)
        limits = {row["name"]: row["milliseconds"] for row in rows}
        if set(limits) != {"lock_timeout", "statement_timeout"} or any(
            type(value) is not int or value < 0 for value in limits.values()
        ):
            raise HistoryError("projection_config")
        lock = min(limits["lock_timeout"] or 250, 250)
        statement = min(limits["statement_timeout"] or 1000, 1000)
        await conn.fetchval(
            "SELECT pg_catalog.set_config('lock_timeout',$1,true) || "
            "pg_catalog.set_config('statement_timeout',$2,true)",
            str(lock) + "ms",
            str(statement) + "ms",
        )
        self._time(deadline)

    async def page(
        self, conn: Any, *, after_seq: int, deadline: float
    ) -> tuple[ProjectionFact, ...]:
        """Caller-owned transaction. No scan/cursor implies admission or parity."""
        self._time(deadline)
        self._transaction(conn)
        if type(after_seq) is not int or not 0 <= after_seq <= MAX_RECORDS:
            raise HistoryError("projection_cursor")
        try:
            async with asyncio.timeout_at(deadline):
                await self._limits(conn, deadline)
                await self.qualify(conn, writing=False, deadline=deadline)
                facts = await self._page(conn, after_seq, deadline)
            self._time(deadline)
            return facts
        except HistoryError:
            raise
        except Exception:
            raise HistoryError("projection_unavailable", uncertain=True) from None
        except BaseException as exc:
            exc.add_note("H1 SQL read interrupted; caller retains transaction ownership")
            raise

    async def stage(self, conn: Any, entry: Entry, *, deadline: float) -> StagedAcceptance:
        """Stage only on caller's original transaction; never commit or retry.

        A rejected/cancelled operation requires caller rollback and ownership
        reconciliation. This method cannot attest a fresh H append or origin.
        """
        self._time(deadline)
        self._transaction(conn)
        if (
            type(entry) is not Entry
            or type(entry.command) is not Command
            or type(entry.command.data) is not AcceptPrepare
        ):
            raise HistoryError("invalid_projection")
        frame = encode_entry(entry.history_id, entry.seq, entry.prev_digest, entry.command)
        if (
            decode_entry(frame) != entry
            or entry.history_id != self.expected.history_id
            or not 1 <= entry.seq <= MAX_RECORDS
        ):
            raise HistoryError("invalid_projection")
        attempted = False
        try:
            async with asyncio.timeout_at(deadline):
                await self._limits(conn, deadline)
                await self.qualify(conn, writing=True, deadline=deadline)
                # Terminal cursor validates the entire binding even for an empty
                # table, without reading additional rows or receiving table ACLs.
                if await self._page(conn, MAX_RECORDS, deadline):
                    raise HistoryError("projection_result_unavailable")
                self._time(deadline)
                attempted = True
                row = await conn.fetchrow(
                    "SELECT * FROM public.task_history_acceptance_project($1)", frame
                )
                self._time(deadline)
                if row is None or set(row.keys()) != {
                    "history_id",
                    "seq",
                    "record_id",
                    "digest",
                    "frame",
                    "created",
                }:
                    raise HistoryError("projection_result_unavailable")
                if type(row["created"]) is not bool:
                    raise HistoryError("projection_result_unavailable")
                fact = self._fact({k: row[k] for k in row.keys() if k != "created"})
                if fact.frame != frame:
                    raise HistoryError("projection_result_unavailable")
                result = StagedAcceptance(fact, row["created"])
            self._time(deadline)
            return result
        except BaseException as exc:
            if attempted:
                block("history_projection_unavailable")
                if isinstance(exc, Exception):
                    raise HistoryError("projection_unavailable", uncertain=True) from None
                exc.add_note(
                    "H1 SQL projection outcome uncertain; caller retains transaction ownership"
                )
            elif isinstance(exc, Exception) and not isinstance(exc, HistoryError):
                raise HistoryError("projection_unavailable", uncertain=True) from None
            raise
