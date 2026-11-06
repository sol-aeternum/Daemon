"""Synthetic full-SQL restore diagnostics, not restore/replay authority.

Each archive contains fixture data only. H, J1 and artifact roots stay independent
of the restored database. The rollback gap is asserted explicitly as a missing
release guarantee, never an application recovery path or permission to retry.
"""

import asyncio
import copy
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from orchestrator.memory.store import MemoryStore
from orchestrator.resource_store import ResourceStore, ReservedOutputPublisher, ResourceError
from orchestrator.resource_storage import StorageError
from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.connection_cleanup import retain_pool_cleanup
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.mixed_store import MixedJournal
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.tasks.inference import InferenceRecovery
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import TaskStore
from scripts.migrate_044_050 import CATALOG_QUERIES
from tests.compute_metadata_support import Database, ENV, ROOT, command
from tests.deletion_journal_support import request
from tests import test_history_output_phases_postgres as q
from tests.restore_history_support import KEY
from tests.test_history_coordination import available

metadata_cluster = q.metadata_cluster
db = q.db
projection_db = q.projection_db
scenario = q.scenario
effects = q.effects
reserved = q.reserved
presenter = q.presenter
reservation_owner = q.reservation_owner
preparer = q.preparer
phase_owner = q.phase_owner

IMAGE = "sha256:33198da2828a14c30348d2ccb4750833d5ed9a44c88d840a0e523d7417120337"

RELATION_ACLS = """SELECT c.relname AS name, COALESCE((
 SELECT jsonb_agg(jsonb_build_array(pg_catalog.pg_get_userbyid(x.grantor),
 CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(x.grantee) END,
 x.privilege_type,x.is_grantable) ORDER BY x.grantor,x.grantee,x.privilege_type,x.is_grantable)
 FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault(
 CASE WHEN c.relkind='S' THEN 's'::\"char\" ELSE 'r'::\"char\" END,c.relowner))) x
 ),'[]'::jsonb)::text AS privileges
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d WHERE
 d.classid='pg_catalog.pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')
 ORDER BY c.relname"""


async def catalogs(conn):
    # Keep the existing runner's raw digest representation intact. This separate
    # test comparison resolves relation ACL defaults and visible column ordinals
    # only; owners, definitions, constraints, functions and every other field stay.
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        await conn.execute("SET LOCAL search_path=pg_catalog,public,pg_temp")
        sections = [[dict(row) for row in await conn.fetch(sql)] for sql in CATALOG_QUERIES]
        grants = {
            row["name"]: json.loads(row["privileges"]) for row in await conn.fetch(RELATION_ACLS)
        }
    normalized = copy.deepcopy(sections)
    assert set(grants) == {row["name"] for row in normalized[1]}
    for row in normalized[1]:
        row["acl"] = grants[row["name"]]
    positions = {}
    for row in normalized[2]:
        relation = row["relation"]
        positions[relation] = positions.get(relation, 0) + 1
        row["position"] = positions[relation]
    raw = hashlib.sha256(json.dumps(sections, sort_keys=True).encode()).hexdigest()
    logical = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    return sections, raw, logical


def owned(cluster):
    info = json.loads(command(["docker", "inspect", cluster.container]).stdout)[0]
    assert info["Id"] == cluster.container and info["Image"] == IMAGE
    assert info["Config"]["Labels"]["opencode.compute-metadata-fixture"] == cluster.nonce
    assert info["Config"]["Labels"]["opencode.task"] == ROOT.name
    assert info["HostConfig"]["NetworkMode"] == "none" and not info["Mounts"]
    assert info["State"]["Running"]


def cli(cluster, args, *, payload=None):
    owned(cluster)
    result = subprocess.run(
        ["docker", "exec", "-i", cluster.container, *args],
        input=payload,
        capture_output=True,
        timeout=15,
        env=ENV,
    )
    cluster.record(
        "artifact_restore_cli",
        operation=args[0],
        returncode=result.returncode,
        stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
    )
    assert result.returncode == 0, "owned synthetic archive command failed; inspect receipts"
    return result.stdout


def file_facts(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def data_facts(s):
    tables = (
        "tasks",
        "messages",
        "artifact_output_jobs",
        "artifact_output_intents",
        "resources",
        "resource_versions",
        "resource_publications",
        "resource_references",
        "resource_presentations",
        "resource_catalog_heads",
        "task_history_projection",
    )
    return {
        table: hashlib.sha256(
            s.db.sql(
                "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text FROM public."
                + table
                + " t"
            ).encode()
        ).hexdigest()
        for table in tables
    }


@dataclass(frozen=True)
class Checkpoint:
    archive: Path
    schema: str
    logical_schema: str
    files: Path
    file_facts: dict[str, str]


async def checkpoint(s, tmp_path):
    assert s.db.name in s.db.cluster.databases
    async with s.db.connection() as conn:
        assert await conn.fetchval("SELECT current_database()") == s.db.name
        catalog, schema, logical_schema = await catalogs(conn)
    (s.db.cluster.evidence / (s.db.name + "-before-catalog.json")).write_text(
        json.dumps(catalog, indent=2) + "\n"
    )
    raw = cli(s.db.cluster, ["pg_dump", "-U", "postgres", "--format=custom", s.db.name])
    assert 0 < len(raw) <= 16 * 1024 * 1024
    archive = tmp_path / (uuid4().hex + ".dump")
    fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(raw)
    listing = cli(s.db.cluster, ["pg_restore", "--list"], payload=raw)
    # Never let archive --create semantics choose or replace the original DB.
    assert not any(
        b" DATABASE " in line for line in listing.splitlines() if not line.startswith(b";")
    )
    files = tmp_path / ("files-" + uuid4().hex)
    shutil.copytree(s.root, files)
    facts = file_facts(files)
    assert facts == file_facts(s.root)
    s.db.cluster.record(
        "artifact_restore_checkpoint",
        source=s.db.name,
        archive_bytes=len(raw),
        archive_sha256=hashlib.sha256(raw).hexdigest(),
        catalog_digest=schema,
        logical_catalog_digest=logical_schema,
        file_count=len(facts),
    )
    return Checkpoint(archive, schema, logical_schema, files, facts)


async def close_pool(pool):
    try:
        async with asyncio.timeout(5):
            await pool.close()
    finally:
        pool.terminate()


@asynccontextmanager
async def restored(s, saved, *, older_files=False):
    cluster = s.db.cluster
    assert not s.history._blocked and not s.effects._blocked
    # No producer runs concurrently. Do not replace an uncertain/failed owner.
    await s.history.aclose(deadline=q.r.p.r.deadline())
    pools = {
        id(p): p
        for p in (
            s.pool,
            s.worker_pool,
            s.publisher._pool,
            s.r0_pool,
            s.native_store._namespaces._pool,
        )
    }
    for pool in pools.values():
        await close_pool(pool)
    assert s.history._closed and available(s.path, "RUNTIME_LOCK")
    owned(cluster)
    target = "restore_" + uuid4().hex
    assert target != s.db.name and target not in cluster.databases
    assert cluster.sql("SELECT count(*) FROM pg_database WHERE datname='" + target + "'") == "0"
    cluster.record("artifact_restore_target_intended", database=target, source=s.db.name)
    cluster.sql('CREATE DATABASE "' + target + '" OWNER md_owner TEMPLATE template0')
    cluster.databases.add(target)
    settled = False
    try:
        cli(
            cluster,
            [
                "pg_restore",
                "-U",
                "postgres",
                "--dbname",
                target,
                "--clean",
                "--if-exists",
                "--single-transaction",
                "--exit-on-error",
            ],
            payload=saved.archive.read_bytes(),
        )
        database = Database(cluster, target, s.db.host, s.db.bridge)
        async with database.connection() as conn:
            assert await conn.fetchval("SELECT current_database()") == target
            catalog, restored_schema, logical_schema = await catalogs(conn)
            (cluster.evidence / (s.db.name + "-after-catalog.json")).write_text(
                json.dumps(catalog, indent=2) + "\n"
            )
            assert logical_schema == saved.logical_schema
        stack = AsyncExitStack()
        try:

            async def pool(role):
                value = await asyncpg.create_pool(
                    host=str(database.host),
                    user=role,
                    password="",
                    database=target,
                    ssl=False,
                    min_size=0,
                    max_size=2,
                    command_timeout=2,
                )
                stack.push_async_callback(close_pool, value)
                return value

            worker_pool = await pool("md_app")
            publication_pool = await pool("reserved_p")
            owner_pool = await pool("md_owner")
            history = HistoryCoordinator(s.path, s.identity, s.ownership)
            await history.open(deadline=q.r.p.r.deadline())
            stack.push_async_callback(history.aclose, deadline=q.r.p.r.deadline())
            projection = HistoryProjection(s.identity, acceptor="md_acceptor", worker="md_app")
            recovery = InferenceRecovery(worker_pool)
            recovery.principals = ("md_app", "md_acceptor")
            worker = TaskStore(
                worker_pool,
                s.tasks._enc,
                MemoryStore(worker_pool, s.tasks._enc),
                inference_recovery=recovery,
            )
            root = saved.files if older_files else s.root
            publisher = ReservedOutputPublisher(
                publication_pool,
                root,
                s.journal,
                s.journal_id,
                s.tasks._enc._cipher,
                inventory_limit=10,
                snapshot_byte_limit=400000,
                presentation_item_limit=10,
                presentation_record_limit=10,
            )
            fresh = SimpleNamespace(
                db=database,
                account=s.account,
                claim=s.claim,
                attempt=s.attempt,
                command=s.command,
                prepared_content=s.prepared_content,
                path=s.path,
                identity=s.identity,
                ownership=s.ownership,
                history=history,
                projection=projection,
                worker=worker,
                tasks=worker,
                effects=HistoryEffects(worker, history, projection),
                publisher=publisher,
                root=root,
                journal=s.journal,
                journal_id=s.journal_id,
                native_store=ResourceStore(
                    owner_pool, root, s.journal, s.journal_id, s.tasks._enc._cipher
                ),
            )
            cluster.record(
                "artifact_restore_reopened",
                database=target,
                source_catalog_digest=saved.schema,
                restored_catalog_digest=restored_schema,
                logical_catalog_digest=logical_schema,
                older_files=older_files,
            )
            yield fresh
        finally:
            await stack.aclose()
            settled = True
    finally:
        # No DROP/retry of an uncertain target. The verified owned network-none
        # container is stopped by the outer fixture even on a failed rehearsal.
        if (
            settled
            and cluster.sql("SELECT count(*) FROM pg_stat_activity WHERE datname='" + target + "'")
            == "0"
        ):
            cluster.sql('DROP DATABASE "' + target + '"')
            cluster.databases.remove(target)
            cluster.record("artifact_restore_target_dropped", database=target, clients=0)
        else:
            cluster.record("artifact_restore_target_quarantined", database=target)


async def parity(s):
    deadline = q.r.p.r.deadline()
    async with s.history.operation(deadline=deadline) as operation, retain_pool_cleanup():
        return await s.effects._parity(operation, deadline)


async def through(s, name):
    for phase in q.PHASES[: q.PHASES.index(name) + 1]:
        await q.phase(s, phase)


async def finish(s, name):
    for phase in q.PHASES[q.PHASES.index(name) + 1 :]:
        await q.phase(s, phase)


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", ["reservation", "catalog", "presentation", "terminal"])
async def test_actual_sql_restore_preserves_checkpoint_but_does_not_prove_later_outputs(
    phase_owner, tmp_path, cut
):
    s = phase_owner
    await through(s, cut)
    saved = await checkpoint(s, tmp_path)
    checkpoint_state = q.state(s)
    await finish(s, cut)
    assert s.db.sql("SELECT status FROM tasks") == "completed"
    assert len(q.r.p.r.entries(s)) == 1  # Outputs are not independently recorded in H.
    newer_files = file_facts(s.root)
    async with restored(s, saved) as fresh:
        assert len((await parity(fresh)).entries) == 1
        reopened = await fresh.worker.task_for_key(s.account, "stable-client-key")
        assert reopened is not None and reopened.task_id == s.claim.task_id
        assert reopened.status is (
            TaskStatus.COMPLETED if cut == "terminal" else TaskStatus.RUNNING
        )
        assert q.state(fresh)[:3] == checkpoint_state[:3]
        assert file_facts(fresh.root) == newer_files
        if cut == "reservation":
            with pytest.raises(ResourceError, match="not_found"):
                await fresh.native_store.read(
                    CapturedGeneration(s.account, 0),
                    s.command.binding.resource_id,
                    s.command.binding.version_id,
                )
            # A retained file is evidence, not a permit to complete missing SQL.
            data = data_facts(fresh)
            with pytest.raises(StorageError, match="needs_reconciliation"):
                await q.phase(fresh, "catalog")
            assert fresh.effects._blocked and fresh.history._blocked
            assert data_facts(fresh) == data
        else:
            loaded = await fresh.native_store.read(
                CapturedGeneration(s.account, 0),
                s.command.binding.resource_id,
                s.command.binding.version_id,
            )
            assert loaded.content == q.r.p.r.CONTENT
            async with fresh.db.connection() as conn:
                content = await conn.fetchval(
                    "SELECT content FROM messages WHERE id=$1", s.claim.result_message_id
                )
            assert (fresh.tasks._enc.decrypt(content) == "synthetic final") is (cut == "terminal")
        assert q.state(fresh)[:3] == checkpoint_state[:3] and file_facts(fresh.root) == newer_files


@pytest.mark.asyncio
async def test_older_sql_and_older_files_can_look_fresh_without_independent_artifact_history(
    phase_owner, tmp_path
):
    s = phase_owner
    await through(s, "reservation")
    saved = await checkpoint(s, tmp_path)
    await q.phase(s, "catalog")
    async with s.db.connection() as conn:
        original_reference = await conn.fetchval("SELECT reference_id FROM resource_references")
    await q.phase(s, "presentation")
    await q.phase(s, "terminal")
    original_files = file_facts(s.root)
    async with restored(s, saved, older_files=True) as fresh:
        assert len((await parity(fresh)).entries) == 1
        assert file_facts(fresh.root) == saved.file_facts
        # Deliberate privileged diagnostic, NOT runtime replay/recovery authority:
        # the uninstalled H join has no independent artifact event to compare.
        observed = await q.phase(fresh, "catalog")
        assert observed is not None
        assert observed.resource_id == s.command.binding.resource_id
        assert observed.version_id == s.command.binding.version_id
        assert observed.reference_id != original_reference
        assert q.r.p.r.rows(fresh) == "1:1:1:0"
        assert fresh.db.sql("SELECT status FROM tasks") == "running"
        assert len((await parity(fresh)).entries) == 1
        assert not fresh.effects._blocked and file_facts(s.root) == original_files
        assert s.db.sql("SELECT status FROM tasks") == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("independent", ["stop", "send", "j1"])
async def test_restore_cannot_erase_independent_stop_send_or_journal_evidence(
    phase_owner, tmp_path, independent
):
    s = phase_owner
    await through(s, "reservation")
    saved = await checkpoint(s, tmp_path)
    if independent == "stop":
        assert (
            await s.effects.request_cancel(
                s.account, s.claim.task_id, key=KEY, deadline=q.r.p.r.deadline()
            )
        ).accepted
    elif independent == "send":
        await s.effects.mark(
            s.attempt,
            s.reservation.id,
            s.invocation,
            effect_kind="completion",
            deadline=q.r.p.r.deadline(),
        )
    else:
        with MixedJournal(s.journal, s.journal_id) as journal:
            journal.append(request(s.account), uuid4(), 1, None)
    files = file_facts(s.root)
    async with restored(s, saved) as fresh:
        before = q.state(fresh)
        data = data_facts(fresh)
        if independent == "j1":
            assert len((await parity(fresh)).entries) == 1
        expected = (JournalError, ResourceError) if independent == "j1" else HistoryError
        with pytest.raises(expected) as refused:
            await q.phase(fresh, "catalog")
        if independent != "j1":
            assert refused.value.code == "effects_reconciliation_required"
        s.db.cluster.record(
            "artifact_restore_refused",
            independent=independent,
            error=type(refused.value).__name__,
            code=refused.value.code,
        )
        assert fresh.effects._blocked and fresh.history._blocked
        assert q.state(fresh) == before and file_facts(s.root) == files
        assert data_facts(fresh) == data


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["grant", "column", "constraint"])
async def test_restore_comparison_still_detects_permission_and_definition_changes(
    phase_owner, tmp_path, damage
):
    s = phase_owner
    await through(s, "terminal")
    saved = await checkpoint(s, tmp_path)
    async with restored(s, saved) as fresh:
        sql = {
            "grant": "GRANT SELECT ON resource_versions TO md_third",
            "column": "ALTER TABLE memories ADD COLUMN synthetic_restore_fault integer",
            "constraint": "ALTER TABLE resource_versions ADD CONSTRAINT synthetic_restore_fault CHECK(generation>=0) NOT VALID",
        }[damage]
        # Faults affect only this fresh owned synthetic restore target.
        fresh.db.sql(sql)
        async with fresh.db.connection() as conn:
            _, _, actual = await catalogs(conn)
        assert actual != saved.logical_schema
