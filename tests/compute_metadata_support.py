"""Owned, finite synthetic PostgreSQL fixture; no application DSN or TCP listener.

Requires an explicitly enabled test and an already cached pgvector image. Only
this fixture's exact labelled network-none container/processes are stopped.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import struct
import time
from uuid import uuid4

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "048_compute_metadata.sql"
UP = ROOT / "migrations" / MIGRATION
DOWN = ROOT / "migrations/rollback/048_compute_metadata.down.sql"
KEY = 0x4441454D4F4E
FUNCTIONS = (
    "compute_metadata_check_account(uuid)",
    "compute_metadata_create_scope(uuid,uuid)",
    "compute_metadata_attach(uuid,uuid,uuid,uuid,uuid)",
    "compute_metadata_read_scope(uuid,uuid)",
    "compute_metadata_read_binding(uuid,uuid)",
)
ENV = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "C.UTF-8"}


def command(args, *, text=None, timeout=15):
    return subprocess.run(
        args, input=text, capture_output=True, text=True, env=ENV, timeout=timeout
    )


class Cluster:
    def __init__(self, resource: Path, evidence: Path):
        self.resource, self.evidence = resource, evidence
        self.nonce = uuid4().hex
        self.container = None
        self.receipts = []
        self.databases = set()

    def record(self, event, **data):
        self.receipts.append({"event": event, **data})
        (self.evidence / f"postgres-{self.nonce}.json").write_text(
            json.dumps(self.receipts, indent=2) + "\n"
        )

    def start(self):
        result = command(
            [
                "docker",
                "run",
                "-d",
                "--pull=never",
                "--name",
                "metadata-test-" + self.nonce,
                "--label",
                "opencode.task=" + ROOT.name,
                "--label",
                "opencode.compute-metadata-fixture=" + self.nonce,
                "--network",
                "none",
                "--memory",
                "384m",
                "--pids-limit",
                "128",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=201326592",
                "--tmpfs",
                "/var/run/postgresql:rw,size=8388608",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "pgvector/pgvector:pg16",
            ]
        )
        assert result.returncode == 0, result.stderr
        self.container = result.stdout.strip()
        self.record("owned_container", id=self.container, nonce=self.nonce, task=ROOT.name)
        until = time.monotonic() + 15
        while "init process complete" not in command(["docker", "logs", self.container]).stdout:
            assert time.monotonic() < until, "owned PostgreSQL init timed out"
            time.sleep(0.05)
        while command(
            ["docker", "exec", self.container, "pg_isready", "-U", "postgres"]
        ).returncode:
            assert time.monotonic() < until, "owned PostgreSQL not ready"
            time.sleep(0.05)
        self.sql(
            "CREATE ROLE md_owner LOGIN NOSUPERUSER NOCREATEROLE;"
            "CREATE ROLE md_app LOGIN NOSUPERUSER NOCREATEROLE;"
            "CREATE ROLE md_third LOGIN NOSUPERUSER NOCREATEROLE;"
            "CREATE ROLE md_controller LOGIN NOSUPERUSER NOCREATEROLE;"
            "CREATE ROLE md_schema NOLOGIN; CREATE ROLE md_function NOLOGIN;"
            "CREATE ROLE md_mid NOLOGIN;"
            "CREATE ROLE md_table NOLOGIN;"
        )
        self.record("server", version=self.sql("SELECT version()"))

    def sql(self, sql, *, database="postgres", role="postgres", success=True):
        result = command(
            [
                "docker",
                "exec",
                "-i",
                self.container,
                "psql",
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                role,
                "-d",
                database,
            ],
            text="SET statement_timeout='5s'; SET lock_timeout='3s';\n" + sql,
        )
        if success:
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()
        assert result.returncode != 0, result.stdout
        return result.stderr

    def prepare(self, database):
        self.sql(f'CREATE DATABASE "{database}"')
        self.databases.add(database)
        # This fixture qualifies the pinned metadata boundary through 048. Later
        # source-only migrations have their own explicit qualification fixtures.
        files = [p for p in sorted((ROOT / "migrations").glob("*.sql")) if p.name <= MIGRATION]
        assert files[-2].name == "047_account_control_persistence.sql"
        self.sql(
            "GRANT USAGE,CREATE ON SCHEMA public TO md_owner;"
            "SET ROLE md_owner; CREATE TABLE public._migrations("
            "filename text PRIMARY KEY, applied_at timestamptz DEFAULT now());",
            database=database,
        )
        script = ""
        for file in files:
            role = "postgres" if file.name == "001_create_extensions.sql" else "md_owner"
            script += (
                f"SET ROLE {role}; BEGIN;"
                + file.read_text()
                + f"\nINSERT INTO public._migrations(filename) VALUES('{file.name}'); COMMIT;\n"
            )
        self.sql(script, database=database)
        self.record(
            "full_migration_chain", database=database, count=len(files), last=files[-1].name
        )
        self.grants(database)

    def grants(self, database):
        self.sql(
            ";".join(f"GRANT EXECUTE ON FUNCTION public.{fn} TO md_app" for fn in FUNCTIONS[1:])
            + ";",
            database=database,
            role="md_owner",
        )

    def stop(self):
        if self.container is None:
            return
        info = json.loads(command(["docker", "inspect", self.container]).stdout)[0]
        assert info["Id"] == self.container
        assert info["Config"]["Labels"]["opencode.compute-metadata-fixture"] == self.nonce
        assert info["Config"]["Labels"]["opencode.task"] == ROOT.name
        assert info["HostConfig"]["NetworkMode"] == "none" and not info["Mounts"]
        if info["State"]["Running"]:
            result = command(["docker", "stop", "--timeout", "2", self.container])
            assert result.returncode == 0, result.stderr
        final = json.loads(command(["docker", "inspect", self.container]).stdout)[0]
        assert not final["State"]["Running"] and final["State"]["Pid"] == 0
        self.record("stopped_container", id=self.container, pid=0, running=False)


class Bridge:
    """Server EOF ends the bridge; exact stdin child is killed AND reaped."""

    def __init__(self, cluster, path):
        self.cluster, self.path = cluster, path
        self.handlers, self.processes, self.failures = set(), [], []
        self.commit_fault = None
        self.commit_seen = asyncio.Event()
        self.discarded_commit_bytes = 0

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        proc = None
        jobs = []
        try:
            async with asyncio.timeout(2):
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "exec",
                    "-i",
                    self.cluster.container,
                    "bash",
                    "-c",
                    "exec 3<>/dev/tcp/127.0.0.1/5432; exec 4<&0; "
                    'cat <&4 >&3 & child=$!; cat <&3; kill "$child" 2>/dev/null; wait "$child"; exit 0',
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=ENV,
                )
            self.processes.append(proc)
            self.cluster.record("bridge_started", pid=proc.pid, container=self.cluster.container)
            stdin, stdout = proc.stdin, proc.stdout
            assert stdin is not None and stdout is not None
            muted = False

            async def up():
                nonlocal muted
                startup = True
                app_client = False
                buffer = b""
                try:
                    while data := await reader.read(65536):
                        buffer += data
                        while True:
                            header = 4 if startup else 5
                            if len(buffer) < header:
                                break
                            length = struct.unpack("!i", buffer[0:4] if startup else buffer[1:5])[0]
                            total = length if startup else length + 1
                            if len(buffer) < total:
                                break
                            frame, buffer = buffer[:total], buffer[total:]
                            if startup:
                                app_client = b"user\0md_app\0" in frame
                            commit = (
                                not startup
                                and frame[:1] == b"Q"
                                and frame[5:].rstrip(b"\0").strip() == b"COMMIT;"
                            )
                            startup = False
                            if commit and app_client and self.commit_fault:
                                muted = True
                                self.commit_seen.set()
                                self.cluster.record(
                                    "wire_commit_fault", mode=self.commit_fault, bridge_pid=proc.pid
                                )
                                if self.commit_fault == "before":
                                    continue
                            stdin.write(frame)
                        await stdin.drain()
                finally:
                    stdin.close()

            async def down():
                try:
                    while data := await stdout.read(65536):
                        if muted:
                            self.discarded_commit_bytes += len(data)
                        else:
                            writer.write(data)
                            await writer.drain()
                finally:
                    writer.close()

            jobs = [asyncio.create_task(up()), asyncio.create_task(down())]
            await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        except Exception as exc:
            self.failures.append(type(exc).__name__)
        finally:
            writer.close()
            if proc and proc.stdin:
                proc.stdin.close()
            for job in jobs:
                job.cancel()
            if jobs:
                done, pending = await asyncio.wait(jobs, timeout=0.5)
                if pending:
                    self.failures.append("pump_not_stopped")
                for job in done:
                    if not job.cancelled():
                        job.exception()
            if proc:
                waiter = asyncio.create_task(proc.wait())
                done, _ = await asyncio.wait([waiter], timeout=0.5)
                if not done:
                    proc.kill()
                    done, _ = await asyncio.wait([waiter], timeout=0.5)
                if not done:
                    self.failures.append("process_not_reaped")
                    waiter.cancel()
                self.cluster.record("bridge_reaped", pid=proc.pid, returncode=proc.returncode)
            try:
                async with asyncio.timeout(0.5):
                    await writer.wait_closed()
            except Exception as exc:
                self.failures.append("writer:" + type(exc).__name__)
            self.handlers.discard(task)

    async def start(self):
        self.server = await asyncio.start_unix_server(self.handle, str(self.path))
        os.chmod(self.path, 0o600)

    async def close(self):
        self.server.close()
        try:
            if self.handlers:
                _, pending = await asyncio.wait(list(self.handlers), timeout=2)
                if pending:
                    for task in pending:
                        task.cancel()
                    _, pending = await asyncio.wait(pending, timeout=2)
                assert not pending, "owned bridge handlers unfinished"
        finally:
            # Python 3.14 Server.wait_closed includes active clients, not just
            # listener shutdown. First let handler finalizers close writers and
            # reap their exact processes; never skip those on an early timeout.
            async with asyncio.timeout(0.5):
                await self.server.wait_closed()
        assert not self.failures, self.failures
        assert all(proc.returncode is not None for proc in self.processes)
        self.cluster.record(
            "bridge_closed", handlers=len(self.handlers), processes=len(self.processes)
        )


async def close_connection(conn):
    try:
        async with asyncio.timeout(0.7):
            await conn.close()
    except TimeoutError:
        conn.terminate()
        raise AssertionError("owned connection did not close gracefully") from None


class Database:
    def __init__(self, cluster, name, host, bridge):
        self.cluster, self.name, self.host = cluster, name, host
        self.bridge = bridge

    def sql(self, sql, *, role="md_owner", success=True):
        return self.cluster.sql(sql, database=self.name, role=role, success=success)

    async def connect(self, role="md_owner", **kw):
        return await asyncpg.connect(
            host=str(self.host),
            user=role,
            password="",  # Synthetic trust fixture; never consult a user's .pgpass.
            database=self.name,
            ssl=False,
            timeout=2,
            command_timeout=3,
            **kw,
        )

    @asynccontextmanager
    async def connection(self, role="md_owner", **kw):
        conn = await self.connect(role, **kw)
        try:
            yield conn
        finally:
            await close_connection(conn)

    @asynccontextmanager
    async def pool(self, **kw):
        kw.setdefault("min_size", 0)
        kw.setdefault("max_size", 2)
        pool = await asyncpg.create_pool(
            host=str(self.host),
            user="md_app",
            password="",
            database=self.name,
            ssl=False,
            timeout=2,
            **kw,
        )
        try:
            yield pool
        finally:
            try:
                async with asyncio.timeout(0.7):
                    await pool.close()
            except TimeoutError:
                pool.terminate()
                raise AssertionError("owned pool did not close gracefully") from None

    async def account(self, conn):
        ident = uuid4()
        await conn.execute(
            "INSERT INTO public.users(id,username) VALUES($1,$2)", ident, "fixture-" + str(ident)
        )
        await conn.execute("INSERT INTO public.entitlement_accounts(user_id) VALUES($1)", ident)
        return ident

    async def reservation(self, conn, account, group=None):
        ident = uuid4()
        await conn.execute(
            """INSERT INTO public.entitlement_reservations
            (id,user_id,period_key,plan,operation,charge_kind,scope_id,reserved_microusd)
            VALUES($1,$2,'2026-10','free','fixture','plan',$3,100)""",
            ident,
            account,
            group,
        )
        return ident

    async def snapshot(self, conn, account):
        return await conn.fetchval(
            """SELECT jsonb_build_object(
            'account',(SELECT to_jsonb(a) FROM public.entitlement_accounts a WHERE user_id=$1),
            'ledger',(SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM public.entitlement_reservations r WHERE user_id=$1),
            'periods',(SELECT jsonb_agg(to_jsonb(p) ORDER BY period_key) FROM public.entitlement_usage_periods p WHERE user_id=$1))""",
            account,
        )

    async def wait_lock(self, admin, pid):
        async with asyncio.timeout(1.5):
            while not await admin.fetchval(
                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", pid
            ):
                await asyncio.sleep(0.005)


@pytest.fixture(scope="module")
def metadata_cluster(request):
    if not request.config.getoption("--compute-metadata-postgres"):
        pytest.skip("requires explicit --compute-metadata-postgres; never uses application DSNs")
    # Managed jobs supply these private SSD destinations. Never fall back to /tmp.
    resource = Path(os.environ["OPENCODE_WORK_TEST_DIR"])
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    cluster = Cluster(resource, evidence)
    try:
        cluster.start()
        yield cluster
    finally:
        cluster.stop()
