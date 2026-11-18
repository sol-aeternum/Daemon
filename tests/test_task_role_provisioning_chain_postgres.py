"""Live role provisioning on the REAL full migration chain (#732 P2).

The database is migrated by scripts/migrate.py's own runner through the latest
file, restore-history (H) included, so ``_migrations`` is exactly the runner's
record. ``apply`` and ``verify`` run as a superuser the way the live deployment
will, through the library calls and the actual CLI in a subprocess. H objects
carry no A/W privilege and are not in the maps; actual host startup on this
schema is in test_history_unbound_host_postgres.py (#731).
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import migrate
from scripts import provision_task_roles as provision
from tests.compute_metadata_support import ROOT
from tests.exact_worker_privileges import ACCEPTOR_MAP, MAP
from tests.test_compute_metadata_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_history_acceptance_upgrade_postgres import upgrade_through

LATEST = provision.expected_migrations()[-1]
PASSWORDS = ("Fixture-acceptor-0001", "Fixture-worker-00002")


class _Undo(Exception):
    """Roll back a deliberate drift after observing the refusal."""


def names() -> tuple[str, str]:
    return "rp_" + uuid4().hex[:8], "rp_" + uuid4().hex[:8]


async def apply(owner, acceptor, worker):
    await provision.apply(
        owner,
        acceptor,
        worker,
        provision.scram_verifier(PASSWORDS[0]),
        provision.scram_verifier(PASSWORDS[1]),
    )


async def count_roles(owner, *roles) -> int:
    return await owner.fetchval(
        "SELECT count(*) FROM pg_roles WHERE rolname = ANY($1::name[])", list(roles)
    )


async def drop_roles(db, *roles):
    async with db.connection("postgres") as owner:
        for role in roles:
            await owner.execute(
                f"DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN "
                f"EXECUTE 'DROP OWNED BY {role}'; EXECUTE 'DROP ROLE {role}'; END IF; END $$"
            )


def secret(directory: Path, name: str, value: str) -> Path:
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(value)
    return path


async def cli(db, tmp_path: Path, command: str, acceptor: str, worker: str):
    """The real entry point, in its own process (it owns its event loop)."""
    work = tmp_path / uuid4().hex[:6]
    work.mkdir(mode=0o700)
    url = secret(work, "url", f"postgresql://postgres@/{db.name}?host={db.host}")
    args = [
        command,
        "--acceptor-role",
        acceptor,
        "--worker-role",
        worker,
        "--database-url-file",
        str(url),
    ]
    if command == "apply":
        args += [
            "--acceptor-password-file",
            str(secret(work, "a", PASSWORDS[0])),
            "--worker-password-file",
            str(secret(work, "w", PASSWORDS[1])),
        ]
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["PYTHONPATH"] = str(ROOT)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "scripts.provision_task_roles",
        *args,
        cwd=ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(), 60)
    return process.returncode, out.decode().strip(), err.decode().strip()


async def migrated(db):
    async with db.connection() as owner:
        await upgrade_through(owner, LATEST)
        recorded = await owner.fetch("SELECT filename FROM _migrations ORDER BY filename")
        assert [r["filename"] for r in recorded] == provision.expected_migrations()


@pytest.mark.asyncio
async def test_apply_and_verify_pass_on_the_complete_runner_chain(db, tmp_path):
    await migrated(db)
    acceptor, worker = names()
    try:
        assert await cli(db, tmp_path, "apply", acceptor, worker) == (
            0,
            f"apply_ok acceptor={ACCEPTOR_MAP['proposal_sha256'][:12]} "
            f"worker={MAP['proposal_sha256'][:12]}",
            "",
        )
        code, out, err = await cli(db, tmp_path, "verify", acceptor, worker)
        assert (code, out.split()[0], err) == (0, "verify_ok", "")
        async with db.connection("postgres") as owner:
            stored = await owner.fetch(
                "SELECT left(rolpassword, 19) AS kind FROM pg_authid "
                "WHERE rolname = ANY($1::name[])",
                [acceptor, worker],
            )
            assert [row["kind"] for row in stored] == ["SCRAM-SHA-256$4096:"] * 2
            await provision.verify_deployment(owner, acceptor, worker)
            # A second apply never adopts or alters existing roles.
            with pytest.raises(provision.ProvisionError, match="role_already_exists"):
                await apply(owner, acceptor, worker)
            for drift, code in (
                (f"GRANT DELETE ON public.messages TO {acceptor}", "table_privileges_differ"),
                (f"GRANT SELECT ON public._migrations TO {worker}", "table_privileges_differ"),
                (
                    f"GRANT SELECT ON public.task_history_binding TO {acceptor}",
                    "table_privileges_differ",
                ),
                (
                    f"GRANT INSERT(task_id) ON public.task_inference_recovery TO {worker}",
                    "column_privileges_differ",
                ),
                (f"GRANT {worker} TO {acceptor}", "role_attributes_refused"),
            ):
                with pytest.raises(_Undo):
                    async with owner.transaction():
                        await owner.execute(drift)
                        with pytest.raises(provision.ProvisionError, match=code):
                            await provision.verify(owner, ACCEPTOR_MAP, MAP, acceptor, worker)
                        raise _Undo
            await provision.verify_deployment(owner, acceptor, worker)
    finally:
        await drop_roles(db, acceptor, worker)


def _earliest():
    return provision.expected_migrations()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        ("DELETE FROM public._migrations WHERE filename=$1", "056_restore_history_acceptance.sql"),
        ("DELETE FROM public._migrations WHERE filename=$1", "earliest"),
        ("DELETE FROM public._migrations WHERE filename=$1", "latest"),
        ("INSERT INTO public._migrations(filename) VALUES($1)", "999_unknown_future.sql"),
    ],
    ids=["hole-056", "hole-earliest", "missing-latest", "unknown-extra"],
)
async def test_incomplete_or_foreign_history_refuses_apply_and_verify(db, tmp_path, damage):
    await migrated(db)
    sql, filename = damage
    filename = {"earliest": _earliest(), "latest": LATEST}.get(filename, filename)
    existing, fresh = names(), names()
    try:
        async with db.connection("postgres") as owner:
            await apply(owner, *existing)
            await provision.verify_deployment(owner, *existing)
            await owner.execute(sql, filename)
            recorded = await owner.fetch("SELECT filename FROM _migrations ORDER BY filename")
            if filename != LATEST:
                # The newest migration stays recorded: max(filename) alone would pass.
                assert max(r["filename"] for r in recorded) in (LATEST, "999_unknown_future.sql")
            for call in (
                provision.verify_deployment(owner, *existing),
                apply(owner, *fresh),
            ):
                with pytest.raises(provision.ProvisionError, match="migration_history_incomplete"):
                    await call
            assert await count_roles(owner, *fresh) == 0
        for command, roles in (("verify", existing), ("apply", fresh)):
            assert await cli(db, tmp_path, command, *roles) == (
                2,
                "",
                "refused: migration_history_incomplete",
            )
        async with db.connection("postgres") as owner:
            assert await count_roles(owner, *fresh) == 0
    finally:
        await drop_roles(db, *existing, *fresh)


@pytest.mark.asyncio
async def test_running_migration_lock_refuses_apply_and_verify(db):
    await migrated(db)
    existing, fresh = names(), names()
    try:
        async with db.connection("postgres") as owner, db.connection() as runner:
            await apply(owner, *existing)
            await migrate.acquire_migration_lock(runner)
            try:
                for call in (
                    provision.verify_deployment(owner, *existing),
                    apply(owner, *fresh),
                ):
                    with pytest.raises(provision.ProvisionError, match="migration_in_progress"):
                        await call
                assert await count_roles(owner, *fresh) == 0
            finally:
                await migrate.release_migration_lock(runner, require_owned=True)
            await provision.verify_deployment(owner, *existing)
    finally:
        await drop_roles(db, *existing, *fresh)


@pytest.mark.asyncio
async def test_missing_bookkeeping_table_refuses(db):
    async with db.connection("postgres") as owner:
        await owner.execute("ALTER TABLE public._migrations RENAME TO _migrations_moved")
        try:
            with pytest.raises(provision.ProvisionError, match="migration_history_incomplete"):
                await provision.verify_deployment(owner, *names())
        finally:
            await owner.execute("ALTER TABLE public._migrations_moved RENAME TO _migrations")


@pytest.mark.asyncio
async def test_completeness_check_requires_a_transaction(db):
    async with db.connection("postgres") as owner:
        with pytest.raises(provision.ProvisionError, match="transaction_required"):
            await provision.require_complete_schema(owner)
