"""Explicit uninstalled v2 catalog projection; staged facts are not permits."""

from __future__ import annotations

import asyncio
from typing import Any

from .acceptance import StagedAcceptance
from .effects_projection import EFFECT_CATALOG, HistoryProjection, _replace
from .model import (
    ArtifactPrepare,
    Command,
    Entry,
    HistoryError,
    Identity,
    Profile,
    encode_entry,
    decode_entry,
)


ARTIFACT_CATALOG = _replace(
    EFFECT_CATALOG, "count(*)=10 AND bool_and(p.proowner", "count(*)=13 AND bool_and(p.proowner"
)
ARTIFACT_CATALOG = ARTIFACT_CATALOG.replace(
    "'task_history_decode_acceptance','task_history_decode_effect'",
    "'task_history_decode_acceptance','task_history_decode_effect','task_history_decode_artifact'",
)
ARTIFACT_CATALOG = _replace(
    ARTIFACT_CATALOG,
    "OR (p.oid IN ('public.task_history_acceptance_page(uuid,bigint,text)'::regprocedure,'public.task_history_page(uuid,bigint,text)'::regprocedure)",
    "OR (p.oid='public.task_history_artifact_project(bytea)'::regprocedure AND a.grantee=(SELECT x.grantee FROM pg_catalog.pg_proc f CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(f.proacl,pg_catalog.acldefault('f',f.proowner))) x WHERE f.oid='public.resource_output_catalog_snapshot(jsonb,uuid)'::regprocedure AND x.grantee<>f.proowner)) OR (p.oid IN ('public.task_history_acceptance_page(uuid,bigint,text)'::regprocedure,'public.task_history_page(uuid,bigint,text)'::regprocedure,'public.task_history_output_page(uuid,bigint,text)'::regprocedure)",
)
ARTIFACT_CATALOG += """
 AND pg_catalog.has_function_privilege($1::name,'public.task_history_output_page(uuid,bigint,text)','EXECUTE')
 AND pg_catalog.has_function_privilege($2::name,'public.task_history_output_page(uuid,bigint,text)','EXECUTE')
 AND NOT pg_catalog.has_function_privilege($1::name,'public.task_history_artifact_project(bytea)','EXECUTE')
 AND NOT pg_catalog.has_function_privilege($2::name,'public.task_history_artifact_project(bytea)','EXECUTE')
"""

PUBLISHER_CATALOG = """SELECT $1::text<>$2::text AND $1::text<>$3::text
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname::text=$1 AND rolcanlogin
 AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
 AND NOT pg_catalog.pg_has_role($1::name,$2::name,'MEMBER') AND NOT pg_catalog.pg_has_role($2::name,$1::name,'MEMBER')
 AND NOT pg_catalog.pg_has_role($1::name,$3::name,'MEMBER') AND NOT pg_catalog.pg_has_role($3::name,$1::name,'MEMBER')
 AND NOT pg_catalog.has_database_privilege($1::name,current_database(),'CREATE')
 AND pg_catalog.has_schema_privilege($1::name,'public','USAGE') AND NOT pg_catalog.has_schema_privilege($1::name,'public','CREATE')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
 OR r.rolreplication OR r.rolbypassrls OR r.rolname IN ('pg_execute_server_program','pg_read_server_files','pg_write_server_files'))
 AND pg_catalog.pg_has_role($1::name,r.oid,'MEMBER'))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND (pg_catalog.pg_has_role($1::name,c.relowner,'MEMBER')
 OR pg_catalog.has_table_privilege($1::name,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 OR pg_catalog.has_any_column_privilege($1::name,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='S' AND (pg_catalog.pg_has_role($1::name,c.relowner,'MEMBER')
 OR pg_catalog.has_sequence_privilege($1::name,c.oid,'USAGE,SELECT,UPDATE')))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND (p.proname LIKE 'attempt_lifecycle_%' OR p.proname LIKE 'task_inference_%'
 OR p.proname LIKE 'compute_metadata_%' OR p.proname LIKE 'artifact_output_%'
 OR (p.proname LIKE 'task_history_%' AND (p.oid<>'public.task_history_artifact_project(bytea)'::regprocedure
 OR a.grantee<>(SELECT oid FROM pg_catalog.pg_roles WHERE rolname::text=$1))))
 AND CASE WHEN a.grantee=0 THEN pg_catalog.has_function_privilege($1::name,p.oid,'EXECUTE')
 ELSE pg_catalog.pg_has_role($1::name,a.grantee,'MEMBER') END)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname LIKE 'task_history_%' AND
 (pg_catalog.pg_has_role($1::name,p.proowner,'MEMBER') OR
 (p.oid<>'public.task_history_artifact_project(bytea)'::regprocedure AND pg_catalog.has_function_privilege($1::name,p.oid,'EXECUTE'))))
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE p.oid='public.task_history_artifact_project(bytea)'::regprocedure AND a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname::text=$1)
 AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE p.oid='public.task_history_artifact_project(bytea)'::regprocedure AND a.grantee<>p.proowner
 AND (a.grantee<>(SELECT oid FROM pg_catalog.pg_roles WHERE rolname::text=$1) OR a.privilege_type<>'EXECUTE' OR a.is_grantable))
"""


class ArtifactHistoryProjection(HistoryProjection):
    _catalog = ARTIFACT_CATALOG
    _reader = "SELECT * FROM public.task_history_output_page($1,$2,$3)"
    _profile: Profile = "artifact-v2"
    _kinds = (*HistoryProjection._kinds, ArtifactPrepare)

    def __init__(self, expected: Identity, *, acceptor: str, worker: str, publisher: str):
        super().__init__(expected, acceptor=acceptor, worker=worker)
        if type(publisher) is not str or not publisher or publisher in (acceptor, worker):
            raise HistoryError("projection_config")
        self.publisher = publisher

    async def _publisher(self, conn: Any, deadline: float) -> None:
        self._time(deadline)
        if (
            await conn.fetchval(PUBLISHER_CATALOG, self.publisher, self.acceptor, self.worker)
            is not True
        ):
            raise HistoryError("projection_catalog_refused")
        self._time(deadline)

    async def qualify(self, conn: Any, *, writing: bool, deadline: float) -> None:
        await super().qualify(conn, writing=writing, deadline=deadline)
        try:
            async with asyncio.timeout_at(deadline):
                await self._publisher(conn, deadline)
        except HistoryError:
            raise
        except Exception:
            raise HistoryError("projection_unavailable", uncertain=True) from None

    async def qualify_publisher(self, conn: Any, *, deadline: float) -> None:
        self._transaction(conn)
        try:
            async with asyncio.timeout_at(deadline):
                await self._limits(conn, deadline)
                if await conn.fetchval("SELECT CURRENT_USER::text") != self.publisher:
                    raise HistoryError("projection_principal_refused")
                if await conn.fetchval(self._catalog, self.acceptor, self.worker) is not True:
                    raise HistoryError("projection_catalog_refused")
                await self._publisher(conn, deadline)
        except HistoryError:
            raise
        except Exception:
            raise HistoryError("projection_unavailable", uncertain=True) from None

    async def stage_artifact(self, conn: Any, entry: Entry, *, deadline: float) -> StagedAcceptance:
        self._time(deadline)
        self._transaction(conn)
        if (
            type(entry) is not Entry
            or type(entry.command) is not Command
            or type(entry.command.data) is not ArtifactPrepare
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
                await self.qualify_publisher(conn, deadline=deadline)
                row = await conn.fetchrow(
                    "SELECT * FROM public.task_history_artifact_project($1)", frame
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
