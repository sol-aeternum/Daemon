"""Fixed 043→050 upgrade only. No application configuration or automatic resume.

Use ``python -m scripts.migrate_044_050 plan`` for the offline source digest.
Execution requires separately reviewed target/schema facts and private inputs.
"""

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import asyncpg

from scripts import migrate

ROOT = Path(__file__).absolute().parents[1]
MANIFEST = "config/migration_044_050.json"
MANIFEST_SHA256 = "63e7e63d21348a0736b5e422d5f5140e66ba122d302150230bf10e2e2eec5550"
SQL_FILES = (
    "migrations/044_durable_tasks.sql",
    "migrations/045_identity_retention_provenance.sql",
    "migrations/046_deletion_journal_markers.sql",
    "migrations/047_account_control_persistence.sql",
    "migrations/048_compute_metadata.sql",
    "migrations/049_attempt_lifecycle.sql",
    "migrations/050_task_inference_recovery.sql",
)
RELEASE_FILES = ("scripts/migrate.py", "scripts/migrate_044_050.py", MANIFEST, *SQL_FILES)
EXCLUDED = "051_artifact_output_reservations.sql"
EXCLUDED_SCHEMA_SQL = """
SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname IN ('artifact_output_jobs','artifact_output_intents'))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
 AND p.proname IN ('artifact_output_job_arg','artifact_output_intent_arg',
 'artifact_output_account_lock','artifact_output_readback',
 'artifact_output_register','artifact_output_reserve'))
"""
IDENTITY_SQL = """
SELECT current_database() AS database, d.oid::bigint AS oid,
       current_user AS principal, session_user AS session_principal,
       pg_catalog.pg_is_in_recovery() AS replica,
       (SELECT system_identifier::text FROM pg_catalog.pg_control_system()) AS cluster_id
FROM pg_catalog.pg_database d WHERE d.datname=current_database()
"""

# Definition-only inventory: no application rows, sequence current values or
# credential catalogs. Extension-owned objects are represented by ext/version.
CATALOG_QUERIES = (
    """SELECT n.nspname AS name,pg_catalog.pg_get_userbyid(n.nspowner) AS owner,
       n.nspacl::text AS acl FROM pg_catalog.pg_namespace n WHERE n.nspname='public'""",
    """SELECT c.relname AS name,c.relkind::text AS kind,
       pg_catalog.pg_get_userbyid(c.relowner) AS owner,c.relacl::text AS acl,
       c.relrowsecurity AS rls,c.relforcerowsecurity AS force_rls
       FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')
       AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d
         WHERE d.classid='pg_catalog.pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')
       ORDER BY c.relname""",
    """SELECT c.relname AS relation,a.attnum AS position,a.attname AS name,
       pg_catalog.format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull AS required,
       a.attidentity::text AS identity,a.attgenerated::text AS generated,
       pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS default_expr
       FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
       WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped
       AND c.relkind IN ('r','p','v','m','f')
       ORDER BY c.relname,a.attnum""",
    """SELECT c.relname AS relation,k.conname AS name,k.convalidated AS valid,
       pg_catalog.pg_get_constraintdef(k.oid) AS definition
       FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' ORDER BY c.relname,k.conname""",
    """SELECT c.relname AS relation,i.relname AS name,x.indisvalid AS valid,
       pg_catalog.pg_get_indexdef(i.oid) AS definition
       FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class c ON c.oid=x.indrelid
       JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' ORDER BY c.relname,i.relname""",
    """SELECT p.proname AS name,pg_catalog.pg_get_function_identity_arguments(p.oid) AS args,
       pg_catalog.pg_get_userbyid(p.proowner) AS owner,p.proacl::text AS acl,
       pg_catalog.pg_get_functiondef(p.oid) AS definition
       FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname='public' AND p.prokind IN ('f','p')
       AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d
         WHERE d.classid='pg_catalog.pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')
       ORDER BY p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid)""",
    """SELECT c.relname AS relation,t.tgname AS name,t.tgenabled::text AS enabled,
       pg_catalog.pg_get_triggerdef(t.oid) AS definition
       FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",
    """SELECT e.extname AS name,e.extversion AS version,n.nspname AS schema
       FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
       ORDER BY e.extname""",
    """SELECT c.relname AS name,s.seqstart,s.seqincrement,s.seqmin,s.seqmax,
       s.seqcache,s.seqcycle FROM pg_catalog.pg_sequence s
       JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' ORDER BY c.relname""",
    """SELECT pg_catalog.pg_get_userbyid(d.defaclrole) AS role,
       n.nspname AS schema,d.defaclobjtype::text AS kind,d.defaclacl::text AS acl
       FROM pg_catalog.pg_default_acl d LEFT JOIN pg_catalog.pg_namespace n
       ON n.oid=d.defaclnamespace ORDER BY role,schema,kind""",
)


class Refused(RuntimeError):
    def __init__(self, code: str, sqlstate: str | None = None):
        self.code = code
        self.phase: str | None = None
        self.commit_uncertain = False
        self.receipt_unavailable = False
        self.termination_unavailable = False
        self.sqlstate = (
            sqlstate
            if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate)
            else None
        )
        super().__init__(code)


def _regular(path: Path) -> None:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_mode & 0o022:
        raise ValueError


@dataclass(frozen=True)
class Payload:
    filename: str
    sql: str


@dataclass(frozen=True)
class Release:
    previous: tuple[str, ...]
    payloads: tuple[Payload, ...]
    sha256: str


def load_release(root: Path = ROOT) -> Release:
    try:
        content = {}
        digest = hashlib.sha256()
        for relative in RELEASE_FILES:
            path = root / relative
            _regular(path)
            data = path.read_bytes()
            content[relative] = data
            digest.update(relative.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
        if hashlib.sha256(content[MANIFEST]).hexdigest() != MANIFEST_SHA256:
            raise ValueError
        manifest = json.loads(content[MANIFEST])
        if ["migrations/" + f["filename"] for f in manifest["files"]] != list(SQL_FILES):
            raise ValueError
        payloads = []
        for entry in manifest["files"]:
            data = content["migrations/" + entry["filename"]]
            if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError
            payloads.append(Payload(entry["filename"], data.decode("utf-8")))
        return Release(
            tuple(manifest["expected_previously_applied_filenames"]),
            tuple(payloads),
            digest.hexdigest(),
        )
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("release_invalid") from None


def read_private(path: Path) -> str:
    fd = None
    try:
        _regular(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != os.getuid()
            or stat.S_IMODE(s.st_mode) != 0o600
            or s.st_nlink != 1
        ):
            raise ValueError
        if s.st_size > 16384:
            raise ValueError
        return os.read(fd, 16385).decode("utf-8").strip()
    except (OSError, ValueError):
        raise Refused("private_file_invalid") from None
    finally:
        if fd is not None:
            os.close(fd)


@dataclass(frozen=True)
class Target:
    database: str
    oid: int
    principal: str
    cluster_id: str
    schema_before: str
    schema_after: str

    def __post_init__(self):
        if (
            not all(
                isinstance(s, str) and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", s)
                for s in (self.database, self.principal)
            )
            or type(self.oid) is not int
            or self.oid <= 0
            or not isinstance(self.cluster_id, str)
            or not self.cluster_id.isdecimal()
            or not all(
                isinstance(s, str) and re.fullmatch(r"[0-9a-f]{64}", s)
                for s in (self.schema_before, self.schema_after)
            )
        ):
            raise Refused("target_input_invalid")

    @classmethod
    def from_json(cls, raw: str) -> "Target":
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        try:
            return cls(**json.loads(raw, object_pairs_hook=unique))
        except (ValueError, TypeError):
            raise Refused("target_input_invalid") from None


def connection_options(url: str) -> dict[str, Any]:
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        host = parsed.hostname or query.get("host", [""])[0]
        port = parsed.port or int(query.get("port", ["0"])[0])
        database = unquote(parsed.path.lstrip("/"))
        if (
            parsed.scheme not in ("postgres", "postgresql")
            or parsed.fragment
            or not set(query).issubset({"host", "port", "sslmode"})
            or any(len(values) != 1 for values in query.values())
            or (parsed.hostname is not None and "host" in query)
            or (parsed.port is not None and "port" in query)
            or not host
            or not port
            or not 0 < port < 65536
            or parsed.username is None
            or parsed.password is None
            or not database
            or query.get("sslmode", [""])[0]
            not in ("disable", "require", "verify-ca", "verify-full")
            or any(
                k in query
                for k in ("user", "password", "database", "dbname", "service", "passfile")
            )
        ):
            raise ValueError
        mode = query["sslmode"][0]
        context: ssl.SSLContext | bool = False
        if mode != "disable":
            context = ssl.create_default_context()
            if mode != "verify-full":
                context.check_hostname = False
            if mode == "require":
                context.verify_mode = ssl.CERT_NONE
        return dict(
            dsn=url,
            host=host,
            port=port,
            database=database,
            user=unquote(parsed.username),
            password=unquote(parsed.password),
            passfile="/dev/null",
            ssl=context,  # No implicit ~/.postgresql client certificate/key fallback.
            timeout=5,
            command_timeout=65,
            server_settings={
                "search_path": "pg_catalog,public,pg_temp",
                "lock_timeout": "5s",
                "statement_timeout": "60s",
                "idle_in_transaction_session_timeout": "60s",
            },
        )
    except (ValueError, TypeError, AttributeError):
        raise Refused("connection_input_invalid") from None


class Receipt:
    def __init__(self, path: Path):
        self.fd = None
        try:
            parent = path.parent
            if (
                not path.is_absolute()
                or any(p.is_symlink() for p in (parent, *parent.parents))
                or parent.stat().st_uid != os.getuid()
                or stat.S_IMODE(parent.stat().st_mode) != 0o700
            ):
                raise ValueError
            self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError):
            self.close()
            raise Refused("receipt_invalid") from None

    def append(self, event: str, **facts: Any) -> None:
        assert self.fd is not None
        data = (json.dumps({"event": event, **facts}, sort_keys=True) + "\n").encode()
        while data:
            count = os.write(self.fd, data)
            if count <= 0:
                raise Refused("receipt_unavailable")
            data = data[count:]
        os.fsync(self.fd)

    def close(self) -> None:
        if self.fd is not None:
            fd = self.fd
            self.fd = None
            try:
                os.close(fd)
            except OSError:
                raise Refused("receipt_unavailable") from None


async def catalog_digest(conn: Any) -> str:
    async with asyncio.timeout(10):
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute("SET LOCAL search_path=pg_catalog,public,pg_temp")
            sections = [[dict(row) for row in await conn.fetch(sql)] for sql in CATALOG_QUERIES]
    return hashlib.sha256(json.dumps(sections, sort_keys=True).encode()).hexdigest()


async def execute(
    release: Release,
    *,
    expected_source: str,
    target: Target,
    database_url: str,
    receipt: Path,
    connect: Callable[..., Awaitable[Any]] = asyncpg.connect,
) -> None:
    if release != load_release() or release.sha256 != expected_source:
        raise Refused("source_mismatch")
    options = connection_options(database_url)
    audit = Receipt(receipt)
    conn = None
    failed = False
    failure: Refused | None = None
    phase, filename = "preflight", None
    try:
        audit.append(
            "intent",
            source_sha256=release.sha256,
            manifest_sha256=MANIFEST_SHA256,
            expected_target=asdict(target),
        )
        conn = await connect(**options)
        async with asyncio.timeout(5):
            identity = await conn.fetchrow(IDENTITY_SQL)
            if (
                identity is None
                or identity["database"] != target.database
                or identity["oid"] != target.oid
                or identity["principal"] != target.principal
                or identity["session_principal"] != target.principal
                or identity["replica"]
                or identity["cluster_id"] != target.cluster_id
            ):
                raise Refused("target_mismatch")
            await migrate.acquire_migration_lock(conn)
            rows = await conn.fetch("SELECT filename FROM public._migrations ORDER BY filename")
            if [r["filename"] for r in rows] != sorted(release.previous):
                raise Refused("migration_state_mismatch")
            if await conn.fetchval(EXCLUDED_SCHEMA_SQL):
                raise Refused("excluded_schema_present")
        if await catalog_digest(conn) != target.schema_before:
            raise Refused("schema_before_mismatch")
        # Omit pg_catalog so PostgreSQL implicitly searches builtins FIRST while
        # unqualified CREATEs target public. Explicit public,pg_catalog would
        # instead bind public extension functions (e.g. gen_random_uuid).
        # pg_temp remains explicit and last for relation/type resolution.
        async with asyncio.timeout(5):
            await conn.execute("SET search_path=public,pg_temp")
        audit.append("preflight_passed", backend_pid=conn.get_server_pid())
        for payload in release.payloads:
            filename, phase = payload.filename, "executing"
            audit.append("file_started", filename=filename)

            def before_commit() -> None:
                nonlocal phase
                phase = "commit_pending"
                audit.append("commit_pending", filename=filename)

            async with asyncio.timeout(63):
                await migrate.apply_migration(
                    conn, Path(filename), sql_content=payload.sql, before_commit=before_commit
                )
            phase = "commit_acknowledged"
            audit.append("commit_acknowledged", filename=filename)
        phase = "postflight"
        async with asyncio.timeout(5):
            rows = await conn.fetch("SELECT filename FROM public._migrations ORDER BY filename")
            expected = sorted((*release.previous, *(p.filename for p in release.payloads)))
            if [r["filename"] for r in rows] != expected:
                raise Refused("migration_state_mismatch")
            if await conn.fetchval(EXCLUDED_SCHEMA_SQL):
                raise Refused("excluded_schema_present")
        if await catalog_digest(conn) != target.schema_after:
            raise Refused("schema_after_mismatch")
        async with asyncio.timeout(5):
            await migrate.release_migration_lock(conn, require_owned=True)
            await conn.close(timeout=3)
        conn = None
        audit.append("completed", watermark=release.payloads[-1].filename)
    except BaseException as exc:
        failed = True
        code = (
            exc.code
            if isinstance(exc, Refused)
            else ("migration_lock_busy" if phase == "preflight" else "migration_lock_unavailable")
            if isinstance(exc, migrate.MigrationLockError)
            else "operation_cancelled"
            if isinstance(exc, asyncio.CancelledError)
            else "operation_timeout"
            if isinstance(exc, TimeoutError)
            else "operation_failed"
        )
        sqlstate = getattr(exc, "sqlstate", None)
        failure = Refused(code, sqlstate)
        failure.phase = phase
        failure.commit_uncertain = phase == "commit_pending"
        if conn is not None:
            try:
                conn.terminate()
            except Exception:
                failure.termination_unavailable = True
            conn = None
        try:
            audit.append(
                "stopped",
                code=code,
                sqlstate=failure.sqlstate,
                phase=phase,
                filename=filename,
                commit_uncertain=phase == "commit_pending",
                automatic_resume=False,
                termination_unavailable=failure.termination_unavailable,
            )
        except (OSError, Refused):
            failure.receipt_unavailable = True
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise failure from None
    finally:
        try:
            audit.close()
        except Refused:
            if not failed:
                raise
            if failure is not None:
                failure.receipt_unavailable = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="Offline fixed manifest and reviewed source digest")
    command = sub.add_parser("execute", help="Requires separate live-execution authorization")
    command.add_argument("--expect-source", required=True)
    command.add_argument("--target-file", type=Path, required=True)
    command.add_argument("--database-url-file", type=Path, required=True)
    command.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        release = load_release()
        if args.command == "plan":
            print(
                json.dumps(
                    {
                        "sha256": release.sha256,
                        "manifest_sha256": MANIFEST_SHA256,
                        "previous_files": list(release.previous),
                        "target_files": [p.filename for p in release.payloads],
                        "target_watermark": release.payloads[-1].filename,
                        "excluded": EXCLUDED,
                    }
                )
            )
        else:
            asyncio.run(
                execute(
                    release,
                    expected_source=args.expect_source,
                    target=Target.from_json(read_private(args.target_file)),
                    database_url=read_private(args.database_url_file),
                    receipt=args.receipt,
                )
            )
            print(json.dumps({"status": "completed", "watermark": release.payloads[-1].filename}))
        return 0
    except (Refused, KeyboardInterrupt, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": "stopped",
                    "code": exc.code
                    if isinstance(exc, Refused)
                    else "operation_cancelled"
                    if isinstance(exc, KeyboardInterrupt)
                    else "local_io_unavailable",
                    "sqlstate": exc.sqlstate if isinstance(exc, Refused) else None,
                    "phase": exc.phase if isinstance(exc, Refused) else None,
                    "commit_uncertain": exc.commit_uncertain if isinstance(exc, Refused) else None,
                    "receipt_unavailable": exc.receipt_unavailable
                    if isinstance(exc, Refused)
                    else None,
                    "termination_unavailable": exc.termination_unavailable
                    if isinstance(exc, Refused)
                    else None,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
