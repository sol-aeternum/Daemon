"""Approved in-place benchmark restore with server-side connection exclusion.

No production target or generic restore API. Calling this is a destructive action;
the launcher requires the separately reviewed live-state action approval artifact.
No error path reopens connections or automatically repeats a restore.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, AsyncIterator, Mapping, cast

import asyncpg

from orchestrator.eval.bench_db import fingerprint_benchmark_users, question_user_id
from orchestrator.eval.campaign_guard import Campaign, CampaignStopped, _write
from orchestrator.eval.campaign_sql import validate_sql
from orchestrator.eval.campaign_validation import (
    SNAPSHOT_SHA256,
    STATE_SHA256,
    digest_file,
    require,
)

CONTAINER = "9aa9b80e8c782e1efddaa8a6839f525b58be5b85cca067db10b81c8ec0002bef"
DB = "daemon_lme_bench"
MARKER = "daemon-longmemeval-benchmark-v1"
DSN = "postgresql://bench:bench@127.0.0.1:55432/daemon_lme_bench"
CONTROL_DSN = "postgresql://bench:bench@127.0.0.1:55432/postgres"


def container_identity() -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .Id}} {{json .Name}} {{json .State.Running}} {{json .NetworkSettings.Ports}}",
            CONTAINER,
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=15,
    )
    decoder = json.JSONDecoder()
    text = result.stdout.strip()
    fields = []
    while text:
        field, end = decoder.raw_decode(text)
        fields.append(field)
        text = text[end:].lstrip()
    require(
        fields
        == [
            CONTAINER,
            "/daemon-lme-bench-postgres-1",
            True,
            {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]},
        ],
        "Benchmark container/port identity differs",
    )
    return {"id": fields[0], "name": fields[1], "ports": fields[3]}


async def target_identity(control: Any) -> dict[str, Any]:
    row = await control.fetchrow(
        "SELECT oid, datname, datallowconn, pg_get_userbyid(datdba) AS owner, "
        "shobj_description(oid,'pg_database') AS marker, datacl::text AS acl, "
        "(SELECT count(*) FROM pg_db_role_setting WHERE setdatabase=pg_database.oid) AS settings "
        "FROM pg_database WHERE datname=$1",
        DB,
    )
    require(row is not None, "Approved benchmark database absent")
    result = dict(row)
    require(
        result["datname"] == DB and result["owner"] == "bench" and result["marker"] == MARKER,
        "Existing target marker/owner differs",
    )
    require(result["settings"] == 0 and result["acl"] is None, "Unexpected database settings/ACL")
    result["system_identifier"] = str(
        await control.fetchval("SELECT system_identifier FROM pg_control_system()")
    )
    return result


async def verify_server(control: Any) -> None:
    # Bind the host-loopback control socket to the exact docker container, not
    # merely to whichever server currently occupies the port.
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "--no-psqlrc",
            "-U",
            "bench",
            "-d",
            "postgres",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT system_identifier FROM pg_control_system()",
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=15,
    )
    require(
        result.stdout.strip()
        == str(await control.fetchval("SELECT system_identifier FROM pg_control_system()")),
        "Control socket belongs to a different server",
    )


async def verify_sessions(control: Any, pids: set[int]) -> None:
    observed = await control.fetch("SELECT pid FROM pg_stat_activity WHERE datname=$1", DB)
    require({int(row["pid"]) for row in observed} == pids, "Unowned or missing benchmark backend")
    require(
        await control.fetchval("SELECT count(*) FROM pg_prepared_xacts WHERE database=$1", DB) == 0,
        "Prepared benchmark transaction exists",
    )
    require(
        await control.fetchval("SELECT count(*) FROM pg_replication_slots WHERE database=$1", DB)
        == 0,
        "Benchmark replication slot exists",
    )
    require(
        (await target_identity(control))["datallowconn"] is False,
        "Benchmark connection admission reopened",
    )


async def catalogue(connection: Any) -> str:
    """Reject out-of-scope persistent objects, then hash relevant schema metadata."""
    checks = [
        "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','pg_catalog','information_schema','pg_toast')",
        "SELECT count(*) FROM pg_event_trigger",
        "SELECT count(*) FROM pg_foreign_server",
        "SELECT count(*) FROM pg_subscription",
        "SELECT count(*) FROM pg_default_acl",
        "SELECT count(*) FROM pg_largeobject_metadata",
        "SELECT count(*) FROM pg_foreign_data_wrapper",
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid>=16384 AND n.nspname IN ('pg_catalog','information_schema')",
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.oid>=16384 AND n.nspname IN ('pg_catalog','information_schema')",
        "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE t.oid>=16384 AND n.nspname IN ('pg_catalog','information_schema')",
    ]
    for sql in checks:
        require(
            await connection.fetchval(sql) == 0, "Out-of-scope database object; restore refused"
        )
    extensions = [
        tuple(row)
        for row in await connection.fetch(
            "SELECT extname, extversion FROM pg_extension ORDER BY extname"
        )
    ]
    require(
        extensions == [("pgcrypto", "1.3"), ("plpgsql", "1.0"), ("vector", "0.8.1")],
        "Unexpected extension inventory",
    )
    namespace = await connection.fetchrow(
        "SELECT pg_get_userbyid(nspowner) AS owner, nspacl::text AS acl FROM pg_namespace WHERE nspname='public'"
    )
    if namespace is None:
        raise CampaignStopped("Public schema missing")
    schema = cast(Mapping[str, Any], namespace)
    require(
        schema["owner"] == "pg_database_owner",
        "Public schema owner differs from approved baseline",
    )
    require(
        schema["acl"] == "{pg_database_owner=UC/pg_database_owner,=U/pg_database_owner}",
        "Public schema ACL differs from baseline",
    )
    queries = [
        "SELECT c.relname,c.relkind::text,pg_get_userbyid(c.relowner),c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,c.relkind",
        "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,k.conname",
        "SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind IN ('f','p') ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)",
        "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname",
        "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        "SELECT t.typname,t.typtype::text,t.typcategory::text,t.typnotnull,t.typdefault,format_type(t.typbasetype,t.typtypmod),format_type(t.typelem,NULL),t.typinput::regprocedure::text,t.typoutput::regprocedure::text,coalesce((SELECT json_agg(json_build_array(e.enumsortorder,e.enumlabel) ORDER BY e.enumsortorder) FROM pg_enum e WHERE e.enumtypid=t.oid)::text,'[]') FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' ORDER BY t.typname",
    ]
    values = [[tuple(row) for row in await connection.fetch(sql)] for sql in queries]
    # Enumerate the entire dependency closure that DROP SCHEMA CASCADE can reach,
    # not just the hand-selected definition classes above. Stable semantic
    # addresses cover new standalone object kinds (enum/domain/operator/etc.).
    # Internal toast names and RI trigger names contain fresh OIDs; their owning
    # public relation/constraint definitions already cover those generated objects.
    objects = await connection.fetch("""
        WITH RECURSIVE affected(classid,objid) AS (
          SELECT 'pg_namespace'::regclass::oid,oid FROM pg_namespace WHERE nspname='public'
          UNION
          SELECT d.classid,d.objid FROM pg_depend d JOIN affected a
            ON d.refclassid=a.classid AND d.refobjid=a.objid
        ), stable AS MATERIALIZED (
          SELECT a.* FROM affected a
          LEFT JOIN pg_class c ON a.classid='pg_class'::regclass AND c.oid=a.objid
          LEFT JOIN pg_namespace n ON n.oid=c.relnamespace
          LEFT JOIN pg_trigger t ON a.classid='pg_trigger'::regclass AND t.oid=a.objid
          WHERE n.nspname IS DISTINCT FROM 'pg_toast'
            AND NOT coalesce(t.tgisinternal,false) AND a.classid<>'pg_enum'::regclass
        )
        SELECT i.type,i.schema,i.identity FROM stable a
        CROSS JOIN LATERAL pg_identify_object(a.classid,a.objid,0) i
        ORDER BY i.type,i.schema,i.identity
    """)
    require(
        all(row["schema"] in (None, "public") for row in objects),
        "Cascade reaches outside approved public schema",
    )
    values.append([tuple(row) for row in objects])
    return hashlib.sha256(
        json.dumps([extensions, dict(schema), values], sort_keys=True).encode()
    ).hexdigest()


class FixedPool:
    """Finite acquire time; no campaign fallback pool can be created."""

    def __init__(self, pool: Any):
        self.pool = pool

    def acquire(self, *, timeout: float = 30) -> Any:
        return self.pool.acquire(timeout=timeout)

    async def fetch(self, sql: str, *args: Any) -> Any:
        async with self.acquire() as connection:
            return await connection.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        async with self.acquire() as connection:
            return await connection.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        async with self.acquire() as connection:
            return await connection.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> Any:
        async with self.acquire() as connection:
            return await connection.execute(sql, *args)

    async def executemany(self, sql: str, args: Any) -> Any:
        async with self.acquire() as connection:
            return await connection.executemany(sql, args)

    async def close(self) -> None:
        await self.pool.close()


def render_archive(snapshot: Path, directory: Path) -> Path:
    require(digest_file(snapshot) == SNAPSHOT_SHA256, "Immutable snapshot hash differs")
    path = directory / "restore.sql"
    with path.open("xb") as out, snapshot.open("rb") as source:
        os.chmod(path, 0o600)
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                CONTAINER,
                "pg_restore",
                "--no-owner",
                "--no-privileges",
                "--file=-",
            ],
            stdin=source,
            stdout=out,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        out.flush()
        os.fsync(out.fileno())
    require(result.returncode == 0, "Archive converter failed; no restore attempted")
    validation = validate_sql(path)
    _write(directory / "restore-script.json", {"sha256": digest_file(path), **validation})
    return path


async def seal_failed_admission(control: Any, pins: Mapping[str, Any]) -> str:
    """One bounded compensation on the existing connection; never reconnect/retry."""
    try:
        async with asyncio.timeout(15):
            for after in (False, True):
                identity = await target_identity(control)
                require(
                    identity["system_identifier"] == pins["server_identifier"]
                    and identity["oid"] == pins["database_oid"],
                    "Compensation target changed",
                )
                if after:
                    require(identity["datallowconn"] is False, "Admission closure unconfirmed")
                else:
                    await control.execute("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS false")
        return "closed"
    except (Exception, asyncio.CancelledError):
        return "unknown"


@asynccontextmanager
async def attached_pool(campaign: Campaign) -> AsyncIterator[FixedPool]:
    """One-shot attach to the already restored A1; never restores or increments it."""
    from orchestrator.eval.campaign_retry import enabled, validate_attribution

    campaign._check()
    require(enabled(campaign), "Attach is only approved for explicit retry recovery")
    campaign._recovery_check()
    state, pins = campaign.state, campaign.plan["pins"]
    validate_attribution(state, campaign.plan, attached=False)
    final_recovery = "final_verdict_recovery_lineage" in pins
    if final_recovery:
        from orchestrator.eval.campaign_final_verdict import before_attach

        before_attach(campaign)
    require(
        state["arm"] == "A1"
        and state["arm_index"] == 1
        and state["restore_attempts"] == 1
        and len(state["restores"]) == 1
        and state.get("attach_pending") is None
        and not state.get("attach_receipt")
        and state["pending"] is None
        and len(state["receipts"]) == (200 if final_recovery else 195),
        "Attach repeated or outside exact recovered A1 state",
    )
    container = container_identity()
    control = await asyncpg.connect(CONTROL_DSN, timeout=10, command_timeout=30)
    pool: Any = None
    admitted = False
    try:
        await verify_server(control)
        identity = await target_identity(control)
        require(
            identity["system_identifier"] == pins["server_identifier"]
            and identity["oid"] == pins["database_oid"]
            and identity["datallowconn"] is False,
            "Attach target identity/admission differs",
        )
        await verify_sessions(control, set())
        campaign._recovery_check()
        state["attach_pending"] = {
            "identity": identity,
            "container": container,
            "inherited_restore": state["imported_restore"],
        }
        campaign._save()  # Before even a possibly unacknowledged enable.
        admitted = True
        await control.execute("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS true")
        pool = await asyncpg.create_pool(
            DSN,
            min_size=10,
            max_size=10,
            max_queries=1000000,
            max_inactive_connection_lifetime=0,
            timeout=10,
            command_timeout=30,
        )
        async with AsyncExitStack() as held:
            connections = [
                await held.enter_async_context(pool.acquire(timeout=30)) for _ in range(10)
            ]
            pids = {
                int(await connection.fetchval("SELECT pg_backend_pid()"))
                for connection in connections
            }
            require(len(pids) == 10, "Attach pool was not eagerly opened")
            await control.execute("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS false")
            await verify_sessions(control, pids)
            require(
                await catalogue(connections[0]) == pins["catalogue_sha256"],
                "Attach catalogue changed",
            )
            fingerprint = await fingerprint_benchmark_users(
                connections[0], [question_user_id(qid) for qid in campaign.plan["question_ids"]]
            )
            require(fingerprint["sha256"] == STATE_SHA256, "Attach protected state changed")
            state["attach_receipt"] = {
                "fingerprint": fingerprint,
                "owned_pids": sorted(pids),
                "inherited_restore": state["imported_restore"],
                "identity": identity,
            }
            if final_recovery:
                state["attach_receipt"]["final_verdict_recovery_lineage"] = pins[
                    "final_verdict_recovery_lineage"
                ]
            state["attach_pending"] = None
            campaign._save()
            campaign.database_pool = FixedPool(pool)
        yield campaign.database_pool
        await verify_sessions(control, set())  # Canonical _phase must have closed the fixed pool.
    except BaseException:
        if admitted:
            sealing = asyncio.create_task(seal_failed_admission(control, pins))
            while not sealing.done():
                try:
                    await asyncio.shield(sealing)
                except asyncio.CancelledError:
                    continue
            state["failure_admission"] = sealing.result()
        campaign.stop("Verified attach failed; no reconnect/retry/resume")
        raise
    finally:
        try:
            if pool is not None:
                await asyncio.wait_for(pool.close(), 30)
        finally:
            campaign.database_pool = None
            await control.close()


@asynccontextmanager
async def restored_pool(campaign: Campaign, arm: str, snapshot: Path) -> AsyncIterator[FixedPool]:
    """One reviewed destructive attempt; admission remains closed on every error."""
    identity = container_identity()
    artifacts = campaign.directory / arm
    artifacts.mkdir(mode=0o700, exist_ok=False)
    require(digest_file(snapshot) == SNAPSHOT_SHA256, "Immutable snapshot changed")
    script_pin = campaign.plan["pins"]["restore_script"]
    script = Path(script_pin["path"])
    require(digest_file(script) == script_pin["sha256"], "Reviewed restore script changed")
    await asyncio.to_thread(validate_sql, script)
    control = await asyncpg.connect(CONTROL_DSN, timeout=10, command_timeout=30)
    pool: Any = None
    child: subprocess.Popen[bytes] | None = None
    admitted = False
    original_failure: BaseException | None = None
    stderr = (artifacts / "restore-stderr.log").open("xb")
    os.chmod(stderr.name, 0o600)
    try:
        await verify_server(control)
        before = await target_identity(control)
        require(
            before["system_identifier"] == campaign.plan["pins"]["server_identifier"],
            "Server identity differs from pin",
        )
        require(
            before["oid"] == campaign.plan["pins"]["database_oid"],
            "Database identity differs from pin",
        )
        campaign.pending_restore(arm, {**identity, **before, "script_sha256": digest_file(script)})
        admitted = True  # Before enabling: even a lost enable acknowledgment needs sealing.
        if not before["datallowconn"]:
            await control.execute("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS true")
        pool = await asyncpg.create_pool(
            DSN,
            min_size=10,
            max_size=10,
            max_queries=1000000,
            max_inactive_connection_lifetime=0,
            timeout=10,
            command_timeout=30,
        )
        async with AsyncExitStack() as held:
            connections = [
                await held.enter_async_context(pool.acquire(timeout=30)) for _ in range(10)
            ]
            pids = {
                int(await connection.fetchval("SELECT pg_backend_pid()"))
                for connection in connections
            }
            require(len(pids) == 10, "Pool was not eagerly opened")
            child = subprocess.Popen(
                [
                    "docker",
                    "exec",
                    "-i",
                    CONTAINER,
                    "psql",
                    "--no-psqlrc",
                    "-U",
                    "bench",
                    "-d",
                    DB,
                    "-qAt",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
            )
            assert child.stdin is not None and child.stdout is not None
            child.stdin.write(b"SELECT pg_backend_pid();\n")
            child.stdin.flush()
            line = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=15)
            require(line.strip().isdigit(), "Restore PID handshake failed")
            psql_pid = int(line)
            await control.execute("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS false")
            await verify_sessions(control, pids | {psql_pid})
            require(
                await catalogue(connections[0]) == campaign.plan["pins"]["catalogue_sha256"],
                "Pre-restore catalogue differs from approved baseline",
            )
            # Schema ACL/owner match the read-only pinned PG16 baseline, not an
            # inferred owner. Entire replacement and restore are one transaction.
            prefix = b"BEGIN;\nDROP SCHEMA public CASCADE;\nCREATE SCHEMA public AUTHORIZATION pg_database_owner;\nGRANT USAGE ON SCHEMA public TO PUBLIC;\n"

            def restore() -> int:
                assert child is not None and child.stdin is not None and child.stdout is not None
                child.stdin.write(prefix)
                with script.open("rb") as source:
                    for block in iter(lambda: source.read(65536), b""):
                        child.stdin.write(block)
                child.stdin.write(b"\nCOMMIT;\n")
                child.stdin.close()
                # Quiet psql output is numeric setval/set_config receipts, never
                # exported. Drain concurrently with the pipe input via a small
                # output reader below to avoid deadlock on a large schema.
                return child.wait(timeout=180)

            output = asyncio.create_task(asyncio.to_thread(child.stdout.read))
            try:
                code = await asyncio.wait_for(asyncio.to_thread(restore), 190)
                await output
            finally:
                if not output.done():
                    output.cancel()
            require(code == 0, "Owned restore session failed; target remains disabled")
            await verify_sessions(control, pids)
            require(
                await catalogue(connections[0]) == campaign.plan["pins"]["catalogue_sha256"],
                "Restored catalogue differs",
            )
            users = [question_user_id(qid) for qid in campaign.plan["question_ids"]]
            fingerprint = await fingerprint_benchmark_users(connections[0], users)
            require(fingerprint["sha256"] == STATE_SHA256, "Restored protected fingerprint differs")
            receipt = {
                "arm": arm,
                "fingerprint": fingerprint,
                "owned_pids": sorted(pids),
                "script_sha256": digest_file(script),
            }
            campaign.restored(arm, receipt)
            campaign.database_pool = FixedPool(pool)
        campaign.start_arm(arm, verified_pins=campaign.plan["pins"], restore=receipt)
        yield campaign.database_pool
        await verify_sessions(control, set())  # Canonical phase must close its pool.
    except BaseException as failure:
        original_failure = failure
        outcome = "not_attempted"
        if admitted:
            sealing = asyncio.create_task(seal_failed_admission(control, campaign.plan["pins"]))
            while not sealing.done():
                try:
                    await asyncio.shield(sealing)
                except asyncio.CancelledError:
                    # Defer cancellation only for this bounded compensation, never
                    # resume restore/evaluation work. Re-raise original failure below.
                    continue
            outcome = sealing.result()
        campaign.state["failure_admission"] = outcome
        try:
            campaign.stop(
                f"Restore/owned pool failed; admission {outcome}; reconciliation required"
            )
        except BaseException as recording_error:
            failure.add_note(
                f"Failure evidence persistence failed: {type(recording_error).__name__}"
            )
        raise
    finally:
        # Stop only our exact child and pool. Never terminate other DB producers,
        # reopen the DB or retry an unknown action during error cleanup.
        cleanup_errors: list[BaseException] = []
        try:
            if child is not None and child.poll() is None:
                child.terminate()
                await asyncio.to_thread(child.wait, timeout=15)
        except BaseException as error:
            cleanup_errors.append(error)
        try:
            if pool is not None:
                await asyncio.wait_for(pool.close(), 30)
        except BaseException as error:
            cleanup_errors.append(error)
        finally:
            campaign.database_pool = None
            stderr.close()
        try:
            await control.close()
        except BaseException as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            if original_failure is not None:
                for error in cleanup_errors:
                    original_failure.add_note(
                        f"Owned resource cleanup unresolved: {type(error).__name__}"
                    )
            else:
                raise cleanup_errors[0]
